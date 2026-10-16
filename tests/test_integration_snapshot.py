"""Bounded integration audit on the local historical snapshot (not committed data).

Deselected by default; run with ``pytest -m integration``. Audits only the
archives around each observed schema change to keep the run bounded.
"""

import pytest

from nem_forecast.ingestion import snapshot
from nem_forecast.paths import ProjectPaths
from nem_forecast.validation.historical_audit import audit_snapshot

pytestmark = pytest.mark.integration

SNAPSHOT = "DISPATCHREGIONSUM_2022-01_2026-09_retrieved20261014"
BOUNDED_MONTHS = {
    "2022-06",
    "2024-04",
    "2024-05",
    "2024-07",
    "2024-08",
    "2025-03",
    "2025-04",
    "2025-05",
    "2025-06",
    "2026-09",
}


@pytest.fixture(scope="module")
def bounded() -> tuple[dict, object, object]:
    paths = ProjectPaths.from_root()
    try:
        document = snapshot.load_complete_snapshot(paths, "DISPATCHREGIONSUM", SNAPSHOT)
    except Exception as exc:
        pytest.skip(f"local snapshot unavailable: {exc}")
    subset = {
        **document,
        "files": [f for f in document["files"] if f["archive_month"] in BOUNDED_MONTHS],
    }
    return audit_snapshot(paths.root, subset)


def test_snapshot_manifest_complete() -> None:
    paths = ProjectPaths.from_root()
    try:
        document = snapshot.load_complete_snapshot(paths, "DISPATCHREGIONSUM", SNAPSHOT)
    except Exception:
        pytest.skip("local snapshot unavailable")
    assert document["file_count"] == 57
    assert next(f["archive_month"] for f in document["files"]) == "2022-01"
    assert [f["archive_month"] for f in document["files"]][-1] == "2026-09"


def test_bounded_audit_schema_and_selection(bounded: tuple) -> None:
    report, _interventions, quarantined = bounded
    assert report["file_count"] == len(BOUNDED_MONTHS)
    assert set(report["report_versions"]) == {"5", "8", "9"}
    added = {c for ch in report["schema_changes"] for c in ch["added_columns"]}
    assert {"BDU_ENERGY_STORAGE", "BDU_INITIAL_ENERGY_STORAGE"} <= added
    assert report["quarantined_rows"] == 0 and quarantined.empty
    assert report["cross_file_pk_duplicate_rows"] == 0
    vic = report["coverage_by_region"]["VIC1"]
    assert vic["dispatchinterval_mismatches"] == 0
    assert vic["totaldemand_null_all_rows"] == 0
    # June 2022 contains intervention pairs: physical selected exactly once per pair
    statuses = vic["selection_status_counts"]
    assert statuses["physical_selected"] == statuses["pricing_not_selected"] > 0
    assert (
        vic["primary_records"]
        == sum(f["rows_by_region"]["VIC1"] for f in report["files"])
        - statuses["pricing_not_selected"]
    )
    assert report["files"][-1]["settlementdate_max"] == "2026-10-01 00:00:00+10:00"


def test_real_intervention_hour_uses_physical_value() -> None:
    """2022-06-15 interval ending 06:20: physical - pricing = -19.97 MW (Phase 1B audit)."""
    import datetime as dt

    import pandas as pd

    from nem_forecast.contract import NEM_MARKET_TZ
    from nem_forecast.ingestion.aemo_csv import read_aemo_zip
    from nem_forecast.ingestion.aemo_normalise import (
        add_quality_flags,
        classify_primary_records,
        typed_frame,
    )
    from nem_forecast.target.hourly import TARGET_COLUMN, build_hourly
    from nem_forecast.target.reconcile import read_raw_vic1

    paths = ProjectPaths.from_root()
    lock_file = paths.root / "configs" / "data_snapshots" / f"{SNAPSHOT}.lock.json"
    try:
        lock = snapshot.load_lock(lock_file)
        archives = snapshot.verify_lock_against_local(paths, lock)
    except Exception as exc:
        pytest.skip(f"local snapshot unavailable: {exc}")
    june = archives[[a["month"] for a in lock["archives"]].index("2022-06")]

    # independent expectation from the stdlib reader
    raw, _ = read_raw_vic1(june)
    ends = [
        f"2022/06/15 {h:02d}:{m:02d}:00" for h, m in [(6, m) for m in range(5, 60, 5)] + [(7, 0)]
    ]
    hour = raw[raw["settlementdate"].isin(ends)]
    at_0620 = hour[hour["settlementdate"] == "2022/06/15 06:20:00"].set_index("intervention")
    physical, pricing = (
        float(at_0620.loc["1", "totaldemand"]),
        float(at_0620.loc["0", "totaldemand"]),
    )
    assert physical - pricing == pytest.approx(-19.97, abs=1e-6)
    chosen = hour.sort_values("intervention").groupby("settlementdate").tail(1)  # "1" > "0"
    assert len(chosen) == 12
    expected = chosen["totaldemand"].astype(float).mean()
    pricing_only = hour[hour["intervention"] == "0"]["totaldemand"].astype(float).mean()

    records = classify_primary_records(
        add_quality_flags(typed_frame(read_aemo_zip(june)).query("region_id == 'VIC1'"))
    )
    label = pd.Timestamp(dt.datetime(2022, 6, 15, 6, tzinfo=NEM_MARKET_TZ))
    hourly, _ = build_hourly(records, label, label)
    h = hourly.set_index("hour_start_market").loc[label]
    assert h["is_complete"] and h["intervention_affected"]
    assert h[TARGET_COLUMN] == pytest.approx(expected, abs=1e-9)
    assert abs(h[TARGET_COLUMN] - pricing_only) > 1e-6  # the pricing run would differ
    assert h["max_abs_physical_minus_pricing_mw"] == pytest.approx(19.97, abs=1e-6)


@pytest.mark.parametrize("profile, lag", [("primary_operational", 1), ("sensitivity_zero_lag", 0)])
def test_real_features_bounded_window_match_truncated_reference(profile: str, lag: int) -> None:
    """Real Phase 2 target; origins 2022-06-01..2022-07-31 (spans June 2022 interventions)."""
    import datetime as dt

    import numpy as np
    import pandas as pd

    from nem_forecast.config import load_feature_config
    from nem_forecast.features.build import load_phase2, source_endpoint_contract_check
    from nem_forecast.features.calendar import vic_public_holidays
    from nem_forecast.features.design import build_profile, design_keys
    from nem_forecast.features.history import Observations
    from nem_forecast.features.reference import reference_features
    from nem_forecast.target.hourly import TARGET_COLUMN

    paths = ProjectPaths.from_root()
    try:
        hourly, _ = load_phase2(paths, load_feature_config().input_phase2_run_id)
    except Exception as exc:
        pytest.skip(f"approved Phase 2 run unavailable locally: {exc}")
    assert source_endpoint_contract_check(hourly)["passed"]
    window = hourly[hourly["hour_start_utc"] < pd.Timestamp("2022-08-01", tz="UTC")]
    obs = Observations.from_hourly(window, TARGET_COLUMN)
    keys = design_keys(dt.date(2022, 6, 1), dt.date(2022, 7, 31))
    wide, audit = build_profile(obs, keys, profile, lag, vic_public_holidays(range(2021, 2024)))
    used = audit[audit["available"]]
    assert (used["latest_source_endpoint_utc"] <= used["cutoff_utc"]).all()
    origins = np.unique(wide["origin_utc"].to_numpy(dtype="datetime64[ns]").astype(np.int64))
    ref = reference_features(obs, origins, lag)
    merged = audit.assign(
        origin_ns=audit["origin_utc"].to_numpy(dtype="datetime64[ns]").astype(np.int64)
    ).merge(ref, on=["origin_ns", "horizon", "feature"], suffixes=("", "_ref"))
    assert len(merged) == len(audit) == len(ref)
    assert (merged["available"] == merged["available_ref"]).all()
    both = merged["available"]
    np.testing.assert_allclose(
        merged.loc[both, "value"], merged.loc[both, "value_ref"], atol=1e-9, rtol=0
    )
    h24 = wide[wide["horizon"] == 24]
    assert (h24["lag_24h__available"] == (lag == 0)).all()


def test_real_backtest_fold1_bounded() -> None:
    """Real canonical features, fold 1 only: guard, calibration counts, structural h24 gap."""
    from nem_forecast.config import load_backtest_config
    from nem_forecast.evaluation.backtest import load_design, run_fold
    from nem_forecast.evaluation.folds import build_folds
    from nem_forecast.models.baselines import make_baselines

    cfg = load_backtest_config()
    paths = ProjectPaths.from_root()
    cols = sorted(
        {c for m in make_baselines(cfg.hybrid.weight_grid.values) for c in m.input_columns}
    )
    try:
        design, info = load_design(paths, cfg, cols)
    except Exception as exc:
        pytest.skip(f"canonical feature run unavailable: {exc}")
    assert info["max_origin_loaded"] < "2026-07-01" and info["holdout_rows_loaded"] == 0
    assert set(design["availability_profile"]) == {"primary_operational"}
    fold = build_folds(cfg)[0]
    preds, fits, offsets, _ = run_fold(design, fold, cfg)
    assert preds["origin_date"].max() == fold.validation_last_origin
    day = preds[preds["model"] == "previous_day"]
    assert not day.loc[day["horizon"] == 24, "available"].any()
    for table in offsets:
        cal = table[table["calibrated"]]
        assert (cal["n_samples"] >= cfg.calibration.min_samples_per_horizon).all()
    hybrid = next(f for f in fits if f["model"] == "day_week_hybrid")
    assert hybrid["fit_last_origin"] == "2024-12-31" and 0.0 <= hybrid["weight"] <= 1.0
