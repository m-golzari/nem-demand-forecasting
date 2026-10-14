"""Source-to-canonical time mapping (interval-ending SETTLEMENTDATE -> start-labelled hours)."""

import datetime as dt

import pandas as pd
import pytest

from nem_forecast.contract import (
    NEM_MARKET_TZ,
    canonical_hour_start,
    parse_settlementdate,
    settlementdate_bounds,
)
from nem_forecast.ingestion.aemo_csv import parse_aemo_csv_text
from nem_forecast.ingestion.aemo_normalise import expected_dispatchinterval, typed_frame
from tests.aemo_fixtures import full_day_rows, render


def test_market_tz_matches_config_offset() -> None:
    assert NEM_MARKET_TZ.utcoffset(None) == dt.timedelta(hours=10)


def test_parse_settlementdate_is_aware_market_time() -> None:
    value = parse_settlementdate("2024/04/04 00:05:00")
    assert value.utcoffset() == dt.timedelta(hours=10)
    assert value.astimezone(dt.UTC) == dt.datetime(2024, 4, 3, 14, 5, tzinfo=dt.UTC)


def test_twelve_interval_ending_records_form_hour_zero() -> None:
    """Records ending 00:05..01:00 represent (00:00, 01:00] -> canonical hour [00:00, 01:00)."""
    ends = [f"2024/04/04 00:{m:02d}:00" for m in range(5, 60, 5)] + ["2024/04/04 01:00:00"]
    hours = {canonical_hour_start(parse_settlementdate(e)) for e in ends}
    assert hours == {dt.datetime(2024, 4, 4, 0, 0, tzinfo=NEM_MARKET_TZ)}
    assert len(ends) == 12


@pytest.mark.parametrize(
    ("settlementdate", "hour"),
    [
        ("2024/04/04 00:00:00", dt.datetime(2024, 4, 3, 23, 0)),  # last interval of prior hour
        ("2024/04/04 00:05:00", dt.datetime(2024, 4, 4, 0, 0)),
        ("2024/04/04 01:00:00", dt.datetime(2024, 4, 4, 0, 0)),
        ("2024/04/04 01:05:00", dt.datetime(2024, 4, 4, 1, 0)),
    ],
)
def test_hour_boundaries(settlementdate: str, hour: dt.datetime) -> None:
    assert canonical_hour_start(parse_settlementdate(settlementdate)) == hour.replace(
        tzinfo=NEM_MARKET_TZ
    )


def test_naive_interval_end_rejected() -> None:
    with pytest.raises(ValueError, match="aware"):
        canonical_hour_start(dt.datetime(2024, 4, 4, 1, 0))


def test_window_bounds_are_interval_ending() -> None:
    first, last = settlementdate_bounds(dt.date(2024, 4, 4), dt.date(2024, 4, 11))
    assert first == dt.datetime(2024, 4, 4, 0, 5, tzinfo=NEM_MARKET_TZ)
    assert last == dt.datetime(2024, 4, 11, 0, 0, tzinfo=NEM_MARKET_TZ)


def test_vectorised_mapping_matches_scalar_and_dst_day_has_24_hours() -> None:
    """2024-04-07 is the Victorian DST end; market time must still give 24 x 12 records."""
    typed = typed_frame(parse_aemo_csv_text(render(full_day_rows(dt.date(2024, 4, 7), ("VIC1",)))))
    for end, hour in zip(typed["interval_end_market"], typed["hour_start_market"], strict=True):
        assert hour == canonical_hour_start(end.to_pydatetime())
    counts = typed.groupby("hour_start_market").size()
    assert len(counts) == 24 and (counts == 12).all()
    assert (typed["interval_end_utc"] - typed["interval_start_utc"] == pd.Timedelta("5min")).all()


@pytest.mark.parametrize(
    ("settlementdate", "expected"),
    [
        ("2024/04/01 00:05:00", 20240331241),  # value observed in the real April 2024 archive
        ("2024/04/04 04:05:00", 20240404001),  # first period of a trading day ("starting at 04:05")
        ("2024/04/05 04:00:00", 20240404288),  # last period of that trading day
    ],
)
def test_expected_dispatchinterval(settlementdate: str, expected: int) -> None:
    series = pd.Series([pd.Timestamp(parse_settlementdate(settlementdate))])
    assert int(expected_dispatchinterval(series).iloc[0]) == expected
