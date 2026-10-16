"""Pellets presented is the tray's 20, and every percentage is out of it.

WHY: until 2026-10-16 "pellets_presented" counted the pellets the animal reached
for at least once, and every percentage divided by it. A session in which the
animal ignored most pellets then showed an inflated success rate -- up to 100%
where the truth was 5%. The count of pellets reached for is a useful number in
its own right, so it is kept, under a name that says what it is.
"""
import pandas as pd

from mousedb.exporters import analysis_reaches as ar
from mousedb.exporters import analysis_sessions as asess


def _session(manual_displaced=None, manual_retrieved=None):
    # One video: reaches at pellets 1, 1 and 4 only; the algorithm saw one
    # displaced and one retrieved pellet among them.
    n = 3
    base = {
        "SubjectID": ["PROJ_01_01"] * n, "Test_Date": ["2026-01-02"] * n,
        "Session_ID": ["s1"] * n, "Tray_ID": ["P1"] * n, "tray_type": ["P"] * n,
        "Test_Type": ["Baseline"] * n, "Test_Type_Grouped": ["Pre-injury"] * n,
        "Days_Post_Injury": [None] * n, "Injury_Type": ["Contusion"] * n,
        "InjGroupAssignTyp": ["A"] * n, "SexTyp": ["F"] * n,
        "Exclusion_in_origin_study": ["No exclusion"] * n,
        "video_name": ["20260102_PROJ0101_P1"] * n,
        "segment_num": [1, 1, 4], "reach_num": [1, 2, 1],
        "segment_outcome": ["retrieved", "retrieved", "displaced_sa"],
        "causal_reach": [0, 1, 1], "duration_frames": [12, 9, 10],
        "Total_Swipes_AI": [n] * n,
        "Video_Displaced": [1] * n, "Video_Retrieved": [1] * n, "Video_Contacted": [2] * n,
    }
    if manual_displaced is not None:
        base.update({"Manual_Displaced": [manual_displaced] * n,
                     "Manual_Retrieved": [manual_retrieved] * n,
                     "Manual_Contacted": [manual_displaced + manual_retrieved] * n})
    reaches, rows = ar.build(pd.DataFrame(base))
    sessions, srows = asess.build(reaches, rows)
    return reaches, sessions.iloc[0], {r["VariableName"]: r for r in srows}


def test_presented_is_twenty_and_reached_for_is_kept_separately():
    _, s, _ = _session()
    assert str(s["pellets_presented"]) == "20"
    assert str(s["pellets_reached_for"]) == "2"


def test_every_outcome_is_a_percentage_of_twenty_from_both_streams():
    _, s, _ = _session(manual_displaced=3, manual_retrieved=5)
    video = [str(s["percent_%s_video" % o]) for o in ("retrieved", "displaced", "contacted", "missed")]
    hand = [str(s["percent_%s_hand_scored" % o]) for o in ("retrieved", "displaced", "contacted", "missed")]
    assert video == ["5", "5", "10", "90"]
    assert hand == ["25", "15", "40", "60"]


def test_a_stream_with_no_score_gives_NA_not_zero():
    _, s, _ = _session()
    assert s["percent_retrieved_hand_scored"] == ar.MISSING
    assert s["percent_missed_hand_scored"] == ar.MISSING


def test_the_reach_file_carries_the_same_numbers_and_the_dictionary_says_what_they_are():
    reaches, s, rows = _session(manual_displaced=3, manual_retrieved=5)
    for column in ("pellets_presented", "pellets_reached_for", "percent_missed_hand_scored"):
        assert set(map(str, reaches[column])) == {str(s[column])}, column
    assert "20" in rows["pellets_presented"]["Description"]
    assert "not a denominator" in rows["pellets_reached_for"]["Description"]
    assert "20 pellets presented" in rows["percent_contacted_video"]["Description"]
