"""The exports button does not say "already being written" just after the
previous refresh let go of the lock.

WHY: on Windows a lockfile whose delete is still pending refuses both to be
re-created and to be read for a moment. The no-wait path (the "Refresh exports
now" button) would otherwise read that unreadable file as a holder ("unknown") and
tell the person the exports were already being written -- when nothing was.
It must wait out the pending delete, as it does for a lockfile that is gone.
"""
import os

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


def _pending_delete_for(monkeypatch, refusals):
    """The exports lockfile behaves as a delete still pending for the first
    ``refusals`` attempts to create it: creating, looking at and reading it
    are all refused. Then the delete completes."""
    real_open, real_stat, real_read = os.open, os.stat, TaskMutex._read
    left = {"n": refusals, "pending": True}

    def is_lock(path):
        return os.path.basename(str(path)) == current.EXPORTS_LOCK + ".task.lock" \
            or str(path).endswith(".task.lock") and current.EXPORTS_LOCK in str(path)

    def fake_open(path, flags, *a, **kw):
        if is_lock(path) and left["n"] > 0:
            left["n"] -= 1
            raise PermissionError(13, "Access is denied", str(path))
        if is_lock(path):
            left["pending"] = False      # the delete has completed
        return real_open(path, flags, *a, **kw)

    def fake_stat(path, *a, **kw):
        if is_lock(path) and left["pending"]:
            raise PermissionError(13, "Access is denied", str(path))
        return real_stat(path, *a, **kw)

    def fake_read(self):
        if is_lock(self.path) and left["pending"]:
            return ""            # there, but cannot be read right now
        return real_read(self)

    monkeypatch.setattr(task_mutex.os, "open", fake_open)
    monkeypatch.setattr(task_mutex.os, "stat", fake_stat)
    monkeypatch.setattr(TaskMutex, "_read", fake_read)
    return left


def test_the_button_waits_out_a_pending_delete_instead_of_saying_busy(tmp_path, monkeypatch):
    left = _pending_delete_for(monkeypatch, refusals=3)
    try:
        m = current.hold_exports_lock(0, lock_dir=tmp_path)
    except current.ExportsBusy as e:
        pytest.fail("told 'already being written' with nothing writing: %s" % e)
    try:
        assert left["n"] == 0
        assert m.path.exists()
    finally:
        m.release()


def test_a_real_holder_is_still_reported_busy_at_once(tmp_path):
    holder = current.hold_exports_lock(0, lock_dir=tmp_path)
    try:
        with pytest.raises(current.ExportsBusy):
            current.hold_exports_lock(0, lock_dir=tmp_path)
    finally:
        holder.release()
