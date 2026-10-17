"""Final pre-holdout artefact for the tagged candidate (lightgbm-development-v1).

The holdout start H = 2026-07-01 is treated as one more fold start and the frozen
development rules are applied unchanged:

* matured outcomes only: origins <= H - 2 d;
* tuning window T = 90 origins before the calibration window; inner training <= T_start - 2 d;
* calibration window C = the 180 most recent matured origins; calibration model trained on
  origins <= C_start - 2 d gives out-of-sample residuals on C (trailing offsets);
* final p10/p50/p90 models trained on all eligible origins <= H - 2 d;
* hybrid fallback: weight fitted on origins <= C_start - 1 d (Phase 4 rule) and trailing
  offsets from its residuals on C.

Inputs are read with the holdout filter (``load_design``); no row at or after H is
loaded, predicted or evaluated. Fixture predictions are for pre-holdout origins only.
"""

from __future__ import annotations

import datetime as dt
import importlib.metadata
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from nem_forecast.config import HOLDOUT_FIRST_ORIGIN, BacktestConfig, LightGBMProtocol
from nem_forecast.evaluation.backtest import TARGET
from nem_forecast.evaluation.calibration import fit_residual_offsets
from nem_forecast.evaluation.folds import Fold, HoldoutAccessError, last_matured_origin
from nem_forecast.evaluation.phase6 import (
    QNAME,
    QUANTILES,
    _between,
    _eligible,
    _fit_quantiles,
    _predict_raw,
    _residuals,
    nested_windows,
    residual_offsets,
    select_structure,
)
from nem_forecast.models.baselines import DayWeekHybrid, select_inputs
from nem_forecast.models.lightgbm_quantile import feature_matrix, predict
from nem_forecast.provenance import sha256_file, utc_now_iso, write_json_atomic

FIXTURE_TOLERANCE_MW = 1e-9
FIXTURE_ORIGINS = 7  # last matured pre-holdout origins (in-sample), for save/load checks
PROPOSED_HOLDOUT_COMMAND = (
    "nem-forecast evaluate-holdout --owner-approved-final-evaluation --artefact-run-id {run_id}"
)


def virtual_fold(holdout_first: dt.date, maturity_lag_hours: int, window_days: int) -> Fold:
    matured = last_matured_origin(holdout_first, maturity_lag_hours)
    cal_first = matured - dt.timedelta(days=window_days - 1)
    return Fold(
        fold_id=0,
        validation_first_origin=holdout_first,
        validation_last_origin=holdout_first,  # never predicted or evaluated here
        maturity_cutoff_market=dt.datetime.combine(holdout_first, dt.time(0))
        - dt.timedelta(hours=maturity_lag_hours),
        last_matured_origin=matured,
        calibration_first_origin=cal_first,
        calibration_last_origin=matured,
        weight_fit_last_origin=cal_first - dt.timedelta(days=1),
    )


def pre_holdout_only(design: pd.DataFrame, holdout_first: dt.date) -> pd.DataFrame:
    """Defensive guard: the artefact never sees an origin at or after the holdout start."""
    if (design["origin_date"] >= holdout_first).any():
        raise HoldoutAccessError("design rows at or after the holdout start were supplied")
    return design


@dataclass
class FinalArtefact:
    """Loaded artefact: hash-verified boosters plus calibration and fallback parameters."""

    allowlist: tuple[str, ...]
    boosters: dict[str, lgb.Booster]
    iterations: dict[str, int]
    offsets: pd.DataFrame  # horizon, quantile, offset, calibrated
    hybrid_weight: float
    hybrid_offsets: pd.DataFrame  # horizon, offset_q10, offset_q50, offset_q90, calibrated

    def predict(self, rows: pd.DataFrame) -> pd.DataFrame:
        """Raw and trailing-calibrated p10/p50/p90 for the selected candidate."""
        x = feature_matrix(rows, self.allowlist)
        out = rows[["origin_utc", "horizon"]].copy().reset_index(drop=True)
        for name in ("p10", "p50", "p90"):
            out[f"raw_{name}"] = predict(self.boosters[name], x, self.iterations[name])
            q = {"p10": 0.1, "p50": 0.5, "p90": 0.9}[name]
            table = self.offsets[self.offsets["quantile"] == q].set_index("horizon")
            out[name] = out[f"raw_{name}"] + out["horizon"].map(table["offset"]).to_numpy()
        return out

    def predict_fallback(self, rows: pd.DataFrame) -> pd.DataFrame:
        hybrid = DayWeekHybrid((self.hybrid_weight,))
        hybrid.weight = self.hybrid_weight
        raw = hybrid.predict(select_inputs(rows, hybrid)).reset_index(drop=True)
        out = rows[["origin_utc", "horizon"]].copy().reset_index(drop=True)
        table = self.hybrid_offsets.set_index("horizon")
        for name, col in (("p10", "offset_q10"), ("p50", "offset_q50"), ("p90", "offset_q90")):
            out[name] = raw["point_raw"] + out["horizon"].map(table[col]).to_numpy()
        out["fallback_used"] = raw["fallback_used"]
        return out


def load_final_artefact(run_dir: Path) -> FinalArtefact:
    manifest = json.loads((run_dir / "pre_evaluation_manifest.json").read_text())
    boosters = {}
    for name, info in manifest["models"].items():
        path = run_dir / info["file"]
        if sha256_file(path) != info["sha256"]:
            raise ValueError(f"model file hash mismatch: {info['file']}")
        boosters[name] = lgb.Booster(model_str=path.read_text())
    offsets = pd.read_parquet(run_dir / "trailing_offsets.parquet")
    hybrid = json.loads((run_dir / "hybrid_fallback.json").read_text())
    return FinalArtefact(
        allowlist=tuple(manifest["feature_allowlist"]),
        boosters=boosters,
        iterations={k: int(v["iterations"]) for k, v in manifest["models"].items()},
        offsets=offsets,
        hybrid_weight=float(hybrid["weight"]),
        hybrid_offsets=pd.DataFrame(hybrid["offsets"]),
    )


def _git(root: Path, *args: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def build_final_artefact(
    design: pd.DataFrame,
    run_dir: Path,
    proto: LightGBMProtocol,
    bcfg: BacktestConfig,
    provenance: dict[str, Any],
    holdout_first: dt.date = HOLDOUT_FIRST_ORIGIN,
) -> dict[str, Any]:
    """Build, save, reload and verify the artefact. ``design`` must be pre-holdout only."""
    design = pre_holdout_only(design, holdout_first)
    fold = virtual_fold(
        holdout_first, bcfg.training.maturity_lag_hours, bcfg.calibration.window_days
    )
    win = nested_windows(fold, proto, bcfg)
    allowlist = proto.feature_allowlist.columns
    tuned = select_structure(design, win, proto)
    structure, iterations = tuned["structure"], tuned["iterations"]

    cal_rows = _between(design, win.calibration_first, win.calibration_last)
    if cal_rows["origin_date"].nunique() != bcfg.calibration.window_days:
        raise ValueError("calibration window is incomplete")
    calmodel_rows = _eligible(_between(design, None, win.calmodel_train_last))
    calmodel = _fit_quantiles(calmodel_rows, proto, structure, iterations, allowlist)
    cal_raw = _predict_raw(calmodel, cal_rows, iterations, allowlist)
    offsets = residual_offsets(
        _residuals(cal_raw, cal_rows), proto.calibration.min_samples_per_horizon, None
    )
    if not offsets["calibrated"].all():
        raise ValueError("trailing calibration has too few samples for some horizon/quantile")

    final_rows = _eligible(_between(design, None, win.final_train_last))
    final = _fit_quantiles(final_rows, proto, structure, iterations, allowlist)

    weight_rows = _between(design, None, fold.weight_fit_last_origin)
    hybrid = DayWeekHybrid(bcfg.hybrid.weight_grid.values)
    hybrid.fit(
        select_inputs(weight_rows, hybrid),
        weight_rows[TARGET].where(weight_rows["target_available"]),
    )
    hyb_cal = hybrid.predict(select_inputs(cal_rows, hybrid))
    hyb_offsets = fit_residual_offsets(
        cal_rows["horizon"],
        hyb_cal["point_raw"],
        cal_rows[TARGET].where(cal_rows["target_available"]),
        bcfg.calibration.min_samples_per_horizon,
    )

    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "models").mkdir()
    models = {}
    for q in QUANTILES:
        name = QNAME[q]
        path = run_dir / "models" / f"lightgbm_{name}.txt"
        path.write_text(final[q].model_to_string(num_iteration=iterations[q]))
        path.chmod(0o444)
        models[name] = {
            "file": str(path.relative_to(run_dir)),
            "sha256": sha256_file(path),
            "alpha": q,
            "iterations": iterations[q],
        }
    offsets.to_parquet(run_dir / "trailing_offsets.parquet", index=False)
    hybrid_doc = {
        "model": "day_week_hybrid_frozen_fallback",
        "weight": hybrid.weight,
        "weight_fit_origins": [
            str(min(weight_rows["origin_date"])),
            str(max(weight_rows["origin_date"])),
        ],
        "weight_fit_rows": hybrid.fit_rows,
        "offsets": hyb_offsets[
            ["horizon", "n_samples", "calibrated", "offset_q10", "offset_q50", "offset_q90"]
        ].to_dict("records"),
        "calibration_origins": [
            win.calibration_first.isoformat(),
            win.calibration_last.isoformat(),
        ],
        "horizon_24": "previous_week_fallback_flagged",
    }
    write_json_atomic(run_dir / "hybrid_fallback.json", hybrid_doc)

    fixture_first = win.final_train_last - dt.timedelta(days=FIXTURE_ORIGINS - 1)
    fixture = _between(design, fixture_first, win.final_train_last)
    fixture_cols = list(
        dict.fromkeys(["origin_utc", "horizon", *allowlist, "lag_168h", "lag_168h__available"])
    )
    fixture_inputs = fixture[fixture_cols].reset_index(drop=True)
    fixture_inputs.to_parquet(run_dir / "fixture_inputs.parquet", index=False)  # no target columns
    in_memory = _predict_raw(final, fixture, iterations, allowlist).reset_index(drop=True)
    write_json_atomic(
        run_dir / "pre_evaluation_manifest.json",
        {"models": models, "feature_allowlist": list(allowlist)},
    )
    loaded = load_final_artefact(run_dir)
    reloaded = loaded.predict(fixture_inputs)
    max_diff = max(
        float(np.max(np.abs(reloaded[f"raw_{n}"].to_numpy() - in_memory[f"raw_{n}"].to_numpy())))
        for n in ("p10", "p50", "p90")
    )
    if max_diff > FIXTURE_TOLERANCE_MW:
        raise ValueError(f"save/load mismatch {max_diff} MW exceeds {FIXTURE_TOLERANCE_MW}")
    reloaded.to_parquet(run_dir / "fixture_predictions.parquet", index=False)
    loaded.predict_fallback(fixture.reset_index(drop=True)).to_parquet(
        run_dir / "fixture_fallback_predictions.parquet", index=False
    )

    (run_dir / "pre_evaluation_manifest.json").unlink()  # replaced by the full manifest below
    manifest = {
        "artefact": "final pre-holdout artefact",
        "candidate": "lightgbm_trailing_calibrated",
        "fallback": "day_week_hybrid_frozen",
        "created_utc": utc_now_iso(),
        "holdout": {
            "first_origin": holdout_first.isoformat(),
            "last_origin": "2026-09-30",
            "holdout_rows_loaded": 0,
            "holdout_evaluated": False,
            "max_origin_loaded": str(max(design["origin_date"])),
        },
        "date_ranges": {
            "inner_training_origins": [
                str(min(design["origin_date"])),
                win.inner_train_last.isoformat(),
            ],
            "tuning_origins": [win.tuning_first.isoformat(), win.tuning_last.isoformat()],
            "calibration_model_training_origins": [
                str(min(design["origin_date"])),
                win.calmodel_train_last.isoformat(),
            ],
            "calibration_origins": [
                win.calibration_first.isoformat(),
                win.calibration_last.isoformat(),
            ],
            "final_training_origins": [
                str(min(final_rows["origin_date"])),
                win.final_train_last.isoformat(),
            ],
            "maturity_cutoff_market": fold.maturity_cutoff_market.isoformat(),
            "latest_training_target_hour_end_market": (
                f"{win.final_train_last + dt.timedelta(days=1)}T00:00:00+10:00"
            ),
        },
        "rows": {
            "inner_training": tuned["rows"]["inner_train"],
            "tuning": tuned["rows"]["tuning"],
            "calibration_model_training": len(calmodel_rows),
            "calibration": len(cal_rows),
            "final_training": len(final_rows),
            "fixture": len(fixture_inputs),
        },
        "selection": {
            "candidates": tuned["candidates"],
            "selected": tuned["selected"],
            "structure": structure,
            "tail_tuning": tuned["tail_tuning"],
        },
        "models": models,
        "feature_allowlist": list(allowlist),
        "fixed_lightgbm_params": dict(proto.lightgbm.fixed_params),
        "trailing_offsets": offsets.to_dict("records"),
        "hybrid_fallback": {
            "file": "hybrid_fallback.json",
            "sha256": sha256_file(run_dir / "hybrid_fallback.json"),
            "weight": hybrid.weight,
        },
        "fixture": {
            "origins": [fixture_first.isoformat(), win.final_train_last.isoformat()],
            "tolerance_mw": FIXTURE_TOLERANCE_MW,
            "max_reload_difference_mw": max_diff,
            "inputs_sha256": sha256_file(run_dir / "fixture_inputs.parquet"),
            "predictions_sha256": sha256_file(run_dir / "fixture_predictions.parquet"),
            "fallback_predictions_sha256": sha256_file(
                run_dir / "fixture_fallback_predictions.parquet"
            ),
        },
        "environment": {"lightgbm": importlib.metadata.version("lightgbm")},
        **provenance,
        "proposed_holdout_command": PROPOSED_HOLDOUT_COMMAND.format(run_id=run_dir.name),
    }
    write_json_atomic(run_dir / "pre_evaluation_manifest.json", manifest)
    (run_dir / "pre_evaluation_manifest.json").chmod(0o444)
    return manifest
