"""Historical MMSDM snapshot ingestion (Phase 1B).

A snapshot is the set of official monthly archives for a frozen month range,
retrieved now. It is a snapshot of historical records as published at
retrieval time, **not** a reconstruction of files as originally published.

Files are processed in bounded batches with skip-if-valid and resume
(:func:`nemweb.download_planned_file`). Progress and failure manifests are
written atomically. The final snapshot manifest (``status: complete``) is
written last, only after every file has been re-verified, so an interrupted
run can never look like a complete snapshot.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from nem_forecast.config import AemoSourceConfig
from nem_forecast.ingestion import nemweb
from nem_forecast.paths import ProjectPaths
from nem_forecast.provenance import read_json, utc_now_iso, write_json_atomic

logger = logging.getLogger(__name__)


def snapshot_id(cfg: AemoSourceConfig, retrieval_date: str) -> str:
    snap = cfg.historical_snapshot
    return f"{cfg.table}_{snap.start_month}_{snap.end_month}_retrieved{retrieval_date}"


def snapshots_dir(paths: ProjectPaths, table: str) -> Path:
    return nemweb.raw_table_dir(paths, table) / "snapshots"


def plan_snapshot(
    cfg: AemoSourceConfig, paths: ProjectPaths, get_listing: Callable[[str], str]
) -> list[nemweb.PlannedFile]:
    """Resolve exactly one official archive per month; stop on ambiguity or a missing month."""
    planned = []
    for year, month in cfg.historical_snapshot.months:
        listing_url = nemweb.month_listing_url(cfg.base_url, year, month)
        pattern = nemweb.table_file_pattern(cfg.table, year, month)
        matches = [
            f
            for f in nemweb.parse_listing(get_listing(listing_url), listing_url)
            if pattern.match(f.name)
        ]
        if not matches:
            raise nemweb.SourceResolutionError(f"missing month {year}-{month:02d}: no {cfg.table}")
        if len(matches) > 1:
            raise nemweb.SourceResolutionError(
                f"filename ambiguity for {year}-{month:02d}: {[m.name for m in matches]}"
            )
        listed = matches[0]
        planned.append(
            nemweb.PlannedFile(
                year=year,
                month=month,
                listing_url=listing_url,
                name=listed.name,
                url=listed.url,
                listed_bytes=listed.listed_bytes,
                listed_timestamp=listed.listed_timestamp,
                destination=nemweb.raw_table_dir(paths, cfg.table) / f"{year:04d}" / listed.name,
            )
        )
    return planned


def plan_document(
    cfg: AemoSourceConfig, planned: list[nemweb.PlannedFile], root: Path, snap_id: str
) -> dict[str, Any]:
    return {
        "snapshot_id": snap_id,
        "dry_run": True,
        "planned_utc": utc_now_iso(),
        "table": cfg.table,
        "months": [cfg.historical_snapshot.start_month, cfg.historical_snapshot.end_month],
        "month_count": len(cfg.historical_snapshot.months),
        "request_count": {"listing": len(planned), "data": len(planned)},
        "estimated_download_bytes": sum(p.listed_bytes for p in planned),
        "files": [
            {
                "month": f"{p.year:04d}-{p.month:02d}",
                "expected_filename": p.name,
                "url": p.url,
                "listed_bytes": p.listed_bytes,
                "listed_timestamp": p.listed_timestamp,
                "destination": str(p.destination.relative_to(root)),
                "already_present": p.destination.exists(),
            }
            for p in planned
        ],
    }


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "archive_month",
        "path",
        "bytes",
        "sha256",
        "source_url",
        "report_version",
        "retrieval_completed_utc",
        "listed_timestamp",
        "status",
    )
    return {k: record.get(k) for k in keys}


def run_snapshot(
    planned: list[nemweb.PlannedFile],
    client: httpx.Client,
    cfg: AemoSourceConfig,
    paths: ProjectPaths,
    snap_id: str,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    out_dir = snapshots_dir(paths, cfg.table)
    final = out_dir / f"{snap_id}.json"
    if final.exists():
        raise FileExistsError(f"snapshot {snap_id} already complete; snapshot IDs are never reused")
    progress = out_dir / f"{snap_id}.progress.json"
    started = utc_now_iso()
    results: list[dict[str, Any]] = []
    batch_size = cfg.historical_snapshot.batch_size
    try:
        for offset in range(0, len(planned), batch_size):
            for item in planned[offset : offset + batch_size]:
                results.append(
                    nemweb.download_planned_file(item, client, cfg, paths, snapshot_id=snap_id)
                )
            write_json_atomic(
                progress,
                {
                    "snapshot_id": snap_id,
                    "status": "in_progress",
                    "started_utc": started,
                    "updated_utc": utc_now_iso(),
                    "completed_files": len(results),
                    "planned_files": len(planned),
                },
                exclusive=False,
            )
            if offset + batch_size < len(planned):
                sleep(cfg.historical_snapshot.pause_seconds_between_batches)
        verified = []
        for item in planned:  # re-verify every file before declaring completeness
            record = nemweb.verify_manifested_file(
                item.destination, nemweb.manifest_path(item.destination)
            )
            verified.append(_summary({**record, "status": "verified"}))
    except Exception as exc:
        failure = out_dir / f"{snap_id}.failed.{utc_now_iso().replace(':', '')}.json"
        write_json_atomic(
            failure,
            {
                "snapshot_id": snap_id,
                "status": "failed",
                "started_utc": started,
                "failed_utc": utc_now_iso(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "completed_files": [_summary(r) for r in results],
                "planned_files": len(planned),
            },
        )
        logger.error("snapshot %s failed; failure manifest %s", snap_id, failure)
        raise
    statuses: dict[str, int] = {}
    for r in results:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    document = {
        "snapshot_id": snap_id,
        "status": "complete",
        "description": "2026 snapshot of historical records; not a reconstruction of files "
        "as originally published",
        "table": cfg.table,
        "months": [cfg.historical_snapshot.start_month, cfg.historical_snapshot.end_month],
        "started_utc": started,
        "completed_utc": utc_now_iso(),
        "file_count": len(verified),
        "total_bytes": sum(v["bytes"] for v in verified),
        "retrieval_status_counts": statuses,
        "files": verified,
    }
    write_json_atomic(final, document)
    final.chmod(0o444)
    write_json_atomic(
        progress,
        {**read_json(progress), "status": "complete", "updated_utc": utc_now_iso()},
        exclusive=False,
    )
    return document


def load_complete_snapshot(paths: ProjectPaths, table: str, snap_id: str) -> dict[str, Any]:
    path = snapshots_dir(paths, table) / f"{snap_id}.json"
    if not path.exists():
        raise nemweb.SourceResolutionError(f"no complete snapshot manifest {path.name}")
    document = read_json(path)
    if document.get("status") != "complete":
        raise nemweb.SourceResolutionError(f"snapshot {snap_id} is not complete")
    return document
