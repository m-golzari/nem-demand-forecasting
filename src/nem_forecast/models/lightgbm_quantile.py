"""Direct LightGBM quantile models with a positive feature allowlist (Phase 6).

Only the allowlisted columns, in the frozen allowlist order, are converted to the
model matrix. Any other column a caller passes (targets, lineage, audit fields,
unregistered columns) is never read, and column order in the input frame has no
effect. Missing values (``lag_24h`` at h = 24 under ``primary_operational``)
stay NaN; LightGBM handles them natively. Nothing is zero-filled or substituted.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd


def feature_matrix(frame: pd.DataFrame, allowlist: tuple[str, ...]) -> np.ndarray:
    """Positive selection in allowlist order; booleans become 0/1; NaN preserved."""
    missing = [c for c in allowlist if c not in frame.columns]
    if missing:
        raise KeyError(f"allowlisted columns missing from input: {missing}")
    return np.column_stack([frame[c].to_numpy(dtype=float, na_value=np.nan) for c in allowlist])


def pinball(alpha: float, prediction: np.ndarray, actual: np.ndarray) -> float:
    diff = actual - prediction
    return float(np.mean(np.maximum(alpha * diff, (alpha - 1) * diff)))


@dataclass
class FitResult:
    booster: lgb.Booster
    best_iteration: int
    seconds: float
    history: list[float] = field(default_factory=list)


def lgb_params(fixed: dict[str, Any], structure: dict[str, Any], alpha: float) -> dict[str, Any]:
    return {**fixed, **structure, "objective": "quantile", "alpha": alpha, "metric": "quantile"}


def fit_quantile(
    x: np.ndarray,
    y: np.ndarray,
    params: dict[str, Any],
    rounds: int,
    allowlist: tuple[str, ...],
    valid: tuple[np.ndarray, np.ndarray] | None = None,
    early_stopping_rounds: int | None = None,
) -> FitResult:
    """Train one quantile booster; with ``valid`` + early stopping, return the best iteration."""
    started = time.monotonic()
    train_set = lgb.Dataset(
        x,
        label=y,
        feature_name=list(allowlist),
        free_raw_data=False,
        params={"max_bin": params.get("max_bin", 255)},
    )
    history: dict[str, dict[str, list[float]]] = {}
    callbacks = []
    valid_sets = []
    if valid is not None:
        valid_sets = [
            lgb.Dataset(valid[0], label=valid[1], reference=train_set, feature_name=list(allowlist))
        ]
        callbacks.append(lgb.record_evaluation(history))
        if early_stopping_rounds:
            callbacks.append(
                lgb.early_stopping(early_stopping_rounds, first_metric_only=True, verbose=False)
            )
    booster = lgb.train(
        params, train_set, num_boost_round=rounds, valid_sets=valid_sets, callbacks=callbacks
    )
    best = booster.best_iteration if valid is not None and booster.best_iteration > 0 else rounds
    curve = next(iter(history.values()), {}).get("quantile", []) if history else []
    return FitResult(booster, int(best), time.monotonic() - started, list(curve))


def predict(booster: lgb.Booster, x: np.ndarray, iterations: int) -> np.ndarray:
    return np.asarray(booster.predict(x, num_iteration=iterations), dtype=float)
