"""Executable form of the forecast origin/horizon contract.

For a market date ``D`` the origin is ``D 00:00`` NEM market time. Target
hour ``h`` (1..24) is the left-closed interval
``[origin + (h - 1) h, origin + h h)``, labelled by its start. Because market
time has no daylight saving, every origin has exactly 24 one-hour targets.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from nem_forecast.config import ForecastConfig

_ONE_HOUR = dt.timedelta(hours=1)


@dataclass(frozen=True)
class TargetHour:
    """One forecast target: hourly mean MW over ``[start_utc, end_utc)``."""

    horizon: int
    start_utc: dt.datetime
    end_utc: dt.datetime
    start_market: dt.datetime


def forecast_origin(market_date: dt.date, config: ForecastConfig) -> dt.datetime:
    """Return the timezone-aware origin instant for a market date."""
    return dt.datetime.combine(
        market_date, config.forecast.origin_time, tzinfo=config.time.market_timezone
    )


def target_hours(market_date: dt.date, config: ForecastConfig) -> list[TargetHour]:
    """Return the ordered target hours for the origin on ``market_date``."""
    origin = forecast_origin(market_date, config)
    targets = []
    for horizon in range(1, config.forecast.horizon_hours + 1):
        start = origin + (horizon - 1) * _ONE_HOUR
        targets.append(
            TargetHour(
                horizon=horizon,
                start_utc=start.astimezone(dt.UTC),
                end_utc=(start + _ONE_HOUR).astimezone(dt.UTC),
                start_market=start,
            )
        )
    return targets
