"""The cohort-setup tab's Import and Replace buttons end the way every sheet
import ends.

WHY: both buttons would otherwise stop after the importer and a ledger line. The
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
from mousedb.animal_records import sync_cohort as _REAL_SYNC  # noqa: E402

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
    # A deliberate rebuild: a tab read as empty is taken as empty, but never
    # the removal permission, which would also delete an unread tab's details.
    assert world.details[0][2].get("allow_empty_tabs") is True
    assert not world.details[0][2].get("allow_large_removal")
    assert len(world.hand_scores) == 1 and world.fix_these
    (line,) = _ledger()
    assert line["mode"] == "replace" and line["imported"]["animal_records"] == 4
    assert line["scores_saved"] is True


def test_a_replace_that_kept_an_animal_detail_tab_is_not_reported_complete(
        app_mod, world, monkeypatch):
    """WHY: a Replace whose animal-detail copy kept a tab (a refusal, which
    fails every other import route) was shown as "Import complete"."""
    import mousedb.animal_records as ar
    monkeypatch.setattr(app_mod, "replace_cohort_from_sheet", lambda *a, **k: {
        "success": True, "imported": {"pellet_scores": 20}, "warnings": [], "errors": [],
        "replace": {"committed": True, "removed": {}, "kept": 0, "kept_rows": [],
                    "start_date": None}})
    monkeypatch.setattr(ar, "sync_cohort", lambda *a, **k: {
        "records": 4, "warnings": [], "error": None,
        "kept": [{"tab": "0a_Metadata", "unread": True, "why": "the tab was not read"}]})
    _Box.CHOICE = "Replace"
    app_mod.CohortSetupTab._import_from_excel(_tab(app_mod, world.db))
    titles = [t for t, _text in _Box.SHOWN]
    assert "Import complete" not in titles
    assert any("NOT SYNCED animal details '0a_Metadata'" in text for _t, text in _Box.SHOWN)


def test_a_replace_keeps_and_reports_the_details_of_a_renamed_tab(app_mod, world, monkeypatch):
    """WHY: Replace copied the animal details with the removal permission, so
    a tab renamed in the workbook had its stored details deleted, nothing said
    so, and the box read "Import complete". The real copy now keeps them and
    the Replace is reported as not complete."""
    import openpyxl
    import mousedb.animal_records as ar
    wb = openpyxl.Workbook()
    wb.active.title = "0a_Metadata"
    wb.active.append(["SubjectID", "Sex"])
    wb.active.append([COHORT + "_01", "F"])
    wb.save(world.sheet)
    _REAL_SYNC(world.db, COHORT, world.sheet)
    assert _stored_tabs(world.db) == {"0a_Metadata": 1}
    wb = openpyxl.load_workbook(world.sheet)
    wb["0a_Metadata"].title = "0a_Metadata (renamed)"
    wb.save(world.sheet)

    monkeypatch.setattr(ar, "sync_cohort", _REAL_SYNC)
    monkeypatch.setattr(app_mod, "replace_cohort_from_sheet", lambda *a, **k: {
        "success": True, "imported": {"pellet_scores": 20}, "warnings": [], "errors": [],
        "replace": {"committed": True, "removed": {}, "kept": 0, "kept_rows": [],
                    "start_date": None}})
    _Box.CHOICE = "Replace"
    app_mod.CohortSetupTab._import_from_excel(_tab(app_mod, world.db))

    assert _stored_tabs(world.db) == {"0a_Metadata": 1}
    titles = [t for t, _text in _Box.SHOWN]
    assert "Import complete" not in titles
    assert any("NOT SYNCED animal details '0a_Metadata'" in text for _t, text in _Box.SHOWN)
    (line,) = _ledger()
    assert ss.sync_refused(line)


def _stored_tabs(db):
    from mousedb.schema import AnimalRecord
    with db.session() as s:
        out = {}
        for r in s.query(AnimalRecord):
            out[r.source_tab] = out.get(r.source_tab, 0) + 1
        return out


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


def test_a_replace_whose_history_line_was_not_written_says_so(app_mod, world, monkeypatch):
    """WHY: the hand-scores file reads the Replace's import-history line to
    decide whether videos waiting for the cohort's scores may go on. The
    window's ledger write swallowed every failure, so a lost line held those
    videos and the person was never told."""
    monkeypatch.setattr(app_mod, "replace_cohort_from_sheet", lambda *a, **k: {
        "success": True, "imported": {"pellet_scores": 20}, "warnings": [], "errors": [],
        "replace": {"committed": True, "removed": {}, "kept": 0, "kept_rows": [],
                    "start_date": None}})

    def unwritable(entry):
        raise OSError("history file unreachable")

    monkeypatch.setattr(ss, "_append_ledger", unwritable)
    _Box.CHOICE = "Replace"
    app_mod.CohortSetupTab._import_from_excel(_tab(app_mod, world.db))

    assert len(world.hand_scores) == 1                  # the files are still rewritten
    assert any(title == "Replace saved, with problems"
               and "import history not written" in text and "unreachable" in text
               for title, text in _Box.SHOWN)


def test_an_undone_replace_whose_history_line_was_not_written_still_shows_its_box(
        app_mod, world, monkeypatch):
    """WHY: the window's record() raises so a lost history line is shown. On
    the undone path that error escaped after_gui_replace and the window
    button, and an error escaping a button aborts the whole program before
    the "Replace undone" box appears."""
    monkeypatch.setattr(app_mod, "replace_cohort_from_sheet", lambda *a, **k: {
        "success": False, "imported": {"pellet_scores": 0}, "warnings": [],
        "errors": ["Replace of %s undone -- nothing changed" % COHORT],
        "replace": {"committed": False, "removed": {}, "kept": 0, "kept_rows": [],
                    "start_date": None}})

    def unwritable(entry):
        raise OSError("history file unreachable")

    monkeypatch.setattr(ss, "_append_ledger", unwritable)
    _Box.CHOICE = "Replace"
    app_mod.CohortSetupTab._import_from_excel(_tab(app_mod, world.db))

    assert world.details == [] and world.hand_scores == []
    assert any(title == "Replace undone" and "undone -- nothing changed" in text
               and "import history not written" in text and "unreachable" in text
               for title, text in _Box.SHOWN)


def test_after_gui_replace_never_raises_on_an_undone_replace():
    def unwritable(entry):
        raise OSError("history file unreachable")

    out = ss.after_gui_replace(None, COHORT, SHEET, {"replace": {"committed": False}},
                               {"cohort_id": COHORT}, record=unwritable)
    assert "unreachable" in out["ledger_error"]
    assert out["cohorts"][0]["scores_saved"] is False


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


def test_the_replace_button_refuses_an_archive_copy_before_any_preview(
        app_mod, world, monkeypatch, tmp_path):
    """Before: Replace never went through the refusal, so an archive copy was
    previewed and then replaced the cohort's rows with its old values."""
    old = tmp_path / "archive" / SHEET
    old.parent.mkdir()
    old.write_bytes(b"x")
    monkeypatch.setattr(app_mod.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(old), "")))
    replaced = []
    monkeypatch.setattr(app_mod, "replace_cohort_from_sheet",
                        lambda *a, **k: replaced.append(a) or {"success": True, "warnings": []})
    _Box.CHOICE = "Replace"
    app_mod.CohortSetupTab._import_from_excel(_tab(app_mod, world.db))
    assert replaced == []
    assert world.details == [] and world.hand_scores == []
    assert len(_Box.SHOWN) == 1 and "REFUSED" in _Box.SHOWN[0][1]


def test_replace_itself_refuses_an_archive_copy_and_changes_nothing(app_mod, world, tmp_path):
    """The refusal sits in replace_cohort_from_sheet too, so no caller can
    replace a cohort from a copy the folder route would never read."""
    old = tmp_path / "archive" / SHEET
    old.parent.mkdir()
    old.write_bytes(b"x")
    cleared = []
    with pytest.raises(ValueError, match="was not used to replace"):
        app_mod.replace_cohort_from_sheet(
            world.db, old, COHORT,
            importer_factory=lambda db: cleared.append("built") or None,
            hold=lambda **k: cleared.append("locked") or contextlib.nullcontext())
    assert cleared == []
