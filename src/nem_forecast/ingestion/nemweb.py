"""AEMO NEMWeb MMSDM monthly-archive adapter.

The MMSDM archive publishes one directory per month::

    {base}/{YYYY}/MMSDM_{YYYY}_{MM}/MMSDM_Historical_Data_SQLLoader/DATA/

containing the table's zipped AEMO-CSV file(s). The file naming changed in
2024-08 from ``PUBLIC_DVD_<TABLE>_<YYYYMM>010000.zip`` to
``PUBLIC_ARCHIVE#<TABLE>#FILE<NN>#<YYYYMM>010000.zip``, so files are resolved
from the official directory listing rather than from a hard-coded name.

Raw responses are written once, never overwritten, hashed (SHA-256), and
described by a JSON manifest next to them.
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
import re
import time
import urllib.parse
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx

from nem_forecast.config import AemoSourceConfig
from nem_forecast.contract import DISPATCH_INTERVAL, settlementdate_bounds
from nem_forecast.paths import ProjectPaths
from nem_forecast.provenance import read_json, sha256_file, utc_now_iso, write_json_atomic

logger = logging.getLogger(__name__)

_LISTING_ROW = re.compile(
    r"(?P<stamp>\w+day, \w+ \d{1,2}, \d{4} +\d{1,2}:\d{2} [AP]M) +(?P<size>\d+) +"
    r'<A HREF="(?P<href>[^"]+)">(?P<name>[^<]+)</A>',
    re.IGNORECASE,
)


class DownloadRefusedError(RuntimeError):
    """Raised when a download would violate an owner gate or overwrite raw data."""


class ArchiveValidationError(RuntimeError):
    """Raised for an invalid archive, unsupported schema or hash inconsistency."""


class SourceResolutionError(RuntimeError):
    """Raised when the official listing does not contain exactly the expected table file(s)."""


@dataclass(frozen=True)
class ListedFile:
    name: str
    url: str
    listed_bytes: int
    listed_timestamp: str


@dataclass(frozen=True)
class PlannedFile:
    year: int
    month: int
    listing_url: str
    name: str
    url: str
    listed_bytes: int
    listed_timestamp: str
    destination: Path


def month_listing_url(base_url: str, year: int, month: int) -> str:
    return (
        f"{base_url}/{year:04d}/MMSDM_{year:04d}_{month:02d}/MMSDM_Historical_Data_SQLLoader/DATA/"
    )


def table_file_pattern(table: str, year: int, month: int) -> re.Pattern[str]:
    stamp = f"{year:04d}{month:02d}010000"
    return re.compile(
        rf"^(PUBLIC_DVD_{table}_{stamp}\.zip|PUBLIC_ARCHIVE#{table}#FILE\d{{2}}#{stamp}\.zip)$"
    )


def parse_listing(html: str, listing_url: str) -> list[ListedFile]:
    """Parse an NEMWeb (IIS-style) directory listing into files with sizes and timestamps."""
    files = []
    for match in _LISTING_ROW.finditer(html.replace("<br>", "\n")):
        href = match.group("href")
        files.append(
            ListedFile(
                name=match.group("name"),
                url=urllib.parse.urljoin(
                    listing_url, urllib.parse.quote(urllib.parse.unquote(href), safe="/:")
                ),
                listed_bytes=int(match.group("size")),
                listed_timestamp=match.group("stamp"),
            )
        )
    return files


def months_for_window(start_date: dt.date, end_date: dt.date) -> list[tuple[int, int]]:
    """Calendar months whose archives may hold the window's interval-ending records.

    Records are selected by ``SETTLEMENTDATE`` (interval end), from start 00:05
    to end 00:00 market time. A record ending exactly at 00:00 on the 1st may
    sit in either neighbouring month's archive; the audit's completeness check
    exposes any such gap rather than guessing.
    """
    first_end, last_end = settlementdate_bounds(start_date, end_date)
    first = (first_end - DISPATCH_INTERVAL).date()
    last = (last_end - DISPATCH_INTERVAL).date()
    months = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def raw_table_dir(paths: ProjectPaths, table: str) -> Path:
    return paths.data_raw / "aemo" / "mmsdm" / table


def check_window_allowed(
    cfg: AemoSourceConfig, start_date: dt.date, end_date: dt.date, owner_approved: bool
) -> None:
    days = (end_date - start_date).days
    if days <= 0:
        raise DownloadRefusedError("window end_date must be after start_date")
    if days > cfg.max_window_days_without_owner_approval and not owner_approved:
        raise DownloadRefusedError(
            f"window of {days} days exceeds {cfg.max_window_days_without_owner_approval} days; "
            "multi-year/large downloads require explicit owner approval "
            "(--owner-approved-large-download)"
        )


Fetcher = Callable[[str], httpx.Response]


def make_client(cfg: AemoSourceConfig) -> httpx.Client:
    return httpx.Client(
        timeout=cfg.http.timeout_seconds,
        headers={"User-Agent": cfg.http.user_agent},
        follow_redirects=False,
    )


def fetch_with_retries(client: httpx.Client, url: str, cfg: AemoSourceConfig) -> httpx.Response:
    """GET with bounded retries on transport errors and 5xx; 4xx fails immediately."""
    last_error: Exception | None = None
    for attempt in range(1, cfg.http.max_attempts + 1):
        try:
            response = client.get(url)
            if response.status_code < 500:
                response.raise_for_status()
                return response
            last_error = httpx.HTTPStatusError(
                f"server error {response.status_code}", request=response.request, response=response
            )
        except httpx.TransportError as exc:
            last_error = exc
        logger.warning(
            "attempt %d/%d failed for %s: %s", attempt, cfg.http.max_attempts, url, last_error
        )
        if attempt < cfg.http.max_attempts:
            time.sleep(cfg.http.backoff_seconds * attempt)
    assert last_error is not None
    raise last_error


def plan_download(
    cfg: AemoSourceConfig,
    paths: ProjectPaths,
    start_date: dt.date,
    end_date: dt.date,
    get_listing: Callable[[str], str],
) -> list[PlannedFile]:
    """Resolve the exact official files for a window from the monthly listings (no data fetch)."""
    planned = []
    for year, month in months_for_window(start_date, end_date):
        listing_url = month_listing_url(cfg.base_url, year, month)
        pattern = table_file_pattern(cfg.table, year, month)
        matches = [
            f for f in parse_listing(get_listing(listing_url), listing_url) if pattern.match(f.name)
        ]
        if not matches:
            raise SourceResolutionError(
                f"no {cfg.table} file for {year}-{month:02d} at {listing_url}"
            )
        for listed in matches:
            planned.append(
                PlannedFile(
                    year=year,
                    month=month,
                    listing_url=listing_url,
                    name=listed.name,
                    url=listed.url,
                    listed_bytes=listed.listed_bytes,
                    listed_timestamp=listed.listed_timestamp,
                    destination=raw_table_dir(paths, cfg.table) / f"{year:04d}" / listed.name,
                )
            )
    return planned


def plan_summary(
    planned: list[PlannedFile], start_date: dt.date, end_date: dt.date, root: Path
) -> dict[str, Any]:
    first_end, last_end = settlementdate_bounds(start_date, end_date)
    return {
        "window_market_dates_end_exclusive": [start_date.isoformat(), end_date.isoformat()],
        "settlementdate_range_market_time": [first_end.isoformat(), last_end.isoformat()],
        "request_count": {"listing": len({p.listing_url for p in planned}), "data": len(planned)},
        "estimated_download_bytes": sum(p.listed_bytes for p in planned),
        "files": [
            {
                "url": p.url,
                "listed_bytes": p.listed_bytes,
                "listed_timestamp": p.listed_timestamp,
                "destination": str(p.destination.relative_to(root)),
                "already_present": p.destination.exists(),
            }
            for p in planned
        ],
    }


def manifest_path(destination: Path) -> Path:
    return destination.parent / "manifests" / f"{destination.name}.manifest.json"


REQUIRED_SOURCE_COLUMNS = (
    "SETTLEMENTDATE",
    "RUNNO",
    "REGIONID",
    "DISPATCHINTERVAL",
    "INTERVENTION",
    "TOTALDEMAND",
    "LASTCHANGED",
)
_HEADER_LINES_TO_SCAN = 20


def inspect_archive(path: Path, table: str) -> dict[str, Any]:
    """Validate zip integrity and the AEMO header without loading the data rows.

    Raises :class:`ArchiveValidationError` for a corrupt archive, an unexpected
    member layout, or an unsupported schema (wrong report or missing columns).
    """
    report = {"DISPATCHREGIONSUM": ("DISPATCH", "REGIONSUM")}[table]
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            infos = archive.infolist()
            members = [i for i in infos if i.filename.upper().endswith(".CSV")]
            if bad is not None or len(infos) != 1 or len(members) != 1:
                raise ArchiveValidationError(
                    f"{path.name}: expected one intact CSV member, got "
                    f"{[i.filename for i in infos]} (bad member: {bad})"
                )
            with archive.open(members[0]) as raw:
                lines = [raw.readline().decode("utf-8") for _ in range(_HEADER_LINES_TO_SCAN)]
    except zipfile.BadZipFile as exc:
        raise ArchiveValidationError(f"{path.name}: not a valid zip archive: {exc}") from exc
    header = next((next(csv.reader([ln])) for ln in lines if ln.startswith("I,")), None)
    if header is None or tuple(header[1:3]) != report:
        raise ArchiveValidationError(
            f"{path.name}: unsupported schema header {header[:4] if header else None}"
        )
    columns = header[4:]
    missing = [c for c in REQUIRED_SOURCE_COLUMNS if c not in columns]
    if missing:
        raise ArchiveValidationError(f"{path.name}: unsupported schema, missing columns {missing}")
    info = members[0]
    return {
        "zip_members": [
            {
                "name": info.filename,
                "file_size": info.file_size,
                "crc": f"{info.CRC:08x}",
                "date_time": list(info.date_time),
            }
        ],
        "report_version": header[3],
        "columns": columns,
    }


def _stream_to_partial(
    client: httpx.Client, planned: PlannedFile, cfg: AemoSourceConfig, partial: Path
) -> tuple[dict[str, str | None], int, str, str]:
    """Stream a file to ``partial`` with bounded retries (transport errors and 5xx)."""
    last_error: Exception | None = None
    for attempt in range(1, cfg.http.max_attempts + 1):
        partial.unlink(missing_ok=True)
        started = utc_now_iso()
        try:
            with client.stream("GET", planned.url) as response:
                if response.status_code < 500:
                    response.raise_for_status()
                    with partial.open("xb") as handle:
                        for chunk in response.iter_bytes():
                            handle.write(chunk)
                    headers = {
                        k: response.headers.get(k)
                        for k in ("content-length", "content-type", "last-modified", "etag", "date")
                    }
                    return headers, response.status_code, started, utc_now_iso()
                last_error = httpx.HTTPStatusError(
                    f"server error {response.status_code}",
                    request=response.request,
                    response=response,
                )
        except httpx.TransportError as exc:
            last_error = exc
        logger.warning(
            "attempt %d/%d failed for %s: %s",
            attempt,
            cfg.http.max_attempts,
            planned.url,
            last_error,
        )
        if attempt < cfg.http.max_attempts:
            time.sleep(cfg.http.backoff_seconds * attempt)
    partial.unlink(missing_ok=True)
    assert last_error is not None
    raise last_error


def verify_manifested_file(dest: Path, manifest: Path) -> dict[str, Any]:
    recorded = read_json(manifest)
    if dest.stat().st_size != recorded["bytes"] or sha256_file(dest) != recorded["sha256"]:
        raise ArchiveValidationError(f"hash inconsistency: {dest} does not match its manifest")
    return recorded


def download_planned_file(
    planned: PlannedFile,
    client: httpx.Client,
    cfg: AemoSourceConfig,
    paths: ProjectPaths,
    window: tuple[dt.date, dt.date] | None = None,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Download one file with skip-if-valid and interruption-safe resume.

    Order of operations: stream to ``.partial`` -> validate size, zip and
    schema -> hash -> rename into place (read-only) -> write manifest
    atomically. A file is only "valid" when its manifest exists and matches.
    An orphan left by an interruption between rename and manifest is accepted
    only if a fresh download is byte-identical; otherwise the run stops.
    """
    dest, manifest = planned.destination, manifest_path(planned.destination)
    if dest.exists() and manifest.exists():
        recorded = verify_manifested_file(dest, manifest)
        logger.info("already present and verified: %s", dest)
        return {**recorded, "status": "already_present_verified"}
    if manifest.exists():
        raise ArchiveValidationError(f"manifest exists but file is missing: {dest}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    headers, status, started, completed = _stream_to_partial(client, planned, cfg, partial)
    size = partial.stat().st_size
    try:
        if headers["content-length"] is not None and int(headers["content-length"]) != size:
            raise ArchiveValidationError(f"truncated download for {planned.url}: {size} bytes")
        if size != planned.listed_bytes:
            raise ArchiveValidationError(
                f"{planned.name}: {size} bytes downloaded but listing says {planned.listed_bytes}"
            )
        inspected = inspect_archive(partial, cfg.table)
    except ArchiveValidationError:
        partial.unlink(missing_ok=True)
        raise
    digest = sha256_file(partial)
    recovered = dest.exists()
    if recovered:
        if sha256_file(dest) != digest:
            partial.unlink()
            raise ArchiveValidationError(
                f"hash inconsistency: unmanifested {dest} differs from a fresh download"
            )
        partial.unlink()
    else:
        partial.replace(dest)
    dest.chmod(0o444)
    record = {
        "source_id": cfg.source_id,
        "table": cfg.table,
        "source_url": planned.url,
        "listing_url": planned.listing_url,
        "listed_bytes": planned.listed_bytes,
        "listed_timestamp": planned.listed_timestamp,
        "requested_window_market_dates_end_exclusive": (
            [d.isoformat() for d in window] if window else None
        ),
        "snapshot_id": snapshot_id,
        "archive_month": f"{planned.year:04d}-{planned.month:02d}",
        "retrieval_started_utc": started,
        "retrieval_completed_utc": completed,
        "http_status": status,
        "response_headers": headers,
        "bytes": size,
        "sha256": digest,
        "path": str(dest.relative_to(paths.root)),
        "recovered_unmanifested_file": recovered,
        "user_agent": cfg.http.user_agent,
        **inspected,
    }
    write_json_atomic(manifest, record)
    manifest.chmod(0o444)
    return {**record, "status": "recovered_verified" if recovered else "downloaded"}


def save_listing(paths: ProjectPaths, table: str, listing_url: str, html: str) -> dict[str, Any]:
    """Retain a listing response as raw provenance (new file per retrieval, never overwritten)."""
    stamp = utc_now_iso().replace(":", "").replace("-", "")
    safe = re.sub(r"[^A-Za-z0-9]+", "_", listing_url.split("/MMSDM/")[-1]).strip("_")
    target = raw_table_dir(paths, table) / "listings" / f"{safe}__{stamp}.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as handle:
        handle.write(html)
    return {
        "listing_url": listing_url,
        "path": str(target.relative_to(paths.root)),
        "sha256": sha256_file(target),
        "bytes": target.stat().st_size,
    }


def planned_as_dict(planned: PlannedFile, root: Path) -> dict[str, Any]:
    data = asdict(planned)
    data["destination"] = str(planned.destination.relative_to(root))
    return data


def find_raw_archive(
    paths: ProjectPaths, table: str, year: int, month: int
) -> tuple[Path, dict[str, Any]]:
    """Return the single manifested raw archive for a month, verifying its SHA-256."""
    manifests = sorted((raw_table_dir(paths, table) / f"{year:04d}" / "manifests").glob("*.json"))
    matches = [
        m for m in manifests if read_json(m).get("archive_month") == f"{year:04d}-{month:02d}"
    ]
    if len(matches) != 1:
        raise SourceResolutionError(
            f"expected one manifested {table} archive for {year}-{month:02d}, found {len(matches)}"
        )
    record = read_json(matches[0])
    path = paths.root / record["path"]
    if sha256_file(path) != record["sha256"]:
        raise SourceResolutionError(f"{path} does not match its manifest hash")
    return path, record
