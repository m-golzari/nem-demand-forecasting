"""Paired daily-block bootstrap of MAE differences (blocks = market dates)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def paired_block_bootstrap(
    errors_a: pd.Series,
    errors_b: pd.Series,
    dates: pd.Series,
    replicates: int,
    seed: int,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """MAE(A) - MAE(B) on identical rows, resampling whole market dates with replacement.

    All rows (horizons) of a sampled date are kept together.
    """
    frame = pd.DataFrame(
        {
            "a": np.abs(errors_a.to_numpy(dtype=float)),
            "b": np.abs(errors_b.to_numpy(dtype=float)),
            "date": dates.to_numpy(),
        }
    )
    if frame[["a", "b"]].isna().any().any():
        raise ValueError("paired bootstrap requires both models on identical rows")
    per_date = frame.groupby("date", sort=True).agg(a=("a", "sum"), b=("b", "sum"), n=("a", "size"))
    sums_a, sums_b, n = (per_date[c].to_numpy(dtype=float) for c in ("a", "b", "n"))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(per_date), size=(replicates, len(per_date)))
    diff = (sums_a[idx].sum(axis=1) - sums_b[idx].sum(axis=1)) / n[idx].sum(axis=1)
    alpha = (1 - confidence) / 2
    observed = float((sums_a.sum() - sums_b.sum()) / n.sum())
    return {
        "mae_difference_mw": observed,
        "ci_low": float(np.quantile(diff, alpha)),
        "ci_high": float(np.quantile(diff, 1 - alpha)),
        "share_replicates_a_worse": float(np.mean(diff > 0)),
        "dates": len(per_date),
        "rows": len(frame),
        "replicates": replicates,
        "seed": seed,
    }
