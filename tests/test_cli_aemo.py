import datetime as dt
import json
from pathlib import Path

import httpx
import pandas as pd
from typer.testing import CliRunner

from nem_forecast.cli import app
from nem_forecast.config import load_aemo_source_config
from nem_forecast.ingestion import nemweb
from nem_forecast.paths import ProjectPaths
from tests.aemo_fixtures import dvd_name, full_day_rows, listing_for, render, zip_bytes
from tests.conftest import REPO_ROOT

runner = CliRunner()


def _project_with_raw(tmp_project: ProjectPaths, tmp_path: Path) -> ProjectPaths:
    (tmp_project.configs / "aemo_source.yaml").write_text(
        (REPO_ROOT / "configs" / "aemo_source.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    cfg = load_aemo_source_config(paths=tmp_project)
    rows = []
    for day in range(4, 11):
        rows += full_day_rows(dt.date(2024, 4, day))
    payload = zip_bytes(render(rows))
    html = listing_for(2024, 4, [(dvd_name(2024, 4), len(payload))])
    planned = nemweb.plan_download(
        cfg, tmp_project, dt.date(2024, 4, 4), dt.date(2024, 4, 11), lambda _: html
    )[0]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=payload))
    )
    nemweb.download_planned_file(
        planned, client, cfg, tmp_project, (dt.date(2024, 4, 4), dt.date(2024, 4, 11))
    )
    return tmp_project


def test_audit_and_normalise_cli(tmp_project: ProjectPaths, tmp_path: Path) -> None:
    project = _project_with_raw(tmp_project, tmp_path)
    root = ["--root", str(project.root)]
    audit = runner.invoke(app, ["aemo-audit", *root])
    assert audit.exit_code == 0, audit.output
    assert "VIC1 rows 2016 | missing 0 | pk_duplicates 0" in audit.output
    norm = runner.invoke(app, ["aemo-normalise", *root])
    assert norm.exit_code == 0, norm.output
    out = project.data_interim / "aemo" / "dispatchregionsum"
    frame = pd.read_parquet(out / "vic1_20240404_20240411.parquet")
    assert len(frame) == 2016 and set(frame["region_id"]) == {"VIC1"}
    assert str(frame["interval_start_utc"].dt.tz) == "UTC"
    manifest = json.loads((out / "vic1_20240404_20240411.manifest.json").read_text())
    assert manifest["reconciliation"]["rows_in_window_all_regions"] == 4032
    # derived outputs are not silently replaced
    again = runner.invoke(app, ["aemo-normalise", *root])
    assert again.exit_code == 1 and "--force" in again.output
    assert runner.invoke(app, ["aemo-normalise", *root, "--force"]).exit_code == 0


def test_audit_without_raw_fails(tmp_project: ProjectPaths) -> None:
    (tmp_project.configs / "aemo_source.yaml").write_text(
        (REPO_ROOT / "configs" / "aemo_source.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    result = runner.invoke(app, ["aemo-audit", "--root", str(tmp_project.root)])
    assert result.exit_code == 1 and "ERROR" in result.output


def test_download_refuses_large_window_before_network(tmp_project: ProjectPaths) -> None:
    (tmp_project.configs / "aemo_source.yaml").write_text(
        (REPO_ROOT / "configs" / "aemo_source.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    result = runner.invoke(
        app,
        [
            "aemo-download",
            "--dry-run",
            "--root",
            str(tmp_project.root),
            "--start",
            "2022-01-01",
            "--end",
            "2026-10-01",
        ],
    )
    assert result.exit_code == 1 and "owner approval" in result.output
