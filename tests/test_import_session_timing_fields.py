"""The reach import records each video's length and each pellet's timing and placement.

WHY: the sessions export reports how long each tray ran, the
reaches per minute, the latency to the first reach, the gap between reaches and
how well the pellets were placed. The results file has all of these; nothing
imported them, so no export could report them. They are now copied into
video_sessions (total_frames, frame_rate) and video_pellets
(time_to_first_reach_sec, mean_inter_reach_interval_sec,
pellet_position_idealness), and an existing database gains the columns on its
next import.
"""
import json
import sqlite3

from mousedb import import_reaches as ir
from mousedb.database import Database

VIDEO = "20250710_PROJA0101_P2"


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_db()
    conn = sqlite3.connect(str(db.db_path))
    with conn:
        ir.ensure_columns(conn)
    return conn


def _features(tmp_path, **top):
    segs = [{"segment_num": s, "outcome": "untouched", "reaches": [], "attention_score": 1.0,
             "time_to_first_reach_sec": None, "mean_inter_reach_interval_sec": None,
             "pellet_position_idealness": 0.25} for s in range(1, 21)]
    segs[0]["time_to_first_reach_sec"] = 1.5
    segs[0]["mean_inter_reach_interval_sec"] = 0.75
    data = {"video_name": VIDEO, "extractor_version": "9.9.9", "segments": segs}
    data.update(top)
    p = tmp_path / (VIDEO + "_features.json")
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_the_import_records_length_timing_and_placement(tmp_path):
    conn = _db(tmp_path)
    ir.import_file(conn, _features(tmp_path, total_frames=1200, frame_rate=60.0))
    assert conn.execute("SELECT total_frames, frame_rate FROM video_sessions").fetchone() == (1200, 60.0)
    assert conn.execute("SELECT time_to_first_reach_sec, mean_inter_reach_interval_sec, "
                        "pellet_position_idealness FROM video_pellets WHERE segment_num = 1"
                        ).fetchone() == (1.5, 0.75, 0.25)
    # a pellet never reached for has no latency and no gap: NULL, not 0
    assert conn.execute("SELECT time_to_first_reach_sec, mean_inter_reach_interval_sec "
                        "FROM video_pellets WHERE segment_num = 2").fetchone() == (None, None)


def test_a_results_file_without_the_fields_imports_with_them_empty(tmp_path):
    conn = _db(tmp_path)
    ir.import_file(conn, _features(tmp_path))
    assert conn.execute("SELECT total_frames, frame_rate FROM video_sessions").fetchone() == (None, None)


def test_an_older_database_gains_the_new_columns_and_keeps_its_rows(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "old.db"))
    old_pellets = ir.VIDEO_PELLETS_DDL
    for c, _t in ir.VIDEO_PELLET_TIMING_COLUMNS:
        old_pellets = "\n".join(line for line in old_pellets.splitlines()
                                if not line.strip().startswith(c + " "))
    old_sessions = ir.VIDEO_SESSIONS_DDL.replace(",\n    total_frames         INTEGER", "") \
        .replace(",\n    frame_rate           REAL", "")
    conn.execute(old_pellets)
    conn.execute(old_sessions)
    conn.execute("INSERT INTO video_sessions (video_name, subject_id) VALUES ('v', 's')")
    assert "total_frames" not in {r[1] for r in conn.execute("PRAGMA table_info(video_sessions)")}
    ir.ensure_video_tables(conn)
    have = {r[1] for r in conn.execute("PRAGMA table_info(video_pellets)")}
    assert {c for c, _t in ir.VIDEO_PELLET_TIMING_COLUMNS} <= have
    have = {r[1] for r in conn.execute("PRAGMA table_info(video_sessions)")}
    assert {"total_frames", "frame_rate"} <= have
    assert conn.execute("SELECT video_name, total_frames FROM video_sessions").fetchall() == [("v", None)]
    ir.ensure_video_tables(conn)          # a second call changes nothing
