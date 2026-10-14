"""Synthetic AEMO-format DISPATCHREGIONSUM fixtures (no real AEMO data)."""

from __future__ import annotations

import datetime as dt
import zipfile
from pathlib import Path

COLUMNS = [
    "SETTLEMENTDATE",
    "RUNNO",
    "REGIONID",
    "DISPATCHINTERVAL",
    "INTERVENTION",
    "TOTALDEMAND",
    "UNUSEDFIELD",
    "LASTCHANGED",
]


def dispatchinterval(end: dt.datetime) -> int:
    """Independent re-statement of AEMO's YYYYMMDDPPP (trading day 04:00-04:00)."""
    start = end - dt.timedelta(minutes=5) - dt.timedelta(hours=4)
    period = (start.hour * 60 + start.minute) // 5 + 1
    return int(start.strftime("%Y%m%d")) * 1000 + period


def row(
    end: dt.datetime,
    region: str = "VIC1",
    intervention: int = 0,
    demand: str = "4000.5",
    lastchanged: dt.datetime | None = None,
) -> list[str]:
    changed = lastchanged or (end - dt.timedelta(minutes=5) + dt.timedelta(seconds=3))
    return [
        end.strftime("%Y/%m/%d %H:%M:%S"),
        "1",
        region,
        str(dispatchinterval(end)),
        str(intervention),
        demand,
        "",
        changed.strftime("%Y/%m/%d %H:%M:%S"),
    ]


def render(rows: list[list[str]]) -> str:
    lines = ["C,SETP.WORLD,DVD_DISPATCHREGIONSUM,AEMO,PUBLIC,2024/05/07,00:15:15,1,,2"]
    lines.append("I,DISPATCH,REGIONSUM,5," + ",".join(COLUMNS))
    for values in rows:
        quoted = [f'"{v}"' if "/" in v else v for v in values]
        lines.append("D,DISPATCH,REGIONSUM,5," + ",".join(quoted))
    lines.append(f'C,"END OF REPORT",{len(lines) + 1}')
    return "\n".join(lines) + "\n"


def full_day_rows(day: dt.date, regions: tuple[str, ...] = ("VIC1", "NSW1")) -> list[list[str]]:
    rows = []
    for i in range(1, 289):
        end = dt.datetime.combine(day, dt.time(0)) + i * dt.timedelta(minutes=5)
        rows.extend(row(end, region=r, demand=f"{4000 + i}.25") for r in regions)
    return rows


def write_zip(
    path: Path, text: str, member: str = "PUBLIC_DVD_DISPATCHREGIONSUM_202404010000.CSV"
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, text)
    return path


LISTING_HTML = """<html><body><pre>
<A HREF="/Data_Archive/Wholesale_Electricity/MMSDM/2024/MMSDM_2024_04/MMSDM_Historical_Data_SQLLoader/">[To Parent Directory]</A><br><br>
       Saturday, May 11, 2024 10:03 PM      4571235 <A HREF="/Data_Archive/Wholesale_Electricity/MMSDM/2024/MMSDM_2024_04/MMSDM_Historical_Data_SQLLoader/DATA/PUBLIC_DVD_DISPATCHREGIONSUM_202404010000.zip">PUBLIC_DVD_DISPATCHREGIONSUM_202404010000.zip</A><br>
       Sunday, May 12, 2024 12:27 AM       777187 <A HREF="/Data_Archive/Wholesale_Electricity/MMSDM/2024/MMSDM_2024_04/MMSDM_Historical_Data_SQLLoader/DATA/PUBLIC_DVD_PREDISPATCHREGIONSUM_D_202404010000.zip">PUBLIC_DVD_PREDISPATCHREGIONSUM_D_202404010000.zip</A><br>
</pre></body></html>"""

LISTING_HTML_ARCHIVE_FORMAT = """<pre>
       Tuesday, May 19, 2026 11:01 AM      5845889 <A HREF="/Data_Archive/Wholesale_Electricity/MMSDM/2024/MMSDM_2024_08/MMSDM_Historical_Data_SQLLoader/DATA/PUBLIC_ARCHIVE#DISPATCHREGIONSUM#FILE01#202408010000.zip">PUBLIC_ARCHIVE#DISPATCHREGIONSUM#FILE01#202408010000.zip</A><br>
</pre>"""


ARCHIVE_ROOT = "/Data_Archive/Wholesale_Electricity/MMSDM"


def listing_for(year: int, month: int, entries: list[tuple[str, int]]) -> str:
    """IIS-style NEMWeb listing for one month's DATA directory."""
    base = f"{ARCHIVE_ROOT}/{year:04d}/MMSDM_{year:04d}_{month:02d}/MMSDM_Historical_Data_SQLLoader/DATA/"
    rows = [
        f'       Saturday, May 11, 2024 10:03 PM {size:>12} <A HREF="{base}{name}">{name}</A><br>'
        for name, size in entries
    ]
    return "<pre>\n" + "\n".join(rows) + "\n</pre>"


def dvd_name(year: int, month: int) -> str:
    return f"PUBLIC_DVD_DISPATCHREGIONSUM_{year:04d}{month:02d}010000.zip"


def archive_name(year: int, month: int, part: int = 1) -> str:
    return f"PUBLIC_ARCHIVE#DISPATCHREGIONSUM#FILE{part:02d}#{year:04d}{month:02d}010000.zip"


def zip_bytes(text: str, member: str = "DATA.CSV") -> bytes:
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, text)
    return buffer.getvalue()


def month_rows(year: int, month: int, regions: tuple[str, ...] = ("VIC1",)) -> list[list[str]]:
    """Every interval-ending record whose interval lies in the calendar month."""
    start = dt.datetime(year, month, 1)
    end = dt.datetime(year + (month == 12), month % 12 + 1, 1)
    rows = []
    t = start + dt.timedelta(minutes=5)
    while t <= end:
        rows.extend(row(t, region=r, demand="4000.5") for r in regions)
        t += dt.timedelta(minutes=5)
    return rows


def render_v6(rows: list[list[str]]) -> str:
    """Same table with report version 6 and one extra trailing column (schema change)."""
    lines = ["C,SETP.WORLD,DVD_DISPATCHREGIONSUM,AEMO,PUBLIC,2024/09/07,00:15:15,1,,2"]
    lines.append("I,DISPATCH,REGIONSUM,6," + ",".join([*COLUMNS, "NEWFIELD"]))
    for values in rows:
        quoted = [f'"{v}"' if "/" in v else v for v in values]
        lines.append("D,DISPATCH,REGIONSUM,6," + ",".join([*quoted, "1"]))
    lines.append(f'C,"END OF REPORT",{len(lines) + 1}')
    return "\n".join(lines) + "\n"
