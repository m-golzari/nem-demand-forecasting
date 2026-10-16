"""Offline end-to-end Phase 3 on a synthetic one-day history (mechanics, not values)."""

from __future__ import annotations

import json
import re
import shutil

import pandas as pd
from typer.testing import CliRunner

from nem_forecast.cli import app
from nem_forecast.paths import ProjectPaths
from tests.conftest import REPO_ROOT
from tests.test_phase2_pipeline import SNAP, project  # noqa: F401 - fixture reuse

runner = CliRunner()


def test_build_and_validate_features(project: ProjectPaths) -> None:  # noqa: F811
    root = ["--root", str(project.root)]
    assert runner.invoke(app, ["aemo-snapshot-lock", "--snapshot", SNAP, *root]).exit_code == 0
    assert runner.invoke(app, ["build-hourly", "--run-id", "p2", *root]).exit_code == 0
    for name in ("feature_availability.yaml", "features.yaml"):
        shutil.copy(REPO_ROOT / "configs" / name, project.configs / name)
    cfg = project.configs / "features.yaml"
    cfg.write_text(re.sub(r"input_phase2_run_id: .*", "input_phase2_run_id: p2", cfg.read_text()))

    result = runner.invoke(app, ["build-features", "--run-id", "p3", *root])
    assert result.exit_code == 0, result.output
    manifest = json.loads((project.results_runs / "p3" / "run_manifest.json").read_text())
    assert manifest["status"] == "complete"
    assert set(manifest["profiles"]) == {"primary_operational", "sensitivity_zero_lag"}
    assert manifest["input_phase2"]["run_id"] == "p2"
    assert set(manifest["config_hashes"]) >= {
        "configs/features.yaml",
        "configs/feature_availability.yaml",
    }
    for profile in manifest["profiles"]:
        checks = manifest["checks"][profile]
        assert (
            checks["passed"]
            and checks["rows"] == 24
            and checks["endpoint_after_cutoff_violations"] == 0
        )
        for out in manifest["outputs"][profile]["features"]:
            assert f"profile={profile}/" in out["path"]
            frame = pd.read_parquet(project.root / out["path"])
            assert set(frame["availability_profile"]) == {profile}
    elig = json.loads((project.results_runs / "p3" / "eligibility_report.json").read_text())
    assert elig["primary_operational"]["rows"] == 24  # no row dropped despite no history
    assert elig["primary_operational"]["flags"]["eligible_lag24_baseline"] == 0
    assert (project.results_runs / "p3" / "schema_report.md").exists()

    check = runner.invoke(app, ["validate-features", "--run-id", "p3", *root])
    assert check.exit_code == 0, check.output
    assert runner.invoke(app, ["build-features", "--run-id", "p3", *root]).exit_code == 1


def test_rebuild_is_logically_identical(project: ProjectPaths) -> None:  # noqa: F811
    root = ["--root", str(project.root)]
    assert runner.invoke(app, ["aemo-snapshot-lock", "--snapshot", SNAP, *root]).exit_code == 0
    assert runner.invoke(app, ["build-hourly", "--run-id", "p2", *root]).exit_code == 0
    for name in ("feature_availability.yaml", "features.yaml"):
        shutil.copy(REPO_ROOT / "configs" / name, project.configs / name)
    cfg = project.configs / "features.yaml"
    cfg.write_text(re.sub(r"input_phase2_run_id: .*", "input_phase2_run_id: p2", cfg.read_text()))
    for rid in ("a", "b"):
        assert runner.invoke(app, ["build-features", "--run-id", rid, *root]).exit_code == 0
    result = runner.invoke(app, ["compare-feature-runs", "--run-a", "a", "--run-b", "b", *root])
    assert result.exit_code == 0, result.output
    report = json.loads((project.results_runs / "b" / "comparison_vs_a.json").read_text())
    assert report["all_logically_identical"] and not report["all_bytes_identical"]
    ds = report["datasets"]["primary_operational/features"]
    assert ds["metadata_keys_differing"] == ["nem_forecast.run_id"]
    assert ds["logical_hash"][0] == ds["logical_hash"][1]
