"""Offline end-to-end Phase 2: synthetic snapshot -> lock -> build-hourly -> validate-data."""

from __future__ import annotations

import datetime as dt
import json
import re

import httpx
import pandas as pd
import pytest
from typer.testing import CliRunner

from nem_forecast.cli import app
from nem_forecast.config import load_aemo_source_config
from nem_forecast.ingestion import nemweb, snapshot
from nem_forecast.paths import ProjectPaths
from nem_forecast.target.hourly import TARGET_COLUMN
from tests.aemo_fixtures import dvd_name, full_day_rows, listing_for, render, row, zip_bytes
from tests.conftest import REPO_ROOT

SNAP = "DISPATCHREGIONSUM_2022-01_2022-01_retrievedTEST"
runner = CliRunner()


@pytest.fixture
def project(tmp_project: ProjectPaths) -> ProjectPaths:
    """A one-day frozen history (2022-01-01) with one intervention pair."""
    root = tmp_project.root
    src = (REPO_ROOT / "configs" / "aemo_source.yaml").read_text(encoding="utf-8")
    src = src.replace('end_month: "2026-09"', 'end_month: "2022-01"')
    src = re.sub(r"snapshot_id: .*", f"snapshot_id: {SNAP}", src)
    src = re.sub(
        r"lock_manifest: .*", f"lock_manifest: configs/data_snapshots/{SNAP}.lock.json", src
    )
    (root / "configs" / "aemo_source.yaml").write_text(src, encoding="utf-8")
    fc = (root / "configs" / "forecast.yaml").read_text(encoding="utf-8")
    (root / "configs" / "forecast.yaml").write_text(
        fc.replace('end_date: "2026-09-30"', 'end_date: "2022-01-01"'), encoding="utf-8"
    )
    rows = full_day_rows(dt.date(2022, 1, 1))  # VIC1 + NSW1, values 4001.25 ... 4288.25
    rows.append(row(dt.datetime(2022, 1, 1, 0, 30), intervention=1, demand="9000"))
    rows.append(row(dt.datetime(2022, 1, 1, 0, 30), region="NSW1", intervention=1, demand="1"))
    payload = zip_bytes(render(rows), member="PUBLIC_DVD_DISPATCHREGIONSUM_202201010000.CSV")
    cfg = load_aemo_source_config(paths=tmp_project)
    html = listing_for(2022, 1, [(dvd_name(2022, 1), len(payload))])
    planned = snapshot.plan_snapshot(cfg, tmp_project, lambda _: html)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=payload))
    )
    snapshot.run_snapshot(planned, client, cfg, tmp_project, SNAP, sleep=lambda _: None)
    return tmp_project


def test_lock_is_path_neutral_and_verified(project: ProjectPaths) -> None:
    result = runner.invoke(
        app, ["aemo-snapshot-lock", "--snapshot", SNAP, "--root", str(project.root)]
    )
    assert result.exit_code == 0, result.output
    path = snapshot.lock_path(project, SNAP)
    text = path.read_text()
    assert str(project.root) not in text and "data/raw" not in text
    lock = json.loads(text)
    (entry,) = lock["archives"]
    assert set(entry) == {
        "month",
        "filename",
        "url",
        "bytes",
        "sha256",
        "retrieved_utc",
        "listed_timestamp",
        "report_version",
        "archive_member",
        "archive_member_bytes",
        "archive_member_crc32",
    }
    assert entry["archive_member"] == "PUBLIC_DVD_DISPATCHREGIONSUM_202201010000.CSV"
    assert snapshot.lock_text(lock) == text  # deterministic serialisation
    again = runner.invoke(
        app, ["aemo-snapshot-lock", "--snapshot", SNAP, "--root", str(project.root)]
    )
    assert again.exit_code == 0  # identical content is accepted, never silently replaced
    assert snapshot.verify_lock_against_local(project, lock)


def test_build_hourly_and_independent_validation(project: ProjectPaths) -> None:
    root = ["--root", str(project.root)]
    assert runner.invoke(app, ["aemo-snapshot-lock", "--snapshot", SNAP, *root]).exit_code == 0
    result = runner.invoke(app, ["build-hourly", "--run-id", "t1", *root])
    assert result.exit_code == 0, result.output
    manifest = json.loads((project.results_runs / "t1" / "run_manifest.json").read_text())
    rc = manifest["row_counts"]
    assert rc["raw_rows_all_regions"] == 2 * 288 + 2
    assert rc["raw_vic1_records"] == 289
    assert rc["selected_primary_records"] == 288 and rc["unselected_pricing"] == 1
    assert rc["selected_physical"] == 1 and rc["quarantined_records"] == 0
    assert rc["hourly_records"] == 24 and rc["complete_hours"] == 24
    assert rc["intervention_affected_hours"] == 1 and rc["model_ready_rows"] == 24
    assert manifest["status"] == "complete" and manifest["quality"]["all_checks_passed"]
    assert manifest["lock_manifest"]["path"] == f"configs/data_snapshots/{SNAP}.lock.json"
    assert manifest["target"]["availability_buffer_applied"] is False
    assert manifest["coverage"]["first_hour_market"] == "2022-01-01T00:00:00+10:00"
    assert manifest["coverage"]["last_hour_market"] == "2022-01-01T23:00:00+10:00"
    ready = pd.read_parquet(project.root / manifest["outputs"]["model_ready"][0]["path"])
    first = ready.iloc[0]
    # hour 00:00: endpoints 00:05..01:00 -> values 4001.25..4012.25, but 00:30 (4006.25)
    # is replaced by the physical record 9000: (48078 + 3 - 4006.25 + 9000) / 12
    expected = (sum(4000.25 + i for i in range(1, 13)) - 4006.25 + 9000) / 12
    assert first[TARGET_COLUMN] == pytest.approx(expected)
    assert first["intervention_affected"]
    assert (project.results_runs / "t1" / "quality_report.md").read_text().startswith("# Phase 2")

    check = runner.invoke(app, ["validate-data", "--run-id", "t1", *root])
    assert check.exit_code == 0, check.output
    rec = json.loads((project.results_runs / "t1" / "independent_reconciliation.json").read_text())
    assert rec["all_passed"] and rec["independent"]["sql_complete_hours"] == 24
    assert rec["independent"]["max_abs_target_difference_mw"] <= 1e-9
    assert (project.results_runs / "t1" / "duckdb_views.sql").exists()

    # run IDs are never reused; validation output is not overwritten
    assert runner.invoke(app, ["build-hourly", "--run-id", "t1", *root]).exit_code == 1
    assert runner.invoke(app, ["validate-data", "--run-id", "t1", *root]).exit_code == 1


def test_build_refuses_archive_that_does_not_match_lock(project: ProjectPaths) -> None:
    root = ["--root", str(project.root)]
    assert runner.invoke(app, ["aemo-snapshot-lock", "--snapshot", SNAP, *root]).exit_code == 0
    archive = nemweb.raw_table_dir(project, "DISPATCHREGIONSUM") / "2022" / dvd_name(2022, 1)
    archive.chmod(0o644)
    archive.write_bytes(b"tampered")
    result = runner.invoke(app, ["build-hourly", "--run-id", "t2", *root])
    assert result.exit_code == 1 and "does not match the lock" in result.output
    failure = json.loads((project.results_runs / "t2" / "failure_manifest.json").read_text())
    assert failure["status"] == "failed"
    assert not (project.results_runs / "t2" / "run_manifest.json").exists()


def test_git_state_lists_uncommitted_paths_exactly(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import subprocess

    from nem_forecast.target.build import git_state

    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (repo / "CLAUDE.md").write_text("a")
    run("add", "CLAUDE.md")
    run("commit", "-q", "-m", "x")
    (repo / "CLAUDE.md").write_text("b")  # " M CLAUDE.md": leading blank status column
    state = git_state(repo)
    assert state["dirty"] and state["uncommitted_paths"] == ["CLAUDE.md"]
    assert len(state["commit"]) == 40
