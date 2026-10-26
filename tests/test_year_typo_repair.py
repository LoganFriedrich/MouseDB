"""A session date typed a year off is read as the year it was meant to be.

WHY: a tracking sheet had three days of tray scores typed as the previous
year. Filed a year early, they stranded those sessions away from their
videos and -- because phases come from the gaps between a cohort's
sessions -- made every real session read as coming after a year-long
break. The import now reads such a date as meant, moves scores an earlier
import filed under the typed date, and reports it so the sheet gets fixed.
"""
from datetime import date, timedelta

import openpyxl
import pandas as pd

from mousedb.date_typos import year_typo_repairs
from mousedb.importers import ExcelImporter


def test_a_date_a_year_off_inside_the_cohorts_span_is_a_typo():
    dates = [date(2026, 7, 16) + timedelta(days=i) for i in range(80)] + [date(2025, 9, 29)] * 4
    assert year_typo_repairs(dates) == {date(2025, 9, 29): date(2026, 9, 29)}


def test_a_cohort_running_over_new_year_keeps_both_years():
    dates = [date(2025, 11, 1) + timedelta(days=i) for i in range(100)]   # Nov 2025 - Feb 2026
    assert year_typo_repairs(dates) == {}


def test_a_date_outside_the_span_even_after_correction_is_left_alone():
    dates = [date(2026, 7, 1) + timedelta(days=i) for i in range(30)] + [date(2025, 12, 1)]
    assert year_typo_repairs(dates) == {}


def _sheet(tmp_path, typo_year):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "3b_Manual_Tray"
    ws.append(["Animal", "Date", "Tray Type/Number"] + list(range(1, 21)))
    d0 = date(2026, 7, 1)
    days = [d0 + timedelta(days=i) for i in list(range(5)) + list(range(25, 30))]
    for d in days:
        ws.append(["CNT_05_01", d, "P1"] + [2, 0, 1, 0] * 5)
    ws.append(["CNT_05_01", date(typo_year, 7, 15), "P1"] + [1] * 20)
    p = tmp_path / "Connectome_05_Animal_Tracking.xlsx"
    wb.save(p)
    return p


def _import(db, path):
    imp = ExcelImporter(db)
    imp.imported_counts = {k: 0 for k in ('subjects', 'pellet_scores')}
    with db.session() as session:
        imp._import_pellet_scores(pd.ExcelFile(path), "CNT_05", session, dry_run=False)
        session.commit()
    return imp


def test_the_import_reads_the_meant_date_and_moves_scores_filed_under_the_typo(tmp_path):
    from mousedb.database import init_database
    from mousedb.schema import Cohort, PelletScore, Subject
    db = init_database(tmp_path / "scratch.db")
    with db.session() as session:
        session.add(Cohort(cohort_id="CNT_05", project_code="CNT", start_date=date(2026, 6, 25)))
        session.add(Subject(subject_id="CNT_05_01", cohort_id="CNT_05"))
        # what an earlier import left: the typo'd day filed under 2025
        for p in range(1, 21):
            session.add(PelletScore(subject_id="CNT_05_01", session_date=date(2025, 7, 15),
                                    test_phase="Pillar", tray_type="P", tray_number=1,
                                    pellet_number=p, score=1, entered_by="excel_import"))
        session.commit()

    imp = _import(db, _sheet(tmp_path, 2025))

    with db.session() as session:
        dates = {d for (d,) in session.query(PelletScore.session_date).distinct()}
        assert date(2025, 7, 15) not in dates and date(2026, 7, 15) in dates
        assert session.query(PelletScore).filter_by(session_date=date(2026, 7, 15)).count() == 20
    assert imp.imported_counts.get('pellet_scores_redated') == 20
    assert any(w.startswith("REPAIRED CNT_05 3b_Manual_Tray: date 2025-07-15 read as 2026-07-15")
               for w in imp.warnings)

    # read again: nothing more to move, the scores are not doubled
    imp = _import(db, _sheet(tmp_path, 2025))
    with db.session() as session:
        assert session.query(PelletScore).filter_by(session_date=date(2026, 7, 15)).count() == 20
    assert not imp.imported_counts.get('pellet_scores_redated')
