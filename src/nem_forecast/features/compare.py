"""Compare two Phase 3 runs: byte identity vs logical identity.

Parquet files embed run-specific schema metadata (``nem_forecast.run_id``), so
two correct builds of the same inputs differ in bytes. Logical identity is
judged on the Arrow schema without metadata, row counts, exact frame equality,
and a content hash (``hash_pandas_object`` over values, plus names and dtypes).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from nem_forecast.paths import ProjectPaths
from nem_forecast.provenance import read_json, sha256_file

REPORTS = (
    "eligibility_report.json",
    "eligibility_report.md",
    "schema_report.json",
    "schema_report.md",
)


def logical_hash(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update("|".join(f"{c}:{t}" for c, t in frame.dtypes.astype(str).items()).encode())
    digest.update(pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes())
    return digest.hexdigest()


def _dataset(
    paths: ProjectPaths, outputs: list[dict[str, Any]]
) -> tuple[pd.DataFrame, list[Any], list[dict[bytes, bytes]]]:
    files = [paths.root / o["path"] for o in outputs]
    schemas = [pq.read_schema(f) for f in files]
    frame = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    return frame, [s.remove_metadata() for s in schemas], [dict(s.metadata or {}) for s in schemas]


def _normalised(path: Path, run_id: str) -> str:
    """Report text with the run ID removed from the title line (its only occurrence)."""
    title, _, body = path.read_text(encoding="utf-8").partition("\n")
    if title.endswith(f": {run_id}"):
        title = title[: -len(run_id)] + "<run_id>"
    return title + "\n" + body


def compare_runs(paths: ProjectPaths, run_a: str, run_b: str) -> dict[str, Any]:
    man_a = read_json(paths.results_runs / run_a / "run_manifest.json")
    man_b = read_json(paths.results_runs / run_b / "run_manifest.json")
    datasets: dict[str, Any] = {}
    for profile in man_a["outputs"]:
        for kind in ("features", "audit"):
            out_a, out_b = man_a["outputs"][profile][kind], man_b["outputs"][profile][kind]
            frame_a, schema_a, meta_a = _dataset(paths, out_a)
            frame_b, schema_b, meta_b = _dataset(paths, out_b)
            try:
                pd.testing.assert_frame_equal(frame_a, frame_b, check_exact=True)
                frames_equal = True
            except AssertionError:
                frames_equal = False
            meta_diff = sorted(
                {
                    k.decode()
                    for a, b in zip(meta_a, meta_b, strict=True)
                    for k in set(a) | set(b)
                    if a.get(k) != b.get(k)
                }
            )
            datasets[f"{profile}/{kind}"] = {
                "files": len(out_a),
                "bytes_identical": [o["sha256"] for o in out_a] == [o["sha256"] for o in out_b],
                "schema_without_metadata_identical": all(
                    a.equals(b) for a, b in zip(schema_a, schema_b, strict=True)
                ),
                "metadata_keys_differing": meta_diff,
                "rows": [len(frame_a), len(frame_b)],
                "frames_exactly_equal": frames_equal,
                "logical_hash": [logical_hash(frame_a), logical_hash(frame_b)],
            }
    reports = {}
    for name in REPORTS:
        a, b = paths.results_runs / run_a / name, paths.results_runs / run_b / name
        same_text = _normalised(a, run_a) == _normalised(b, run_b)
        reports[name] = {
            "bytes_identical": sha256_file(a) == sha256_file(b),
            "identical_after_run_id_normalisation": same_text,
        }
    leak_a, leak_b = (
        read_json(paths.results_runs / r / "leakage_checks.json") for r in (run_a, run_b)
    )
    for doc in (leak_a, leak_b):
        for value in doc.values():
            if isinstance(value, dict):
                value.pop("reference_seconds", None)
    reports["leakage_checks.json (excluding timing)"] = {"identical": leak_a == leak_b}
    logical = all(
        d["schema_without_metadata_identical"]
        and d["frames_exactly_equal"]
        and d["logical_hash"][0] == d["logical_hash"][1]
        for d in datasets.values()
    )
    return {
        "run_a": run_a,
        "run_b": run_b,
        "code_commits": [man_a["code"]["commit"], man_b["code"]["commit"]],
        "all_bytes_identical": all(d["bytes_identical"] for d in datasets.values()),
        "all_logically_identical": logical
        and all(
            r.get("identical_after_run_id_normalisation", r.get("identical"))
            for r in reports.values()
        ),
        "datasets": datasets,
        "reports": reports,
    }
