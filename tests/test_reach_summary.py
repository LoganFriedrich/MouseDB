"""The shareable per-reach table: does it answer correctly, and never leave a cell empty?

These pin the three things the collaborator-facing document exists to get right, all of
which are easy to break by "tidying" later:

  1. A reach that did not decide the pellet says so in words. It does NOT say the paw
     missed, because contact is never measured on such a reach.
  2. The pellet's fate rides on every reach aimed at that pellet.
  3. The pipeline's undecided words ('triaged', 'uncertain', 'abnormal_exception') are
     never presented to a reader as something an animal did.
"""
import pandas as pd
import pytest

from mousedb.exporters import missing, reach_summary as rs


def _frame(rows):
    """A minimal complete-record frame: only the columns the summary reads."""
    return pd.DataFrame(rows)


def test_only_the_deciding_reach_reports_what_happened():
    out, _ = rs.build(_frame([
        {"video_name": "V", "segment_num": 1, "reach_num": 1,
         "causal_reach": 0, "segment_outcome": "retrieved"},
        {"video_name": "V", "segment_num": 1, "reach_num": 2,
         "causal_reach": 1, "segment_outcome": "retrieved"},
    ]))
    assert list(out["reach_result"]) == [rs.NO_EFFECT, rs.RETRIEVED]
    assert list(out["reach_decided_the_pellet"]) == ["No", "Yes"]


def test_the_pellets_fate_is_carried_on_every_reach():
    out, _ = rs.build(_frame([
        {"video_name": "V", "segment_num": 1, "reach_num": n,
         "causal_reach": 1 if n == 3 else 0, "segment_outcome": "displaced_sa"}
        for n in (1, 2, 3, 4)
    ]))
    assert list(out["pellet_result"]) == [rs.DISPLACED_IN] * 4, (
        "a reach that did not move the pellet still belongs to a pellet that went "
        "somewhere -- that is the field the collaborator reads")


@pytest.mark.parametrize("word", ["triaged", "uncertain", "abnormal_exception"])
def test_the_pipelines_undecided_words_never_reach_a_reader(word):
    out, _ = rs.build(_frame([
        {"video_name": "V", "segment_num": 1, "reach_num": 1,
         "causal_reach": 1, "segment_outcome": word},
    ]))
    assert out["pellet_result"].iloc[0] == missing.UNDETERMINED
    assert out["reach_result"].iloc[0] == missing.UNDETERMINED
    assert word not in out.to_csv(index=False), (
        "'%s' means we could not decide, not something the animal did" % word)


def test_an_untouched_pellet_is_never_reported_as_an_effect():
    """A missed pellet CAN have a causal reach (the dictionary defines it as the last
    reach at a pellet nobody moved), so the deciding reach must still say it moved
    nothing."""
    out, _ = rs.build(_frame([
        {"video_name": "V", "segment_num": 1, "reach_num": 1,
         "causal_reach": 1, "segment_outcome": "untouched"},
    ]))
    assert out["reach_result"].iloc[0] == rs.NO_EFFECT
    assert out["pellet_result"].iloc[0] == rs.NEVER_TOUCHED


def test_no_cell_is_ever_empty():
    out, _ = rs.build(_frame([
        {"video_name": "V", "segment_num": 1, "reach_num": 1, "causal_reach": 0,
         "segment_outcome": "retrieved", "Days_Post_Injury": None,
         "Exclusion_reason": "", "Manual_Retrieved": None},
    ]))
    assert not (out == "").any().any()
    assert not out.isna().any().any()
    assert out["days_post_injury"].iloc[0] == missing.NOT_APPLICABLE, (
        "a session before the injury has no day count to know -- that is not the same "
        "as a measurement we failed to take")
    assert out["pellets_retrieved_hand_scored"].iloc[0] == missing.NOT_RECORDED


def test_nothing_about_who_or_what_produced_the_answer_travels():
    out, _ = rs.build(_frame([
        {"video_name": "V", "segment_num": 1, "reach_num": 1, "causal_reach": 1,
         "segment_outcome": "retrieved", "outcome_source": "human_review",
         "reviewed_by": "someuser", "algo_outcome": "untouched",
         "dlc_scorer": "DLC_model", "mousereach_version": "9.9.9",
         "processed_by": "SOME-MACHINE", "source_file": "C:/somewhere/x.json"},
    ]))
    text = out.to_csv(index=False)
    for leaked in ("someuser", "DLC_model", "9.9.9", "SOME-MACHINE", "somewhere",
                   "human_review"):
        assert leaked not in text, "%s must not leave the lab in this document" % leaked


def test_every_column_has_a_dictionary_row():
    out, rows = rs.build(_frame([
        {"video_name": "V", "segment_num": 1, "reach_num": 1, "causal_reach": 1,
         "segment_outcome": "retrieved", "SubjectID": "X_01_01", "Test_Date": "2025-01-01"},
    ]))
    documented = {r["VariableName"] for r in rows}
    assert set(out.columns) <= documented, (
        "an ODC-SCI upload is refused when a column has no dictionary entry")


def test_the_answer_columns_declare_their_permitted_values():
    _, rows = rs.build(_frame([
        {"video_name": "V", "segment_num": 1, "reach_num": 1, "causal_reach": 1,
         "segment_outcome": "retrieved"},
    ]))
    by_name = {r["VariableName"]: r for r in rows}
    for name in ("reach_result", "pellet_result", "reach_decided_the_pellet"):
        assert by_name[name]["PermittedValues"], "%s must list its values" % name
        assert by_name[name]["PVDescribed"], "%s must explain its values" % name
