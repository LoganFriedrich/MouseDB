"""The sheet import fills in a cohort the pipeline created from a video.

WHY: the MouseReach sync creates a cohort from the first video it sees,
with that video's date as a placeholder start (cohorts.start_date is NOT
NULL). The tracking sheet is the authority on the real start date, so when
it is finally imported it must overwrite the placeholder -- and only the
placeholder: a cohort the sheet created is never touched.
"""
from datetime import date, timedelta

import openpyxl
import pandas as pd
import pytest

from mousedb.database import init_database
from mousedb.importers import ExcelImporter
from mousedb.schema import Cohort


def _sheet(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "3b_Manual_Tray"
    ws.append(["Animal", "Date", "Tray Type/Number"] + list(range(1, 21)))
    d0 = date(2026, 6, 5)
    for i in range(3):
        ws.append(["CNT_07_01", d0 + timedelta(days=i), "P1"] + [2] * 20)
    p = tmp_path / "Connectome_07_Animal_Tracking.xlsx"
    wb.save(p)
    return p


@pytest.mark.parametrize("notes, expect_updated", [
    ("auto-created from video 20260901_CNT0701_P2 by mousereach sync", True),
    ("Imported from Connectome_07_Animal_Tracking.xlsx", False),
])
def test_placeholder_start_date_is_replaced_only_when_auto_created(tmp_path, notes, expect_updated):
    db = init_database(tmp_path / "scratch.db")
    with db.session() as s:
        s.add(Cohort(cohort_id="CNT_07", project_code="CNT",
                     start_date=date(2026, 9, 1), notes=notes))
        s.commit()

    imp = ExcelImporter(db)
    imp.import_cohort_file(_sheet(tmp_path))

    with db.session() as s:
        c = s.query(Cohort).filter_by(cohort_id="CNT_07").first()
        if expect_updated:
            # sheet's earliest date minus the 4-day training offset
            assert c.start_date == date(2026, 6, 1)
            assert not c.notes.lower().startswith("auto-created")
        else:
            assert c.start_date == date(2026, 9, 1)
            assert c.notes == notes
