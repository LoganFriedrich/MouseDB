"""The cross-task database mutex: one holder, stale locks break, release works.

The overlap it prevents: an import's commit going PENDING behind the
snapshot's long reads refuses every new reader for the duration -- 22 minutes
of "database is locked" on 2026-09-02 with neither task individually slow.
"""

import os
import time

from mousedb.task_mutex import TaskMutex


def test_single_holder_and_release(tmp_path):
    a = TaskMutex(base_dir=tmp_path)
    b = TaskMutex(base_dir=tmp_path)
    assert a.try_acquire()
    assert not b.try_acquire()      # second holder refused
    a.release()
    assert b.try_acquire()          # free after release
    b.release()
    assert not (tmp_path / ".central-db.task.lock").exists()


def test_stale_lock_is_broken(tmp_path):
    a = TaskMutex(base_dir=tmp_path, stale_minutes=45)
    assert a.try_acquire()
    # Simulate a crashed holder: age the lockfile past the stale window.
    old = time.time() - 46 * 60
    os.utime(a.path, (old, old))
    b = TaskMutex(base_dir=tmp_path, stale_minutes=45)
    assert b.try_acquire()          # broke the stale lock and took it
    b.release()


def test_context_manager_releases_on_exit(tmp_path):
    with TaskMutex(base_dir=tmp_path).acquire(wait_seconds=1, log=lambda *_: None):
        assert (tmp_path / ".central-db.task.lock").exists()
    assert not (tmp_path / ".central-db.task.lock").exists()
