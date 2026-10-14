"""Reader for AEMO's multi-record CSV format inside MMSDM zip archives.

Each line starts with a record type: ``C`` (comment/control), ``I`` (column
header: record type, report type, report subtype, report version, columns...)
or ``D`` (data, same layout as its ``I`` row). Values are kept as the exact
source strings so that raw values can be audited before any type conversion.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

SOURCE_LINE = "source_line"
_PREFIX = ("record_type", "report_type", "report_subtype", "report_version")


class AemoCsvError(ValueError):
    """Raised when a file does not follow the expected AEMO CSV structure."""


@dataclass
class AemoTable:
    member: str
    report_type: str
    report_subtype: str
    report_version: str
    columns: list[str]
    data: pd.DataFrame  # all source values as str ("" for empty), plus SOURCE_LINE
    comments: list[list[str]] = field(default_factory=list)


def parse_aemo_csv_text(text: str, member: str = "<text>") -> AemoTable:
    header: list[str] | None = None
    rows: list[list[str]] = []
    lines: list[int] = []
    comments: list[list[str]] = []
    for line_no, record in enumerate(csv.reader(io.StringIO(text)), start=1):
        if not record:
            continue
        kind = record[0]
        if kind == "C":
            comments.append(record)
        elif kind == "I":
            if header is not None and record != header:
                raise AemoCsvError(f"{member}: multiple different I (header) rows; not supported")
            header = record
        elif kind == "D":
            if header is None:
                raise AemoCsvError(f"{member}: D row at line {line_no} before any I row")
            if len(record) != len(header) or record[1:4] != header[1:4]:
                raise AemoCsvError(f"{member}: D row at line {line_no} does not match I row")
            rows.append(record[len(_PREFIX) :])
            lines.append(line_no)
        else:
            raise AemoCsvError(f"{member}: unknown record type {kind!r} at line {line_no}")
    if header is None:
        raise AemoCsvError(f"{member}: no I (header) row")
    columns = header[len(_PREFIX) :]
    if len(set(columns)) != len(columns):
        raise AemoCsvError(f"{member}: duplicate column names in I row")
    frame = pd.DataFrame(rows, columns=columns, dtype="string")
    frame[SOURCE_LINE] = pd.array(lines, dtype="int64")
    return AemoTable(
        member=member,
        report_type=header[1],
        report_subtype=header[2],
        report_version=header[3],
        columns=columns,
        data=frame,
        comments=comments,
    )


def read_aemo_zip(path: Path) -> AemoTable:
    """Parse the single CSV member of an MMSDM table archive."""
    with zipfile.ZipFile(path) as archive:
        members = [n for n in archive.namelist() if n.upper().endswith(".CSV")]
        if len(members) != 1:
            raise AemoCsvError(f"{path.name}: expected exactly one CSV member, found {members}")
        text = archive.read(members[0]).decode("utf-8")
    return parse_aemo_csv_text(text, member=members[0])
