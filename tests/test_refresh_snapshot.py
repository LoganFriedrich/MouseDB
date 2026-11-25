"""The scheduled snapshot job and the per-cohort ODC session exports.

WHY: refresh_snapshot's main() would otherwise hand db_ok=--force to the current
exports, and the scheduled task runs without --force -- so the
per-cohort ODC_sessions_*.csv files were never refreshed and MANIFEST.json
said odc_sessions_refreshed=False on every run. These tests
pin that a successful snapshot vouches for the database, that --force still
overrides, that a watcher appearing after the snapshot withholds the vouch,
and that a watcher running before it still aborts the whole run.
"""
import sqlite3
import sys

import pytest

from mousedb.exporters import current as current_mod
from mousedb.exporters import refresh_snapshot as rs


@pytest.fixture
def configured(tmp_path, monkeypatch):
    """A tiny connectome.db holding every snapshotted table, and an empty
    snapshot folder, both under tmp_path and reachable through the config
    environment variables.

    Also stubs out the cross-process task mutex: refresh() acquires the
    PRODUCTION database lock (under the configured logs dir), and a unit
    test must never contend for it -- when a real scheduled task held that
    lock, this suite silently hung for many minutes."""
    import contextlib
    from mousedb import pipeline_census, task_mutex
    monkeypatch.setattr(task_mutex, "hold",
                        lambda *a, **k: contextlib.nullcontext())
    # The job retakes the pipeline census last: a long scan of the real
    # pipeline folders that no unit test may start.
    monkeypatch.setattr(pipeline_census, "refresh",
                        lambda *a, **k: {"generated_at": "2026-10-22T00:00:00",
                                         "totals": {"expected": 1, "finished_files": 1}})
    db = tmp_path / "connectome.db"
    con = sqlite3.connect(db)
    for table in rs.TABLES:
        con.execute("CREATE TABLE %s (id INTEGER)" % table)
        con.execute("INSERT INTO %s VALUES (1)" % table)
    con.commit()
    con.close()
    snap = tmp_path / "snap"
    monkeypatch.setenv("MOUSEDB_DB_PATH", str(db))
    monkeypatch.setenv("MOUSEDB_SNAPSHOT_DIR", str(snap))
    return snap


@pytest.fixture
def db_ok_seen(monkeypatch):
    """Replace the current-exports step with a recorder of the db_ok it got."""
    seen = []

    def fake_refresh_current(*args, **kwargs):
        seen.append(kwargs.get("db_ok"))
        return {"files": {}, "complete": True, "problems": []}

    monkeypatch.setattr(current_mod, "refresh_current", fake_refresh_current)
    return seen


def test_successful_snapshot_vouches_for_the_database(configured, db_ok_seen, monkeypatch):
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: False)
    assert rs.main([]) == 0
    assert (configured / "pellet_scores.parquet").is_file()
    assert db_ok_seen == [True]


def test_force_still_vouches(configured, db_ok_seen, monkeypatch):
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: True)
    assert rs.main(["--force"]) == 0
    assert (configured / "reach_data.parquet").is_file()
    assert db_ok_seen == [True]


def test_watcher_appearing_after_the_snapshot_withholds_the_vouch(configured, db_ok_seen, monkeypatch):
    # first answers: the job's check before it queues for the lock and the
    # snapshot's own guard (idle); last: the re-check before the exports (a
    # watcher has started since)
    answers = iter([False, False, True])
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: next(answers))
    assert rs.main([]) == 0
    assert (configured / "subjects.parquet").is_file()
    assert db_ok_seen == [False]


def _census_calls(monkeypatch, order, fail=False):
    from mousedb import pipeline_census

    def fake(*a, **k):
        order.append("census")
        if fail:
            raise RuntimeError("the pipeline census failed (exit 1)")
        return {"generated_at": "2026-10-22T00:00:00", "totals": {}}
    monkeypatch.setattr(pipeline_census, "refresh", fake)

    def fake_current(*a, **k):
        order.append("exports")
        return {"files": {}, "complete": True, "problems": []}
    monkeypatch.setattr(current_mod, "refresh_current", fake_current)


def test_the_census_is_retaken_after_the_exports(configured, monkeypatch):
    # WHY: when the census was retaken only by a button, the tab could show
    # one weeks old as its pipeline counts.
    order = []
    _census_calls(monkeypatch, order)
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: False)
    assert rs.main([]) == 0
    assert order == ["exports", "census"]


def test_skip_census_skips_it(configured, monkeypatch):
    order = []
    _census_calls(monkeypatch, order)
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: False)
    assert rs.main(["--skip-census"]) == 0
    assert order == ["exports"]


def test_a_failed_census_is_loud_but_does_not_fail_the_snapshot(configured, monkeypatch, capsys):
    # A non-zero exit would make the scheduler re-run the whole snapshot.
    order = []
    _census_calls(monkeypatch, order, fail=True)
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: False)
    assert rs.main([]) == 0
    assert order == ["exports", "census"]
    assert "[FAIL] pipeline census not retaken" in capsys.readouterr().out


def test_running_watcher_aborts_the_whole_run(configured, db_ok_seen, monkeypatch):
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: True)
    with pytest.raises(RuntimeError):
        rs.main([])
    assert not (configured / "pellet_scores.parquet").exists()
    assert db_ok_seen == []


def test_refresh_leaves_no_temporary_files(configured, monkeypatch):
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: False)
    rs.refresh()
    # The tables, and the stamp saying which refresh they all came from.
    assert sorted(p.name for p in configured.iterdir()) == sorted(
        ["%s.parquet" % t for t in rs.TABLES] + ["SNAPSHOT_GENERATION.json"])


def test_a_write_that_fails_keeps_the_previous_snapshot_intact(configured, monkeypatch):
    """WHY: readers open the snapshot at any time, and the task's time limit or
    a person can stop the refresh mid-write. The previous file must survive a
    write that does not complete."""
    import pandas as pd
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: False)
    rs.refresh()
    before = (configured / "reach_data.parquet").read_bytes()

    real_to_parquet = pd.DataFrame.to_parquet

    def failing_to_parquet(self, path, *args, **kwargs):
        if "reach_data" in str(path):
            with open(path, "wb") as fh:          # a partial write, then the stop
                fh.write(b"PAR1-truncated")
            raise KeyboardInterrupt("stopped mid-write")
        return real_to_parquet(self, path, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", failing_to_parquet)
    with pytest.raises(KeyboardInterrupt):
        rs.refresh()
    assert (configured / "reach_data.parquet").read_bytes() == before
    assert pd.read_parquet(configured / "reach_data.parquet")["id"].tolist() == [1]


@pytest.mark.skipif(sys.platform != "win32",
                    reason="only Windows refuses to replace a file that is open")
def test_swap_waits_for_a_reader_to_let_go(tmp_path):
    """WHY: anything reading the snapshot (the Where Is My Data tab, the data
    browser making its private copy) holds a table file open for seconds. On
    Windows a replace fails while the file is open, so without a wait one such
    read at the wrong moment failed the whole nightly refresh."""
    import threading
    import time
    out = tmp_path / "t.parquet"
    out.write_bytes(b"old")
    tmp = tmp_path / "t.parquet.tmp"
    tmp.write_bytes(b"new")
    reader = open(out, "rb")
    threading.Thread(target=lambda: (time.sleep(1.5), reader.close())).start()
    rs.swap_in(tmp, out, wait_seconds=30)
    assert out.read_bytes() == b"new"
    assert not tmp.exists()


@pytest.mark.skipif(sys.platform != "win32",
                    reason="only Windows refuses to replace a file that is open")
def test_swap_gives_up_loudly_on_a_reader_that_never_lets_go(tmp_path):
    out = tmp_path / "t.parquet"
    out.write_bytes(b"old")
    tmp = tmp_path / "t.parquet.tmp"
    tmp.write_bytes(b"new")
    with open(out, "rb"):
        with pytest.raises(PermissionError):
            rs.swap_in(tmp, out, wait_seconds=1)
    assert out.read_bytes() == b"old"


def test_a_long_wait_for_the_task_lock_does_not_use_up_the_retry_window(configured, db_ok_seen,
                                                                       monkeypatch):
    """refresh() may wait up to --lock-wait-minutes for the cross-task lock
    before it reads anything. That wait must not count against --retry-minutes:
    a "database is locked" met right after it would otherwise find the window spent
    and fail the night's refresh without a single retry."""
    from mousedb import db_contention as dc

    class Clock:
        now = 1000.0

        def __call__(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    real_retry = dc.retry_while_locked
    monkeypatch.setattr(dc, "retry_while_locked",
                        lambda work, **kw: real_retry(work, sleep=clock.sleep, clock=clock, **kw))
    real_refresh = rs.refresh
    calls = []

    def refresh(**kw):
        calls.append(1)
        if len(calls) == 1:
            clock.now += 120 * 60             # two hours behind an import, then a lock
            raise sqlite3.OperationalError("database is locked")
        return real_refresh(**kw)

    monkeypatch.setattr(rs, "refresh", refresh)
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: False)
    assert rs.main(["--skip-census", "--retry-minutes", "25"]) == 0
    assert len(calls) == 2


def test_a_retry_does_not_queue_for_the_task_lock_again(configured, db_ok_seen, monkeypatch):
    """The job takes the cross-task lock once and keeps it across its retries.
    A retry that queued for it again could wait the whole --lock-wait-minutes a
    second time and push the job past its time limit part way through
    rewriting the exports."""
    import contextlib
    from mousedb import db_contention as dc, task_mutex
    taken = []
    monkeypatch.setattr(task_mutex, "hold",
                        lambda *a, **k: taken.append(1) or contextlib.nullcontext())
    real_retry = dc.retry_while_locked
    monkeypatch.setattr(dc, "retry_while_locked",
                        lambda work, **kw: real_retry(work, sleep=lambda s: None, **kw))
    real_read_sql = rs.pd.read_sql
    reads = []

    def read_sql(*a, **kw):            # the first attempt meets an outside lock
        reads.append(1)
        if len(reads) == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_read_sql(*a, **kw)

    monkeypatch.setattr(rs.pd, "read_sql", read_sql)
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: False)
    assert rs.main(["--skip-census"]) == 0
    assert len(reads) > 1 and taken == [1]


def test_the_session_exports_are_written_under_the_task_lock(configured, monkeypatch):
    """The per-cohort session exports read the live database for minutes; an
    import committing at the same time is the stall the task lock prevents."""
    import contextlib
    from mousedb import task_mutex
    state = {"held": False, "exports_under_lock": None}

    @contextlib.contextmanager
    def hold(*a, **k):
        state["held"] = True
        try:
            yield
        finally:
            state["held"] = False

    def refresh_current(*a, **k):
        state["exports_under_lock"] = state["held"]
        return {"files": {}, "complete": True, "problems": []}

    monkeypatch.setattr(task_mutex, "hold", hold)
    monkeypatch.setattr(current_mod, "refresh_current", refresh_current)
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: False)
    assert rs.main(["--skip-census"]) == 0
    assert state["exports_under_lock"] is True
