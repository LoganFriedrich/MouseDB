"""How many pellets a tray presented is measured, and a count other than 20 is an error.

WHY (2026-10-16): the exports counted pellets from reach rows, so a pellet
nobody reached for was invisible, and a displaced or retrieved pellet whose
reach went undetected was never counted. The per-pellet table records every
pellet the pipeline cut each video into; the hand scores record every pellet
scored from the tray. Each should be 20, and the exports show both.
"""
import pandas as pd

from mousedb.exporters import export_exclusions as ee
from mousedb.exporters import odc_reaches as ox

SUBJECT = "PROJA_01_01"


def _reaches(video, date, segments):
    return [{"id": i, "subject_id": SUBJECT, "video_name": video, "session_date": date,
             "tray_type": "P", "run_number": 1, "segment_num": s, "reach_id": i + 1,
             "reach_num": 1, "segment_outcome": "retrieved" if s == 2 else "untouched",
             "attention_score": 0.5, "test_phase": "Pillar", "phase_group": "Baseline",
             "extended_features": "{}"} for i, s in enumerate(segments)]


def _pellets(video, n, displaced=()):
    return pd.DataFrame([{"video_name": video, "segment_num": s, "subject_id": SUBJECT,
                          "outcome": ("displaced_sa" if s in displaced
                                      else "retrieved" if s == 2 else "untouched")}
                         for s in range(1, n + 1)])


def test_the_complete_record_counts_every_pellet_of_the_video():
    video = "20250710_PROJA0101_P1"
    reach = pd.DataFrame(_reaches(video, "2025-07-10", [2, 2, 5]))
    subjects = pd.DataFrame([{"subject_id": SUBJECT, "sex": "F", "date_of_birth": None}])
    hand = pd.DataFrame([{"subject_id": SUBJECT, "session_date": "2025-07-10", "tray_type": "P",
                          "tray_number": 1, "pellet_number": p, "score": 0} for p in range(1, 20)])
    # pellet 9 was knocked off with no reach detected: only the per-pellet table knows
    df, _ = ox.build_cohort("PROJA_01", reach, subjects, None, hand, lambda s: {}, "",
                            video_pellets=_pellets(video, 20, displaced=(9,)))
    row = df.iloc[0]
    assert row["Video_Pellets"] == 20
    assert (row["Video_Displaced"], row["Video_Retrieved"], row["Video_Contacted"]) == (1, 1, 2)
    assert row["Manual_Pellets"] == 19


def test_without_the_per_pellet_table_the_reach_rows_are_the_fallback():
    video = "20250710_PROJA0101_P1"
    reach = pd.DataFrame(_reaches(video, "2025-07-10", [2, 5]))
    subjects = pd.DataFrame([{"subject_id": SUBJECT, "sex": "F", "date_of_birth": None}])
    df, _ = ox.build_cohort("PROJA_01", reach, subjects, None, None, lambda s: {}, "")
    assert pd.isna(df.iloc[0]["Video_Pellets"]) and df.iloc[0]["Video_Retrieved"] == 1


def _snapshot(tmp_path, pellet_counts, hand_counts):
    rows, vp, ps = [], [], []
    for i, (video, n) in enumerate(pellet_counts.items()):
        rows += _reaches(video, "2025-07-1%d" % i, [2])
        vp.append(_pellets(video, n))
        for p in range(1, hand_counts[video] + 1):
            ps.append({"subject_id": SUBJECT, "session_date": "2025-07-1%d" % i, "tray_type": "P",
                       "tray_number": 1, "pellet_number": p, "score": 0})
    pd.DataFrame(rows).to_parquet(tmp_path / "reach_data.parquet", index=False)
    pd.concat(vp).to_parquet(tmp_path / "video_pellets.parquet", index=False)
    pd.DataFrame(ps).to_parquet(tmp_path / "pellet_scores.parquet", index=False)
    return tmp_path


def test_a_video_not_cut_into_twenty_is_left_out_and_short_hand_scores_are_blanked(tmp_path):
    good, short_video, short_hand = ("20250710_PROJA0101_P1", "20250711_PROJA0101_P1",
                                     "20250712_PROJA0101_P1")
    snap = _snapshot(tmp_path, {good: 20, short_video: 19, short_hand: 20},
                     {good: 20, short_video: 20, short_hand: 19})
    ex = ee.compute(snap)
    assert good not in ex.drop_videos and good not in ex.blank_fields
    assert "found 19 pellets" in ex.drop_videos[short_video]
    blanked = ex.blank_fields[short_hand]
    assert set(blanked) == set(ee.MANUAL_FIELDS)
    assert "19 pellets hand scored" in blanked["manual_retrieved"]


def test_blanking_the_hand_scores_covers_every_hand_scored_column():
    for column in ("pellets_hand_scored", "percent_retrieved_hand_scored",
                   "percent_displaced_hand_scored", "percent_contacted_hand_scored",
                   "percent_missed_hand_scored"):
        assert column in ee.MANUAL_FIELDS, column


def test_an_old_snapshot_says_the_video_check_did_not_run(tmp_path):
    snap = _snapshot(tmp_path, {"20250710_PROJA0101_P1": 20}, {"20250710_PROJA0101_P1": 20})
    (snap / "video_pellets.parquet").unlink()
    assert any("not run" in n and "per-pellet" in n for n in ee.compute(snap).notes)
