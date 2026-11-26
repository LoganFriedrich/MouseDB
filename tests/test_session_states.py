"""Progress is counted against the tracking sheets: every hand-scored session
is in exactly one state, and done % is filed / sheet-scored.

WHY: progress counted from the pipeline alone cannot see a recording that
never reached it. A cohort's hand-scored sessions sat on a recording PC for
over a year while every progress figure read as if they did not exist.
Every input here is synthetic, under tmp_path.
"""
import csv
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mousedb import missing_videos as mv  # noqa: E402
from mousedb import session_states as ss  # noqa: E402
from mousedb.output_folders import hand_scores_dir

VIEW = "2026-03-20T02:00:00"
TAKEN = datetime.fromisoformat(VIEW)


def _h(subject, day, tray, cohort):
    key = "%s_%s_%s" % (day.replace("-", ""), subject.replace("_", ""), tray)
    return key, {"cohort_id": cohort, "subject_id": subject, "session_date": day, "tray": tray}


def _hand_dict(*sessions):
    return dict(_h(*s) for s in sessions)


def _state(res, key):
    [r] = [r for r in res["sessions"] if r["video_key"] == key]
    return r


# --- each state -------------------------------------------------------------------

def test_every_census_place_maps_to_one_state():
    hand = _hand_dict(("PROJA_01_01", "2026-03-02", "P1", "PROJA_01"),
                      ("PROJA_01_02", "2026-03-02", "P1", "PROJA_01"),
                      ("PROJA_01_03", "2026-03-02", "P1", "PROJA_01"),
                      ("PROJA_01_04", "2026-03-02", "P1", "PROJA_01"),
                      ("PROJA_01_05", "2026-03-02", "P1", "PROJA_01"),
                      ("PROJA_01_06", "2026-03-02", "P1", "PROJA_01"),
                      ("PROJA_01_07", "2026-03-02", "P1", "PROJA_01"),
                      ("PROJA_01_08", "2026-03-02", "P1", "PROJA_01"),
                      ("PROJA_01_09", "2026-03-02", "P1", "PROJA_01"))
    census = {
        "20260302_PROJA0101_P1": {"element": "mousereach", "finished": True},
        "20260302_PROJA0102_P1": {"element": "mousereach", "finished": False},
        "20260302_PROJA0103_P1": {"element": "unanalyzed", "finished": False},
        "20260302_PROJA0104_P1": {"element": "crop_dlc", "finished": False},
        # Filed, then sent back to a queue: counted in the queue, not as filed.
        "20260302_PROJA0105_P1": {"element": "triage", "finished": True},
        "20260302_PROJA0106_P1": {"element": "deep_review", "finished": False},
        "20260302_PROJA0107_P1": {"element": "quarantined", "finished": False},
        "20260302_PROJA0108_P1": {"element": "recording_issue", "finished": False},
    }
    res = ss.classify(hand, census, TAKEN)
    got = {r["video_key"].split("_")[1]: r["state"] for r in res["sessions"]}
    assert got == {"PROJA0101": "filed", "PROJA0102": "in_processing",
                   "PROJA0103": "in_processing", "PROJA0104": "in_processing",
                   "PROJA0105": "held_for_review", "PROJA0106": "held_for_review",
                   "PROJA0107": "held_for_review", "PROJA0108": "recording_failed",
                   "PROJA0109": "not_on_server"}
    d = res["by_cohort"]["PROJA_01"]
    assert sum(d[s] for s in ss.STATES) == d["sheet_scored"] == 9
    assert d["held"] == {"triage": 1, "deep_review": 1, "quarantined": 1}


def test_done_percent_is_filed_over_sheet_scored_with_pillar_separately():
    hand = _hand_dict(("PROJA_01_01", "2026-03-02", "P1", "PROJA_01"),
                      ("PROJA_01_01", "2026-03-02", "P2", "PROJA_01"),
                      ("PROJA_01_01", "2026-03-02", "E1", "PROJA_01"),
                      ("PROJA_01_01", "2026-03-02", "F1", "PROJA_01"))
    census = {"20260302_PROJA0101_P1": {"element": "mousereach", "finished": True},
              "20260302_PROJA0101_E1": {"element": "unanalyzed", "finished": False}}
    d = ss.classify(hand, census, TAKEN)["by_cohort"]["PROJA_01"]
    # The pipeline knows 2 sessions; the sheet says 4 were recorded.
    assert (d["filed"], d["sheet_scored"], d["done_pct"]) == (1, 4, 25.0)
    assert (d["pillar_filed"], d["pillar_scored"], d["pillar_done_pct"]) == (1, 2, 50.0)
    assert d["not_on_server"] == 2


def test_a_pipeline_hold_outside_the_queues_is_held_for_review():
    hand = _hand_dict(("PROJA_01_01", "2026-03-02", "P1", "PROJA_01"),
                      ("PROJA_01_02", "2026-03-02", "P1", "PROJA_01"),
                      ("PROJA_01_03", "2026-03-02", "P1", "PROJA_01"))
    census = {k: {"element": "mousereach", "finished": False} for k in hand}
    holds = {"20260302_PROJA0101_P1": "label_mismatch",
             "20260302_PROJA0102_P1": "hand_scores_not_found"}
    res = ss.classify(hand, census, TAKEN, holds=holds)
    assert _state(res, "20260302_PROJA0101_P1")["state"] == "held_for_review"
    assert _state(res, "20260302_PROJA0101_P1")["detail"] == "label_mismatch"
    assert _state(res, "20260302_PROJA0102_P1")["state"] == "held_for_review"
    assert _state(res, "20260302_PROJA0103_P1")["state"] == "in_processing"


def test_holds_are_read_from_the_pipelines_attention_lists(tmp_path):
    att = tmp_path / "attention"
    att.mkdir()
    (att / "HOST_hand_scores.json").write_text(json.dumps({"items": [
        {"kind": "label_mismatch", "video_id": "20260302_PROJA0101_P1", "needs_person": True},
        {"kind": "hand_scores_waiting", "video_id": "20260302_PROJA0102_P1"},
        {"kind": "hand_score_check_failed", "video_id": "20260302_PROJA0103_P1"}]}))
    (att / "HOST_broken.json").write_text("{not json")
    holds, problems = ss.read_holds(tmp_path)
    # Waiting for the sheet's next import releases itself: not a hold.
    assert holds == {"20260302_PROJA0101_P1": "label_mismatch",
                     "20260302_PROJA0103_P1": "hand_score_check_failed"}
    assert len(problems) == 1 and "HOST_broken.json" in problems[0]


# --- not on the server yet --------------------------------------------------------

def test_not_on_server_is_red_only_when_overdue_live_and_unacknowledged():
    hand = _hand_dict(("PROJA_01_01", "2026-03-02", "P1", "PROJA_01"),   # overdue
                      ("PROJA_01_01", "2026-03-18", "P1", "PROJA_01"),   # recent
                      ("PROJA_01_02", "2026-03-02", "P1", "PROJA_01"),   # acknowledged
                      ("PROJB_04_01", "2020-01-01", "P1", "PROJB_04"))   # frozen sheet
    acks = {"20260302_PROJA0102_P1": {"by": "someone", "reason": "gone"}}
    res = ss.classify(hand, {}, TAKEN, live_cohorts={"PROJA_01"}, grace=3, acks=acks)
    red = {r["video_key"] for r in res["sessions"] if r["red"]}
    assert red == {"20260302_PROJA0101_P1"}
    d = res["by_cohort"]["PROJA_01"]
    assert (d["not_on_server"], d["not_on_server_red"], d["not_on_server_recent"],
            d["not_on_server_acknowledged"]) == (3, 1, 1, 1)
    # Every one stays in the denominator: the sheet says it was recorded.
    assert d["sheet_scored"] == 3 and d["done_pct"] == 0.0
    assert res["by_cohort"]["PROJB_04"]["live_sheet"] is False


def test_red_sessions_are_exactly_what_missing_videos_lists(tmp_path, monkeypatch):
    root = tmp_path / "db"
    hand_scores_dir(root).mkdir(parents=True)
    (root / "logs").mkdir()
    monkeypatch.setenv("MOUSEDB_ROOT", str(root))
    monkeypatch.setenv("MOUSEREACH_PIPELINE_ROOT", str(tmp_path / "pipe"))
    sessions = [("PROJA_01_01", "2026-03-02", "P1", "PROJA_01"),
                ("PROJA_01_02", "2026-03-02", "P1", "PROJA_01"),
                ("PROJA_01_02", "2026-03-02", "P2", "PROJA_01"),
                ("PROJA_01_03", "2026-03-19", "P1", "PROJA_01"),
                ("PROJA_02_01", "2026-03-05", "P1", "PROJA_02"),
                ("PROJB_04_01", "2020-01-01", "P1", "PROJB_04")]
    with open(hand_scores_dir(root) / "hand_pellet_scores.csv", "w",
              newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["video_key", "pellet", "score", "subject_id", "cohort_id", "project",
                    "session_date", "tray"])
        for sid, day, tray, cohort in sessions:
            key = "%s_%s_%s" % (day.replace("-", ""), sid.replace("_", ""), tray)
            w.writerow([key, 1, 0, sid, cohort, cohort.split("_")[0], day, tray])
    (hand_scores_dir(root) / "hand_pellet_scores.json").write_text(
        json.dumps({"sheets_seen": ["PROJA_01", "PROJA_02"]}))
    (root / "logs" / "pipeline_census.json").write_text(json.dumps({
        "generated_at": VIEW, "recording_issues": [],
        "sessions": {"20260302_PROJA0101_P1": {"element": "mousereach", "finished": True},
                     "20260302_PROJA0102_P3": {"element": "triage", "finished": False}}}))
    res = ss.from_files()
    red = {r["video_key"] for r in res["sessions"] if r["red"]}
    missing = {g["video_key"] for g in mv.check()["gaps"] if g["status"] == "missing"}
    assert red == missing == {"20260302_PROJA0102_P1", "20260302_PROJA0102_P2",
                              "20260305_PROJA0201_P1"}
    # The pipeline holds that animal and day under a tray the sheet does not have.
    assert _state(res, "20260302_PROJA0102_P1")["other_name"] == "P3"
    assert res["by_cohort"]["PROJA_01"]["not_on_server_other_name"] == 2
    assert res["caveats"] == []


# --- the reverse: videos with no sheet row ---------------------------------------

def test_videos_with_no_sheet_row_are_listed_never_dropped():
    hand = _hand_dict(("PROJA_01_01", "2026-03-02", "P1", "PROJA_01"))
    census = {"20260302_PROJA0101_P1": {"element": "mousereach", "finished": True},
              "20260302_PROJA0109_P1": {"element": "mousereach", "finished": True},
              "20260302_PROJC0301_P4": {"element": "unanalyzed", "finished": False},
              "20260302_unreadable": {"element": "unanalyzed", "finished": False}}
    res = ss.classify(hand, census, TAKEN)
    by = {u["video_key"]: u["cohort_id"] for u in res["unmatched_videos"]}
    assert by == {"20260302_PROJA0109_P1": "PROJA_01", "20260302_PROJC0301_P4": "PROJC_03",
                  "20260302_unreadable": "?"}
    assert res["by_cohort"]["PROJA_01"]["videos_without_sheet_row"] == 1
    # A cohort the sheets do not have at all still shows its videos, with no done %.
    assert res["by_cohort"]["PROJC_03"]["sheet_scored"] == 0
    assert res["by_cohort"]["PROJC_03"]["done_pct"] is None
    assert res["totals"]["videos_without_sheet_row"] == 3
    # Never counted in done %.
    assert res["by_cohort"]["PROJA_01"]["done_pct"] == 100.0


def test_a_census_that_cannot_report_failed_recordings_says_so():
    hand = _hand_dict(("PROJA_01_01", "2026-03-02", "P1", "PROJA_01"))
    res = ss.classify(hand, {}, TAKEN, recording_issues_reported=False)
    assert any("failed recordings" in c for c in res["caveats"])


def test_failed_recording_listed_by_the_census_wins_over_any_other_place():
    hand = _hand_dict(("PROJA_01_01", "2026-03-02", "P1", "PROJA_01"))
    failed = {"20260302_PROJA0101_P1": {"collage": "x.mkv", "reason": "empty file"}}
    res = ss.classify(hand, {"20260302_PROJA0101_P1": {"element": "unanalyzed"}}, TAKEN,
                      failed=failed)
    r = _state(res, "20260302_PROJA0101_P1")
    assert (r["state"], r["detail"]) == ("recording_failed", "empty file")


# --- where it is shown -------------------------------------------------------------

def test_the_words_give_the_sheet_denominator_and_every_state():
    hand = _hand_dict(("PROJA_01_01", "2026-03-02", "P1", "PROJA_01"),
                      ("PROJA_01_02", "2026-03-02", "P1", "PROJA_01"))
    res = ss.classify(hand, {"20260302_PROJA0101_P1": {"element": "mousereach",
                                                        "finished": True}}, TAKEN)
    line = ss.headline(res)
    assert line.startswith("1 of 2 hand-scored sessions filed (50.0%)")
    for words in ("in processing", "held for review", "recording failed",
                  "NOT ON THE SERVER YET (1 overdue"):
        assert words in line
    table = ss.cohort_lines(res)
    assert table[1].split()[:4] == ["PROJA_01", "2", "1", "50.0%"]
    line.encode("ascii")
    "\n".join(table).encode("ascii")
    [g] = ss.not_on_server_recordings(res)
    assert (g["cohort_id"], g["tray"], g["animals"]) == ("PROJA_01", "P1", ["PROJA0102"])


def test_the_weekly_page_headline_uses_the_sheet_denominator():
    from mousedb import weekly_report
    hand = _hand_dict(("PROJA_01_01", "2026-03-02", "P1", "PROJA_01"),
                      ("PROJA_01_02", "2026-03-02", "P1", "PROJA_01"))
    res = ss.classify(hand, {"20260302_PROJA0101_P1": {"element": "mousereach",
                                                        "finished": True}}, TAKEN)
    page = weekly_report.sheet_progress_html(res)
    assert "1 of 2 hand-scored sessions filed (50.0%)" in page
    assert "NOT ON THE SERVER YET" in page
    assert "PROJA_01 2026-03-02 tray P1" in page


def test_the_tab_cells_show_done_and_not_on_server():
    from mousedb.gui.data_status_tab import sheet_cells
    hand = _hand_dict(("PROJA_01_01", "2026-03-02", "P1", "PROJA_01"),
                      ("PROJA_01_02", "2026-03-02", "P1", "PROJA_01"))
    res = ss.classify(hand, {"20260302_PROJA0101_P1": {"element": "mousereach",
                                                        "finished": True}}, TAKEN)
    done, notsrv, others, nosheet, red = sheet_cells(res["by_cohort"]["PROJA_01"])
    assert done == "1 / 2 (50.0%)"
    assert notsrv.startswith("1") and red is True
    assert others == "0 / 0 / 0" and nosheet == "0"
    assert sheet_cells(None)[:4] == ("-", "-", "-", "-")
