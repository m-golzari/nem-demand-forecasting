"""Seasonal-naive baselines behind one forecaster interface (Phase 4).

Each forecaster declares a strict **input allowlist**. Prediction receives only
the row keys plus those columns (built by :func:`select_inputs`), and refuses
any frame that carries anything else. In particular it never sees the target,
lineage/audit columns or other features. Targets are attached in the
evaluation layer after predictions exist. Only ``fit`` (hybrid weight selection
on matured training rows) receives outcomes, and those are passed separately.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

import numpy as np
import pandas as pd

KEYS = ("origin_utc", "horizon")
PRED_COLUMNS = ["point_raw", "available", "unavailable_reason", "fallback_used"]


class InputAllowlistError(ValueError):
    """Raised when a forecaster receives a column outside its allowlist."""


def select_inputs(frame: pd.DataFrame, model: Forecaster) -> pd.DataFrame:
    """The only way design rows are handed to a forecaster."""
    return frame.loc[:, [*KEYS, *model.input_columns]].copy()


def _feature_pair(name: str) -> tuple[str, str]:
    return name, f"{name}__available"


class Forecaster(ABC):
    name: ClassVar[str]
    input_columns: ClassVar[tuple[str, ...]]

    def _check(self, inputs: pd.DataFrame) -> None:
        extra = set(inputs.columns) - {*KEYS, *self.input_columns}
        missing = {*KEYS, *self.input_columns} - set(inputs.columns)
        if extra or missing:
            raise InputAllowlistError(
                f"{self.name}: unexpected {sorted(extra)} / missing {sorted(missing)} columns"
            )

    def fit(self, inputs: pd.DataFrame, actual: pd.Series) -> Forecaster:
        """Default: nothing to fit (seasonal-naive)."""
        self._check(inputs)
        return self

    def predict(self, inputs: pd.DataFrame) -> pd.DataFrame:
        self._check(inputs)
        out = self._predict(inputs)
        return out.reindex(columns=PRED_COLUMNS).set_axis(inputs.index)

    @abstractmethod
    def _predict(self, inputs: pd.DataFrame) -> pd.DataFrame: ...

    def params(self) -> dict[str, object]:
        return {}


def _single(inputs: pd.DataFrame, feature: str) -> pd.DataFrame:
    value, flag = _feature_pair(feature)
    available = inputs[flag].to_numpy(dtype=bool) & np.isfinite(inputs[value].to_numpy(dtype=float))
    return pd.DataFrame(
        {
            "point_raw": np.where(available, inputs[value].to_numpy(dtype=float), np.nan),
            "available": available,
            "unavailable_reason": np.where(available, "", f"{feature}_unavailable"),
            "fallback_used": False,
        }
    )


class PreviousDay(Forecaster):
    """``lag_24h`` exactly. Unavailable at h = 24 under the primary profile; never substituted."""

    name = "previous_day"
    input_columns = _feature_pair("lag_24h")

    def _predict(self, inputs: pd.DataFrame) -> pd.DataFrame:
        return _single(inputs, "lag_24h")


class PreviousWeek(Forecaster):
    name = "previous_week"
    input_columns = _feature_pair("lag_168h")

    def _predict(self, inputs: pd.DataFrame) -> pd.DataFrame:
        return _single(inputs, "lag_168h")


class SameWeekday4w(Forecaster):
    name = "same_weekday_4w"
    input_columns = _feature_pair("same_hour_same_weekday_mean_4w")

    def _predict(self, inputs: pd.DataFrame) -> pd.DataFrame:
        return _single(inputs, "same_hour_same_weekday_mean_4w")


class DayWeekHybrid(Forecaster):
    """h 1-23: ``w * lag_24h + (1 - w) * lag_168h``; h 24: flagged previous-week fallback.

    ``w`` is chosen from a frozen grid by MAE on matured rows (h 1-23 with both lags
    available), ties to the smallest ``w``. No substitution at h 1-23.
    """

    name = "day_week_hybrid"
    input_columns = (*_feature_pair("lag_24h"), *_feature_pair("lag_168h"))

    def __init__(self, grid: tuple[float, ...]) -> None:
        self.grid = grid
        self.weight: float | None = None
        self.fit_rows = 0
        self.fit_mae_by_weight: dict[str, float] = {}

    def fit(self, inputs: pd.DataFrame, actual: pd.Series) -> DayWeekHybrid:
        self._check(inputs)
        both = (
            (inputs["horizon"] <= 23)
            & inputs["lag_24h__available"].astype(bool)
            & inputs["lag_168h__available"].astype(bool)
            & actual.notna()
        ).to_numpy()
        day = inputs["lag_24h"].to_numpy(dtype=float)[both]
        week = inputs["lag_168h"].to_numpy(dtype=float)[both]
        y = actual.to_numpy(dtype=float)[both]
        if len(y) == 0:
            raise ValueError("hybrid: no eligible training rows")
        maes = {w: float(np.mean(np.abs(w * day + (1 - w) * week - y))) for w in self.grid}
        self.weight = min(self.grid, key=lambda w: (maes[w], w))
        self.fit_rows = int(both.sum())
        self.fit_mae_by_weight = {f"{w:.2f}": round(m, 6) for w, m in maes.items()}
        return self

    def _predict(self, inputs: pd.DataFrame) -> pd.DataFrame:
        if self.weight is None:
            raise RuntimeError("hybrid must be fitted before prediction")
        w = self.weight
        h24 = inputs["horizon"].to_numpy() == 24
        day_ok = inputs["lag_24h__available"].to_numpy(dtype=bool)
        week_ok = inputs["lag_168h__available"].to_numpy(dtype=bool)
        day = inputs["lag_24h"].to_numpy(dtype=float)
        week = inputs["lag_168h"].to_numpy(dtype=float)
        blend_ok = ~h24 & day_ok & week_ok
        fallback_ok = h24 & week_ok
        available = blend_ok | fallback_ok
        point = np.where(blend_ok, w * day + (1 - w) * week, np.where(fallback_ok, week, np.nan))
        reason = np.where(
            available, "", np.where(h24, "lag_168h_unavailable", "lag_24h_or_lag_168h_unavailable")
        )
        return pd.DataFrame(
            {
                "point_raw": point,
                "available": available,
                "unavailable_reason": reason,
                "fallback_used": fallback_ok,
            }
        )

    def params(self) -> dict[str, object]:
        return {
            "weight": self.weight,
            "fit_rows": self.fit_rows,
            "fit_mae_by_weight": self.fit_mae_by_weight,
        }


def make_baselines(grid: tuple[float, ...]) -> list[Forecaster]:
    return [PreviousDay(), PreviousWeek(), SameWeekday4w(), DayWeekHybrid(grid)]
