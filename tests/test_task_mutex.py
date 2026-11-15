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


# -- breaking is atomic, release is owner-checked, every break is recorded --

DEAD = "999999999 2020-01-01T00:00:00"


def test_two_waiters_judging_the_same_dead_lock_leave_one_holder(tmp_path):
    """Waiter B judged the lock dead, then waiter A broke it and took a new
    one before B acted. B's break must find different contents and leave A's
    NEW lock alone -- otherwise both would hold the mutex."""
    a = TaskMutex(base_dir=tmp_path)
    b = TaskMutex(base_dir=tmp_path)
    a.path.write_text(DEAD)
    seen_by_b = b._read()
    assert b._classify(seen_by_b)[0] == "dead"
    assert a.try_acquire()                      # A breaks and takes
    assert not b._break(seen_by_b, "dead")      # B acts on its stale judgement
    assert a.path.read_text() == a._identity    # A's lock untouched
    assert not b.try_acquire()                  # and B sees a live holder
    a.release()


def test_only_the_token_holder_can_break(tmp_path):
    """While another process holds the break token, nobody else breaks."""
    m = TaskMutex(base_dir=tmp_path)
    m.path.write_text(DEAD)
    m._token_path.write_text("12345 elsewhere")      # someone is breaking now
    assert not m.try_acquire()
    assert m.path.read_text() == DEAD
    m._token_path.unlink()
    assert m.try_acquire()
    m.release()


def test_an_orphaned_break_token_is_cleared(tmp_path):
    """A process that died while holding the token must not block breaking
    forever: an old token is removed and the next attempt goes through."""
    from mousedb import task_mutex
    m = TaskMutex(base_dir=tmp_path)
    m.path.write_text(DEAD)
    m._token_path.write_text("12345 elsewhere")
    old = time.time() - task_mutex.BREAK_TOKEN_STALE_SECONDS - 5
    os.utime(m._token_path, (old, old))
    assert not m.try_acquire()          # this attempt clears the orphan ...
    assert not m._token_path.exists()
    assert m.try_acquire()              # ... and the next one breaks and takes
    m.release()


def test_release_never_removes_another_holders_lock(tmp_path):
    """A holder whose lock was broken while it ran must not delete the lock
    of the job that took over when it finally exits."""
    a = TaskMutex(base_dir=tmp_path)
    assert a.try_acquire()
    other = "%d %s %s %s" % (os.getpid(), _own_stamp(), "thishost", "x" * 32)
    a.path.write_text(other)            # broken and retaken by someone else
    said = []
    a.release(log=said.append)
    assert a.path.read_text() == other
    assert said and "left alone" in said[0]


def test_every_break_is_recorded(tmp_path):
    """A broken lock is written to the job's log and to the permanent record
    beside the lock."""
    m = TaskMutex(base_dir=tmp_path)
    m.path.write_text(DEAD)
    said = []
    assert m.try_acquire(log=said.append)
    m.release()
    assert said and "broke the lock" in said[0] and "no longer running" in said[0]
    record = m.breaks_log.read_text(encoding="utf-8")
    assert "broke the lock" in record and "999999999" in record


def test_a_lock_held_on_another_computer_is_not_judged_dead(tmp_path):
    """A pid from another computer means nothing in this machine's process
    table. Its lock is 'unknown' (respected while fresh), never 'dead'."""
    m = TaskMutex(base_dir=tmp_path)
    m.path.write_text("999999999 2020-01-01T00:00:00 some-other-computer abc")
    state, who = m.holder()
    assert state == "unknown" and "another computer" in who
    assert not m.try_acquire()
    assert m.path.exists()


def test_own_lock_carries_this_computer_and_a_unique_token(tmp_path):
    a = TaskMutex(base_dir=tmp_path)
    assert a.try_acquire()
    fields = a.path.read_text().split()
    assert int(fields[0]) == os.getpid() and len(fields) == 4
    assert a.holder()[0] == "alive"
    a.release()
    assert not a.path.exists()


# -- where the lockfile goes when no folder is passed ------------------------

def _unguarded_mutex(monkeypatch):
    """TaskMutex's real constructor: the suite's guard refuses a mutex without
    base_dir, and these tests are about exactly that case (with the location
    pointed at tmp_path through configuration)."""
    from tests import conftest as suite
    monkeypatch.setattr(TaskMutex, "__init__", suite._orig_init)


def test_with_only_db_path_set_the_lock_sits_beside_the_database(tmp_path, monkeypatch):
    """Two jobs exclude each other only if they look for the lock in the SAME
    place. With db_path set and mousedb_root not, the lockfile used to go into
    each process's working directory, so jobs started from different folders
    each found it free."""
    _unguarded_mutex(monkeypatch)
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("MOUSEDB_DB_PATH", str(data / "example.db"))
    elsewhere = tmp_path / "started_here"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    a = TaskMutex()
    assert a.path.parent == data / "logs"
    assert a.try_acquire()
    try:
        monkeypatch.chdir(tmp_path)          # a second job started from another folder
        assert not TaskMutex().try_acquire()
    finally:
        a.release()
    assert not list(elsewhere.iterdir())


def test_with_mousedb_root_set_the_lock_is_in_its_logs_folder(tmp_path, monkeypatch):
    _unguarded_mutex(monkeypatch)
    monkeypatch.setenv("MOUSEDB_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("MOUSEDB_DB_PATH", str(tmp_path / "other" / "example.db"))
    assert TaskMutex().path.parent == tmp_path / "root" / "logs"


def test_with_nothing_configured_it_says_what_to_set(tmp_path, monkeypatch):
    import pytest
    from mousedb.config import ConfigError
    _unguarded_mutex(monkeypatch)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError) as e:
        TaskMutex()
    assert "mousedb_root" in str(e.value)
    assert not list(tmp_path.iterdir())
