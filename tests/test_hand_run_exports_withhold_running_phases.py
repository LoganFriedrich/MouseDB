"""`mousedb export` leaves the phase empty for a cohort that is still running.

WHY: a session's phase label is derived from the cohort's WHOLE set of dates
(mousedb.phases), so while a cohort is running a later date can move the label
of a session already written to a file. The nightly files already leave the
phase empty for a running cohort (odc_reaches.running_cohorts); the exports a
person runs by hand (`mousedb export`: the tracking-sheet workbook, the ODC
workbook, both together, and the unified parquet) read the database directly
and wrote the stored labels anyway. They now make the same decision, from the
database's own reach rows, and leave the phase empty.

Dates in 2099 are always "recent" (inside the window); dates in 2000 never are.
"""
from datetime import date

import pandas as pd
import pytest

import mousedb.database as dbmod
from mousedb.exporters import cohort_reports as cr
from mousedb.exporters import odc_reaches as ox
from mousedb.schema import (Base, Cohort, PelletScore, Project, ReachData,
                            Subject)

RUNNING = "PROJR_01"
OVER = "PROJR_02"
PHASE = "Post_Injury_1"


@pytest.fixture(autouse=True)
def _nothing_declared(monkeypatch):
    # No Study_Status written by the lab: running is decided by date alone.
    monkeypatch.setattr(ox, "_declared_study_status", lambda cohort: "")
    monkeypatch.setattr("mousedb.study_facts.facts", lambda project: {})


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DEFAULT_LOG_PATH", None)
    # The exports work out their default folder even when given a path.
    monkeypatch.setattr("mousedb.config.export_path", lambda: tmp_path / "exports")
    d = dbmod.Database(tmp_path / "t.db")
    Base.metadata.create_all(d.engine)
    with d.session() as s:
        s.add(Project(project_code="PROJR", project_name="invented project"))
        for cohort, year in ((RUNNING, 2099), (OVER, 2000)):
            sid = cohort + "_01"
            s.add(Cohort(cohort_id=cohort, project_code="PROJR",
                         start_date=date(year, 1, 1)))
            s.add(Subject(subject_id=sid, cohort_id=cohort, sex="F"))
            for n in (1, 2):
                s.add(PelletScore(subject_id=sid, session_date=date(year, 1, 10),
                                  test_phase=PHASE, phase_group=PHASE, tray_type="P",
                                  tray_number=1, pellet_number=n, score=2))
            s.add(ReachData(subject_id=sid,
                            video_name="%s0110_%s01_P1" % (year, cohort.replace("_", "")),
                            session_date="%s-01-10" % year, tray_type="P", run_number=1,
                            test_phase=PHASE, phase_group=PHASE,
                            segment_num=1, reach_id=1, reach_num=1,
                            start_frame=10, end_frame=20, duration_frames=10,
                            source_file="invented_features.json"))
    return d


def _stored(db, cohort):
    """The phase the database holds for the cohort's one session (the write
    hook derives it from the dates, so it is read back, not assumed)."""
    with db.session() as s:
        phases = {p.test_phase for p in s.query(PelletScore).filter(
            PelletScore.subject_id == cohort + "_01")}
    assert len(phases) == 1 and next(iter(phases)), phases
    return next(iter(phases))


def test_the_tracking_workbook_withholds_a_running_cohort_s_phase(db, tmp_path):
    for cohort, expected in ((RUNNING, ""), (OVER, _stored(db, OVER))):
        out = tmp_path / ("%s_tracking.xlsx" % cohort)
        cr.export_cohort_to_excel(db, cohort, out)
        df = pd.read_excel(out, sheet_name="3b_Manual_Tray", dtype=str,
                           keep_default_na=False)
        assert len(df) == 1, cohort   # one tray row: blanking never merges rows
        assert set(df["Test_Phase"]) == {expected}, cohort


def test_the_odc_workbook_withholds_a_running_cohort_s_phase(db, tmp_path):
    for cohort, expected in ((RUNNING, ""), (OVER, _stored(db, OVER))):
        out = tmp_path / ("%s_ODC.xlsx" % cohort)
        cr.export_odc_format(db, cohort, out)
        df = pd.read_excel(out, dtype=str, keep_default_na=False)
        assert set(df["Test_Phase"]) == {expected}, cohort


def test_all_formats_withhold_a_running_cohort_s_phase(db, tmp_path):
    paths = cr.export_all_formats(db, RUNNING, tmp_path / "all")
    tray = pd.read_excel(paths["excel"], sheet_name="3b_Manual_Tray", dtype=str,
                         keep_default_na=False)
    odc = pd.read_excel(paths["odc"], dtype=str, keep_default_na=False)
    assert set(tray["Test_Phase"]) == {""}
    assert set(odc["Test_Phase"]) == {""}


def test_the_unified_parquet_withholds_a_running_cohort_s_phase(db, tmp_path):
    out = cr.export_unified_to_parquet(db, tmp_path / "unified.parquet")
    df = pd.read_parquet(out)
    by_cohort = dict(zip(df["cohort_id"], df["test_phase"]))
    assert by_cohort[RUNNING] == ""
    assert by_cohort[OVER] == _stored(db, OVER)


def test_a_lab_declaration_releases_the_labels(db, tmp_path, monkeypatch):
    """The lab's own Study_Status decides over the date: a cohort declared
    completed gets its labels even though its last session is recent."""
    monkeypatch.setattr(ox, "_declared_study_status",
                        lambda cohort: "completed" if cohort == RUNNING else "")
    out = tmp_path / "declared_ODC.xlsx"
    cr.export_odc_format(db, RUNNING, out)
    df = pd.read_excel(out, dtype=str, keep_default_na=False)
    assert set(df["Test_Phase"]) == {_stored(db, RUNNING)}


def test_a_caller_s_decision_is_used_as_given(db, tmp_path):
    """The nightly refresh decides from the snapshot and passes that in, so the
    session file and its manifest entry agree with every other file."""
    out = tmp_path / "given_ODC.xlsx"
    cr.export_odc_format(db, OVER, out, running=[OVER])
    df = pd.read_excel(out, dtype=str, keep_default_na=False)
    assert set(df["Test_Phase"]) == {""}
