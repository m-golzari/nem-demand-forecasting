"""Phase 4: baselines, calibration, metrics, folds, holdout guard, comparisons (offline)."""

from __future__ import annotations

import datetime as dt
import itertools
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nem_forecast.config import ConfigError, load_backtest_config
from nem_forecast.contract import NEM_MARKET_TZ
from nem_forecast.evaluation import metrics as mt
from nem_forecast.evaluation.backtest import TARGET, attach_evaluation, comparisons, run_fold
from nem_forecast.evaluation.bootstrap import paired_block_bootstrap
from nem_forecast.evaluation.calibration import apply_offsets, fit_residual_offsets
from nem_forecast.evaluation.folds import (
    HoldoutAccessError,
    assert_development_origins,
    build_folds,
)
from nem_forecast.features.calendar import vic_public_holidays
from nem_forecast.features.design import build_profile, design_keys
from nem_forecast.features.history import Observations
from nem_forecast.models.baselines import (
    DayWeekHybrid,
    InputAllowlistError,
    PreviousDay,
    PreviousWeek,
    SameWeekday4w,
    select_inputs,
)
from tests.conftest import REPO_ROOT

CFG = load_backtest_config()
GRID = CFG.hybrid.weight_grid.values


def frame(**cols: list[float | bool | int]) -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    base = {
        "origin_utc": pd.date_range("2025-07-01", periods=n, freq="h", tz="UTC"),
        "horizon": [1] * n,
    }
    return pd.DataFrame({**base, **cols})


# --- baseline formulas -----------------------------------------------------------------


def test_single_lag_baselines() -> None:
    f = frame(
        lag_24h=[100.0, 200.0],
        lag_24h__available=[True, False],
        lag_168h=[110.0, 210.0],
        lag_168h__available=[True, True],
        same_hour_same_weekday_mean_4w=[120.0, np.nan],
        same_hour_same_weekday_mean_4w__available=[True, False],
    )
    d = PreviousDay().predict(select_inputs(f, PreviousDay()))
    assert d["point_raw"].iloc[0] == 100.0 and math.isnan(d["point_raw"].iloc[1])
    assert (
        list(d["available"]) == [True, False]
        and d["unavailable_reason"].iloc[1] == "lag_24h_unavailable"
    )
    assert list(PreviousWeek().predict(select_inputs(f, PreviousWeek()))["point_raw"]) == [
        110.0,
        210.0,
    ]
    s = SameWeekday4w().predict(select_inputs(f, SameWeekday4w()))
    assert s["point_raw"].iloc[0] == 120.0 and not s["available"].iloc[1]


def test_hybrid_weight_selection_and_horizon_24_fallback() -> None:
    rng = np.random.default_rng(0)
    day, week = rng.normal(5000, 500, 200), rng.normal(5000, 500, 200)
    train = frame(
        lag_24h=list(day),
        lag_24h__available=[True] * 200,
        lag_168h=list(week),
        lag_168h__available=[True] * 200,
    )
    y = pd.Series(0.3 * day + 0.7 * week)  # exact blend
    model = DayWeekHybrid(GRID).fit(select_inputs(train, DayWeekHybrid(GRID)), y)
    assert model.weight == pytest.approx(0.3) and model.fit_rows == 200
    test = frame(
        lag_24h=[100.0, 100.0, 100.0],
        lag_24h__available=[True, False, False],
        lag_168h=[200.0, 200.0, 200.0],
        lag_168h__available=[True, True, True],
    )
    test["horizon"] = [1, 23, 24]
    p = model.predict(select_inputs(test, model))
    assert p["point_raw"].iloc[0] == pytest.approx(0.3 * 100 + 0.7 * 200)  # 170
    assert not p["available"].iloc[1]  # h 1-23 never substituted
    assert p["point_raw"].iloc[2] == 200.0 and p["fallback_used"].iloc[2]  # flagged fallback
    assert not p["fallback_used"].iloc[0]


def test_hybrid_tie_breaks_to_smallest_weight() -> None:
    train = frame(
        lag_24h=[10.0, 10.0],
        lag_24h__available=[True, True],
        lag_168h=[10.0, 10.0],
        lag_168h__available=[True, True],
    )
    model = DayWeekHybrid(GRID).fit(
        select_inputs(train, DayWeekHybrid(GRID)), pd.Series([10.0, 10.0])
    )
    assert model.weight == 0.0  # every weight has MAE 0


def test_hybrid_fit_ignores_horizon_24_rows() -> None:
    train = frame(
        lag_24h=[0.0, 1000.0],
        lag_24h__available=[True, True],
        lag_168h=[10.0, 0.0],
        lag_168h__available=[True, True],
    )
    train["horizon"] = [1, 24]
    model = DayWeekHybrid(GRID).fit(
        select_inputs(train, DayWeekHybrid(GRID)), pd.Series([0.0, 1000.0])
    )
    assert model.weight == 1.0 and model.fit_rows == 1


def test_prediction_ignores_target_and_rejects_unapproved_columns() -> None:
    f = frame(
        lag_24h=[100.0], lag_24h__available=[True], lag_168h=[200.0], lag_168h__available=[True]
    )
    f[TARGET] = [1.0]
    f["recent_24h_mean"] = [5.0]
    model = DayWeekHybrid(GRID)
    model.weight = 0.5
    base = model.predict(select_inputs(f, model))
    changed = f.assign(**{TARGET: [9e9], "recent_24h_mean": [-1.0]})
    pd.testing.assert_frame_equal(base, model.predict(select_inputs(changed, model)))
    with pytest.raises(InputAllowlistError):
        model.predict(f)  # target / unapproved columns present


# --- calibration --------------------------------------------------------------------


def test_residual_quantiles_hand_calculated() -> None:
    horizon = pd.Series([1] * 10 + [2] * 3)
    raw = pd.Series([0.0] * 13)
    actual = pd.Series([float(i) for i in range(1, 11)] + [1.0, 2.0, 3.0])
    table = fit_residual_offsets(horizon, raw, actual, min_samples=5).set_index("horizon")
    # numpy linear quantiles of 1..10: q10 = 1.9, q50 = 5.5, q90 = 9.1
    assert (
        table.loc[1, "offset_q10"],
        table.loc[1, "offset_q50"],
        table.loc[1, "offset_q90"],
    ) == pytest.approx((1.9, 5.5, 9.1))
    assert table.loc[1, "n_samples"] == 10 and table.loc[1, "calibrated"]
    assert table.loc[2, "n_samples"] == 3 and not table.loc[2, "calibrated"]
    preds = pd.DataFrame(
        {
            "horizon": [1, 2],
            "point_raw": [100.0, 100.0],
            "available": [True, True],
            "unavailable_reason": ["", ""],
            "fallback_used": [False, False],
        }
    )
    out = apply_offsets(preds, table.reset_index())
    assert (out["p10"].iloc[0], out["p50"].iloc[0], out["p90"].iloc[0]) == pytest.approx(
        (101.9, 105.5, 109.1)
    )
    assert (
        not out["available"].iloc[1]
        and out["unavailable_reason"].iloc[1] == "insufficient_calibration_samples"
    )


# --- metrics ------------------------------------------------------------------------


def test_metric_formulas_hand_calculated() -> None:
    f, y = np.array([110.0, 90.0, 100.0]), np.array([100.0, 100.0, 150.0])
    assert mt.mae(f, y) == pytest.approx((10 + 10 + 50) / 3)
    assert mt.rmse(f, y) == pytest.approx(math.sqrt((100 + 100 + 2500) / 3))
    assert mt.wape(f, y) == pytest.approx(70 / 350)  # aggregate, not mean of ratios
    assert mt.mean_signed_error(f, y) == pytest.approx((10 - 10 - 50) / 3)
    assert mt.pinball(0.9, np.array([8.0]), np.array([10.0])) == pytest.approx(1.8)
    assert mt.pinball(0.9, np.array([10.0]), np.array([8.0])) == pytest.approx(0.2)
    assert mt.pinball(0.1, np.array([8.0]), np.array([10.0])) == pytest.approx(0.2)
    assert mt.coverage(np.array([0.0, 0.0]), np.array([10.0, 10.0]), np.array([5.0, 11.0])) == 0.5
    assert mt.crossing_rate(np.array([1.0, 3.0]), np.array([2.0, 2.0]), np.array([3.0, 4.0])) == 0.5


# --- folds and holdout --------------------------------------------------------------


def test_monthly_fold_boundaries_and_temporal_cutoffs() -> None:
    folds = build_folds(CFG)
    assert len(folds) == 12
    assert (folds[0].validation_first_origin, folds[-1].validation_last_origin) == (
        dt.date(2025, 7, 1),
        dt.date(2026, 6, 30),
    )
    for f in folds:
        assert f.validation_first_origin.day == 1
        assert f.last_matured_origin == f.validation_first_origin - dt.timedelta(days=2)
        assert f.calibration_last_origin == f.last_matured_origin
        assert (f.calibration_last_origin - f.calibration_first_origin).days == 179
        assert f.weight_fit_last_origin < f.calibration_first_origin < f.validation_first_origin
        # matured: the last calibration origin's final target hour ends <= F - 1 h
        final_end = dt.datetime.combine(f.calibration_last_origin, dt.time(0)) + dt.timedelta(
            hours=24
        )
        assert final_end <= f.maturity_cutoff_market
    for a, b in itertools.pairwise(folds):
        assert b.validation_first_origin == a.validation_last_origin + dt.timedelta(days=1)


def test_holdout_rejected_everywhere(tmp_project) -> None:  # type: ignore[no-untyped-def]
    assert_development_origins([dt.date(2026, 6, 30)])
    with pytest.raises(HoldoutAccessError):
        assert_development_origins([dt.date(2026, 7, 1)])
    text = (REPO_ROOT / "configs" / "backtest.yaml").read_text()
    path = Path(tmp_project.configs / "backtest.yaml")
    path.write_text(text.replace('last_origin: "2026-06-30"', 'last_origin: "2026-07-01"'))
    with pytest.raises(ConfigError, match="final holdout"):
        load_backtest_config(paths=tmp_project)
    from typer.testing import CliRunner

    from nem_forecast.cli import app

    result = CliRunner().invoke(app, ["evaluate-holdout"])
    assert result.exit_code == 1 and "HoldoutAccessError" in result.output
    result = CliRunner().invoke(app, ["evaluate-holdout", "--owner-approved-final-evaluation"])
    assert result.exit_code == 1 and "nothing was read" in result.output


# --- fold execution on a synthetic design (real Phase 3 code) -------------------------


def synthetic_design() -> pd.DataFrame:
    start = dt.date(2024, 5, 1)
    labels = pd.date_range(
        pd.Timestamp(dt.datetime.combine(start, dt.time(0), tzinfo=NEM_MARKET_TZ)),
        pd.Timestamp("2025-08-01 00:00", tz=NEM_MARKET_TZ),
        freq="h",
        inclusive="left",
    )
    hour = labels.hour.to_numpy()
    rng = np.random.default_rng(1)
    value = 5000 + 800 * np.sin(2 * np.pi * (hour - 6) / 24) + rng.normal(0, 150, len(labels))
    hourly = pd.DataFrame(
        {
            "hour_start_utc": labels.tz_convert("UTC"),
            "is_complete": True,
            "dispatch_totaldemand_hourly_mean_mw": value,
            "last_source_settlementdate": (labels + pd.Timedelta(hours=1)).strftime(
                "%Y/%m/%d %H:%M:%S"
            ),
        }
    )
    obs = Observations.from_hourly(hourly, "dispatch_totaldemand_hourly_mean_mw")
    wide, _ = build_profile(
        obs,
        design_keys(start, dt.date(2025, 7, 31)),
        "primary_operational",
        1,
        vic_public_holidays(range(2023, 2027)),
    )
    wide["origin_date"] = wide["origin_market"].dt.date
    return wide


@pytest.fixture(scope="module")
def fold_result() -> tuple:
    design = synthetic_design()
    fold = build_folds(CFG)[0]
    preds, fits, offsets, threshold = run_fold(design, fold, CFG)
    return design, fold, preds, fits, offsets, threshold


def test_fold_partitions_disjoint_and_origins_not_split(fold_result: tuple) -> None:
    _design, fold, preds, fits, offsets, _ = fold_result
    val_dates = set(preds["origin_date"])
    assert (
        min(val_dates) == fold.validation_first_origin
        and max(val_dates) == fold.validation_last_origin
    )
    assert (preds.groupby(["model", "origin_date"]).size() == 24).all()  # whole origins only
    hybrid = next(f for f in fits if f["model"] == "day_week_hybrid")
    assert dt.date.fromisoformat(hybrid["fit_last_origin"]) == fold.weight_fit_last_origin
    weight_dates = pd.Series(
        pd.date_range(hybrid["fit_first_origin"], hybrid["fit_last_origin"])
    ).dt.date
    cal_dates = set(pd.date_range(fold.calibration_first_origin, fold.calibration_last_origin).date)
    assert not (set(weight_dates) & cal_dates) and not (cal_dates & val_dates)
    assert set(offsets[0]["n_samples"]) == {0, 180}  # previous_day: 180 per horizon 1-23, 0 at h24


def test_previous_day_horizon_24_missing_and_calibration_counts(fold_result: tuple) -> None:
    _, _, preds, _, offsets, _ = fold_result
    day = preds[preds["model"] == "previous_day"]
    assert not day.loc[day["horizon"] == 24, "available"].any()
    assert day.loc[day["horizon"] < 24, "available"].all()
    table = next(o for o in offsets if o["model"].iloc[0] == "previous_day").set_index("horizon")
    assert table.loc[24, "n_samples"] == 0 and not table.loc[24, "calibrated"]
    assert (table.loc[1:23, "n_samples"] == 180).all()
    hyb = preds[preds["model"] == "day_week_hybrid"]
    assert hyb.loc[hyb["horizon"] == 24, "fallback_used"].all() and hyb["available"].all()


def test_training_only_high_demand_threshold(fold_result: tuple) -> None:
    design, fold, _, _, _, threshold = fold_result
    train = design[design["origin_date"] <= fold.last_matured_origin]
    assert threshold == pytest.approx(np.quantile(train[TARGET], 0.9))
    poisoned = design.copy()
    val = poisoned["origin_date"] >= fold.validation_first_origin
    poisoned.loc[val, TARGET] = 1e9  # validation outcomes cannot move the threshold
    _, _, _, threshold2 = run_fold(poisoned, fold, CFG)
    assert threshold2 == threshold


def test_predictions_invariant_to_future_targets(fold_result: tuple) -> None:
    design, fold, preds, _, _, _ = fold_result
    poisoned = design.copy()
    after = poisoned["origin_date"] >= fold.validation_first_origin
    poisoned.loc[after, TARGET] = -1.0
    poisoned.loc[after, "recent_24h_mean"] = -1.0
    preds2, _, _, _ = run_fold(poisoned, fold, CFG)
    cols = ["model", "origin_utc", "horizon", "point_raw", "p10", "p50", "p90", "available"]
    pd.testing.assert_frame_equal(preds[cols], preds2[cols])


def test_common_rows_and_coverage_accounting(fold_result: tuple) -> None:
    design, fold, preds, _, _, threshold = fold_result
    ev = attach_evaluation(preds, design, {fold.fold_id: threshold}, CFG)
    res = comparisons(ev, CFG)
    assert "previous_day" not in res["full_horizon_models"]
    assert set(res["full_horizon_models"]) == {
        "previous_week",
        "same_weekday_4w",
        "day_week_hybrid",
    }
    assert res["common_row_horizons"] == list(range(1, 24))
    n_days = (fold.validation_last_origin - fold.validation_first_origin).days + 1
    assert res["common_rows"] == n_days * 23
    assert all(r["n"] == n_days * 23 for r in res["common_row_comparison"].values())
    cov = res["coverage"]["previous_day"]
    assert cov["expected_rows"] == n_days * 24 and cov["predicted_rows"] == n_days * 23
    assert cov["origins_with_all_24_horizons"] == 0 and cov["origin_coverage"] == 0.0
    assert res["coverage"]["previous_week"]["row_coverage"] == 1.0
    assert ev["is_civil_evening"].sum() == len(ev) * 5 / 24
    assert {p["row_set"] for p in res["paired_bootstrap"]} == {"full_horizon", "common_rows_h1_23"}


# --- bootstrap ----------------------------------------------------------------------


def test_daily_block_bootstrap_keeps_dates_together() -> None:
    dates = pd.Series(["d1"] * 4 + ["d2"])
    a = pd.Series([2.0, 2.0, 2.0, 2.0, 0.0])
    b = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0])
    res = paired_block_bootstrap(a, b, dates, replicates=500, seed=7)
    assert res["mae_difference_mw"] == pytest.approx((8 - 0 - 4 - 1) / 5)  # 0.6
    # block resampling can only produce: all d1 (+1), all d2 (-1), or mixtures of whole dates
    rng = np.random.default_rng(7)
    idx = rng.integers(0, 2, size=(500, 2))
    sums_a, sums_b, n = np.array([8.0, 0.0]), np.array([4.0, 1.0]), np.array([4, 1])
    expected = (sums_a[idx].sum(1) - sums_b[idx].sum(1)) / n[idx].sum(1)
    assert set(np.round(expected, 6)) <= {1.0, -1.0, 0.6}
    assert res["ci_low"] == pytest.approx(np.quantile(expected, 0.025))
    again = paired_block_bootstrap(a, b, dates, replicates=500, seed=7)
    assert again == res  # seeded and deterministic
    with pytest.raises(ValueError):
        paired_block_bootstrap(
            pd.Series([1.0, np.nan]), pd.Series([1.0, 1.0]), pd.Series(["d1", "d1"]), 10, 1
        )


def test_readable_reports_and_plots(fold_result: tuple, tmp_path: Path) -> None:
    from nem_forecast.evaluation.report import write_readable_reports

    design, fold, preds, fits, offsets, threshold = fold_result
    ev = attach_evaluation(preds, design, {fold.fold_id: threshold}, CFG)
    res = comparisons(ev, CFG)
    write_readable_reports(tmp_path, res, fits, pd.concat(offsets), ev, [fold])
    for name in ("metrics.md", "slices.md", "residual_quantiles.md"):
        assert (tmp_path / name).read_text().startswith("# ")
    for png in ("mae_by_horizon.png", "coverage_by_horizon.png", "mae_by_fold.png"):
        assert (tmp_path / "plots" / png).stat().st_size > 5_000
