"""A limited export can be defined, saved and made from the Where Is My Data tab.

WHY: routine lab work never needs a terminal. The window must save the same
definition the command takes, offer only cohorts and columns the full export
has, and the tab must show a limited export the nightly job failed to remake
without anyone opening a log.
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from mousedb.exporters import layout, subsets  # noqa: E402

_APP = []


@pytest.fixture
def qt():
    from PyQt5.QtWidgets import QApplication
    if QApplication.instance() is None:
        _APP.append(QApplication([]))


@pytest.fixture
def lab(tmp_path, monkeypatch):
    root = tmp_path / "db"
    monkeypatch.setenv("MOUSEDB_ROOT", str(root))
    cur = root / "exports" / "current"
    for cohort in ("PROJA_01", "PROJA_02"):
        f = layout.cohort_file(cur, "ODC_reaches", cohort, cohort)
        f.parent.mkdir(parents=True)
        f.write_text("subject_id,video_name\n", encoding="utf-8")
    pd.DataFrame({"VariableName": ["subject_id", "video_name", "start_frame"]}).to_csv(
        cur / "ODC_reaches_DATA_DICTIONARY.csv", index=False)
    return root


def test_the_form_becomes_the_definition_the_command_saves():
    p = subsets.validate("pre", __import__("mousedb.gui.limited_export_dialog", fromlist=["x"])
                         .profile_from_form({
                             "cohorts": ["PROJA_01"], "tray_types": ["P"], "window": "last_before_injury",
                             "n": 3, "window_tray": "P", "kinds": ["ODC_reaches"],
                             "columns": {"ODC_reaches": ["video_name"], "ODC_sessions": ["Date"]},
                             "flag_column": " used_for_x ", "flag_rule": None, "nightly": False}),
                         rules={})
    assert p == {"cohorts": ["PROJA_01"], "tray_types": ["P"],
                 "window": {"type": "last_before_injury", "n": 3, "tray_type": "P"},
                 "kinds": ["ODC_reaches"], "columns": {"ODC_reaches": ["video_name"]},
                 "flag": {"column": "used_for_x", "rule": None}, "nightly": False}


def test_the_window_offers_the_exports_cohorts_and_columns_and_saves_for_everyone(qt, lab):
    from PyQt5.QtCore import Qt
    from mousedb.gui.limited_export_dialog import LimitedExportDialog
    d = LimitedExportDialog()
    assert [d.cohorts.item(i).text() for i in range(d.cohorts.count())] == ["PROJA_01", "PROJA_02"]
    assert [d.col_list.item(i).text() for i in range(d.col_list.count())] == [
        "subject_id", "video_name", "start_frame"]
    d.name.setText("pre")
    d.cohorts.item(0).setCheckState(Qt.Checked)
    d.trays["P"].setChecked(True)
    d.w_last.setChecked(True)
    d.col_list.item(2).setCheckState(Qt.Unchecked)
    assert d.save()
    from mousedb import sheet_projects
    cfg = json.loads(sheet_projects.config_path().read_text())
    saved = cfg["export_subsets"]["pre"]
    assert saved["cohorts"] == ["PROJA_01"] and saved["window"]["n"] == 3
    assert saved["columns"] == {"ODC_reaches": ["subject_id", "video_name"]}
    # Reopened, it shows what was saved.
    d2 = LimitedExportDialog()
    d2.saved.setCurrentText("pre")
    assert d2.w_last.isChecked() and d2.trays["P"].isChecked()
    assert d2.col_list.item(2).checkState() == Qt.Unchecked


def test_the_tab_has_the_button_and_shows_a_failed_remake(qt, lab, monkeypatch):
    from mousedb.gui import data_status_tab
    assert hasattr(data_status_tab.DataStatusTab, "limited_export")
    subsets.save_profile("pre", {"cohorts": ["PROJA_01"]})
    out = lab / "exports" / "subsets"
    out.mkdir(parents=True)
    (out / subsets.STATUS_NAME).write_text(json.dumps({"pre": {
        "ok": False, "last_attempt": "2026-05-02T00:30:00",
        "result": "SubsetRefused: no column ghost"}}))
    lines = data_status_tab.limited_export_lines()
    assert any(l.strip().startswith("[FAIL] pre") and "ghost" in l for l in lines)


def test_check_only_saves_nothing_and_checks_what_the_window_shows(qt, lab, monkeypatch):
    from PyQt5.QtCore import Qt
    from mousedb import sheet_projects
    from mousedb.gui.limited_export_dialog import LimitedExportDialog
    d = LimitedExportDialog()
    d.name.setText("trial")
    d.cohorts.item(1).setCheckState(Qt.Checked)
    started = []
    monkeypatch.setattr(d, "_start", lambda dry_run, definition_file=None:
                        started.append((dry_run, json.loads(definition_file.read_text()))))
    d.check()
    assert started and started[0][0] is True
    assert started[0][1]["cohorts"] == ["PROJA_02"]
    cfg = sheet_projects.config_path()
    assert not cfg.is_file() or "export_subsets" not in json.loads(cfg.read_text())


def test_the_window_asks_before_replacing_a_saved_definition(qt, lab, monkeypatch):
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QMessageBox
    from mousedb.gui.limited_export_dialog import LimitedExportDialog
    subsets.save_profile("pre", {"cohorts": ["PROJA_01"]})
    before = subsets.profiles()["pre"]
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a) or QMessageBox.No)
    d = LimitedExportDialog()                   # a new one, typed under a taken name
    d.name.setText("pre")
    d.cohorts.item(1).setCheckState(Qt.Checked)
    assert d.save() is False
    assert asked and subsets.profiles()["pre"] == before
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    assert d.save() is True
    assert subsets.profiles()["pre"]["cohorts"] == ["PROJA_02"]
    from mousedb import sheet_projects
    kept = json.loads(sheet_projects.config_path().read_text())[subsets.REPLACED_KEY]["pre"]
    assert kept[-1]["definition"] == before


ROUND_TRIP = {
    # every cohort of a project, future ones included, with a description
    "whole": {"project": "PROJA", "kinds": ["ODC_reaches"], "description": "for one analysis"},
    # a column the full export has since renamed, and a cohort no longer exported
    "stale": {"cohorts": ["PROJA_01", "PROJA_09"], "kinds": ["ODC_reaches"],
              "columns": {"ODC_reaches": ["subject_id", "old_column_name"]}},
    # a fixed list naming every column there is today, in its own order
    "fixed": {"cohorts": ["PROJA_02"], "kinds": ["ODC_reaches"],
              "columns": {"ODC_reaches": ["start_frame", "subject_id", "video_name"]}},
}


@pytest.mark.parametrize("name", sorted(ROUND_TRIP))
def test_a_saved_definition_opened_and_saved_again_is_unchanged(qt, lab, monkeypatch, name):
    """Open a saved definition and press Save without touching anything: the
    window must not rewrite it (drop a column it does not show, turn a project
    into a cohort list, lose the description)."""
    from PyQt5.QtWidgets import QMessageBox
    from mousedb.gui.limited_export_dialog import LimitedExportDialog
    subsets.save_profile(name, ROUND_TRIP[name])
    before = subsets.profiles()[name]
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a) or QMessageBox.No)
    d = LimitedExportDialog()
    d.saved.setCurrentText(name)
    assert d.save() is True
    assert subsets.profiles()[name] == before
    assert not asked                     # nothing changed, so nothing to confirm


def test_a_column_choice_survives_an_unreadable_dictionary(qt, lab, monkeypatch):
    from mousedb.gui.limited_export_dialog import LimitedExportDialog
    subsets.save_profile("pre", {"cohorts": ["PROJA_01"], "kinds": ["ODC_reaches"],
                                 "columns": {"ODC_reaches": ["video_name"]}})
    before = subsets.profiles()["pre"]
    (lab / "exports" / "current" / "ODC_reaches_DATA_DICTIONARY.csv").unlink()
    d = LimitedExportDialog()
    d.saved.setCurrentText("pre")
    assert d.save() is True
    assert subsets.profiles()["pre"] == before


def test_a_column_the_exports_lack_is_shown_and_can_be_taken_out(qt, lab, monkeypatch):
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import QMessageBox
    from mousedb.gui.limited_export_dialog import LimitedExportDialog
    subsets.save_profile("pre", {"cohorts": ["PROJA_01"], "kinds": ["ODC_reaches"],
                                 "columns": {"ODC_reaches": ["video_name", "old_column_name"]}})
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    d = LimitedExportDialog()
    d.saved.setCurrentText("pre")
    texts = [d.col_list.item(i).text() for i in range(d.col_list.count())]
    assert texts[-1].startswith("old_column_name") and "not in the current exports" in texts[-1]
    d.col_list.item(d.col_list.count() - 1).setCheckState(Qt.Unchecked)   # taken out
    d.col_list.item(2).setCheckState(Qt.Checked)                          # start_frame added
    assert d.save() is True
    assert subsets.profiles()["pre"]["columns"] == {"ODC_reaches": ["video_name", "start_frame"]}
