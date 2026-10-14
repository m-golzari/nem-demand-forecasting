"""Hand-calculated tests for the Phase 2 hourly target (synthetic data, offline)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from nem_forecast.contract import NEM_MARKET_TZ
from nem_forecast.ingestion.aemo_csv import parse_aemo_csv_text
from nem_forecast.ingestion.aemo_normalise import (
    add_quality_flags,
    classify_primary_records,
    typed_frame,
)
from nem_forecast.target.hourly import TARGET_COLUMN, build_hourly, model_ready
from tests.aemo_fixtures import full_day_rows, render, row

FIVE = dt.timedelta(minutes=5)


def records(rows: list[list[str]]) -> pd.DataFrame:
    return classify_primary_records(
        add_quality_flags(typed_frame(parse_aemo_csv_text(render(rows))))
    )


def hour_rows(start: dt.datetime, values: list[str], **kwargs: object) -> list[list[str]]:
    """Rows for the 12 endpoints start+5min ... start+60min with the given demand strings."""
    return [row(start + (i + 1) * FIVE, demand=v, **kwargs) for i, v in enumerate(values)]


def ts(*args: int) -> pd.Timestamp:
    return pd.Timestamp(dt.datetime(*args, tzinfo=NEM_MARKET_TZ))


def build(
    rows: list[list[str]], first: pd.Timestamp, last: pd.Timestamp | None = None
) -> pd.DataFrame:
    hourly, rec = build_hourly(records(rows), first, last or first)
    assert rec["identity_records_sum"] and rec["identity_primary_sum"]
    return hourly.set_index("hour_start_market")


H0 = dt.datetime(2024, 4, 4, 0, 0)


def test_complete_ordinary_hour() -> None:
    # 4001 + ... + 4012 = 48078; / 12 = 4006.5
    out = build(hour_rows(H0, [str(4000 + i) for i in range(1, 13)]), ts(2024, 4, 4, 0))
    h = out.loc[ts(2024, 4, 4, 0)]
    assert h["is_complete"] and h["quality_reasons"] == ""
    assert h[TARGET_COLUMN] == pytest.approx(4006.5)
    assert h["n_primary_records"] == 12 and h["n_unique_expected_endpoints"] == 12
    assert h["first_source_settlementdate"] == "2024/04/04 00:05:00"
    assert h["last_source_settlementdate"] == "2024/04/04 01:00:00"
    assert str(h["hour_start_utc"]) == "2024-04-03 14:00:00+00:00"
    assert str(h["hour_end_utc"]) == "2024-04-03 15:00:00+00:00"


def test_endpoint_normalisation_record_ending_midnight_belongs_to_previous_hour() -> None:
    rows = [row(H0, demand="9999"), *hour_rows(H0, ["100"] * 12)]
    out = build(rows, ts(2024, 4, 3, 23), ts(2024, 4, 4, 0))
    assert out.loc[ts(2024, 4, 4, 0), TARGET_COLUMN] == pytest.approx(100.0)  # 9999 excluded
    prev = out.loc[ts(2024, 4, 3, 23)]
    assert prev["n_records"] == 1 and not prev["is_complete"]
    assert prev["last_source_settlementdate"] == "2024/04/04 00:00:00"
    assert np.isnan(prev[TARGET_COLUMN])  # never averaged over fewer records


@pytest.mark.parametrize(
    ("start", "label_year", "label_date"),
    [
        (dt.datetime(2024, 6, 30, 23, 0), 2024, dt.date(2024, 6, 30)),  # month boundary
        (dt.datetime(2023, 12, 31, 23, 0), 2023, dt.date(2023, 12, 31)),  # year boundary
    ],
)
def test_midnight_month_and_year_boundary(
    start: dt.datetime, label_year: int, label_date: dt.date
) -> None:
    # 11 x 10 + 22 = 132; / 12 = 11. The final endpoint is 00:00 of the next month/year.
    first = hour_rows(start, ["10"] * 11 + ["22"])
    june, july = first[:11], first[11:]  # as if read from two monthly archives
    out = build(june + july, ts(start.year, start.month, start.day, 23))
    h = out.loc[ts(start.year, start.month, start.day, 23)]
    assert h["is_complete"] and h[TARGET_COLUMN] == pytest.approx(11.0)
    assert h["year"] == label_year and h["market_date"] == label_date
    assert h["last_source_settlementdate"].endswith("00:00:00")


def test_0400_trading_day_boundary() -> None:
    rows = hour_rows(dt.datetime(2024, 4, 5, 3), ["300"] * 12) + hour_rows(
        dt.datetime(2024, 4, 5, 4), ["400"] * 12
    )
    recs = records(rows).set_index("settlementdate_source")
    assert recs.loc["2024/04/05 04:00:00", "dispatchinterval"] == 20240404288  # last of trading day
    assert recs.loc["2024/04/05 04:05:00", "dispatchinterval"] == 20240405001  # first of next
    out = build(rows, ts(2024, 4, 5, 3), ts(2024, 4, 5, 4))
    assert out["is_complete"].all()
    assert out.loc[ts(2024, 4, 5, 3), TARGET_COLUMN] == pytest.approx(300.0)
    assert out.loc[ts(2024, 4, 5, 4), TARGET_COLUMN] == pytest.approx(400.0)
    assert (out["n_dispatchinterval_mismatch"] == 0).all()


@pytest.mark.parametrize("day", [dt.date(2024, 4, 7), dt.date(2024, 10, 6)])  # DST end, DST start
def test_victorian_dst_dates_have_24_complete_market_hours(day: dt.date) -> None:
    out = build(
        full_day_rows(day, ("VIC1",)),
        ts(day.year, day.month, day.day, 0),
        ts(day.year, day.month, day.day, 23),
    )
    assert len(out) == 24 and out["is_complete"].all()
    assert [t.hour for t in out.index] == list(range(24))  # 02:00 exists exactly once
    assert out.index.is_unique
    utc = out["hour_start_utc"]
    assert ((utc.diff().dropna()) == pd.Timedelta("1h")).all()


def test_intervention_hour_uses_physical_record_only() -> None:
    rows = hour_rows(H0, ["5000"] * 12)  # INTERVENTION=0 everywhere
    rows.append(row(H0 + 6 * FIVE, intervention=1, demand="5120"))  # physical run at 00:30
    out = build(rows, ts(2024, 4, 4, 0))
    h = out.loc[ts(2024, 4, 4, 0)]
    # (11 x 5000 + 5120) / 12 = 5010; pricing-only would give 5000; averaging runs 5005
    assert h["is_complete"] and h[TARGET_COLUMN] == pytest.approx(5010.0)
    assert h["n_records"] == 13 and h["n_primary_records"] == 12
    assert h["n_pricing_not_selected"] == 1 and h["n_intervention_intervals"] == 1
    assert h["max_abs_physical_minus_pricing_mw"] == pytest.approx(120.0)
    assert h["intervention_affected"]


def test_eleven_intervals_is_incomplete() -> None:
    rows = hour_rows(H0, ["10"] * 12)
    del rows[6]  # drop endpoint 00:35
    h = build(rows, ts(2024, 4, 4, 0)).loc[ts(2024, 4, 4, 0)]
    assert not h["is_complete"] and h["quality_reasons"] == "missing_endpoint"
    assert h["n_primary_records"] == 11 and np.isnan(h[TARGET_COLUMN])


def test_duplicate_plus_missing_endpoint_with_twelve_rows() -> None:
    rows = hour_rows(H0, ["10"] * 12)
    rows[2] = row(H0 + 2 * FIVE, demand="10")  # endpoint 00:15 replaced by a duplicate 00:10
    h = build(rows, ts(2024, 4, 4, 0)).loc[ts(2024, 4, 4, 0)]
    assert h["n_records"] == 12  # row count alone looks complete
    assert not h["is_complete"]
    assert set(h["quality_reasons"].split(";")) == {"missing_endpoint", "quarantined_record"}
    assert h["n_quarantined_records"] == 2


def test_unexpected_endpoint() -> None:
    rows = [*hour_rows(H0, ["10"] * 12), row(H0 + dt.timedelta(minutes=7), demand="10")]
    h = build(rows, ts(2024, 4, 4, 0)).loc[ts(2024, 4, 4, 0)]
    assert not h["is_complete"] and "unexpected_endpoint" in h["quality_reasons"]


@pytest.mark.parametrize("bad", ["", "nan", "inf", "-inf"])
def test_null_or_nonfinite_demand(bad: str) -> None:
    values = ["10"] * 12
    values[4] = bad
    h = build(hour_rows(H0, values), ts(2024, 4, 4, 0)).loc[ts(2024, 4, 4, 0)]
    assert not h["is_complete"] and "nonfinite_demand" in h["quality_reasons"]
    assert np.isnan(h[TARGET_COLUMN])


def test_mixed_regions_flagged() -> None:
    rows = [*hour_rows(H0, ["10"] * 12), row(H0 + FIVE, region="NSW1", demand="10")]
    h = build(rows, ts(2024, 4, 4, 0)).loc[ts(2024, 4, 4, 0)]
    assert not h["is_complete"] and "non_vic1_region" in h["quality_reasons"]


def test_irregular_spacing() -> None:
    rows = hour_rows(H0, ["10"] * 12)
    rows[1] = row(H0 + dt.timedelta(minutes=11), demand="10")  # 00:11 instead of 00:10
    h = build(rows, ts(2024, 4, 4, 0)).loc[ts(2024, 4, 4, 0)]
    assert not h["is_complete"]
    assert {"missing_endpoint", "unexpected_endpoint"} <= set(h["quality_reasons"].split(";"))


@pytest.mark.parametrize(
    "extra",
    [
        [
            row(H0 + 4 * FIVE, intervention=1, demand="11"),
            row(H0 + 4 * FIVE, intervention=1, demand="12"),
        ],
        [row(H0 + 4 * FIVE, intervention=2, demand="11")],
    ],
)
def test_unsupported_intervention_combinations_quarantine_the_hour(extra: list[list[str]]) -> None:
    rows = hour_rows(H0, ["10"] * 12) + extra
    h = build(rows, ts(2024, 4, 4, 0)).loc[ts(2024, 4, 4, 0)]
    assert not h["is_complete"] and "quarantined_record" in h["quality_reasons"]
    assert np.isnan(h[TARGET_COLUMN])


def test_lone_physical_record_is_quarantined() -> None:
    rows = hour_rows(H0, ["10"] * 12)
    rows[3] = row(H0 + 4 * FIVE, intervention=1, demand="10")  # physical without pricing
    h = build(rows, ts(2024, 4, 4, 0)).loc[ts(2024, 4, 4, 0)]
    assert set(h["quality_reasons"].split(";")) == {"missing_endpoint", "quarantined_record"}


def test_empty_and_out_of_range_hours_and_model_ready() -> None:
    rows = hour_rows(H0, ["10"] * 12) + hour_rows(dt.datetime(2024, 4, 4, 5), ["20"] * 12)
    hourly, rec = build_hourly(records(rows), ts(2024, 4, 4, 0), ts(2024, 4, 4, 1))
    by_hour = hourly.set_index("hour_start_market")
    assert by_hour.loc[ts(2024, 4, 4, 1), "quality_reasons"] == "no_records;missing_endpoint"
    assert by_hour.loc[ts(2024, 4, 4, 5), "quality_reasons"] == "outside_canonical_range"
    assert rec["hours_expected"] == 2 and rec["hours_outside_canonical_range"] == 1
    ready = model_ready(hourly)
    assert list(ready["hour_start_market"]) == [ts(2024, 4, 4, 0)]
    assert ready[TARGET_COLUMN].notna().all()
