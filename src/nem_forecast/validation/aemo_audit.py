"""Machine-readable audit of a raw ``DISPATCHREGIONSUM`` archive and a VIC1 window.

The audit reports facts only; it does not resolve duplicates or decide
semantics. File-level statistics cover the whole downloaded archive;
quality statistics cover the requested window.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import numpy as np
import pandas as pd

from nem_forecast.contract import DISPATCH_INTERVAL, NEM_MARKET_TZ, settlementdate_bounds
from nem_forecast.ingestion.aemo_csv import AemoTable
from nem_forecast.ingestion.aemo_normalise import (
    REGION,
    add_quality_flags,
    typed_frame,
    window_mask,
)

_DATETIME_STR = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}$")


def _infer_kind(values: pd.Series) -> str:
    present = values[values.fillna("") != ""]
    if present.empty:
        return "all_empty"
    if present.str.match(_DATETIME_STR).all():
        return "datetime_string(YYYY/MM/DD HH:MM:SS)"
    numeric = pd.to_numeric(present, errors="coerce")
    if numeric.notna().all():
        return "integer" if (numeric == np.floor(numeric)).all() else "decimal"
    return "string"


def column_profile(table: AemoTable) -> list[dict[str, Any]]:
    profile = []
    for name in table.columns:
        values = table.data[name]
        empty = int((values.fillna("") == "").sum())
        profile.append(
            {
                "column": name,
                "source_kind": _infer_kind(values),
                "empty_count": empty,
                "non_empty_count": len(values) - empty,
            }
        )
    return profile


def _quantiles(series: pd.Series) -> dict[str, float]:
    clean = series.dropna()
    if clean.empty:
        return {}
    levels = [0.0, 0.01, 0.5, 0.99, 1.0]
    values = clean.quantile(levels).to_numpy(dtype=float)
    return {
        f"q{round(q * 100):02d}": round(float(v), 5) for q, v in zip(levels, values, strict=True)
    } | {"mean": round(float(clean.mean()), 5)}


def _iso(value: Any) -> str | None:
    return None if pd.isna(value) else pd.Timestamp(value).isoformat()


def audit_dispatchregionsum(
    table: AemoTable, start_date: dt.date, end_date: dt.date, source: dict[str, Any]
) -> dict[str, Any]:
    typed = typed_frame(table)
    end = typed["interval_end_market"]

    end_of_report = [c for c in table.comments if len(c) > 1 and c[1] == "END OF REPORT"]
    file_level = {
        "data_rows": len(typed),
        "end_of_report_declared_rows": int(end_of_report[0][2]) if end_of_report else None,
        "settlementdate_min": _iso(end.min()),
        "settlementdate_max": _iso(end.max()),
        "rows_by_region": {str(k): int(v) for k, v in typed["region_id"].value_counts().items()},
        "rows_by_intervention": {
            str(k): int(v) for k, v in typed["intervention"].value_counts(dropna=False).items()
        },
        "runno_values": sorted(int(v) for v in typed["runno"].dropna().unique()),
        "intervention_rows_by_region": {
            str(k): int(v)
            for k, v in typed[typed["intervention"] == 1]["region_id"].value_counts().items()
        },
    }

    in_window = window_mask(typed, start_date, end_date)
    window_all = add_quality_flags(typed[in_window])
    vic = window_all[window_all["region_id"] == REGION].sort_values(
        ["interval_end_market", "intervention"]
    )
    first_end, last_end = settlementdate_bounds(start_date, end_date)
    expected = pd.date_range(first_end, last_end, freq=DISPATCH_INTERVAL, tz=NEM_MARKET_TZ)
    observed = pd.DatetimeIndex(vic["interval_end_market"].dropna().unique())
    missing = expected.difference(observed)
    unexpected = observed.difference(expected)
    spacing = observed.sort_values().to_series().diff().dropna()

    base = vic[vic["intervention"] == 0]
    deltas = base.set_index("interval_end_market")["total_demand_mw"].sort_index().diff()
    top_moves = deltas.abs().nlargest(5)

    pairs = vic[vic["q_has_intervention_counterpart"]]
    pair_diff = (
        pairs.pivot_table(
            index="settlementdate_source", columns="intervention", values="total_demand_mw"
        )
        if not pairs.empty
        else pd.DataFrame()
    )
    lag_minutes = (vic["lastchanged_market"] - vic["interval_end_market"]).dt.total_seconds() / 60

    hourly = vic.groupby(["intervention", "hour_start_market"]).size()
    first_hour = pd.Timestamp(dt.datetime.combine(start_date, dt.time(0), tzinfo=NEM_MARKET_TZ))
    first_hour_members = vic[(vic["hour_start_market"] == first_hour) & (vic["intervention"] == 0)][
        "settlementdate_source"
    ].tolist()

    window = {
        "window_market_dates_end_exclusive": [start_date.isoformat(), end_date.isoformat()],
        "settlementdate_range_expected": [first_end.isoformat(), last_end.isoformat()],
        "rows_all_regions": len(window_all),
        "rows_by_region": {
            str(k): int(v) for k, v in window_all["region_id"].value_counts().items()
        },
        "rows_by_region_intervention": {
            "|".join(str(part) for part in key): int(n)
            for key, n in window_all.groupby(["region_id", "intervention"]).size().items()
            if isinstance(key, tuple)
        },
        "vic1": {
            "rows": len(vic),
            "expected_unique_intervals": len(expected),
            "observed_unique_intervals": len(observed),
            "missing_intervals": [t.isoformat() for t in missing],
            "unexpected_intervals": [t.isoformat() for t in unexpected],
            "spacing_minutes_counts": {
                str(int(pd.Timedelta(str(k)).total_seconds() // 60)): int(v)
                for k, v in spacing.value_counts().items()
            },
            "intervals_by_market_date": {
                str(k): int(v) for k, v in base.groupby("market_date").size().items()
            },
            "runno_values": sorted(int(v) for v in vic["runno"].dropna().unique()),
            "intervention_counts": {
                str(k): int(v) for k, v in vic["intervention"].value_counts().items()
            },
            "pk_duplicates": int(vic["q_pk_duplicate"].sum()),
            "settlementdate_region_duplicates": int(
                vic.duplicated(["settlementdate_source", "region_id"], keep=False).sum()
            ),
            "intervention_pair_rows": len(pairs),
            "intervention_pair_max_abs_totaldemand_diff_mw": (
                round(float((pair_diff[1] - pair_diff[0]).abs().max()), 5)
                if {0, 1} <= set(pair_diff.columns)
                else None
            ),
            "settlementdate_unparsed": int(vic["q_settlementdate_unparsed"].sum()),
            "dispatchinterval_mismatches": int(vic["q_dispatchinterval_mismatch"].sum()),
            "totaldemand_null": int(vic["q_demand_null"].sum()),
            "totaldemand_nonfinite": int(vic["q_demand_nonfinite"].sum()),
            "totaldemand_nonpositive": int(vic["q_demand_nonpositive"].sum()),
            "totaldemand_mw_stats": _quantiles(vic["total_demand_mw"]),
            "largest_5min_changes_mw_intervention0": [
                {
                    "settlementdate_end": pd.Timestamp(str(t)).isoformat(),
                    "abs_change_mw": round(float(v), 3),
                }
                for t, v in top_moves.items()
            ],
            "lastchanged_minus_settlementdate_minutes": _quantiles(lag_minutes),
            "lastchanged_before_settlementdate": int((lag_minutes < 0).sum()),
            "hourly_record_counts_by_intervention": {
                str(i): {
                    str(int(k)): int(v)
                    for k, v in hourly.loc[i].value_counts().sort_index().items()
                }
                for i in hourly.index.get_level_values(0).unique()
            },
            "first_canonical_hour_label": first_hour.isoformat(),
            "first_canonical_hour_member_settlementdates": first_hour_members,
        },
    }
    return {
        "audit_type": "aemo_dispatchregionsum_raw_audit",
        "source": source
        | {
            "member": table.member,
            "report_type": table.report_type,
            "report_subtype": table.report_subtype,
            "report_version": table.report_version,
            "control_comments": table.comments,
        },
        "columns": column_profile(table),
        "file_level": file_level,
        "window": window,
    }
