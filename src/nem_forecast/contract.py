"""Executable form of the forecast origin/horizon contract and source time semantics.

For a market date ``D`` the origin is ``D 00:00`` NEM market time. Target
hour ``h`` (1..24) is the left-closed interval
``[origin + (h - 1) h, origin + h h)``, labelled by its start. Because market
time has no daylight saving, every origin has exactly 24 one-hour targets.

Source semantics (verified in Phase 1, see docs/aemo_schema_audit.md): AEMO
``SETTLEMENTDATE`` is a naive NEM-market-time (AEST, UTC+10) timestamp marking
the **end** of a 5-minute dispatch interval ``(end - 5 min, end]``. A record is
assigned to the canonical hour containing its represented interval, so the 12
records ending 00:05 ... 01:00 form the hour ``[00:00, 01:00)`` labelled 00:00.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from nem_forecast.config import ForecastConfig

_ONE_HOUR = dt.timedelta(hours=1)
DISPATCH_INTERVAL = dt.timedelta(minutes=5)
NEM_MARKET_TZ = dt.timezone(dt.timedelta(hours=10), name="NEM")
SETTLEMENTDATE_FORMAT = "%Y/%m/%d %H:%M:%S"


def parse_settlementdate(value: str) -> dt.datetime:
    """Parse an AEMO ``SETTLEMENTDATE`` string as an aware market-time interval end."""
    return dt.datetime.strptime(value, SETTLEMENTDATE_FORMAT).replace(tzinfo=NEM_MARKET_TZ)


def canonical_hour_start(interval_end: dt.datetime) -> dt.datetime:
    """Start label of the canonical hour containing the interval ``(end - 5 min, end]``."""
    if interval_end.tzinfo is None:
        raise ValueError("interval_end must be timezone-aware")
    start = (interval_end - DISPATCH_INTERVAL).astimezone(NEM_MARKET_TZ)
    return start.replace(minute=0, second=0, microsecond=0)


def settlementdate_bounds(
    start_date: dt.date, end_date: dt.date
) -> tuple[dt.datetime, dt.datetime]:
    """First and last interval-ending timestamps for market dates ``[start, end)``."""
    first = dt.datetime.combine(start_date, dt.time(0), tzinfo=NEM_MARKET_TZ) + DISPATCH_INTERVAL
    last = dt.datetime.combine(end_date, dt.time(0), tzinfo=NEM_MARKET_TZ)
    return first, last


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
