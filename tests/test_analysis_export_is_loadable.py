"""The analysis export must load and be usable without being cleaned first.

Its format is not a matter of taste. It was taken from a dataset an analysis
collaborator assembled and sent back, and the point of copying it is that their
existing analysis runs against it unchanged.

A format that is only written down drifts the first time somebody adds a column.
This project has already paid for that lesson: the "console output is ASCII only"
rule was documented for a long time, the scripts drifted anyway, and a tick
character stopped the extraction of a 37 GB brain before it read a slice. So each
quality of the format is asserted here, and a violation fails rather than ships.
"""
import re

import pandas as pd
import pytest

from mousedb.exporters import analysis_reaches as ar
from mousedb.exporters import extended_features as extended


def frame(**overrides):
    """A complete per-reach frame, as the exporter receives it."""
    base = {
        "SubjectID": ["PROJ_01_01", "PROJ_01_01"],
        "Test_Date": ["2026-01-02", "2026-01-02"],
        "Session_ID": ["s1", "s1"],
        "Tray_ID": ["P1", "P1"],
        "tray_type": ["P", "P"],
        "Test_Type": ["Baseline", "Baseline"],
        "Test_Type_Grouped": ["Pre-injury", "Pre-injury"],
        "Days_Post_Injury": [None, None],
        "Injury_Type": ["Contusion", "Contusion"],
        "InjGroupAssignTyp": ["A", "A"],
        "SexTyp": ["F", "F"],
        "Exclusion_in_origin_study": ["No exclusion", "No exclusion"],
        "video_name": ["20260102_PROJ0101_P1", "20260102_PROJ0101_P1"],
        "segment_num": [1, 1],
        "reach_num": [1, 2],
        "segment_outcome": ["retrieved", "retrieved"],
        "causal_reach": [1, 0],
        "duration_frames": [12, 9],
        "ext_righthand_total_path_mm": [4.2, None],
        "ext_paw_outline_area_mean_mm2": [1.5, 2.0],
        "Total_Swipes_AI": [2, 2],
        "Manual_Retrieved": [1, 1],
    }
    base.update(overrides)
    return pd.DataFrame(base)


# ------------------------------------------------------------------ the format
def test_every_column_name_is_lowercase_with_underscores():
    out, _ = ar.build(frame())
    bad = [c for c in out.columns if not ar.NAME_RULE.match(c)]
    assert not bad, "not in the format's naming: %s" % bad


def test_a_numeric_column_holds_only_numbers_or_NA():
    """The whole reason this document exists: one word makes the column text."""
    out, _ = ar.build(frame())
    for name in out.columns:
        if name in ar.TEXT_COLUMNS:
            continue
        for value in out[name]:
            assert value == ar.MISSING or isinstance(value, (int, float)), (
                "%s holds %r, which would make the column text" % (name, value))


def test_a_reason_word_from_the_other_documents_becomes_NA():
    """'Not recorded' is the right answer elsewhere and unusable here."""
    out, _ = ar.build(frame(ext_righthand_total_path_mm=["Not recorded", 4.0]))
    assert out["path_length"].iloc[0] == ar.MISSING
    assert out["path_length"].iloc[1] == 4.0


def test_nothing_is_ever_blank():
    out, _ = ar.build(frame())
    for name in out.columns:
        for value in out[name]:
            assert str(value).strip() != "", "%s has an empty cell" % name


def test_an_undecided_outcome_survives_as_a_word():
    """'Undetermined' is an answer about the pellet, not a missing value.

    Turning it into NA would convert a statement -- nobody has decided this yet
    -- into a silence, which reads as though the question was never asked.
    """
    out, _ = ar.build(frame(segment_outcome=[None, None]))
    assert out["pellet_outcome"].iloc[0] not in ("", ar.MISSING)


def test_the_row_stands_alone():
    """No file should have to be joined to another to answer a question."""
    out, _ = ar.build(frame())
    for needed in ("subject_id", "test_date", "session_id", "tray_type",
                   "test_type", "test_type_grouped", "injury_type", "sex",
                   "total_swipes_ai"):
        assert needed in out.columns, "%s must travel on the row" % needed


def test_the_session_totals_repeat_on_every_row_of_the_session():
    out, _ = ar.build(frame())
    assert out["total_swipes_ai"].nunique() == 1


def test_both_scoring_streams_are_carried():
    """They disagree sometimes; which is right is the reader's question."""
    out, _ = ar.build(frame())
    assert "total_swipes_ai" in out.columns      # the algorithm's
    assert "manual_retrieved" in out.columns     # the hand scores'


# --------------------------------------------------- what is deliberately absent
def test_no_analysis_outputs_are_reproduced():
    """PC and agreement columns are the result of an analysis, not measurements."""
    out, _ = ar.build(frame())
    for name in out.columns:
        assert not re.match(r"^pc\d+$", name), "%s is an analysis output" % name
        assert not name.endswith("_match"), "%s is an analysis output" % name


# --------------------------------------------------------------- the kinematics
def test_every_measurement_is_carried_not_a_chosen_few():
    novel = extended.PREFIX + "lefthand_total_path_mm"
    out, _ = ar.build(frame(**{novel: [1.0, 2.0]}))
    assert "lefthand_total_path_mm" in out.columns


def test_a_measure_with_a_collaborator_name_keeps_that_name_once():
    """So a script written against their file still runs."""
    out, _ = ar.build(frame())
    assert "path_length" in out.columns
    assert "righthand_total_path_mm" not in out.columns, "carried twice"


# ------------------------------------------------------------- the dictionary
def test_every_column_has_a_dictionary_row():
    out, rows = ar.build(frame())
    named = {r["VariableName"] for r in rows}
    assert set(out.columns) == named


def test_the_dictionary_says_how_absence_is_written():
    _out, rows = ar.build(frame())
    assert all("NA" in r["Comments"] for r in rows)


# ------------------------------------------------------- the three phase columns
#
# A reader groups at whichever coarseness their question needs: the session's own
# label, its timepoint, and the stage of the experiment. The dataset this format
# came from carried all three for the same reason.

def test_the_stage_is_derived_from_the_words_in_the_label():
    """Matched by meaning, not against one lab's list of phase names."""
    import pandas as pd
    got = list(ar.experiment_stage(pd.Series(
        ["Baseline", "Pillar_Training", "Post_Injury_2-4", "Rehab_Easy",
         "Post_Rehab_Test"])))
    assert got == [ar.STAGE_BEFORE, ar.STAGE_BEFORE, ar.STAGE_AFTER,
                   ar.STAGE_REHAB, ar.STAGE_AFTER_REHAB]


def test_post_rehab_is_not_swallowed_by_the_rehab_rule():
    """'post_rehab' contains 'rehab'; order decides, so it is asserted."""
    import pandas as pd
    assert list(ar.experiment_stage(pd.Series(["Post_Rehab_Test"]))) == [
        ar.STAGE_AFTER_REHAB]


def test_a_label_naming_no_stage_is_unknown_rather_than_forced():
    import pandas as pd
    assert list(ar.experiment_stage(pd.Series(["Unscheduled", "", None]))) == [
        ar.STAGE_UNKNOWN] * 3


def test_all_three_phase_columns_travel():
    out, _ = ar.build(frame())
    for name in ("test_type", "test_type_grouped", "experiment_stage"):
        assert name in out.columns


# -------------------------------------------------------------- the row number
#
# Sorting a sheet is a one-way door otherwise. The order written here is
# chronological within each animal and session, and no combination of the other
# columns reproduces it once it has been lost.

def test_every_row_carries_a_number():
    out, _ = ar.build(frame())
    assert ar.ROW_NUMBER in out.columns
    assert list(out[ar.ROW_NUMBER]) == [1, 2]


def test_the_number_comes_first_so_it_is_visible_on_opening():
    out, _ = ar.build(frame())
    assert list(out.columns)[0] == ar.ROW_NUMBER


def test_it_is_a_number_not_text():
    """It must sort as 1, 2, 10 rather than 1, 10, 2."""
    out, _ = ar.build(frame())
    assert ar.ROW_NUMBER not in ar.TEXT_COLUMNS
    assert all(isinstance(v, (int, float)) for v in out[ar.ROW_NUMBER])


def test_it_is_never_absent():
    out, _ = ar.build(frame())
    assert ar.MISSING not in list(out[ar.ROW_NUMBER].astype(str))


def test_it_is_unique():
    out, _ = ar.build(frame())
    assert out[ar.ROW_NUMBER].nunique() == len(out)


def test_its_dictionary_entry_says_it_means_nothing_about_the_reach():
    _out, rows = ar.build(frame())
    entry = next(r for r in rows if r["VariableName"] == ar.ROW_NUMBER)
    assert "carries no meaning" in entry["Description"]
    assert "put back in the order" in entry["Description"]


# ----------------------------------------------- facts, not verdicts, about animals
#
# This project keeps the data of animals that did not finish. A mouse lost in its
# injury surgery still made every reach it made beforehand, and those are ordinary
# measurements. Deciding whether they belong in an analysis depends on the
# question being asked, so the export states what happened and stops there.

def test_the_export_states_survival_rather_than_declaring_an_exclusion():
    out, _ = ar.build(frame(Survived_All_Surgeries=["No -- did not survive a surgery"] * 2))
    assert "survived_all_surgeries" in out.columns
    assert out["survived_all_surgeries"].iloc[0].startswith("No")


def test_no_column_announces_a_verdict_about_whether_an_animal_counts():
    out, _ = ar.build(frame())
    for name in out.columns:
        assert "inclusion" not in name, (
            "%s reads as a decision about whether the animal counts" % name)


def test_the_dictionary_says_the_decision_is_the_readers():
    _out, rows = ar.build(frame(Survived_All_Surgeries=["Yes"] * 2))
    entry = next((r for r in rows if r["VariableName"] == "survived_all_surgeries"), None)
    assert entry is not None
    assert "reader" in entry["Description"].lower() or "question" in entry["Description"].lower()
