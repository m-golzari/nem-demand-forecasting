"""Historical audit on a synthetic snapshot with a schema change and interventions."""

import datetime as dt
from pathlib import Path

import pytest

from nem_forecast.provenance import sha256_file
from nem_forecast.validation.historical_audit import audit_snapshot
from tests.aemo_fixtures import month_rows, render, render_v6, row, write_zip


def _snapshot(tmp_path: Path, june_rows: list[list[str]], july_text: str) -> dict:
    files = []
    for month, text in (("2024-06", render(june_rows)), ("2024-07", july_text)):
        path = write_zip(tmp_path / f"{month}.zip", text)
        files.append(
            {
                "archive_month": month,
                "path": path.name,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return {"snapshot_id": "sid", "files": files, "total_bytes": sum(f["bytes"] for f in files)}


def test_counts_schema_change_and_intervention_differences(tmp_path: Path) -> None:
    june = month_rows(2024, 6, ("VIC1", "NSW1"))
    june.append(row(dt.datetime(2024, 6, 10, 12, 0), intervention=1, demand="4010.5"))
    june.append(row(dt.datetime(2024, 6, 10, 12, 5), intervention=1, demand="4000.5"))
    report, interventions, quarantined = audit_snapshot(
        tmp_path, _snapshot(tmp_path, june, render_v6(month_rows(2024, 7, ("VIC1", "NSW1"))))
    )
    assert report["file_count"] == 2
    assert report["report_versions"] == {"5": 1, "6": 1}
    change = report["schema_changes"][0]
    assert change["added_columns"] == ["NEWFIELD"] and change["report_version"] == ["5", "6"]
    vic = report["coverage_by_region"]["VIC1"]
    assert vic["expected_intervals"] == (30 + 31) * 288
    assert vic["missing_intervals"] == 0 and vic["spacing_minutes_counts"] == {"5": 61 * 288 - 1}
    assert vic["market_days_not_288_primary"] == {}
    assert vic["selection_status_counts"]["physical_selected"] == 2
    iv = report["intervention"]["by_region"]["VIC1"]
    assert iv["intervention_intervals"] == 2 and iv["intervals_with_nonzero_diff"] == 1
    assert iv["abs_diff_mw"]["q100"] == pytest.approx(10.0)
    assert report["intervention"]["vic1_episodes"] == [
        {
            "first_end": "2024-06-10T12:00:00+10:00",
            "last_end": "2024-06-10T12:05:00+10:00",
            "intervals": 2,
        }
    ]
    assert len(interventions) == 4 and quarantined.empty
    assert report["files"][0]["end_of_report_matches_line_count"]
    assert report["files"][0]["rows_outside_month_support"] == 0


def test_gaps_and_quarantine_reported(tmp_path: Path) -> None:
    june = [r for r in month_rows(2024, 6) if r[0] != "2024/06/15 06:00:00"]
    june.append(row(dt.datetime(2024, 6, 20, 1, 0), intervention=1))  # extra physical row -> pair
    june.append(row(dt.datetime(2024, 6, 21, 1, 0)))  # duplicate pricing row -> quarantine
    report, _, quarantined = audit_snapshot(
        tmp_path, _snapshot(tmp_path, june, render(month_rows(2024, 7)))
    )
    vic = report["coverage_by_region"]["VIC1"]
    assert vic["missing_intervals"] == 1
    assert vic["missing_examples"] == ["2024-06-15T06:00:00+10:00"]
    assert vic["market_days_not_288_primary"] == {"2024-06-15": 287, "2024-06-21": 287}
    assert report["quarantined_rows"] == 2 and len(quarantined) == 2
    assert report["cross_file_pk_duplicate_rows"] == 2


def test_hash_mismatch_refused(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path, month_rows(2024, 6), render(month_rows(2024, 7)))
    snap["files"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash inconsistency"):
        audit_snapshot(tmp_path, snap)
