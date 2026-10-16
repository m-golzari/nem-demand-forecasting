"""Phase 2 pipeline: frozen snapshot -> canonical VIC1 hourly target, quality reports, manifest.

Outputs (all git-ignored; ``<run_id>`` is never reused):

* ``data/interim/aemo_vic1/<run_id>/intervals/year=YYYY/part-0.parquet``:
  every VIC1 source record with canonical timestamps and selection status;
* ``data/interim/aemo_vic1/<run_id>/canonical_hourly/year=YYYY/part-0.parquet``:
  every canonical hour, complete or not, with quality reasons;
* ``data/processed/vic1_hourly_target/<run_id>/model_ready/year=YYYY/part-0.parquet``:
  complete hours only;
* ``results/runs/<run_id>/``: ``quality_report.json``, ``quality_report.md``,
  ``reconciliation.json``, ``run_manifest.json`` (written last), or
  ``failure_manifest.json``.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from nem_forecast import __version__
from nem_forecast.config import AemoSourceConfig, ForecastConfig
from nem_forecast.contract import NEM_MARKET_TZ
from nem_forecast.ingestion import nemweb, snapshot
from nem_forecast.ingestion.aemo_csv import read_aemo_zip
from nem_forecast.ingestion.aemo_normalise import (
    PHYSICAL,
    PRICING,
    QUARANTINED,
    REGION,
    add_quality_flags,
    classify_primary_records,
    typed_frame,
)
from nem_forecast.paths import ProjectPaths
from nem_forecast.provenance import sha256_file, utc_now_iso, write_json_atomic
from nem_forecast.target.hourly import (
    EXPECTED_ENDPOINTS,
    REASONS,
    TARGET_COLUMN,
    TARGET_DESCRIPTION,
    build_hourly,
    model_ready,
)

logger = logging.getLogger(__name__)

TARGET_SEMANTICS = {
    "name": "Hourly mean of AEMO dispatch TOTALDEMAND estimates for VIC1",
    "column": TARGET_COLUMN,
    "unit": "MW",
    "description": TARGET_DESCRIPTION,
    "not": ["measured consumption", "metered load", "operational demand", "MWh"],
    "source": "AEMO NEMWeb MMSDM DISPATCHREGIONSUM.TOTALDEMAND",
    "endpoint_normalisation": "source support (H, H+1h] from interval-ending records "
    "H+00:05 ... H+01:00 -> canonical [H, H+1h) labelled H, NEM market time UTC+10",
    "selection": "one ordinary record retained; physical+pricing pair -> INTERVENTION=1 "
    "(physical); other combinations quarantined; never combined (D-06)",
    "aggregation": "arithmetic mean of 12 equal-duration 5-minute values (= duration-weighted)",
    "completeness": "all 12 expected endpoints exactly once after selection, finite values, "
    "VIC1 only, consistent DISPATCHINTERVAL, no quarantined record in the hour",
    "availability_buffer_applied": False,
}
INTERVAL_COLUMNS = [
    "source_file",
    "archive_month",
    "source_line",
    "settlementdate_source",
    "lastchanged_source",
    "totaldemand_source",
    "region_id",
    "runno",
    "intervention",
    "dispatchinterval",
    "total_demand_mw",
    "interval_start_market",
    "interval_end_market",
    "interval_start_utc",
    "interval_end_utc",
    "hour_start_market",
    "hour_start_utc",
    "selection_status",
    "is_primary_target_record",
    "physical_minus_pricing_mw",
    "q_pk_duplicate",
    "q_dispatchinterval_mismatch",
    "q_demand_null",
    "q_demand_nonfinite",
    "q_demand_nonpositive",
]


class Phase2CheckError(RuntimeError):
    """Raised when a derived reconciliation check fails (outputs are kept for inspection)."""


@dataclass(frozen=True)
class RunLayout:
    run_id: str
    run_dir: Path
    intervals: Path
    canonical: Path
    model_ready: Path

    @classmethod
    def create(cls, paths: ProjectPaths, run_id: str) -> RunLayout:
        layout = cls(
            run_id=run_id,
            run_dir=paths.results_runs / run_id,
            intervals=paths.data_interim / "aemo_vic1" / run_id / "intervals",
            canonical=paths.data_interim / "aemo_vic1" / run_id / "canonical_hourly",
            model_ready=paths.data_processed / "vic1_hourly_target" / run_id / "model_ready",
        )
        for directory in (layout.run_dir, layout.intervals, layout.canonical, layout.model_ready):
            if directory.exists():
                raise FileExistsError(f"{directory} exists; run IDs are never reused")
        layout.run_dir.mkdir(parents=True)
        return layout


def git_state(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
        ).stdout

    try:
        # porcelain v1 lines are "XY path"; never strip, the status columns may be blank
        lines = [line for line in run("status", "--porcelain").splitlines() if line]
        return {
            "commit": run("rev-parse", "HEAD").strip(),
            "dirty": bool(lines),
            "uncommitted_entries": len(lines),
            "uncommitted_paths": [line[3:] for line in lines],
        }
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"commit": None, "dirty": None, "error": str(exc)}


def environment() -> dict[str, Any]:
    import duckdb
    import numpy

    return {
        "python": sys.version.split()[0],
        "package_version": __version__,
        "pandas": pd.__version__,
        "numpy": numpy.__version__,
        "pyarrow": pa.__version__,
        "duckdb": duckdb.__version__,
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
    }


def load_vic1_records(
    archives: list[Path], lock: dict[str, Any]
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    frames, per_archive = [], []
    for path, entry in zip(archives, lock["archives"], strict=True):
        table = read_aemo_zip(path)
        if table.member != entry["archive_member"]:
            raise nemweb.ArchiveValidationError(
                f"{path.name}: member {table.member} differs from lock {entry['archive_member']}"
            )
        typed = typed_frame(table)
        vic = typed[typed["region_id"] == REGION].copy()
        if vic.empty:
            raise ValueError(f"{path.name}: no {REGION} records")
        vic["source_file"] = path.name
        vic["archive_month"] = entry["month"]
        per_archive.append(
            {
                "month": entry["month"],
                "filename": path.name,
                "report_version": table.report_version,
                "raw_rows": len(typed),
                "raw_rows_by_region": {
                    str(k): int(v) for k, v in typed["region_id"].value_counts().items()
                },
                "vic1_rows": len(vic),
            }
        )
        frames.append(vic)
        logger.info("loaded %s: %d raw rows, %d VIC1", entry["month"], len(typed), len(vic))
    records = classify_primary_records(add_quality_flags(pd.concat(frames, ignore_index=True)))
    return records, per_archive


def write_partitioned(
    frame: pd.DataFrame, base: Path, root: Path, year_column: pd.Series, metadata: dict[str, str]
) -> list[dict[str, Any]]:
    outputs = []
    for year in sorted({int(y) for y in year_column.tolist()}):
        part = frame[year_column == year]
        target = base / f"year={int(year)}" / "part-0.parquet"
        target.parent.mkdir(parents=True, exist_ok=False)
        table = pa.Table.from_pandas(part, preserve_index=False)
        existing = table.schema.metadata or {}
        table = table.replace_schema_metadata(
            {**existing, **{k.encode(): v.encode() for k, v in metadata.items()}}
        )
        pq.write_table(table, target, compression="zstd")
        target.chmod(0o444)
        outputs.append(
            {
                "path": str(target.relative_to(root)),
                "year": int(year),
                "rows": len(part),
                "sha256": sha256_file(target),
                "bytes": target.stat().st_size,
            }
        )
    return outputs


def _stats(series: pd.Series) -> dict[str, float]:
    clean = series.dropna()
    if clean.empty:
        return {"n": 0}
    q = clean.quantile([0.0, 0.01, 0.05, 0.5, 0.95, 0.99, 1.0]).to_numpy(dtype=float)
    keys = ["min", "p01", "p05", "median", "p95", "p99", "max"]
    return {
        "n": len(clean),
        "mean": round(float(clean.mean()), 4),
        "std": round(float(clean.std()), 4),
    } | {k: round(float(v), 4) for k, v in zip(keys, q, strict=True)}


def derived_checks(
    hourly: pd.DataFrame,
    ready: pd.DataFrame,
    rec: dict[str, Any],
    records: pd.DataFrame,
    first_hour: pd.Timestamp,
    last_hour: pd.Timestamp,
) -> list[dict[str, Any]]:
    """Checks whose expected values are derived from the configured window, never hard-coded."""
    in_range = hourly[hourly["in_canonical_range"]]
    complete = hourly[hourly["is_complete"]]
    checks = [
        ("hour_labels_unique", True, bool(hourly["hour_start_market"].is_unique)),
        ("hours_equal_grid", rec["hours_expected"], len(in_range)),
        ("no_hours_outside_range", 0, rec["hours_outside_canonical_range"]),
        (
            "first_canonical_hour",
            first_hour.isoformat(),
            in_range["hour_start_market"].min().isoformat(),
        ),
        (
            "last_canonical_hour",
            last_hour.isoformat(),
            in_range["hour_start_market"].max().isoformat(),
        ),
        ("records_sum_identity", True, rec["identity_records_sum"]),
        ("primary_sum_identity", True, rec["identity_primary_sum"]),
        ("all_records_vic1", True, bool((records["region_id"] == REGION).all())),
        ("no_unmapped_timestamps", 0, rec["records_unmapped_timestamp"]),
        (
            "primary_records_le_grid_endpoints",
            True,
            rec["primary_records"] <= rec["expected_primary_records_for_grid"],
        ),
        (
            "complete_hours_have_12_unique_endpoints",
            True,
            bool(
                (complete["n_unique_expected_endpoints"] == EXPECTED_ENDPOINTS).all()
                and (complete["n_primary_records"] == EXPECTED_ENDPOINTS).all()
            ),
        ),
        ("complete_hours_have_target", True, bool(complete[TARGET_COLUMN].notna().all())),
        (
            "incomplete_hours_have_no_target",
            True,
            bool(hourly.loc[~hourly["is_complete"], TARGET_COLUMN].isna().all()),
        ),
        (
            "model_ready_rows_equal_complete_in_range",
            int((hourly["is_complete"] & hourly["in_canonical_range"]).sum()),
            len(ready),
        ),
        (
            "never_both_runs_selected",
            0,
            int(
                records[records["is_primary_target_record"]]
                .duplicated(["settlementdate_source", "region_id"])
                .sum()
            ),
        ),
        (
            "pricing_records_never_primary",
            0,
            int(
                (
                    records["is_primary_target_record"] & (records["selection_status"] == PRICING)
                ).sum()
            ),
        ),
    ]
    return [{"check": n, "expected": e, "observed": o, "passed": e == o} for n, e, o in checks]


def yearly_summary(hourly: pd.DataFrame) -> dict[str, dict[str, Any]]:
    out = {}
    for year, group in hourly[hourly["in_canonical_range"]].groupby("year"):
        out[str(year)] = {
            "hours": len(group),
            "complete": int(group["is_complete"].sum()),
            "incomplete": int((~group["is_complete"]).sum()),
            "intervention_affected": int(group["intervention_affected"].sum()),
            "target_mw": _stats(group[TARGET_COLUMN]),
        }
    return out


def markdown_report(report: dict[str, Any]) -> str:
    rec, rows = report["reconciliation"], report["row_counts"]
    lines = [
        f"# Phase 2 quality report: {report['run_id']}",
        "",
        f"Target: **{TARGET_SEMANTICS['name']}** (`{TARGET_COLUMN}`, MW). "
        "Not measured consumption, operational demand or MWh.",
        "",
        f"Snapshot `{report['snapshot_id']}`; canonical hours "
        f"{report['coverage']['first_hour_market']} to {report['coverage']['last_hour_market']} "
        "(NEM market time, UTC+10).",
        "",
        "## Row reconciliation",
        "",
        "| Item | Count |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in rows.items()]
    lines += [
        "",
        "## Derived checks",
        "",
        "| Check | Expected | Observed | Passed |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {c['check']} | {c['expected']} | {c['observed']} | {c['passed']} |"
        for c in report["checks"]
    ]
    lines += ["", "## Quality reasons (hours)", "", "| Reason | Hours |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in rec["reason_counts"].items()]
    lines += [
        "",
        "## Yearly counts",
        "",
        "| Year | Hours | Complete | Incomplete | Intervention-affected "
        "| Mean MW | Min MW | Max MW |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for year, y in report["yearly"].items():
        t = y["target_mw"]
        lines.append(
            f"| {year} | {y['hours']} | {y['complete']} | {y['incomplete']} | "
            f"{y['intervention_affected']} | {t.get('mean')} | {t.get('min')} | {t.get('max')} |"
        )
    t = report["target_summary_mw"]
    lines += [
        "",
        "## Target summary (complete hours, MW)",
        "",
        "| " + " | ".join(t) + " |",
        "|" + "---|" * len(t),
        "| " + " | ".join(str(v) for v in t.values()) + " |",
        "",
        "## Warnings",
        "",
    ]
    lines += [f"- {w}" for w in report["warnings"]] or ["- none"]
    return "\n".join(lines) + "\n"


def run_phase2(
    paths: ProjectPaths,
    forecast_cfg: ForecastConfig,
    source_cfg: AemoSourceConfig,
    run_id: str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    snap = source_cfg.historical_snapshot
    if not snap.snapshot_id or not snap.lock_manifest:
        raise ValueError("historical_snapshot.snapshot_id and lock_manifest must be configured")
    lock_file = paths.root / snap.lock_manifest
    lock = snapshot.load_lock(lock_file)
    if lock["snapshot_id"] != snap.snapshot_id:
        raise ValueError("lock manifest snapshot_id does not match configuration")
    run_id = run_id or f"phase2_vic1_hourly_{utc_now_iso().replace(':', '').replace('-', '')}"
    layout = RunLayout.create(paths, run_id)
    created = utc_now_iso()
    try:
        archives = snapshot.verify_lock_against_local(paths, lock)
        records, per_archive = load_vic1_records(archives, lock)
        first_hour = pd.Timestamp(
            dt.datetime.combine(forecast_cfg.history.start_date, dt.time(0), tzinfo=NEM_MARKET_TZ)
        )
        if forecast_cfg.history.end_date is None:
            raise ValueError("history.end_date must be frozen for Phase 2")
        last_hour = pd.Timestamp(
            dt.datetime.combine(forecast_cfg.history.end_date, dt.time(23), tzinfo=NEM_MARKET_TZ)
        )
        hourly, rec = build_hourly(records, first_hour, last_hour)
        ready = model_ready(hourly)
        checks = derived_checks(hourly, ready, rec, records, first_hour, last_hour)

        metadata = {
            "nem_forecast.run_id": run_id,
            "nem_forecast.snapshot_id": snap.snapshot_id,
            "nem_forecast.target": TARGET_DESCRIPTION,
        }
        outputs = {
            "intervals": write_partitioned(
                records[INTERVAL_COLUMNS],
                layout.intervals,
                paths.root,
                records["hour_start_market"].dt.year,
                metadata,
            ),
            "canonical_hourly": write_partitioned(
                hourly, layout.canonical, paths.root, hourly["year"], metadata
            ),
            "model_ready": write_partitioned(
                ready, layout.model_ready, paths.root, ready["year"], metadata
            ),
        }
        status = records["selection_status"]
        warnings = []
        if rec["hours_incomplete_or_invalid"]:
            warnings.append(
                f"{rec['hours_incomplete_or_invalid']} incomplete/invalid hours "
                "(kept in canonical_hourly, excluded from model_ready)"
            )
        if int((records["total_demand_mw"] <= 0).sum()):
            warnings.append("non-positive VIC1 TOTALDEMAND values present")
        if int(records["lastchanged_market"].isna().sum()):
            warnings.append("unparsable LASTCHANGED values present")
        row_counts = {
            "raw_rows_all_regions": sum(a["raw_rows"] for a in per_archive),
            "raw_vic1_records": len(records),
            "selected_primary_records": int(records["is_primary_target_record"].sum()),
            "unselected_records": int((~records["is_primary_target_record"]).sum()),
            "selected_ordinary_single": int((status == "ordinary_single").sum()),
            "selected_physical": int((status == PHYSICAL).sum()),
            "unselected_pricing": int((status == PRICING).sum()),
            "quarantined_records": int((status == QUARANTINED).sum()),
            "hourly_records": len(hourly),
            "complete_hours": rec["hours_complete"],
            "incomplete_or_invalid_hours": rec["hours_incomplete_or_invalid"],
            "intervention_affected_hours": rec["hours_intervention_affected"],
            "model_ready_rows": len(ready),
        }
        coverage = {
            "first_hour_market": first_hour.isoformat(),
            "last_hour_market": last_hour.isoformat(),
            "first_hour_utc": first_hour.tz_convert("UTC").isoformat(),
            "last_hour_utc": last_hour.tz_convert("UTC").isoformat(),
            "first_source_settlementdate": str(records["settlementdate_source"].min()),
            "last_source_settlementdate": str(records["settlementdate_source"].max()),
        }
        report = {
            "run_id": run_id,
            "snapshot_id": snap.snapshot_id,
            "target": TARGET_SEMANTICS,
            "coverage": coverage,
            "row_counts": row_counts,
            "reconciliation": rec,
            "checks": checks,
            "all_checks_passed": all(c["passed"] for c in checks),
            "yearly": yearly_summary(hourly),
            "target_summary_mw": _stats(ready[TARGET_COLUMN]),
            "intervention_hours": {
                "count": rec["hours_intervention_affected"],
                "max_abs_physical_minus_pricing_mw": round(
                    float(hourly["max_abs_physical_minus_pricing_mw"].max(skipna=True)), 5
                ),
                "hours_with_nonzero_difference": int(
                    (hourly["max_abs_physical_minus_pricing_mw"] > 1e-6).sum()
                ),
            },
            "incomplete_hours_sample": hourly.loc[
                ~hourly["is_complete"], ["hour_start_market", "quality_reasons"]
            ]
            .head(200)
            .astype(str)
            .to_dict("records"),
            "per_archive": per_archive,
            "warnings": warnings,
        }
        (layout.run_dir / "quality_report.json").write_text(
            json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
        )
        (layout.run_dir / "quality_report.md").write_text(markdown_report(report), encoding="utf-8")
        (layout.run_dir / "reconciliation.json").write_text(
            json.dumps(
                {"row_counts": row_counts, "reconciliation": rec, "checks": checks},
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        manifest = {
            "run_id": run_id,
            "phase": "2",
            "status": "complete" if report["all_checks_passed"] else "failed_checks",
            "created_utc": created,
            "completed_utc": utc_now_iso(),
            "runtime_seconds": round(time.monotonic() - started, 2),
            "snapshot_id": snap.snapshot_id,
            "lock_manifest": {"path": snap.lock_manifest, "sha256": sha256_file(lock_file)},
            "inputs": [
                {k: a[k] for k in ("month", "filename", "bytes", "sha256", "url")}
                for a in lock["archives"]
            ],
            "code": git_state(paths.root),
            "environment": environment(),
            "resolved_config": {
                "forecast": forecast_cfg.model_dump(mode="json"),
                "aemo_source": source_cfg.model_dump(mode="json"),
            },
            "target": TARGET_SEMANTICS,
            "row_counts": row_counts,
            "coverage": coverage,
            "quality": {
                "all_checks_passed": report["all_checks_passed"],
                "reason_counts": rec["reason_counts"],
                "warnings": warnings,
            },
            "outputs": outputs,
            "reports": {
                name: {
                    "path": str((layout.run_dir / name).relative_to(paths.root)),
                    "sha256": sha256_file(layout.run_dir / name),
                }
                for name in ("quality_report.json", "quality_report.md", "reconciliation.json")
            },
        }
        write_json_atomic(layout.run_dir / "run_manifest.json", manifest)
    except Exception as exc:
        write_json_atomic(
            layout.run_dir / "failure_manifest.json",
            {
                "run_id": run_id,
                "status": "failed",
                "created_utc": created,
                "failed_utc": utc_now_iso(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "code": git_state(paths.root),
            },
        )
        raise
    if manifest["status"] != "complete":
        failed = [c["check"] for c in checks if not c["passed"]]
        raise Phase2CheckError(f"derived checks failed: {failed} (see {layout.run_dir})")
    return manifest


__all__ = ["REASONS", "Phase2CheckError", "run_phase2"]
