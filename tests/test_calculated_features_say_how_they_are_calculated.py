"""A calculated value's Description must say how it was calculated.

The dictionary's Description field is where a reader finds out what a column IS.
For a measurement that nobody observed directly -- every one of these -- that
means the calculation, because without it the reader cannot tell what they are
comparing. An "area" could be a convex hull, a bounding box or a polygon through
the tracked points, and the three are not the same number.

These descriptions used to be generated from the column NAME, which gives the
reader back the words they just read. The owner's objection, 2026-10-13: "if
something is a calculated value, it must say how calculated in the description."

They are composed from a landmark, a measure and a variant so that one accurate
sentence per measure covers every column using it -- less text, and fewer chances
to describe a measurement wrongly, which was the original worry behind generating
them from the name.
"""
import re

import pytest

from mousedb.exporters import extended_features as ex

# Words that only appear if a description is actually explaining a computation.
CALCULATION_WORDS = re.compile(
    r"divided by|sum|summed|total distance|difference|average|averaged|mean|"
    r"largest|smallest|greatest|minus|per |correlation|polygon|distance between|"
    r"number of|count|likelihood|frame[- ]to[- ]frame|relative to|"
    r"earliest|latest|in order of|through the four",
    re.I)


def every_key():
    """One key per measure family, with its variants -- the shape really exported."""
    keys = []
    for landmark in ("righthand", "rhleft", "rhright", "rhout"):
        for measure in ("extension_past_nose", "total_path", "lateral_spread",
                        "swept_area", "path_directness", "motion_smoothness",
                        "lateral_deviation", "mean_speed", "peak_speed",
                        "apex_speed", "apex_frame"):
            keys.append("%s_%s_mm" % (landmark, measure))
        for variant in ("at_apex", "at_start", "at_end", "max", "min", "mean", "range"):
            keys.append("%s_visibility_%s" % (landmark, variant))
    for measure in ("outline_area", "spread_max", "width_proxy"):
        for variant in ("at_apex", "at_contact", "at_start", "at_end",
                        "max", "min", "mean", "range"):
            keys.append("paw_%s_%s_mm" % (measure, variant))
    keys += ["paw_velocity_correlation", "paw_apex_lead_frames",
             "paw_leading_point", "frames_any_paw_low_confidence",
             "tray_contact_duration_frames"]
    return keys


@pytest.mark.parametrize("key", every_key())
def test_the_description_explains_the_calculation(key):
    text = ex._describe(key)
    assert "No written definition yet" not in text, (
        "%s has no definition; add its measure to _MEASURES" % key)
    assert CALCULATION_WORDS.search(text), (
        "%s does not say how it is calculated:\n  %s" % (key, text))


@pytest.mark.parametrize("key", every_key())
def test_the_description_is_not_just_the_name_back(key):
    """The failure being corrected: "Paw outline area mean" defines nothing."""
    text = ex._describe(key)
    words = [w for w in key.split("_") if len(w) > 3 and not w.isdigit()]
    restated = " ".join(words).lower()
    assert restated not in text.lower(), "%s restates its own name" % key
    assert len(text) > 60, "%s is too short to be explaining anything: %r" % (key, text)


ANCHOR_WORDS = "calculated between the nose and the slit edge, not a detected hand position"


@pytest.mark.parametrize("key", ["righthand_total_path_mm", "rhout_peak_speed_mm_per_frame",
                                 "rhleft_path_directness", "rhright_apex_speed_px_per_frame",
                                 "paw_velocity_correlation"])
def test_a_path_measure_says_its_ends_are_calculated(key):
    """Extractor 2.3.0: a reader must not take the ends of the path for hand positions."""
    assert "calculated between the nose and the slit edge" in ex._describe(key)


@pytest.mark.parametrize("key", ["righthand_apex_frame", "rhout_extension_past_nose_mm"])
def test_the_furthest_point_is_said_to_use_detected_frames(key):
    """These two read detected frames only, so the anchored basis would be wrong."""
    text = ex._describe(key)
    assert "detected frames only" in text
    assert ANCHOR_WORDS not in text


def test_the_flat_wrist_columns_say_their_ends_are_calculated():
    from mousedb.exporters import data_dictionary as dd
    rows = {r["VariableName"]: r for r in dd.REACH_DATA}
    for name in ("peak_velocity_px_per_frame", "mean_velocity_px_per_frame",
                 "trajectory_straightness", "trajectory_smoothness"):
        assert ANCHOR_WORDS in rows[name]["Description"], name
    assert "first to last wrist" not in rows["trajectory_straightness"]["Description"]
    assert "start anchor to the end anchor" in rows["trajectory_straightness"]["Description"]


def test_the_anchor_columns_are_defined_and_not_called_numbers():
    rows = {r["VariableName"]: r for r in ex.dictionary_rows(
        ["anchor_start_source", "anchor_end_source",
         "anchor_start_in_neighbour_reach", "anchor_end_in_neighbour_reach"])}
    assert len(rows) == 4
    for name, row in rows.items():
        assert "No written definition yet" not in row["Description"], name
    source = rows["ext_anchor_start_source"]
    assert ANCHOR_WORDS in source["Description"]
    assert source["DataType"] == "text", source
    for word in ("anchor_frame", "nearest_frame", "video_edge", "none"):
        assert word in source["PermittedValues"]
    assert rows["ext_anchor_end_in_neighbour_reach"]["DataType"] == "boolean"


def test_the_leading_point_is_defined_and_not_called_a_number():
    row = ex.dictionary_rows(["paw_leading_point"])[0]
    assert row["DataType"] == "text", row
    assert "No written definition yet" not in row["Description"]
    for word in ("righthand", "rhleft", "rhright", "rhout"):
        assert word in row["PermittedValues"]
        assert word + "=" in row["PVDescribed"]


def test_the_analysis_file_keeps_the_worded_columns_as_words():
    """Everything not named a text column is read as a number there, which would
    write every anchor source and every leading point as NA."""
    from mousedb.exporters import analysis_reaches as ar
    for name in ("anchor_start_source", "anchor_end_source",
                 "anchor_start_in_neighbour_reach", "anchor_end_in_neighbour_reach",
                 "paw_leading_point"):
        assert name in ar.TEXT_COLUMNS
    extra = dict(ar.other_kinematics(["ext_anchor_start_source", "ext_paw_leading_point"]))
    assert extra["ext_anchor_start_source"] == "anchor_start_source"
    assert extra["ext_paw_leading_point"] == "paw_leading_point"


def test_a_variant_says_what_it_was_taken_over():
    """max of what? mean over what? -- the part a bare name never carries."""
    assert "largest value over the frames" in ex._describe("paw_spread_max_max_mm")
    assert "Averaged over the frames" in ex._describe("paw_spread_max_mean_mm")
    assert "furthest point of the reach" in ex._describe("paw_spread_max_at_apex_mm")
    assert "first frame" in ex._describe("paw_spread_max_at_start_mm")


def test_trajectory_measures_state_the_frame_of_reference():
    """These are nose-relative between calculated anchors; a reader cannot guess that."""
    text = ex._describe("righthand_total_path_mm")
    assert "relative to the nose" in text
    assert ANCHOR_WORDS in text


def test_inter_paw_measures_name_which_points(ter=None):
    assert "four tracked paw points" in ex._describe("paw_spread_max_max_mm")
    assert "four tracked paw points" in ex._describe("paw_outline_area_mean_mm2")


def test_a_measure_nobody_has_defined_admits_it():
    """Honest about saying little beats prose that sounds like a definition."""
    text = ex._describe("righthand_some_measure_nobody_wrote_mm")
    assert "No written definition yet" in text


def test_every_description_still_gets_a_dictionary_row():
    rows = ex.dictionary_rows([k for k in every_key()])
    assert len(rows) == len(every_key())
    assert all(r["Description"] for r in rows)
