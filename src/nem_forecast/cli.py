"""Thin command-line entry point. Business logic lives in library modules."""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from nem_forecast import __version__
from nem_forecast.config import (
    AemoSourceConfig,
    ConfigError,
    ForecastConfig,
    load_aemo_source_config,
    load_forecast_config,
)
from nem_forecast.ingestion import nemweb
from nem_forecast.ingestion.aemo_csv import AemoTable, read_aemo_zip
from nem_forecast.ingestion.aemo_normalise import SourceRef, normalise_vic1
from nem_forecast.paths import ProjectPaths, ProjectRootError
from nem_forecast.provenance import sha256_file, utc_now_iso
from nem_forecast.validation.aemo_audit import audit_dispatchregionsum

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


# --- Phase 1: AEMO source ---------------------------------------------------

DateOption = Annotated[
    dt.datetime | None,
    typer.Option(formats=["%Y-%m-%d"], help="Market date (default: configs/aemo_source.yaml)."),
]


def _window(
    start: dt.datetime | None, end: dt.datetime | None, cfg: AemoSourceConfig
) -> tuple[dt.date, dt.date]:
    return (
        start.date() if start else cfg.approved_window.start_date,
        end.date() if end else cfg.approved_window.end_date,
    )


def _source(root: Path | None) -> tuple[ProjectPaths, AemoSourceConfig]:
    try:
        paths = ProjectPaths.from_root(root)
        return paths, load_aemo_source_config(paths=paths)
    except (ConfigError, ProjectRootError) as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(code=1) from exc


def _fail(exc: Exception) -> typer.Exit:
    typer.echo(f"ERROR: {type(exc).__name__}: {exc}", err=True)
    return typer.Exit(code=1)


@app.command("aemo-download")
def aemo_download(
    start: DateOption = None,
    end: DateOption = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Resolve and report only.")] = False,
    owner_approved_large_download: Annotated[
        bool, typer.Option("--owner-approved-large-download", help="Owner-gate override.")
    ] = False,
    root: RootOption = None,
) -> None:
    """Download official MMSDM DISPATCHREGIONSUM archives for a market-date window."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    paths, cfg = _source(root)
    start_date, end_date = _window(start, end, cfg)
    try:
        nemweb.check_window_allowed(cfg, start_date, end_date, owner_approved_large_download)
        with nemweb.make_client(cfg) as client:
            listings: dict[str, str] = {}

            def get_listing(url: str) -> str:
                listings[url] = nemweb.fetch_with_retries(client, url, cfg).text
                return listings[url]

            planned = nemweb.plan_download(cfg, paths, start_date, end_date, get_listing)
            summary = nemweb.plan_summary(planned, start_date, end_date, paths.root)
            if dry_run:
                typer.echo(json.dumps({"dry_run": True, **summary}, indent=2))
                return
            saved = [nemweb.save_listing(paths, cfg.table, u, h) for u, h in listings.items()]
            results = [
                nemweb.download_planned_file(p, client, cfg, paths, (start_date, end_date))
                for p in planned
            ]
    except (
        nemweb.DownloadRefusedError,
        nemweb.SourceResolutionError,
        httpx.HTTPError,
        RuntimeError,
    ) as exc:
        raise _fail(exc) from exc
    typer.echo(json.dumps({"dry_run": False, "listings_saved": saved, "files": results}, indent=2))


def _load_window_table(
    paths: ProjectPaths, cfg: AemoSourceConfig, start_date: dt.date, end_date: dt.date
) -> tuple[AemoTable, dict[str, Any]]:
    months = nemweb.months_for_window(start_date, end_date)
    if len(months) != 1:
        raise NotImplementedError("Phase 1 audits windows within a single archive month only")
    path, record = nemweb.find_raw_archive(paths, cfg.table, *months[0])
    return read_aemo_zip(path), {
        "file": record["path"],
        "sha256": record["sha256"],
        "bytes": record["bytes"],
        "source_url": record["source_url"],
        "retrieval_completed_utc": record["retrieval_completed_utc"],
    }


def _interim_dir(paths: ProjectPaths) -> Path:
    return paths.data_interim / "aemo" / "dispatchregionsum"


def _stem(start_date: dt.date, end_date: dt.date) -> str:
    return f"vic1_{start_date:%Y%m%d}_{end_date:%Y%m%d}"


def _check_new(target: Path, force: bool) -> None:
    if target.exists() and not force:
        raise FileExistsError(f"{target} exists; pass --force to rebuild this derived output")


ForceOption = Annotated[bool, typer.Option("--force", help="Rebuild an existing derived output.")]


@app.command("aemo-audit")
def aemo_audit(
    start: DateOption = None,
    end: DateOption = None,
    force: ForceOption = False,
    root: RootOption = None,
) -> None:
    """Audit the raw archive and VIC1 window; write a machine-readable JSON report."""
    paths, cfg = _source(root)
    start_date, end_date = _window(start, end, cfg)
    target = _interim_dir(paths) / f"audit_{_stem(start_date, end_date)}.json"
    try:
        _check_new(target, force)
        table, source = _load_window_table(paths, cfg, start_date, end_date)
        report = audit_dispatchregionsum(table, start_date, end_date, source)
    except (FileExistsError, nemweb.SourceResolutionError, NotImplementedError, ValueError) as exc:
        raise _fail(exc) from exc
    report["generated_utc"] = utc_now_iso()
    report["package_version"] = __version__
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    vic = report["window"]["vic1"]
    typer.echo(
        f"OK: audit written to {target.relative_to(paths.root)} | VIC1 rows {vic['rows']} | "
        f"missing {len(vic['missing_intervals'])} | pk_duplicates {vic['pk_duplicates']} | "
        f"intervention {vic['intervention_counts']}"
    )


@app.command("aemo-normalise")
def aemo_normalise(
    start: DateOption = None,
    end: DateOption = None,
    force: ForceOption = False,
    root: RootOption = None,
) -> None:
    """Write canonical VIC1 interim Parquet (all records kept, anomalies flagged)."""
    paths, cfg = _source(root)
    start_date, end_date = _window(start, end, cfg)
    target = _interim_dir(paths) / f"{_stem(start_date, end_date)}.parquet"
    try:
        _check_new(target, force)
        table, source = _load_window_table(paths, cfg, start_date, end_date)
        frame, reconciliation = normalise_vic1(
            table, SourceRef(source["file"], source["sha256"]), start_date, end_date
        )
    except (FileExistsError, nemweb.SourceResolutionError, NotImplementedError, ValueError) as exc:
        raise _fail(exc) from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target, index=False)
    manifest = {
        "output": str(target.relative_to(paths.root)),
        "output_sha256": sha256_file(target),
        "inputs": [source],
        "window_market_dates_end_exclusive": [start_date.isoformat(), end_date.isoformat()],
        "reconciliation": reconciliation,
        "quality_flag_counts": {
            c: int(frame[c].sum()) for c in frame.columns if c.startswith("q_")
        },
        "columns": {c: str(t) for c, t in frame.dtypes.items()},
        "generated_utc": utc_now_iso(),
        "package_version": __version__,
    }
    manifest_file = target.with_suffix(".manifest.json")
    manifest_file.write_text(json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    typer.echo(
        f"OK: {reconciliation['rows_written']} VIC1 rows -> {target.relative_to(paths.root)} | "
        f"raw rows {reconciliation['raw_data_rows']} | in window "
        f"{reconciliation['rows_in_window_all_regions']}"
    )
