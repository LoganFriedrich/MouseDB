"""The cross-task database mutex: one holder, dead locks break, live ones never do.

The overlap it prevents: an import's commit going PENDING behind the
snapshot's long reads refuses every new reader for the duration -- 22 minutes
of "database is locked" on 2026-09-02 with neither task individually slow.

The rule it must never get wrong (2026-10-15): a holder that is still running
keeps the lock however long it has held it. Breaking a live holder's lock by
age put two writers on the database every night for a week.
"""

import os
import time
from datetime import datetime

import psutil

from mousedb.task_mutex import TaskMutex


def _own_stamp(offset_seconds: float = 1.0) -> str:
    """A lock timestamp consistent with THIS process being the holder."""
    return datetime.fromtimestamp(psutil.Process().create_time() + offset_seconds).isoformat()


def test_single_holder_and_release(tmp_path):
    a = TaskMutex(base_dir=tmp_path)
    b = TaskMutex(base_dir=tmp_path)
    assert a.try_acquire()
    assert not b.try_acquire()      # second holder refused
    a.release()
    assert b.try_acquire()          # free after release
    b.release()
    assert not (tmp_path / ".central-db.task.lock").exists()


def test_live_holder_is_never_broken_by_age(tmp_path):
    """The reach import held the lock for 3 hours on 2026-10-15 while
    rewriting phase labels; the brain import broke in at 45 minutes and the
    two fought over the database. A running holder keeps its lock."""
    a = TaskMutex(base_dir=tmp_path, stale_minutes=45)
    a.path.write_text("%d %s" % (os.getpid(), _own_stamp()))
    old = time.time() - 6 * 3600
    os.utime(a.path, (old, old))                    # six hours old
    b = TaskMutex(base_dir=tmp_path, stale_minutes=45)
    assert b.holder()[0] == "alive"
    assert not b.try_acquire()
    assert a.path.exists()


def test_dead_holder_lock_breaks_immediately(tmp_path):
    """A crashed holder's lock must not wedge the next 45 minutes: the pid in
    the lockfile is dead, so the next acquirer breaks it at once."""
    a = TaskMutex(base_dir=tmp_path)
    a.path.write_text("999999999 2026-01-01T00:00:00")
    b = TaskMutex(base_dir=tmp_path)
    assert b.holder()[0] == "dead"
    assert b.try_acquire()
    b.release()


def test_reused_pid_counts_as_dead(tmp_path):
    """A pid that now belongs to a process started AFTER the lock was written
    is not the holder; the holder is gone and the lock is broken."""
    a = TaskMutex(base_dir=tmp_path)
    a.path.write_text("%d 2020-01-01T00:00:00" % os.getpid())   # long before this process
    b = TaskMutex(base_dir=tmp_path)
    assert b.holder()[0] == "dead"
    assert b.try_acquire()
    b.release()


def test_unidentifiable_holder_falls_back_to_age(tmp_path):
    """When the lockfile says nothing usable about its holder, age is the only
    evidence: a young lock is respected, an old one is broken."""
    a = TaskMutex(base_dir=tmp_path, stale_minutes=45)
    a.path.write_text("not a pid")
    b = TaskMutex(base_dir=tmp_path, stale_minutes=45)
    assert b.holder()[0] == "unknown"
    assert not b.try_acquire()                      # fresh: respected
    old = time.time() - 46 * 60
    os.utime(a.path, (old, old))
    assert b.try_acquire()                          # stale: broken and taken
    b.release()


def test_context_manager_releases_on_exit(tmp_path):
    with TaskMutex(base_dir=tmp_path).acquire(wait_seconds=1, log=lambda *_: None):
        assert (tmp_path / ".central-db.task.lock").exists()
    assert not (tmp_path / ".central-db.task.lock").exists()


def test_waiting_names_the_holder(tmp_path):
    """The person reading the log learns WHO holds the lock, not just that
    someone does."""
    a = TaskMutex(base_dir=tmp_path)
    a.path.write_text("%d %s" % (os.getpid(), _own_stamp()))
    said = []
    try:
        TaskMutex(base_dir=tmp_path).acquire(wait_seconds=0, log=said.append,
                                            waiting_for="the other import")
    except RuntimeError as e:
        assert "pid %d" % os.getpid() in str(e) and "the other import" in str(e)
    else:
        raise AssertionError("acquire must give up after wait_seconds")
    assert said and "pid %d" % os.getpid() in said[0]
