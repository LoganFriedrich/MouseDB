"""Pellet-score import must succeed against the LIVE pellet_scores DDL.

The live connectome.db was created under an older schema where
``pellet_scores.test_phase`` is NOT NULL; the ORM later relaxed that and the
importer moved to insert-NULL-then-derive. Against the live table that
violated the constraint and rolled back every import of a new cohort --
silently, hourly, for CNT_05 (found 2026-08-28). Phases are now derived
before the insert. This test builds a scratch database with the live DDL
(not the ORM's) and imports a small synthetic 3b_Manual_Tray sheet through
the real code path.
"""
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import openpyxl
import pandas as pd

from mousedb.importers import ExcelImporter

LIVE_PELLET_DDL = """
CREATE TABLE pellet_scores (
    id INTEGER NOT NULL,
    subject_id VARCHAR(20) NOT NULL,
    session_date DATE NOT NULL,
    test_phase VARCHAR(50) NOT NULL,
    tray_type VARCHAR(1) NOT NULL,
    tray_number INTEGER NOT NULL,
    pellet_number INTEGER NOT NULL,
    score INTEGER NOT NULL,
    entered_by VARCHAR(50),
    entered_at DATETIME, phase_group TEXT,
    contact_group TEXT GENERATED ALWAYS AS (CASE WHEN score = 0 THEN 'missed' ELSE 'contacted' END) VIRTUAL,
    PRIMARY KEY (id),
    CONSTRAINT unique_pellet_score UNIQUE (subject_id, session_date, tray_type, tray_number, pellet_number),
    CONSTRAINT valid_tray_type CHECK (tray_type IN ('E', 'F', 'P')),
    CONSTRAINT valid_score CHECK (score IN (0, 1, 2))
)
"""


def _sheet(tmp_path):
    """A 3b_Manual_Tray with a pre-injury block, a 14-day gap, and rehab."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "3b_Manual_Tray"
    ws.append(["Animal", "Date", "Tray Type/Number"] + list(range(1, 21)))
    d0 = date(2026, 6, 1)
    sessions = [(d0 + timedelta(days=i), "P1") for i in range(4)]
    sessions += [(d0 + timedelta(days=30 + i), "F1") for i in range(2)]
    sessions += [(d0 + timedelta(days=40 + i), "P1") for i in range(4)]
    for d, tray in sessions:
        ws.append(["CNT_05_01", d, tray] + [2, 0, 1, 0] * 5)
    p = tmp_path / "Connectome_05_Animal_Tracking.xlsx"
    wb.save(p)
    return p


def test_import_lands_with_a_phase_on_every_row_under_live_ddl(tmp_path):
    from mousedb.database import init_database
    dbp = tmp_path / "scratch.db"
    con = sqlite3.connect(dbp)
    con.execute(LIVE_PELLET_DDL)
    con.commit()
    con.close()
    db = init_database(dbp)  # creates the OTHER tables with the ORM's DDL

    imp = ExcelImporter(db)
    imp.imported_counts = {k: 0 for k in ('subjects', 'pellet_scores')}
    xl = pd.ExcelFile(_sheet(tmp_path))
    with db.session() as session:
        # the cohort row the real path would have created first (the CNT
        # project itself is seeded by init_database)
        from mousedb.schema import Cohort
        session.add(Cohort(cohort_id="CNT_05", project_code="CNT",
                           start_date=date(2026, 6, 1)))
        session.flush()
        imp._import_pellet_scores(xl, "CNT_05", session, dry_run=False)
        session.commit()

    con = sqlite3.connect(dbp)
    n = con.execute("SELECT COUNT(*) FROM pellet_scores").fetchone()[0]
    nulls = con.execute(
        "SELECT COUNT(*) FROM pellet_scores WHERE test_phase IS NULL "
        "OR test_phase='unassigned'").fetchone()[0]
    phases = {r[0] for r in con.execute("SELECT DISTINCT test_phase FROM pellet_scores")}
    con.close()
    assert n == 10 * 20
    assert nulls == 0
    assert "Rehab_Flat" in phases and any(p.startswith("Rehab_Pillar") for p in phases)
    assert not any("unassigned" in w for w in imp.warnings)
