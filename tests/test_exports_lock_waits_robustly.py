"""The current-exports lock: messages name it, and a wait never ends early by mistake.

WHY each test exists:

* A dead holder's lock that a waiter breaks is written to the log. It used to
  be written as "database-task lock" whatever the lock was, so a broken
  current-exports lock sent the person to look at the database jobs.
* On Windows, creating a lockfile whose delete is still pending (the holder
  released a moment ago and something still has the old file open) is refused
  with "access denied", not "already exists". That raised out of the wait --
  a wait that was about to succeed ended in a traceback.
* A waiter judges a live holder on the same computer by its CPU and
  read/write counters. A holder started in another person's login (the
  exports button) may refuse those counters to a scheduled job running as
  someone else. Nothing in the waiter's view then ever moved, and a healthy
  holder was given up on as "may be hung" after the stall time. Its lockfile
  heartbeat is the evidence left.
"""
import os
from datetime import datetime

import psutil
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
    task_mutex.set_wait_minutes(None)


def test_breaking_a_dead_exports_lock_is_logged_under_its_own_name(tmp_path, capsys):
    """A refresh that finds the exports lock held by a process that has died
    breaks it -- and its log says it broke the current-exports lock."""
    dead = TaskMutex(current.EXPORTS_LOCK, base_dir=tmp_path)
    dead.path.write_text("999999999 2025-01-01T00:00:00")
    m = current.hold_exports_lock(lock_wait_seconds=60, lock_dir=tmp_path)
    m.release()
    said = capsys.readouterr().out
    assert "broke the lock" in said, said
    assert "current-exports" in said, said
    assert "database-task" not in said, said


def test_a_lockfile_whose_delete_is_pending_is_waited_for_not_raised(tmp_path, monkeypatch):
    m = TaskMutex(current.EXPORTS_LOCK, base_dir=tmp_path)
    real_open = os.open
    refused = {"n": 0}

    def pending_delete(path, flags, *a, **kw):
        if str(path) == str(m.path) and refused["n"] < 3:
            refused["n"] += 1
            raise PermissionError(13, "Access is denied", str(path))
        return real_open(path, flags, *a, **kw)

    monkeypatch.setattr(task_mutex.os, "open", pending_delete)
    slept = []
    m._sleep = slept.append
    m.acquire(wait_seconds=60, log=lambda *_: None, waiting_for="another export refresh")
    try:
        assert m._held and refused["n"] == 3
    finally:
        m.release()
