"""Suite-wide guards.

A unit test must never contend for the PRODUCTION database-task lock: when a
real hourly task held it, the suite silently hung for minutes (2026-09-04).
Constructing TaskMutex without an explicit base_dir resolves to the
production logs dir -- make that an instant, loud failure instead.
"""
import pytest

from mousedb.task_mutex import TaskMutex

_orig_init = TaskMutex.__init__


@pytest.fixture(autouse=True)
def _no_production_hand_scores(monkeypatch):
    """An import in a test never rewrites the hand-scores file: its location is
    this machine's configuration (sheet_sync.WRITE_HAND_SCORES)."""
    from mousedb import sheet_sync
    monkeypatch.setattr(sheet_sync, "WRITE_HAND_SCORES", False)
    # nor writes a FIX THESE file into a sheets folder
    monkeypatch.setattr(sheet_sync, "WRITE_FIX_THESE", False)


@pytest.fixture(autouse=True)
def _lab_settings_for_tests(tmp_path_factory, monkeypatch):
    """Every test reads its OWN lab settings file, never the lab's: one project
    whose sheets are named Connectome_NN_Animal_Tracking.xlsx, the name the
    suite's sheet fixtures use. WHY suite-wide: which file names are sheets is
    lab configuration (mousedb.sheet_projects) with no built-in default, and a
    test must not depend on, or write to, the machine's real settings. A test
    about the settings themselves points MOUSEDB_LAB_CONFIG somewhere else."""
    import json
    f = tmp_path_factory.mktemp("lab_settings") / "mousedb_lab_config.json"
    f.write_text(json.dumps({"sheet_projects": {
        "CNT": {"sheet_name": "Connectome_{NN}_Animal_Tracking.xlsx"}}}), encoding="utf-8")
    monkeypatch.setenv("MOUSEDB_LAB_CONFIG", str(f))


@pytest.fixture(autouse=True)
def _no_production_lock(monkeypatch):
    def guarded(self, name="central-db", stale_minutes=45, base_dir=None):
        if base_dir is None:
            pytest.fail(
                "test constructed TaskMutex with the PRODUCTION lock dir; "
                "pass base_dir=tmp_path (or stub task_mutex.hold)")
        _orig_init(self, name=name, stale_minutes=stale_minutes,
                   base_dir=base_dir)
    monkeypatch.setattr(TaskMutex, "__init__", guarded)
