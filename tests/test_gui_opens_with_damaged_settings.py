"""The data-entry window still opens -- with the Setup tab -- when the settings
cannot give it the database.

WHY: the main window is built around the database, and the database's
location comes from the settings. A damaged settings file (not valid JSON)
is refused rather than read as empty (see
test_damaged_settings_are_refused_not_emptied), so building the main window
raises config.ConfigError. Uncaught, ``mousedb-entry`` died with a traceback
(or showed nothing at all when started from a shortcut), and the Setup tab --
the place that names the damaged file and fixes it -- was inside the window
that never opened. Now the GUI's start-up opens a window holding only the
Setup tab, with the reason above it and in a message box.
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt5")

from mousedb import config  # noqa: E402

DAMAGED = '{"mousedb_root": "/somewhere", "pinned_sheets": {"PROJ_99": "a.xlsx"'


@pytest.fixture
def qapp():
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def damaged_settings(tmp_path, monkeypatch):
    for env in config.KEYS.values():
        monkeypatch.delenv(env, raising=False)
    own = tmp_path / "home" / ".mousedb" / "config.json"
    own.parent.mkdir(parents=True)
    own.write_text(DAMAGED, encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", own)
    monkeypatch.setenv(config.MACHINE_CONFIG_ENV,
                       str(tmp_path / "machine" / "config.json"))
    return own


def test_start_up_with_a_damaged_settings_file_opens_the_setup_tab(
        qapp, damaged_settings, monkeypatch):
    from mousedb import task_mutex
    from mousedb import database
    from mousedb.gui import app as app_module

    # main()'s process-wide settings, kept to this test.
    monkeypatch.setenv("MOUSEDB_DB_TIMEOUT", "30")
    monkeypatch.setattr(task_mutex, "set_wait_minutes", lambda minutes: None)
    monkeypatch.setattr(database, "_db", None)

    class _App:
        """Stands in for the QApplication main() makes (one already exists
        in this test process); its event loop returns at once."""
        def __init__(self, argv):
            pass

        def setStyle(self, style):
            pass

        def exec_(self):
            return 0

    monkeypatch.setattr(app_module, "QApplication", _App)
    shown = {}

    def _record(parent, title, text, *a, **k):
        shown["window"] = parent
        shown["title"] = title
        shown["text"] = text
    monkeypatch.setattr(app_module.QMessageBox, "warning", staticmethod(_record))

    with pytest.raises(SystemExit):
        app_module.main()

    # The person is told, naming the damaged file ...
    assert str(damaged_settings) in shown["text"]
    assert "damaged" in shown["text"].lower()
    # ... over a window that holds the Setup tab, which lists the same file
    # first under its problems.
    window = shown["window"]
    assert window.setup_tab is not None
    problems = window.setup_tab.problems.text()
    assert "damaged" in problems and str(damaged_settings) in problems
    # Nothing was saved over the damaged file by opening the window.
    assert damaged_settings.read_text(encoding="utf-8") == DAMAGED
    window.close()
