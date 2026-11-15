"""A phase refresh that fails after a save is put right and seen.

WHY: after any save that can change a cohort's test phases (a start date, an
animal moved, a pellet score's date or tray), the database re-derives that
cohort's phases. When that failed, the save stood -- its rows carrying labels
from the cohort's OLD dates -- and the only trace was a line printed on a
console nobody reads. Now the cohort goes on the reach import's list of
cohorts still to re-label (the nightly import re-derives it), the window shows
a warning box, and a sheet import made from the window records the failure in
its ledger line.
"""
import contextlib
import datetime as dt
import json

from mousedb import import_reaches as ir
from mousedb.database import Database
from mousedb.schema import Cohort, Project

from tests.test_videos_back_in_review_leave_the_database import _features, _setup

COHORT = "PROJA_01"


def _failing_refresh(monkeypatch):
    from mousedb import backfill

    def boom(db=None, cohort_id=None, **kw):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(backfill, "backfill_phases", boom)


def _configured_db(tmp_path, monkeypatch):
    root = tmp_path / "dbroot"
    root.mkdir()
    monkeypatch.setenv("MOUSEDB_ROOT", str(root))
    monkeypatch.setenv("MOUSEDB_DB_PATH", str(root / "connectome.db"))
    db = Database(root / "connectome.db")
    db.init_db()
    return db, root / "logs" / ir.PENDING_PHASES_NAME


def _save_a_cohort(db):
    with db.session() as s:
        s.add(Project(project_code="PROJA", project_name="test project"))
        s.add(Cohort(cohort_id=COHORT, project_code="PROJA", start_date=dt.date(2026, 1, 1)))


def _pending(path):
    """The cohort names in the list file (an object {cohort: queue count}; an
    older version wrote a plain list)."""
    return sorted(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else None


def test_a_failed_refresh_puts_the_cohort_on_the_reach_imports_list(tmp_path, monkeypatch):
    db, pending = _configured_db(tmp_path, monkeypatch)
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps(["PROJB_02"]), encoding="utf-8")   # already waiting
    told = []
    db.on_phase_refresh_failed = told.append
    _failing_refresh(monkeypatch)
    _save_a_cohort(db)                                   # the save itself stands
    assert _pending(pending) == ["PROJA_01", "PROJB_02"]
    rec = db.phase_refreshes[-1]
    assert rec["cohort_id"] == COHORT and "database is locked" in rec["error"]
    assert rec.get("queued_for_reach_import") is True
    assert len(told) == 1 and told[0]["cohort_id"] == COHORT and told[0]["error"]
    with db.session() as s:
        assert s.query(Cohort).filter_by(cohort_id=COHORT).count() == 1


def test_a_database_that_is_not_the_configured_one_leaves_the_list_alone(tmp_path, monkeypatch):
    db, pending = _configured_db(tmp_path, monkeypatch)
    other = Database(tmp_path / "a_copy.db")
    other.init_db()
    _failing_refresh(monkeypatch)
    _save_a_cohort(other)
    assert _pending(pending) is None
    assert other.phase_refreshes[-1].get("queued_for_reach_import") is False


def test_a_cohort_queued_while_the_import_runs_is_kept_and_relabelled(tmp_path, monkeypatch):
    """The reach import adds to the list, it never overwrites it from memory."""
    root, db = _setup(tmp_path, monkeypatch)
    folder = root / "Analyzed" / "Project" / "PROJA01"
    _features(folder, "20260102_PROJA0101_P1")
    _features(folder, "20260102_PROJA0102_P1")
    monkeypatch.setattr(ir, "LEDGER_SAVE_SECONDS", 0)
    real = ir.import_file
    calls = []

    def queued_meanwhile(conn, path, dry_run=False):
        if not calls:
            # A save in the window fails its refresh while the import runs.
            ir.queue_pending_phases(["PROJB_02"])
        calls.append(path.name)
        return real(conn, path, dry_run=dry_run)
    monkeypatch.setattr(ir, "import_file", queued_meanwhile)
    import mousedb.backfill as bf
    relabelled = []
    monkeypatch.setattr(bf, "backfill_phases", lambda **k: relabelled.append(k["cohort_id"]))
    ir.run(log=lambda *_: None)
    assert sorted(relabelled) == ["PROJA_01", "PROJB_02"]
    assert ir.load_pending_phases() == set()


def test_a_cohort_relabelled_earlier_and_queued_again_stays_on_the_list(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    _features(root / "Analyzed" / "Project" / "PROJA01", "20260102_PROJA0101_P1")
    _features(root / "Analyzed" / "Project" / "PROJB02", "20260102_PROJB0201_P1")
    import mousedb.backfill as bf
    relabelled = []

    def relabel(**k):
        relabelled.append(k["cohort_id"])
        if k["cohort_id"] == "PROJB_02":
            # PROJA_01 was done first; a failure elsewhere queues it again.
            ir.queue_pending_phases(["PROJA_01"])
    monkeypatch.setattr(bf, "backfill_phases", relabel)
    ir.run(log=lambda *_: None)
    assert relabelled == ["PROJA_01", "PROJB_02"]
    assert ir.load_pending_phases() == {"PROJA_01"}


def test_a_cohort_queued_while_it_is_being_relabelled_stays_on_the_list(tmp_path, monkeypatch):
    """A save of cohort X that lands while the import re-labels X: the
    re-label may have read X's dates before that save, so X must stay on the
    list for the next run. Taking it off left X with stale labels and nothing
    reporting it."""
    root, db_file = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("MOUSEDB_ROOT", str(tmp_path / "dbroot"))
    monkeypatch.setenv("MOUSEDB_DB_PATH", str(db_file))
    window_db = Database(db_file)
    _features(root / "Analyzed" / "Project" / "PROJA01", "20260102_PROJA0101_P1")
    import mousedb.backfill as bf
    relabelled = []
    queued = []

    def relabel(**k):
        relabelled.append(k["cohort_id"])
        # The window saves PROJA_01 now; its own refresh fails (the import is
        # writing this cohort) and it queues the cohort, as the window does.
        queued.append(window_db._queue_for_reach_import(k["cohort_id"]))
    monkeypatch.setattr(bf, "backfill_phases", relabel)
    res = ir.run(log=lambda *_: None)
    assert res.errors == 0 and queued == [True]
    assert relabelled == ["PROJA_01"]
    assert ir.load_pending_phases() == {"PROJA_01"}
    # The next run re-labels it and, with nothing queued meanwhile, clears it.
    monkeypatch.setattr(bf, "backfill_phases", lambda **k: relabelled.append(k["cohort_id"]))
    ir.run(log=lambda *_: None)
    assert relabelled == ["PROJA_01", "PROJA_01"]
    assert ir.load_pending_phases() == set()


def test_a_list_written_by_an_older_version_is_read_and_relabelled(tmp_path, monkeypatch):
    """Older versions wrote the list as a plain JSON list of cohort names."""
    root, db = _setup(tmp_path, monkeypatch)
    pending = tmp_path / "dbroot" / "logs" / ir.PENDING_PHASES_NAME
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps(["PROJB_02"]), encoding="utf-8")
    _features(root / "Analyzed" / "Project" / "PROJA01", "20260102_PROJA0101_P1")
    import mousedb.backfill as bf
    relabelled = []
    monkeypatch.setattr(bf, "backfill_phases", lambda **k: relabelled.append(k["cohort_id"]))
    ir.run(log=lambda *_: None)
    assert relabelled == ["PROJA_01", "PROJB_02"]
    assert ir.load_pending_phases() == set()


def test_a_window_import_whose_refresh_failed_is_recorded_as_failed():
    import pytest
    pytest.importorskip("PyQt5")
    from mousedb.gui import app
    mark = getattr(app, "mark_failed_phase_refresh", None)
    assert mark is not None, "the window's sheet import does not record a failed refresh"
    entry = {"success": True, "error": None,
             "phases_rederived": {"cohort_id": COHORT, "error": "RuntimeError: locked",
                                  "queued_for_reach_import": True}}
    mark(entry, COHORT)
    assert entry["success"] is False
    assert "re-deriving PROJA_01's phases failed" in entry["error"]
    assert "the next reach import re-labels it" in entry["error"]
    ok = {"success": True, "error": None,
          "phases_rederived": {"cohort_id": COHORT, "pellet_rows_relabelled": 3}}
    mark(ok, COHORT)
    assert ok["success"] is True and ok["error"] is None
    text = app.phase_refresh_failed_text(entry["phases_rederived"])
    assert "was saved" in text and "FAILED" in text and "next scheduled reach import" in text


def test_a_share_drop_while_reading_the_list_keeps_every_cohort_on_it(tmp_path, monkeypatch):
    """A read that fails as an unreachable share does (FileNotFoundError on
    Windows) is not an empty list: the cohorts already waiting are kept, the
    queue call says it did not queue, and why."""
    from tests.test_killed_import_resumes import _share_down
    db, pending = _configured_db(tmp_path, monkeypatch)
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text(json.dumps(["PROJB_02", "PROJC_03"]), encoding="utf-8")
    monkeypatch.setattr(ir.time, "sleep", lambda s: None)
    _share_down(monkeypatch, type(pending), ir.PENDING_PHASES_NAME)
    _failing_refresh(monkeypatch)
    said = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: said.append(" ".join(map(str, a))))
    _save_a_cohort(db)
    monkeypatch.undo()
    assert _pending(pending) == ["PROJB_02", "PROJC_03"]         # nothing dropped
    assert db.phase_refreshes[-1].get("queued_for_reach_import") is False
    assert any("Could not queue cohort PROJA_01" in s and "backfill-phases" in s for s in said)


def test_a_cohort_queued_between_the_imports_read_and_write_is_not_lost(tmp_path, monkeypatch):
    """The two writers of the list interleave: the window queues a cohort
    after the import has read the list and before it writes it back. With a
    plain read-change-write the import's write dropped that cohort; under the
    list's lock the window's write waits for the import's and then adds to
    what the import wrote."""
    import threading
    import time as _time
    root, db_file = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("MOUSEDB_ROOT", str(tmp_path / "dbroot"))
    monkeypatch.setenv("MOUSEDB_DB_PATH", str(db_file))
    window_db = Database(db_file)
    _features(root / "Analyzed" / "Project" / "PROJA01", "20260102_PROJA0101_P1")
    import mousedb.backfill as bf
    relabelled = []
    monkeypatch.setattr(bf, "backfill_phases", lambda **k: relabelled.append(k["cohort_id"]))
    real_load = ir.load_pending_entries
    window = []

    def load_then_window_queues(consequence=ir.BEFORE_IMPORTING):
        now = real_load(consequence)
        if consequence == ir.PART_WAY and not window:
            # The import's checkpoint has just read the list. The window's
            # failed refresh queues a cohort now; give it time to write.
            t = threading.Thread(
                target=lambda: window.append(window_db._queue_for_reach_import("PROJB_02")))
            window.append(t)
            t.start()
            t.join(timeout=1.5)
        return now
    monkeypatch.setattr(ir, "load_pending_entries", load_then_window_queues)
    ir.run(log=lambda *_: None)
    window[0].join(timeout=30)
    monkeypatch.setattr(ir, "load_pending_entries", real_load)
    assert window[1:] == [True]                      # the window says it queued it
    # Either the import re-labelled it in this run, or it waits for the next.
    assert "PROJB_02" in relabelled or "PROJB_02" in ir.load_pending_phases()
    assert "PROJA_01" in relabelled


def test_a_writer_that_cannot_get_the_lists_lock_changes_nothing(tmp_path, monkeypatch):
    from mousedb.task_mutex import LockWaitTimeout, TaskMutex
    root, db_file = _setup(tmp_path, monkeypatch)
    ir.save_pending_phases({"PROJA_01"})
    monkeypatch.setattr(ir, "PENDING_PHASES_LOCK_WAIT_SECONDS", 0)
    other = TaskMutex("reach-phases-list", base_dir=ir.pending_phases_path().parent)
    assert other.try_acquire()
    try:
        import pytest
        with pytest.raises(LockWaitTimeout):
            ir.update_pending_phases(lambda now: now | {"PROJB_02"})
    finally:
        other.release()
    assert ir.load_pending_phases() == {"PROJA_01"}


class _ImporterWhoseRefreshFailed:
    """Stands in for the sheet importer: it saved the workbook, and the phase
    refresh its commit caused failed (queued or not for the reach import)."""
    db = None

    def __init__(self, queued):
        self.queued = queued

    def import_cohort_file(self, sheet, dry_run=False):
        return {"success": True, "errors": [], "warnings": [], "imported": {"subjects": 1},
                "phases_rederived": {"cohort_id": COHORT, "error": "RuntimeError: locked",
                                     "queued_for_reach_import": self.queued}}


def _scheduled_import(tmp_path, monkeypatch, queued):
    from mousedb import sheet_sync, task_mutex
    monkeypatch.setattr(task_mutex, "hold", lambda *a, **k: contextlib.nullcontext())
    sheet = tmp_path / "PROJA_01_tracking.xlsx"
    sheet.write_bytes(b"x")
    return sheet_sync._import_one(COHORT, sheet, dry_run=False, triggered_by="test",
                                  imp=_ImporterWhoseRefreshFailed(queued))


def test_the_scheduled_sheet_import_says_the_reach_import_relabels_a_queued_cohort(
        tmp_path, monkeypatch):
    """The same state reads the same way whichever route imported the sheet:
    a cohort the reach import will re-label is not sent to backfill-phases."""
    entry = _scheduled_import(tmp_path, monkeypatch, queued=True)
    assert entry["success"] is False
    assert "re-deriving PROJA_01's phases failed" in entry["error"]
    assert entry["error"].endswith("the next reach import re-labels it")
    assert "backfill-phases" not in entry["error"]


def test_the_scheduled_sheet_import_says_backfill_when_the_cohort_could_not_be_queued(
        tmp_path, monkeypatch):
    entry = _scheduled_import(tmp_path, monkeypatch, queued=False)
    assert entry["success"] is False
    assert entry["error"].endswith("run 'mousedb backfill-phases'")
