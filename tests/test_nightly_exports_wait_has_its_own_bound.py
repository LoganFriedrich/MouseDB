"""The nightly job's wait for the current-exports lock has its own short bound.

WHY: the nightly snapshot job is sized to fit its scheduler time limit as
"database-lock wait + prefetch + two refreshes + retry window + exports +
census". When the current-exports lock was added, the job's wait for it reused
the database-lock budget (hours) -- a second wait of that size the sizing never
counted. A "Refresh exports now" press late in the evening could then make the
job overrun its limit and be stopped part way through the exports, leaving one
night's cohort files beside another night's combined files and manifest.
"""
from mousedb.exporters import current, refresh_snapshot as rs
from mousedb.task_mutex import LockWaitTimeout


def test_the_nightly_exports_wait_is_short_and_separate(tmp_path, monkeypatch, capsys):
    from mousedb import task_mutex
    monkeypatch.setattr(current, "require", lambda key: tmp_path / key)
    task_mutex.set_wait_minutes(rs.DEFAULT_LOCK_WAIT_MINUTES)   # as the nightly job sets it
    seen = []

    def busy(lock_wait_seconds=None, lock_dir=None):
        seen.append(lock_wait_seconds)
        raise LockWaitTimeout("could not acquire the current-exports lock: test")

    monkeypatch.setattr(current, "hold_exports_lock", busy)
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: False)
    try:
        rc = rs._finish({"reach_data": 1}, force=False)
    finally:
        task_mutex.set_wait_minutes(None)
    assert seen, "the nightly job never asked for the exports lock"
    wait = seen[0]
    assert wait is not None, "the nightly job reused its database-lock budget for the exports lock"
    assert 0 < wait <= 30 * 60, wait
    assert rc == 1 and "[FAIL]" in capsys.readouterr().out


def test_the_whole_job_is_sized_as_before():
    """The exports-lock wait came out of the database-lock budget, so the
    job's worst case did not grow past what its time limit was sized for."""
    assert rs.DEFAULT_LOCK_WAIT_MINUTES + rs.EXPORTS_LOCK_WAIT_MINUTES <= 180
