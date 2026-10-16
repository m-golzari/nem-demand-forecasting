"""Target calendar features: NEM market time and Victorian civil time (kept separate).

Calendar values depend only on the target timestamp and a published holiday
calendar, so they are known at any origin. Market-time fields (fixed UTC+10)
and civil-time fields (``Australia/Melbourne``, with daylight saving) are
separately named and both derived from the canonical UTC timestamp.
"""

from __future__ import annotations

import datetime as dt

import holidays as holidays_lib
import numpy as np
import pandas as pd

from nem_forecast.contract import NEM_MARKET_TZ

CIVIL_TZ = "Australia/Melbourne"
TWO_PI = 2.0 * np.pi
MEAN_YEAR_DAYS = 365.25  # mean Gregorian year; one cycle per year incl. leap years


def vic_public_holidays(years: range) -> dict[dt.date, str]:
    calendar = holidays_lib.country_holidays("AU", subdiv="VIC", years=list(years))
    return {day: str(name) for day, name in sorted(calendar.items())}


def market_calendar(target_utc: pd.Series) -> pd.DataFrame:
    market = target_utc.dt.tz_convert(NEM_MARKET_TZ)
    hour = market.dt.hour
    weekday = market.dt.weekday
    doy = market.dt.dayofyear
    return pd.DataFrame(
        {
            "market_date": market.dt.date,
            "market_hour": hour.astype("int8"),
            "market_weekday": weekday.astype("int8"),
            "market_month": market.dt.month.astype("int8"),
            "market_day_of_year": doy.astype("int16"),
            "market_is_weekend": weekday >= 5,
            "market_hour_sin": np.sin(TWO_PI * hour / 24),
            "market_hour_cos": np.cos(TWO_PI * hour / 24),
            "market_weekday_sin": np.sin(TWO_PI * weekday / 7),
            "market_weekday_cos": np.cos(TWO_PI * weekday / 7),
            "market_doy_sin": np.sin(TWO_PI * (doy - 1) / MEAN_YEAR_DAYS),
            "market_doy_cos": np.cos(TWO_PI * (doy - 1) / MEAN_YEAR_DAYS),
        },
        index=target_utc.index,
    )


def civil_calendar(target_utc: pd.Series, public_holidays: dict[dt.date, str]) -> pd.DataFrame:
    civil = target_utc.dt.tz_convert(CIVIL_TZ)
    offset = civil.dt.tz_localize(None) - target_utc.dt.tz_localize(None)
    offset_hours = offset.dt.total_seconds() / 3600
    dst = civil.map(lambda t: bool(t.dst()))
    civil_date = civil.dt.date
    holiday_set = set(public_holidays)
    one_day = dt.timedelta(days=1)
    weekday = civil.dt.weekday
    return pd.DataFrame(
        {
            "civil_timestamp": civil,
            "civil_date": civil_date,
            "civil_hour": civil.dt.hour.astype("int8"),
            "civil_weekday": weekday.astype("int8"),
            "civil_utc_offset_hours": offset_hours.astype(float),
            "civil_is_dst": dst.astype(bool),
            "civil_is_weekend": weekday >= 5,
            "civil_is_public_holiday": civil_date.map(lambda d: d in holiday_set).astype(bool),
            "civil_public_holiday_name": civil_date.map(public_holidays.get).astype("string"),
            "civil_is_day_before_public_holiday": civil_date.map(
                lambda d: d + one_day in holiday_set
            ).astype(bool),
            "civil_is_day_after_public_holiday": civil_date.map(
                lambda d: d - one_day in holiday_set
            ).astype(bool),
        },
        index=target_utc.index,
    )
