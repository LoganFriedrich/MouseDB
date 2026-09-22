"""An empty cell must be filled with the RIGHT reason, not just with something.

The bug these pin was live in the first build of the two-document export: the reason
was inferred from the fact that a column happened to be empty, so every column empty in
a cohort was labelled 'Not measured'. The frozen ASPA workbooks record no species, so
that export told a reader the lab had not measured a mouse's species -- which is both
false and, in a file meant for people outside the lab, embarrassing in a specific way.

The reason now comes from what the column IS (its data dictionary entry), never from
what it happens to contain.
"""
import pandas as pd
import pytest

from mousedb.exporters import data_dictionary as dd
from mousedb.exporters import missing
from mousedb.exporters import odc_reaches


def test_never_computed_is_read_from_the_dictionary_not_from_the_data():
    names = dd.never_computed_names()
    # Columns the dictionary itself marks as never computed.
    for expected in ("grasp_aperture_max_mm", "grasp_aperture_at_contact_mm",
                     "lateral_deviation_mm", "apex_distance_to_pellet_mm",
                     "tracking_quality_score", "distance_to_interaction"):
        assert expected in names, "%s is marked NEVER_COMPUTED in the dictionary" % expected
    # The extent columns are deliberately NOT in this set: a legacy detector did fill
    # them, so the dictionary tells that fuller story in prose instead of carrying the
    # marker. They are handled by name in the exporter (NOT_MEASURED_BY_CURRENT_DETECTOR),
    # because no current detector fills them.
    for legacy_only in ("max_extent_mm", "max_extent_pixels", "max_extent_ruler"):
        assert legacy_only not in names
        assert legacy_only in odc_reaches.NOT_MEASURED_BY_CURRENT_DETECTOR
    # Columns that come from a source. They may be empty in some cohorts, and that
    # must never be described as something no code computes.
    for from_a_source in ("SpeciesTyp", "SpeciesStrainTyp", "Animal_origin", "SexTyp",
                          "BodyWgtMeasrVal", "Injury_level"):
        assert from_a_source not in names, (
            "%s is recorded by a source; empty means the source did not carry it"
            % from_a_source)


def test_a_source_column_empty_everywhere_is_not_recorded_not_unmeasured():
    """The ASPA case, reduced: a whole column empty because the workbook lacks it."""
    frame = pd.DataFrame({"SpeciesTyp": ["", "", ""], "max_extent_mm": [None, None, None]})
    never = dd.never_computed_names() | odc_reaches.NOT_MEASURED_BY_CURRENT_DETECTOR
    reasons = {c: missing.NOT_MEASURED for c in never if c in frame}
    filled = missing.fill_frame(frame, reasons, default=missing.NOT_RECORDED)
    assert set(filled["SpeciesTyp"]) == {missing.NOT_RECORDED}
    assert set(filled["max_extent_mm"]) == {missing.NOT_MEASURED}


def test_the_four_reasons_stay_distinct():
    assert len(set(missing.ALL)) == 4
    for word in missing.ALL:
        assert word in missing.PV_DESCRIBED, (
            "every reason must be explained in the dictionary's PVDescribed")


@pytest.mark.parametrize("value", [None, "", "   ", float("nan"), pd.NA])
def test_every_shape_of_nothing_counts_as_blank(value):
    assert missing.is_blank(value)


@pytest.mark.parametrize("value", [0, 0.0, "0", False, "None"])
def test_real_values_are_never_treated_as_blank(value):
    assert not missing.is_blank(value), (
        "a zero is a measurement; overwriting it with a reason would invent missing data")


def test_fill_leaves_present_values_alone():
    frame = pd.DataFrame({"a": [1, None, 3]})
    filled = missing.fill_frame(frame, {"a": missing.NOT_APPLICABLE})
    assert list(filled["a"]) == [1, missing.NOT_APPLICABLE, 3]
