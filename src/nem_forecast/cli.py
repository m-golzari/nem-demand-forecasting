"""Thin command-line entry point. Business logic lives in library modules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from nem_forecast import __version__
from nem_forecast.config import ConfigError, ForecastConfig, load_forecast_config
from nem_forecast.paths import ProjectPaths, ProjectRootError

app = typer.Typer(add_completion=False, no_args_is_help=True, help="NEM VIC1 demand forecasting.")

ConfigOption = Annotated[
    Path | None,
    typer.Option("--config", help="Forecast config path (default: configs/forecast.yaml)."),
]
RootOption = Annotated[
    Path | None, typer.Option("--root", help="Project root (default: NEM_FORECAST_ROOT/auto).")
]


def _load(config: Path | None, root: Path | None) -> tuple[ProjectPaths, ForecastConfig]:
    try:
        paths = ProjectPaths.from_root(root)
        return paths, load_forecast_config(config, paths)
    except (ConfigError, ProjectRootError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@app.command("validate-config")
def validate_config(config: ConfigOption = None, root: RootOption = None) -> None:
    """Validate the forecast config against the frozen version-1 contract."""
    paths, cfg = _load(config, root)
    t, f = cfg.target, cfg.forecast
    typer.echo(
        f"OK: {paths.forecast_config_path(config)} | {t.region} {t.source_field} "
        f"hourly mean {t.unit} | origin {f.origin_market_time} "
        f"UTC+{cfg.time.market_utc_offset_hours} | horizon {f.horizon_hours}h | "
        f"quantiles {list(f.quantiles)}"
    )


@app.command("show-config")
def show_config(config: ConfigOption = None, root: RootOption = None) -> None:
    """Print the resolved, validated config and project root as JSON."""
    paths, cfg = _load(config, root)
    payload = {
        "package_version": __version__,
        "project_root": str(paths.root),
        "config_path": str(paths.forecast_config_path(config)),
        "config": cfg.model_dump(mode="json"),
    }
    typer.echo(json.dumps(payload, indent=2))
