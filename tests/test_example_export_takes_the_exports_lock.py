"""mousedb export-example writes into the live exports folder only under the lock.

WHY: its default folder is exports/Behavior/All_Cohorts/Example, inside the folder the
nightly job, the Where Is My Data button and the export commands rewrite under
the current-exports lock. Written without it, the example could be written
while a refresh rearranges that folder. It now waits for a running refresh and
writes nothing when the wait runs out; a folder outside the live exports takes
no lock.
"""
import pandas as pd
import pytest

import mousedb.config as config
from mousedb.exporters import current
from mousedb.exporters import example_export as ex
from mousedb.output_folders import all_cohorts_dir

COHORT = "PROJX_01"


def _snapshot(tmp_path):
    snap = tmp_path / "snap"
    snap.mkdir()
    rows = []
    for vid, date in (("20000110_PROJX0101_P1", "2000-01-10"),
                      ("20000111_PROJX0101_P1", "2000-01-11")):
        for seg in (1, 2):
            rows.append({"id": len(rows), "subject_id": COHORT + "_01", "video_name": vid,
                         "session_date": date, "tray_type": "P", "run_number": 1,
                         "segment_num": seg, "reach_id": seg, "reach_num": 1,
                         "segment_outcome": "retrieved", "attention_score": 0.5,
                         "test_phase": "Pillar", "phase_group": "Baseline",
                         "extended_features": "{}"})
    pd.DataFrame(rows).to_parquet(snap / "reach_data.parquet", index=False)
    pd.DataFrame([{"subject_id": COHORT + "_01", "sex": "F", "date_of_birth": None}]
                 ).to_parquet(snap / "subjects.parquet", index=False)
    return snap


@pytest.fixture
def live_root(tmp_path, monkeypatch):
    """A configured mousedb root in tmp_path, so exports/Behavior/All_Cohorts under it is
    the live exports folder."""
    root = tmp_path / "dbroot"
    all_cohorts_dir(root).mkdir(parents=True)
    snap = _snapshot(tmp_path)

    def require(key):
        return {"mousedb_root": root, "snapshot_dir": snap}[key]

    monkeypatch.setattr(config, "require", require)
    monkeypatch.setattr(ex, "require", require)
    monkeypatch.setattr(ex, "snapshot_dir", lambda: snap)
    monkeypatch.setattr(ex.odc_reaches, "_declared_study_status", lambda c: "")
    return root


def test_the_example_waits_for_a_running_refresh_and_writes_nothing(live_root, monkeypatch):
    monkeypatch.setenv("MOUSEDB_LOCK_WAIT_MINUTES", "0")
    example = all_cohorts_dir(live_root) / ex.FOLDER_NAME
    held = current.hold_exports_lock(0)       # a refresh is writing
    try:
        with pytest.raises(Exception) as caught:
            ex.build(cohort=COHORT, use_cache=False)
    finally:
        held.release()
    assert "Lock" in type(caught.value).__name__, repr(caught.value)
    assert not (example.exists() and any(example.iterdir())), (
        "the example was written while another refresh held the exports lock")


def test_with_no_refresh_running_the_example_is_written(live_root):
    """The control: the lock is taken and released, and the files are written."""
    result = ex.build(cohort=COHORT, use_cache=False)
    example = all_cohorts_dir(live_root) / ex.FOLDER_NAME
    assert (example / ("reaches_%s_analysis.csv" % COHORT)).exists(), result
    # Released: a refresh can take it at once.
    current.hold_exports_lock(0).release()


def test_a_folder_outside_the_live_exports_takes_no_lock(live_root, tmp_path, monkeypatch):
    monkeypatch.setenv("MOUSEDB_LOCK_WAIT_MINUTES", "0")
    held = current.hold_exports_lock(0)
    try:
        ex.build(cohort=COHORT, out_dir=tmp_path / "elsewhere", use_cache=False)
    finally:
        held.release()
    assert (tmp_path / "elsewhere" / ("reaches_%s_analysis.csv" % COHORT)).exists()
