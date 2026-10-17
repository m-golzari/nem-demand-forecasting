"""Final pre-holdout artefact: chronology, save/load consistency, guards (offline, synthetic)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from nem_forecast.cli import app
from nem_forecast.evaluation.backtest import TARGET
from nem_forecast.evaluation.final_artefact import (
    FIXTURE_TOLERANCE_MW,
    build_final_artefact,
    load_final_artefact,
    virtual_fold,
)
from nem_forecast.evaluation.folds import HoldoutAccessError
from nem_forecast.evaluation.phase6 import nested_windows
from tests.test_phase6 import BCFG, design, small_proto  # noqa: F401 - fixture reuse

H = dt.date(2025, 7, 1)  # synthetic "holdout start"


@pytest.fixture(scope="module")
def built(design: pd.DataFrame, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:  # noqa: F811
    run = tmp_path_factory.mktemp("artefacts") / "final"
    pre = design[design["origin_date"] < H]
    return run, build_final_artefact(
        pre, run, small_proto(), BCFG, {"code": {"dirty": False}}, holdout_first=H
    )


def test_chronology_matches_frozen_rules(built: tuple[Path, dict]) -> None:
    _, m = built
    fold = virtual_fold(H, 1, 180)
    w = nested_windows(fold, small_proto(), BCFG)
    r = m["date_ranges"]
    assert r["final_training_origins"][1] == "2025-06-29" == (H - dt.timedelta(days=2)).isoformat()
    assert r["calibration_origins"] == [w.calibration_first.isoformat(), "2025-06-29"]
    assert r["tuning_origins"] == [w.tuning_first.isoformat(), w.tuning_last.isoformat()]
    assert r["inner_training_origins"][1] == (w.tuning_first - dt.timedelta(days=2)).isoformat()
    assert (
        r["calibration_model_training_origins"][1]
        == (w.calibration_first - dt.timedelta(days=2)).isoformat()
    )
    assert (
        m["holdout"]["holdout_rows_loaded"] == 0
        and m["holdout"]["max_origin_loaded"] < H.isoformat()
    )
    assert len(m["trailing_offsets"]) == 72 and all(
        o["n_samples"] == 180 for o in m["trailing_offsets"]
    )
    assert set(m["models"]) == {"p10", "p50", "p90"}
    assert m["proposed_holdout_command"].endswith("--artefact-run-id final")


def test_save_load_reproduces_fixture_exactly(built: tuple[Path, dict]) -> None:
    run, m = built
    art = load_final_artefact(run)
    inputs = pd.read_parquet(run / "fixture_inputs.parquet")
    assert TARGET not in inputs.columns
    stored = pd.read_parquet(run / "fixture_predictions.parquet")
    again = art.predict(inputs)
    for c in ("raw_p10", "raw_p50", "raw_p90", "p10", "p50", "p90"):
        assert (again[c] - stored[c]).abs().max() <= FIXTURE_TOLERANCE_MW
    assert m["fixture"]["max_reload_difference_mw"] <= FIXTURE_TOLERANCE_MW
    shuffled = inputs[list(reversed(inputs.columns))].assign(extra=1.0)
    pd.testing.assert_frame_equal(again, art.predict(shuffled))  # order / extra columns ignored
    fb = pd.read_parquet(run / "fixture_fallback_predictions.parquet")
    assert fb.loc[fb["horizon"] == 24, "fallback_used"].all()


def test_tampered_model_file_rejected(built: tuple[Path, dict], tmp_path: Path) -> None:
    import shutil

    run, _ = built
    copy = tmp_path / "copy"
    shutil.copytree(run, copy)
    target = copy / "models" / "lightgbm_p50.txt"
    target.chmod(0o644)
    target.write_text(target.read_text() + "\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_final_artefact(copy)


def test_holdout_rows_rejected(design: pd.DataFrame, tmp_path: Path) -> None:  # noqa: F811
    with pytest.raises(HoldoutAccessError):
        build_final_artefact(design, tmp_path / "x", small_proto(), BCFG, {}, holdout_first=H)
    assert not (tmp_path / "x").exists()


def test_unmatured_outcomes_cannot_change_models(
    design: pd.DataFrame,  # noqa: F811
    built: tuple[Path, dict],
    tmp_path: Path,
) -> None:
    run, _ = built
    pre = design[design["origin_date"] < H].copy()
    pre.loc[pre["origin_date"] == H - dt.timedelta(days=1), TARGET] = 1e9  # not matured at H - 1 h
    build_final_artefact(pre, tmp_path / "poisoned", small_proto(), BCFG, {}, holdout_first=H)
    for q in ("p10", "p50", "p90"):
        a = (run / "models" / f"lightgbm_{q}.txt").read_text()
        b = (tmp_path / "poisoned" / "models" / f"lightgbm_{q}.txt").read_text()
        assert a == b


def test_cli_guards(tmp_project) -> None:  # type: ignore[no-untyped-def]
    runner = CliRunner()
    result = runner.invoke(
        app, ["build-final-artefact", "--run-id", "x", "--root", str(tmp_project.root)]
    )
    assert result.exit_code == 1 and "clean git tree" in result.output
    result = runner.invoke(app, ["evaluate-holdout", "--artefact-run-id", "x"])
    assert result.exit_code == 1 and "HoldoutAccessError" in result.output
    result = runner.invoke(
        app, ["evaluate-holdout", "--owner-approved-final-evaluation", "--artefact-run-id", "x"]
    )
    assert result.exit_code == 1 and "nothing was read" in result.output
    assert json.dumps({"ok": True})
