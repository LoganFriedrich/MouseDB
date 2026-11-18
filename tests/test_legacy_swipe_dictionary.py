"""The nine legacy_swipe_* values each carry a real definition and the right unit.

Kinematics extractor 2.6.0 adds nine values to every reach's extended block that
reproduce an earlier program's per-swipe measures, so analyses built on that
program can be continued with new data. Their names did not parse into the
landmark / measure / variant scheme that writes the other definitions, so every
one of them read "No written definition yet", and the speed -- whose unit is
pixels divided by four, per second -- was described as seconds, because its name
ends in "_sec".
"""
from mousedb.exporters import data_dictionary as dd
from mousedb.exporters import extended_features as extended

LEGACY_KEYS = [
    "legacy_swipe_duration_frames",
    "legacy_swipe_first_frame",
    "legacy_swipe_last_frame",
    "legacy_swipe_breadth_px",
    "legacy_swipe_length_px",
    "legacy_swipe_area_px2",
    "legacy_swipe_path_length_px",
    "legacy_swipe_speed_px_div4_per_sec",
    "legacy_swipe_path_over_frames_px_per_frame",
]


def _rows():
    return {r["VariableName"]: r for r in extended.dictionary_rows(LEGACY_KEYS)}


def test_every_legacy_value_has_a_written_definition():
    rows = _rows()
    for key in LEGACY_KEYS:
        text = rows[extended.PREFIX + key]["Description"]
        assert "No written definition" not in text, key
        # Each says it is the earlier definition, so nobody takes it for a
        # better measurement of the reach than the rest of the block.
        assert "earlier" in text.lower(), key


def test_the_speed_is_not_described_as_seconds():
    row = _rows()[extended.PREFIX + "legacy_swipe_speed_px_div4_per_sec"]
    assert row["Unit_of_Measure"] != "seconds"
    assert "pixels / 4" in row["Unit_of_Measure"]


def test_the_other_units():
    units = {k: _rows()[extended.PREFIX + k]["Unit_of_Measure"] for k in LEGACY_KEYS}
    assert units["legacy_swipe_breadth_px"] == "pixels"
    assert units["legacy_swipe_length_px"] == "pixels"
    assert units["legacy_swipe_path_length_px"] == "pixels"
    assert units["legacy_swipe_area_px2"] == "pixels^2"
    assert units["legacy_swipe_duration_frames"] == "frames"
    assert units["legacy_swipe_path_over_frames_px_per_frame"] == "pixels per frame"
    assert units["legacy_swipe_first_frame"] == "frame"
    assert units["legacy_swipe_last_frame"] == "frame"


def test_the_dictionary_says_rows_from_an_older_extractor_are_empty():
    for key in LEGACY_KEYS:
        assert "2.6.0" in _rows()[extended.PREFIX + key]["Comments"], key


def test_the_block_size_in_the_dictionary_is_current():
    row = next(r for r in dd.REACH_DATA if r["VariableName"] == "extended_features")
    assert "174 values" in row["Description"]
    assert "165" in row["Description"]          # what an older row holds
