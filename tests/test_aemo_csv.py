import datetime as dt
from pathlib import Path

import pytest

from nem_forecast.ingestion.aemo_csv import (
    SOURCE_LINE,
    AemoCsvError,
    parse_aemo_csv_text,
    read_aemo_zip,
)
from tests.aemo_fixtures import COLUMNS, render, row, write_zip

END = dt.datetime(2024, 4, 4, 0, 5)


def test_parses_header_data_and_comments() -> None:
    table = parse_aemo_csv_text(render([row(END), row(END, region="NSW1")]))
    assert (table.report_type, table.report_subtype, table.report_version) == (
        "DISPATCH",
        "REGIONSUM",
        "5",
    )
    assert table.columns == COLUMNS
    assert len(table.data) == 2
    assert table.data.loc[0, "SETTLEMENTDATE"] == "2024/04/04 00:05:00"  # quotes removed, text kept
    assert table.data.loc[0, "UNUSEDFIELD"] == ""
    assert list(table.data[SOURCE_LINE]) == [3, 4]
    assert table.comments[-1][:2] == ["C", "END OF REPORT"]


def test_values_stay_strings() -> None:
    table = parse_aemo_csv_text(render([row(END, demand="4000.50000")]))
    assert table.data.loc[0, "TOTALDEMAND"] == "4000.50000"


@pytest.mark.parametrize(
    "text",
    [
        "C,x\nD,DISPATCH,REGIONSUM,5,a\n",  # data before header
        "C,x\n",  # no header
        "I,DISPATCH,REGIONSUM,5,A,B\nD,DISPATCH,REGIONSUM,5,1\n",  # wrong width
        "I,DISPATCH,REGIONSUM,5,A\nD,DISPATCH,OTHER,5,1\n",  # report mismatch
        "I,DISPATCH,REGIONSUM,5,A\nX,1\n",  # unknown record type
        "I,DISPATCH,REGIONSUM,5,A\nI,DISPATCH,REGIONSUM,6,A\n",  # second different header
        "I,DISPATCH,REGIONSUM,5,A,A\n",  # duplicate column
    ],
)
def test_malformed_files_fail_loudly(text: str) -> None:
    with pytest.raises(AemoCsvError):
        parse_aemo_csv_text(text)


def test_read_zip(tmp_path: Path) -> None:
    path = write_zip(tmp_path / "a.zip", render([row(END)]))
    assert len(read_aemo_zip(path).data) == 1


def test_zip_with_two_members_rejected(tmp_path: Path) -> None:
    import zipfile

    path = tmp_path / "b.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("a.CSV", render([row(END)]))
        archive.writestr("b.CSV", render([row(END)]))
    with pytest.raises(AemoCsvError, match="exactly one"):
        read_aemo_zip(path)
