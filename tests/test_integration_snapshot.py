"""Bounded integration audit on the local historical snapshot (not committed data).

Deselected by default; run with ``pytest -m integration``. Audits only the
archives around each observed schema change to keep the run bounded.
"""

import pytest

from nem_forecast.ingestion import snapshot
from nem_forecast.paths import ProjectPaths
from nem_forecast.validation.historical_audit import audit_snapshot

pytestmark = pytest.mark.integration

SNAPSHOT = "DISPATCHREGIONSUM_2022-01_2026-09_retrieved20261014"
BOUNDED_MONTHS = {
    "2022-06",
    "2024-04",
    "2024-05",
    "2024-07",
    "2024-08",
    "2025-03",
    "2025-04",
    "2025-05",
    "2025-06",
    "2026-09",
}


@pytest.fixture(scope="module")
def bounded() -> tuple[dict, object, object]:
    paths = ProjectPaths.from_root()
    try:
        document = snapshot.load_complete_snapshot(paths, "DISPATCHREGIONSUM", SNAPSHOT)
    except Exception as exc:
        pytest.skip(f"local snapshot unavailable: {exc}")
    subset = {
        **document,
        "files": [f for f in document["files"] if f["archive_month"] in BOUNDED_MONTHS],
    }
    return audit_snapshot(paths.root, subset)


def test_snapshot_manifest_complete() -> None:
    paths = ProjectPaths.from_root()
    try:
        document = snapshot.load_complete_snapshot(paths, "DISPATCHREGIONSUM", SNAPSHOT)
    except Exception:
        pytest.skip("local snapshot unavailable")
    assert document["file_count"] == 57
    assert next(f["archive_month"] for f in document["files"]) == "2022-01"
    assert [f["archive_month"] for f in document["files"]][-1] == "2026-09"


def test_bounded_audit_schema_and_selection(bounded: tuple) -> None:
    report, _interventions, quarantined = bounded
    assert report["file_count"] == len(BOUNDED_MONTHS)
    assert set(report["report_versions"]) == {"5", "8", "9"}
    added = {c for ch in report["schema_changes"] for c in ch["added_columns"]}
    assert {"BDU_ENERGY_STORAGE", "BDU_INITIAL_ENERGY_STORAGE"} <= added
    assert report["quarantined_rows"] == 0 and quarantined.empty
    assert report["cross_file_pk_duplicate_rows"] == 0
    vic = report["coverage_by_region"]["VIC1"]
    assert vic["dispatchinterval_mismatches"] == 0
    assert vic["totaldemand_null_all_rows"] == 0
    # June 2022 contains intervention pairs: physical selected exactly once per pair
    statuses = vic["selection_status_counts"]
    assert statuses["physical_selected"] == statuses["pricing_not_selected"] > 0
    assert (
        vic["primary_records"]
        == sum(f["rows_by_region"]["VIC1"] for f in report["files"])
        - statuses["pricing_not_selected"]
    )
    assert report["files"][-1]["settlementdate_max"] == "2026-10-01 00:00:00+10:00"
