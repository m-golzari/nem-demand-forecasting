import datetime as dt

import pytest

from nem_forecast.ingestion.aemo_csv import parse_aemo_csv_text
from nem_forecast.ingestion.aemo_normalise import SourceRef, normalise_vic1
from nem_forecast.validation.aemo_audit import audit_dispatchregionsum
from tests.aemo_fixtures import full_day_rows, render, row

DAY = dt.date(2024, 4, 4)
NEXT = dt.date(2024, 4, 5)
SRC = SourceRef("data/raw/x.zip", "0" * 64)


def _rows_with_extras() -> list[list[str]]:
    rows = full_day_rows(DAY)
    # interval outside the window on either side (must be excluded, not dropped silently)
    rows.append(row(dt.datetime(2024, 4, 4, 0, 0)))
    rows.append(row(dt.datetime(2024, 4, 5, 0, 5)))
    return rows


def test_vic1_filter_window_and_reconciliation() -> None:
    table = parse_aemo_csv_text(render(_rows_with_extras()))
    frame, rec = normalise_vic1(table, SRC, DAY, NEXT)
    assert (frame["region_id"] == "VIC1").all()
    assert len(frame) == 288
    assert rec == {
        "raw_data_rows": 578,
        "raw_rows_unparsed_settlementdate": 0,
        "rows_in_window_all_regions": 576,
        "rows_in_window_by_region": {"VIC1": 288, "NSW1": 288},
        "rows_in_window_vic1": 288,
        "rows_written": 288,
        "rows_dropped_after_vic1_filter": 0,
        "selection_status_counts": {"ordinary_single": 288},
    }
    assert frame["settlementdate_source"].iloc[0] == "2024/04/04 00:05:00"
    assert frame["settlementdate_source"].iloc[-1] == "2024/04/05 00:00:00"
    assert str(frame["interval_start_utc"].iloc[0]) == "2024-04-03 14:00:00+00:00"
    assert str(frame["hour_start_market"].iloc[-1]) == "2024-04-04 23:00:00+10:00"
    assert not frame.filter(like="q_").any().any()


def test_intervention_pairs_and_duplicates_are_flagged_not_dropped() -> None:
    end = dt.datetime(2024, 4, 4, 12, 0)
    rows = full_day_rows(DAY, ("VIC1",))
    rows.append(row(end, intervention=1, demand="4100"))  # physical-run counterpart
    rows.append(row(dt.datetime(2024, 4, 4, 13, 0)))  # exact primary-key duplicate
    table = parse_aemo_csv_text(render(rows))
    frame, rec = normalise_vic1(table, SRC, DAY, NEXT)
    assert rec["rows_written"] == 290  # nothing removed
    assert frame["q_has_intervention_counterpart"].sum() == 2
    assert frame["q_pk_duplicate"].sum() == 2
    audit = audit_dispatchregionsum(table, DAY, NEXT, {"file": "x"})
    vic = audit["window"]["vic1"]
    assert vic["intervention_counts"] == {"0": 289, "1": 1}
    assert vic["intervention_pair_rows"] == 2
    assert vic["pk_duplicates"] == 2
    assert vic["intervention_pair_max_abs_totaldemand_diff_mw"] == pytest.approx(
        abs(4100 - 4144.25)
    )


def test_anomalies_flagged() -> None:
    rows = full_day_rows(DAY, ("VIC1",))
    rows[0][5] = ""  # null demand
    rows[1][5] = "-5"  # non-positive
    rows[2][3] = "20240404999"  # inconsistent DISPATCHINTERVAL
    table = parse_aemo_csv_text(render(rows))
    frame, _ = normalise_vic1(table, SRC, DAY, NEXT)
    assert frame["q_demand_null"].sum() == 1
    assert frame["q_demand_nonpositive"].sum() == 1
    assert frame["q_dispatchinterval_mismatch"].sum() == 1
    assert frame["totaldemand_source"].iloc[1] == "-5"  # raw value preserved


def test_audit_reports_missing_intervals_and_spacing() -> None:
    rows = [r for r in full_day_rows(DAY, ("VIC1",)) if r[0] != "2024/04/04 06:00:00"]
    audit = audit_dispatchregionsum(parse_aemo_csv_text(render(rows)), DAY, NEXT, {"file": "x"})
    vic = audit["window"]["vic1"]
    assert vic["missing_intervals"] == ["2024-04-04T06:00:00+10:00"]
    assert vic["spacing_minutes_counts"] == {"5": 285, "10": 1}
    assert vic["hourly_record_counts_by_intervention"]["0"] == {"11": 1, "12": 23}
    assert len(vic["first_canonical_hour_member_settlementdates"]) == 12


def test_audit_file_level_and_columns() -> None:
    audit = audit_dispatchregionsum(
        parse_aemo_csv_text(render(_rows_with_extras())), DAY, NEXT, {"file": "x"}
    )
    assert audit["file_level"]["data_rows"] == 578
    assert audit["file_level"]["end_of_report_declared_rows"] == 581  # AEMO counts all lines
    kinds = {c["column"]: c["source_kind"] for c in audit["columns"]}
    assert kinds["SETTLEMENTDATE"].startswith("datetime_string")
    assert kinds["UNUSEDFIELD"] == "all_empty"
    assert kinds["TOTALDEMAND"] == "decimal"
    assert kinds["REGIONID"] == "string"


def test_window_without_vic1_fails() -> None:
    table = parse_aemo_csv_text(render(full_day_rows(DAY, ("NSW1",))))
    with pytest.raises(ValueError, match="no VIC1"):
        normalise_vic1(table, SRC, DAY, NEXT)


def test_missing_required_column_fails() -> None:
    text = render(full_day_rows(DAY)).replace("TOTALDEMAND", "OTHERDEMAND")
    with pytest.raises(ValueError, match="TOTALDEMAND"):
        normalise_vic1(parse_aemo_csv_text(text), SRC, DAY, NEXT)
