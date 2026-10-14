"""Whether the pellet was still there to be had when a reach was made.

A pellet is settled by at most one reach. Reaches before that one happened while
the pellet was still available; reaches after it, at the same pellet, happened
when it was already retrieved or knocked out of reach.

This is not a convenience column. A reach made after its pellet was gone could
not have succeeded whatever the paw did, so counting it alongside reaches that
could have succeeded does not fail -- it quietly makes every success rate, every
per-reach average and every comparison between animals wrong, because the
denominator contains attempts that were never attempts at anything.
"""
import pandas as pd
import pytest

from mousedb.exporters import analysis_reaches as ar


def reaches(causal_flags, video="v1", segment=1):
    """One pellet's worth of reaches, in order, with one marked as deciding."""
    n = len(causal_flags)
    return pd.DataFrame({
        "video_name": [video] * n,
        "segment_num": [segment] * n,
        "reach_num": list(range(1, n + 1)),
        "causal_reach": causal_flags,
    })


def test_reaches_before_the_deciding_one_had_a_pellet_to_get():
    state = ar.pellet_state(reaches([0, 0, 1, 0]))
    assert list(state)[:2] == [ar.STATE_AVAILABLE, ar.STATE_AVAILABLE]


def test_the_deciding_reach_is_marked_as_such():
    state = ar.pellet_state(reaches([0, 0, 1, 0]))
    assert state.iloc[2] == ar.STATE_DECIDED_BY_THIS


def test_reaches_after_the_deciding_one_could_not_have_succeeded():
    state = ar.pellet_state(reaches([0, 0, 1, 0]))
    assert state.iloc[3] == ar.STATE_ALREADY_GONE


def test_a_pellet_nothing_moved_stayed_available_throughout():
    """An untouched pellet was gettable at every attempt."""
    state = ar.pellet_state(reaches([0, 0, 0]))
    assert set(state) == {ar.STATE_AVAILABLE}


def test_at_most_one_reach_decides_a_pellet():
    state = ar.pellet_state(reaches([0, 1, 0, 0]))
    assert list(state).count(ar.STATE_DECIDED_BY_THIS) == 1


def test_the_first_reach_can_be_the_deciding_one():
    state = ar.pellet_state(reaches([1, 0, 0]))
    assert list(state) == [ar.STATE_DECIDED_BY_THIS,
                           ar.STATE_ALREADY_GONE, ar.STATE_ALREADY_GONE]


def test_pellets_are_grouped_within_their_own_video():
    """Every video has a pellet 1; grouping by number alone would merge animals."""
    a = reaches([0, 1], video="v1", segment=1)
    b = reaches([0, 0], video="v2", segment=1)
    both = pd.concat([a, b], ignore_index=True)
    state = ar.pellet_state(both)
    assert state.iloc[1] == ar.STATE_DECIDED_BY_THIS
    assert list(state)[2:] == [ar.STATE_AVAILABLE, ar.STATE_AVAILABLE], (
        "the second video's pellet was never settled")


def test_two_pellets_in_one_video_are_kept_apart():
    a = reaches([0, 1], video="v1", segment=1)
    b = reaches([0, 0], video="v1", segment=2)
    state = ar.pellet_state(pd.concat([a, b], ignore_index=True))
    assert list(state)[2:] == [ar.STATE_AVAILABLE, ar.STATE_AVAILABLE]


def test_it_travels_on_the_export_with_its_own_dictionary_row():
    frame = pd.DataFrame({
        "SubjectID": ["PROJ_01_01"] * 3,
        "video_name": ["v1"] * 3,
        "segment_num": [1, 1, 1],
        "reach_num": [1, 2, 3],
        "causal_reach": [0, 1, 0],
        "segment_outcome": ["retrieved"] * 3,
        "duration_frames": [10, 11, 12],
    })
    out, rows = ar.build(frame)
    assert "pellet_state_at_this_reach" in out.columns
    entry = next(r for r in rows if r["VariableName"] == "pellet_state_at_this_reach")
    assert entry["PermittedValues"] == ar.PELLET_STATE_VALUES
    assert "could not have succeeded" in entry["Description"]
    assert list(out["pellet_state_at_this_reach"]) == [
        ar.STATE_AVAILABLE, ar.STATE_DECIDED_BY_THIS, ar.STATE_ALREADY_GONE]


def test_it_is_a_word_column_so_it_is_never_turned_into_NA():
    assert "pellet_state_at_this_reach" in ar.TEXT_COLUMNS
