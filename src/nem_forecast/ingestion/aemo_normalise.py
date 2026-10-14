"""Normalise raw ``DISPATCHREGIONSUM`` records into canonical interim Parquet.

Rules (docs/aemo_schema_audit.md):

* source strings are preserved alongside typed values;
* ``SETTLEMENTDATE`` is an interval **end** in NEM market time (fixed UTC+10);
* each record maps to the start-labelled canonical hour containing
  ``(end - 5 min, end]``;
* only ``REGIONID == "VIC1"`` rows inside the window are written, and the
  filter is reconciled by count;
* **no record is dropped, deduplicated, clipped or imputed**: intervention
  pairs and anomalies are flagged. Duplicate resolution is an open owner
  decision (docs/decisions.md O-03).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from nem_forecast.contract import (
    DISPATCH_INTERVAL,
    NEM_MARKET_TZ,
    SETTLEMENTDATE_FORMAT,
    settlementdate_bounds,
)
from nem_forecast.ingestion.aemo_csv import SOURCE_LINE, AemoTable

REGION = "VIC1"
PRIMARY_KEY = ["SETTLEMENTDATE", "RUNNO", "REGIONID", "DISPATCHINTERVAL", "INTERVENTION"]
REQUIRED_COLUMNS = [*PRIMARY_KEY, "TOTALDEMAND", "LASTCHANGED"]
TRADING_DAY_OFFSET = dt.timedelta(hours=4)


@dataclass(frozen=True)
class SourceRef:
    file: str
    sha256: str


def _market_time(values: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(values, format=SETTLEMENTDATE_FORMAT, errors="coerce")
    return parsed.dt.tz_localize(NEM_MARKET_TZ)


def expected_dispatchinterval(interval_end: pd.Series) -> pd.Series:
    """AEMO ``DISPATCHINTERVAL`` (``YYYYMMDDPPP``) implied by interval-ending semantics.

    The trading day runs 04:00-04:00 market time; period 001 ends at 04:05.
    """
    trading_start = interval_end - DISPATCH_INTERVAL - TRADING_DAY_OFFSET
    trading_day = trading_start.dt.floor("D")
    period = (trading_start - trading_day) // DISPATCH_INTERVAL + 1
    day_number = trading_day.dt.strftime("%Y%m%d").astype("int64")
    return (day_number * 1000 + period).astype("Int64")


def typed_frame(table: AemoTable) -> pd.DataFrame:
    """Add typed columns next to the raw strings; does not filter or drop rows."""
    missing = [c for c in REQUIRED_COLUMNS if c not in table.columns]
    if missing:
        raise ValueError(f"source table is missing required columns: {missing}")
    raw = table.data
    out = pd.DataFrame(index=raw.index)
    out["source_line"] = raw[SOURCE_LINE]
    out["settlementdate_source"] = raw["SETTLEMENTDATE"]
    out["lastchanged_source"] = raw["LASTCHANGED"]
    out["totaldemand_source"] = raw["TOTALDEMAND"]
    out["region_id"] = raw["REGIONID"]
    out["runno"] = pd.to_numeric(raw["RUNNO"], errors="coerce").astype("Int64")
    out["intervention"] = pd.to_numeric(raw["INTERVENTION"], errors="coerce").astype("Int64")
    out["dispatchinterval"] = pd.to_numeric(raw["DISPATCHINTERVAL"], errors="coerce").astype(
        "Int64"
    )
    out["total_demand_mw"] = pd.to_numeric(raw["TOTALDEMAND"], errors="coerce").astype("float64")
    end = _market_time(raw["SETTLEMENTDATE"])
    out["interval_end_market"] = end
    out["interval_start_market"] = end - DISPATCH_INTERVAL
    out["interval_start_utc"] = out["interval_start_market"].dt.tz_convert("UTC")
    out["interval_end_utc"] = end.dt.tz_convert("UTC")
    out["hour_start_market"] = out["interval_start_market"].dt.floor("h")
    out["hour_start_utc"] = out["hour_start_market"].dt.tz_convert("UTC")
    out["market_date"] = out["hour_start_market"].dt.date
    out["lastchanged_market"] = _market_time(raw["LASTCHANGED"])
    return out


def window_mask(typed: pd.DataFrame, start_date: dt.date, end_date: dt.date) -> pd.Series:
    first_end, last_end = settlementdate_bounds(start_date, end_date)
    end = typed["interval_end_market"]
    mask: pd.Series = (end >= pd.Timestamp(first_end)) & (end <= pd.Timestamp(last_end))
    return mask


def add_quality_flags(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    demand = out["total_demand_mw"]
    out["q_settlementdate_unparsed"] = out["interval_end_market"].isna()
    out["q_demand_null"] = demand.isna()
    out["q_demand_nonfinite"] = demand.notna() & ~np.isfinite(demand.fillna(0.0))
    out["q_demand_nonpositive"] = demand.notna() & (demand <= 0)
    key = ["settlementdate_source", "runno", "region_id", "dispatchinterval", "intervention"]
    out["q_pk_duplicate"] = out.duplicated(subset=key, keep=False)
    per_interval = out.groupby(["settlementdate_source", "region_id"])["intervention"].transform(
        "nunique"
    )
    out["q_has_intervention_counterpart"] = per_interval > 1
    out["q_dispatchinterval_mismatch"] = (
        (expected_dispatchinterval(out["interval_end_market"]) != out["dispatchinterval"])
        .fillna(True)
        .astype(bool)
    )
    return out


def normalise_vic1(
    table: AemoTable, source: SourceRef, start_date: dt.date, end_date: dt.date
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return canonical VIC1 records for the window and a row-count reconciliation."""
    typed = typed_frame(table)
    in_window = window_mask(typed, start_date, end_date)
    is_region = typed["region_id"] == REGION
    selected = typed[in_window & is_region]
    if selected.empty:
        raise ValueError(f"no {REGION} records in window {start_date}..{end_date}")
    assert (selected["region_id"] == REGION).all()
    canonical = add_quality_flags(selected).reset_index(drop=True)
    canonical.insert(0, "source_file", source.file)
    canonical.insert(1, "source_sha256", source.sha256)
    canonical.insert(2, "source_member", table.member)
    canonical.insert(3, "report_version", table.report_version)
    reconciliation = {
        "raw_data_rows": len(typed),
        "raw_rows_unparsed_settlementdate": int(typed["interval_end_market"].isna().sum()),
        "rows_in_window_all_regions": int(in_window.sum()),
        "rows_in_window_by_region": {
            str(k): int(v) for k, v in typed[in_window]["region_id"].value_counts().items()
        },
        "rows_in_window_vic1": len(selected),
        "rows_written": len(canonical),
        "rows_dropped_after_vic1_filter": 0,
    }
    return canonical, reconciliation
