"""An existing animal's birth date, death date and sex come in from the sheet
-- into empty fields only.

WHY: 0a_Metadata was read only for animals the import was creating, so an
animal created earlier (from a video, or from another tab) never got these
facts. A subject records no origin, so a value already in the database may
have been typed in on purpose: it is never overwritten, and a disagreement is
put in front of a person instead.
"""
import json
from datetime import date

import openpyxl
import pytest

from mousedb import attention, sheet_sync
from mousedb.database import init_database
from mousedb.importers import ExcelImporter
from mousedb.schema import AuditLog, Cohort, Project, Subject

COHORT = "PROJA_01"


@pytest.fixture(autouse=True)
def _project_a(tmp_path_factory, monkeypatch):
    f = tmp_path_factory.mktemp("lab") / "lab.json"
    f.write_text(json.dumps({"sheet_projects": {
        "PROJA": {"sheet_name": "ProjectA_{NN}_Tracking.xlsx"}}}), encoding="utf-8")
    monkeypatch.setenv("MOUSEDB_LAB_CONFIG", str(f))


def _db(tmp_path, **animals):
    db = init_database(tmp_path / "scratch.db")
    assert db.log_path == tmp_path / "logs"
    with db.session() as s:
        if not s.query(Project).filter_by(project_code="PROJA").first():
            s.add(Project(project_code="PROJA", project_name="PROJA"))
        s.add(Cohort(cohort_id=COHORT, project_code="PROJA", start_date=date(2026, 1, 5)))
        for sid, fields in animals.items():
            s.add(Subject(subject_id=sid, cohort_id=COHORT, **fields))
    return db


def _sheet(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "0a_Metadata"
    ws.append(["SubjectID", "Date_of_Birth", "Date_of_Death", "Sex"])
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    return path


def test_empty_facts_are_filled_and_logged(tmp_path):
    db = _db(tmp_path, PROJA_01_01={}, PROJA_01_02={})
    p = _sheet(tmp_path / "ProjectA_01_Tracking.xlsx", [
        ("PROJA_01_01", date(2025, 11, 1), date(2026, 3, 2), "F"),
        ("PROJA_01_02", date(2025, 11, 8), None, "M")])

    r = ExcelImporter(db).import_cohort_file(p)

    with db.session() as s:
        a = s.query(Subject).filter_by(subject_id="PROJA_01_01").one()
        b = s.query(Subject).filter_by(subject_id="PROJA_01_02").one()
        assert (a.date_of_birth, a.date_of_death, a.sex) == (date(2025, 11, 1), date(2026, 3, 2), "F")
        assert (b.date_of_birth, b.date_of_death, b.sex) == (date(2025, 11, 8), None, "M")
        assert s.query(AuditLog).filter_by(table_name="subjects", action="UPDATE").count() == 5
    assert r["imported"]["subject_facts_filled"] == 5
    assert "5 empty facts filled" in sheet_sync.sync_summary(r)

    # Nothing left to fill: a second import changes nothing.
    r = ExcelImporter(db).import_cohort_file(p)
    assert "subject_facts_filled" not in r["imported"]


def test_a_present_value_is_never_overwritten_and_the_difference_is_shown(tmp_path, monkeypatch):
    db = _db(tmp_path, PROJA_01_01={"date_of_death": date(2026, 3, 3), "sex": "F"})
    p = _sheet(tmp_path / "ProjectA_01_Tracking.xlsx",
               [("PROJA_01_01", None, date(2026, 3, 2), "F")])

    r = ExcelImporter(db).import_cohort_file(p)

    with db.session() as s:
        assert s.query(Subject).filter_by(subject_id="PROJA_01_01").one().date_of_death == date(2026, 3, 3)
        assert not s.query(AuditLog).filter_by(table_name="subjects").count()
    differs = r["sheet_sync"]["subjects"]["differs"]
    assert differs == [{"subject_id": "PROJA_01_01", "field": "date_of_death",
                        "database": "2026-03-03", "sheet": "2026-03-02"}]
    assert any(w.startswith("0a_Metadata: PROJA_01_01's date of death is 2026-03-03 in the "
                            "database but 2026-03-02 in the sheet") for w in r["warnings"])

    # ... and it reaches the FIX THESE list through the import's ledger line.
    monkeypatch.setattr(sheet_sync, "LEDGER", tmp_path / "ledger.jsonl")
    sheet_sync._append_ledger({"cohort_id": COHORT, "success": True,
                               "sheet_sync": r["sheet_sync"], "warnings": []})
    items = attention.sheet_items([COHORT])
    assert any("PROJA_01_01's date of death is 2026-03-03 in the database" in i["says"]
               for i in items)


def test_a_death_date_still_to_come_is_not_taken(tmp_path):
    db = _db(tmp_path, PROJA_01_01={})
    p = _sheet(tmp_path / "ProjectA_01_Tracking.xlsx",
               [("PROJA_01_01", None, date(2026, 6, 1), None)])
    imp = ExcelImporter(db)
    imp.today = date(2026, 5, 1)

    imp.import_cohort_file(p)

    with db.session() as s:
        assert s.query(Subject).filter_by(subject_id="PROJA_01_01").one().date_of_death is None


def test_a_dry_run_reports_the_fills_and_writes_nothing(tmp_path):
    db = _db(tmp_path, PROJA_01_01={})
    p = _sheet(tmp_path / "ProjectA_01_Tracking.xlsx",
               [("PROJA_01_01", date(2025, 11, 1), None, "F")])

    r = ExcelImporter(db).import_cohort_file(p, dry_run=True)

    assert len(r["sheet_sync"]["subjects"]["filled"]) == 2
    with db.session() as s:
        a = s.query(Subject).filter_by(subject_id="PROJA_01_01").one()
        assert a.date_of_birth is None and a.sex is None
