"""A weekend-dated run inside a weekday's block is read as that weekday.

WHY (2026-10-26): CNT_04's tray tab dates its rows with formulas that count a
fixed number of rows up the sheet. After a row was cut, the rows of animals
02-04, tray 1, sitting in the 2025-10-28 block computed to 2025-10-25 -- a
Saturday, when nothing is recorded -- so their scores were filed under a day
with no video and the 28th's videos found none. A weekday date out of place (a
make-up session) and a whole block on a weekend are left as typed.
"""
from datetime import date

import openpyxl
import pandas as pd

from mousedb.date_typos import block_date_repairs
from mousedb.importers import ExcelImporter

TUE, SAT, WED = date(2025, 10, 28), date(2025, 10, 25), date(2025, 10, 29)


def _block(day, animals, tray="P", n=1):
    return [("CNT_04_%02d" % a, day, tray, n) for a in animals]


def test_a_weekend_run_inside_a_weekday_block_is_read_as_that_weekday():
    rows = _block(TUE, [1]) + _block(SAT, [2, 3, 4]) + _block(TUE, range(5, 16))
    assert block_date_repairs(rows) == {1: TUE, 2: TUE, 3: TUE}


def test_a_weekday_make_up_session_among_the_rows_is_left_as_typed():
    rows = _block(TUE, [1]) + _block(WED, [2]) + _block(TUE, range(3, 16))
    assert block_date_repairs(rows) == {}


def test_a_whole_block_on_a_weekend_between_two_different_days_is_left_alone():
    fri, sun, mon = date(2025, 12, 19), date(2025, 12, 21), date(2025, 12, 22)
    rows = _block(fri, range(1, 15), "F") + _block(sun, range(1, 15), "F") + _block(mon, range(1, 15))
    assert block_date_repairs(rows) == {}


def test_nothing_is_moved_onto_a_day_that_already_has_that_tray():
    rows = _block(TUE, [1, 2]) + _block(SAT, [2]) + _block(TUE, range(3, 16))
    assert block_date_repairs(rows) == {}


def _sheet(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "3b_Manual_Tray"
    ws.append(["Animal", "Date", "Tray Type/Number"] + list(range(1, 21)))
    for animal, day in [(1, TUE), (2, SAT), (3, SAT), (4, SAT)] + [(a, TUE) for a in range(5, 8)]:
        ws.append(["CNT_04_%02d" % animal, day, "P1"] + [1] * 20)
    p = tmp_path / "Connectome_04_Animal_Tracking.xlsx"
    wb.save(p)
    return p


def _import(db, path):
    imp = ExcelImporter(db)
    imp.imported_counts = {k: 0 for k in ("subjects", "pellet_scores")}
    with db.session() as session:
        imp._import_pellet_scores(pd.ExcelFile(path), "CNT_04", session, dry_run=False)
        session.commit()
    return imp


def test_the_import_reads_the_block_day_and_moves_scores_filed_under_the_weekend(tmp_path):
    from mousedb.database import init_database
    from mousedb.schema import Cohort, PelletScore, Subject
    db = init_database(tmp_path / "scratch.db")
    with db.session() as session:
        session.add(Cohort(cohort_id="CNT_04", project_code="CNT", start_date=date(2025, 10, 1)))
        for a in range(1, 8):
            session.add(Subject(subject_id="CNT_04_%02d" % a, cohort_id="CNT_04"))
        # what an earlier import left: animal 02's tray 1 filed under the Saturday
        for p in range(1, 21):
            session.add(PelletScore(subject_id="CNT_04_02", session_date=SAT, test_phase="Pillar",
                                    tray_type="P", tray_number=1, pellet_number=p, score=1,
                                    entered_by="excel_import"))
        session.commit()

    imp = _import(db, _sheet(tmp_path))

    with db.session() as session:
        assert session.query(PelletScore).filter_by(session_date=SAT).count() == 0
        for a in (2, 3, 4):
            assert session.query(PelletScore).filter_by(
                subject_id="CNT_04_%02d" % a, session_date=TUE).count() == 20
    assert imp.imported_counts.get("pellet_scores_redated") == 20
    said = [w for w in imp.warnings if w.startswith("REPAIRED CNT_04 3b_Manual_Tray: rows")]
    assert len(said) == 1 and "CNT_04_02 P1, CNT_04_03 P1, CNT_04_04 P1" in said[0]
    assert "2025-10-25 (a Saturday" in said[0] and "read as 2025-10-28" in said[0]

    imp = _import(db, _sheet(tmp_path))                      # again: nothing more to move
    assert not imp.imported_counts.get("pellet_scores_redated")
