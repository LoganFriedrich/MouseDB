"""A lock folder that refuses writes is an error, said at once -- never "busy".

WHY: on Windows, creating a lockfile whose delete is still pending is refused
with "access denied" for a moment, and a waiter must treat that as "not free
yet". But a folder this login cannot write (no write rights, a read-only
share) gives the same error forever. Read as "not free yet", the exports
button told the person "already being written -- try again" with nothing
running, and every scheduled job waited out its whole budget and then blamed a
holder that did not exist. These tests refuse creation permanently and require
a prompt error that says the lock folder cannot be written.
"""
import os
from types import SimpleNamespace

import pytest

from mousedb import task_mutex
from mousedb.exporters import current
from mousedb.task_mutex import TaskMutex


@pytest.fixture(autouse=True)
def _fresh_process_state():
    task_mutex._gave_up_on.clear()
    task_mutex.set_wait_minutes(None)
    yield
    task_mutex._gave_up_on.clear()


def _refuse_every_lockfile(monkeypatch):
    """os.open refuses to create any lock or break-token file, as a folder
    without write rights does."""
    real_open = os.open

    def refuse(path, flags, *a, **kw):
        name = os.path.basename(str(path))
        if name.endswith(".task.lock") or name.endswith(".breaking"):
            raise PermissionError(13, "Access is denied", str(path))
        return real_open(path, flags, *a, **kw)
    monkeypatch.setattr(task_mutex.os, "open", refuse)


class _FakeTime:
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s


def _not_a_busy_message(e):
    text = str(e)
    assert "cannot be written" in text, text
    assert "already being written" not in text, text
    assert "still holds" not in text, text


def test_the_no_wait_exports_lock_says_the_folder_cannot_be_written(tmp_path, monkeypatch):
    _refuse_every_lockfile(monkeypatch)
    with pytest.raises(PermissionError) as caught:
        current.hold_exports_lock(0, lock_dir=tmp_path)
    assert not isinstance(caught.value, current.ExportsBusy)
    _not_a_busy_message(caught.value)


def test_a_waiting_job_stops_at_once_instead_of_waiting_out_its_budget(tmp_path, monkeypatch):
    _refuse_every_lockfile(monkeypatch)
    m = TaskMutex("central-db", base_dir=tmp_path)
    fake = _FakeTime()
    m._clock, m._sleep = fake.clock, fake.sleep
    with pytest.raises(PermissionError) as caught:
        m.acquire(wait_seconds=600, log=lambda *_: None)
    assert not isinstance(caught.value, task_mutex.LockWaitTimeout)
    _not_a_busy_message(caught.value)
    # Nothing sat at the lock path, so there was no pending delete to wait for.
    assert fake.now < 60, "waited %d s of a 600 s budget" % fake.now


def test_a_refusal_that_outlasts_a_pending_delete_stops_the_wait(tmp_path, monkeypatch):
    """An entry seems to be there (it cannot even be looked at, as a
    delete-pending file), but the refusal never clears: the wait ends after the
    short grace period, not after the job's whole budget."""
    _refuse_every_lockfile(monkeypatch)
    m = TaskMutex("central-db", base_dir=tmp_path)
    real_stat = os.stat

    def unreadable(path, *a, **kw):
        if str(path) == str(m.path):
            raise PermissionError(13, "Access is denied", str(path))
        return real_stat(path, *a, **kw)
    monkeypatch.setattr(task_mutex.os, "stat", unreadable)
    fake = _FakeTime()
    m._clock, m._sleep = fake.clock, fake.sleep
    with pytest.raises(PermissionError) as caught:
        m.acquire(wait_seconds=165 * 60, log=lambda *_: None)
    assert not isinstance(caught.value, task_mutex.LockWaitTimeout)
    _not_a_busy_message(caught.value)
    assert fake.now <= task_mutex.PENDING_DELETE_GRACE_SECONDS + task_mutex.POLL_SECONDS


def test_the_command_exits_with_its_own_code_and_says_why(tmp_path, monkeypatch, capsys):
    _refuse_every_lockfile(monkeypatch)
    monkeypatch.setattr(current, "_lock_dir", lambda: tmp_path)
    try:
        rc = current.main(["--snapshot-dir", str(tmp_path / "snap"),
                           "--out-dir", str(tmp_path / "out"), "--no-wait"])
    except PermissionError as e:       # a traceback instead of a plain line
        pytest.fail("the command crashed: %r" % e)
    said = capsys.readouterr().out
    assert "cannot be written" in said and "already being written" not in said, said
    assert rc not in (0, current.EXIT_BUSY)
    assert rc == current.EXIT_LOCK_FOLDER


def test_the_button_reports_the_lock_folder_not_busy(monkeypatch):
    import subprocess
    from mousedb.gui import data_status_tab as tab_mod
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        returncode=current.EXIT_LOCK_FOLDER, stdout=b"[!] the lock folder cannot be written"))
    r = tab_mod.run_exports_program()
    assert r["lock_folder"] is True and r["busy"] is False
