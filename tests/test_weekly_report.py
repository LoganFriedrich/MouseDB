"""Weekly 'Where is my data' page: stage counting and the not-started reasons."""
from collections import Counter

from mousedb import weekly_report as w


def test_stage_mapping():
    assert w._stage({"element": "mousereach", "finished": True}) == "done"
    assert w._stage({"element": "unanalyzed", "tray": "P"}) == "not_started_pillar"
    assert w._stage({"element": "unanalyzed", "tray": "E"}) == "not_started_easy_flat"
    assert w._stage({"element": "triage"}) == "triage"


def test_why_not_started_groups(tmp_path, monkeypatch):
    intake = tmp_path / "intake"
    intake.mkdir()
    (intake / "20240101_PRJA0101,PRJA0102_P1.mkv").write_bytes(b"")
    monkeypatch.setattr(w, "_priority_lists", lambda: ({"PRJA": {"20240101_PRJA0101_P1"}}, "A then B"))
    census = {"roots": {"collages_intake": {"path": str(intake)}}, "sessions": {
        "20240101_PRJA0101_P1": {"element": "unanalyzed", "tray": "P", "project": "PRJA"},
        "20240101_PRJA0102_P1": {"element": "unanalyzed", "tray": "P", "project": "PRJA"},
        "20240101_PRJA0103_P1": {"element": "unanalyzed", "tray": "P", "project": "PRJA"},
        "20240101_X01_P1.mkv_1": {"element": "unanalyzed", "tray": "P", "project": "X"},
        "20240101_PRJA0104_E1": {"element": "unanalyzed", "tray": "E", "project": "PRJA"},
    }}
    problems = []
    page = w.why_not_started(census, problems)
    assert problems == []
    assert "on the priority list)</td><td>1<" in page
    assert "after the priority list)</td><td>1<" in page
    assert "no recording for it in the intake folder" in page and "20240101_PRJA0103_P1" in page
    assert "does not read as animal IDs" in page
    assert "PRJA0104_E1" not in page          # Easy/Flat are never listed
    assert "A then B" in page                 # the work order's own words


def test_unreadable_intake_says_so(tmp_path):
    census = {"roots": {"collages_intake": {"path": str(tmp_path / "missing")}},
              "sessions": {"20240101_PRJA0101_P1": {"element": "unanalyzed", "tray": "P", "project": "PRJA"}}}
    problems = []
    assert "could not be read" in w.why_not_started(census, problems)
    assert problems
