"""Hand-calculated and leakage tests for Phase 3 point-in-time features (offline)."""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

from nem_forecast.contract import NEM_MARKET_TZ
from nem_forecast.features.calendar import vic_public_holidays
from nem_forecast.features.design import TARGET_COLUMN, build_profile, design_keys
from nem_forecast.features.history import FEATURES, HOUR_NS, Observations
from nem_forecast.features.reference import reference_features

VALUE = "dispatch_totaldemand_hourly_mean_mw"
HOL = vic_public_holidays(range(2021, 2028))
PROFILES = {"primary_operational": 1, "sensitivity_zero_lag": 0}


def hourly(first: dt.date, days: int, missing: tuple[int, ...] = ()) -> pd.DataFrame:
    """Phase-2-shaped hourly frame; value = position index (hour number since start)."""
    start = pd.Timestamp(dt.datetime.combine(first, dt.time(0), tzinfo=NEM_MARKET_TZ))
    labels = pd.date_range(start, periods=days * 24, freq="h")
    complete = np.ones(len(labels), dtype=bool)
    complete[list(missing)] = False
    ends = (labels + pd.Timedelta(hours=1)).strftime("%Y/%m/%d %H:%M:%S")
    return pd.DataFrame(
        {
            "hour_start_utc": labels.tz_convert("UTC"),
            "is_complete": complete,
            VALUE: np.where(complete, np.arange(len(labels), dtype=float), np.nan),
            "last_source_settlementdate": np.where(complete, ends, None),
        }
    )


def build(
    frame: pd.DataFrame, first: dt.date, last: dt.date, profile: str
) -> tuple[pd.DataFrame, pd.DataFrame]:
    obs = Observations.from_hourly(frame, VALUE)
    return build_profile(obs, design_keys(first, last), profile, PROFILES[profile], HOL)


D0 = dt.date(2024, 1, 1)


def row(wide: pd.DataFrame, origin_date: dt.date, horizon: int) -> pd.Series:
    origin = pd.Timestamp(dt.datetime.combine(origin_date, dt.time(0), tzinfo=NEM_MARKET_TZ))
    match = wide[(wide["origin_utc"] == origin) & (wide["horizon"] == horizon)]
    assert len(match) == 1
    return match.iloc[0]


@pytest.fixture(scope="module")
def tables() -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    frame = hourly(D0, 30)
    return {p: build(frame, D0, dt.date(2024, 1, 30), p) for p in PROFILES}


def test_hand_calculated_lags_and_windows(tables: dict) -> None:
    p = 24 * 28  # origin 2024-01-29 00:00 is position 672 (4 full weeks of history)
    od = dt.date(2024, 1, 29)
    prim, zero = tables["primary_operational"][0], tables["sensitivity_zero_lag"][0]
    r = row(prim, od, 1)
    assert r[TARGET_COLUMN] == p
    assert (r["lag_24h"], r["lag_48h"], r["lag_168h"]) == (p - 24, p - 48, p - 168)
    # primary window = labels T-25h .. T-2h (endpoints up to T-1h)
    assert r["recent_24h_mean"] == pytest.approx(p - 13.5)
    assert r["recent_24h_std"] == pytest.approx(math.sqrt(24 * 25 / 12))  # 7.0711
    assert (r["recent_24h_min"], r["recent_24h_max"]) == (p - 25, p - 2)
    assert r["recent_168h_mean"] == pytest.approx(p - 85.5)
    assert r["recent_168h_std"] == pytest.approx(math.sqrt(168 * 169 / 12))
    assert r["same_hour_mean_7d"] == pytest.approx(p - 96)  # k = 1..7
    assert r["same_hour_same_weekday_mean_4w"] == pytest.approx(p - 420)  # k = 1..4 weeks
    z = row(zero, od, 1)  # zero lag: window = T-24h .. T-1h
    assert z["recent_24h_mean"] == pytest.approx(p - 12.5)
    assert (z["recent_24h_min"], z["recent_24h_max"]) == (p - 24, p - 1)
    # horizon 24: target T+23h. Primary same-hour days k=2..8; zero lag k=1..7
    assert row(prim, od, 24)["same_hour_mean_7d"] == pytest.approx(p + 23 - 120)
    assert row(zero, od, 24)["same_hour_mean_7d"] == pytest.approx(p + 23 - 96)


def test_horizon_24_structural_lag_24h(tables: dict) -> None:
    od = dt.date(2024, 1, 29)
    prim, zero = tables["primary_operational"][0], tables["sensitivity_zero_lag"][0]
    p24 = row(prim, od, 24)
    assert math.isnan(p24["lag_24h"]) and not p24["lag_24h__available"]
    assert p24["lag_24h__missing_reason"] == "after_cutoff"
    assert not p24["eligible_lag24_baseline"]
    assert p24["eligible_initial_ml_features"]  # structural null is allowed, not substituted
    assert p24["lag_48h"] == 24 * 28 + 23 - 48  # other lags unaffected
    assert row(prim, od, 23)["lag_24h__available"]
    z24 = row(zero, od, 24)
    assert z24["lag_24h__available"] and z24["lag_24h"] == 24 * 28 + 23 - 24
    assert (prim[prim["horizon"] == 24]["lag_24h__missing_reason"] == "after_cutoff").all()


def test_every_used_source_endpoint_is_no_later_than_cutoff(tables: dict) -> None:
    for profile, (_, audit) in tables.items():
        used = audit[audit["available"]]
        assert len(used) > 0
        assert (used["latest_source_endpoint_utc"] <= used["cutoff_utc"]).all(), profile
        assert used["endpoint_le_cutoff"].all()
        lag = PROFILES[profile]
        assert (used["cutoff_utc"] == used["origin_utc"] - pd.Timedelta(hours=lag)).all()
    prim_audit = tables["primary_operational"][1]
    latest = prim_audit[prim_audit["available"]]
    assert (
        latest["latest_observation_label_utc"] <= latest["origin_utc"] - pd.Timedelta(hours=2)
    ).all()


@pytest.mark.parametrize("profile", list(PROFILES))
def test_future_perturbation_cannot_change_features(profile: str) -> None:
    frame = hourly(D0, 30)
    base, _ = build(frame, D0, dt.date(2024, 1, 30), profile)
    rng = np.random.default_rng(7)
    for od in (dt.date(2024, 1, 9), dt.date(2024, 1, 21)):
        origin = pd.Timestamp(dt.datetime.combine(od, dt.time(0), tzinfo=NEM_MARKET_TZ))
        cutoff = origin - pd.Timedelta(hours=PROFILES[profile])
        endpoints = frame["hour_start_utc"] + pd.Timedelta(hours=1)
        perturbed = frame.copy()
        after = endpoints > cutoff
        perturbed.loc[after, VALUE] = rng.normal(1e6, 1e5, int(after.sum()))
        perturbed.loc[after.to_numpy() & (rng.random(len(frame)) < 0.1), "is_complete"] = False
        changed, _ = build(perturbed, D0, dt.date(2024, 1, 30), profile)
        cols = [f.name for f in FEATURES] + [f"{f.name}__available" for f in FEATURES]
        a = base[base["origin_utc"] == origin][cols].reset_index(drop=True)
        b = changed[changed["origin_utc"] == origin][cols].reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b)


@pytest.mark.parametrize("profile", list(PROFILES))
def test_truncation_equivalence_with_missing_hours(profile: str) -> None:
    frame = hourly(D0, 20, missing=(100, 333))
    _, audit = build(frame, D0, dt.date(2024, 1, 20), profile)
    obs = Observations.from_hourly(frame, VALUE)
    origins = np.unique(audit["origin_utc"].to_numpy(dtype="datetime64[ns]").astype(np.int64))
    ref = reference_features(obs, origins, PROFILES[profile])
    full = audit.assign(
        origin_ns=audit["origin_utc"].to_numpy(dtype="datetime64[ns]").astype(np.int64)
    ).merge(ref, on=["origin_ns", "horizon", "feature"], suffixes=("", "_ref"))
    assert len(full) == len(audit) == len(ref)
    assert (full["available"] == full["available_ref"]).all()
    assert (full["missing_reason"].astype(str) == full["missing_reason_ref"]).all()
    both = full["available"]
    np.testing.assert_allclose(
        full.loc[both, "value"], full.loc[both, "value_ref"], rtol=0, atol=1e-9
    )
    ends = (
        full.loc[both, "latest_source_endpoint_utc"]
        .to_numpy(dtype="datetime64[ns]")
        .astype(np.int64)
    )
    assert (ends == full.loc[both, "latest_source_endpoint_ns"].astype(np.int64).to_numpy()).all()
    assert set(full["missing_reason"].astype(str)) >= {
        "insufficient_history",
        "missing_source_hour",
    }


def test_missing_hour_and_insufficient_lookback_keep_rows() -> None:
    frame = hourly(D0, 12, missing=(24 * 9 + 5,))  # 2024-01-10 05:00 incomplete
    wide, _ = build(frame, D0, dt.date(2024, 1, 12), "primary_operational")
    assert len(wide) == 12 * 24  # no row dropped
    first = row(wide, D0, 1)
    assert first["lag_24h__missing_reason"] == "insufficient_history"
    assert not first["eligible_initial_ml_features"]
    r = row(wide, dt.date(2024, 1, 11), 6)  # target 01-11 05:00 -> lag_24h label 01-10 05:00
    assert r["lag_24h__missing_reason"] == "missing_source_hour" and math.isnan(r["lag_24h"])
    w = row(wide, dt.date(2024, 1, 11), 1)  # recent 24h window 01-09 23:00 .. 01-10 22:00
    assert w["recent_24h_mean__missing_reason"] == "missing_source_hour"
    t = row(wide, dt.date(2024, 1, 10), 6)  # the target hour itself is missing
    assert not t["target_available"] and not t["eligible_lag168_baseline"]
    lag168 = wide["lag_168h__available"]
    assert not lag168[wide["origin_utc"] < pd.Timestamp("2024-01-07 14:00", tz="UTC")].any()


@pytest.mark.parametrize(
    ("day", "civil_hours", "offsets"),
    [
        # DST start: 02:00 AEST -> 03:00 AEDT. Market 00..23 -> civil 0,1,3..23,0
        (dt.date(2024, 10, 6), [0, 1, *range(3, 24), 0], [10, 10] + [11] * 22),
        # DST end: 03:00 AEDT -> 02:00 AEST. Market 00..23 -> civil 1,2,2,3..23
        (dt.date(2024, 4, 7), [1, 2, 2, *range(3, 24)], [11, 11] + [10] * 22),
    ],
)
def test_dst_dates_have_24_market_horizons(
    day: dt.date, civil_hours: list[int], offsets: list[int]
) -> None:
    frame = hourly(day - dt.timedelta(days=9), 11)
    wide, _ = build(frame, day, day, "primary_operational")
    assert list(wide["horizon"]) == list(range(1, 25))
    assert list(wide["market_hour"]) == list(range(24))
    assert list(wide["civil_hour"]) == civil_hours
    assert list(wide["civil_utc_offset_hours"]) == offsets
    assert list(wide["civil_is_dst"]) == [o == 11 for o in offsets]
    assert (wide["target_hour_utc"].diff().dropna() == pd.Timedelta(hours=1)).all()
    assert (wide["market_date"] == day).all()


def _calendar_row(target_market: dt.datetime) -> pd.Series:
    day = target_market.date()
    frame = hourly(day - dt.timedelta(days=9), 11)
    wide, _ = build(frame, day, day, "primary_operational")
    return wide[wide["market_hour"] == target_market.hour].iloc[0]


@pytest.mark.parametrize(
    ("target", "holiday", "name", "before", "after"),
    [
        (
            dt.datetime(2022, 12, 27, 12),
            True,
            "Christmas Day (observed)",
            False,
            True,
        ),  # substitute
        (dt.datetime(2022, 12, 24, 12), False, None, True, False),
        (dt.datetime(2022, 12, 28, 12), False, None, False, True),
        (dt.datetime(2023, 1, 2, 12), True, "New Year's Day (observed)", False, True),
        (dt.datetime(2023, 12, 31, 12), False, None, True, False),  # year boundary
        (dt.datetime(2024, 1, 2, 12), False, None, False, True),
        (dt.datetime(2024, 11, 5, 12), True, "Melbourne Cup Day", False, False),
    ],
)
def test_victorian_holidays(
    target: dt.datetime, holiday: bool, name: str | None, before: bool, after: bool
) -> None:
    r = _calendar_row(target)
    assert r["civil_is_public_holiday"] == holiday
    assert (r["civil_public_holiday_name"] if holiday else None) == name
    assert r["civil_is_day_before_public_holiday"] == before
    assert r["civil_is_day_after_public_holiday"] == after


def test_market_and_civil_dates_differ_at_year_boundary_in_summer() -> None:
    r = _calendar_row(dt.datetime(2023, 12, 31, 23))  # market 23:00 = civil 2024-01-01 00:00 AEDT
    assert r["market_date"] == dt.date(2023, 12, 31) and r["market_day_of_year"] == 365
    assert r["civil_date"] == dt.date(2024, 1, 1) and r["civil_hour"] == 0
    assert r["civil_is_public_holiday"] and r["civil_public_holiday_name"] == "New Year's Day"


def test_leap_day() -> None:
    frame = hourly(dt.date(2024, 2, 15), 20)
    wide, _ = build(frame, dt.date(2024, 2, 28), dt.date(2024, 3, 2), "primary_operational")
    leap = wide[wide["market_date"] == dt.date(2024, 2, 29)]
    assert len(leap) == 24 and (leap["market_day_of_year"] == 60).all()
    mar1 = row(wide, dt.date(2024, 3, 1), 1)
    assert mar1["market_day_of_year"] == 61
    pos = 24 * 15  # 2024-03-01 00:00 relative to 2024-02-15 (2024-02 has 29 days)
    assert mar1[TARGET_COLUMN] == pos
    assert mar1["lag_24h"] == pos - 24  # 2024-02-29 00:00
    assert mar1["lag_168h"] == pos - 168  # 2024-02-23 00:00


def test_unique_keys_stable_sort_and_determinism() -> None:
    frame = hourly(D0, 15)
    a, audit_a = build(frame, D0, dt.date(2024, 1, 15), "primary_operational")
    b, audit_b = build(
        frame.sample(frac=1.0, random_state=3), D0, dt.date(2024, 1, 15), "primary_operational"
    )
    pd.testing.assert_frame_equal(a, b)
    pd.testing.assert_frame_equal(audit_a, audit_b)
    assert not a.duplicated(["availability_profile", "origin_utc", "horizon"]).any()
    assert a["origin_utc"].is_monotonic_increasing
    assert all(h == list(range(1, 25)) for h in a.groupby("origin_utc")["horizon"].apply(list))
    assert not audit_a.duplicated(["origin_utc", "horizon", "feature"]).any()
    assert len(audit_a) == len(a) * len(FEATURES)


def test_profiles_are_labelled_and_never_mixed(tables: dict) -> None:
    for profile, (wide, audit) in tables.items():
        assert set(wide["availability_profile"]) == {profile}
        assert set(audit["availability_profile"]) == {profile}
        assert set(wide["availability_lag_hours"]) == {PROFILES[profile]}


def test_hour_step_constant() -> None:
    assert HOUR_NS == 3_600_000_000_000
