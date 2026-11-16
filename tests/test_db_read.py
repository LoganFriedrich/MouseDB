"""A read of mousedb's own database works right after a writer was stopped
mid-save (see mousedb/db_read.py for why)."""
import shutil
import sqlite3

import pytest

from mousedb import bench_scan
from mousedb.db_read import connect_for_reading

VP_COLUMNS = ("video_name, segment_num, subject_id, session_date, tray_type, "
              "run_number, outcome, n_reaches")


def _stopped_mid_save(tmp_path):
    """A database holding 3 saved per-pellet rows, with a stopped writer's
    unfinished save (2000 more rows) left beside it."""
    live = tmp_path / "live.db"
    con = sqlite3.connect(str(live), isolation_level=None)
    con.execute("PRAGMA journal_mode = DELETE")
    con.execute("PRAGMA cache_size = 1")
    con.execute("CREATE TABLE video_pellets (video_name TEXT, segment_num INTEGER, "
                "subject_id TEXT, session_date TEXT, tray_type TEXT, run_number INTEGER, "
                "outcome TEXT, n_reaches INTEGER, pad TEXT)")
    con.execute("BEGIN")
    con.executemany("INSERT INTO video_pellets VALUES (?,?,?,?,?,?,?,?,?)",
                    [("20990101_ABC0101_P1", i, "ABC_01_01", "2099-01-01", "P", 1,
                      "retrieved", 1, "x" * 900) for i in range(1, 4)])
    con.execute("COMMIT")
    con.execute("BEGIN")
    con.executemany("INSERT INTO video_pellets VALUES (?,?,?,?,?,?,?,?,?)",
                    [("20990102_ABC0101_P1", i, "ABC_01_01", "2099-01-02", "P", 1,
                      "untouched", 0, "y" * 900) for i in range(2000)])
    stopped = tmp_path / "stopped.db"
    shutil.copy(live, stopped)
    shutil.copy(str(live) + "-journal", str(stopped) + "-journal")
    con.execute("ROLLBACK")
    con.close()
    return stopped


def test_a_plain_read_only_open_is_refused_there(tmp_path):
    """The situation the helper exists for: what a mode=ro open does."""
    db = _stopped_mid_save(tmp_path)
    con = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("SELECT count(*) FROM video_pellets").fetchall()
    con.close()


def test_the_helper_reads_only_what_was_saved(tmp_path):
    db = _stopped_mid_save(tmp_path)
    con = connect_for_reading(db)
    try:
        assert con.execute("SELECT count(*) FROM video_pellets").fetchone() == (3,)
    finally:
        con.close()


def test_the_helper_still_cannot_change_a_row(tmp_path):
    db = _stopped_mid_save(tmp_path)
    con = connect_for_reading(db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            con.execute("DELETE FROM video_pellets")
    finally:
        con.close()


def test_a_clean_database_is_opened_read_only(tmp_path):
    db = tmp_path / "clean.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE t (n INTEGER)")
    c.commit()
    c.close()
    con = connect_for_reading(db)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            con.execute("INSERT INTO t VALUES (1)")
    finally:
        con.close()


def test_the_bench_scan_does_not_mistake_an_unfinished_save_for_a_missing_table(
        tmp_path, monkeypatch):
    """Before: the read-only open failed, the error was caught as 'the table is
    not there', and the scan went on without the per-pellet rows, silently."""
    db = _stopped_mid_save(tmp_path)
    monkeypatch.setattr(bench_scan, "DEFAULT_CACHE", None)
    vp = bench_scan.load_video_pellets(db_path=db)
    assert vp is not None
    assert len(vp) == 3
