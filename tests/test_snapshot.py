"""Historical snapshot planning, batching, interruption and resume (offline)."""

import json
import stat
from pathlib import Path

import httpx
import pytest

from nem_forecast.config import AemoSourceConfig, load_aemo_source_config
from nem_forecast.ingestion import nemweb, snapshot
from nem_forecast.paths import ProjectPaths
from tests.aemo_fixtures import (
    archive_name,
    dvd_name,
    listing_for,
    month_rows,
    render,
    render_v6,
    zip_bytes,
)
from tests.conftest import REPO_ROOT

MONTHS = [(2024, 6), (2024, 7), (2024, 8), (2024, 9)]  # spans the 2024-08 naming change


@pytest.fixture
def project(tmp_project: ProjectPaths) -> ProjectPaths:
    text = (REPO_ROOT / "configs" / "aemo_source.yaml").read_text(encoding="utf-8")
    text = text.replace('start_month: "2022-01"', 'start_month: "2024-06"')
    text = text.replace('end_month: "2026-09"', 'end_month: "2024-09"')
    text = text.replace("batch_size: 6", "batch_size: 2")
    (tmp_project.configs / "aemo_source.yaml").write_text(text, encoding="utf-8")
    return tmp_project


@pytest.fixture
def cfg(project: ProjectPaths) -> AemoSourceConfig:
    return load_aemo_source_config(paths=project)


def _payloads() -> dict[str, bytes]:
    out = {}
    for y, m in MONTHS:
        text = render(month_rows(y, m)) if (y, m) < (2024, 9) else render_v6(month_rows(y, m))
        name = dvd_name(y, m) if (y, m) < (2024, 8) else archive_name(y, m)
        out[name] = zip_bytes(text)
    return out


def _listings(payloads: dict[str, bytes]) -> dict[str, str]:
    out = {}
    for y, m in MONTHS:
        name = dvd_name(y, m) if (y, m) < (2024, 8) else archive_name(y, m)
        out[nemweb.month_listing_url(nemweb_base(), y, m)] = listing_for(
            y,
            m,
            [
                (name, len(payloads[name])),
                (f"PUBLIC_DVD_PREDISPATCHREGIONSUM_D_{y}{m:02d}010000.zip", 9),
            ],
        )
    return out


def nemweb_base() -> str:
    return "https://nemweb.com.au/Data_Archive/Wholesale_Electricity/MMSDM"


def _client(payloads: dict[str, bytes], fail_names: set[str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1].replace("%23", "#")
        if fail_names and name in fail_names:
            return httpx.Response(404)
        return httpx.Response(200, content=payloads[name])

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_config_months_frozen() -> None:
    real = load_aemo_source_config()
    months = real.historical_snapshot.months
    assert len(months) == 57 and months[0] == (2022, 1) and months[-1] == (2026, 9)


def test_plan_resolves_both_filename_patterns(project: ProjectPaths, cfg: AemoSourceConfig) -> None:
    listings = _listings(_payloads())
    planned = snapshot.plan_snapshot(cfg, project, listings.__getitem__)
    assert [p.name for p in planned] == [
        dvd_name(2024, 6),
        dvd_name(2024, 7),
        archive_name(2024, 8),
        archive_name(2024, 9),
    ]
    doc = snapshot.plan_document(cfg, planned, project.root, "sid")
    assert doc["month_count"] == 4 and doc["request_count"] == {"listing": 4, "data": 4}
    assert doc["files"][2]["url"].endswith(
        "PUBLIC_ARCHIVE%23DISPATCHREGIONSUM%23FILE01%23202408010000.zip"
    )
    assert all(f["destination"].startswith("data/raw/aemo/mmsdm/") for f in doc["files"])


def test_missing_month_stops(project: ProjectPaths, cfg: AemoSourceConfig) -> None:
    listings = _listings(_payloads())
    listings[nemweb.month_listing_url(nemweb_base(), 2024, 7)] = "<pre></pre>"
    with pytest.raises(nemweb.SourceResolutionError, match="missing month 2024-07"):
        snapshot.plan_snapshot(cfg, project, listings.__getitem__)


def test_filename_ambiguity_stops(project: ProjectPaths, cfg: AemoSourceConfig) -> None:
    listings = _listings(_payloads())
    listings[nemweb.month_listing_url(nemweb_base(), 2024, 8)] = listing_for(
        2024, 8, [(archive_name(2024, 8, 1), 10), (archive_name(2024, 8, 2), 10)]
    )
    with pytest.raises(nemweb.SourceResolutionError, match="ambiguity"):
        snapshot.plan_snapshot(cfg, project, listings.__getitem__)


def test_complete_snapshot(project: ProjectPaths, cfg: AemoSourceConfig) -> None:
    payloads = _payloads()
    planned = snapshot.plan_snapshot(cfg, project, _listings(payloads).__getitem__)
    pauses: list[float] = []
    doc = snapshot.run_snapshot(
        planned, _client(payloads), cfg, project, "sid", sleep=pauses.append
    )
    assert doc["status"] == "complete" and doc["file_count"] == 4
    assert doc["retrieval_status_counts"] == {"downloaded": 4}
    assert pauses == [cfg.historical_snapshot.pause_seconds_between_batches]  # 2 batches of 2
    final = snapshot.snapshots_dir(project, cfg.table) / "sid.json"
    assert not final.stat().st_mode & stat.S_IWUSR
    assert snapshot.load_complete_snapshot(project, cfg.table, "sid")["file_count"] == 4
    with pytest.raises(FileExistsError):
        snapshot.run_snapshot(planned, _client(payloads), cfg, project, "sid", sleep=lambda _: None)


def test_interrupted_run_leaves_no_complete_manifest_then_resumes(
    project: ProjectPaths, cfg: AemoSourceConfig
) -> None:
    payloads = _payloads()
    planned = snapshot.plan_snapshot(cfg, project, _listings(payloads).__getitem__)
    broken = _client(payloads, fail_names={archive_name(2024, 8)})
    with pytest.raises(httpx.HTTPStatusError):
        snapshot.run_snapshot(planned, broken, cfg, project, "sid", sleep=lambda _: None)
    snaps = snapshot.snapshots_dir(project, cfg.table)
    assert not (snaps / "sid.json").exists()
    with pytest.raises(nemweb.SourceResolutionError):
        snapshot.load_complete_snapshot(project, cfg.table, "sid")
    failures = list(snaps.glob("sid.failed.*.json"))
    assert len(failures) == 1
    failure = json.loads(failures[0].read_text())
    assert failure["status"] == "failed" and len(failure["completed_files"]) == 2
    progress = json.loads((snaps / "sid.progress.json").read_text())
    assert progress["status"] == "in_progress" and progress["completed_files"] == 2
    assert not list(Path(project.data_raw).rglob("*.partial"))
    assert not list(Path(project.data_raw).rglob(".*.tmp"))
    # resume: valid files are skipped, the rest downloaded, then the snapshot completes
    doc = snapshot.run_snapshot(
        planned, _client(payloads), cfg, project, "sid", sleep=lambda _: None
    )
    assert doc["retrieval_status_counts"] == {"already_present_verified": 2, "downloaded": 2}
    assert json.loads((snaps / "sid.progress.json").read_text())["status"] == "complete"


def test_hash_inconsistency_during_snapshot_stops(
    project: ProjectPaths, cfg: AemoSourceConfig
) -> None:
    payloads = _payloads()
    planned = snapshot.plan_snapshot(cfg, project, _listings(payloads).__getitem__)
    snapshot.run_snapshot(
        planned[:1], _client(payloads), cfg, project, "first", sleep=lambda _: None
    )
    planned[0].destination.chmod(0o644)
    planned[0].destination.write_bytes(b"corrupted")
    with pytest.raises(nemweb.ArchiveValidationError, match="hash inconsistency"):
        snapshot.run_snapshot(
            planned, _client(payloads), cfg, project, "second", sleep=lambda _: None
        )
    assert not (snapshot.snapshots_dir(project, cfg.table) / "second.json").exists()
