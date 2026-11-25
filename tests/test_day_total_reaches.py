"""Reaches per animal per testing day, across every tray of that day.

An earlier reach program's "Total Swipes" counted the reaches one animal made in
one testing session -- one date, every tray of it. The export's own count
(called Total_Swipes_AI in older files) counts the reaches of ONE VIDEO (one
tray), so carrying the earlier name on it would invite reading a per-tray count as
the per-day one. So:

  Video_Reaches_AI            reaches in this video (one tray); the old
                              Total_Swipes_AI under a name that says so
  Day_Total_Reaches_AI        reaches by this animal on this date, all trays
  Day_Total_Reaches_Complete  yes when every tray the hand-score sheet records
                              for that animal and date is filed; no otherwise,
                              and then the total is left empty -- a partial
                              count is never reported as the day's count
"""
import pandas as pd

from mousedb.exporters import analysis_reaches as ar
from mousedb.exporters import analysis_sessions
from mousedb.exporters import odc_reaches as ox
from mousedb.exporters import reach_summary

SID = "PROJA_01_01"


def _reach(trays):
    """trays: [(video, date, tray_type, run, n_reaches)]"""
    rows = []
    for vid, date, tt, run, n in trays:
        for k in range(1, n + 1):
            rows.append({"id": len(rows), "subject_id": SID, "video_name": vid,
                         "session_date": date, "tray_type": tt, "run_number": run,
                         "segment_num": 1, "reach_id": len(rows), "reach_num": k,
                         "segment_outcome": "untouched", "attention_score": 0.5,
                         "test_phase": "Pillar", "phase_group": "Baseline",
                         "extended_features": "{}"})
    return pd.DataFrame(rows)


def _scores(trays):
    """trays: [(date, tray_type, tray_number)] -- 20 hand scores each."""
    return pd.DataFrame([{"subject_id": SID, "session_date": d, "tray_type": tt,
                          "tray_number": n, "pellet_number": p, "score": 0}
                         for d, tt, n in trays for p in range(1, 21)])


def _build(reach, pellets, video_pellets=None):
    subjects = pd.DataFrame([{"subject_id": SID, "sex": "F", "date_of_birth": None}])
    return ox.build_cohort("PROJA_01", reach, subjects, None, pellets,
                           lambda sid: {}, "lab-x", video_pellets=video_pellets)


DAY = "2025-07-10"
FOUR = [("20250710_PROJA0101_P%d" % i, DAY, "P", i, i) for i in range(1, 5)]   # 1+2+3+4 = 10


def test_the_day_total_counts_every_tray_of_the_day():
    df, rows = _build(_reach(FOUR), _scores([(DAY, "P", i) for i in range(1, 5)]))
    assert (df["Day_Total_Reaches_AI"] == 10).all()
    assert (df["Day_Total_Reaches_Complete"] == "yes").all()
    # The per-video count is still there, under a name that says it is per video.
    per_video = df.groupby("video_name")["Video_Reaches_AI"].first()
    assert per_video["20250710_PROJA0101_P3"] == 3
    assert "Total_Swipes_AI" not in df.columns
    named = {r["VariableName"] for r in rows}
    assert {"Video_Reaches_AI", "Day_Total_Reaches_AI", "Day_Total_Reaches_Complete"} <= named


def test_a_day_with_a_tray_not_filed_is_marked_incomplete_and_left_empty():
    # The sheet scored four trays; only three are filed.
    df, _ = _build(_reach(FOUR[:3]), _scores([(DAY, "P", i) for i in range(1, 5)]))
    assert (df["Day_Total_Reaches_Complete"] == "no").all()
    assert df["Day_Total_Reaches_AI"].isna().all()


def test_a_filed_tray_with_no_reaches_still_counts_as_filed():
    """A tray the animal did not reach at is complete at 0, not missing."""
    vp = pd.DataFrame([{"video_name": "20250710_PROJA0101_P4", "segment_num": s,
                        "subject_id": SID, "session_date": DAY, "tray_type": "P",
                        "run_number": 4, "outcome": "untouched"} for s in range(1, 21)])
    df, _ = _build(_reach(FOUR[:3]), _scores([(DAY, "P", i) for i in range(1, 5)]), vp)
    assert (df["Day_Total_Reaches_Complete"] == "yes").all()
    assert (df["Day_Total_Reaches_AI"] == 6).all()


def test_a_day_without_hand_scores_is_not_assumed_complete():
    df, _ = _build(_reach(FOUR), None)
    assert (df["Day_Total_Reaches_Complete"] == "no").all()
    assert df["Day_Total_Reaches_AI"].isna().all()


def test_days_are_counted_separately():
    other = [("20250711_PROJA0101_P1", "2025-07-11", "P", 1, 5)]
    df, _ = _build(_reach(FOUR + other),
                   _scores([(DAY, "P", i) for i in range(1, 5)] + [("2025-07-11", "P", 1)]))
    by_day = df.groupby("Test_Date")["Day_Total_Reaches_AI"].first()
    assert by_day[DAY] == 10 and by_day["2025-07-11"] == 5


def _analysis_frame():
    df, _ = _build(_reach(FOUR), _scores([(DAY, "P", i) for i in range(1, 5)]))
    return df


def test_the_analysis_documents_carry_both_counts_under_their_names():
    out, rows = ar.build(_analysis_frame())
    assert "total_swipes_ai" not in out.columns
    assert "reaches_in_session" not in out.columns
    assert (out["day_total_reaches"] == 10).all()
    assert (out["day_total_reaches_complete"] == "yes").all()
    assert out.groupby("video_name")["reaches_in_video"].first()["20250710_PROJA0101_P2"] == 2
    sessions, _ = analysis_sessions.build(out, rows)
    assert "reaches_in_video" in sessions.columns
    assert (sessions["day_total_reaches"] == 10).all()


def test_the_shareable_table_names_the_per_video_count_per_video():
    summary, _ = reach_summary.build(_analysis_frame())
    assert "reaches_in_session" not in summary.columns
    assert "reaches_in_video" in summary.columns
    assert "day_total_reaches" in summary.columns


def test_a_day_with_a_tray_left_out_of_the_analysis_file_is_incomplete_there():
    """The analysis file leaves out entries known to be wrong. A day total that
    still counted a left-out tray's reaches would carry the doubtful reaches
    back in, so that day's total is emptied and marked incomplete there."""
    out, _ = ar.build(_analysis_frame())
    kept = out[out["video_name"] != "20250710_PROJA0101_P4"].copy()
    voided = ar.void_day_totals_after_exclusion(out, kept)
    assert (voided["day_total_reaches_complete"] == "no").all()
    assert (voided["day_total_reaches"] == ar.MISSING).all()


def test_a_per_pellet_table_without_tray_columns_does_not_stop_the_export():
    """An older per-pellet table carries only the video and pellet: it cannot
    show which tray is filed, so the day is judged from the reach rows alone."""
    vp = pd.DataFrame([{"video_name": "20250710_PROJA0101_P4", "segment_num": 1,
                        "outcome": "untouched"}])
    df, _ = _build(_reach(FOUR[:3]), _scores([(DAY, "P", i) for i in range(1, 5)]), vp)
    assert (df["Day_Total_Reaches_Complete"] == "no").all()


def test_the_written_analysis_files_void_a_day_that_lost_a_tray(tmp_path, monkeypatch):
    """End to end through write_cohort: the exclusion pass drops one tray of
    the day, and the analysis and sessions files then read no / NA for that
    day, with the reason in their dictionaries."""
    from mousedb.exporters import export_exclusions

    def drop_one_tray(frame, ex, cohort_id="", missing_word="NA"):
        return frame[frame["video_name"] != "20250710_PROJA0101_P4"].copy(), []

    monkeypatch.setattr(export_exclusions, "apply", drop_one_tray)
    df, rows = _build(_reach(FOUR), _scores([(DAY, "P", i) for i in range(1, 5)]))
    assert (df["Day_Total_Reaches_AI"] == 10).all()
    entry, label = ox.write_cohort(tmp_path, "PROJA_01", df, rows, exclusions=object(),
                                   analysis_only=True)
    reaches = pd.read_csv(next(tmp_path.rglob("reaches_*_analysis.csv")))
    sessions = pd.read_csv(next(tmp_path.rglob("sessions_*_analysis.csv")))
    # Published names (exporters.levels): every day column is voided.
    assert reaches["day_reaches_ai"].isna().all()
    assert reaches["day_retrieved_ai"].isna().all() and reaches["day_retrieved_hand"].isna().all()
    assert (reaches["day_complete"] == "no").all()
    assert sessions["day_reaches_ai"].isna().all()
    assert (sessions["day_complete"] == "no").all()
    for doc in ("_analysis_dictionary", "_sessions_dictionary"):
        by = {r["VariableName"]: r for r in entry[doc]}
        assert "left out of this file" in by["day_complete"]["Comments"]
