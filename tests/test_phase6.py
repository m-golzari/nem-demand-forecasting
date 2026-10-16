"""Phase 6 LightGBM: chronology, allowlist, missing values, quantiles, calibration (offline)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nem_forecast.config import ConfigError, load_backtest_config, load_lightgbm_protocol
from nem_forecast.contract import NEM_MARKET_TZ
from nem_forecast.evaluation import metrics as mt
from nem_forecast.evaluation.backtest import TARGET
from nem_forecast.evaluation.folds import (
    HoldoutAccessError,
    assert_development_origins,
    build_folds,
)
from nem_forecast.evaluation.phase6 import (
    apply_quantile_offsets,
    combine_with_fallback,
    hybrid_season_offsets,
    nested_windows,
    residual_offsets,
    run_lightgbm_fold,
    season_months,
)
from nem_forecast.features.calendar import vic_public_holidays
from nem_forecast.features.design import build_profile, design_keys
from nem_forecast.features.history import Observations
from nem_forecast.models.lightgbm_quantile import feature_matrix, fit_quantile, lgb_params, predict
from tests.conftest import REPO_ROOT

BCFG = load_backtest_config()
PROTO = load_lightgbm_protocol()
ALLOW = PROTO.feature_allowlist.columns
FOLD1 = build_folds(BCFG)[0]


def small_proto():  # type: ignore[no-untyped-def]
    """Reduced search for fast tests (2 candidates, 60 rounds); validated protocol untouched."""
    lgbp = PROTO.lightgbm.model_copy(
        update={
            "max_boosting_rounds": 60,
            "early_stopping_rounds": 10,
            "candidates": {"num_leaves": (7, 15), "min_data_in_leaf": (50,), "lambda_l2": (0.0,)},
            "fixed_params": {**PROTO.lightgbm.fixed_params, "learning_rate": 0.2, "num_threads": 2},
        }
    )
    return PROTO.model_copy(update={"lightgbm": lgbp})


@pytest.fixture(scope="module")
def design() -> pd.DataFrame:
    start = dt.date(2022, 1, 1)
    labels = pd.date_range(
        pd.Timestamp(dt.datetime.combine(start, dt.time(0), tzinfo=NEM_MARKET_TZ)),
        pd.Timestamp("2025-08-01 00:00", tz=NEM_MARKET_TZ),
        freq="h",
        inclusive="left",
    )
    rng = np.random.default_rng(3)
    value = (
        5000
        + 900 * np.sin(2 * np.pi * (labels.hour.to_numpy() - 6) / 24)
        + 400 * np.cos(2 * np.pi * labels.dayofyear.to_numpy() / 365.25)
        + rng.normal(0, 150, len(labels))
    )
    hourly = pd.DataFrame(
        {
            "hour_start_utc": labels.tz_convert("UTC"),
            "is_complete": True,
            "v": value,
            "last_source_settlementdate": (labels + pd.Timedelta(hours=1)).strftime(
                "%Y/%m/%d %H:%M:%S"
            ),
        }
    )
    wide, _ = build_profile(
        Observations.from_hourly(hourly, "v"),
        design_keys(start, dt.date(2025, 7, 31)),
        "primary_operational",
        1,
        vic_public_holidays(range(2021, 2027)),
    )
    wide["origin_date"] = wide["origin_market"].dt.date
    return wide


# --- allowlist and protocol ---------------------------------------------------------


def test_allowlist_is_registered_and_positive() -> None:
    registry = (REPO_ROOT / "docs" / "feature_registry.md").read_text()
    assert len(ALLOW) == 32
    for col in ALLOW:
        documented = col.rsplit("_", 1)[0] + "_sin/cos" if col.endswith(("_sin", "_cos")) else col
        assert documented in registry, col
    forbidden_parts = ("target", "missing_reason", "eligible")
    forbidden_exact = {
        "civil_date",
        "civil_timestamp",
        "market_date",
        "origin_utc",
        "origin_market",
        "target_hour_utc",
        "target_hour_market",
        "cutoff_utc",
        "year",
        "fold_id",
        "availability_profile",
        "availability_lag_hours",
        "civil_public_holiday_name",
    }
    assert not [c for c in ALLOW if c in forbidden_exact or any(f in c for f in forbidden_parts)]
    assert len(PROTO.lightgbm.candidate_grid) == 12


def test_feature_matrix_positive_selection_order_and_missing(design: pd.DataFrame) -> None:
    rows = design[design["horizon"] == 24].head(5)
    x = feature_matrix(rows, ALLOW)
    lag_col = ALLOW.index("lag_24h")
    assert np.isnan(x[:, lag_col]).all()  # structural NaN preserved, never zero-filled
    assert (x[:, ALLOW.index("lag_24h__available")] == 0).all()
    shuffled = rows[list(reversed(rows.columns))].assign(unapproved=123.0)
    np.testing.assert_array_equal(
        np.nan_to_num(x, nan=-1), np.nan_to_num(feature_matrix(shuffled, ALLOW), nan=-1)
    )
    with pytest.raises(KeyError):
        feature_matrix(rows.drop(columns=["horizon"]), ALLOW)


def test_prediction_invariance(design: pd.DataFrame) -> None:
    train = design[
        (design["origin_date"] < dt.date(2024, 1, 1)) & design["eligible_initial_ml_features"]
    ]
    fixed = {**PROTO.lightgbm.fixed_params, "num_threads": 2}
    res = fit_quantile(
        feature_matrix(train, ALLOW),
        train[TARGET].to_numpy(),
        lgb_params(fixed, {"num_leaves": 15, "min_data_in_leaf": 50, "lambda_l2": 0.0}, 0.5),
        40,
        ALLOW,
    )
    test = design[design["origin_date"] >= dt.date(2024, 1, 1)].head(200)
    base = predict(res.booster, feature_matrix(test, ALLOW), 40)
    changed = test.copy()
    changed[TARGET] = -1e9  # future targets
    for col in [c for c in changed.columns if c.endswith("__missing_reason")]:
        changed[col] = "tampered"  # audit / lineage fields
    changed["cutoff_utc"] = changed["cutoff_utc"] + pd.Timedelta(days=3)
    changed["unapproved_column"] = np.arange(len(changed))
    changed = changed[list(reversed(changed.columns))]  # column order
    np.testing.assert_array_equal(base, predict(res.booster, feature_matrix(changed, ALLOW), 40))


def test_quantile_objectives_order_and_coverage() -> None:
    rng = np.random.default_rng(0)
    x = rng.uniform(0, 10, (4000, 1))
    y = 3 * x[:, 0] + rng.normal(0, 2, 4000)
    fixed = {**PROTO.lightgbm.fixed_params, "num_threads": 2}
    preds = {}
    for q in (0.1, 0.5, 0.9):
        r = fit_quantile(
            x,
            y,
            lgb_params(fixed, {"num_leaves": 7, "min_data_in_leaf": 50, "lambda_l2": 0.0}, q),
            200,
            ("x",),
        )
        preds[q] = predict(r.booster, x, 200)
    assert np.mean(y < preds[0.1]) == pytest.approx(0.1, abs=0.03)
    assert np.mean(y < preds[0.5]) == pytest.approx(0.5, abs=0.03)
    assert np.mean(y < preds[0.9]) == pytest.approx(0.9, abs=0.03)
    assert np.mean(preds[0.1] < preds[0.9]) > 0.99


def test_deterministic_training(design: pd.DataFrame) -> None:
    train = design[
        (design["origin_date"] < dt.date(2023, 6, 1)) & design["eligible_initial_ml_features"]
    ]
    params = lgb_params(
        {**PROTO.lightgbm.fixed_params, "num_threads": 2},
        {"num_leaves": 15, "min_data_in_leaf": 50, "lambda_l2": 10.0},
        0.9,
    )
    x, y = feature_matrix(train, ALLOW), train[TARGET].to_numpy()
    a = fit_quantile(x, y, params, 30, ALLOW).booster.model_to_string()
    b = fit_quantile(x, y, params, 30, ALLOW).booster.model_to_string()
    assert a == b


# --- chronology ---------------------------------------------------------------------


def test_nested_windows_chronology() -> None:
    for fold in build_folds(BCFG):
        w = nested_windows(fold, PROTO, BCFG)
        assert w.inner_train_last == w.tuning_first - dt.timedelta(days=2)
        assert (w.tuning_last - w.tuning_first).days == 89
        assert w.tuning_last == w.calibration_first - dt.timedelta(days=1)
        assert w.calmodel_train_last == w.calibration_first - dt.timedelta(days=2)
        assert w.final_train_last == fold.validation_first_origin - dt.timedelta(days=2)
        assert w.calibration_last < fold.validation_first_origin
        assert w.season_window_last < fold.validation_first_origin
        assert w.season_window_first == fold.validation_first_origin - dt.timedelta(days=730)
    assert nested_windows(FOLD1, PROTO, BCFG).season == "winter"


def test_season_months_same_season_and_matured(design: pd.DataFrame) -> None:
    w = nested_windows(FOLD1, PROTO, BCFG)
    months = season_months(design, w, BCFG)
    assert [m.month for m, _ in months] and {m.month for m, _ in months} <= {6, 7, 8}
    for _, rows in months:
        assert rows["origin_date"].max() <= w.season_window_last
        assert rows["origin_date"].min() >= w.season_window_first


def test_holdout_rejection_and_cli_guard(tmp_project) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(HoldoutAccessError):
        assert_development_origins([dt.date(2026, 7, 1)])
    text = (REPO_ROOT / "configs" / "backtest.yaml").read_text()
    Path(tmp_project.configs / "backtest.yaml").write_text(
        text.replace('last_origin: "2026-06-30"', 'last_origin: "2026-07-15"')
    )
    with pytest.raises(ConfigError):
        load_backtest_config(paths=tmp_project)


# --- calibration --------------------------------------------------------------------


def test_residual_offsets_most_recent_cap_and_min() -> None:
    dates = [dt.date(2025, 1, d) for d in range(1, 13)]
    res = pd.DataFrame(
        {
            "horizon": 1,
            "quantile": 0.5,
            "origin_date": dates,
            "residual": [100.0, 100.0] + [float(i) for i in range(1, 11)],
        }
    )
    capped = residual_offsets(res, min_samples=5, max_samples=10).iloc[0]
    assert capped["n_samples"] == 10 and capped["offset"] == pytest.approx(
        5.5
    )  # oldest two dropped
    short = residual_offsets(res, min_samples=20, max_samples=None).iloc[0]
    assert not short["calibrated"] and np.isnan(short["offset"])


def test_fallback_and_quantile_specific_adjustment() -> None:
    trailing = pd.DataFrame(
        {
            "horizon": [1, 1, 1, 2, 2, 2],
            "quantile": [0.1, 0.5, 0.9] * 2,
            "offset": [-10.0, 0.0, 10.0, -20.0, 1.0, 20.0],
            "calibrated": True,
        }
    )
    season = pd.DataFrame(
        {
            "horizon": [1, 1, 1, 2, 2, 2],
            "quantile": [0.1, 0.5, 0.9] * 2,
            "offset": [-5.0, 2.0, 5.0, np.nan, np.nan, np.nan],
            "calibrated": [True] * 3 + [False] * 3,
            "n_samples": [200] * 3 + [40] * 3,
        }
    )
    combo = combine_with_fallback(season, trailing)
    assert list(combo["source"]) == ["season"] * 3 + ["trailing_fallback"] * 3
    raw = pd.DataFrame(
        {
            "horizon": [1, 2],
            "raw_p10": [90.0, 90.0],
            "raw_p50": [100.0, 100.0],
            "raw_p90": [110.0, 110.0],
            "available": True,
        }
    )
    out = apply_quantile_offsets(raw, combo, "applied_offset", "applied_calibrated")
    assert list(out["p10"]) == [85.0, 70.0] and list(out["p50"]) == [102.0, 101.0]
    assert list(out["p90"]) == [115.0, 130.0]
    crossing = apply_quantile_offsets(
        raw, trailing.assign(offset=[50.0, 0.0, 0.0, 0.0, 0.0, 0.0]), "offset", "calibrated"
    )
    assert (
        mt.crossing_rate(
            crossing["p10"].to_numpy(), crossing["p50"].to_numpy(), crossing["p90"].to_numpy()
        )
        == 0.5
    )


def test_mean_pinball_and_crossing_magnitude_hand_calculated() -> None:
    y = np.array([100.0])
    p10, p50, p90 = np.array([90.0]), np.array([110.0]), np.array([105.0])
    # pinball: 0.1*10=1, 0.5*10=5, 0.1*5=0.5 -> mean 6.5/3
    assert np.mean(
        [mt.pinball(0.1, p10, y), mt.pinball(0.5, p50, y), mt.pinball(0.9, p90, y)]
    ) == pytest.approx(6.5 / 3)
    assert mt.crossing_magnitude(p10, p50, p90) == pytest.approx(5.0)


def test_hybrid_season_offsets_ignore_outer_outcomes(design: pd.DataFrame) -> None:
    w = nested_windows(FOLD1, PROTO, BCFG)
    base = hybrid_season_offsets(design, w, PROTO, BCFG)
    poisoned = design.copy()
    poisoned.loc[poisoned["origin_date"] >= FOLD1.validation_first_origin, TARGET] = 1e9
    pd.testing.assert_frame_equal(base, hybrid_season_offsets(poisoned, w, PROTO, BCFG))
    assert (base["n_samples"] <= 270).all()


# --- full fold with reduced search ----------------------------------------------------


@pytest.fixture(scope="module")
def fold_run(design: pd.DataFrame) -> dict:
    return run_lightgbm_fold(
        design, FOLD1, nested_windows(FOLD1, small_proto(), BCFG), small_proto(), BCFG
    )


def test_fold_selection_records_and_outer_only(fold_run: dict, design: pd.DataFrame) -> None:
    cands = fold_run["candidates"]
    assert len(cands) == 2
    assert fold_run["selected"]["inner_mae_mw"] == min(c["inner_mae_mw"] for c in cands)
    assert set(fold_run["iterations"]) == {"p10", "p50", "p90"}
    raw = fold_run["raw"]
    assert raw["origin_date"].min() == FOLD1.validation_first_origin
    assert raw["origin_date"].max() == FOLD1.validation_last_origin
    assert len(raw) == 31 * 24
    assert raw.loc[raw["horizon"] == 24, "raw_p50"].notna().all()  # NaN lag handled natively
    assert set(fold_run["trailing"]["n_samples"]) == {180}


def test_fold_invariant_to_outer_targets(fold_run: dict, design: pd.DataFrame) -> None:
    poisoned = design.copy()
    poisoned.loc[poisoned["origin_date"] >= FOLD1.validation_first_origin, TARGET] = -1e6
    again = run_lightgbm_fold(
        poisoned, FOLD1, nested_windows(FOLD1, small_proto(), BCFG), small_proto(), BCFG
    )
    pd.testing.assert_frame_equal(fold_run["raw"], again["raw"])
    pd.testing.assert_frame_equal(fold_run["trailing"], again["trailing"])
    pd.testing.assert_frame_equal(fold_run["season"], again["season"])
