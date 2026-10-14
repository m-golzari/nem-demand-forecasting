"""Explicit project-root and directory resolution.

All reads and writes go through :class:`ProjectPaths`, so nothing is ever
written relative to the process's current working directory.

Root resolution order:

1. an explicit ``root`` argument;
2. the ``NEM_FORECAST_ROOT`` environment variable;
3. discovery upward from this module's location (works for editable installs).

The resolved directory must contain ``pyproject.toml`` and ``configs/``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT_ENV_VAR = "NEM_FORECAST_ROOT"
CONFIG_ENV_VAR = "NEM_FORECAST_CONFIG"
_ROOT_MARKERS = ("pyproject.toml", "configs")


class ProjectRootError(RuntimeError):
    """Raised when the project root cannot be resolved or is not a valid root."""


def _is_project_root(path: Path) -> bool:
    return all((path / marker).exists() for marker in _ROOT_MARKERS)


def resolve_project_root(root: str | Path | None = None) -> Path:
    """Return the absolute project root, failing loudly if it is not valid."""
    if root is None:
        env_root = os.environ.get(ROOT_ENV_VAR, "").strip()
        root = env_root or None
    if root is not None:
        candidate = Path(root).expanduser().resolve()
        if not _is_project_root(candidate):
            raise ProjectRootError(
                f"{candidate} is not a nem-forecast project root "
                f"(expected {', '.join(_ROOT_MARKERS)})"
            )
        return candidate
    for parent in Path(__file__).resolve().parents:
        if _is_project_root(parent):
            return parent
    raise ProjectRootError(
        f"Could not discover the project root from the package location; set {ROOT_ENV_VAR}."
    )


@dataclass(frozen=True)
class ProjectPaths:
    """Canonical project directories (see CLAUDE.md section 17). Never creates them."""

    root: Path

    @classmethod
    def from_root(cls, root: str | Path | None = None) -> ProjectPaths:
        return cls(resolve_project_root(root))

    @property
    def configs(self) -> Path:
        return self.root / "configs"

    @property
    def data_raw(self) -> Path:
        return self.root / "data" / "raw"

    @property
    def data_interim(self) -> Path:
        return self.root / "data" / "interim"

    @property
    def data_processed(self) -> Path:
        return self.root / "data" / "processed"

    @property
    def docs(self) -> Path:
        return self.root / "docs"

    @property
    def results_runs(self) -> Path:
        return self.root / "results" / "runs"

    @property
    def results_figures(self) -> Path:
        return self.root / "results" / "figures"

    @property
    def results_tables(self) -> Path:
        return self.root / "results" / "tables"

    @property
    def scratch(self) -> Path:
        return self.root / "scratch"

    @property
    def default_forecast_config(self) -> Path:
        return self.configs / "forecast.yaml"

    def forecast_config_path(self, override: str | Path | None = None) -> Path:
        """Return the forecast config path: argument, then env var, then default.

        Relative paths are resolved against the project root, never the CWD.
        """
        if override is None:
            override = os.environ.get(CONFIG_ENV_VAR, "").strip() or None
        if override is None:
            return self.default_forecast_config
        path = Path(override).expanduser()
        return path if path.is_absolute() else self.root / path
