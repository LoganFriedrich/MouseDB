"""The census-database join: promotion, the invariant, and honest refusals.

Identifiers are synthetic (project ABC). The behaviours under test were each
measured to fail silently before being guarded; assert behaviour, never a
corpus count.
"""
import pytest

from mousedb.pipeline_census import join_with_db, load_cached, save_cache


def _census():
    S = {
        # finished, not held, IN the database -> analyzed
        "20240101_ABC0101_P1": {"element": "mousereach", "finished": True,
                                "tray": "P", "project": "ABC"},
        # finished, not held, NOT in the database -> the invariant gap
        "20240101_ABC0102_P1": {"element": "mousereach", "finished": True,
                                "tray": "P", "project": "ABC"},
        # finished but HELD for a person -> stays in its queue, never a violation
        "20240101_ABC0103_P1": {"element": "triage", "finished": True,
                                "tray": "P", "project": "ABC"},
        # still in cropping/pose
        "20240101_ABC0104_P1": {"element": "crop_dlc", "finished": False,
                                "tray": "P", "project": "ABC"},
        # outcome-free tray, finished -> session_only, no database condition
        "20240101_ABC0105_E1": {"element": "mousereach", "finished": True,
                                "tray": "E", "project": "ABC"},
        # untouched
        "20240101_ABC0106_P1": {"element": "unanalyzed", "finished": False,
                                "tray": "P", "project": "ABC"},
    }
    return {"generated_at": "2026-09-02T10:00:00", "scan_seconds": 1.0,
            "totals": {"expected": 6, "finished_files": 4, "unfinished": 2},
            "sessions": S, "by_element": {}, "eta": {}, "review": {},
            "diagnostics": {}, "roots": {}}


def test_finished_and_landed_becomes_analyzed():
    j = join_with_db(_census(), {"20240101_ABC0101_P1"})
    assert j["by_element"]["analyzed"] == 1
    assert j["totals"]["analyzed"] == 1


def test_finished_but_not_landed_is_the_invariant_gap():
    j = join_with_db(_census(), {"20240101_ABC0101_P1"})
    inv = j["invariant"]
    assert inv["count"] == 1
    assert "20240101_ABC0102_P1" in inv["sessions"]
    # the reason names the transient path so a fresh finish is not an alarm
    assert "next hourly import" in inv["sessions"]["20240101_ABC0102_P1"]


def test_held_sessions_are_never_violations():
    j = join_with_db(_census(), set())
    assert "20240101_ABC0103_P1" not in j["invariant"]["sessions"]
    assert j["by_element"]["triage"] == 1


def test_outcome_free_tray_needs_no_database_row():
    j = join_with_db(_census(), set())
    assert j["by_element"]["session_only"] == 1
    assert "20240101_ABC0105_E1" not in j["invariant"]["sessions"]


def test_no_database_view_refuses_rather_than_guessing():
    j = join_with_db(_census(), None)
    assert j["database_view"] is False
    assert j["totals"]["analyzed"] is None      # None, never zero
    assert j["invariant"] is None               # no verdict, not "0 violations"
    assert j["caveats"]


def test_cohort_rollup_uses_video_names():
    j = join_with_db(_census(), {"20240101_ABC0101_P1"})
    row = j["by_cohort"]["ABC_01"]
    assert row["expected"] == 6
    assert row["analyzed"] == 1
    assert row["crop_dlc"] == 1


def test_cache_roundtrip(tmp_path):
    p = tmp_path / "pipeline_census.json"
    save_cache(_census(), p)
    assert load_cached(p)["totals"]["expected"] == 6


def test_missing_cache_is_none_not_error(tmp_path):
    assert load_cached(tmp_path / "nope.json") is None


def test_corrupt_cache_raises_rather_than_reading_as_absent(tmp_path):
    p = tmp_path / "pipeline_census.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(Exception):
        load_cached(p)
