"""The shareable per-reach document carries every measurement, not a chosen few.

The document is trimmed so a collaborator can read it: identifying columns in
plain English, one committed answer per reach, no process columns. For a while
it was trimmed on the measurement side too, down to the twenty-odd measures a
collaborator's own analysis had happened to use.

That is the wrong thing to trim. Which measures matter is their question to ask,
and a measurement left out of the shared file is one they cannot ask about at
all -- they would have to come back and request it, or re-derive it from video
we are not sending. So: identifying columns stay few, measurements are complete.
"""
import pandas as pd
import pytest

from mousedb.exporters import extended_features as extended
from mousedb.exporters import reach_summary


def frame_with(extended_columns):
    """A minimal per-reach frame carrying the named extended measurements."""
    base = {
        "subject_id": ["PROJ_01_01"],
        "video_name": ["20250101_PROJ0101_P1"],
        "segment_num": [1],
        "reach_id": [1],
        "segment_outcome": ["retrieved"],
        "causal_reach": [1],
        "start_frame": [100],
        "end_frame": [112],
        "duration_frames": [12],
    }
    for c in extended_columns:
        base[c] = [1.0]
    return pd.DataFrame(base)


def test_every_extended_measurement_reaches_the_document():
    novel = [extended.PREFIX + n for n in
             ("lefthand_total_path_mm", "paw_spread_min_mm", "nose_speed_mean_mm_per_frame")]
    out, _ = reach_summary.build(frame_with(novel))
    for column in novel:
        plain = column[len(extended.PREFIX):]
        assert plain in out.columns, "%s was dropped" % column


def test_the_ext_prefix_is_not_shown_to_the_reader():
    """It means "came from the extended block" -- an implementation detail."""
    out, _ = reach_summary.build(frame_with([extended.PREFIX + "paw_spread_min_mm"]))
    assert "paw_spread_min_mm" in out.columns
    assert not [c for c in out.columns if c.startswith(extended.PREFIX)]


def test_a_curated_measure_keeps_its_plain_name_and_is_not_duplicated():
    """ext_righthand_total_path_mm is already carried as path_length_mm."""
    out, _ = reach_summary.build(
        frame_with([extended.PREFIX + "righthand_total_path_mm"]))
    assert "path_length_mm" in out.columns
    assert "righthand_total_path_mm" not in out.columns, "carried twice"


def test_the_readable_columns_still_come_first():
    """A reader meets the identity, the answer and the familiar measures first."""
    out, _ = reach_summary.build(frame_with([extended.PREFIX + "zzz_last_measure"]))
    columns = list(out.columns)
    assert columns.index("reach_result") < columns.index("zzz_last_measure")
    assert columns.index("duration_frames") < columns.index("zzz_last_measure")


def test_every_extended_column_gets_its_real_dictionary_entry():
    """An upload fails without a row; a generic row passes and says nothing.

    The extended block has its own descriptions and units, and those are what
    must appear -- not the fallback "Per-reach measurement."
    """
    out, rows = reach_summary.build(
        frame_with([extended.PREFIX + "paw_outline_area_mean_mm2"]))
    by_name = {r["VariableName"]: r for r in rows}

    assert set(out.columns) <= set(by_name), "every column needs a dictionary row"
    entry = by_name["paw_outline_area_mean_mm2"]
    assert entry["Description"] != "Per-reach measurement.", (
        "fell through to the generic row instead of the extended block's own text")
    assert entry["Unit_of_Measure"], "the extended block knows the unit; it must be carried"


def test_ordering_is_stable_so_two_runs_produce_comparable_files():
    columns = [extended.PREFIX + n for n in ("b_measure", "a_measure", "c_measure")]
    first, _ = reach_summary.build(frame_with(columns))
    second, _ = reach_summary.build(frame_with(list(reversed(columns))))
    assert list(first.columns) == list(second.columns)


def test_a_frame_with_no_extended_block_still_builds():
    """Older cohorts predate the extended extractor; that is not an error."""
    out, rows = reach_summary.build(frame_with([]))
    assert "reach_result" in out.columns
    assert not out.empty


def test_no_empty_cells_survive_the_wider_document():
    """The document's standing rule: a reason word, never a blank."""
    frame = frame_with([extended.PREFIX + "paw_spread_min_mm"])
    frame[extended.PREFIX + "paw_spread_min_mm"] = [None]
    out, _ = reach_summary.build(frame)
    assert out["paw_spread_min_mm"].notna().all()
    assert (out["paw_spread_min_mm"].astype(str).str.strip() != "").all()


def test_the_skip_signature_changed_with_the_output():
    """A machine holding older code must not skip the rewrite.

    The per-reach files are skipped when their INPUTS are unchanged, so a format
    change has to be declared or superseded files sit there looking current.
    """
    from mousedb.exporters import current
    import inspect
    source = inspect.getsource(current)
    assert 'sig["output_format"] = 4' in source, (
        "bump output_format whenever these columns change")
