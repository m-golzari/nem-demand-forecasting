import datetime as dt
import itertools
from zoneinfo import ZoneInfo

import pytest

from nem_forecast.config import ForecastConfig, load_forecast_config
from nem_forecast.contract import forecast_origin, target_hours

MELBOURNE = ZoneInfo("Australia/Melbourne")


@pytest.fixture(scope="module")
def cfg() -> ForecastConfig:
    return load_forecast_config()


def test_origin_is_market_midnight_utc_plus_10(cfg: ForecastConfig) -> None:
    origin = forecast_origin(dt.date(2024, 1, 15), cfg)
    assert origin.astimezone(dt.UTC) == dt.datetime(2024, 1, 14, 14, 0, tzinfo=dt.UTC)


def test_24_ordered_contiguous_left_closed_hours(cfg: ForecastConfig) -> None:
    targets = target_hours(dt.date(2024, 1, 15), cfg)
    assert [t.horizon for t in targets] == list(range(1, 25))
    assert targets[0].start_utc == forecast_origin(dt.date(2024, 1, 15), cfg)
    for prev, nxt in itertools.pairwise(targets):
        assert prev.end_utc == nxt.start_utc
    assert all(t.end_utc - t.start_utc == dt.timedelta(hours=1) for t in targets)
    assert targets[-1].end_utc == forecast_origin(dt.date(2024, 1, 16), cfg)
    assert all(t.start_utc.tzinfo is dt.UTC for t in targets)


@pytest.mark.parametrize(
    "market_date",
    [
        dt.date(2024, 4, 7),  # Victorian DST ends (civil day has 25 hours)
        dt.date(2024, 10, 6),  # Victorian DST starts (civil day has 23 hours)
        dt.date(2025, 4, 6),
        dt.date(2025, 10, 5),
    ],
)
def test_dst_transition_dates_have_24_hours_and_fixed_offset(
    cfg: ForecastConfig, market_date: dt.date
) -> None:
    targets = target_hours(market_date, cfg)
    assert len(targets) == 24
    expected_origin = dt.datetime.combine(market_date, dt.time(0), tzinfo=dt.UTC) - dt.timedelta(
        hours=10
    )
    assert targets[0].start_utc == expected_origin
    assert targets[-1].end_utc == expected_origin + dt.timedelta(hours=24)


def test_market_time_differs_from_melbourne_civil_time_in_summer(cfg: ForecastConfig) -> None:
    """Guards against accidentally using Australia/Melbourne (which has DST)."""
    origin = forecast_origin(dt.date(2024, 1, 15), cfg)
    assert origin.astimezone(MELBOURNE).hour == 1  # AEDT = UTC+11
    winter = forecast_origin(dt.date(2024, 7, 15), cfg)
    assert winter.astimezone(MELBOURNE).hour == 0  # AEST = UTC+10
