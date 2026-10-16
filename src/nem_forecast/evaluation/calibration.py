"""Empirical additive residual calibration (per model and horizon)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

QUANTILES = (0.1, 0.5, 0.9)


def fit_residual_offsets(
    horizon: pd.Series, raw: pd.Series, actual: pd.Series, min_samples: int
) -> pd.DataFrame:
    """Per-horizon empirical quantiles of ``actual - raw`` (numpy linear interpolation).

    Rows without a raw forecast or an actual are ignored. Horizons with fewer than
    ``min_samples`` residuals are marked not calibrated (no offsets).
    """
    residual = (actual - raw).to_numpy(dtype=float)
    ok = np.isfinite(residual)
    rows: list[dict[str, Any]] = []
    for h in range(1, 25):
        values = residual[ok & (horizon.to_numpy() == h)]
        entry: dict[str, Any] = {
            "horizon": h,
            "n_samples": len(values),
            "calibrated": len(values) >= min_samples,
        }
        for q in QUANTILES:
            entry[f"offset_q{int(q * 100):02d}"] = (
                float(np.quantile(values, q, method="linear")) if entry["calibrated"] else np.nan
            )
        rows.append(entry)
    return pd.DataFrame(rows)


def apply_offsets(predictions: pd.DataFrame, offsets: pd.DataFrame) -> pd.DataFrame:
    """Add p10/p50/p90 to raw point forecasts; uncalibrated horizons become unavailable."""
    merged = predictions.merge(
        offsets[["horizon", "calibrated", "offset_q10", "offset_q50", "offset_q90"]],
        on="horizon",
        how="left",
    )
    usable = merged["available"] & merged["calibrated"].fillna(False).astype(bool)
    for q in ("10", "50", "90"):
        merged[f"p{q}"] = np.where(usable, merged["point_raw"] + merged[f"offset_q{q}"], np.nan)
    reason = merged["unavailable_reason"].where(
        ~merged["available"] | usable, "insufficient_calibration_samples"
    )
    merged["unavailable_reason"] = reason
    merged["available"] = usable
    return merged.drop(columns=["calibrated", "offset_q10", "offset_q50", "offset_q90"])
