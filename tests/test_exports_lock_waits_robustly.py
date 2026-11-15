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


class _FakeTime:
    def __init__(self, on_sleep):
        self.now = 0.0
        self.on_sleep = on_sleep

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s
        self.on_sleep(self.now)


def test_a_holder_whose_counters_are_refused_is_judged_by_its_heartbeat(tmp_path, monkeypatch):
    """A live holder (this process) whose CPU and read/write counters are
    refused, but whose lockfile is touched by its heartbeat, is still working:
    the waiter keeps waiting past the stall time and takes the lock when it is
    released."""
    holder = TaskMutex(current.EXPORTS_LOCK, base_dir=tmp_path)
    stamp = datetime.fromtimestamp(psutil.Process().create_time() + 1).isoformat()
    holder.path.write_text("%d %s" % (os.getpid(), stamp))

    def refused(self, *a, **kw):
        raise psutil.AccessDenied(os.getpid())

    monkeypatch.setattr(psutil.Process, "cpu_times", refused)
    monkeypatch.setattr(psutil.Process, "io_counters", refused)
    stall = 30 * 60
    release_at = 3 * stall

    def beat(now):
        if not holder.path.exists():
            return
        if now >= release_at:
            holder.path.unlink()
            return
        # The holder's heartbeat touches the lockfile once a minute.
        if int(now) % 60 == 0:
            st = holder.path.stat()
            os.utime(holder.path, (st.st_atime, st.st_mtime + 60))

    fake = _FakeTime(beat)
    m = TaskMutex(current.EXPORTS_LOCK, base_dir=tmp_path)
    m._clock, m._sleep = fake.clock, fake.sleep
    m.acquire(wait_seconds=4 * stall, stall_seconds=stall, log=lambda *_: None,
              waiting_for="another export refresh")
    try:
        assert m._held and fake.now >= release_at
    finally:
        m.release()
