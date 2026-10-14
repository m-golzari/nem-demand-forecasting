"""Shared offline fixtures."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from nem_forecast.paths import ProjectPaths

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent a developer's environment from leaking into tests."""
    monkeypatch.delenv("NEM_FORECAST_ROOT", raising=False)
    monkeypatch.delenv("NEM_FORECAST_CONFIG", raising=False)


@pytest.fixture
def valid_config_dict() -> dict[str, Any]:
    text = (REPO_ROOT / "configs" / "forecast.yaml").read_text(encoding="utf-8")
    data: dict[str, Any] = yaml.safe_load(text)
    return data


@pytest.fixture
def tmp_project(tmp_path: Path) -> ProjectPaths:
    """A minimal throwaway project root containing a copy of the real config."""
    root = tmp_path / "project"
    (root / "configs").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'tmp'\n", encoding="utf-8")
    shutil.copy(REPO_ROOT / "configs" / "forecast.yaml", root / "configs" / "forecast.yaml")
    return ProjectPaths(root)
