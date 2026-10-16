"""Every pellet the pipeline cut a video into is recorded, reached for or not.

WHY: reach_data has one row per reach, so a pellet nobody reached for left no
trace, and "pellets presented" counted from reach rows came out below 20 in
over half the sessions (2026-10-16). How many pellets the video shows is a
measurement -- 20 unless something went wrong -- and it needs a row per pellet.
"""
import contextlib
import json
import sqlite3

from mousedb import import_reaches as ir
from mousedb import task_mutex
from mousedb.database import Database

VIDEO = "20260102_PROJA0101_P1"


def _reach(n):
    return {"reach_id": n, "reach_num": 1, "start_frame": 10 * n, "end_frame": 10 * n + 5,
            "duration_frames": 5, "causal_reach": True, "is_first_reach": True,
            "is_last_reach": True, "n_reaches_in_segment": 1, "flagged_for_review": False}


def _features(folder, n_pellets=20, reached=(3, 7)):
    segs = []
    for s in range(1, n_pellets + 1):
        segs.append({"segment_num": s,
                     "outcome": "retrieved" if s == 7 else "untouched",
                     "outcome_confidence": 0.9,
                     "reaches": [_reach(s)] if s in reached else []})
    path = folder / (VIDEO + "_features.json")
    path.write_text(json.dumps({"video_name": VIDEO, "segments": segs}), encoding="utf-8")
    return path


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_db()
    conn = sqlite3.connect(str(db.db_path))
    with conn:
        ir.ensure_columns(conn)
    return conn


def test_a_pellet_with_no_reach_still_gets_a_row(tmp_path):
    conn = _db(tmp_path)
    ir.import_file(conn, _features(tmp_path))
    rows = conn.execute("SELECT segment_num, outcome, n_reaches FROM video_pellets "
                        "WHERE video_name = ? ORDER BY segment_num", (VIDEO,)).fetchall()
    assert len(rows) == 20
    assert rows[0] == (1, "untouched", 0)
    assert rows[6] == (7, "retrieved", 1)
    assert conn.execute("SELECT COUNT(*) FROM reach_data WHERE video_name = ?", (VIDEO,)).fetchone()[0] == 2


def test_a_reimport_replaces_the_pellets_so_a_short_video_shows_as_short(tmp_path):
    conn = _db(tmp_path)
    ir.import_file(conn, _features(tmp_path))
    ir.import_file(conn, _features(tmp_path, n_pellets=19))
    assert conn.execute("SELECT COUNT(*) FROM video_pellets WHERE video_name = ?", (VIDEO,)).fetchone()[0] == 19


def test_the_backfill_fills_pellets_and_touches_nothing_else(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    root = tmp_path / "pipe"
    folder = root / "Analyzed" / "Project"
    folder.mkdir(parents=True)
    ir.import_file(conn, _features(folder))
    conn.execute("DELETE FROM video_pellets")
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM reach_data").fetchone()[0]

    monkeypatch.setattr(task_mutex, "hold", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(ir, "require", lambda key: tmp_path / "t.db" if key == "db_path" else root)
    said = []
    out = ir.backfill_video_pellets(log=said.append)
    assert out["videos"] == 1 and out["pellets_per_video"] == {20: 1}
    assert conn.execute("SELECT COUNT(*) FROM video_pellets").fetchone()[0] == 20
    assert conn.execute("SELECT COUNT(*) FROM reach_data").fetchone()[0] == before
    assert any("20 pellets: 1 videos" in s for s in said)
