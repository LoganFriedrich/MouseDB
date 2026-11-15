"""Rows of a video whose results file is gone from the pipeline are REPORTED.

WHY: the reach import takes a video's rows out only on positive evidence (its
results sit in a review queue). A results file that leaves Analyzed any other
way -- deleted, moved where the import does not look -- left its rows in the
database and in every export as if the video were still filed, and nothing
said so. Removing them automatically would turn a failed listing into lost
data, so the import reports them (its output, a list file, and the Where Is My
Data tab) and removes nothing.
"""
import json
import shutil

from mousedb import data_status
from mousedb import import_reaches as ir

from tests.test_videos_back_in_review_leave_the_database import (
    OTHER, _count, _features, _setup, VIDEO)

THIRD = "20260102_PROJA0103_P1"
# The list file the import writes (import_reaches.ROWS_WITHOUT_FILE_NAME).
LIST_NAME = "reach_rows_without_file.json"


def _report(tmp_path):
    p = tmp_path / "dbroot" / "logs" / LIST_NAME
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def _filed_three(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    filed = root / "Analyzed" / "Project" / "PROJA01"
    for v in (VIDEO, OTHER, THIRD):
        _features(filed, v)
    assert ir.run(log=lambda *_: None).imported == 3
    return root, db, filed


def test_a_video_whose_file_is_gone_is_reported_and_kept(tmp_path, monkeypatch):
    root, db, filed = _filed_three(tmp_path, monkeypatch)
    (filed / (VIDEO + ir.FEATURES_SUFFIX)).unlink()       # gone, not into a queue
    said = []
    res = ir.run(log=said.append)
    assert getattr(res, 'without_file', None) == [VIDEO]
    assert _count(db, VIDEO) == (2, 20)                    # nothing removed
    assert res.errors == 0                                 # exit code unchanged
    assert any(VIDEO in m and "no results file" in m for m in res.messages)
    # Said before the summary line, which must stay the last line.
    i = next(i for i, s in enumerate(said) if VIDEO in s and "no results file" in s)
    assert i < len(said) - 1 and said[-1].startswith("import-reaches: ")
    rep = _report(tmp_path)
    assert rep["count"] == 1 and rep["videos"] == [VIDEO] and rep["checked_at"]


def test_a_video_in_review_is_not_reported(tmp_path, monkeypatch):
    root, db, filed = _filed_three(tmp_path, monkeypatch)
    queue = root / "Processing" / "Review" / "triage" / "Pending" / VIDEO
    queue.mkdir(parents=True)
    shutil.move(str(filed / (VIDEO + ir.FEATURES_SUFFIX)), str(queue))
    res = ir.run(log=lambda *_: None)
    assert res.withdrawn == 1 and getattr(res, 'without_file', None) == []
    assert _report(tmp_path)["count"] == 0


def test_the_list_is_cleared_once_the_file_is_back(tmp_path, monkeypatch):
    root, db, filed = _filed_three(tmp_path, monkeypatch)
    keep = tmp_path / "aside.json"
    shutil.move(str(filed / (VIDEO + ir.FEATURES_SUFFIX)), str(keep))
    assert getattr(ir.run(log=lambda *_: None), 'without_file', None) == [VIDEO]
    shutil.move(str(keep), str(filed / (VIDEO + ir.FEATURES_SUFFIX)))
    assert getattr(ir.run(log=lambda *_: None), 'without_file', None) == []
    assert _report(tmp_path) == {"checked_at": _report(tmp_path)["checked_at"],
                                 "count": 0, "videos": []}


def test_a_dry_run_reports_but_writes_no_list(tmp_path, monkeypatch):
    root, db, filed = _filed_three(tmp_path, monkeypatch)
    (tmp_path / "dbroot" / "logs" / LIST_NAME).unlink(missing_ok=True)
    (filed / (VIDEO + ir.FEATURES_SUFFIX)).unlink()
    assert getattr(ir.run(dry_run=True, log=lambda *_: None), 'without_file', None) == [VIDEO]
    assert _report(tmp_path) is None


def test_where_is_my_data_names_them(tmp_path):
    problem = getattr(data_status, "rows_without_file_problem", None)
    assert problem is not None, "Where Is My Data does not report rows without a results file"
    p = tmp_path / LIST_NAME
    assert problem(p) is None          # never checked
    p.write_text(json.dumps({"checked_at": "2026-01-02T21:00:00", "count": 0,
                             "videos": []}), encoding="utf-8")
    assert problem(p) is None          # checked, none
    p.write_text(json.dumps({"checked_at": "2026-01-02T21:00:00", "count": 1,
                             "videos": [VIDEO]}), encoding="utf-8")
    line = problem(p)
    assert line.startswith("1 video(s) have reaches in the database but no results file")
    assert VIDEO in line and "Nothing was removed" in line and str(p) in line
    p.write_text("{", encoding="utf-8")
    assert "could not be read" in problem(p)


def test_a_renamed_results_file_is_not_reported(tmp_path, monkeypatch):
    """A results file renamed without re-processing still carries the old
    video name inside it, so its rows carry that name too. The rows also
    record which results FILE they came from (``source_file``), and that is
    what the report matches: the file is there, so nothing is reported."""
    root, db, filed = _filed_three(tmp_path, monkeypatch)
    renamed = "20260101_PROJA0109_P1"
    (filed / (VIDEO + ir.FEATURES_SUFFIX)).rename(filed / (renamed + ir.FEATURES_SUFFIX))
    res = ir.run(log=lambda *_: None)
    assert getattr(res, 'without_file', None) == []
    assert _report(tmp_path)["count"] == 0
    assert _count(db, VIDEO) == (2, 20)                    # the rows are still there


def test_rows_with_no_source_file_fall_back_to_the_video_name(tmp_path, monkeypatch):
    import sqlite3
    root, db, filed = _filed_three(tmp_path, monkeypatch)
    c = sqlite3.connect(str(tmp_path / "t.db"))
    with c:
        c.execute("UPDATE reach_data SET source_file = ''")
        c.execute("UPDATE video_pellets SET source_file = ''")
    c.close()
    (filed / (OTHER + ir.FEATURES_SUFFIX)).unlink()
    assert getattr(ir.run(log=lambda *_: None), 'without_file', None) == [OTHER]
