"""Compact DuckDB reconciliation of a Phase 3 feature run."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

import duckdb

from nem_forecast.paths import ProjectPaths
from nem_forecast.provenance import read_json, sha256_file

TOLERANCE = 1e-9


def _one(con: duckdb.DuckDBPyConnection, query: str) -> Any:
    row = con.execute(query).fetchone()
    assert row is not None
    return row[0]


def reconcile_features(paths: ProjectPaths, run_id: str) -> dict[str, Any]:
    manifest = read_json(paths.results_runs / run_id / "run_manifest.json")
    for profile_outputs in manifest["outputs"].values():
        for group in profile_outputs.values():
            for out in group:
                if sha256_file(paths.root / out["path"]) != out["sha256"]:
                    raise ValueError(f"output hash mismatch: {out['path']}")
    hourly_files = [
        paths.root / o["path"] for o in manifest["input_phase2"]["canonical_hourly_outputs"]
    ]
    for path, out in zip(
        hourly_files, manifest["input_phase2"]["canonical_hourly_outputs"], strict=True
    ):
        if sha256_file(path) != out["sha256"]:
            raise ValueError(f"Phase 2 input hash mismatch: {out['path']}")
    root = paths.root
    globs = {
        "features_glob": str(
            root / "data/processed/features" / run_id / "profile=*/year=*/part-0.parquet"
        ),
        "audit_glob": str(
            root / "data/interim/feature_audit" / run_id / "profile=*/year=*/part-0.parquet"
        ),
        "hourly_glob": str(Path(hourly_files[0]).parent.parent / "year=*" / "part-0.parquet"),
    }
    sql = (
        resources.files("nem_forecast.features")
        .joinpath("sql", "features_reconcile.sql")
        .read_text()
    )
    for key, value in globs.items():
        sql = sql.replace("{" + key + "}", value.replace("'", "''"))
    con = duckdb.connect()
    con.execute(sql)
    rows = con.execute(
        "SELECT availability_profile, count(*), count(DISTINCT (o, horizon)) "
        "FROM f GROUP BY 1 ORDER BY 1"
    ).fetchall()
    q: dict[str, Any] = {
        "rows_by_profile": {p: {"rows": n, "unique_keys": u} for p, n, u in rows},
        "profile_path_mismatches": _one(
            con,
            "SELECT count(*) FROM f WHERE source_file NOT LIKE "
            "'%profile=' || availability_profile || '/%'",
        ),
        "audit_profile_path_mismatches": _one(
            con,
            "SELECT count(*) FROM a WHERE source_file NOT LIKE "
            "'%profile=' || availability_profile || '/%'",
        ),
        "audit_endpoint_after_cutoff": _one(
            con,
            "SELECT count(*) FROM a WHERE available AND latest_source_endpoint_utc > cutoff_utc",
        ),
        "lag_availability_mismatches": _one(
            con, "SELECT count(*) FROM lag_sql WHERE available_sql <> available_pipe"
        ),
        "lag_max_abs_value_difference": _one(
            con,
            "SELECT coalesce(max(abs(value_sql - value_pipe)), 0) FROM lag_sql WHERE available_sql",
        ),
        "lag_values_checked": _one(con, "SELECT count(*) FROM lag_sql WHERE available_sql"),
        "lag24_h24_unavailable_by_profile": dict(
            con.execute(
                "SELECT availability_profile, count(*) FILTER (WHERE NOT available_sql) "
                "FROM lag_sql "
                "WHERE k = 24 AND horizon = 24 GROUP BY 1 ORDER BY 1"
            ).fetchall()
        ),
        "recent24_availability_mismatches": _one(
            con, "SELECT count(*) FROM recent_sql WHERE (n = 24 AND all_ok) <> available_pipe"
        ),
        "recent24_max_abs_value_difference": _one(
            con,
            "SELECT coalesce(max(abs(mean_sql - mean_pipe)), 0) "
            "FROM recent_sql WHERE n = 24 AND all_ok",
        ),
    }
    expected_rows = manifest["checks"][next(iter(manifest["profiles"]))]["expected_rows"]
    checks = [
        (
            "rows_per_profile",
            all(v["rows"] == expected_rows for v in q["rows_by_profile"].values()),
        ),
        ("unique_keys", all(v["rows"] == v["unique_keys"] for v in q["rows_by_profile"].values())),
        ("both_profiles_present", set(q["rows_by_profile"]) == set(manifest["profiles"])),
        (
            "profile_matches_path",
            q["profile_path_mismatches"] == 0 and q["audit_profile_path_mismatches"] == 0,
        ),
        ("no_endpoint_after_cutoff", q["audit_endpoint_after_cutoff"] == 0),
        ("lag_availability_agrees", q["lag_availability_mismatches"] == 0),
        ("lag_values_agree", q["lag_max_abs_value_difference"] <= TOLERANCE),
        ("recent24_availability_agrees", q["recent24_availability_mismatches"] == 0),
        ("recent24_values_agree", q["recent24_max_abs_value_difference"] <= TOLERANCE),
    ]
    return {
        "run_id": run_id,
        "tolerance": TOLERANCE,
        "results": q,
        "checks": [{"check": n, "passed": bool(p)} for n, p in checks],
        "all_passed": all(p for _, p in checks),
    }
