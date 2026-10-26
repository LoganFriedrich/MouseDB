"""Easy and flat trays: every question about a pellet reads "Not applicable".

WHY (2026-10-26): on those training trays the pellets are not tracked, no outcome
is computed, and the video is not cut into one stretch per pellet. The exports
used to fill those rows by the general rules and so answered the pellet questions
wrongly, four different ways: "Undetermined" (reads as a pellet waiting for a
person), "pellet still available", contact group "missed", and counts such as
"337 reaches at this pellet". A pillar row beside them must keep its real answer.
"""
import pandas as pd

from mousedb.exporters import analysis_sessions, missing
from mousedb.exporters import odc_reaches as ox

SUBJECT = "PROJA_01_01"
FLAT = "20250710_PROJA0101_F1"
PILLAR = "20250711_PROJA0101_P1"


def _reach_rows():
    common = {"subject_id": SUBJECT, "run_number": 1, "attention_score": 50.0,
              "test_phase": "Rehab", "phase_group": "Rehab", "extended_features": "{}"}
    flat = [dict(common, id=i, video_name=FLAT, session_date="2025-07-10", tray_type="F",
                 segment_num=1, reach_id=i + 1, reach_num=i + 1, n_reaches_in_segment=3,
                 is_first_reach=int(i == 0), is_last_reach=int(i == 2),
                 segment_outcome=None, causal_reach=0, contact_group="missed",
                 segment_contact_group="missed")
            for i in range(3)]
    pillar = [dict(common, id=10 + i, video_name=PILLAR, session_date="2025-07-11",
                   tray_type="P", segment_num=1, reach_id=i + 1, reach_num=i + 1,
                   n_reaches_in_segment=2, segment_outcome="retrieved",
                   is_first_reach=int(i == 0), is_last_reach=int(i == 1),
                   causal_reach=1 if i == 1 else 0, contact_group="contacted",
                   segment_contact_group="contacted")
              for i in range(2)]
    return pd.DataFrame(flat + pillar)


def _written(tmp_path):
    subjects = pd.DataFrame([{"subject_id": SUBJECT, "sex": "F", "date_of_birth": None}])
    df, rows = ox.build_cohort("PROJA_01", _reach_rows(), subjects, None, None,
                               lambda s: {}, "")
    out = tmp_path / "current"
    out.mkdir()
    entry, label = ox.write_cohort(out, "PROJA_01", df, rows)
    read = lambda name: pd.read_csv(out / name, dtype=str, keep_default_na=False)
    return entry, {
        "complete": read("ODC_reaches_%s.csv" % label),
        "summary": read("reaches_%s_summary.csv" % label),
        "analysis": read("reaches_%s_analysis.csv" % label),
    }


def test_the_shareable_table_says_not_applicable_on_a_flat_tray(tmp_path):
    _, f = _written(tmp_path)
    s = f["summary"]
    flat, pillar = s[s["session_video"] == FLAT], s[s["session_video"] == PILLAR]
    for column in ("pellet_result", "reach_result", "reach_decided_the_pellet",
                   "pellet_number", "reaches_at_this_pellet"):
        assert (flat[column] == missing.NOT_APPLICABLE).all(), column
    assert missing.UNDETERMINED not in set(flat["pellet_result"])
    assert list(pillar["reach_result"]) == ["Missed", "Retrieved"]
    assert list(pillar["reach_decided_the_pellet"]) == ["No", "Yes"]


def test_the_analysis_table_uses_words_for_words_and_na_for_numbers(tmp_path):
    _, f = _written(tmp_path)
    a = f["analysis"]
    flat, pillar = a[a["video_name"] == FLAT], a[a["video_name"] == PILLAR]
    for column in ("reach_outcome", "pellet_outcome", "pellet_state_at_this_reach"):
        assert (flat[column] == missing.NOT_APPLICABLE).all(), column
    for column in ("pellet_number", "reaches_at_this_pellet", "is_first_reach_at_pellet",
                   "is_last_reach_at_pellet", "pellets_reached_for", "reaches_that_decided_a_pellet",
                   "reaches_after_the_pellet_was_gone"):
        assert (flat[column] == "NA").all(), column
    assert list(pillar["pellet_state_at_this_reach"]) == [
        "pellet still available", "this reach decided the pellet"]
    assert set(pillar["reaches_that_decided_a_pellet"]) == {"1"}


def test_the_complete_record_overrides_what_the_database_claims(tmp_path):
    _, f = _written(tmp_path)
    c = f["complete"]
    flat, pillar = c[c["video_name"] == FLAT], c[c["video_name"] == PILLAR]
    for column in ("segment_outcome", "causal_reach", "contact_group",
                   "segment_contact_group", "Video_Pellets"):
        assert (flat[column] == missing.NOT_APPLICABLE).all(), column
    assert set(pillar["contact_group"]) == {"contacted"}
    assert set(pillar["segment_outcome"]) == {"retrieved"}


def test_the_sessions_table_counts_no_pellets_on_a_flat_tray(tmp_path):
    _, f = _written(tmp_path)
    sessions, _ = analysis_sessions.build(f["analysis"], [])
    flat = sessions[sessions["video_name"] == FLAT].iloc[0]
    for column in analysis_sessions.PELLET_COUNTS:
        assert flat[column] == "NA", column
    pillar = sessions[sessions["video_name"] == PILLAR].iloc[0]
    assert pillar["pellets_reached_for"] == 1


def test_the_dictionaries_name_the_word_and_say_why():
    from mousedb.exporters import analysis_reaches as ar, reach_summary as rs
    rows = {r["VariableName"]: r for r in rs._dictionary_rows([], {})}
    for name in ("pellet_result", "reach_result", "reach_decided_the_pellet"):
        assert missing.NOT_APPLICABLE in rows[name]["PermittedValues"], name
        assert "easy or flat" in rows[name]["PVDescribed"], name
    assert missing.NOT_APPLICABLE in ar.PELLET_STATE_VALUES
    assert "easy or flat" in ar.PELLET_STATE_DESCRIBED
