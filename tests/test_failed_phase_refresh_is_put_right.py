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
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


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
            ir.save_pending_phases(ir.load_pending_phases() | {"PROJB_02"})
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
            ir.save_pending_phases(ir.load_pending_phases() | {"PROJA_01"})
    monkeypatch.setattr(bf, "backfill_phases", relabel)
    ir.run(log=lambda *_: None)
    assert relabelled == ["PROJA_01", "PROJB_02"]
    assert ir.load_pending_phases() == {"PROJA_01"}


class _FakeBox:
    """Stands in for QMessageBox: the preview box is answered with its first
    button (Import), and every other box is only recorded."""
    Warning = Information = Close = Yes = No = 0
    AcceptRole = DestructiveRole = RejectRole = 0
    shown = []

    def __init__(self, *a, **k):
        self._buttons = []

    def __getattr__(self, name):             # setWindowTitle, setText, setIcon ...
        return lambda *a, **k: None

    def addButton(self, text, role):
        self._buttons.append(text)
        return text

    def exec_(self):
        return 0

    def clickedButton(self):
        return self._buttons[0]

    @classmethod
    def _record(cls, kind):
        return staticmethod(lambda *a, **k: cls.shown.append((kind, a[1:])))


for _kind in ("warning", "critical", "information", "question"):
    setattr(_FakeBox, _kind, _FakeBox._record(_kind))


class _SheetImporter:
    """Stands in for ExcelImporter: a clean preview, then a real import whose
    commit saved the rows but whose phase refresh failed."""

    def __init__(self, db):
        pass

    def _extract_cohort_from_filename(self, name):
        return COHORT

    def import_cohort_file(self, path, dry_run=False):
        if dry_run:
            return {"imported": {"subjects": 1}, "warnings": [], "errors": []}
        return {"imported": {"subjects": 1}, "warnings": [], "errors": [],
                "phases_rederived": {"cohort_id": COHORT, "error": "RuntimeError: locked",
                                     "queued_for_reach_import": True}}


def test_a_window_import_whose_refresh_failed_is_recorded_as_failed(tmp_path, monkeypatch):
    """Drives the window's own "Import from Excel" path (CohortSetupTab.
    _import_from_excel) end to end, with the dialogs and the importer stood
    in, and reads the ledger line it writes. Without the failed-refresh rule
    in that path the line says success, and the Tracking Sheets tab shows a
    cohort whose phase labels are stale as done."""
    import types
    import pytest
    pytest.importorskip("PyQt5")
    from mousedb import importers, task_mutex
    from mousedb.gui import app

    sheet = tmp_path / "PROJA_01_tracking.xlsx"
    sheet.write_bytes(b"x")
    monkeypatch.setattr(app, "QMessageBox", _FakeBox)
    monkeypatch.setattr(_FakeBox, "shown", [])
    monkeypatch.setattr(app.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(sheet), "")))
    monkeypatch.setattr(app.QApplication, "setOverrideCursor", staticmethod(lambda *a: None))
    monkeypatch.setattr(app.QApplication, "restoreOverrideCursor", staticmethod(lambda: None))
    monkeypatch.setattr(importers, "ExcelImporter", _SheetImporter)
    monkeypatch.setattr(task_mutex, "hold", lambda *a, **k: contextlib.nullcontext())

    class _NoCohortYet:
        def query(self, *a):
            return self

        def filter_by(self, **k):
            return self

        def first(self):
            return None

    ledger = []
    tab = types.SimpleNamespace(
        db=types.SimpleNamespace(session=lambda: contextlib.nullcontext(_NoCohortYet())),
        _may_wait_for_database_lock=lambda: True,
        _append_sheet_ledger=lambda entry, path: ledger.append(dict(entry)),
        _refresh_all_cohort_lists=lambda: None)
    app.CohortSetupTab._import_from_excel(tab)

    assert len(ledger) == 1
    entry = ledger[0]
    assert entry["success"] is False                 # not "done" while labels are stale
    assert "re-deriving PROJA_01's phases failed" in entry["error"]
    assert entry["error"].endswith("the next reach import re-labels it")
    assert entry["phases_rederived"]["queued_for_reach_import"] is True


def test_the_warning_box_says_the_save_stood_and_what_happens_next():
    """A test of the warning box's wording helper (phase_refresh_failed_text)
    only; the window that shows it is not driven here."""
    import pytest
    pytest.importorskip("PyQt5")
    from mousedb.gui import app
    text = app.phase_refresh_failed_text(
        {"cohort_id": COHORT, "error": "RuntimeError: locked", "queued_for_reach_import": True})
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
    after the import has read the list and just before its write lands. With
    a plain read-change-write the import's write dropped that cohort; under
    the list's lock the window's write waits for the import's and then adds
    to what the import wrote.

    The window's queue call runs on its own thread, started from inside the
    import's first write of the list, and is given a moment to finish before
    that write lands. A writer that does not wait for the import finishes in
    that moment and is then overwritten; one that waits adds afterwards."""
    import threading
    root, db_file = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("MOUSEDB_ROOT", str(tmp_path / "dbroot"))
    monkeypatch.setenv("MOUSEDB_DB_PATH", str(db_file))
    window_db = Database(db_file)
    _features(root / "Analyzed" / "Project" / "PROJA01", "20260102_PROJA0101_P1")
    import mousedb.backfill as bf
    relabelled = []
    monkeypatch.setattr(bf, "backfill_phases", lambda **k: relabelled.append(k["cohort_id"]))
    real_save, real_load = ir.save_pending_phases, ir.load_pending_phases

    def window_queues():
        queue = getattr(window_db, "_queue_for_reach_import", None)
        if queue is not None:
            return queue("PROJB_02")
        real_save(real_load() | {"PROJB_02"})        # a plain read-change-write
        return True

    window = []

    def import_writes(cohorts):
        if not window:
            t = threading.Thread(target=lambda: window.append(window_queues()))
            window.append(t)
            t.start()
            t.join(timeout=1.5)
        return real_save(cohorts)
    monkeypatch.setattr(ir, "save_pending_phases", import_writes)
    ir.run(log=lambda *_: None)
    window[0].join(timeout=30)
    monkeypatch.setattr(ir, "save_pending_phases", real_save)
    assert window[1:] == [True]                      # the window says it queued it
    # Either the import re-labelled it in this run, or it waits for the next.
    assert "PROJB_02" in relabelled or "PROJB_02" in real_load()
    assert "PROJA_01" in relabelled


def test_a_writer_that_cannot_get_the_lists_lock_changes_nothing(tmp_path, monkeypatch):
    """A test of the locked read-and-write helper (update_pending_phases)
    only: a writer that cannot get the list's lock in time raises and leaves
    the list as it was."""
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
