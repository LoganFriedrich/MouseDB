"""The cohort-setup tab's Import and Replace buttons end the way every sheet
import ends.

WHY: both buttons used to stop after the importer and a ledger line. The
animal details, the hand-scores file the reaching pipeline compares each video
with, and the FIX THESE files beside the sheets then described the previous
import until the nightly one -- so the pipeline went on comparing videos with
scores the person had just corrected. Now Import is the same route as
``mousedb import --file`` (sheet_sync.import_sheet_file), and Replace, once it
has committed, finishes through sheet_sync.after_gui_replace.
"""
import contextlib
import json
import os
from datetime import date
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from mousedb import hand_scores_file as hsf  # noqa: E402
from mousedb import sheet_sync as ss  # noqa: E402
from mousedb import task_mutex  # noqa: E402

COHORT = "CNT_77"
SHEET = "Connectome_77_Animal_Tracking.xlsx"


@pytest.fixture
def app_mod(monkeypatch):
    pytest.importorskip("PyQt5")
    from mousedb.gui import app
    return app


class _Box:
    """QMessageBox stand-in: the preview's button named in CHOICE is pressed;
    every message shown is kept in SHOWN."""
    AcceptRole = DestructiveRole = RejectRole = 0
    Warning = Information = Close = Yes = No = 0
    CHOICE = "Import"
    SHOWN = []

    def __init__(self, *a, **k):
        self._buttons = []

    def setWindowTitle(self, *a):
        pass

    def setText(self, *a):
        pass

    def setIcon(self, *a):
        pass

    def setStandardButtons(self, *a):
        pass

    def addButton(self, text, role):
        self._buttons.append(text)
        return text

    def exec_(self):
        pass

    def clickedButton(self):
        return next(b for b in self._buttons if b.startswith(self.CHOICE))

    @classmethod
    def _show(cls, parent, title, text, *a):
        cls.SHOWN.append((title, text))

    information = warning = critical = _show

    @staticmethod
    def question(*a, **k):
        return _Box.Yes


@pytest.fixture
def world(tmp_path, monkeypatch, app_mod):
    """A scratch database holding the cohort, a sheet in the configured sheets
    folder, and every outside effect recorded instead of done."""
    import mousedb.animal_records as ar
    import mousedb.cohort_sheets as cs
    import mousedb.importers as importers
    from mousedb.database import init_database
    from mousedb.schema import Cohort

    sheets = tmp_path / "sheets"
    sheets.mkdir()
    sheet = sheets / SHEET
    sheet.write_bytes(b"x")
    monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv(cs.ENV_VAR, str(sheets))
    monkeypatch.setattr(ss, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(task_mutex, "hold", lambda *a, **k: contextlib.nullcontext())
    db = init_database(tmp_path / "scratch.db")
    with db.session() as s:
        s.add(Cohort(cohort_id=COHORT, project_code="CNT", start_date=date(2026, 1, 5)))

    class Importer:
        def __init__(self, db=None):
            self.db = db

        def _extract_cohort_from_filename(self, name):
            return COHORT

        def import_cohort_file(self, path, dry_run=False):
            return {"success": True, "imported": {"pellet_scores": 20},
                    "warnings": [], "errors": []}

    monkeypatch.setattr(importers, "ExcelImporter", Importer)
    seen = SimpleNamespace(details=[], hand_scores=[], fix_these=[])
    monkeypatch.setattr(ar, "sync_cohort", lambda db, cid, sh, kind, dry_run=False, **k:
                        seen.details.append((cid, dry_run, k)) or
                        {"records": 4, "warnings": [], "error": None, "kept": []})
    monkeypatch.setattr(ss, "WRITE_HAND_SCORES", True)
    monkeypatch.setattr(ss, "WRITE_FIX_THESE", True)
    monkeypatch.setattr(hsf, "write", lambda **k: seen.hand_scores.append(k) or
                        {"rows": 20, "videos": 1})
    import mousedb.attention as attention
    monkeypatch.setattr(attention, "write_all_fix_these", lambda d, ids:
                        seen.fix_these.append((str(d), list(ids))) or
                        {i: "none" for i in ids})

    monkeypatch.setattr(app_mod, "QMessageBox", _Box)
    monkeypatch.setattr(app_mod, "QApplication", SimpleNamespace(
        setOverrideCursor=lambda *a: None, restoreOverrideCursor=lambda *a: None))
    monkeypatch.setattr(app_mod.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(sheet), "")))
    monkeypatch.setattr(app_mod, "clear_cohort_sheet_rows",
                        lambda session, cid, delete=True: {"removed": {}, "kept": []})
    monkeypatch.setattr(app_mod, "replace_preview_lines", lambda would: [])
    _Box.SHOWN = []
    seen.db, seen.sheet, seen.sheets = db, sheet, sheets
    return seen


def _tab(app_mod, db):
    """The tab's own methods on a stand-in for the widget (no window needed)."""
    T = app_mod.CohortSetupTab
    tab = SimpleNamespace(db=db)
    for name in dir(T):
        if name.startswith("_") and not name.startswith("__") and callable(getattr(T, name)):
            fn = getattr(T, name)
            setattr(tab, name, (lambda f: lambda *a, **k: f(tab, *a, **k))(fn))
    tab._replace_summary = T._replace_summary
    if hasattr(T, "_after_import_lines"):
        tab._after_import_lines = T._after_import_lines
    tab._sheet_start_date = lambda *a: None
    tab._may_wait_for_database_lock = lambda: True
    tab._refresh_all_cohort_lists = lambda: None
    return tab


def _ledger():
    return [json.loads(l) for l in open(ss.LEDGER, encoding="utf-8").read().splitlines()]


def test_the_import_button_copies_details_and_rewrites_both_files(app_mod, world):
    _Box.CHOICE = "Import"
    app_mod.CohortSetupTab._import_from_excel(_tab(app_mod, world.db))

    assert [d[:2] for d in world.details] == [(COHORT, False)]
    assert len(world.hand_scores) == 1
    # A named workbook is not a listing of the sheets folder.
    assert world.hand_scores[0]["full_run"] is False
    assert world.hand_scores[0]["sheets_seen"] is None
    # FIX THESE only into the configured sheets folder.
    assert world.fix_these == [(str(world.sheets), [COHORT])]
    (line,) = _ledger()                                   # one line, not two
    assert line["triggered_by"] == "gui_cohort_setup" and line["success"] is True
    assert line["imported"]["animal_records"] == 4


def test_a_committed_replace_copies_details_and_rewrites_both_files(app_mod, world, monkeypatch):
    monkeypatch.setattr(app_mod, "replace_cohort_from_sheet", lambda *a, **k: {
        "success": True, "imported": {"pellet_scores": 20}, "warnings": [], "errors": [],
        "replace": {"committed": True, "removed": {}, "kept": 0, "kept_rows": [],
                    "start_date": None}})
    _Box.CHOICE = "Replace"
    app_mod.CohortSetupTab._import_from_excel(_tab(app_mod, world.db))

    assert len(world.details) == 1
    assert world.details[0][2].get("allow_large_removal") is True   # a deliberate rebuild
    assert len(world.hand_scores) == 1 and world.fix_these
    (line,) = _ledger()
    assert line["mode"] == "replace" and line["imported"]["animal_records"] == 4
    assert line["scores_saved"] is True


def test_an_undone_replace_changes_nothing_else(app_mod, world, monkeypatch):
    monkeypatch.setattr(app_mod, "replace_cohort_from_sheet", lambda *a, **k: {
        "success": False, "imported": {"pellet_scores": 0}, "warnings": [],
        "errors": ["Replace of %s undone -- nothing changed" % COHORT],
        "replace": {"committed": False, "removed": {}, "kept": 0, "kept_rows": [],
                    "start_date": None}})
    _Box.CHOICE = "Replace"
    app_mod.CohortSetupTab._import_from_excel(_tab(app_mod, world.db))
    assert world.details == [] and world.hand_scores == []
    (line,) = _ledger()
    assert line["success"] is False and line["scores_saved"] is False


def test_the_import_button_refuses_an_archive_copy(app_mod, world, monkeypatch, tmp_path):
    old = tmp_path / "archive" / SHEET
    old.parent.mkdir()
    old.write_bytes(b"x")
    monkeypatch.setattr(app_mod.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(old), "")))
    _Box.CHOICE = "Import"
    app_mod.CohortSetupTab._import_from_excel(_tab(app_mod, world.db))
    assert world.details == [] and world.hand_scores == []
    assert any("REFUSED" in text for _title, text in _Box.SHOWN)
