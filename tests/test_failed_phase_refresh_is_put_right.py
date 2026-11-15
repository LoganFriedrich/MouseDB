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
