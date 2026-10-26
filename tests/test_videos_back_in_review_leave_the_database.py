"""A video sent back to review leaves the database until it is filed again.

WHY (2026-10-26): filing means done. A filed video goes back to a review queue
only when something in it is not done after all, but its rows stayed in the
database -- with its old answers -- and went out in every export as finished:
286 videos and 44,178 reach rows that day. Only positive evidence takes a video
out (its results sit in a review queue and not in Analyzed); its rows are copied
aside first; and its ledger entry goes, so filing it again brings it back even
when its file did not change.
"""
import contextlib
import json
import shutil
import sqlite3

from mousedb import import_reaches as ir
from mousedb import task_mutex
from mousedb.database import Database

VIDEO = "20260102_PROJA0101_P1"
OTHER = "20260102_PROJA0102_P1"


def _features(folder, video):
    folder.mkdir(parents=True, exist_ok=True)
    reach = {"reach_id": 1, "reach_num": 1, "start_frame": 10, "end_frame": 15,
             "duration_frames": 5, "causal_reach": True, "is_first_reach": True,
             "is_last_reach": True, "n_reaches_in_segment": 1, "flagged_for_review": False}
    segs = [{"segment_num": s, "outcome": "untouched", "outcome_confidence": 0.9,
             "reaches": [dict(reach, reach_id=s)] if s in (1, 2) else []} for s in range(1, 21)]
    path = folder / (video + "_features.json")
    path.write_text(json.dumps({"video_name": video, "segments": segs}), encoding="utf-8")
    return path


def _setup(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    db.init_db()
    root = tmp_path / "pipe"
    (root / "Analyzed").mkdir(parents=True)
    paths = {"db_path": tmp_path / "t.db", "mousereach_pipeline_root": root,
             "mousedb_root": tmp_path / "dbroot"}
    monkeypatch.setattr(ir, "require", lambda key: paths[key])
    monkeypatch.setattr(ir, "watcher_blocks_db", lambda: False)
    monkeypatch.setattr(task_mutex, "hold", lambda *a, **k: contextlib.nullcontext())
    import mousedb.backfill as bf
    import mousedb.prefetch as pf
    monkeypatch.setattr(bf, "backfill_phases", lambda **k: None)
    monkeypatch.setattr(pf, "warmed", lambda *a, **k: contextlib.nullcontext())
    return root, tmp_path / "t.db"


def _count(db, video):
    conn = sqlite3.connect(str(db))
    try:
        return (conn.execute("SELECT COUNT(*) FROM reach_data WHERE video_name = ?", (video,)).fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM video_pellets WHERE video_name = ?", (video,)).fetchone()[0])
    finally:
        conn.close()


def test_sent_back_taken_out_then_filed_again_comes_back(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    filed = root / "Analyzed" / "Project" / "PROJA01"
    queue = root / "Processing" / "Review" / "triage" / "Pending" / VIDEO
    _features(filed, VIDEO)
    _features(filed, OTHER)
    say = []
    assert ir.run(log=say.append).imported == 2
    assert _count(db, VIDEO) == (2, 20)

    # Sent back: the bundle moves from Analyzed into the triage queue.
    queue.mkdir(parents=True)
    shutil.move(str(filed / (VIDEO + "_features.json")), str(queue))
    res = ir.run(log=say.append)
    assert (res.withdrawn, res.withdrawn_reaches) == (1, 2)
    assert _count(db, VIDEO) == (0, 0)
    assert _count(db, OTHER) == (2, 20)                 # nothing else touched
    copies = list((tmp_path / "dbroot" / "_archived" / ir.WITHDRAWALS_DIR).rglob("*.csv"))
    assert sorted(c.name for c in copies) == [VIDEO + "_reach_data.csv", VIDEO + "_video_pellets.csv"]
    assert any("back in the triage queue" in m for m in res.messages)

    # Filed again, file unchanged: imported again because its ledger entry went.
    shutil.move(str(queue / (VIDEO + "_features.json")), str(filed))
    res = ir.run(log=say.append)
    assert res.imported == 1 and res.withdrawn == 0
    assert _count(db, VIDEO) == (2, 20)


def test_only_a_video_in_a_review_queue_and_not_filed_is_taken_out(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    filed = root / "Analyzed" / "Project" / "PROJA01"
    _features(filed, VIDEO)
    _features(filed, OTHER)
    ir.run(log=lambda *_: None)
    # VIDEO is in a queue AND still filed: filed wins, kept.
    _features(root / "Processing" / "Review" / "deep_review" / "Pending" / VIDEO, VIDEO)
    # OTHER vanished from Analyzed with no copy in any queue: no evidence, kept.
    (filed / (OTHER + "_features.json")).unlink()
    res = ir.run(log=lambda *_: None)
    assert res.withdrawn == 0
    assert _count(db, VIDEO) == (2, 20) and _count(db, OTHER) == (2, 20)


def test_a_dry_run_counts_and_changes_nothing(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    filed = root / "Analyzed" / "Project" / "PROJA01"
    _features(filed, VIDEO)
    _features(filed, OTHER)
    ir.run(log=lambda *_: None)
    queue = root / "Processing" / "Review" / "triage" / "Pending" / VIDEO
    queue.mkdir(parents=True)
    shutil.move(str(filed / (VIDEO + "_features.json")), str(queue))
    res = ir.run(dry_run=True, log=lambda *_: None)
    assert (res.withdrawn, res.withdrawn_reaches) == (1, 2)
    assert _count(db, VIDEO) == (2, 20)
    assert not (tmp_path / "dbroot" / "_archived").exists()
