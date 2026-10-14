"""Audit of a complete historical DISPATCHREGIONSUM snapshot (Phase 1B).

Reports facts only: schema versions and changes, counts by month and region,
coverage and spacing, nulls/ranges, duplicate keys, intervention groups and
physical-versus-pricing ``TOTALDEMAND`` differences, and ``LASTCHANGED``
descriptives. It applies the D-06 selection rule only to *count* outcomes; no
aggregation is performed (Phase 2).
"""

from __future__ import annotations

import datetime as dt
import itertools
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from nem_forecast.contract import DISPATCH_INTERVAL, NEM_MARKET_TZ, settlementdate_bounds
from nem_forecast.ingestion.aemo_csv import read_aemo_zip
from nem_forecast.ingestion.aemo_normalise import (
    PHYSICAL,
    QUARANTINED,
    add_quality_flags,
    classify_primary_records,
    typed_frame,
)
from nem_forecast.provenance import sha256_file

logger = logging.getLogger(__name__)

SLIM = [
    "settlementdate_source",
    "interval_end_market",
    "region_id",
    "runno",
    "intervention",
    "dispatchinterval",
    "total_demand_mw",
    "lastchanged_market",
]
_LIST_LIMIT = 25


def _month_bounds(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    year, mon = (int(x) for x in month.split("-"))
    start = dt.date(year, mon, 1)
    nxt = dt.date(year + (mon == 12), mon % 12 + 1, 1)
    first, last = settlementdate_bounds(start, nxt)
    return pd.Timestamp(first), pd.Timestamp(last)


def _stats(series: pd.Series) -> dict[str, float]:
    clean = series.dropna()
    if clean.empty:
        return {}
    levels = [0.0, 0.001, 0.5, 0.999, 1.0]
    values = clean.quantile(levels).to_numpy(dtype=float)
    out = {f"q{lv * 100:g}": round(float(v), 5) for lv, v in zip(levels, values, strict=True)}
    return out | {"mean": round(float(clean.mean()), 5), "n": len(clean)}


def _counts(series: pd.Series) -> dict[str, int]:
    return {str(k): int(v) for k, v in series.value_counts(dropna=False).sort_index().items()}


def _file_audit(path: Path, record: dict[str, Any]) -> tuple[dict[str, Any], pd.DataFrame]:
    table = read_aemo_zip(path)
    typed = typed_frame(table)
    month = record["archive_month"]
    first, last = _month_bounds(month)
    end = typed["interval_end_market"]
    end_of_report = [c for c in table.comments if len(c) > 2 and c[1] == "END OF REPORT"]
    demand = typed["total_demand_mw"]
    stats = {
        "month": month,
        "file": record["path"],
        "report_version": table.report_version,
        "n_columns": len(table.columns),
        "rows": len(typed),
        "end_of_report_declared": int(end_of_report[0][2]) if end_of_report else None,
        "end_of_report_matches_line_count": bool(
            end_of_report and int(end_of_report[0][2]) == len(typed) + len(table.comments) + 1
        ),
        "settlementdate_min": str(end.min()),
        "settlementdate_max": str(end.max()),
        "rows_outside_month_support": int(((end < first) | (end > last)).sum()),
        "unparsed_settlementdate": int(end.isna().sum()),
        "rows_by_region": _counts(typed["region_id"]),
        "rows_by_intervention": _counts(typed["intervention"]),
        "runno_values": sorted(int(v) for v in typed["runno"].dropna().unique()),
        "totaldemand_null": int(demand.isna().sum()),
        "totaldemand_nonpositive": int((demand <= 0).sum()),
        "control_header": table.comments[0] if table.comments else None,
    }
    slim = typed[SLIM].copy()
    slim["archive_month"] = month
    return stats | {"_columns": table.columns}, slim


def _schema_changes(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    changes = []
    for prev, cur in itertools.pairwise(files):
        added = [c for c in cur["_columns"] if c not in prev["_columns"]]
        removed = [c for c in prev["_columns"] if c not in cur["_columns"]]
        reordered = not added and not removed and cur["_columns"] != prev["_columns"]
        if added or removed or reordered or cur["report_version"] != prev["report_version"]:
            changes.append(
                {
                    "from_month": prev["month"],
                    "to_month": cur["month"],
                    "report_version": [prev["report_version"], cur["report_version"]],
                    "added_columns": added,
                    "removed_columns": removed,
                    "column_order_changed": reordered,
                }
            )
    return changes


def _episodes(ends: pd.Series) -> list[dict[str, Any]]:
    """Group sorted interval ends into contiguous 5-minute runs."""
    if ends.empty:
        return []
    ordered = ends.sort_values().reset_index(drop=True)
    breaks = ordered.diff() != DISPATCH_INTERVAL
    episode = breaks.cumsum()
    out = []
    for _, group in ordered.groupby(episode):
        out.append(
            {
                "first_end": group.iloc[0].isoformat(),
                "last_end": group.iloc[-1].isoformat(),
                "intervals": len(group),
            }
        )
    return out


def audit_snapshot(
    root: Path, snapshot: dict[str, Any]
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    """Return (report, intervention_rows, quarantined_rows) for a complete snapshot."""
    files, slims = [], []
    for record in snapshot["files"]:
        path = root / record["path"]
        if sha256_file(path) != record["sha256"]:
            raise ValueError(f"hash inconsistency for {path}")
        stats, slim = _file_audit(path, record)
        files.append(stats)
        slims.append(slim)
        logger.info("audited %s (%d rows)", record["archive_month"], stats["rows"])
    data = pd.concat(slims, ignore_index=True)
    del slims
    flagged = classify_primary_records(add_quality_flags(data))

    first_month = files[0]["month"]
    last_month = files[-1]["month"]
    expected = pd.date_range(
        _month_bounds(first_month)[0],
        _month_bounds(last_month)[1],
        freq=DISPATCH_INTERVAL,
        tz=NEM_MARKET_TZ,
    )
    coverage: dict[str, Any] = {}
    for region, group in flagged.groupby("region_id"):
        observed = pd.DatetimeIndex(group["interval_end_market"].dropna().unique()).sort_values()
        missing = expected.difference(observed)
        spacing = observed.to_series().diff().dropna()
        primary = group[group["is_primary_target_record"]]
        per_day = primary.groupby(
            (primary["interval_end_market"] - DISPATCH_INTERVAL).dt.date
        ).size()
        coverage[str(region)] = {
            "expected_intervals": len(expected),
            "observed_unique_intervals": len(observed),
            "missing_intervals": len(missing),
            "missing_examples": [t.isoformat() for t in missing[:_LIST_LIMIT]],
            "unexpected_intervals": len(observed.difference(expected)),
            "first_end": observed.min().isoformat() if len(observed) else None,
            "last_end": observed.max().isoformat() if len(observed) else None,
            "spacing_minutes_counts": {
                str(int(pd.Timedelta(str(k)).total_seconds() // 60)): int(v)
                for k, v in spacing.value_counts().items()
            },
            "market_days_not_288_primary": {
                str(k): int(v) for k, v in per_day[per_day != 288].items()
            },
            "primary_records": len(primary),
            "totaldemand_primary_mw": _stats(primary["total_demand_mw"]),
            "totaldemand_null_all_rows": int(group["q_demand_null"].sum()),
            "totaldemand_nonfinite_all_rows": int(group["q_demand_nonfinite"].sum()),
            "totaldemand_nonpositive_all_rows": int(group["q_demand_nonpositive"].sum()),
            "dispatchinterval_mismatches": int(group["q_dispatchinterval_mismatch"].sum()),
            "pk_duplicate_rows": int(group["q_pk_duplicate"].sum()),
            "selection_status_counts": _counts(group["selection_status"]),
        }

    pairs = flagged[flagged["selection_status"] == PHYSICAL]
    intervention: dict[str, Any] = {
        "definition": "INTERVENTION=1 physical (base case target/outturn) run; INTERVENTION=0 "
        "pricing (what-if) run; AEMO Intervention Pricing Methodology v2.3 s3.3",
        "group_size_counts": _counts(
            flagged.drop_duplicates(["settlementdate_source", "region_id"])["group_size"]
        ),
        "by_region": {},
    }
    for region, group in pairs.groupby("region_id"):
        diff = group["physical_minus_pricing_mw"]
        nonzero = diff[diff.abs() > 1e-6]
        months = group["archive_month"].value_counts().sort_index()
        intervention["by_region"][str(region)] = {
            "intervention_intervals": len(group),
            "intervals_by_month": {str(k): int(v) for k, v in months.items()},
            "intervals_with_nonzero_diff": len(nonzero),
            "physical_minus_pricing_mw": _stats(diff),
            "abs_diff_mw": _stats(diff.abs()),
            "episodes": len(_episodes(group["interval_end_market"])),
        }
    vic_pairs = pairs[pairs["region_id"] == "VIC1"]
    intervention["vic1_episodes"] = _episodes(vic_pairs["interval_end_market"])
    top = vic_pairs.assign(abs_diff=vic_pairs["physical_minus_pricing_mw"].abs()).nlargest(
        10, "abs_diff"
    )
    intervention["vic1_largest_abs_diffs"] = [
        {"settlementdate": str(sd), "physical_minus_pricing_mw": round(float(d), 5)}
        for sd, d in zip(
            top["settlementdate_source"], top["physical_minus_pricing_mw"], strict=True
        )
    ]

    quarantined = flagged[flagged["selection_status"] == QUARANTINED]
    lag = (flagged["lastchanged_market"] - flagged["interval_end_market"]).dt.total_seconds() / 60
    vic_mask = flagged["region_id"] == "VIC1"
    years = flagged.loc[vic_mask, "interval_end_market"].dt.year
    report = {
        "audit_type": "aemo_dispatchregionsum_historical_snapshot_audit",
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_description": snapshot.get("description"),
        "file_count": len(files),
        "total_bytes": snapshot["total_bytes"],
        "rows_total": len(flagged),
        "rows_by_region": _counts(flagged["region_id"]),
        "settlementdate_support": [expected[0].isoformat(), expected[-1].isoformat()],
        "files": [{k: v for k, v in f.items() if k != "_columns"} for f in files],
        "report_versions": _counts(pd.Series([f["report_version"] for f in files])),
        "column_counts": _counts(pd.Series([f["n_columns"] for f in files])),
        "schema_changes": _schema_changes(files),
        "cross_file_pk_duplicate_rows": int(flagged["q_pk_duplicate"].sum()),
        "coverage_by_region": coverage,
        "intervention": intervention,
        "quarantined_rows": len(quarantined),
        "quarantined_by_region": _counts(quarantined["region_id"]),
        "quarantined_examples": quarantined.head(_LIST_LIMIT)[
            [
                "settlementdate_source",
                "region_id",
                "runno",
                "intervention",
                "total_demand_mw",
                "group_size",
            ]
        ]
        .astype(str)
        .to_dict("records"),
        "lastchanged_minus_settlementdate_minutes_vic1_by_year": {
            str(int(y)): _stats(lag[vic_mask][years == y]) for y in sorted(years.unique())
        },
        "lastchanged_after_interval_end_vic1": int((lag[vic_mask] > 0).sum()),
    }
    keep = [
        "settlementdate_source",
        "region_id",
        "runno",
        "intervention",
        "total_demand_mw",
        "lastchanged_market",
        "selection_status",
        "physical_minus_pricing_mw",
        "archive_month",
    ]
    intervention_rows = flagged[flagged["group_size"] > 1][keep].reset_index(drop=True)
    return report, intervention_rows, quarantined[keep].reset_index(drop=True)
