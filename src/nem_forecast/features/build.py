"""Phase 3 pipeline: approved Phase 2 hourly target -> point-in-time feature dataset.

Outputs (git-ignored; profile in every path, schema column and report):

* ``data/processed/features/<run_id>/profile=<profile>/year=YYYY/part-0.parquet``
  (full design table, all 41,616 rows per profile);
* ``data/interim/feature_audit/<run_id>/profile=<profile>/year=YYYY/part-0.parquet``
  (one lineage row per row x historical feature);
* ``results/runs/<run_id>/``: ``eligibility_report.{json,md}``,
  ``schema_report.{json,md}``, ``leakage_checks.json``, ``run_manifest.json``
  (written last) or ``failure_manifest.json``.

Every historical value is recomputed for every row by the independent
physically-truncated reference builder, and the run fails unless both agree
and every used source endpoint is ``<= cutoff``.
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from nem_forecast.config import (
    FeatureAvailabilityConfig,
    FeatureConfig,
    ForecastConfig,
)
from nem_forecast.features.calendar import vic_public_holidays
from nem_forecast.features.design import TARGET_COLUMN, build_profile, design_keys
from nem_forecast.features.history import FEATURES, Observations
from nem_forecast.features.reference import reference_features
from nem_forecast.paths import ProjectPaths
from nem_forecast.provenance import read_json, sha256_file, utc_now_iso, write_json_atomic
from nem_forecast.target.build import environment, git_state
from nem_forecast.target.hourly import TARGET_COLUMN as PHASE2_TARGET

logger = logging.getLogger(__name__)

CONFIG_FILES = (
    "configs/forecast.yaml",
    "configs/aemo_source.yaml",
    "configs/feature_availability.yaml",
    "configs/features.yaml",
)
VALUE_TOLERANCE = 1e-9


class Phase3CheckError(RuntimeError):
    """Raised when a leakage or reconciliation check fails (outputs kept for inspection)."""


def load_phase2(paths: ProjectPaths, run_id: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read the approved Phase 2 canonical hourly target after verifying its hashes."""
    manifest_file = paths.results_runs / run_id / "run_manifest.json"
    manifest = read_json(manifest_file)
    if manifest.get("status") != "complete":
        raise ValueError(f"Phase 2 run {run_id} is not complete")
    parts = []
    for out in manifest["outputs"]["canonical_hourly"]:
        path = paths.root / out["path"]
        if sha256_file(path) != out["sha256"]:
            raise ValueError(f"Phase 2 output hash mismatch: {out['path']}")
        parts.append(pd.read_parquet(path))
    hourly = pd.concat(parts, ignore_index=True).sort_values("hour_start_utc", kind="mergesort")
    info = {
        "run_id": run_id,
        "run_manifest_sha256": sha256_file(manifest_file),
        "code_commit": manifest["code"].get("commit"),
        "snapshot_id": manifest["snapshot_id"],
        "lock_manifest": manifest["lock_manifest"],
        "canonical_hourly_outputs": manifest["outputs"]["canonical_hourly"],
    }
    return hourly.reset_index(drop=True), info


def source_endpoint_contract_check(hourly: pd.DataFrame) -> dict[str, Any]:
    """Complete hours must have latest source endpoint == label + 1 h (Phase 2 contract)."""
    complete = hourly[hourly["is_complete"]]
    end = pd.to_datetime(
        complete["last_source_settlementdate"], format="%Y/%m/%d %H:%M:%S"
    ).dt.tz_localize("+10:00")
    expected = complete["hour_start_utc"] + pd.Timedelta(hours=1)
    violations = int((end != expected).sum())
    return {
        "complete_hours": len(complete),
        "endpoint_ne_label_plus_1h": violations,
        "passed": violations == 0,
    }


def write_partitioned(
    frame: pd.DataFrame, base: Path, root: Path, metadata: dict[str, str]
) -> list[dict[str, Any]]:
    outputs = []
    for year in sorted({int(y) for y in frame["year"].tolist()}):
        part = frame[frame["year"] == year]
        target = base / f"year={year}" / "part-0.parquet"
        target.parent.mkdir(parents=True, exist_ok=False)
        table = pa.Table.from_pandas(part, preserve_index=False)
        table = table.replace_schema_metadata(
            {
                **(table.schema.metadata or {}),
                **{k.encode(): v.encode() for k, v in metadata.items()},
            }
        )
        pq.write_table(table, target, compression="zstd")
        target.chmod(0o444)
        outputs.append(
            {
                "path": str(target.relative_to(root)),
                "year": year,
                "rows": len(part),
                "sha256": sha256_file(target),
                "bytes": target.stat().st_size,
            }
        )
    return outputs


def leakage_checks(
    obs: Observations, wide: pd.DataFrame, audit: pd.DataFrame, lag_hours: int
) -> dict[str, Any]:
    used = audit[audit["available"]]
    violations = int((used["latest_source_endpoint_utc"] > used["cutoff_utc"]).sum())
    origins = np.unique(wide["origin_utc"].to_numpy(dtype="datetime64[ns]").astype(np.int64))
    started = time.monotonic()
    ref = reference_features(obs, origins, lag_hours)
    merged = audit.assign(
        origin_ns=audit["origin_utc"].to_numpy(dtype="datetime64[ns]").astype(np.int64)
    ).merge(
        ref,
        on=["origin_ns", "horizon", "feature"],
        how="outer",
        suffixes=("", "_ref"),
        indicator=True,
    )
    matched = merged["_merge"] == "both"
    avail_diff = int(
        (merged.loc[matched, "available"] != merged.loc[matched, "available_ref"]).sum()
    )
    reason_diff = int(
        (
            merged.loc[matched, "missing_reason"].astype(str)
            != merged.loc[matched, "missing_reason_ref"]
        ).sum()
    )
    both = matched & merged["available"].fillna(False).astype(bool)
    value_diff = float(
        np.max(
            np.abs(
                (merged.loc[both, "value"] - merged.loc[both, "value_ref"]).to_numpy(dtype=float)
            ),
            initial=0.0,
        )
    )
    ends = (
        merged.loc[both, "latest_source_endpoint_utc"]
        .to_numpy(dtype="datetime64[ns]")
        .astype(np.int64)
    )
    end_diff = int((ends != merged.loc[both, "latest_source_endpoint_ns"].astype(np.int64)).sum())
    lineage_cols = [
        "represented_start_label_utc",
        "represented_end_label_utc",
        "latest_observation_label_utc",
        "latest_source_endpoint_utc",
        "cutoff_utc",
    ]
    lineage_missing = int(used[lineage_cols].isna().any(axis=1).sum())
    checks = {
        "used_values": len(used),
        "endpoint_after_cutoff_violations": violations,
        "lineage_incomplete_for_used_values": lineage_missing,
        "reference_rows": len(ref),
        "rows_unmatched_between_builders": int((~matched).sum()),
        "availability_disagreements": avail_diff,
        "reason_disagreements": reason_diff,
        "max_abs_value_difference": value_diff,
        "latest_endpoint_disagreements": end_diff,
        "reference_seconds": round(time.monotonic() - started, 2),
    }
    checks["passed"] = (
        violations == 0
        and lineage_missing == 0
        and checks["rows_unmatched_between_builders"] == 0
        and avail_diff == 0
        and reason_diff == 0
        and value_diff <= VALUE_TOLERANCE
        and end_diff == 0
    )
    return checks


def eligibility_report(wide: pd.DataFrame) -> dict[str, Any]:
    flags = [
        "target_available",
        "eligible_lag24_baseline",
        "eligible_lag168_baseline",
        "eligible_initial_ml_features",
    ]
    out: dict[str, Any] = {
        "rows": len(wide),
        "flags": {},
        "by_horizon": {},
        "by_year": {},
        "missing_reasons": {},
        "first_fully_eligible_origin": {},
    }
    for flag in flags:
        out["flags"][flag] = int(wide[flag].sum())
        by_origin = wide.groupby("origin_market")[flag].all()
        eligible = by_origin[by_origin]
        out["first_fully_eligible_origin"][flag] = (
            eligible.index.min().isoformat() if len(eligible) else None
        )
    out["by_horizon"] = {
        str(h): {f: int(g[f].sum()) for f in flags} | {"rows": len(g)}
        for h, g in wide.groupby("horizon")
    }
    out["by_year"] = {
        str(y): {f: int(g[f].sum()) for f in flags} | {"rows": len(g)}
        for y, g in wide.groupby("year")
    }
    for feature in FEATURES:
        reasons = wide[f"{feature.name}__missing_reason"].astype(str)
        counts = {(r or "available"): int(n) for r, n in reasons.value_counts().items()}
        by_h = {
            r: sorted({int(h) for h in wide.loc[reasons == r, "horizon"]})
            for r in counts
            if r != "available"
        }
        out["missing_reasons"][feature.name] = {"counts": counts, "horizons_by_reason": by_h}
    return out


def schema_report(wide: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for col in wide.columns:
        s = wide[col]
        entry: dict[str, Any] = {"column": col, "dtype": str(s.dtype), "nulls": int(s.isna().sum())}
        if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            clean = s.dropna()
            if len(clean):
                entry |= {"min": float(clean.min()), "max": float(clean.max())}
        elif pd.api.types.is_datetime64_any_dtype(s):
            entry |= {"min": str(s.min()), "max": str(s.max())}
        rows.append(entry)
    return rows


def markdown(title: str, profiles: dict[str, Any]) -> str:
    lines = [f"# {title}", ""]
    for profile, rep in profiles.items():
        label = " (SENSITIVITY ONLY)" if profile == "sensitivity_zero_lag" else ""
        lines += [
            f"## Profile `{profile}`{label}",
            "",
            f"Rows: {rep['rows']}",
            "",
            "| Flag | Rows | First fully eligible origin |",
            "|---|---|---|",
        ]
        lines += [
            f"| {f} | {n} | {rep['first_fully_eligible_origin'][f]} |"
            for f, n in rep["flags"].items()
        ]
        lines += [
            "",
            "| Feature | Missingness reasons (rows) | Horizons for each reason |",
            "|---|---|---|",
        ]
        for feat, info in rep["missing_reasons"].items():
            hz = {
                r: (f"{v[0]}..{v[-1]}" if v else "") for r, v in info["horizons_by_reason"].items()
            }
            lines.append(f"| {feat} | {info['counts']} | {hz} |")
        lines += [
            "",
            "| Year | Rows | target | lag24 | lag168 | initial ML |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {y} | {v['rows']} | {v['target_available']} | {v['eligible_lag24_baseline']} | "
            f"{v['eligible_lag168_baseline']} | {v['eligible_initial_ml_features']} |"
            for y, v in rep["by_year"].items()
        ]
        lines.append("")
    return "\n".join(lines) + "\n"


def run_phase3(
    paths: ProjectPaths,
    forecast_cfg: ForecastConfig,
    feature_cfg: FeatureConfig,
    availability: FeatureAvailabilityConfig,
    run_id: str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    run_id = run_id or f"phase3_features_{utc_now_iso().replace(':', '').replace('-', '')}"
    run_dir = paths.results_runs / run_id
    feature_dir = paths.data_processed / "features" / run_id
    audit_dir = paths.data_interim / "feature_audit" / run_id
    for directory in (run_dir, feature_dir, audit_dir):
        if directory.exists():
            raise FileExistsError(f"{directory} exists; run IDs are never reused")
    run_dir.mkdir(parents=True)
    created = utc_now_iso()
    try:
        hourly, phase2 = load_phase2(paths, feature_cfg.input_phase2_run_id)
        contract = source_endpoint_contract_check(hourly)
        if not contract["passed"]:
            raise Phase3CheckError(f"Phase 2 source-endpoint contract violated: {contract}")
        obs = Observations.from_hourly(hourly, PHASE2_TARGET)
        if forecast_cfg.history.end_date is None:
            raise ValueError("history.end_date must be frozen")
        keys = design_keys(forecast_cfg.history.start_date, forecast_cfg.history.end_date)
        years = range(
            forecast_cfg.history.start_date.year - 1, forecast_cfg.history.end_date.year + 2
        )
        public_holidays = vic_public_holidays(years)
        lags = {
            "primary_operational": availability.primary_operational.availability_lag_hours,
            "sensitivity_zero_lag": availability.sensitivity_zero_lag.availability_lag_hours,
        }
        outputs: dict[str, Any] = {}
        reports: dict[str, Any] = {}
        checks: dict[str, Any] = {"phase2_source_endpoint_contract": contract}
        schemas: dict[str, Any] = {}
        for profile in feature_cfg.profiles:
            wide, audit = build_profile(obs, keys, profile, lags[profile], public_holidays)
            checks[profile] = leakage_checks(obs, wide, audit, lags[profile])
            checks[profile]["rows"] = len(wide)
            checks[profile]["expected_rows"] = len(keys)
            checks[profile]["unique_keys"] = not wide.duplicated(["origin_utc", "horizon"]).any()
            checks[profile]["passed"] &= (len(wide) == len(keys)) and checks[profile]["unique_keys"]
            meta = {
                "nem_forecast.run_id": run_id,
                "nem_forecast.availability_profile": profile,
                "nem_forecast.availability_lag_hours": str(lags[profile]),
                "nem_forecast.feature_set_version": str(feature_cfg.feature_set_version),
                "nem_forecast.input_phase2_run_id": feature_cfg.input_phase2_run_id,
            }
            outputs[profile] = {
                "features": write_partitioned(
                    wide, feature_dir / f"profile={profile}", paths.root, meta
                ),
                "audit": write_partitioned(
                    audit, audit_dir / f"profile={profile}", paths.root, meta
                ),
            }
            reports[profile] = eligibility_report(wide)
            schemas[profile] = schema_report(wide)
            logger.info(
                "profile %s: %d rows, checks passed=%s",
                profile,
                len(wide),
                checks[profile]["passed"],
            )
        all_passed = contract["passed"] and all(checks[p]["passed"] for p in feature_cfg.profiles)
        (run_dir / "eligibility_report.json").write_text(json.dumps(reports, indent=2) + "\n")
        (run_dir / "eligibility_report.md").write_text(
            markdown(f"Phase 3 eligibility: {run_id}", reports)
        )
        (run_dir / "schema_report.json").write_text(
            json.dumps(schemas, indent=2, default=str) + "\n"
        )
        (run_dir / "schema_report.md").write_text(
            f"# Phase 3 schema: {run_id}\n\n"
            + "\n".join(
                f"## {p}\n\n| Column | dtype | nulls | min | max |\n|---|---|---|---|---|\n"
                + "\n".join(
                    f"| {c['column']} | {c['dtype']} | {c['nulls']} | "
                    f"{c.get('min', '')} | {c.get('max', '')} |"
                    for c in cols
                )
                + "\n"
                for p, cols in schemas.items()
            )
        )
        (run_dir / "leakage_checks.json").write_text(json.dumps(checks, indent=2) + "\n")
        manifest = {
            "run_id": run_id,
            "phase": "3",
            "status": "complete" if all_passed else "failed_checks",
            "created_utc": created,
            "completed_utc": utc_now_iso(),
            "runtime_seconds": round(time.monotonic() - started, 2),
            "feature_set_version": feature_cfg.feature_set_version,
            "profiles": {
                p: {"availability_lag_hours": lags[p], "label": getattr(availability, p).label}
                for p in feature_cfg.profiles
            },
            "input_phase2": phase2,
            "config_hashes": {f: sha256_file(paths.root / f) for f in CONFIG_FILES},
            "resolved_config": {
                "features": feature_cfg.model_dump(mode="json"),
                "availability": availability.model_dump(mode="json"),
                "forecast_history": forecast_cfg.history.model_dump(mode="json"),
            },
            "holidays": {
                "package_version": importlib.metadata.version("holidays"),
                "country": "AU",
                "subdivision": "VIC",
                "years": [years.start, years.stop - 1],
                "count": len(public_holidays),
            },
            "code": git_state(paths.root),
            "environment": environment(),
            "target_column": TARGET_COLUMN,
            "features": [f.name for f in FEATURES],
            "checks": checks,
            "outputs": outputs,
            "reports": {
                name: sha256_file(run_dir / name)
                for name in (
                    "eligibility_report.json",
                    "eligibility_report.md",
                    "schema_report.json",
                    "schema_report.md",
                    "leakage_checks.json",
                )
            },
        }
        write_json_atomic(run_dir / "run_manifest.json", manifest)
    except Exception as exc:
        write_json_atomic(
            run_dir / "failure_manifest.json",
            {
                "run_id": run_id,
                "status": "failed",
                "created_utc": created,
                "failed_utc": utc_now_iso(),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "code": git_state(paths.root),
            },
        )
        raise
    if not all_passed:
        raise Phase3CheckError(f"Phase 3 checks failed; see {run_dir / 'leakage_checks.json'}")
    return manifest
