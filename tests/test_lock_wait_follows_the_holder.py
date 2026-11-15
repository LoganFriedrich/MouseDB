"""A job waits for the database-task lock while the holder is working, and
fails loudly -- never silently, never by breaking a live lock -- when it cannot.

WHY: the operating system's scheduler re-runs a task only when it could not be
STARTED. A job that started, waited a fixed short time for the job ahead of it,
gave up and exited with an error was simply lost until its next scheduled run,
even when the job ahead finished minutes later. These tests pin the rules that
replace the fixed wait: wait while the holder makes progress, up to the job's
own budget; give up early on a holder that has stopped making progress; say so
loudly either way; and never wait twice on the same hung holder.
"""
import os
import time
from datetime import datetime

import psutil
import pytest

from mousedb import task_mutex
from mousedb.task_mutex import LockWaitTimeout, TaskMutex


def _held_by_this_process(tmp_path):
    """A lockfile whose holder is THIS (running) process: judged alive."""
    holder = TaskMutex(base_dir=tmp_path)
    stamp = datetime.fromtimestamp(psutil.Process().create_time() + 1).isoformat()
    holder.path.write_text("%d %s" % (os.getpid(), stamp))
    return holder


class FakeTime:
    """A clock that moves only when the waiter sleeps, so hours of waiting
    take no real time. ``on_sleep`` runs after each sleep (to release the
    lock, or to fake the holder's progress)."""

    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.on_sleep = on_sleep

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s
        if self.on_sleep:
            self.on_sleep(self.now)


def _waiter(tmp_path, fake, progress=True):
    m = TaskMutex(base_dir=tmp_path)
    m._clock, m._sleep = fake.clock, fake.sleep
    if progress:
        # The holder's CPU/IO counters move on every look.
        m._activity = lambda text, state: (text, fake.now)
    else:
        m._activity = lambda text, state: (text, "no change")
    return m


@pytest.fixture(autouse=True)
def _fresh_process_state():
    task_mutex._gave_up_on.clear()
    task_mutex.set_wait_minutes(None)
    yield
    task_mutex._gave_up_on.clear()
    task_mutex.set_wait_minutes(None)


def test_a_slow_holder_delays_the_waiter_instead_of_cancelling_it(tmp_path):
    """The holder works for longer than the old fixed 25-minute wait and then
    finishes; the waiter takes the lock right after instead of having given up."""
    holder = _held_by_this_process(tmp_path)
    released_at = 3 * 3600 - 60          # just under three hours
    fake = FakeTime(lambda now: holder.path.unlink()
                    if now >= released_at and holder.path.exists() else None)
    m = _waiter(tmp_path, fake)
    said = []
    m.acquire(wait_seconds=4 * 3600, log=said.append, waiting_for="the import")
    try:
        assert m._held
        assert fake.now >= released_at > 1500
        assert any("still waiting" in s for s in said)      # a long wait reports itself
    finally:
        m.release()


def test_the_wait_ends_loudly_at_the_jobs_budget(tmp_path):
    _held_by_this_process(tmp_path)
    fake = FakeTime()
    m = _waiter(tmp_path, fake)
    with pytest.raises(LockWaitTimeout) as e:
        m.acquire(wait_seconds=600, log=lambda *_: None, waiting_for="the import")
    msg = str(e.value)
    assert "the import" in msg and "pid %d" % os.getpid() in msg
    assert "does NOT run again by itself" in msg
    assert 600 <= fake.now < 600 + 2 * task_mutex.POLL_SECONDS
    assert (tmp_path / ".central-db.task.lock").exists()      # live lock left alone
    # Not mistaken for an outside database lock by the retry wrapper.
    from mousedb.db_contention import is_locked_error
    assert not is_locked_error(e.value)


def test_a_holder_with_no_progress_is_given_up_on_early(tmp_path):
    """A live holder that shows no progress may be hung; waiting the whole
    budget for it would help nobody, and its lock is never broken."""
    _held_by_this_process(tmp_path)
    fake = FakeTime()
    m = _waiter(tmp_path, fake, progress=False)
    with pytest.raises(LockWaitTimeout) as e:
        m.acquire(wait_seconds=6 * 3600, stall_seconds=1800, log=lambda *_: None)
    assert "no sign of progress" in str(e.value)
    assert 1800 <= fake.now < 1800 + 2 * task_mutex.POLL_SECONDS
    assert (tmp_path / ".central-db.task.lock").exists()


def test_the_same_hung_holder_is_not_waited_for_twice(tmp_path):
    """A job that takes the lock once per item must not sit through the whole
    wait again for each item behind a holder it already gave up on."""
    _held_by_this_process(tmp_path)
    fake = FakeTime()
    first = _waiter(tmp_path, fake, progress=False)
    with pytest.raises(LockWaitTimeout):
        first.acquire(wait_seconds=6 * 3600, stall_seconds=1800, log=lambda *_: None)
    gave_up = fake.now
    second = _waiter(tmp_path, fake, progress=False)
    with pytest.raises(LockWaitTimeout) as e:
        second.acquire(wait_seconds=6 * 3600, stall_seconds=1800, log=lambda *_: None)
    assert "already waited" in str(e.value)
    assert fake.now - gave_up <= task_mutex.POLL_SECONDS


def test_the_wait_budget_comes_from_the_job_then_the_environment(monkeypatch):
    monkeypatch.delenv(task_mutex.WAIT_ENV, raising=False)
    assert task_mutex.default_wait_seconds() == task_mutex.DEFAULT_WAIT_MINUTES * 60
    task_mutex.set_wait_minutes(180)
    assert task_mutex.default_wait_seconds() == 180 * 60
    monkeypatch.setenv(task_mutex.WAIT_ENV, "40")       # one task's override
    assert task_mutex.default_wait_seconds() == 40 * 60


def test_a_holder_keeps_its_lock_fresh_while_it_runs(tmp_path, monkeypatch):
    """The heartbeat: a waiter that cannot look the holder up judges it by the
    lockfile's age, so a running holder must keep that age small."""
    monkeypatch.setattr(task_mutex, "HEARTBEAT_SECONDS", 0.05)
    m = TaskMutex(base_dir=tmp_path).acquire(wait_seconds=1, log=lambda *_: None)
    try:
        old = time.time() - 3600
        os.utime(m.path, (old, old))
        deadline = time.time() + 5
        while m.path.stat().st_mtime < time.time() - 60 and time.time() < deadline:
            time.sleep(0.05)
        assert m.path.stat().st_mtime > time.time() - 60
    finally:
        m.release()
    assert not m.path.exists()


def test_snapshot_job_fails_loudly_when_it_cannot_get_the_lock(tmp_path, monkeypatch, capsys):
    """The nightly snapshot that gives up on the lock prints [FAIL] and exits
    non-zero: the exports were not refreshed and nothing re-runs it."""
    from mousedb.exporters import refresh_snapshot as rs
    from mousedb import prefetch
    import contextlib
    db = tmp_path / "connectome.db"
    db.write_bytes(b"")
    monkeypatch.setenv("MOUSEDB_DB_PATH", str(db))
    monkeypatch.setenv("MOUSEDB_SNAPSHOT_DIR", str(tmp_path / "snap"))
    monkeypatch.setattr(prefetch, "warmed", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.delenv(task_mutex.WAIT_ENV, raising=False)

    def refuse(**_k):
        raise LockWaitTimeout("could not acquire the database-task lock: test holder")
    monkeypatch.setattr(rs, "refresh", refuse)
    assert rs.main(["--skip-census", "--lock-wait-minutes", "5"]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] snapshot not refreshed" in out
    assert task_mutex.default_wait_seconds() == 5 * 60
