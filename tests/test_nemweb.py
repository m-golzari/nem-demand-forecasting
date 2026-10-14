import datetime as dt
import hashlib
import json
import stat

import httpx
import pytest

from nem_forecast.config import load_aemo_source_config
from nem_forecast.ingestion import nemweb
from nem_forecast.paths import ProjectPaths
from tests.aemo_fixtures import (
    LISTING_HTML,
    LISTING_HTML_ARCHIVE_FORMAT,
    dvd_name,
    full_day_rows,
    listing_for,
    render,
    zip_bytes,
)
from tests.conftest import REPO_ROOT

BASE = "https://nemweb.com.au/Data_Archive/Wholesale_Electricity/MMSDM"


@pytest.fixture
def aemo_project(tmp_project: ProjectPaths) -> ProjectPaths:
    (tmp_project.configs / "aemo_source.yaml").write_text(
        (REPO_ROOT / "configs" / "aemo_source.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_project


def test_source_config_valid() -> None:
    cfg = load_aemo_source_config()
    assert cfg.table == "DISPATCHREGIONSUM"
    assert cfg.approved_window.days == 7
    assert (cfg.approved_window.start_date, cfg.approved_window.end_date) == (
        dt.date(2024, 4, 4),
        dt.date(2024, 4, 11),
    )


def test_parse_listing_and_pattern() -> None:
    url = nemweb.month_listing_url(BASE, 2024, 4)
    files = nemweb.parse_listing(LISTING_HTML, url)
    assert [f.listed_bytes for f in files] == [4571235, 777187]
    pattern = nemweb.table_file_pattern("DISPATCHREGIONSUM", 2024, 4)
    matched = [f for f in files if pattern.match(f.name)]
    assert len(matched) == 1  # PREDISPATCHREGIONSUM must not match
    assert matched[0].url == url + "PUBLIC_DVD_DISPATCHREGIONSUM_202404010000.zip"


def test_archive_format_names_are_percent_encoded() -> None:
    url = nemweb.month_listing_url(BASE, 2024, 8)
    files = nemweb.parse_listing(LISTING_HTML_ARCHIVE_FORMAT, url)
    assert nemweb.table_file_pattern("DISPATCHREGIONSUM", 2024, 8).match(files[0].name)
    assert files[0].url.endswith("PUBLIC_ARCHIVE%23DISPATCHREGIONSUM%23FILE01%23202408010000.zip")


@pytest.mark.parametrize(
    ("start", "end", "months"),
    [
        (dt.date(2024, 4, 4), dt.date(2024, 4, 11), [(2024, 4)]),
        (dt.date(2024, 4, 25), dt.date(2024, 5, 1), [(2024, 4)]),  # ends 05-01 00:00
        (dt.date(2024, 4, 25), dt.date(2024, 5, 2), [(2024, 4), (2024, 5)]),
        (dt.date(2023, 12, 30), dt.date(2024, 1, 2), [(2023, 12), (2024, 1)]),
    ],
)
def test_months_for_window(start: dt.date, end: dt.date, months: list[tuple[int, int]]) -> None:
    assert nemweb.months_for_window(start, end) == months


def test_large_window_refused_without_owner_flag() -> None:
    cfg = load_aemo_source_config()
    with pytest.raises(nemweb.DownloadRefusedError, match="owner approval"):
        nemweb.check_window_allowed(cfg, dt.date(2022, 1, 1), dt.date(2026, 10, 1), False)
    nemweb.check_window_allowed(cfg, dt.date(2022, 1, 1), dt.date(2026, 10, 1), True)
    with pytest.raises(nemweb.DownloadRefusedError):
        nemweb.check_window_allowed(cfg, dt.date(2024, 4, 4), dt.date(2024, 4, 4), False)


def test_plan_is_offline_with_injected_listing(aemo_project: ProjectPaths) -> None:
    cfg = load_aemo_source_config(paths=aemo_project)
    requested: list[str] = []

    def listing(url: str) -> str:
        requested.append(url)
        return LISTING_HTML

    planned = nemweb.plan_download(
        cfg, aemo_project, dt.date(2024, 4, 4), dt.date(2024, 4, 11), listing
    )
    summary = nemweb.plan_summary(
        planned, dt.date(2024, 4, 4), dt.date(2024, 4, 11), aemo_project.root
    )
    assert requested == [nemweb.month_listing_url(BASE, 2024, 4)]
    assert summary["request_count"] == {"listing": 1, "data": 1}
    assert summary["estimated_download_bytes"] == 4571235
    assert summary["files"][0]["destination"] == (
        "data/raw/aemo/mmsdm/DISPATCHREGIONSUM/2024/PUBLIC_DVD_DISPATCHREGIONSUM_202404010000.zip"
    )
    assert not (aemo_project.root / "data").exists()  # dry-run planning writes nothing


def test_plan_fails_when_table_missing(aemo_project: ProjectPaths) -> None:
    cfg = load_aemo_source_config(paths=aemo_project)
    with pytest.raises(nemweb.SourceResolutionError):
        nemweb.plan_download(
            cfg, aemo_project, dt.date(2024, 4, 4), dt.date(2024, 4, 11), lambda _: "<pre></pre>"
        )


def _client(payload: bytes, headers: dict[str, str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers=headers or {})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _payload() -> bytes:
    return zip_bytes(render(full_day_rows(dt.date(2024, 4, 4))))


def _planned(project: ProjectPaths, size: int) -> nemweb.PlannedFile:
    cfg = load_aemo_source_config(paths=project)
    html = listing_for(2024, 4, [(dvd_name(2024, 4), size)])
    return nemweb.plan_download(
        cfg, project, dt.date(2024, 4, 4), dt.date(2024, 4, 11), lambda _: html
    )[0]


WINDOW = (dt.date(2024, 4, 4), dt.date(2024, 4, 11))


def test_download_writes_hash_manifest_and_never_overwrites(aemo_project: ProjectPaths) -> None:
    cfg = load_aemo_source_config(paths=aemo_project)
    payload = _payload()
    planned = _planned(aemo_project, len(payload))
    result = nemweb.download_planned_file(planned, _client(payload), cfg, aemo_project, WINDOW)
    assert result["status"] == "downloaded"
    assert planned.destination.read_bytes() == payload
    manifest_file = nemweb.manifest_path(planned.destination)
    manifest = json.loads(manifest_file.read_text())
    assert manifest["bytes"] == len(payload)
    assert manifest["sha256"] == hashlib.sha256(payload).hexdigest()
    assert manifest["source_url"] == planned.url
    assert manifest["report_version"] == "5"
    assert manifest["path"].startswith("data/raw/")  # relative, no machine-specific path
    assert not planned.destination.stat().st_mode & stat.S_IWUSR  # read-only
    assert not manifest_file.stat().st_mode & stat.S_IWUSR
    assert not list(planned.destination.parent.glob("*.partial"))
    again = nemweb.download_planned_file(planned, _client(b"different"), cfg, aemo_project, WINDOW)
    assert again["status"] == "already_present_verified"
    assert planned.destination.read_bytes() == payload
    path, record = nemweb.find_raw_archive(aemo_project, cfg.table, 2024, 4)
    assert path == planned.destination and record["sha256"] == manifest["sha256"]


def test_tampered_raw_file_detected(aemo_project: ProjectPaths) -> None:
    cfg = load_aemo_source_config(paths=aemo_project)
    payload = _payload()
    planned = _planned(aemo_project, len(payload))
    nemweb.download_planned_file(planned, _client(payload), cfg, aemo_project, WINDOW)
    planned.destination.chmod(0o644)
    planned.destination.write_bytes(payload + b"x")
    with pytest.raises(nemweb.ArchiveValidationError, match="hash inconsistency"):
        nemweb.download_planned_file(planned, _client(payload), cfg, aemo_project, WINDOW)
    with pytest.raises(nemweb.SourceResolutionError):
        nemweb.find_raw_archive(aemo_project, cfg.table, 2024, 4)


def test_orphan_identical_to_fresh_download_is_recovered(aemo_project: ProjectPaths) -> None:
    """Interruption after rename but before manifest: accept only if byte-identical."""
    cfg = load_aemo_source_config(paths=aemo_project)
    payload = _payload()
    planned = _planned(aemo_project, len(payload))
    planned.destination.parent.mkdir(parents=True)
    planned.destination.write_bytes(payload)
    result = nemweb.download_planned_file(planned, _client(payload), cfg, aemo_project, WINDOW)
    assert result["status"] == "recovered_verified"
    assert json.loads(nemweb.manifest_path(planned.destination).read_text())[
        "recovered_unmanifested_file"
    ]


def test_orphan_differing_from_fresh_download_stops(aemo_project: ProjectPaths) -> None:
    cfg = load_aemo_source_config(paths=aemo_project)
    payload = _payload()
    planned = _planned(aemo_project, len(payload))
    planned.destination.parent.mkdir(parents=True)
    planned.destination.write_bytes(b"owner file")
    with pytest.raises(nemweb.ArchiveValidationError, match="hash inconsistency"):
        nemweb.download_planned_file(planned, _client(payload), cfg, aemo_project, WINDOW)
    assert planned.destination.read_bytes() == b"owner file"
    assert not nemweb.manifest_path(planned.destination).exists()


def test_manifest_without_file_stops(aemo_project: ProjectPaths) -> None:
    cfg = load_aemo_source_config(paths=aemo_project)
    payload = _payload()
    planned = _planned(aemo_project, len(payload))
    manifest = nemweb.manifest_path(planned.destination)
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{}")
    with pytest.raises(nemweb.ArchiveValidationError, match="file is missing"):
        nemweb.download_planned_file(planned, _client(payload), cfg, aemo_project, WINDOW)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"not a zip", "not a valid zip"),
        (
            zip_bytes(render(full_day_rows(dt.date(2024, 4, 4))).replace("REGIONSUM", "PRICE")),
            "unsupported schema",
        ),
        (
            zip_bytes(render(full_day_rows(dt.date(2024, 4, 4))).replace("TOTALDEMAND", "X")),
            "missing columns",
        ),
    ],
)
def test_invalid_archive_or_schema_stops_and_leaves_nothing(
    aemo_project: ProjectPaths, payload: bytes, message: str
) -> None:
    cfg = load_aemo_source_config(paths=aemo_project)
    planned = _planned(aemo_project, len(payload))
    with pytest.raises(nemweb.ArchiveValidationError, match=message):
        nemweb.download_planned_file(planned, _client(payload), cfg, aemo_project, WINDOW)
    assert not planned.destination.exists()
    assert not list(planned.destination.parent.glob("*.partial"))


def test_listing_size_mismatch_stops(aemo_project: ProjectPaths) -> None:
    cfg = load_aemo_source_config(paths=aemo_project)
    payload = _payload()
    planned = _planned(aemo_project, len(payload) + 1)
    with pytest.raises(nemweb.ArchiveValidationError, match="listing says"):
        nemweb.download_planned_file(planned, _client(payload), cfg, aemo_project, WINDOW)


def test_download_retries_transient_server_error(aemo_project: ProjectPaths) -> None:
    base = load_aemo_source_config(paths=aemo_project)
    cfg = base.model_copy(update={"http": base.http.model_copy(update={"backoff_seconds": 0.0})})
    payload = _payload()
    planned = _planned(aemo_project, len(payload))
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, content=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = nemweb.download_planned_file(planned, client, cfg, aemo_project, WINDOW)
    assert result["status"] == "downloaded" and len(calls) == 2


def test_fetch_retries_then_fails_on_server_error(aemo_project: ProjectPaths) -> None:
    cfg = load_aemo_source_config(paths=aemo_project).model_copy(
        update={
            "http": load_aemo_source_config(paths=aemo_project).http.model_copy(
                update={"backoff_seconds": 0.0}
            )
        }
    )
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    with pytest.raises(httpx.HTTPStatusError):
        nemweb.fetch_with_retries(
            httpx.Client(transport=httpx.MockTransport(handler)), "https://x/", cfg
        )
    assert len(calls) == cfg.http.max_attempts


def test_fetch_does_not_retry_client_error(aemo_project: ProjectPaths) -> None:
    cfg = load_aemo_source_config(paths=aemo_project)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    with pytest.raises(httpx.HTTPStatusError):
        nemweb.fetch_with_retries(
            httpx.Client(transport=httpx.MockTransport(handler)), "https://x/", cfg
        )
    assert len(calls) == 1
