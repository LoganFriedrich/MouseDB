"""A results file that disappears between the listing and its first read is
skipped, and the rest of the run still imports.

WHY (2026-11-03): the watcher refiles a video by replacing its results file.
On 2026-11-02 21:00 one file went missing in that window and the whole import
stopped with nothing imported, while a version bump was rewriting thousands of
results files a day.
"""
from mousedb import import_reaches as ir

from tests.test_videos_back_in_review_leave_the_database import (
    VIDEO, _count, _setup, _features)


def test_a_file_gone_while_listing_does_not_stop_the_run(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    folder = root / "Analyzed" / "Project" / "PROJA01"
    _features(folder, VIDEO)
    gone = folder / "20990101_PROJA0199_P1_features.json"
    real = ir.find_features_files
    monkeypatch.setattr(ir, "find_features_files",
                        lambda *a, **k: list(real(*a, **k)) + [gone])
    said = []
    res = ir.run(log=said.append)
    assert res.imported == 1 and res.errors == 0
    assert any("vanished while listing" in s for s in said)
    assert _count(db, VIDEO)[0] > 0
