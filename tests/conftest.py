"""Suite-wide guards.

A unit test must never contend for the PRODUCTION database-task lock: when a
real hourly task held it, the suite silently hung for minutes (2026-09-04).
Constructing TaskMutex without an explicit base_dir resolves to the
production logs dir -- make that an instant, loud failure instead.
"""
import pytest

from mousedb.task_mutex import TaskMutex

_orig_init = TaskMutex.__init__


@pytest.fixture(autouse=True)
def _no_production_lock(monkeypatch):
    def guarded(self, name="central-db", stale_minutes=45, base_dir=None):
        if base_dir is None:
            pytest.fail(
                "test constructed TaskMutex with the PRODUCTION lock dir; "
                "pass base_dir=tmp_path (or stub task_mutex.hold)")
        _orig_init(self, name=name, stale_minutes=stale_minutes,
                   base_dir=base_dir)
    monkeypatch.setattr(TaskMutex, "__init__", guarded)
