"""The hourly snapshot job and the per-cohort ODC session exports.

WHY: refresh_snapshot's main() used to hand db_ok=--force to the current
exports, and the hourly scheduled task runs without --force -- so the
per-cohort ODC_sessions_*.csv files were never refreshed and MANIFEST.json
said odc_sessions_refreshed=False every hour (found 2026-08-28). These tests
pin that a successful snapshot vouches for the database, that --force still
overrides, that a watcher appearing after the snapshot withholds the vouch,
and that a watcher running before it still aborts the whole run.
"""
import sqlite3

import pytest

from mousedb.exporters import current as current_mod
from mousedb.exporters import refresh_snapshot as rs


@pytest.fixture
def configured(tmp_path, monkeypatch):
    """A tiny connectome.db holding every snapshotted table, and an empty
    snapshot folder, both under tmp_path and reachable through the config
    environment variables."""
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
    # first answer: the snapshot's own guard (idle); second: the re-check
    # before the exports (a watcher has started since)
    answers = iter([False, True])
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: next(answers))
    assert rs.main([]) == 0
    assert (configured / "subjects.parquet").is_file()
    assert db_ok_seen == [False]


def test_running_watcher_aborts_the_whole_run(configured, db_ok_seen, monkeypatch):
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: True)
    with pytest.raises(RuntimeError):
        rs.main([])
    assert not (configured / "pellet_scores.parquet").exists()
    assert db_ok_seen == []
