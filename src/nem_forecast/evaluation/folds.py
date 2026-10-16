"""Development folds, temporal cutoffs and the final-holdout guard (Phase 4).

Folds are calendar months of forecast **origins** (all 24 horizons of an origin
stay together). For a fold whose first origin is ``F`` (00:00 market time):

* matured cutoff ``F - maturity_lag`` (operational rule at the fold's first
  origin). An origin is matured only if all 24 target hours have latest source
  endpoint ``<= F - 1 h``, i.e. origin ``<= F - 2 days``;
* calibration origins: the ``window_days`` most recent matured origins;
* hybrid weight-fit origins: every matured origin before the calibration window
  (expanding);
* validation origins: every origin in the fold's month.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from nem_forecast.config import HOLDOUT_FIRST_ORIGIN, BacktestConfig

HORIZONS = 24


class HoldoutAccessError(RuntimeError):
    """Raised when a development step touches an origin in the final holdout."""


def assert_development_origins(origins: pd.Series | list[dt.date]) -> None:
    """Hard guard: no origin on or after 2026-07-01 may enter the development backtest."""
    dates = (
        pd.to_datetime(pd.Series(list(origins))).dt.date
        if not isinstance(origins, pd.Series)
        else origins
    )
    bad = [d for d in dates if d >= HOLDOUT_FIRST_ORIGIN]
    if bad:
        raise HoldoutAccessError(
            f"{len(bad)} origin(s) on/after {HOLDOUT_FIRST_ORIGIN} requested by the development "
            "backtest; final-holdout evaluation requires explicit owner approval"
        )


@dataclass(frozen=True)
class Fold:
    fold_id: int
    validation_first_origin: dt.date
    validation_last_origin: dt.date
    maturity_cutoff_market: dt.datetime  # F - maturity_lag (UTC+10)
    last_matured_origin: dt.date
    calibration_first_origin: dt.date
    calibration_last_origin: dt.date
    weight_fit_last_origin: dt.date

    def as_dict(self) -> dict[str, Any]:
        return {
            k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in asdict(self).items()
        }


def last_matured_origin(first_origin: dt.date, maturity_lag_hours: int) -> dt.date:
    """Latest 00:00 origin whose final target hour ends ``<= first_origin - lag``."""
    cutoff = dt.datetime.combine(first_origin, dt.time(0)) - dt.timedelta(hours=maturity_lag_hours)
    # final target hour of origin O is O + 23 h, ending O + 24 h; need O + 24 h <= cutoff
    latest = cutoff - dt.timedelta(hours=HORIZONS)
    return latest.date()  # 00:00 origins: the latest one not after `latest`


def build_folds(cfg: BacktestConfig) -> list[Fold]:
    dev = cfg.development
    lag = cfg.training.maturity_lag_hours
    window = cfg.calibration.window_days
    folds = []
    first = dev.first_origin
    fold_id = 1
    while first <= dev.last_origin:
        nxt = dt.date(first.year + (first.month == 12), first.month % 12 + 1, 1)
        last = min(nxt - dt.timedelta(days=1), dev.last_origin)
        matured = last_matured_origin(first, lag)
        cal_first = matured - dt.timedelta(days=window - 1)
        folds.append(
            Fold(
                fold_id=fold_id,
                validation_first_origin=first,
                validation_last_origin=last,
                maturity_cutoff_market=dt.datetime.combine(first, dt.time(0))
                - dt.timedelta(hours=lag),
                last_matured_origin=matured,
                calibration_first_origin=cal_first,
                calibration_last_origin=matured,
                weight_fit_last_origin=cal_first - dt.timedelta(days=1),
            )
        )
        first, fold_id = nxt, fold_id + 1
    assert_development_origins([f.validation_last_origin for f in folds])
    return folds
