"""Phase 6 LightGBM development run (configs/lightgbm.yaml; no holdout access).

Per outer fold F (``folds.Fold``), nested chronology (D-37):

* tuning window T = 90 origins before the calibration window C; inner training
  origins <= T_start - 2 d. 12 candidates; p50 by inner MAE; p10/p90 share the
  structure with their own early-stopped iterations;
* calibration model trained on origins <= C_start - 2 d gives out-of-sample
  residuals on C (trailing calibration);
* season archive: each same-season month m in [F - 730 d, F - 2 d] is predicted
  by a model refitted on origins <= m_start - 2 d (season-matched calibration);
* final model trained on origins <= F - 2 d predicts the outer month.

The Phase 4 hybrid predictions are loaded unchanged from the tagged benchmark
run. Actuals are attached only in the evaluation layer.
"""

from __future__ import annotations

import datetime as dt
import importlib.metadata
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nem_forecast.config import BacktestConfig, LightGBMProtocol
from nem_forecast.evaluation.backtest import TARGET, attach_evaluation, comparisons, load_design
from nem_forecast.evaluation.bootstrap import paired_block_bootstrap
from nem_forecast.evaluation.folds import Fold, assert_development_origins, build_folds
from nem_forecast.evaluation.metrics import summarise
from nem_forecast.models.baselines import DayWeekHybrid, select_inputs
from nem_forecast.models.lightgbm_quantile import feature_matrix, fit_quantile, lgb_params, predict
from nem_forecast.paths import ProjectPaths
from nem_forecast.provenance import read_json, sha256_file, utc_now_iso, write_json_atomic
from nem_forecast.target.build import environment, git_state

QUANTILES = (0.1, 0.5, 0.9)
QNAME = {0.1: "p10", 0.5: "p50", 0.9: "p90"}
HYBRID = "day_week_hybrid"
CANDIDATES = (
    "day_week_hybrid_frozen",
    "day_week_hybrid_season_calibrated",
    "lightgbm_raw",
    "lightgbm_trailing_calibrated",
    "lightgbm_season_calibrated",
)
CONFIG_FILES = (
    "configs/lightgbm.yaml",
    "configs/backtest.yaml",
    "configs/features.yaml",
    "configs/feature_availability.yaml",
    "configs/forecast.yaml",
)
DAY = dt.timedelta(days=1)


@dataclass(frozen=True)
class Windows:
    fold_id: int
    season: str
    tuning_first: dt.date
    tuning_last: dt.date
    inner_train_last: dt.date
    calmodel_train_last: dt.date
    calibration_first: dt.date
    calibration_last: dt.date
    final_train_last: dt.date
    season_window_first: dt.date
    season_window_last: dt.date

    def as_dict(self) -> dict[str, Any]:
        return {
            k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in asdict(self).items()
        }


def season_of_month(month: int, bcfg: BacktestConfig) -> str:
    return next(s for s, ms in bcfg.slices.seasons_by_civil_month.items() if month in ms)


def nested_windows(fold: Fold, proto: LightGBMProtocol, bcfg: BacktestConfig) -> Windows:
    gap = proto.nested.inner_maturity_gap_days * DAY
    tuning_last = fold.calibration_first_origin - DAY
    tuning_first = fold.calibration_first_origin - proto.nested.tuning_window_days * DAY
    return Windows(
        fold_id=fold.fold_id,
        season=season_of_month(fold.validation_first_origin.month, bcfg),
        tuning_first=tuning_first,
        tuning_last=tuning_last,
        inner_train_last=tuning_first - gap,
        calmodel_train_last=fold.calibration_first_origin - gap,
        calibration_first=fold.calibration_first_origin,
        calibration_last=fold.calibration_last_origin,
        final_train_last=fold.last_matured_origin,
        season_window_first=fold.validation_first_origin
        - proto.calibration.season_matched.lookback_days * DAY,
        season_window_last=fold.last_matured_origin,
    )


def _between(design: pd.DataFrame, first: dt.date | None, last: dt.date) -> pd.DataFrame:
    mask = design["origin_date"] <= last
    if first is not None:
        mask &= design["origin_date"] >= first
    return design[mask]


def _eligible(rows: pd.DataFrame) -> pd.DataFrame:
    return rows[rows["eligible_initial_ml_features"] & rows["target_available"]]


def _y(rows: pd.DataFrame) -> np.ndarray:
    return rows[TARGET].to_numpy(dtype=float)


# --- calibration helpers ------------------------------------------------------------


def residual_offsets(
    residuals: pd.DataFrame, min_samples: int, max_samples: int | None
) -> pd.DataFrame:
    """Empirical quantile-specific offsets per (horizon, quantile).

    ``residuals`` has columns horizon, quantile, origin_date, residual (= actual - raw_q).
    With ``max_samples``, only the most recent samples per horizon are used.
    """
    rows = []
    for (h, q), g in residuals.dropna(subset=["residual"]).groupby(["horizon", "quantile"]):
        g = g.sort_values("origin_date", ascending=False, kind="mergesort")
        if max_samples is not None:
            g = g.head(max_samples)
        n = len(g)
        ok = n >= min_samples
        rows.append(
            {
                "horizon": int(str(h)),
                "quantile": float(str(q)),
                "n_samples": n,
                "calibrated": ok,
                "offset": float(
                    np.quantile(g["residual"].to_numpy(dtype=float), float(str(q)), method="linear")
                )
                if ok
                else np.nan,
                "first_origin": str(min(g["origin_date"])) if n else None,
                "last_origin": str(max(g["origin_date"])) if n else None,
            }
        )
    return pd.DataFrame(rows)


def combine_with_fallback(season: pd.DataFrame, trailing: pd.DataFrame) -> pd.DataFrame:
    """Season offsets where calibrated, else deterministic trailing fallback (recorded)."""
    keys = ["horizon", "quantile"]
    merged = (
        trailing[[*keys, "offset", "calibrated"]]
        .rename(columns={"offset": "trailing_offset", "calibrated": "trailing_calibrated"})
        .merge(season[[*keys, "offset", "calibrated", "n_samples"]], on=keys, how="left")
    )
    use_season = merged["calibrated"].fillna(False).astype(bool)
    merged["applied_offset"] = np.where(use_season, merged["offset"], merged["trailing_offset"])
    merged["source"] = np.where(use_season, "season", "trailing_fallback")
    merged["applied_calibrated"] = use_season | merged["trailing_calibrated"].astype(bool)
    merged["n_samples"] = merged["n_samples"].fillna(0).astype(int)
    return merged


def apply_quantile_offsets(
    raw: pd.DataFrame, offsets: pd.DataFrame, offset_col: str, calibrated_col: str
) -> pd.DataFrame:
    """p_q = raw_q + offset_q per horizon; uncalibrated (horizon, q) -> unavailable."""
    out = raw.copy()
    ok = np.ones(len(out), dtype=bool)
    for q in QUANTILES:
        table = offsets[offsets["quantile"] == q].set_index("horizon")
        off = out["horizon"].map(table[offset_col])
        cal = out["horizon"].map(table[calibrated_col]).fillna(False).astype(bool)
        out[QNAME[q]] = out[f"raw_{QNAME[q]}"] + off
        ok &= cal.to_numpy()
    out["available"] = out["available"] & ok
    out.loc[~out["available"], ["p10", "p50", "p90"]] = np.nan
    return out


# --- LightGBM per fold --------------------------------------------------------------


def _fit_quantiles(
    train: pd.DataFrame,
    proto: LightGBMProtocol,
    structure: dict[str, Any],
    iterations: dict[float, int],
    allowlist: tuple[str, ...],
) -> dict[float, Any]:
    x, y = feature_matrix(train, allowlist), _y(train)
    fixed = dict(proto.lightgbm.fixed_params)
    return {
        q: fit_quantile(x, y, lgb_params(fixed, structure, q), iterations[q], allowlist).booster
        for q in QUANTILES
    }


def _predict_raw(
    boosters: dict[float, Any],
    rows: pd.DataFrame,
    iterations: dict[float, int],
    allowlist: tuple[str, ...],
) -> pd.DataFrame:
    x = feature_matrix(rows, allowlist)
    out = rows[["origin_utc", "horizon", "target_hour_utc", "origin_date"]].copy()
    for q in QUANTILES:
        out[f"raw_{QNAME[q]}"] = predict(boosters[q], x, iterations[q])
    return out


def _residuals(raw: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    actual = rows[TARGET].where(rows["target_available"]).to_numpy(dtype=float)
    parts = [
        pd.DataFrame(
            {
                "horizon": raw["horizon"].to_numpy(),
                "quantile": q,
                "origin_date": raw["origin_date"].to_numpy(),
                "residual": actual - raw[f"raw_{QNAME[q]}"].to_numpy(),
            }
        )
        for q in QUANTILES
    ]
    return pd.concat(parts, ignore_index=True)


def season_months(
    design: pd.DataFrame, win: Windows, bcfg: BacktestConfig
) -> list[tuple[dt.date, pd.DataFrame]]:
    rows = _between(design, win.season_window_first, win.season_window_last)
    months = pd.to_datetime(rows["origin_date"]).dt.month
    rows = rows[months.map(lambda m: season_of_month(int(m), bcfg)) == win.season]
    out = []
    for (year, month), g in rows.groupby(
        [pd.to_datetime(rows["origin_date"]).dt.year, pd.to_datetime(rows["origin_date"]).dt.month]
    ):
        out.append((dt.date(int(str(year)), int(str(month)), 1), g))
    return out


def run_lightgbm_fold(
    design: pd.DataFrame, fold: Fold, win: Windows, proto: LightGBMProtocol, bcfg: BacktestConfig
) -> dict[str, Any]:
    allowlist = proto.feature_allowlist.columns
    fixed = dict(proto.lightgbm.fixed_params)
    rounds, patience = proto.lightgbm.max_boosting_rounds, proto.lightgbm.early_stopping_rounds
    timing: dict[str, float] = {}
    started = time.monotonic()
    inner_train = _eligible(_between(design, None, win.inner_train_last))
    tuning = _eligible(_between(design, win.tuning_first, win.tuning_last))
    xi, yi = feature_matrix(inner_train, allowlist), _y(inner_train)
    xt, yt = feature_matrix(tuning, allowlist), _y(tuning)
    candidates = []
    for idx, structure in enumerate(proto.lightgbm.candidate_grid):
        res = fit_quantile(
            xi,
            yi,
            lgb_params(fixed, structure, 0.5),
            rounds,
            allowlist,
            valid=(xt, yt),
            early_stopping_rounds=patience,
        )
        pred = predict(res.booster, xt, res.best_iteration)
        candidates.append(
            {
                "candidate": idx,
                **structure,
                "best_iteration": res.best_iteration,
                "inner_mae_mw": float(np.mean(np.abs(pred - yt))),
                "fit_seconds": round(res.seconds, 3),
            }
        )
    selected = min(candidates, key=lambda c: (c["inner_mae_mw"], c["candidate"]))
    structure = {k: selected[k] for k in ("num_leaves", "min_data_in_leaf", "lambda_l2")}
    iterations = {0.5: int(selected["best_iteration"])}
    tail_tuning = {}
    for q in (0.1, 0.9):
        res = fit_quantile(
            xi,
            yi,
            lgb_params(fixed, structure, q),
            rounds,
            allowlist,
            valid=(xt, yt),
            early_stopping_rounds=patience,
        )
        iterations[q] = res.best_iteration
        tail_tuning[QNAME[q]] = {
            "best_iteration": res.best_iteration,
            "inner_pinball_mw": float(
                np.mean(
                    np.maximum(
                        q * (yt - predict(res.booster, xt, res.best_iteration)),
                        (q - 1) * (yt - predict(res.booster, xt, res.best_iteration)),
                    )
                )
            ),
        }
    timing["tuning"] = time.monotonic() - started

    t0 = time.monotonic()
    cal_rows = _between(design, win.calibration_first, win.calibration_last)
    calmodel = _fit_quantiles(
        _eligible(_between(design, None, win.calmodel_train_last)),
        proto,
        structure,
        iterations,
        allowlist,
    )
    cal_raw = _predict_raw(calmodel, cal_rows, iterations, allowlist)
    trailing = residual_offsets(
        _residuals(cal_raw, cal_rows), proto.calibration.min_samples_per_horizon, None
    )
    timing["trailing_calibration"] = time.monotonic() - t0

    t0 = time.monotonic()
    archive = []
    for month_start, rows in season_months(design, win, bcfg):
        model = _fit_quantiles(
            _eligible(_between(design, None, month_start - 2 * DAY)),
            proto,
            structure,
            iterations,
            allowlist,
        )
        archive.append(_residuals(_predict_raw(model, rows, iterations, allowlist), rows))
    sm = proto.calibration.season_matched
    season_res = (
        pd.concat(archive, ignore_index=True)
        if archive
        else pd.DataFrame(columns=["horizon", "quantile", "origin_date", "residual"])
    )
    season = residual_offsets(season_res, sm.min_samples_per_horizon, sm.max_samples_per_horizon)
    timing["season_archive"] = time.monotonic() - t0

    t0 = time.monotonic()
    final = _fit_quantiles(
        _eligible(_between(design, None, win.final_train_last)),
        proto,
        structure,
        iterations,
        allowlist,
    )
    timing["final_fit"] = time.monotonic() - t0
    t0 = time.monotonic()
    outer = _between(design, fold.validation_first_origin, fold.validation_last_origin)
    raw = _predict_raw(final, outer, iterations, allowlist)
    timing["final_predict"] = time.monotonic() - t0
    gain = final[0.5].feature_importance(importance_type="gain")
    return {
        "candidates": candidates,
        "selected": selected,
        "structure": structure,
        "iterations": {QNAME[q]: v for q, v in iterations.items()},
        "tail_tuning": tail_tuning,
        "trailing": trailing,
        "season": season,
        "raw": raw,
        "archive_months": [m.isoformat() for m, _ in season_months(design, win, bcfg)],
        "importance": dict(zip(allowlist, (float(g) for g in gain), strict=True)),
        "timing": timing,
        "models": {QNAME[q]: final[q].model_to_string() for q in QUANTILES},
        "rows": {
            "inner_train": len(inner_train),
            "tuning": len(tuning),
            "calibration": len(cal_rows),
            "outer": len(outer),
        },
    }


# --- hybrid season calibration ------------------------------------------------------


def hybrid_season_offsets(
    design: pd.DataFrame, win: Windows, proto: LightGBMProtocol, bcfg: BacktestConfig
) -> pd.DataFrame:
    """Out-of-sample hybrid residuals for same-season months (weight refitted per month)."""
    archive = []
    for month_start, rows in season_months(design, win, bcfg):
        model = DayWeekHybrid(bcfg.hybrid.weight_grid.values)
        history = _between(design, None, month_start - 2 * DAY)
        model.fit(select_inputs(history, model), history[TARGET].where(history["target_available"]))
        point = model.predict(select_inputs(rows, model))["point_raw"].to_numpy()
        actual = rows[TARGET].where(rows["target_available"]).to_numpy(dtype=float)
        for q in QUANTILES:
            archive.append(
                pd.DataFrame(
                    {
                        "horizon": rows["horizon"].to_numpy(),
                        "quantile": q,
                        "origin_date": rows["origin_date"].to_numpy(),
                        "residual": actual - point,
                    }
                )
            )
    sm = proto.calibration.season_matched
    res = (
        pd.concat(archive, ignore_index=True)
        if archive
        else pd.DataFrame(columns=["horizon", "quantile", "origin_date", "residual"])
    )
    return residual_offsets(res, sm.min_samples_per_horizon, sm.max_samples_per_horizon)


def load_benchmark(
    paths: ProjectPaths, proto: LightGBMProtocol
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Phase 4 hybrid predictions and trailing offsets, verified against the frozen record."""
    run = paths.results_runs / proto.benchmark_run_id
    record = read_json(paths.root / "docs" / "benchmarks" / "phase4_development_benchmark.json")
    if sha256_file(run / "run_manifest.json") != record["run_manifest_sha256"]:
        raise ValueError("Phase 4 benchmark run manifest does not match the frozen record")
    manifest = read_json(run / "run_manifest.json")
    for name in ("predictions.parquet", "residual_quantiles.parquet"):
        if sha256_file(run / name) != manifest["outputs"][name]:
            raise ValueError(f"Phase 4 {name} changed since the benchmark run")
    preds = pd.read_parquet(run / "predictions.parquet")
    preds = preds[preds["model"] == HYBRID].copy()
    offsets = pd.read_parquet(run / "residual_quantiles.parquet")
    offsets = offsets[offsets["model"] == HYBRID]
    return (
        preds,
        offsets,
        {"run_id": proto.benchmark_run_id, "run_manifest_sha256": record["run_manifest_sha256"]},
    )


def _long_offsets(phase4: pd.DataFrame, fold_id: int) -> pd.DataFrame:
    t = phase4[phase4["fold_id"] == fold_id]
    parts = [
        pd.DataFrame(
            {
                "horizon": t["horizon"],
                "quantile": q,
                "offset": t[f"offset_q{int(q * 100):02d}"],
                "calibrated": t["calibrated"],
            }
        )
        for q in QUANTILES
    ]
    return pd.concat(parts, ignore_index=True)


# --- run --------------------------------------------------------------------------


def run_phase6(
    paths: ProjectPaths, bcfg: BacktestConfig, proto: LightGBMProtocol, run_id: str | None = None
) -> dict[str, Any]:
    from nem_forecast.evaluation.phase6_report import write_phase6_reports

    started = time.monotonic()
    run_id = run_id or f"phase6_lightgbm_{utc_now_iso().replace(':', '').replace('-', '')}"
    run_dir = paths.results_runs / run_id
    if run_dir.exists():
        raise FileExistsError(f"{run_dir} exists; run IDs are never reused")
    run_dir.mkdir(parents=True)
    created = utc_now_iso()
    try:
        folds = build_folds(bcfg)  # layer 1: config validator + fold guard
        allowlist = proto.feature_allowlist.columns
        extra = [
            "eligible_initial_ml_features",
            "lag_24h",
            "lag_24h__available",
            "lag_168h",
            "lag_168h__available",
        ]
        design, input_info = load_design(paths, bcfg, sorted({*allowlist, *extra}))  # layer 2
        hybrid_preds, hybrid_offsets, bench = load_benchmark(paths, proto)
        hybrid_preds["origin_date"] = hybrid_preds["origin_utc"].dt.tz_convert("+10:00").dt.date
        rows, offsets_log, fold_log, thresholds = [], [], [], {}
        (run_dir / "models").mkdir()
        for fold in folds:
            win = nested_windows(fold, proto, bcfg)
            training = _between(design, None, fold.last_matured_origin)
            thresholds[fold.fold_id] = float(
                np.quantile(
                    training[TARGET].where(training["target_available"]).dropna(),
                    bcfg.slices.high_demand_training_quantile,
                )
            )
            res = run_lightgbm_fold(design, fold, win, proto, bcfg)
            raw = res["raw"].assign(fold_id=fold.fold_id, available=True, unavailable_reason="")
            raw_c = raw.assign(
                model="lightgbm_raw",
                calibration_source="none",
                p10=raw["raw_p10"],
                p50=raw["raw_p50"],
                p90=raw["raw_p90"],
            )
            trail = res["trailing"]
            trailing_c = apply_quantile_offsets(raw, trail, "offset", "calibrated").assign(
                model="lightgbm_trailing_calibrated", calibration_source="trailing"
            )
            combo = combine_with_fallback(res["season"], trail)
            season_c = apply_quantile_offsets(
                raw, combo, "applied_offset", "applied_calibrated"
            ).assign(model="lightgbm_season_calibrated", calibration_source="season_or_fallback")
            hp = hybrid_preds[hybrid_preds["fold_id"] == fold.fold_id].copy()
            hp_raw = hp.assign(
                raw_p10=hp["point_raw"], raw_p50=hp["point_raw"], raw_p90=hp["point_raw"]
            )
            frozen = hp_raw.assign(
                model="day_week_hybrid_frozen", calibration_source="phase4_trailing"
            )
            h_combo = combine_with_fallback(
                hybrid_season_offsets(design, win, proto, bcfg),
                _long_offsets(hybrid_offsets, fold.fold_id),
            )
            h_season = apply_quantile_offsets(
                hp_raw, h_combo, "applied_offset", "applied_calibrated"
            ).assign(
                model="day_week_hybrid_season_calibrated", calibration_source="season_or_fallback"
            )
            for frame in (frozen, h_season, raw_c, trailing_c, season_c):
                rows.append(frame)
            for name, table in (
                ("lightgbm_trailing", trail.assign(source="trailing")),
                ("lightgbm_season", combo),
                ("hybrid_season", h_combo),
            ):
                offsets_log.append(table.assign(fold_id=fold.fold_id, calibration=name))
            for q, text in res["models"].items():
                (run_dir / "models" / f"fold{fold.fold_id:02d}_{q}.txt").write_text(text)
            fold_log.append(
                {
                    "fold": fold.as_dict(),
                    "windows": win.as_dict(),
                    "selected": res["selected"],
                    "structure": res["structure"],
                    "iterations": res["iterations"],
                    "tail_tuning": res["tail_tuning"],
                    "candidates": res["candidates"],
                    "archive_months": res["archive_months"],
                    "rows": res["rows"],
                    "timing_seconds": res["timing"],
                    "importance_gain": res["importance"],
                    "season_fallback": {
                        "lightgbm": int((combo["source"] == "trailing_fallback").sum()),
                        "hybrid": int((h_combo["source"] == "trailing_fallback").sum()),
                        "cells": len(combo),
                    },
                }
            )
        cols = [
            "model",
            "fold_id",
            "origin_utc",
            "horizon",
            "target_hour_utc",
            "origin_date",
            "raw_p10",
            "raw_p50",
            "raw_p90",
            "p10",
            "p50",
            "p90",
            "available",
            "unavailable_reason",
            "calibration_source",
        ]
        cands = pd.concat([r[cols] for r in rows], ignore_index=True)
        assert_development_origins(cands["origin_date"])  # layer 3
        ev = attach_evaluation(cands, design, thresholds, bcfg)
        results = comparisons(ev, bcfg)
        boot = bcfg.bootstrap
        keyed = ev.set_index(["model", "origin_utc", "horizon"])
        pin = {}
        for m in CANDIDATES:
            g = keyed.loc[m]
            y = g["actual"].to_numpy(dtype=float)
            pin[m] = pd.Series(
                np.mean(
                    [
                        np.maximum(q * (y - g[QNAME[q]]), (q - 1) * (y - g[QNAME[q]]))
                        for q in QUANTILES
                    ],
                    axis=0,
                ),
                index=g.index,
            )
        ref = "day_week_hybrid_frozen"
        dates = keyed.loc[ref]["origin_date"]
        results["paired_bootstrap_mean_pinball_vs_frozen_hybrid"] = [
            {
                "model_a": m,
                "model_b": ref,
                **paired_block_bootstrap(
                    pin[m],
                    pin[ref].loc[pin[m].index],
                    dates.loc[pin[m].index],
                    boot.replicates,
                    boot.seed,
                    boot.confidence,
                ),
            }
            for m in CANDIDATES
            if m != ref
        ]
        sorted_ev = ev.copy()
        arr = np.sort(sorted_ev[["p10", "p50", "p90"]].to_numpy(dtype=float), axis=1)
        sorted_ev[["p10", "p50", "p90"]] = arr
        results["after_rearrangement"] = {m: summarise(g) for m, g in sorted_ev.groupby("model")}
        results["raw_quantile_crossing"] = {
            m: {
                "rate": float(
                    np.mean((g["raw_p10"] > g["raw_p50"]) | (g["raw_p50"] > g["raw_p90"]))
                ),
                "mean_magnitude_mw": float(
                    np.mean(
                        np.maximum(0, g["raw_p10"] - g["raw_p50"])
                        + np.maximum(0, g["raw_p50"] - g["raw_p90"])
                    )
                ),
            }
            for m, g in ev.groupby("model")
            if str(m).startswith("lightgbm")
        }
        cands.drop(columns=["origin_date"]).to_parquet(run_dir / "predictions.parquet", index=False)
        ev.drop(columns=["civil_date"]).to_parquet(run_dir / "evaluation_rows.parquet", index=False)
        offsets = pd.concat(offsets_log, ignore_index=True)
        offsets.to_parquet(run_dir / "calibration_offsets.parquet", index=False)
        (run_dir / "folds_and_tuning.json").write_text(json.dumps(fold_log, indent=2, default=str))
        (run_dir / "protocol.resolved.json").write_text(proto.model_dump_json(indent=2))
        (run_dir / "feature_allowlist.json").write_text(json.dumps(list(allowlist), indent=2))
        (run_dir / "metrics.json").write_text(json.dumps(results, indent=2, default=str))
        write_phase6_reports(run_dir, results, fold_log, offsets, ev)
        outputs = {
            str(p.relative_to(run_dir)): sha256_file(p)
            for p in sorted(run_dir.rglob("*"))
            if p.is_file()
        }
        from nem_forecast.evaluation.backtest import _git

        manifest = {
            "run_id": run_id,
            "phase": "6-development",
            "status": "complete",
            "created_utc": created,
            "completed_utc": utc_now_iso(),
            "runtime_seconds": round(time.monotonic() - started, 2),
            "holdout_guard": {
                "holdout_first_origin": "2026-07-01",
                "max_origin_loaded": input_info["max_origin_loaded"],
                "max_origin_predicted": str(max(cands["origin_date"])),
                "holdout_rows_loaded": 0,
                "evaluate_holdout_executed": False,
            },
            "inputs": input_info,
            "benchmark": bench,
            "contracts": {
                t: {
                    "tag_object": _git(paths.root, "rev-parse", t),
                    "commit": _git(paths.root, "rev-parse", f"{t}^{{commit}}"),
                }
                for t in ("data-contract-v1", "feature-contract-v1", "baseline-benchmark-v1")
            },
            "config_hashes": {f: sha256_file(paths.root / f) for f in CONFIG_FILES},
            "code": git_state(paths.root),
            "environment": environment() | {"lightgbm": importlib.metadata.version("lightgbm")},
            "rows": {
                "candidate_predictions": len(cands),
                "per_candidate": len(cands) // len(CANDIDATES),
            },
            "outputs": outputs,
        }
        write_json_atomic(run_dir / "run_manifest.json", manifest)
    except Exception as exc:
        write_json_atomic(
            run_dir / "failure_manifest.json",
            {
                "run_id": run_id,
                "status": "failed",
                "created_utc": created,
                "failed_utc": utc_now_iso(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "code": git_state(paths.root),
            },
        )
        raise
    return manifest


__all__ = ["CANDIDATES", "Path", "Windows", "nested_windows", "run_phase6"]
