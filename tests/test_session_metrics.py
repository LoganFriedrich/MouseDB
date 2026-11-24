"""The session measurements of the sessions document (exporters.session_metrics).

Each is checked on a small hand-made session whose answer can be worked out on
paper, plus the two rules about absent values: a tray with no reach has every
measure computed from reaches NA (and the rest filled, total reaches 0), and an
easy or flat tray has the algorithm's pellet measures NA (and the hand-scored
one filled). The per-reach file's own call must not gain any of them.
"""
import math

import pandas as pd
import pytest

from mousedb.exporters import analysis_sessions as asx
from mousedb.exporters import session_metrics as sm
from mousedb.exporters.analysis_reaches import (STATE_ALREADY_GONE, STATE_AVAILABLE,
                                                STATE_DECIDED_BY_THIS)

SID = "PROJA_01_01"
DAY = "2025-07-10"
P1 = "20250710_PROJA0101_P1"
P2 = "20250710_PROJA0101_P2"      # a tray with no reach
E1 = "20250710_PROJA0101_E1"
DUR = "reach_duration_frames"
ROT = "reach_paw_rotation_total_deg"


def _reach(video, tray, pellet, first, outcome, state, duration, rotation=None):
    return {"subject_id": SID, "video_name": video, "test_date": DAY, "tray_type": tray,
            "pellet_number": pellet, "is_first_reach_at_pellet": first,
            "reach_outcome": outcome, "pellet_state_at_this_reach": state,
            "duration_frames": duration, "paw_rotation_total_deg": rotation,
            "start_frame": 100, "tracking_confidence_mean": 0.9,
            "body_weight_g": 22.5, "body_weight_percent_of_free_feeding": 88.0,
            "video_retrieved": 1 if tray == "P" else "NA",
            "video_displaced": 1 if tray == "P" else "NA",
            "manual_retrieved": 2, "manual_displaced": 2}


def _frame():
    rows = [
        # pellet 1: the first reach retrieved it; a swipe after it was gone
        _reach(P1, "P", 1, 1, "Retrieved", STATE_DECIDED_BY_THIS, 10, 30.0),
        _reach(P1, "P", 1, 0, "Missed", STATE_ALREADY_GONE, 100, None),
        # pellet 2: the first reach missed, the second displaced it
        _reach(P1, "P", 2, 1, "Missed", STATE_AVAILABLE, 20, 10.0),
        _reach(P1, "P", 2, 0, "Displaced into the scoring area", STATE_DECIDED_BY_THIS, 30, None),
        _reach(E1, "E", "NA", "NA", "Not applicable", "Not applicable", 40, 5.0),
        _reach(E1, "E", "NA", "NA", "Not applicable", "Not applicable", 60, 5.0),
    ]
    no_reach = {k: None for k in rows[0]}
    no_reach.update({"subject_id": SID, "video_name": P2, "test_date": DAY, "tray_type": "P",
                     "body_weight_g": 22.5, "body_weight_percent_of_free_feeding": 88.0,
                     "video_retrieved": 0, "video_displaced": 0,
                     "manual_retrieved": 0, "manual_displaced": 0})
    return pd.DataFrame(rows + [no_reach])


def _sources(recording=True):
    vs = pd.DataFrame([{"video_name": v, "subject_id": SID, "total_frames": 36000,
                        "frame_rate": 60.0} for v in (P1, P2, E1)])
    vp = pd.DataFrame(
        [{"video_name": P1, "subject_id": SID, "segment_num": s,
          "outcome": {1: "retrieved", 2: "displaced_sa", 3: "displaced_outside",
                      4: "abnormal"}.get(s, "untouched"),
          "n_reaches": {1: 2, 2: 2}.get(s, 0),
          "time_to_first_reach_sec": {1: 1.0, 2: 3.0}.get(s),
          "mean_inter_reach_interval_sec": {1: 2.0, 2: 4.0}.get(s),
          "pellet_position_idealness": 0.5 if s <= 10 else 0.3} for s in range(1, 21)]
        + [{"video_name": P2, "subject_id": SID, "segment_num": s, "outcome": "untouched",
            "n_reaches": 0, "time_to_first_reach_sec": None,
            "mean_inter_reach_interval_sec": None, "pellet_position_idealness": 0.4}
           for s in range(1, 21)])
    rec = (pd.DataFrame([{"subject_id": SID, "session_date": DAY, "tray_type": "P",
                          "tray_number": 3, "status": "recording_failed",
                          "reason": "4 frames", "collage_name": "x.mkv"}])
           if recording else None)
    return sm.prepare("PROJA_01", vs, vp, rec)


@pytest.fixture(scope="module")
def built():
    out, rows = asx.build(_frame(), [], session_only={P2}, metrics=True, sources=_sources())
    return out.set_index("video_name"), rows


def _v(built, video, col):
    return built[0].loc[video, col]


def test_every_new_column_has_exactly_one_dictionary_row(built):
    out, rows = built
    names = [r["VariableName"] for r in rows]
    assert sorted(names) == sorted(["video_name"] + list(out.columns))
    for c in sm.COLUMNS + ["body_weight_g", "body_weight_percent_of_free_feeding"]:
        assert names.count(c) == 1


def test_absent_values_are_the_text_na_never_blank(built):
    out, _ = built
    assert not out.isna().any().any()
    assert not (out.astype(str) == "").any().any()


def test_1_weight_on_the_day_also_on_a_tray_with_no_reach(built):
    for v in (P1, P2, E1):
        assert _v(built, v, "body_weight_g") == 22.5
        assert _v(built, v, "body_weight_percent_of_free_feeding") == 88


def test_2_day_pillar_reaches(built):
    assert _v(built, P1, "day_pillar_reaches_ai") == 4
    assert _v(built, P2, "day_pillar_reaches_ai") == 4
    assert _v(built, E1, "day_pillar_reaches_ai") == "NA"


def test_3_timing(built):
    assert _v(built, P1, "session_duration_sec") == 600
    assert _v(built, P1, "session_reaches_per_minute") == 0.4      # 4 reaches / 10 min
    assert _v(built, P1, "session_median_latency_to_first_reach_sec") == 2   # median(1, 3)
    assert _v(built, P1, "session_mean_inter_reach_interval_sec") == 3       # (2*1 + 4*1)/2
    # no reach: length filled, rate a real 0, latency and gap NA
    assert _v(built, P2, "session_duration_sec") == 600
    assert _v(built, P2, "session_reaches_per_minute") == 0
    assert _v(built, P2, "session_median_latency_to_first_reach_sec") == "NA"
    assert _v(built, P2, "session_mean_inter_reach_interval_sec") == "NA"
    # easy tray: length and rate filled, the pellet timing NA
    assert _v(built, E1, "session_reaches_per_minute") == 0.2
    assert _v(built, E1, "session_median_latency_to_first_reach_sec") == "NA"
    assert _v(built, E1, "session_mean_inter_reach_interval_sec") == "NA"


def test_the_gap_is_the_mean_over_every_gap_not_over_pellets():
    g = pd.DataFrame({"mean_inter_reach_interval_sec": [1.0, 4.0], "n_reaches": [11, 2]})
    # ten gaps of 1 s and one of 4 s: 14 / 11, not (1 + 4) / 2
    assert sm.pellet_timing(g)["gap"] == pytest.approx(14 / 11)


def test_4_success_given_contact(built):
    assert _v(built, P1, "session_percent_retrieved_of_contacted_ai") == 50
    assert _v(built, P1, "session_percent_retrieved_of_contacted_hand") == 50
    # nothing contacted: no answer
    assert _v(built, P2, "session_percent_retrieved_of_contacted_ai") == "NA"
    assert _v(built, P2, "session_percent_retrieved_of_contacted_hand") == "NA"
    # easy tray: the algorithm's NA, the hand score's filled
    assert _v(built, E1, "session_percent_retrieved_of_contacted_ai") == "NA"
    assert _v(built, E1, "session_percent_retrieved_of_contacted_hand") == 50


def test_5_first_attempt_success(built):
    assert _v(built, P1, "session_first_attempt_retrieved_ai") == 1
    assert _v(built, P1, "session_percent_first_attempt_success_ai") == 50   # 1 of 2 reached for
    for v in (P2, E1):
        assert _v(built, v, "session_first_attempt_retrieved_ai") == "NA"
        assert _v(built, v, "session_percent_first_attempt_success_ai") == "NA"


def test_6_which_columns_are_kinematic_measures():
    cols = ["duration_frames", "paw_rotation_total_deg", "start_frame", "apex_frame",
            "tracking_confidence_mean", "rhleft_confidence_max", "anchor_end_source",
            "paw_leading_point", "kinematics_version", "subject_id", "video_name",
            "pellet_position_idealness", "attention_at_this_reach", "reach_outcome",
            "tray_contact_duration_frames", "swipe_length"]
    got = dict(sm.kinematic_measures(cols))
    assert got == {"duration_frames": "reach_duration_frames",
                   "paw_rotation_total_deg": "reach_paw_rotation_total_deg",
                   "tray_contact_duration_frames": "reach_tray_contact_duration_frames",
                   "swipe_length": "reach_swipe_length"}


def test_6_kinematics_median_mean_sem_n_over_every_reach(built):
    col = lambda s: sm.kinematic_column(s, DUR)
    # every reach of the session, also the swipe after the pellet was gone: 10, 100, 20, 30
    assert _v(built, P1, col("n")) == 4
    assert _v(built, P1, col("median")) == 25
    assert _v(built, P1, col("mean")) == 40
    sd = pd.Series([10, 100, 20, 30]).std(ddof=1)
    assert _v(built, P1, col("sem")) == pytest.approx(sd / 2, rel=1e-5)
    rot = lambda s: sm.kinematic_column(s, ROT)
    assert _v(built, P1, rot("n")) == 2               # only reaches with a value
    # easy tray: every reach counts
    assert _v(built, E1, col("n")) == 2 and _v(built, E1, col("mean")) == 50
    assert _v(built, E1, rot("sem")) == 0
    # frame numbers and tracking confidence are not summarised
    assert not [c for c in built[0].columns if c.startswith("session_") and
                ("start_frame" in c or "confidence" in c)]


def test_6_sem_is_na_with_one_value_and_n_is_0_without_values():
    assert sm.describe(pd.Series([3.0]))["sem"] is None
    assert sm.describe(pd.Series([], dtype=float)) == {"median": None, "mean": None,
                                                       "sem": None, "n": 0}
    frame = _frame()
    frame.loc[frame["video_name"] == P1, "paw_rotation_total_deg"] = None
    out, _ = asx.build(frame, [], session_only={P2}, metrics=True, sources=_sources())
    out = out.set_index("video_name")
    assert out.loc[P1, sm.kinematic_column("n", ROT)] == 0
    assert out.loc[P1, sm.kinematic_column("mean", ROT)] == "NA"
    one = _frame()
    one["duration_frames"] = one["duration_frames"].astype(object)
    one.loc[one["video_name"] == E1, "duration_frames"] = [40, None]
    out, _ = asx.build(one, [], session_only={P2}, metrics=True, sources=_sources())
    out = out.set_index("video_name")
    assert out.loc[E1, sm.kinematic_column("n", DUR)] == 1
    assert out.loc[E1, sm.kinematic_column("sem", DUR)] == "NA"


def test_zero_reach_tray_has_every_reach_derived_column_na_and_0_reaches(built):
    assert _v(built, P2, "reaches_in_video") == 0
    kin = [c for c in built[0].columns if c.startswith(("session_median_reach_",
                                                        "session_mean_reach_",
                                                        "session_sem_reach_",
                                                        "session_n_reach_"))]
    assert kin
    for c in kin + list(sm.FROM_REACHES):
        assert _v(built, P2, c) == "NA", c
    # everything else filled
    for c in ("session_duration_sec", "session_displaced_in_scoring_area_ai",
              "session_mean_pellet_position_idealness", "day_recording_status",
              "body_weight_g", "day_pillar_reaches_ai"):
        assert _v(built, P2, c) != "NA", c


def test_easy_flat_tray_has_the_algorithm_pellet_measures_na(built):
    for c in ("session_displaced_in_scoring_area_ai", "session_displaced_outside_ai",
              "session_moved_not_by_reach_ai", "session_mean_pellet_position_idealness",
              "session_percent_retrieved_of_contacted_ai",
              "session_first_attempt_retrieved_ai", "day_pillar_reaches_ai"):
        assert _v(built, E1, c) == "NA", c
    for c in ("session_duration_sec", "session_reaches_per_minute", "day_recording_status",
              "session_percent_retrieved_of_contacted_hand", sm.kinematic_column("mean", DUR)):
        assert _v(built, E1, c) != "NA", c


def test_7_8_9_pellet_counts_and_placement(built):
    assert _v(built, P1, "session_displaced_in_scoring_area_ai") == 1
    assert _v(built, P1, "session_displaced_outside_ai") == 1
    assert _v(built, P1, "session_moved_not_by_reach_ai") == 1
    assert _v(built, P1, "session_mean_pellet_position_idealness") == 0.4
    assert _v(built, P2, "session_displaced_in_scoring_area_ai") == 0
    assert _v(built, P2, "session_mean_pellet_position_idealness") == 0.4


def test_10_recording_status_of_the_day(built):
    for v in (P1, P2, E1):
        assert _v(built, v, "day_recording_status") == "recording_failed"
        assert _v(built, v, "day_recording_issue_reason") == "tray P3: 4 frames (recording x.mkv)"
    empty = pd.DataFrame(columns=["subject_id", "session_date", "status"])
    out, _ = asx.build(_frame(), [], session_only={P2}, metrics=True,
                       sources=sm.prepare("PROJA_01", recording_status=empty))
    assert set(out["day_recording_status"]) == {"no_issue_recorded"}
    assert set(out["day_recording_issue_reason"]) == {"NA"}
    # no table in the snapshot at all: unknown, never "no issue"
    out, _ = asx.build(_frame(), [], session_only={P2}, metrics=True,
                       sources=_sources(recording=False))
    assert set(out["day_recording_status"]) == {"NA"}


def test_without_sources_the_reach_computed_measures_are_still_filled():
    out, _ = asx.build(_frame(), [], session_only={P2}, metrics=True)
    out = out.set_index("video_name")
    assert out.loc[P1, "session_first_attempt_retrieved_ai"] == 1
    assert out.loc[P1, "session_duration_sec"] == "NA"
    assert out.loc[P1, sm.kinematic_column("n", DUR)] == 4


def test_the_per_reach_files_own_call_gains_nothing():
    """analysis_reaches carries session totals onto every reach through build()
    without ``metrics``: none of the session measurements may appear there."""
    out, _ = asx.build(_frame(), [], session_only={P2})
    assert not set(sm.COLUMNS) & set(out.columns)
    assert not [c for c in out.columns if c.startswith("session_n_reach_")]


# ------------------------------------------------------------------ through the export

def test_the_export_writes_them_with_the_weight_on_a_tray_with_no_reach(tmp_path):
    """build_cohort + write_cohort, as the export calls them: the session
    measurements are in the sessions file, written NA where absent, every column
    has one dictionary row, the day's weight is on the tray with no reach too,
    and the per-reach analysis file has none of them."""
    import tests.test_trays_without_reaches_have_session_rows as base
    from mousedb.exporters import odc_reaches as ox
    weights = pd.DataFrame([{"subject_id": base.SID, "date": base.DAY,
                             "weight_grams": 21.0, "weight_percent": 90.0}])
    reach = ox.attach_weights(base._reach_rows(), weights)
    vs = base._video_sessions()
    vs["total_frames"], vs["frame_rate"] = 1200, 60.0
    subjects = pd.DataFrame([{"subject_id": base.SID, "sex": "F", "date_of_birth": None}])
    df, rows = ox.build_cohort("PROJA_01", reach, subjects, None, base._hand_scores(),
                               lambda s: {}, "lab-x", video_pellets=base._video_pellets(),
                               video_sessions=vs, weights=weights)
    sources = sm.prepare("PROJA_01", vs, base._video_pellets(), pd.DataFrame(
        columns=["subject_id", "session_date", "status"]))
    entry, _ = ox.write_cohort(tmp_path, "PROJA_01", df, rows, session_sources=sources)
    files = {p.name: p for p in tmp_path.rglob("*.csv")}
    s = pd.read_csv(files["sessions_PROJA_01_analysis.csv"], dtype=str,
                    keep_default_na=False).set_index("video_name")
    assert (s != "").all().all()
    for v in (base.REACHED, base.NO_REACH, base.EASY):
        assert s.loc[v, "day_body_weight_g"] == "21"
        assert s.loc[v, "session_duration_sec"] == "20"
        assert s.loc[v, "day_recording_status"] == "no_issue_recorded"
    assert s.loc[base.NO_REACH, "session_reaches_ai"] == "0"
    assert s.loc[base.NO_REACH, "session_reaches_per_minute"] == "0"
    assert s.loc[base.NO_REACH, "session_first_attempt_retrieved_ai"] == "NA"
    assert s.loc[base.REACHED, "session_reaches_per_minute"] == "6"     # 2 reaches / 20 s
    # these reach rows do not say which reach came first at its pellet: unknown, NA
    assert s.loc[base.REACHED, "session_first_attempt_retrieved_ai"] == "NA"
    dictionary = [r["VariableName"] for r in entry["_sessions_dictionary"]]
    assert sorted(dictionary) == sorted(["video_name"] + list(s.columns))
    reach_file = pd.read_csv(files["reaches_PROJA_01_analysis.csv"], dtype=str)
    assert not set(sm.COLUMNS) & set(reach_file.columns)
