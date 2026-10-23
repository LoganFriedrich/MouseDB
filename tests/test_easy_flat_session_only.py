"""Easy and Flat sessions: reaches and attention import; pellet counts stay blank.

WHY (lab decision, 2026-10-23): on Easy and Flat trays pellets are presented
but not tracked -- the pipeline does not cut the video per pellet and computes
no outcome -- while the session's reaches and attention are wanted. A pellet
row would claim "1 pellet presented, 0 retrieved" for a session whose pellets
nobody counted, and the "pellets presented must be 20" check would then drop
the session from the exports altogether.
"""
import pandas as pd

from mousedb.exporters import odc_reaches as ox
from mousedb.import_reaches import pellet_rows

SUBJECT = "PROJA_01_01"
FEATURES = {"segments": [{"segment_num": 1, "outcome": None, "reaches": [{}, {}]}]}


def test_easy_or_flat_writes_no_pellet_rows():
    assert pellet_rows(FEATURES, "20250710_PROJA0101_F1", SUBJECT, "now", "f.json") == []
    assert pellet_rows(FEATURES, "20250710_PROJA0101_E2", SUBJECT, "now", "f.json") == []
    assert len(pellet_rows(FEATURES, "20250710_PROJA0101_P1", SUBJECT, "now", "f.json")) == 1


def test_easy_or_flat_exports_reaches_and_attention_with_blank_pellet_counts():
    rows = [{"id": i, "subject_id": SUBJECT, "video_name": "20250710_PROJA0101_F1",
             "session_date": "2025-07-10", "tray_type": "F", "run_number": 1,
             "segment_num": 1, "reach_id": i + 1, "reach_num": i + 1,
             "segment_outcome": None, "attention_score": 62.5, "test_phase": "Rehab Flat",
             "phase_group": "Rehab", "extended_features": "{}"} for i in range(3)]
    subjects = pd.DataFrame([{"subject_id": SUBJECT, "sex": "F", "date_of_birth": None}])
    df, _ = ox.build_cohort("PROJA_01", pd.DataFrame(rows), subjects, None, None,
                            lambda s: {}, "")
    row = df.iloc[0]
    assert row["Total_Swipes_AI"] == 3
    assert row["Attention_AI"] == 62.5
    for c in ("Video_Pellets", "Video_Displaced", "Video_Retrieved", "Video_Contacted"):
        assert pd.isna(row[c]), c
