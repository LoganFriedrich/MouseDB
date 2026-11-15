"""A button pressed while the Where Is My Data tab is busy says so, and only then.

WHY: Refresh and "Refresh exports now" pressed while another job of the tab
ran did nothing and said nothing -- the exports button even wrote
"Rewriting current exports..." first, so the press looked accepted. And a job
that finished and started the next one (Update, then Refresh) was turned away
silently, because its result arrived while its thread was still running.
"""
import os
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


_APP = []   # keeps the one QApplication alive for the whole module


@pytest.fixture
def tab_mod(monkeypatch):
    from PyQt5.QtWidgets import QApplication
    if QApplication.instance() is None:
        _APP.append(QApplication([]))
    from mousedb.gui import data_status_tab
    return data_status_tab


def _busy_tab(tab_mod, shown):
    written = []
    stub = SimpleNamespace(
        _worker=SimpleNamespace(isRunning=lambda: True),
        BUSY_TEXT=getattr(tab_mod.DataStatusTab, "BUSY_TEXT", ""),
        exports=SimpleNamespace(append=written.append),
        snapshot_label=SimpleNamespace(setText=written.append),
        update_label=SimpleNamespace(setText=written.append),
        _show_last_update=lambda: None,
        _on_exports_done=lambda r: None, _on_status=lambda r: None)
    stub._busy = lambda: tab_mod.DataStatusTab._busy(stub) if hasattr(
        tab_mod.DataStatusTab, "_busy") else True
    stub._run = lambda fn, cb: tab_mod.DataStatusTab._run(stub, fn, cb)
    return stub, written


@pytest.mark.parametrize("button", ["refresh_exports", "refresh"])
def test_a_press_while_busy_writes_nothing_and_says_it_is_still_running(tab_mod, monkeypatch, button):
    shown = []
    monkeypatch.setattr(tab_mod.QMessageBox, "information",
                        lambda parent, title, text: shown.append(text))
    import mousedb.data_status
    monkeypatch.setattr(mousedb.data_status, "status", lambda: {}, raising=False)
    stub, written = _busy_tab(tab_mod, shown)
    getattr(tab_mod.DataStatusTab, button)(stub)
    assert not written, "the button wrote %r and then did nothing" % written
    assert shown and "still running" in shown[0]


def test_run_refuses_while_busy_and_says_so_to_its_caller(tab_mod):
    stub, _ = _busy_tab(tab_mod, [])
    assert tab_mod.DataStatusTab._run(stub, lambda: {}, lambda r: None) is False


def test_a_finished_job_can_start_the_next_one(tab_mod):
    """The result is delivered once the thread has stopped, so a handler that
    starts the next job (as Update does with Refresh) is not turned away."""
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance()
    stub = SimpleNamespace(_worker=None)
    stub._busy = lambda: tab_mod.DataStatusTab._busy(stub)
    got = {}

    def first_done(result):
        got["first"] = result
        got["second_started"] = tab_mod.DataStatusTab._run(
            stub, lambda: {"second": 2}, lambda r: got.__setitem__("second", r))

    assert tab_mod.DataStatusTab._run(stub, lambda: {"first": 1}, first_done)
    deadline = time.time() + 10
    while "second" not in got and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert got["first"] == {"first": 1}
    assert got["second_started"] is True
    assert got["second"] == {"second": 2}


def test_a_job_that_raises_delivers_its_problem(tab_mod):
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance()
    stub = SimpleNamespace(_worker=None)
    stub._busy = lambda: tab_mod.DataStatusTab._busy(stub)
    got = {}

    def boom():
        raise ValueError("snapshot unreadable")
    tab_mod.DataStatusTab._run(stub, boom, lambda r: got.setdefault("r", r))
    deadline = time.time() + 10
    while "r" not in got and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert got["r"] == {"problems": ["ValueError: snapshot unreadable"]}
