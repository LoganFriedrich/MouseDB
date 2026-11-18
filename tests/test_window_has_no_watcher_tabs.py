"""The data-entry window has no Video Pipeline or Archive Status tab, and the
tabs after Database Browser are numbered on from it.

WHY: those two tabs showed what ONE computer's MouseReach watcher had
recorded (its own watcher database), laid out as if it were the pipeline's
state. Work done on other computers and anything waiting in the review
queues' folders was not in them, so a person reading them saw videos as not
started, or missing, that the pipeline had in fact handled. The lab decided
to remove them. The whole pipeline is on the Where Is My Data tab (read from
the pipeline's folders); `mousedb video-status` and `mousedb archive-status`
still show one watcher's records, each saying so above its numbers.

The tab numbers are the ones the operator documentation uses, so they must
run on without a gap once the two tabs are gone.
"""
import importlib.util
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt5")


@pytest.fixture
def qapp():
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    """The real data-entry window, built over a scratch database."""
    from mousedb import database
    from mousedb.database import init_database
    from mousedb.gui import app as app_module

    # init_database sets the process-wide database; keep that to this test.
    monkeypatch.setattr(database, "_db", None)
    db = init_database(tmp_path / "scratch.db")
    monkeypatch.setattr(app_module, "init_database", lambda *a, **k: db)
    w = app_module.DataEntryWindow()
    yield w
    w.close()


def _titles(window):
    return [window.tabs.tabText(i) for i in range(window.tabs.count())]


def test_the_window_has_no_watcher_record_tabs(window):
    titles = _titles(window)
    assert not [t for t in titles if "Video Pipeline" in t], titles
    assert not [t for t in titles if "Archive Status" in t], titles


def test_the_tabs_after_the_database_browser_are_numbered_on(window):
    titles = _titles(window)
    # The first tabs are built unconditionally; the later ones each sit in
    # their own try/except, so only the ones that built are checked here.
    assert "7. Database Browser" in titles
    for later in ("8. Tracking Sheets", "9. Where Is My Data", "10. Study Facts"):
        bare = later.split(". ", 1)[1]
        built = [t for t in titles if t.endswith(bare)]
        assert built == [later] or built == [], titles
    # Where Is My Data -- the whole pipeline's state -- stays.
    assert "9. Where Is My Data" in titles


def test_the_watcher_tab_modules_are_gone():
    assert importlib.util.find_spec("mousedb.gui.video_pipeline_tab") is None
    assert importlib.util.find_spec("mousedb.gui.archive_status_tab") is None
