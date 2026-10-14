from pathlib import Path

import pytest

from nem_forecast.paths import ProjectPaths, ProjectRootError, resolve_project_root
from tests.conftest import REPO_ROOT


def test_discovers_repo_root_from_package_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # discovery must not depend on the CWD
    assert resolve_project_root() == REPO_ROOT


def test_explicit_root_wins_over_env(
    tmp_project: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NEM_FORECAST_ROOT", str(REPO_ROOT))
    assert resolve_project_root(tmp_project.root) == tmp_project.root


def test_env_root(tmp_project: ProjectPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEM_FORECAST_ROOT", str(tmp_project.root))
    assert resolve_project_root() == tmp_project.root


def test_invalid_root_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(ProjectRootError):
        resolve_project_root(tmp_path)


def test_invalid_env_root_fails_loudly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEM_FORECAST_ROOT", str(tmp_path))
    with pytest.raises(ProjectRootError):
        resolve_project_root()


def test_standard_directories() -> None:
    paths = ProjectPaths(Path("/r"))
    assert paths.data_raw == Path("/r/data/raw")
    assert paths.data_interim == Path("/r/data/interim")
    assert paths.data_processed == Path("/r/data/processed")
    assert paths.results_runs == Path("/r/results/runs")
    assert paths.default_forecast_config == Path("/r/configs/forecast.yaml")


def test_paths_do_not_create_directories(tmp_project: ProjectPaths) -> None:
    _ = tmp_project.data_raw, tmp_project.results_runs
    assert not tmp_project.data_raw.exists()
    assert not tmp_project.results_runs.exists()


def test_relative_config_resolves_against_root_not_cwd(
    tmp_project: ProjectPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert tmp_project.forecast_config_path("configs/x.yaml") == tmp_project.root / "configs/x.yaml"


def test_config_env_override(tmp_project: ProjectPaths, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEM_FORECAST_CONFIG", "configs/alt.yaml")
    assert tmp_project.forecast_config_path() == tmp_project.root / "configs/alt.yaml"
