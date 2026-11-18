"""In the analysis document the swipe_* names hold the earlier definition, nothing else.

swipe_length, swipe_breadth, swipe_area, swipe_speed, path_length,
swipe_duration_frames and path_over_frames are the names an earlier reach
program gave its per-swipe measures, and the analysis document keeps them so a
script written against that program's files keeps running. Until this change
they held the pipeline's OWN measures (extension past the nose in mm, lateral
deviation, swept area, peak speed per frame, anchored path length, reach
duration): different quantities, in different units, under a name that said
otherwise. Now they hold the extractor's legacy_swipe_* values, and the
pipeline's measures keep the extractor's own names.

A reach filed before extractor 2.6.0 has no legacy value. Its swipe_* cells are
NA -- never the pipeline's measure standing in, which would mix two definitions
in one column without saying so.
"""
import pandas as pd

from mousedb.exporters import analysis_reaches as ar


def frame(**overrides):
    base = {
        "SubjectID": ["PROJ_01_01", "PROJ_01_01"],
        "Test_Date": ["2026-01-02", "2026-01-02"],
        "Session_ID": ["s1", "s1"],
        "Tray_ID": ["P1", "P1"],
        "tray_type": ["P", "P"],
        "video_name": ["20260102_PROJ0101_P1", "20260102_PROJ0101_P1"],
        "segment_num": [1, 1],
        "reach_num": [1, 2],
        "segment_outcome": ["retrieved", "retrieved"],
        "causal_reach": [1, 0],
        "duration_frames": [12, 9],
        "ext_righthand_extension_past_nose_mm": [3.1, 2.2],
        "ext_righthand_lateral_deviation_mm": [0.4, 0.5],
        "ext_righthand_swept_area_mm2": [1.1, 1.2],
        "ext_righthand_peak_speed_mm_per_frame": [0.9, 0.8],
        "ext_righthand_total_path_mm": [4.2, 3.3],
    }
    base.update(overrides)
    return pd.DataFrame(base)


LEGACY = {
    "ext_legacy_swipe_length_px": [40.0, None],
    "ext_legacy_swipe_breadth_px": [12.0, None],
    "ext_legacy_swipe_area_px2": [300.0, None],
    "ext_legacy_swipe_speed_px_div4_per_sec": [55.5, None],
    "ext_legacy_swipe_path_length_px": [90.0, None],
    "ext_legacy_swipe_duration_frames": [7, 1],
    "ext_legacy_swipe_path_over_frames_px_per_frame": [12.857, None],
}

EXPECTED = {
    "swipe_length": "ext_legacy_swipe_length_px",
    "swipe_breadth": "ext_legacy_swipe_breadth_px",
    "swipe_area": "ext_legacy_swipe_area_px2",
    "swipe_speed": "ext_legacy_swipe_speed_px_div4_per_sec",
    "path_length": "ext_legacy_swipe_path_length_px",
    "swipe_duration_frames": "ext_legacy_swipe_duration_frames",
    "path_over_frames": "ext_legacy_swipe_path_over_frames_px_per_frame",
}


def test_the_swipe_names_take_the_legacy_values():
    out, _ = ar.build(frame(**LEGACY))
    for name, src in EXPECTED.items():
        assert out[name].iloc[0] == LEGACY[src][0], name


def test_the_pipelines_own_measures_keep_their_own_names():
    out, _ = ar.build(frame(**LEGACY))
    assert out["righthand_extension_past_nose_mm"].iloc[0] == 3.1
    assert out["righthand_lateral_deviation_mm"].iloc[0] == 0.4
    assert out["righthand_swept_area_mm2"].iloc[0] == 1.1
    assert out["righthand_peak_speed_mm_per_frame"].iloc[0] == 0.9
    assert out["righthand_total_path_mm"].iloc[0] == 4.2
    assert out["duration_frames"].iloc[0] == 12
    # Each is carried once, under one name.
    assert list(out.columns).count("duration_frames") == 1
    assert "legacy_swipe_length_px" not in out.columns


def test_a_reach_from_before_the_legacy_values_is_NA_not_a_fallback():
    """All rows older than extractor 2.6.0: the columns still exist, all NA."""
    out, rows = ar.build(frame())
    for name in EXPECTED:
        assert name in out.columns, "%s must exist even before any 2.6.0 row" % name
        assert (out[name] == ar.MISSING).all(), name
    described = {r["VariableName"]: r for r in rows}
    assert "earlier" in described["swipe_length"]["Description"].lower()


def test_a_mixed_file_blanks_only_the_old_rows():
    legacy = {k: [v[0], None] for k, v in LEGACY.items()}
    out, _ = ar.build(frame(**legacy))
    assert out["swipe_length"].iloc[0] == 40.0
    assert out["swipe_length"].iloc[1] == ar.MISSING
    assert out["swipe_length"].iloc[1] != 2.2


def test_the_dictionary_records_both_directions_of_the_rename():
    _out, rows = ar.build(frame(**LEGACY))
    by = {r["VariableName"]: r for r in rows}
    assert "formerly named swipe_length" in by["righthand_extension_past_nose_mm"]["Comments"]
    assert "formerly named swipe_duration_frames" in by["duration_frames"]["Comments"]
    assert "righthand_extension_past_nose_mm" in by["swipe_length"]["Comments"]
    assert "(pixels / 4) per second" == by["swipe_speed"]["Unit_of_Measure"]
