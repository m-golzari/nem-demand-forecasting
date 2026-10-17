"""Explanatory-only diagnostic of the Phase 6 development run (D-44).

Uses only the stored Phase 6 development predictions, evaluation rows and
calibration offsets (hash-verified) plus pre-holdout design rows for level
comparisons. No model is trained, no calibration is refitted, no candidate is
added, and nothing at or after 2026-07-01 is loaded.
"""

from __future__ import annotations

import json
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd

from nem_forecast.config import BacktestConfig
from nem_forecast.evaluation.backtest import TARGET, load_design
from nem_forecast.evaluation.bootstrap import paired_block_bootstrap_mean
from nem_forecast.evaluation.folds import assert_development_origins
from nem_forecast.paths import ProjectPaths
from nem_forecast.provenance import read_json, sha256_file

LGB = ("lightgbm_raw", "lightgbm_trailing_calibrated", "lightgbm_season_calibrated")
QS = {"p10": 0.1, "p50": 0.5, "p90": 0.9}
LATE_FOLDS = (11, 12)  # May and June 2026


def load_phase6(
    paths: ProjectPaths, run_id: str
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    run = paths.results_runs / run_id
    manifest = read_json(run / "run_manifest.json")
    for name in ("evaluation_rows.parquet", "calibration_offsets.parquet", "predictions.parquet"):
        if sha256_file(run / name) != manifest["outputs"][name]:
            raise ValueError(f"{name} changed since run {run_id}")
    ev = pd.read_parquet(run / "evaluation_rows.parquet")
    offsets = pd.read_parquet(run / "calibration_offsets.parquet")
    assert_development_origins(ev["origin_date"])
    return ev, offsets, manifest


def row_quantities(ev: pd.DataFrame) -> pd.DataFrame:
    y = ev["actual"]
    out = ev.copy()
    out["abs_error"] = (ev["p50"] - y).abs()
    out["signed_error"] = ev["p50"] - y
    out["covered"] = ((y >= ev["p10"]) & (y <= ev["p90"])).astype(float)
    out["lower_miss"] = (y < ev["p10"]).astype(float)
    out["upper_miss"] = (y > ev["p90"]).astype(float)
    out["width"] = ev["p90"] - ev["p10"]
    out["mean_pinball"] = np.mean(
        [np.maximum(q * (y - ev[c]), (q - 1) * (y - ev[c])) for c, q in QS.items()], axis=0
    )
    return out


def _summary(g: pd.DataFrame) -> dict[str, float]:
    return {
        "n": len(g),
        "mae_mw": float(g["abs_error"].mean()),
        "bias_mw": float(g["signed_error"].mean()),
        "coverage": float(g["covered"].mean()),
        "lower_miss_rate": float(g["lower_miss"].mean()),
        "upper_miss_rate": float(g["upper_miss"].mean()),
        "mean_width_mw": float(g["width"].mean()),
        "mean_pinball": float(g["mean_pinball"].mean()),
    }


def formula_checks(ev: pd.DataFrame, offsets: pd.DataFrame) -> dict[str, Any]:
    """Stored calibrated quantiles must equal raw + stored offset exactly (per fold/horizon/q)."""
    out: dict[str, Any] = {}
    sources = {
        "lightgbm_trailing_calibrated": ("lightgbm_trailing", "offset"),
        "lightgbm_season_calibrated": ("lightgbm_season", "applied_offset"),
        "day_week_hybrid_season_calibrated": ("hybrid_season", "applied_offset"),
    }
    for model, (cal, col) in sources.items():
        rows = ev[ev["model"] == model]
        worst = 0.0
        for q_name, q in QS.items():
            table = offsets[(offsets["calibration"] == cal) & (offsets["quantile"] == q)]
            lookup = table.set_index(["fold_id", "horizon"])[col]
            off = lookup.reindex(
                pd.MultiIndex.from_arrays([rows["fold_id"], rows["horizon"]])
            ).to_numpy()
            worst = max(
                worst,
                float(
                    np.max(
                        np.abs(rows[q_name].to_numpy() - (rows[f"raw_{q_name}"].to_numpy() + off))
                    )
                ),
            )
        out[model] = {"max_abs_difference_mw": worst, "passed": worst == 0.0}
    raw = ev[ev["model"] == "lightgbm_raw"]
    out["lightgbm_raw_equals_raw"] = bool(all((raw[c] == raw[f"raw_{c}"]).all() for c in QS))
    trailing = offsets[offsets["calibration"] == "lightgbm_trailing"].pivot_table(
        index=["fold_id", "horizon"], columns="quantile", values="offset"
    )
    # Informational: quantile-specific offsets are added to different raw quantiles, so they need
    # not be ordered; only the calibrated quantiles must be (their crossing is reported separately).
    unordered = ~((trailing[0.1] <= trailing[0.5]) & (trailing[0.5] <= trailing[0.9]))
    out["trailing_offset_cells_not_monotone_informational"] = float(unordered.mean())
    calibrated = ev[ev["model"] == "lightgbm_trailing_calibrated"]
    out["trailing_calibrated_crossing_rate"] = float(
        ((calibrated["p10"] > calibrated["p50"]) | (calibrated["p50"] > calibrated["p90"])).mean()
    )
    season = offsets[offsets["calibration"] == "lightgbm_season"]
    out["season_samples_within_150_270"] = bool(season["n_samples"].between(150, 270).all())
    out["season_all_from_season_source"] = bool((season["source"] == "season").all())
    out["trailing_samples"] = sorted(
        {int(n) for n in offsets.loc[offsets["calibration"] == "lightgbm_trailing", "n_samples"]}
    )
    return out


def bias_attribution(ev: pd.DataFrame) -> list[dict[str, Any]]:
    """Per fold: calibrated bias = raw p50 bias + mean applied p50 offset (identity check)."""
    rows = []
    for (model, fold), g in ev[ev["model"].isin(LGB[1:])].groupby(["model", "fold_id"]):
        raw_bias = float((g["raw_p50"] - g["actual"]).mean())
        offset = float((g["p50"] - g["raw_p50"]).mean())
        cal_bias = float((g["p50"] - g["actual"]).mean())
        rows.append(
            {
                "model": model,
                "fold_id": int(str(fold)),
                "raw_p50_bias_mw": raw_bias,
                "mean_p50_offset_mw": offset,
                "calibrated_bias_mw": cal_bias,
                "identity_error_mw": cal_bias - (raw_bias + offset),
            }
        )
    return rows


def run_diagnostic(paths: ProjectPaths, bcfg: BacktestConfig, phase6_run: str) -> dict[str, Any]:
    ev, offsets, manifest = load_phase6(paths, phase6_run)
    ev = row_quantities(ev)
    boot = bcfg.bootstrap
    keyed = ev.set_index(["model", "origin_utc", "horizon"]).sort_index()
    pairs = []
    for a, b in combinations(LGB, 2):
        ga, gb = keyed.loc[a], keyed.loc[b].loc[keyed.loc[a].index]
        entry: dict[str, Any] = {"model_a": a, "model_b": b}
        for quantity in (
            "mean_pinball",
            "abs_error",
            "signed_error",
            "covered",
            "width",
            "lower_miss",
            "upper_miss",
        ):
            entry[quantity] = paired_block_bootstrap_mean(
                ga[quantity],
                gb[quantity],
                ga["origin_date"],
                boot.replicates,
                boot.seed,
                boot.confidence,
            )
        pairs.append(entry)
    overall = {m: _summary(g) for m, g in ev.groupby("model")}
    by_fold = [
        {"model": m, "fold_id": int(str(f)), **_summary(g)}
        for (m, f), g in ev.groupby(["model", "fold_id"])
    ]
    late = ev[ev["fold_id"].isin(LATE_FOLDS)]
    late_by_horizon = [
        {"model": m, "fold_id": int(str(f)), "horizon": int(str(h)), **_summary(g)}
        for (m, f, h), g in late[late["model"].isin(LGB)].groupby(["model", "fold_id", "horizon"])
    ]
    decomposition = []
    for m, g in ev.groupby("model"):
        early, recent = g[~g["fold_id"].isin(LATE_FOLDS)], g[g["fold_id"].isin(LATE_FOLDS)]
        e, r = _summary(early), _summary(recent)
        decomposition.append(
            {
                "model": m,
                "folds_1_10": e,
                "folds_11_12": r,
                "mae_ratio": r["mae_mw"] / e["mae_mw"],
                "width_ratio": r["mean_width_mw"] / e["mean_width_mw"],
                "error_to_width_ratio_change": (r["mae_mw"] / r["mean_width_mw"])
                / (e["mae_mw"] / e["mean_width_mw"]),
            }
        )
    design, _ = load_design(paths, bcfg, ["lag_24h"])  # pre-holdout rows only (filtered + guarded)
    design["month"] = pd.to_datetime(design["origin_date"]).dt.to_period("M").astype(str)
    level = (
        design[design["target_available"]]
        .groupby("month")[TARGET]
        .agg(["mean", "std"])
        .reset_index()
    )
    return {
        "phase6_run": phase6_run,
        "phase6_manifest_sha256": sha256_file(
            paths.results_runs / phase6_run / "run_manifest.json"
        ),
        "scope": "explanatory only (D-44); stored development predictions; no refit; no holdout",
        "holdout_rows_loaded": 0,
        "formula_checks": formula_checks(ev, offsets),
        "bias_attribution": bias_attribution(ev),
        "paired_differences": pairs,
        "overall": overall,
        "by_fold": by_fold,
        "late_folds_by_horizon": late_by_horizon,
        "late_fold_decomposition": decomposition,
        "monthly_target_level": level.to_dict("records"),
        "phase6_code_commit": manifest["code"]["commit"],
    }


def write(result: dict[str, Any], path: Any) -> None:
    path.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
