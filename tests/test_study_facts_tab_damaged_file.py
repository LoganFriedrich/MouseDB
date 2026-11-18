"""The Study Facts tab says so when its file is damaged, and saves nothing.

WHY: a damaged study-facts file used to read as "nothing set", so the tab showed
every fact as NOT SET and the next Set wrote back a file holding only that one
fact. The file is now refused (study_facts.StudyFactsFileDamaged); the tab must
show which file is damaged instead of empty values, must not close the window
from inside a button, and must leave the file exactly as it was.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt5")

from mousedb import study_facts as sf  # noqa: E402

DAMAGED = '{"PROJA": {"SpeciesStrainTyp": "strain-a",, "StudyLeader": "someone"}}\n'


@pytest.fixture
def qapp():
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def damaged(tmp_path, monkeypatch):
    path = tmp_path / "study_facts.json"
    path.write_text(DAMAGED, encoding="utf-8")
    monkeypatch.setenv(sf.ENV_VAR, str(path))
    return path


def test_the_tab_names_the_damaged_file_and_shows_no_values(qapp, damaged):
    from mousedb.gui.study_facts_tab import StudyFactsTab
    tab = StudyFactsTab()
    assert "NOT READ" in tab.path_label.text()
    assert str(damaged) in tab.path_label.text()
    assert tab.facts_table.rowCount() == 0
    assert damaged.read_text(encoding="utf-8") == DAMAGED


def test_set_and_exception_buttons_save_nothing_and_say_why(qapp, damaged, monkeypatch):
    from PyQt5.QtWidgets import QInputDialog, QMessageBox
    from mousedb.gui import study_facts_tab as mod
    said = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: said.append(a[2]))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("strain-z", True))
    # No real box may open in a test (an offscreen modal box can crash Qt).
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    tab = mod.StudyFactsTab()
    tab.facts_table.setRowCount(1)
    tab.facts_table.setItem(0, 0, mod.QTableWidgetItem("SpeciesStrainTyp"))
    tab.facts_table.setCurrentCell(0, 0)

    tab._set_fact()
    tab._set_other_fact()
    tab._move_rule(1)
    tab._remove_rule()

    assert damaged.read_text(encoding="utf-8") == DAMAGED
    assert said and all(str(damaged) in text for text in said)
