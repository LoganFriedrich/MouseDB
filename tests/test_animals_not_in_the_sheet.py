"""An animal the database holds, that its cohort's sheet no longer names and
that nothing is recorded for, is put in front of a person.

WHY: an import never removes an animal (an animal can exist because of its
videos alone), and a subject records no origin. So an id typed into a sheet,
imported, and then taken out of the sheet again stayed in the database for
good as an empty animal -- counted in the cohort, shown in the GUI, with
nothing anywhere saying it is not real. Only a person can tell an animal that
never existed from one whose data has not arrived yet, so the import names it
(warning, sheet_sync, the attention box and the cohort's FIX THESE file) and
removes nothing.
"""
import json
from datetime import date

import openpyxl
import pytest
from sqlalchemy import text

from mousedb import attention, sheet_sync
from mousedb.database import init_database
from mousedb.importers import ExcelImporter
from mousedb.schema import Cohort, Project, Subject, Weight

COHORT = "PROJA_01"
NAMED = ("PROJA_01_01", "PROJA_01_02")
LEFT_OUT = "PROJA_01_03"


@pytest.fixture(autouse=True)
def _project_a(tmp_path_factory, monkeypatch):
    f = tmp_path_factory.mktemp("lab") / "lab.json"
    f.write_text(json.dumps({"sheet_projects": {
        "PROJA": {"sheet_name": "ProjectA_{NN}_Tracking.xlsx"}}}), encoding="utf-8")
    monkeypatch.setenv("MOUSEDB_LAB_CONFIG", str(f))
    from mousedb import database as dbmod
    monkeypatch.setattr(dbmod, "DEFAULT_LOG_PATH", None)


def _db(tmp_path, animals=NAMED + (LEFT_OUT,)):
    db = init_database(tmp_path / "scratch.db")
    with db.session() as s:
        s.add(Project(project_code="PROJA", project_name="PROJA"))
        s.add(Cohort(cohort_id=COHORT, project_code="PROJA", start_date=date(2026, 1, 5)))
        for sid in animals:
            s.add(Subject(subject_id=sid, cohort_id=COHORT))
    return db


def _sheet(tmp_path, animals=NAMED):
    p = tmp_path / "ProjectA_01_Tracking.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "0a_Metadata"
    ws.append(["SubjectID", "Date_of_Birth", "Date_of_Death", "Sex"])
    for a in animals:
        ws.append([a, date(2025, 11, 1), None, "F"])
    wb.save(p)
    return p


def _not_in_sheet(r):
    return ((r.get("sheet_sync") or {}).get("subjects") or {}).get("not_in_sheet")


def test_an_empty_animal_the_sheet_no_longer_names_is_shown_and_kept(tmp_path, monkeypatch):
    db = _db(tmp_path)

    r = ExcelImporter(db).import_cohort_file(_sheet(tmp_path))

    assert _not_in_sheet(r) == [LEFT_OUT]
    assert any(w.startswith("%s is in the database but not in this cohort's sheet" % LEFT_OUT)
               for w in r["warnings"])
    assert "1 animal not in the sheet" in sheet_sync.sync_summary(r)
    with db.session() as s:                       # never removed by the import
        assert s.query(Subject).filter_by(subject_id=LEFT_OUT).count() == 1

    # ... and it reaches the attention box and FIX THESE file through the ledger.
    monkeypatch.setattr(sheet_sync, "LEDGER", tmp_path / "ledger.jsonl")
    sheet_sync._append_ledger({"cohort_id": COHORT, "success": True,
                               "sheet_sync": r["sheet_sync"], "warnings": []})
    assert any(i["says"].startswith("%s is in the database but not in %s's sheet"
                                     % (LEFT_OUT, COHORT))
               for i in attention.sheet_items([COHORT]))


def test_an_animal_with_data_is_not_listed(tmp_path):
    """An animal with any row anywhere (a weight, a video's reaches) has a
    record of existing, and may be waiting for the sheet to catch up."""
    db = _db(tmp_path)
    with db.session() as s:
        s.add(Weight(subject_id=LEFT_OUT, date=date(2026, 1, 6), weight_grams=20.0,
                     entered_by="someone"))

    r = ExcelImporter(db).import_cohort_file(_sheet(tmp_path))

    assert not _not_in_sheet(r)


def test_an_animal_the_sheet_names_is_not_listed(tmp_path):
    db = _db(tmp_path)

    r = ExcelImporter(db).import_cohort_file(_sheet(tmp_path, NAMED + (LEFT_OUT,)))

    assert not _not_in_sheet(r)


def test_reach_rows_count_as_data(tmp_path):
    db = _db(tmp_path)
    with db.engine.begin() as c:
        c.execute(text(
            "INSERT INTO reach_data (subject_id, video_name, session_date, segment_num, "
            "reach_id, reach_num, causal_reach, is_first_reach, is_last_reach, "
            "n_reaches_in_segment, start_frame, end_frame, duration_frames, source_file, "
            "flagged_for_review) VALUES (:s, 'v', '2026-01-06', 1, 1, 1, 0, 1, 1, 1, 10, 20, "
            "10, 'f', 0)"), {"s": LEFT_OUT})

    r = ExcelImporter(db).import_cohort_file(_sheet(tmp_path))

    assert not _not_in_sheet(r)


def test_nothing_is_judged_on_a_workbook_read_on_a_guess(tmp_path):
    """A workbook that may not be the cohort's sheet says nothing about which
    animals the sheet names."""
    db = _db(tmp_path)
    imp = ExcelImporter(db)
    imp.sync_blocked = "two files match the cohort's sheet name"

    r = imp.import_cohort_file(_sheet(tmp_path))

    assert not _not_in_sheet(r)
