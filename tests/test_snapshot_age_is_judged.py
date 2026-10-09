"""Every figure in "where is my data" is as of the snapshot, so its age matters.

The snapshot is rewritten by a scheduled job. When that job fails -- most often
because another program had the database locked -- the numbers do not go wrong,
they stop moving, which is harder to notice. Printing the timestamp was not
enough: it asks the reader to know what today is, know how often the job is
meant to run, and do the subtraction. Three days once passed that way.
"""
import datetime as dt

import pytest

from mousedb import data_status


# ------------------------------------------------------------------- in words
@pytest.mark.parametrize("hours,expected", [
    (0.25, "under an hour"),
    (1.0, "1 hour"),
    (4.0, "4 hours"),
    (30.0, "30 hours"),
    (72.0, "3 days"),
    (24.4, "24 hours"),
])
def test_an_age_reads_as_a_person_would_say_it(hours, expected):
    assert data_status.describe_age(hours) == expected


def test_an_unknown_age_says_unknown_rather_than_guessing():
    assert data_status.describe_age(None) == "unknown"


# ------------------------------------------------------- judged, not reported
def _snapshot(tmp_path, hours_old):
    """A snapshot folder whose parquet files are a given age."""
    import pandas as pd
    for name, frame in (
            ("reach_data", pd.DataFrame({"subject_id": ["PROJ_01_01"],
                                         "video_name": ["v"],
                                         "outcome_source": ["algo"],
                                         "segment_num": [1]})),
            ("pellet_scores", pd.DataFrame({"subject_id": ["PROJ_01_01"],
                                            "session_date": ["2026-01-01"]}))):
        frame.to_parquet(tmp_path / ("%s.parquet" % name), index=False)

    when = (dt.datetime.now() - dt.timedelta(hours=hours_old)).timestamp()
    for p in tmp_path.glob("*.parquet"):
        import os
        os.utime(p, (when, when))
    return tmp_path


def test_a_fresh_snapshot_is_not_complained_about(tmp_path, monkeypatch):
    """Warning about a healthy system teaches people to ignore warnings."""
    pytest.importorskip("pandas")
    st = data_status.status(_snapshot(tmp_path, hours_old=4))
    assert st["snapshot_age"] == "4 hours"
    assert not any("old" in p for p in st["problems"])


def test_a_snapshot_that_stopped_being_rewritten_is_called_out(tmp_path):
    """The failure this is for: a scheduled job that has not run for days."""
    pytest.importorskip("pandas")
    st = data_status.status(_snapshot(tmp_path, hours_old=72))
    assert st["snapshot_age"] == "3 days"
    trouble = " ".join(st["problems"])
    assert "3 days old" in trouble
    assert "has not succeeded" in trouble
    assert "will not appear" in trouble, (
        "must say what the consequence is, not only that a number is old")


def test_the_threshold_allows_one_late_run_of_a_daily_job(tmp_path):
    """24 h means a job that ran an hour late; that is not news."""
    pytest.importorskip("pandas")
    assert data_status.STALE_SNAPSHOT_HOURS > 24
    st = data_status.status(_snapshot(tmp_path, hours_old=25))
    assert not any("old" in p for p in st["problems"])


def test_the_age_is_reported_as_a_number_too_so_a_screen_can_colour_it(tmp_path):
    pytest.importorskip("pandas")
    st = data_status.status(_snapshot(tmp_path, hours_old=72))
    assert 70 < st["snapshot_age_hours"] < 74
