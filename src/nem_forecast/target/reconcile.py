"""Independent reconciliation of a Phase 2 run with DuckDB.

Raw archives are re-read with :mod:`csv` (not the pipeline's AEMO reader);
DuckDB SQL re-derives primary selection, the canonical hour of each record and
the hourly means, then compares them with the run's Parquet outputs.
"""

from __future__ import annotations

import csv
import io
import zipfile
from importlib import resources
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from nem_forecast.ingestion import snapshot
from nem_forecast.paths import ProjectPaths
from nem_forecast.provenance import read_json, sha256_file

TOLERANCE_MW = 1e-9


def read_raw_vic1(path: Path) -> tuple[pd.DataFrame, int]:
    """Return VIC1 source strings (date, runno, intervention, demand) and the D-row count."""
    rows: list[tuple[str, str, str, str]] = []
    total = 0
    with zipfile.ZipFile(path) as archive:
        (member,) = [n for n in archive.namelist() if n.upper().endswith(".CSV")]
        with archive.open(member) as raw:
            index: dict[str, int] = {}
            for record in csv.reader(io.TextIOWrapper(raw, encoding="utf-8")):
                if not record:
                    continue
                if record[0] == "I":
                    index = {name: i for i, name in enumerate(record)}
                elif record[0] == "D":
                    total += 1
                    if record[index["REGIONID"]] == "VIC1":
                        rows.append(
                            (
                                record[index["SETTLEMENTDATE"]],
                                record[index["RUNNO"]],
                                record[index["INTERVENTION"]],
                                record[index["TOTALDEMAND"]],
                            )
                        )
    frame = pd.DataFrame(rows, columns=["settlementdate", "runno", "intervention", "totaldemand"])
    return frame, total


def _sql(name: str, **globs: str) -> str:
    text = resources.files("nem_forecast.target").joinpath("sql", name).read_text(encoding="utf-8")
    for key, value in globs.items():
        text = text.replace("{" + key + "}", value.replace("'", "''"))
    return text


def _one(con: duckdb.DuckDBPyConnection, query: str) -> Any:
    row = con.execute(query).fetchone()
    assert row is not None
    return row[0]


def reconcile_run(paths: ProjectPaths, run_id: str) -> dict[str, Any]:
    run_dir = paths.results_runs / run_id
    manifest = read_json(run_dir / "run_manifest.json")
    for group in manifest["outputs"].values():  # outputs unchanged since the run
        for out in group:
            if sha256_file(paths.root / out["path"]) != out["sha256"]:
                raise ValueError(f"output hash mismatch: {out['path']}")
    lock = snapshot.load_lock(paths.root / manifest["lock_manifest"]["path"])
    if (
        sha256_file(paths.root / manifest["lock_manifest"]["path"])
        != manifest["lock_manifest"]["sha256"]
    ):
        raise ValueError("lock manifest changed since the run")
    archives = snapshot.verify_lock_against_local(paths, lock)
    frames, raw_total = [], 0
    for path in archives:
        frame, total = read_raw_vic1(path)
        frames.append(frame)
        raw_total += total
    raw_vic1_input = pd.concat(frames, ignore_index=True)

    def glob(key: str) -> str:
        base = paths.root / Path(manifest["outputs"][key][0]["path"]).parent.parent
        return str(base / "year=*" / "part-0.parquet")

    globs = {
        "intervals_glob": glob("intervals"),
        "canonical_glob": glob("canonical_hourly"),
        "model_ready_glob": glob("model_ready"),
    }
    views_sql = _sql("vic1_hourly_views.sql", **globs)
    con = duckdb.connect()
    con.register("raw_vic1_input", raw_vic1_input)
    con.execute(views_sql)
    con.execute(_sql("vic1_hourly_reconcile.sql"))
    (run_dir / "duckdb_views.sql").write_text(views_sql, encoding="utf-8")

    q = {
        "raw_rows_all_regions": raw_total,
        "raw_vic1_records": _one(con, "SELECT count(*) FROM raw_typed"),
        "sql_selected_records": _one(con, "SELECT count(*) FROM raw_selected"),
        "sql_unselected_records": _one(
            con, "SELECT (SELECT count(*) FROM raw_typed) - (SELECT count(*) FROM raw_selected)"
        ),
        "sql_quarantined_intervals": _one(
            con,
            "SELECT count(*) FROM raw_quarantined_intervals",
        ),
        "sql_intervention_intervals": _one(
            con, "SELECT count(*) FROM raw_groups WHERE n = 2 AND n_physical = 1 AND n_pricing = 1"
        ),
        "sql_hours": _one(con, "SELECT count(*) FROM sql_hourly"),
        "sql_complete_hours": _one(con, "SELECT count(*) FROM sql_hourly_complete"),
        "pipeline_hours": _one(con, "SELECT count(*) FROM vic1_canonical_hourly"),
        "pipeline_complete_hours": _one(
            con, "SELECT count(*) FROM vic1_canonical_hourly WHERE is_complete"
        ),
        "pipeline_model_ready_rows": _one(con, "SELECT count(*) FROM vic1_model_ready"),
        "pipeline_interval_rows": _one(con, "SELECT count(*) FROM vic1_intervals"),
        "pipeline_intervention_affected_hours": _one(
            con, "SELECT count(*) FROM vic1_canonical_hourly WHERE intervention_affected"
        ),
        "sql_intervention_affected_hours": _one(
            con,
            "SELECT count(*) FROM sql_intervention_hours",
        ),
        "completeness_disagreements": _one(
            con,
            """
            SELECT count(*) FROM vic1_canonical_hourly p
            FULL OUTER JOIN sql_hourly_complete s USING (hour_market_naive)
            WHERE coalesce(p.is_complete, false) <> (s.hour_market_naive IS NOT NULL)""",
        ),
        "model_ready_hours_missing_from_sql": _one(
            con,
            """
            SELECT count(*) FROM vic1_model_ready m
            ANTI JOIN sql_hourly_complete s USING (hour_market_naive)""",
        ),
        "max_abs_target_difference_mw": _one(
            con,
            """
            SELECT coalesce(max(abs(m.dispatch_totaldemand_hourly_mean_mw - s.mean_mw)), 0)
            FROM vic1_model_ready m JOIN sql_hourly_complete s USING (hour_market_naive)""",
        ),
        "first_model_ready_hour_market": str(
            _one(con, "SELECT min(hour_market_naive) FROM vic1_model_ready")
        ),
        "last_model_ready_hour_market": str(
            _one(con, "SELECT max(hour_market_naive) FROM vic1_model_ready")
        ),
    }
    yearly = con.execute("""
        SELECT year, count(*) AS hours,
               round(avg(dispatch_totaldemand_hourly_mean_mw), 4) AS mean_mw,
               round(min(dispatch_totaldemand_hourly_mean_mw), 4) AS min_mw,
               round(max(dispatch_totaldemand_hourly_mean_mw), 4) AS max_mw
        FROM vic1_model_ready GROUP BY year ORDER BY year""").fetchall()
    q["model_ready_yearly"] = [
        dict(zip(["year", "hours", "mean_mw", "min_mw", "max_mw"], r, strict=True)) for r in yearly
    ]
    rc = manifest["row_counts"]
    comparisons = [
        ("raw_rows_all_regions", rc["raw_rows_all_regions"], q["raw_rows_all_regions"]),
        ("raw_vic1_records", rc["raw_vic1_records"], q["raw_vic1_records"]),
        ("interval_rows_written", rc["raw_vic1_records"], q["pipeline_interval_rows"]),
        ("selected_records", rc["selected_primary_records"], q["sql_selected_records"]),
        ("unselected_records", rc["unselected_records"], q["sql_unselected_records"]),
        (
            "quarantined_records_vs_intervals",
            rc["quarantined_records"] == 0,
            q["sql_quarantined_intervals"] == 0,
        ),
        ("hours", rc["hourly_records"], q["pipeline_hours"]),
        ("complete_hours", rc["complete_hours"], q["sql_complete_hours"]),
        ("model_ready_rows", rc["model_ready_rows"], q["pipeline_model_ready_rows"]),
        (
            "intervention_affected_hours",
            rc["intervention_affected_hours"],
            q["sql_intervention_affected_hours"],
        ),
        ("completeness_disagreements", 0, q["completeness_disagreements"]),
        ("model_ready_hours_missing_from_sql", 0, q["model_ready_hours_missing_from_sql"]),
        ("target_within_tolerance", True, q["max_abs_target_difference_mw"] <= TOLERANCE_MW),
    ]
    checks = [
        {"check": n, "pipeline": a, "independent": b, "passed": a == b} for n, a, b in comparisons
    ]
    return {
        "run_id": run_id,
        "tolerance_mw": TOLERANCE_MW,
        "independent": q,
        "checks": checks,
        "all_passed": all(c["passed"] for c in checks),
    }
