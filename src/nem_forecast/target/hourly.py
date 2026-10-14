"""Hourly target construction from selected interval records (pure, offline).

Target: the **hourly mean of AEMO dispatch ``TOTALDEMAND`` estimates for VIC1,
in MW** (docs/forecast_specification.md). It is not measured consumption,
operational demand or energy (MWh).

Rules:

* each record maps to the canonical hour containing its represented interval
  ``(end - 5 min, end]``. Records ending 00:05 ... 01:00 form the hour labelled
  00:00; the record ending exactly 00:00 belongs to the preceding hour;
* only the primary record per interval is used (D-06): the single ordinary
  record, or the physical (``INTERVENTION=1``) record of a pair. Pricing
  records are never used and never combined with physical records;
* an hour is **complete** only if all 12 expected endpoints
  ``hour + 5 min, ..., hour + 60 min`` are present exactly once among primary
  records, every value is finite, every record is VIC1 with a consistent
  ``DISPATCHINTERVAL``, and no record in the hour is quarantined. Row count
  alone is insufficient;
* the target is the arithmetic mean of the 12 values (equal 5-minute
  durations make this the duration-weighted mean). Incomplete hours keep a null
  target: nothing is interpolated, clipped or averaged over fewer records;
* no availability buffer is applied here (D-07 belongs to Phase 3 features).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from nem_forecast.contract import DISPATCH_INTERVAL, NEM_MARKET_TZ
from nem_forecast.ingestion.aemo_normalise import PHYSICAL, PRICING, QUARANTINED, REGION

TARGET_COLUMN = "dispatch_totaldemand_hourly_mean_mw"
TARGET_DESCRIPTION = (
    "Hourly mean of AEMO dispatch TOTALDEMAND estimates for VIC1, in MW: arithmetic mean of "
    "the 12 selected 5-minute end-of-interval estimates whose intervals lie in the half-open "
    "hour [start, start + 1 h) NEM market time (UTC+10). Physical run (INTERVENTION=1) in "
    "intervention intervals. Not measured consumption, operational demand or MWh."
)
EXPECTED_ENDPOINTS = 12
REASONS = (
    "no_records",
    "missing_endpoint",
    "duplicate_endpoint",
    "unexpected_endpoint",
    "nonfinite_demand",
    "quarantined_record",
    "non_vic1_region",
    "dispatchinterval_mismatch",
    "outside_canonical_range",
)
REQUIRED = [
    "settlementdate_source",
    "region_id",
    "intervention",
    "total_demand_mw",
    "interval_end_market",
    "hour_start_market",
    "selection_status",
    "is_primary_target_record",
    "physical_minus_pricing_mw",
    "q_dispatchinterval_mismatch",
]


def canonical_hour_grid(first_hour: pd.Timestamp, last_hour: pd.Timestamp) -> pd.DatetimeIndex:
    if first_hour.tzinfo is None or last_hour.tzinfo is None:
        raise ValueError("canonical hours must be timezone-aware")
    return pd.date_range(
        first_hour.tz_convert(NEM_MARKET_TZ),
        last_hour.tz_convert(NEM_MARKET_TZ),
        freq="h",
        name="hour_start_market",
    )


def build_hourly(
    records: pd.DataFrame, first_hour: pd.Timestamp, last_hour: pd.Timestamp
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return one row per canonical hour (plus any out-of-range hours) and a reconciliation."""
    missing = [c for c in REQUIRED if c not in records.columns]
    if missing:
        raise ValueError(f"records are missing columns: {missing}")
    mapped = records[records["interval_end_market"].notna()].copy()
    hour = mapped["hour_start_market"]
    offset = (
        mapped["interval_end_market"] - hour
    ).dt.total_seconds() / DISPATCH_INTERVAL.total_seconds()
    status = mapped["selection_status"]
    primary = mapped["is_primary_target_record"].astype(bool)
    expected_endpoint = (offset == np.floor(offset)) & offset.between(1, EXPECTED_ENDPOINTS)
    demand = mapped["total_demand_mw"]
    finite = pd.Series(
        np.isfinite(demand.to_numpy(dtype=float, na_value=np.nan)), index=mapped.index
    )
    good = primary & expected_endpoint

    frame = pd.DataFrame(
        {
            "hour_start_market": hour,
            "one": 1,
            "primary": primary.astype("int64"),
            "pricing": (status == PRICING).astype("int64"),
            "quarantined": (status == QUARANTINED).astype("int64"),
            "physical": (status == PHYSICAL).astype("int64"),
            "non_vic1": (mapped["region_id"] != REGION).astype("int64"),
            "di_mismatch": mapped["q_dispatchinterval_mismatch"].astype("int64"),
            "unexpected": (primary & ~expected_endpoint).astype("int64"),
            "nonfinite": (primary & ~finite).astype("int64"),
            "good_rows": good.astype("int64"),
            "good_value": demand.where(good & finite),
            "good_end": mapped["interval_end_market"].where(good),
            "abs_diff": mapped["physical_minus_pricing_mw"].abs().where(status == PHYSICAL),
            "src_primary": mapped["settlementdate_source"].where(primary),
        }
    )
    grouped = frame.groupby("hour_start_market", sort=True)
    hourly = pd.DataFrame(
        {
            "n_records": grouped["one"].sum(),
            "n_primary_records": grouped["primary"].sum(),
            "n_pricing_not_selected": grouped["pricing"].sum(),
            "n_quarantined_records": grouped["quarantined"].sum(),
            "n_intervention_intervals": grouped["physical"].sum(),
            "n_non_vic1_records": grouped["non_vic1"].sum(),
            "n_dispatchinterval_mismatch": grouped["di_mismatch"].sum(),
            "n_unexpected_endpoints": grouped["unexpected"].sum(),
            "n_nonfinite_values": grouped["nonfinite"].sum(),
            "n_expected_endpoint_rows": grouped["good_rows"].sum(),
            "n_unique_expected_endpoints": grouped["good_end"].nunique(),
            "n_finite_values": grouped["good_value"].count(),
            "value_mean": grouped["good_value"].mean(),
            "max_abs_physical_minus_pricing_mw": grouped["abs_diff"].max(),
            "first_source_settlementdate": grouped["src_primary"].min(),
            "last_source_settlementdate": grouped["src_primary"].max(),
        }
    )
    grid = canonical_hour_grid(first_hour, last_hour)
    hourly.index = pd.DatetimeIndex(hourly.index).tz_convert(NEM_MARKET_TZ)
    hourly = hourly.reindex(hourly.index.union(grid))
    counts = [c for c in hourly.columns if c.startswith("n_")]
    hourly[counts] = hourly[counts].fillna(0).astype("int64")
    hourly.index.name = "hour_start_market"
    hourly["in_canonical_range"] = hourly.index.isin(grid)

    n_dup = hourly["n_expected_endpoint_rows"] - hourly["n_unique_expected_endpoints"]
    flags = {
        "no_records": hourly["n_records"] == 0,
        "missing_endpoint": hourly["n_unique_expected_endpoints"] < EXPECTED_ENDPOINTS,
        "duplicate_endpoint": n_dup > 0,
        "unexpected_endpoint": hourly["n_unexpected_endpoints"] > 0,
        "nonfinite_demand": hourly["n_nonfinite_values"] > 0,
        "quarantined_record": hourly["n_quarantined_records"] > 0,
        "non_vic1_region": hourly["n_non_vic1_records"] > 0,
        "dispatchinterval_mismatch": hourly["n_dispatchinterval_mismatch"] > 0,
        "outside_canonical_range": ~hourly["in_canonical_range"],
    }
    flag_frame = pd.DataFrame(flags)
    complete = ~flag_frame.any(axis=1)
    complete &= hourly["n_primary_records"] == EXPECTED_ENDPOINTS
    complete &= hourly["n_finite_values"] == EXPECTED_ENDPOINTS
    hourly["is_complete"] = complete
    hourly["quality_reasons"] = flag_frame.apply(
        lambda r: ";".join(name for name in REASONS if r[name]), axis=1
    ).astype("string")
    hourly[TARGET_COLUMN] = hourly["value_mean"].where(complete)
    hourly["intervention_affected"] = hourly["n_intervention_intervals"] > 0
    hourly = hourly.drop(columns=["value_mean"]).reset_index()

    start = hourly["hour_start_market"]
    hourly.insert(1, "hour_end_market", start + pd.Timedelta(hours=1))
    hourly.insert(2, "hour_start_utc", start.dt.tz_convert("UTC"))
    hourly.insert(3, "hour_end_utc", (start + pd.Timedelta(hours=1)).dt.tz_convert("UTC"))
    hourly.insert(4, "market_date", start.dt.date)
    hourly.insert(5, "year", start.dt.year.astype("int64"))

    reason_counts = {name: int(flag_frame[name].sum()) for name in REASONS}
    reconciliation = {
        "records_in": len(records),
        "records_unmapped_timestamp": len(records) - len(mapped),
        "records_by_region": {
            str(k): int(v) for k, v in mapped["region_id"].value_counts().items()
        },
        "records_by_selection_status": {str(k): int(v) for k, v in status.value_counts().items()},
        "primary_records": int(primary.sum()),
        "hours_expected": len(grid),
        "hours_output": len(hourly),
        "hours_outside_canonical_range": int((~hourly["in_canonical_range"]).sum()),
        "hours_complete": int(hourly["is_complete"].sum()),
        "hours_incomplete_or_invalid": int((~hourly["is_complete"]).sum()),
        "hours_intervention_affected": int(hourly["intervention_affected"].sum()),
        "reason_counts": reason_counts,
        "identity_records_sum": int(hourly["n_records"].sum()) == len(mapped),
        "identity_primary_sum": int(hourly["n_primary_records"].sum()) == int(primary.sum()),
        "expected_primary_records_for_grid": len(grid) * EXPECTED_ENDPOINTS,
    }
    return hourly, reconciliation


def model_ready(hourly: pd.DataFrame) -> pd.DataFrame:
    """Complete, in-range hours only, with the target and minimal provenance columns."""
    keep = hourly["is_complete"] & hourly["in_canonical_range"]
    cols = [
        "hour_start_market",
        "hour_end_market",
        "hour_start_utc",
        "hour_end_utc",
        "market_date",
        "year",
        TARGET_COLUMN,
        "intervention_affected",
        "n_intervention_intervals",
        "first_source_settlementdate",
        "last_source_settlementdate",
    ]
    return hourly.loc[keep, cols].reset_index(drop=True)
