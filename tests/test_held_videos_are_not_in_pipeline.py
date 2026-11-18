"""A video held for review, or one this computer has no file for, is not
counted as 'In Pipeline' / 'In Progress'.

WHY: the watcher marks a video 'triage' or 'deep_review' while it waits for a
person, and 'unresolvable' when this computer simply has no file for it.
mousedb did not know those states, so they fell into the catch-all and were
shown as videos the pipeline was working on. A person reading the view could
not tell that the video was waiting for THEM. Each now has its own bucket,
and a state mousedb does not know is shown under its own name ('other')
instead of being folded into a bucket that means something else.
"""
import json
import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest

from tests.test_watcher_views_read_the_live_database import _watcher_db

STATES = (["archived"] * 4 + ["triage"] * 3 + ["deep_review"] * 2
          + ["unresolvable"] * 5 + ["processing"] * 1 + ["outdated"] * 1
          + ["crystallized"] * 1 + ["failed"] * 1 + ["some_future_state"] * 2)


@pytest.fixture
def bridge(tmp_path):
    from mousedb.watcher_bridge import WatcherBridge
    db = tmp_path / "w.db"
    _watcher_db(db, STATES)
    return WatcherBridge(db_path=db)


def test_archive_summary_counts_held_and_absent_videos_on_their_own(bridge):
    s = bridge.get_archive_summary()
    assert s.total_videos == len(STATES)
    assert s.in_pipeline == 1                 # only 'processing'
    assert s.in_review == 5                   # triage + deep_review
    assert s.not_on_this_node == 5            # unresolvable
    assert s.other == {"some_future_state": 2}
    assert s.archived == 4 and s.outdated == 1 and s.crystallized == 1 and s.failed == 1
    parts = (s.archived + s.outdated + s.crystallized + s.in_pipeline + s.failed
             + s.in_review + s.not_on_this_node + sum(s.other.values()))
    assert parts == s.total_videos


def test_archive_cohort_and_animal_rollups_agree(bridge):
    (c,) = bridge.get_archive_cohort_rollup()
    (a,) = bridge.get_archive_animal_rollup()
    for r in (c, a):
        assert r.in_pipeline == 1
        assert r.in_review == 5
        assert r.not_on_this_node == 5
        assert r.other == {"some_future_state": 2}


def test_pipeline_summary_in_progress_leaves_out_held_and_absent(bridge):
    s = bridge.get_pipeline_summary()
    assert s.in_review_count == 5
    assert s.not_on_this_node_count == 5
    assert s.in_progress_count == 2           # processing + outdated (waiting to re-run)
    assert s.other_states == {"some_future_state": 2}
    (a,) = bridge.get_animal_rollup()
    assert (a.in_progress, a.in_review, a.not_on_this_node) == (2, 5, 5)
    c = bridge.get_cohort_rollup()["PROJA_01"]
    assert (c["in_progress"], c["in_review"], c["not_on_this_node"]) == (2, 5, 5)


def test_every_watcher_state_has_a_label():
    from mousedb.watcher_bridge import STATE_DISPLAY
    for st in ("triage", "deep_review", "unresolvable", "outdated", "crystallized"):
        assert st in STATE_DISPLAY


def _cfg(home, db):
    cfg = home / ".mousereach" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"watcher": {"db_path": str(db)},
                               "processing_root": str(db.parent)}), encoding="utf-8")


def test_video_status_command_in_progress_leaves_out_held(_isolated_from_this_machine,
                                                         tmp_path, capsys):
    db = tmp_path / "watcher.db"
    _watcher_db(db, STATES)
    _cfg(_isolated_from_this_machine, db)
    from mousedb import cli
    cli.cmd_video_status(Namespace(json=False, by_animal=True, cohort=None,
                                   show_errors=False))
    out = capsys.readouterr().out
    assert "2 in progress" in out
    assert "5 waiting for review" in out
    assert "5 not on this computer" in out
    assert "some_future_state" in out


def test_archive_status_json_has_the_new_buckets(_isolated_from_this_machine,
                                                 tmp_path, capsys):
    db = tmp_path / "watcher.db"
    _watcher_db(db, STATES)
    _cfg(_isolated_from_this_machine, db)
    from mousedb import cli
    cli.cmd_archive_status(Namespace(json=True, cohort=None, verbose=True))
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["in_pipeline"] == 1
    assert out["summary"]["in_review"] == 5
    assert out["summary"]["not_on_this_node"] == 5
    assert out["summary"]["other"] == {"some_future_state": 2}
    assert out["cohorts"][0]["in_review"] == 5
    assert out["animals"][0]["not_on_this_node"] == 5


ALL_HELD = STATES + ["quarantined"] * 3


def test_rollups_count_quarantined_per_animal_and_cohort(tmp_path):
    from mousedb.watcher_bridge import WatcherBridge
    db = tmp_path / "w.db"
    _watcher_db(db, ALL_HELD)
    b = WatcherBridge(db_path=db)
    (a,) = b.get_animal_rollup()
    assert a.quarantined == 3
    assert b.get_cohort_rollup()["PROJA_01"]["quarantined"] == 3


def test_video_status_by_animal_shows_quarantined_and_other(_isolated_from_this_machine,
                                                           tmp_path, capsys):
    db = tmp_path / "watcher.db"
    _watcher_db(db, ALL_HELD)
    _cfg(_isolated_from_this_machine, db)
    from mousedb import cli
    cli.cmd_video_status(Namespace(json=False, by_animal=True, cohort=None,
                                   show_errors=False))
    out = capsys.readouterr().out
    animal_part = out.split("By animal:")[1]
    header, _, row = animal_part.strip().splitlines()[:3]
    assert "Quarantined" in header and "Other" in header
    # Total, Done, In Progress, In Review, Not Here, Failed, Quarantined, Other
    nums = [int(x) for x in row.split()[1:]]
    assert nums[0] == len(ALL_HELD) and sum(nums[1:]) == nums[0]
    assert nums[6] == 3


# --- Archive Status view: quarantined is a hold, not a failure ------------

def test_archive_bucket_keeps_quarantined_out_of_failed(tmp_path):
    """A quarantined video waits for a person to fix the recording; showing
    it as Failed gave the hold the wrong reason."""
    from mousedb.watcher_bridge import WatcherBridge
    db = tmp_path / "w.db"
    _watcher_db(db, ALL_HELD)
    b = WatcherBridge(db_path=db)
    s = b.get_archive_summary()
    (c,) = b.get_archive_cohort_rollup()
    (a,) = b.get_archive_animal_rollup()
    for r in (s, c, a):
        # Only the one 'failed' video is Failed (quarantined used to be
        # folded in here, making it 4).
        assert r.failed == 1
        assert r.quarantined == 3
        parts = (r.archived + r.outdated + r.crystallized + r.in_pipeline
                 + r.in_review + r.not_on_this_node + r.quarantined
                 + r.failed + sum(r.other.values()))
        assert parts == r.total_videos == len(ALL_HELD)


def test_archive_status_command_counts_add_up_with_quarantined(
        _isolated_from_this_machine, tmp_path, capsys):
    db = tmp_path / "watcher.db"
    _watcher_db(db, ALL_HELD)
    _cfg(_isolated_from_this_machine, db)
    from mousedb import cli
    cli.cmd_archive_status(Namespace(json=False, cohort=None, verbose=True))
    out = capsys.readouterr().out
    assert "Quarantined (held until a person fixes the recording): 3" in out
    animal_part = out.split("Per-animal breakdown:")[1]
    header, _, row = animal_part.strip().splitlines()[:3]
    assert "Quarant" in header and "Other" in header
    # Total, Archived, Outdated, Crystal, Pipeline, Review, NotHere,
    # Quarant, Failed, Other
    nums = [int(x) for x in row.split()[1:]]
    assert nums[0] == len(ALL_HELD) and sum(nums[1:]) == nums[0]
    assert nums[7] == 3 and nums[8] == 1 and nums[9] == 2

    cli.cmd_archive_status(Namespace(json=True, cohort=None, verbose=True))
    js = json.loads(capsys.readouterr().out)
    for part in (js["summary"], js["cohorts"][0], js["animals"][0]):
        assert part["quarantined"] == 3
        assert part["failed"] == 1
