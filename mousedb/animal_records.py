"""Everything a tracking sheet says about each animal, kept column for column.

WHY THIS EXISTS
---------------
A shared ODC-SCI dataset repeats each animal's details (surgery parameters,
drugs and doses, virus, injury level, survival ...) on every row. The sheet
importer only ever kept a handful of those columns in its typed tables (force,
displacement, velocity, dwell, anesthetic), so an export built from the
database could not carry the rest -- even though the lab had typed it all into
the tracking sheets.

This module copies the animal-level tabs of a cohort's workbook into ONE
long table, ``animal_records`` (subject, tab, record number, column name,
value as text), exactly as written. Long, not wide, on purpose: sheets gain and
rename columns over the years, and a long table takes any column without a
schema change. The per-reach ODC export pivots it back out.

What is copied
--------------
* Project cohort workbooks (one row per animal or per surgery, header in row 1):
  ``0a_Metadata``, ``4_Contusion_Injury_Details``, ``5_SC_Injection_Details``.
  A surgery row that the blank-sheet generator pre-filled (scheduled date,
  surgery type, protocol) but nobody performed carries none of the outcome
  columns in PERFORMED_FIELDS; those rows are planned, not records, and are
  skipped -- the same rule the surgery importer uses.
* Frozen single-letter cohort workbooks: the ``ODC`` tab (ODC-SCI common data
  elements, one row per animal; its header row is found by the "Animal ID"
  cell because rows above it hold notes). Animal numbers are encoded the
  pipeline's way (cohort number = the letter's alphabet position).

Each import REPLACES that cohort's rows for the tabs it read, so a correction
made in the sheet replaces the old value instead of piling up beside it.
Values are text; dates become YYYY-MM-DD; blanks and "?" are not stored.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import Column, DateTime, Integer, String, Text

CNT_TABS = ("0a_Metadata", "4_Contusion_Injury_Details", "5_SC_Injection_Details")
# The scoring tab repeats each animal's sex on every session row (and it is often the
# only place sex is recorded); one Sex record per animal is taken from it.
TRAY_TAB, TRAY_FIELDS = "3b_Manual_Tray", ("Sex",)
ODC_TAB = "ODC"
SUBJECT_COLUMNS = ("Subject_ID", "SubjectID", "Animal", "Animal ID", "Subject", "Mouse ID")

# A performed surgery fills at least one of these; a planned (pre-filled) row fills none.
PERFORMED_FIELDS = {
    "4_Contusion_Injury_Details": ("Actual_kd", "Actual_displacement", "Actual_Velocity",
                                   "Actual_Dwell", "Subject_Weight (g)", "Survived"),
    "5_SC_Injection_Details": ("Injected_Virus", "Virus_Titer", "Injection_Target",
                               "Subject_Weight (g)", "Survived"),
}

_ID = re.compile(r"^[A-Z]+_\d{2}_\d{2}$")


def _table():
    """The ORM class, created lazily so importing this module never touches schema order."""
    from .schema import AnimalRecord
    return AnimalRecord


def text_value(v) -> Optional[str]:
    """A cell as the text stored: dates as YYYY-MM-DD, whole floats without '.0',
    None for blank, NaN or '?' (the frozen workbooks use '?' for 'not recorded')."""
    if v is None:
        return None
    if isinstance(v, datetime):
        if (v.hour, v.minute, v.second, v.microsecond) == (0, 0, 0, 0):
            return v.date().isoformat()
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        if v != v:
            return None
        return str(int(v)) if v.is_integer() else repr(v)
    t = str(v).strip()
    return t if t and t != "?" else None


def _subject(value, cohort_id: str) -> Optional[str]:
    t = text_value(value)
    if t is None:
        return None
    t = t.upper().replace("-", "_")
    if re.fullmatch(r"\d{1,2}", t):
        return "%s_%02d" % (cohort_id, int(t))
    return t if _ID.match(t) and t.startswith(cohort_id + "_") else None


def _records_from_rows(rows, header, tab, cohort_id, subject_col, source, warnings,
                       performed=None) -> List[dict]:
    out, seen = [], {}
    for r in rows:
        if r is None or subject_col >= len(r):
            continue
        sid = _subject(r[subject_col], cohort_id)
        if sid is None:
            if text_value(r[subject_col]) is not None:
                warnings.append("%s: '%s' row with unrecognised animal %r skipped"
                                % (source, tab, r[subject_col]))
            continue
        cells = {header[i]: text_value(v) for i, v in enumerate(r)
                 if i < len(header) and header[i] and i != subject_col}
        cells = {k: v for k, v in cells.items() if v is not None}
        if performed and not any(cells.get(f) for f in performed):
            continue
        if not cells:
            continue
        n = seen[sid] = seen.get(sid, 0) + 1
        for field, value in cells.items():
            out.append({"subject_id": sid, "cohort_id": cohort_id, "source_tab": tab,
                        "record_no": n, "field": field, "value": value, "source_file": source})
    return out


def read_cnt_workbook(path: Path, cohort_id: str, warnings: List[str]) -> List[dict]:
    """Records from a project cohort workbook's animal-level tabs (header in row 1)."""
    import openpyxl
    path = Path(path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: List[dict] = []
    try:
        for tab in CNT_TABS:
            if tab not in wb.sheetnames:
                continue
            it = wb[tab].iter_rows(values_only=True)
            try:
                header = [text_value(h) or "" for h in next(it)]
            except StopIteration:
                continue
            col = next((header.index(c) for c in SUBJECT_COLUMNS if c in header), None)
            if col is None:
                warnings.append("%s: '%s' has no animal id column" % (path.name, tab))
                continue
            out += _records_from_rows(it, header, tab, cohort_id, col, path.name, warnings,
                                      performed=PERFORMED_FIELDS.get(tab))
        if TRAY_TAB in wb.sheetnames:
            out += _tray_fields(wb[TRAY_TAB], cohort_id, path.name)
    finally:
        wb.close()
    return out


def _tray_fields(ws, cohort_id: str, source: str) -> List[dict]:
    """First non-blank value of each TRAY_FIELDS column per animal on the scoring tab."""
    it = ws.iter_rows(values_only=True)
    try:
        header = [text_value(h) or "" for h in next(it)]
    except StopIteration:
        return []
    col = next((header.index(c) for c in SUBJECT_COLUMNS if c in header), None)
    fields = [(header.index(f), f) for f in TRAY_FIELDS if f in header]
    if col is None or not fields:
        return []
    found: Dict[tuple, str] = {}
    for r in it:
        if r is None or col >= len(r):
            continue
        sid = _subject(r[col], cohort_id)
        if sid is None:
            continue
        for i, f in fields:
            v = text_value(r[i]) if i < len(r) else None
            if v is not None and (sid, f) not in found:
                found[(sid, f)] = v
    return [{"subject_id": sid, "cohort_id": cohort_id, "source_tab": TRAY_TAB, "record_no": 1,
             "field": f, "value": v, "source_file": source} for (sid, f), v in found.items()]


def read_odc_tab(path: Path, cohort_id: str, warnings: List[str]) -> List[dict]:
    """Records from a frozen letter-cohort workbook's ODC tab (header row found by 'Animal ID')."""
    import openpyxl
    path = Path(path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if ODC_TAB not in wb.sheetnames:
            return []
        rows = list(wb[ODC_TAB].iter_rows(values_only=True))
    finally:
        wb.close()
    for i, r in enumerate(rows[:15]):
        header = [text_value(h) or "" for h in (r or ())]
        if "Animal ID" in header:
            col = header.index("Animal ID")
            return _records_from_rows(rows[i + 1:], header, ODC_TAB, cohort_id, col, path.name, warnings)
    warnings.append("%s: '%s' tab has no 'Animal ID' header" % (path.name, ODC_TAB))
    return []


def ensure_table(db) -> None:
    """Create animal_records if this database predates it (adds a table; changes nothing else)."""
    _table().__table__.create(db.engine, checkfirst=True)


def replace_cohort(db, cohort_id: str, tabs, records: List[dict]) -> int:
    """Swap this cohort's rows for ``tabs`` with ``records`` in one transaction."""
    AnimalRecord = _table()
    ensure_table(db)
    now = datetime.now()
    with db.session() as s:
        s.query(AnimalRecord).filter(AnimalRecord.cohort_id == cohort_id,
                                     AnimalRecord.source_tab.in_(list(tabs))
                                     ).delete(synchronize_session=False)
        s.bulk_insert_mappings(AnimalRecord, [dict(r, imported_at=now) for r in records])
    return len(records)


def sync_cohort(db, cohort_id: str, sheet: Path, kind: str = "cohort", dry_run: bool = False) -> dict:
    """Read and (unless dry_run) store one cohort's animal records. Never raises:
    returns {"records": n, "warnings": [...], "error": text-or-None}."""
    warnings: List[str] = []
    try:
        if kind == "odc":
            records, tabs = read_odc_tab(sheet, cohort_id, warnings), (ODC_TAB,)
        else:
            records, tabs = read_cnt_workbook(sheet, cohort_id, warnings), CNT_TABS + (TRAY_TAB,)
        n = len(records) if dry_run else replace_cohort(db, cohort_id, tabs, records)
        return {"records": n, "warnings": warnings, "error": None}
    except Exception as e:
        return {"records": 0, "warnings": warnings, "error": "%s: %s" % (type(e).__name__, e)}
