"""Phase 4 development backtest of seasonal-naive baselines (no holdout access).

Order of operations per fold (``folds.py`` defines the dates):

1. hybrid weight fit on matured origins before the calibration window;
2. raw predictions on the 180 calibration origins -> per-horizon residual offsets;
3. raw predictions on validation origins (inputs allowlisted) -> p10/p50/p90;
4. only then are actual targets and slice attributes attached (evaluation layer).

Holdout rows (origins >= 2026-07-01) are excluded at the Parquet read and guarded.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import time
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nem_forecast.config import HOLDOUT_FIRST_ORIGIN, BacktestConfig
from nem_forecast.contract import NEM_MARKET_TZ
from nem_forecast.evaluation.bootstrap import paired_block_bootstrap
from nem_forecast.evaluation.calibration import apply_offsets, fit_residual_offsets
from nem_forecast.evaluation.folds import Fold, assert_development_origins, build_folds
from nem_forecast.evaluation.metrics import coverage_accounting, summarise
from nem_forecast.models.baselines import Forecaster, make_baselines, select_inputs
from nem_forecast.paths import ProjectPaths
from nem_forecast.provenance import read_json, sha256_file, utc_now_iso, write_json_atomic
from nem_forecast.target.build import environment, git_state

TARGET = "target_dispatch_totaldemand_hourly_mean_mw"
SLICE_COLUMNS = [
    "civil_hour",
    "civil_is_weekend",
    "civil_is_public_holiday",
    "civil_date",
    "market_hour",
]
CONFIG_FILES = (
    "configs/backtest.yaml",
    "configs/features.yaml",
    "configs/feature_availability.yaml",
    "configs/forecast.yaml",
)


def holdout_start_utc() -> pd.Timestamp:
    return pd.Timestamp(
        dt.datetime.combine(HOLDOUT_FIRST_ORIGIN, dt.time(0), tzinfo=NEM_MARKET_TZ)
    ).tz_convert("UTC")


def load_design(
    paths: ProjectPaths, cfg: BacktestConfig, model_columns: list[str]
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Primary-profile design rows with origin < 2026-07-01 only (filtered at read)."""
    run = cfg.inputs.feature_run_id
    manifest_file = paths.results_runs / run / "run_manifest.json"
    manifest = read_json(manifest_file)
    if manifest.get("status") != "complete":
        raise ValueError(f"feature run {run} is not complete")
    outputs = manifest["outputs"][cfg.inputs.primary_profile]["features"]
    columns = sorted(
        {
            "origin_utc",
            "origin_market",
            "horizon",
            "target_hour_utc",
            "availability_profile",
            TARGET,
            "target_available",
            *SLICE_COLUMNS,
            *model_columns,
        }
    )
    cutoff = holdout_start_utc()
    parts = []
    for out in outputs:
        path = paths.root / out["path"]
        if sha256_file(path) != out["sha256"]:
            raise ValueError(f"feature output hash mismatch: {out['path']}")
        parts.append(pd.read_parquet(path, columns=columns, filters=[("origin_utc", "<", cutoff)]))
    design = pd.concat(parts, ignore_index=True).sort_values(
        ["origin_utc", "horizon"], kind="mergesort"
    )
    if set(design["availability_profile"]) != {cfg.inputs.primary_profile}:
        raise ValueError("design rows are not all from the primary profile")
    design["origin_date"] = design["origin_market"].dt.date
    assert_development_origins(design["origin_date"])
    info = {
        "feature_run_id": run,
        "feature_run_manifest_sha256": sha256_file(manifest_file),
        "feature_outputs_verified": [{"path": o["path"], "sha256": o["sha256"]} for o in outputs],
        "rows_loaded": len(design),
        "max_origin_loaded": str(max(design["origin_date"])),
        "holdout_rows_loaded": 0,
    }
    return design.reset_index(drop=True), info


def _rows(design: pd.DataFrame, first: dt.date | None, last: dt.date) -> pd.DataFrame:
    mask = design["origin_date"] <= last
    if first is not None:
        mask &= design["origin_date"] >= first
    return design[mask]


def _actual(frame: pd.DataFrame) -> pd.Series:
    return frame[TARGET].where(frame["target_available"])


def run_fold(
    design: pd.DataFrame, fold: Fold, cfg: BacktestConfig
) -> tuple[pd.DataFrame, list[dict[str, Any]], list[pd.DataFrame], float]:
    weight_rows = _rows(design, None, fold.weight_fit_last_origin)
    cal_rows = _rows(design, fold.calibration_first_origin, fold.calibration_last_origin)
    val_rows = _rows(design, fold.validation_first_origin, fold.validation_last_origin)
    training = _rows(design, None, fold.last_matured_origin)
    threshold = float(
        np.quantile(_actual(training).dropna(), cfg.slices.high_demand_training_quantile)
    )
    assert cal_rows["origin_date"].nunique() == cfg.calibration.window_days
    assert max(cal_rows["origin_date"]) < fold.validation_first_origin
    preds, fits, offsets = [], [], []
    for model in make_baselines(cfg.hybrid.weight_grid.values):
        model.fit(select_inputs(weight_rows, model), _actual(weight_rows))
        cal_pred = model.predict(select_inputs(cal_rows, model))
        table = fit_residual_offsets(
            cal_rows["horizon"],
            cal_pred["point_raw"],
            _actual(cal_rows),
            cfg.calibration.min_samples_per_horizon,
        )
        table.insert(0, "model", model.name)
        table.insert(0, "fold_id", fold.fold_id)
        table["calibration_first_origin"] = fold.calibration_first_origin.isoformat()
        table["calibration_last_origin"] = fold.calibration_last_origin.isoformat()
        offsets.append(table)
        raw = model.predict(select_inputs(val_rows, model))  # inputs only: no target
        raw = pd.concat(
            [val_rows[["origin_utc", "horizon", "target_hour_utc", "origin_date"]], raw], axis=1
        )
        out = apply_offsets(raw, table)
        out.insert(0, "model", model.name)
        out.insert(0, "fold_id", fold.fold_id)
        weight = model.params().get("weight")
        out["weight"] = float(weight) if isinstance(weight, int | float) else np.nan
        preds.append(out)
        fits.append(
            {"fold_id": fold.fold_id, "model": model.name, **_fit_record(model, weight_rows)}
        )
    return pd.concat(preds, ignore_index=True), fits, offsets, threshold


def _fit_record(model: Forecaster, weight_rows: pd.DataFrame) -> dict[str, Any]:
    params = model.params()
    if not params:
        return {"fitted": False}
    return {
        "fitted": True,
        "fit_first_origin": str(min(weight_rows["origin_date"])),
        "fit_last_origin": str(max(weight_rows["origin_date"])),
        **params,
    }


def attach_evaluation(
    preds: pd.DataFrame, design: pd.DataFrame, thresholds: dict[int, float], cfg: BacktestConfig
) -> pd.DataFrame:
    """Evaluation layer: actuals and slice attributes are joined only after prediction."""
    attrs = design[["origin_utc", "horizon", *SLICE_COLUMNS]].assign(actual=_actual(design))
    ev = preds.merge(attrs, on=["origin_utc", "horizon"], how="left", validate="many_to_one")
    ev["is_civil_evening"] = ev["civil_hour"].isin(cfg.slices.evening_civil_start_hours)
    ev["day_type"] = np.where(
        ev["civil_is_public_holiday"],
        "holiday",
        np.where(ev["civil_is_weekend"], "weekend", "weekday"),
    )
    month_to_season = {m: s for s, ms in cfg.slices.seasons_by_civil_month.items() for m in ms}
    ev["season"] = pd.to_datetime(ev["civil_date"]).dt.month.map(month_to_season)
    ev["high_demand_threshold_mw"] = ev["fold_id"].map(thresholds)
    ev["is_high_demand"] = ev["actual"] >= ev["high_demand_threshold_mw"]
    return ev


def _table(ev: pd.DataFrame, by: list[str]) -> list[dict[str, Any]]:
    return [
        {**dict(zip(by, key if isinstance(key, tuple) else (key,), strict=True)), **summarise(g)}
        for key, g in ev.groupby(by, sort=True)
    ]


def comparisons(ev: pd.DataFrame, cfg: BacktestConfig) -> dict[str, Any]:
    models = sorted(ev["model"].unique())
    coverage = {m: coverage_accounting(g) for m, g in ev.groupby("model")}
    coverage_by_horizon = {
        m: {int(str(h)): float(gh["available"].mean()) for h, gh in g.groupby("horizon")}
        for m, g in ev.groupby("model")
    }
    full_models = [
        m for m in models if coverage[m]["predicted_rows"] == coverage[m]["expected_rows"]
    ]
    wide = ev.pivot_table(
        index=["origin_utc", "horizon"], columns="model", values="available", aggfunc="first"
    )
    common_keys = wide[wide.all(axis=1)].index
    keyed = ev.set_index(["origin_utc", "horizon"])
    common = keyed.loc[keyed.index.isin(common_keys)].reset_index()
    full = ev[ev["model"].isin(full_models)]
    out: dict[str, Any] = {
        "models": models,
        "coverage": coverage,
        "coverage_by_horizon": coverage_by_horizon,
        "full_horizon_models": full_models,
        "full_horizon_comparison": {m: summarise(g) for m, g in full.groupby("model")},
        "common_rows": len(common_keys),
        "common_row_horizons": sorted({int(h) for h in common["horizon"]}),
        "common_row_comparison": {m: summarise(g) for m, g in common.groupby("model")},
        "own_rows": {m: summarise(g) for m, g in ev.groupby("model")},
        "slices_common_rows": {
            dim: _table(common, ["model", dim])
            for dim in (
                "fold_id",
                "horizon",
                "market_hour",
                "is_civil_evening",
                "day_type",
                "season",
                "is_high_demand",
            )
        },
        "by_horizon_own_rows": _table(ev, ["model", "horizon"]),
        "by_fold_own_rows": _table(ev, ["model", "fold_id"]),
    }
    boot = cfg.bootstrap
    pairs = []
    keyed_err = ev.assign(err=ev["p50"] - ev["actual"]).set_index(
        ["model", "origin_utc", "horizon"]
    )

    def paired(a: str, b: str, keys: pd.Index, label: str) -> dict[str, Any]:
        ea = keyed_err.loc[a].loc[keys]
        eb = keyed_err.loc[b].loc[keys]
        res = paired_block_bootstrap(
            ea["err"], eb["err"], ea["origin_date"], boot.replicates, boot.seed, boot.confidence
        )
        return {"model_a": a, "model_b": b, "row_set": label, **res}

    full_keys = (
        full[full["model"] == full_models[0]].set_index(["origin_utc", "horizon"]).index
        if full_models
        else pd.Index([])
    )
    for a, b in combinations(full_models, 2):
        pairs.append(paired(a, b, full_keys, "full_horizon"))
    for a in full_models:
        if "previous_day" in models:
            pairs.append(paired(a, "previous_day", common_keys, "common_rows_h1_23"))
    out["paired_bootstrap"] = pairs
    return out


def run_backtest(
    paths: ProjectPaths, cfg: BacktestConfig, run_id: str | None = None
) -> dict[str, Any]:
    started = time.monotonic()
    run_id = run_id or f"phase4_backtest_{utc_now_iso().replace(':', '').replace('-', '')}"
    run_dir = paths.results_runs / run_id
    if run_dir.exists():
        raise FileExistsError(f"{run_dir} exists; run IDs are never reused")
    run_dir.mkdir(parents=True)
    created = utc_now_iso()
    try:
        folds = build_folds(cfg)
        model_columns = sorted(
            {c for m in make_baselines(cfg.hybrid.weight_grid.values) for c in m.input_columns}
        )
        design, input_info = load_design(paths, cfg, model_columns)
        preds, fits, offsets, thresholds = [], [], [], {}
        for fold in folds:
            p, f, o, t = run_fold(design, fold, cfg)
            preds.append(p)
            fits += f
            offsets += o
            thresholds[fold.fold_id] = t
        predictions = pd.concat(preds, ignore_index=True)
        assert_development_origins(predictions["origin_date"])
        offsets_table = pd.concat(offsets, ignore_index=True)
        ev = attach_evaluation(predictions, design, thresholds, cfg)
        results = comparisons(ev, cfg)

        predictions.drop(columns=["origin_date"]).to_parquet(
            run_dir / "predictions.parquet", index=False
        )
        ev.drop(columns=["civil_date"]).to_parquet(run_dir / "evaluation_rows.parquet", index=False)
        offsets_table.to_parquet(run_dir / "residual_quantiles.parquet", index=False)
        (run_dir / "residual_quantiles.json").write_text(
            offsets_table.to_json(orient="records", indent=1)
        )
        (run_dir / "folds.json").write_text(
            json.dumps(
                [f.as_dict() | {"high_demand_threshold_mw": thresholds[f.fold_id]} for f in folds],
                indent=2,
            )
        )
        (run_dir / "fitted_parameters.json").write_text(json.dumps(fits, indent=2, default=str))
        (run_dir / "backtest_config.resolved.json").write_text(cfg.model_dump_json(indent=2))
        (run_dir / "metrics.json").write_text(json.dumps(results, indent=2, default=str))
        from nem_forecast.evaluation.report import (
            write_readable_reports,
        )  # local: matplotlib import

        write_readable_reports(run_dir, results, fits, offsets_table, ev, folds)
        outputs = {p.name: sha256_file(p) for p in sorted(run_dir.rglob("*")) if p.is_file()}
        tag = _git(paths.root, "rev-parse", f"{cfg.inputs.feature_contract_tag}")
        tag_commit = _git(paths.root, "rev-parse", f"{cfg.inputs.feature_contract_tag}^{{commit}}")
        manifest = {
            "run_id": run_id,
            "phase": "4",
            "status": "complete",
            "created_utc": created,
            "completed_utc": utc_now_iso(),
            "runtime_seconds": round(time.monotonic() - started, 2),
            "holdout_guard": {
                "holdout_first_origin": HOLDOUT_FIRST_ORIGIN.isoformat(),
                "max_origin_loaded": input_info["max_origin_loaded"],
                "max_origin_predicted": str(max(predictions["origin_date"])),
                "holdout_rows_loaded": 0,
            },
            "inputs": input_info,
            "feature_contract": {
                "tag": cfg.inputs.feature_contract_tag,
                "tag_object": tag,
                "commit": tag_commit,
            },
            "config_hashes": {f: sha256_file(paths.root / f) for f in CONFIG_FILES},
            "resolved_config": cfg.model_dump(mode="json"),
            "code": git_state(paths.root),
            "environment": environment(),
            "folds": [f.as_dict() for f in folds],
            "rows": {
                "predictions": len(predictions),
                "evaluation_rows": len(ev),
                "validation_origins": int(predictions["origin_date"].nunique()),
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


def _git(root: Path, *args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
