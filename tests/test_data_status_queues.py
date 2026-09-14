"""Where Is My Data counts review queues honestly.

The deep-review queue folder was renamed (flagged_for_review -> deep_review).
Reading the old name made every cohort's deep-review count a silent 0, which
tells a person nothing is waiting when videos are. A queue folder that cannot
be read must show up as a problem, and scratch folders inside a queue are not
videos.
"""
from pathlib import Path

import mousedb.data_status as ds


def _root(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(ds, "_pipeline_root", lambda: tmp_path)
    return tmp_path / "Processing" / "Review"


def test_the_deep_review_queue_is_read_under_its_current_name():
    assert ds.DEEP_REVIEW_QUEUE == "deep_review"
    assert ds.TRIAGE_QUEUE == "triage"


def test_only_date_named_bundles_count(tmp_path, monkeypatch):
    review = _root(tmp_path, monkeypatch)
    for d in ("20250101_CNT0101_P1", "20220318_ASPA0501_P4", "_Problematic", ".return_claims", "notes"):
        (review / "deep_review" / d).mkdir(parents=True)
    (review / "deep_review" / "20250101_CNT0102_P1.txt").write_text("a file, not a bundle")
    assert sorted(ds._queue_videos("deep_review")) == ["20220318_ASPA0501_P4", "20250101_CNT0101_P1"]


def test_an_unreadable_queue_is_none_not_empty(tmp_path, monkeypatch):
    review = _root(tmp_path, monkeypatch)
    review.mkdir(parents=True)
    assert ds._queue_videos("deep_review") is None               # missing
    (review / "flagged_for_review").write_text("moved to deep_review")
    assert ds._queue_videos("flagged_for_review") is None        # a guard file, not a folder
    (review / "triage").mkdir()
    assert ds._queue_videos("triage") == []                      # present and truly empty


def test_counts_by_cohort_and_a_missing_queue_is_reported(tmp_path, monkeypatch):
    review = _root(tmp_path, monkeypatch)
    (review / "triage" / "20250101_CNT0101_P1").mkdir(parents=True)
    (review / "triage" / "20250102_CNT0101_P2").mkdir(parents=True)
    problems = []
    counts = ds.review_queue_counts(problems)
    assert counts == {"CNT_01": {"triage": 2, "deep_review": 0}}
    assert len(problems) == 1 and "deep_review" in problems[0] and "not zero" in problems[0]


def test_both_queues_counted_with_no_problem(tmp_path, monkeypatch):
    review = _root(tmp_path, monkeypatch)
    (review / "triage" / "20250101_CNT0101_P1").mkdir(parents=True)
    (review / "deep_review" / "20220318_ASPA0501_P4").mkdir(parents=True)
    problems = []
    counts = ds.review_queue_counts(problems)
    assert problems == []
    assert counts == {"CNT_01": {"triage": 1, "deep_review": 0},
                      "ASPA_05": {"triage": 0, "deep_review": 1}}
