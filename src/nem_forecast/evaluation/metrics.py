"""Point and probabilistic forecast metrics (errors are forecast - actual)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def mae(f: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean(np.abs(f - y)))


def rmse(f: np.ndarray, y: np.ndarray) -> float:
    return float(np.sqrt(np.mean((f - y) ** 2)))


def wape(f: np.ndarray, y: np.ndarray) -> float:
    """Aggregate WAPE = sum(|f - y|) / sum(y)."""
    return float(np.sum(np.abs(f - y)) / np.sum(y))


def mean_signed_error(f: np.ndarray, y: np.ndarray) -> float:
    """Bias: positive means over-forecasting."""
    return float(np.mean(f - y))


def pinball(q: float, f: np.ndarray, y: np.ndarray) -> float:
    diff = y - f
    return float(np.mean(np.maximum(q * diff, (q - 1) * diff)))


def coverage(lo: np.ndarray, hi: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((y >= lo) & (y <= hi)))


def crossing_rate(p10: np.ndarray, p50: np.ndarray, p90: np.ndarray) -> float:
    return float(np.mean((p10 > p50) | (p50 > p90)))


def crossing_magnitude(p10: np.ndarray, p50: np.ndarray, p90: np.ndarray) -> float:
    """Mean over all rows of the total order violation max(0, p10-p50) + max(0, p50-p90)."""
    return float(np.mean(np.maximum(0, p10 - p50) + np.maximum(0, p50 - p90)))


def summarise(frame: pd.DataFrame) -> dict[str, Any]:
    """Metrics over rows with an available forecast and actual (p50 = point forecast)."""
    rows = frame[frame["available"] & frame["actual"].notna()]
    if rows.empty:
        return {"n": 0}
    y = rows["actual"].to_numpy(dtype=float)
    p10, p50, p90 = (rows[c].to_numpy(dtype=float) for c in ("p10", "p50", "p90"))
    evening = rows["is_civil_evening"].to_numpy(dtype=bool)
    high = rows["is_high_demand"].to_numpy(dtype=bool)
    return {
        "n": len(rows),
        "mae_mw": mae(p50, y),
        "rmse_mw": rmse(p50, y),
        "wape": wape(p50, y),
        "mean_signed_error_mw": mean_signed_error(p50, y),
        "evening_mae_mw": mae(p50[evening], y[evening]) if evening.any() else None,
        "high_demand_mae_mw": mae(p50[high], y[high]) if high.any() else None,
        "n_evening": int(evening.sum()),
        "n_high_demand": int(high.sum()),
        "pinball_p10": pinball(0.1, p10, y),
        "pinball_p50": pinball(0.5, p50, y),
        "pinball_p90": pinball(0.9, p90, y),
        "coverage_p10_p90": coverage(p10, p90, y),
        "mean_interval_width_mw": float(np.mean(p90 - p10)),
        "crossing_rate": crossing_rate(p10, p50, p90),
        "crossing_mean_magnitude_mw": crossing_magnitude(p10, p50, p90),
        "mean_pinball": float(
            np.mean([pinball(0.1, p10, y), pinball(0.5, p50, y), pinball(0.9, p90, y)])
        ),
    }


def coverage_accounting(frame: pd.DataFrame) -> dict[str, Any]:
    """Prediction-row and origin coverage over all expected validation rows."""
    expected = len(frame)
    available = int(frame["available"].sum())
    by_origin = frame.groupby("origin_utc")["available"].all()
    return {
        "expected_rows": expected,
        "predicted_rows": available,
        "row_coverage": available / expected if expected else None,
        "origins": len(by_origin),
        "origins_with_all_24_horizons": int(by_origin.sum()),
        "origin_coverage": float(by_origin.mean()) if len(by_origin) else None,
        "missing_by_reason": {
            str(k): int(v)
            for k, v in frame.loc[~frame["available"], "unavailable_reason"].value_counts().items()
        },
    }
