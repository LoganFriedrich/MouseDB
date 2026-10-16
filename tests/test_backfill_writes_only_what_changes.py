"""Phase backfill: the end state is unchanged, but only rows that differ are written.

WHY: the database lives on a network share that takes writes at a fraction of
a megabyte per second. Rewriting every reach of a cohort to the label it
already has cost hours every night (2026-10-15: one reprocessed video of the
largest cohort -> the whole cohort rewritten -> the reach import ran past its
limit and held the database through the brain import and the export refresh).
A night on which nothing changed must write nothing.
"""
from datetime import date

import pytest
from sqlalchemy import text

from mousedb import backfill
from mousedb.database import Database
from mousedb.phases import assign_phases_for_cohort

COHORT = "PROJA_01"
# A realistic scored-day pattern: flat, pillar baseline, weekly post-injury,
# easy/flat/pillar rehab (the shape the phase rules expect).
SCORED = [
    (date(2025, 6, 19), "F"), (date(2025, 6, 20), "F"), (date(2025, 6, 23), "F"),
    (date(2025, 6, 24), "P"), (date(2025, 6, 25), "P"), (date(2025, 6, 26), "P"),
    (date(2025, 6, 27), "P"), (date(2025, 6, 30), "P"), (date(2025, 7, 1), "P"),
    (date(2025, 7, 11), "P"), (date(2025, 7, 18), "P"), (date(2025, 7, 25), "P"),
    (date(2025, 8, 1), "P"),
    (date(2025, 8, 4), "E"), (date(2025, 8, 5), "E"), (date(2025, 8, 6), "E"),
    (date(2025, 8, 7), "E"), (date(2025, 8, 8), "E"), (date(2025, 8, 11), "E"),
    (date(2025, 8, 12), "F"), (date(2025, 8, 13), "F"), (date(2025, 8, 14), "F"),
    (date(2025, 8, 15), "F"), (date(2025, 8, 18), "F"),
    (date(2025, 8, 19), "P"), (date(2025, 8, 20), "P"), (date(2025, 8, 21), "P"),
    (date(2025, 8, 22), "P"),
]
UNSCORED_VIDEO_DAY = date(2025, 7, 5)      # a recording during recovery: no pellet scores


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    d.init_db()
    with d.engine.begin() as c:
        c.execute(text("INSERT INTO projects (project_code, project_name) VALUES ('PROJA', 'A')"))
        c.execute(text("INSERT INTO cohorts (cohort_id, project_code, start_date) VALUES (:c, 'PROJA', '2025-06-16')"),
                  {"c": COHORT})
        for s in ("PROJA_01_01", "PROJA_01_02"):
            c.execute(text("INSERT INTO subjects (subject_id, cohort_id) VALUES (:s, :c)"), {"s": s, "c": COHORT})
            for d_, tray in SCORED:
                c.execute(text("INSERT INTO pellet_scores (subject_id, session_date, tray_type, tray_number, "
                               "pellet_number, score) VALUES (:s, :d, :t, 1, 1, 2)"),
                          {"s": s, "d": d_, "t": tray})
            for d_, _ in SCORED + [(UNSCORED_VIDEO_DAY, "P")]:
                c.execute(text("INSERT INTO reach_data (subject_id, video_name, session_date, segment_num, reach_id, "
                               "reach_num, start_frame, end_frame, duration_frames, source_file, causal_reach, "
                               "is_first_reach, is_last_reach, n_reaches_in_segment, flagged_for_review) "
                               "VALUES (:s, :v, :d, 1, 1, 1, 0, 10, 10, 'f', 0, 0, 0, 1, 0)"),
                          {"s": s, "v": "%s_%s" % (d_.strftime("%Y%m%d"), s), "d": d_.isoformat()})
    return d


def _expected():
    return {a.session_date.isoformat(): (a.test_phase, a.phase_group) for a in assign_phases_for_cohort(SCORED)}


def _reach_labels(db):
    with db.engine.begin() as c:
        return {(r[0], r[1]): (r[2], r[3]) for r in c.execute(
            text("SELECT subject_id, session_date, test_phase, phase_group FROM reach_data")).fetchall()}


def test_end_state_is_every_row_labelled_and_second_run_writes_nothing(db):
    first = backfill.backfill_phases(db, cohort_id=COHORT)
    n_subjects, n_scored = 2, len(SCORED)
    assert first.pellet_rows_updated == n_subjects * n_scored
    assert first.reach_rows_updated == n_subjects * (n_scored + 1)     # + the unscored video day

    expected = _expected()
    for (subject, day), labels in _reach_labels(db).items():
        if day == UNSCORED_VIDEO_DAY.isoformat():
            assert labels == (backfill.UNSCHEDULED, backfill.UNSCHEDULED)
        else:
            assert labels == expected[day], (subject, day)

    second = backfill.backfill_phases(db, cohort_id=COHORT)
    assert (second.pellet_rows_updated, second.reach_rows_updated) == (0, 0)


def test_only_the_rows_that_differ_are_written(db):
    backfill.backfill_phases(db, cohort_id=COHORT)
    with db.engine.begin() as c:
        # a legacy spelling on one reach row, a stale label on one pellet row,
        # and a formerly-unscored video day that someone hand-labelled
        c.execute(text("UPDATE reach_data SET test_phase = 'Rehab_1' WHERE subject_id = 'PROJA_01_01' "
                       "AND session_date = '2025-08-04'"))
        c.execute(text("UPDATE pellet_scores SET phase_group = 'Old' WHERE subject_id = 'PROJA_01_02' "
                       "AND session_date = '2025-06-24'"))
        c.execute(text("UPDATE reach_data SET test_phase = 'Pillar', phase_group = 'Baseline' "
                       "WHERE session_date = :d"), {"d": UNSCORED_VIDEO_DAY.isoformat()})
    dry = backfill.backfill_phases(db, cohort_id=COHORT, dry_run=True)
    assert (dry.pellet_rows_updated, dry.reach_rows_updated) == (1, 3)
    assert _reach_labels(db)[("PROJA_01_01", "2025-08-04")][0] == "Rehab_1"   # dry run wrote nothing

    real = backfill.backfill_phases(db, cohort_id=COHORT)
    assert (real.pellet_rows_updated, real.reach_rows_updated) == (1, 3)
    labels = _reach_labels(db)
    assert labels[("PROJA_01_01", "2025-08-04")] == _expected()["2025-08-04"]
    assert labels[("PROJA_01_01", UNSCORED_VIDEO_DAY.isoformat())] == (backfill.UNSCHEDULED, backfill.UNSCHEDULED)
    assert backfill.backfill_phases(db, cohort_id=COHORT).reach_rows_updated == 0
