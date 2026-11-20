"""No phase labels for a cohort that is still running.

WHY: a session's phase label is derived from the cohort's WHOLE set of dates
(mousedb.phases). While the cohort is running, a later date can move the label
of a session that was already exported, and the earlier file carried the old
label with nothing to say it had changed. So every export leaves the phase
empty for every session of a running cohort, and fills it once the cohort is
over. "Running" is the decision the exports already make for the cause of
death (odc_reaches._cohort_still_on_study): the lab's own Study_Status for the
cohort, else a last session less than a month old.

Dates in 2099 are always "recent" (inside the window); dates in 2000 never are.
"""
import pandas as pd
import pytest

from mousedb.exporters import analysis_reaches as ar
from mousedb.exporters import current
from mousedb.exporters import odc_reaches as ox

RUNNING = "PROJR_01"
OVER = "PROJR_02"
# The real reader of the lab's declaration, kept before the fixture below
# replaces it, for the tests of the operator's own command.
_READ_DECLARATION = ox._declared_study_status


def _reach(cohort, year):
    sid = cohort + "_01"
    vid = "%s0110_%s01_P1" % (year, cohort.replace("_", ""))
    rows = []
    for seg in (1, 2):
        rows.append({"id": len(rows), "subject_id": sid, "video_name": vid,
                     "session_date": "%s-01-10" % year, "tray_type": "P", "run_number": 1,
                     "segment_num": seg, "reach_id": seg, "reach_num": 1,
                     "segment_outcome": "retrieved", "attention_score": 0.5,
                     "test_phase": "Post_Injury_1", "phase_group": "Post_Injury_1",
                     "extended_features": "{}"})
    return pd.DataFrame(rows)


def _build(cohort, year):
    subjects = pd.DataFrame([{"subject_id": cohort + "_01", "sex": "F", "date_of_birth": None}])
    return ox.build_cohort(cohort, _reach(cohort, year), subjects, None, None,
                           lambda s: {}, "lab-x")


@pytest.fixture(autouse=True)
def _nothing_declared(monkeypatch):
    monkeypatch.setattr(ox, "_declared_study_status", lambda cohort: "")


# ------------------------------------------------------ the per-reach files

def test_a_running_cohort_gets_no_phase_in_the_complete_record():
    df, _ = _build(RUNNING, 2099)
    assert set(df["Test_Type"]) == {""}
    assert set(df["Test_Type_Grouped"]) == {""}


def test_a_cohort_that_is_over_keeps_its_labels():
    df, _ = _build(OVER, 2000)
    assert set(df["Test_Type"]) == {"Post_Injury_1"}
    assert set(df["Test_Type_Grouped"]) == {"Post_Injury_1"}


def test_the_lab_s_declaration_releases_the_labels_early(monkeypatch):
    monkeypatch.setattr(ox, "_declared_study_status", lambda cohort: "completed")
    df, _ = _build(RUNNING, 2099)
    assert set(df["Test_Type"]) == {"Post_Injury_1"}


def test_the_lab_s_declaration_holds_the_labels_back(monkeypatch):
    monkeypatch.setattr(ox, "_declared_study_status", lambda cohort: "in_progress")
    df, _ = _build(OVER, 2000)
    assert set(df["Test_Type"]) == {""}


@pytest.mark.parametrize("status, cohort, year, expected", [
    ("completed", RUNNING, 2099, "Post_Injury_1"),
    ("in_progress", OVER, 2000, ""),
])
def test_what_mousedb_study_facts_writes_is_what_the_export_obeys(
        tmp_path, monkeypatch, status, cohort, year, expected):
    """The operator's lever, end to end: `mousedb study-facts --set <cohort>
    Study_Status <status>` writes the study-facts file (study_facts.set_fact),
    and the export reads it back for that exact cohort."""
    from mousedb import study_facts
    monkeypatch.setenv(study_facts.ENV_VAR, str(tmp_path / "study_facts.json"))
    monkeypatch.setattr(ox, "_declared_study_status", _READ_DECLARATION)
    study_facts.set_fact(cohort, "Study_Status", status)
    df, _ = _build(cohort, year)
    assert set(df["Test_Type"]) == {expected}


def test_the_complete_record_says_undetermined_not_not_recorded(tmp_path):
    """The complete record carries no empty cells; the withheld phase is a
    question not decided yet, not a source that failed to record it."""
    df, rows = _build(RUNNING, 2099)
    ox.write_cohort(tmp_path, RUNNING, df, rows)
    written = list(tmp_path.rglob("ODC_reaches_%s.csv" % RUNNING))
    assert written, "the complete record was not written"
    out = pd.read_csv(written[0], dtype=str, keep_default_na=False)
    assert set(out["Test_Type"]) == {"Undetermined"}
    assert set(out["Test_Type_Grouped"]) == {"Undetermined"}


def test_the_analysis_file_has_no_phase_and_no_stage_for_a_running_cohort():
    df, _ = _build(RUNNING, 2099)
    reaches, _rows = ar.build(df)
    assert set(reaches["test_type"]) == {"NA"}
    assert set(reaches["test_type_grouped"]) == {"NA"}
    # The stage is the same label, coarser: withheld with it, never "unknown".
    assert set(reaches["experiment_stage"]) == {"NA"}


def test_the_analysis_file_keeps_the_stage_of_a_cohort_that_is_over():
    df, _ = _build(OVER, 2000)
    reaches, _rows = ar.build(df)
    assert set(reaches["experiment_stage"]) == {ar.STAGE_AFTER}


def test_running_cohorts_names_each_cohort_once():
    reach = pd.concat([_reach(RUNNING, 2099), _reach(OVER, 2000)])
    assert ox.running_cohorts(reach, facts=lambda c: {}) == [RUNNING]
    # A cohort with hand scores and no reach yet has no dated session: running.
    assert ox.running_cohorts(reach, facts=lambda c: {}, cohorts=["PROJR_03"]) == [
        RUNNING, "PROJR_03"]


# ------------------------------------------- the all-cohort tables and sessions

def _snapshot(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    pd.concat([_reach(RUNNING, 2099), _reach(OVER, 2000)], ignore_index=True).assign(
        id=lambda d: range(len(d))).to_parquet(snap / "reach_data.parquet", index=False)
    pellets = []
    for cohort, year in ((RUNNING, 2099), (OVER, 2000)):
        for n in (1, 2):
            pellets.append({"id": len(pellets), "subject_id": cohort + "_01",
                            "session_date": "%s-01-10" % year, "test_phase": "Post_Injury_1",
                            "phase_group": "Post_Injury_1", "tray_type": "P",
                            "tray_number": 1, "pellet_number": n, "score": 2})
    pd.DataFrame(pellets).to_parquet(snap / "pellet_scores.parquet", index=False)
    return snap


def test_reach_data_and_manual_scores_withhold_a_running_cohort_s_phase(tmp_path, monkeypatch):
    monkeypatch.setattr(current, "_odc_reaches_csvs", lambda *a: None)
    snap = _snapshot(tmp_path)
    out = tmp_path / "current"
    manifest = current.refresh_current(snap, out, db_ok=False)
    for name in ("reach_data.csv", "manual_scores.csv"):
        df = pd.read_csv(out / name, dtype=str, keep_default_na=False)
        cohort = df["subject_id"].str.rsplit("_", n=1).str[0]
        assert set(df.loc[cohort == RUNNING, "test_phase"]) == {""}, name
        assert set(df.loc[cohort == RUNNING, "phase_group"]) == {""}, name
        assert set(df.loc[cohort == OVER, "test_phase"]) == {"Post_Injury_1"}, name
        assert manifest["files"][name]["phase_withheld_for_running_cohorts"] == [RUNNING]


def test_the_odc_session_file_withholds_a_running_cohort_s_phase(tmp_path, monkeypatch):
    """The per-cohort session tables come from the database; the decision
    comes from the snapshot, like every other file of the refresh."""
    import mousedb.database as database
    from mousedb.exporters import cohort_reports

    class _Q:
        def __init__(self, ids):
            self.ids = ids

        def order_by(self, *a):
            return self

        def all(self):
            return [type("C", (), {"cohort_id": c})() for c in self.ids]

    class _S:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def query(self, *a):
            return _Q([RUNNING, OVER])

    class _DB:
        def session(self):
            return _S()

    def fake_export(db, cid, path, running=None):
        pd.DataFrame([{"Animal": cid + "_01", "Cohort": cid, "Date": "2000-01-10",
                       "Test_Phase": "Post_Injury_1"}]).to_excel(path, index=False)

    monkeypatch.setattr(database, "get_db", lambda: _DB())
    monkeypatch.setattr(cohort_reports, "export_odc_format", fake_export)
    monkeypatch.setattr(current, "_odc_reaches_csvs", lambda *a: None)
    snap = _snapshot(tmp_path)
    out = tmp_path / "current"
    # Through the refresh, as the nightly job runs it (the database may be read).
    manifest = current.refresh_current(snap, out, db_ok=True)
    assert not [p for p in manifest["problems"] if "ODC" in p], manifest["problems"]
    running = pd.read_csv(next(out.rglob("ODC_days_%s.csv" % RUNNING)),
                          dtype=str, keep_default_na=False)
    over = pd.read_csv(next(out.rglob("ODC_days_%s.csv" % OVER)),
                       dtype=str, keep_default_na=False)
    assert set(running["Test_Phase"]) == {""}
    assert set(over["Test_Phase"]) == {"Post_Injury_1"}


def test_a_cohort_that_ends_changes_the_skip_signature(tmp_path, monkeypatch):
    """Which cohorts are running changes with the date alone. If it were not in
    the signature, a cohort that had just ended would keep its empty phases
    for as long as no other input changed."""
    snap = _snapshot(tmp_path)
    before = current.odc_reaches_signature(snap)
    monkeypatch.setattr(ox, "_declared_study_status",
                        lambda cohort: "completed" if cohort == RUNNING else "")
    after = current.odc_reaches_signature(snap)
    assert before != after
