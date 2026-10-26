"""A reach has three possible outcomes: it misses, it displaces, or it retrieves.

WHY THIS TEST EXISTS
--------------------
The per-reach outcome column used to spell its first value "Did not move the
pellet". That is a description of a miss, not the name of one, and naming an
outcome by what did not happen has a cost: it reads as an absence, and a reader
who treats it as one drops the majority of the file. Most reaches miss.

The vocabulary is fixed and it is short. A reach either leaves the pellet where
it was (missed), knocks it somewhere (displaced -- the destination is recorded
because it decides whether the pellet could still be scored), or takes it
(retrieved). The only other values allowed in the column are the pipeline saying
it has not decided yet, which is not an outcome and is never presented as one,
and "Not applicable" on an easy or flat training tray, where pellets are not
tracked and there is no pellet answer at all (2026-10-26).
"""
import pandas as pd

from mousedb.exporters import analysis_reaches as ar
from mousedb.exporters import missing
from mousedb.exporters import reach_summary as rs

# The whole vocabulary. Anything else in the column is a defect.
OUTCOMES = {rs.MISSED, rs.DISPLACED_IN, rs.DISPLACED_OUT, rs.RETRIEVED}
ALLOWED = OUTCOMES | {missing.UNDETERMINED, missing.NOT_APPLICABLE}


def _result(causal, pellet_words):
    return rs._reach_result(pd.Series(causal), rs._pellet_result(pd.Series(pellet_words)))


def test_a_reach_that_moved_nothing_missed():
    """The common case, and the one that was named wrongly."""
    assert list(_result([0], ["retrieved"])) == [rs.MISSED]
    assert rs.MISSED == "Missed"


def test_the_three_things_a_reach_can_do():
    assert list(_result([1, 1, 1], ["retrieved", "displaced_sa", "displaced_outside"])) == [
        rs.RETRIEVED, rs.DISPLACED_IN, rs.DISPLACED_OUT]


def test_an_undecided_segment_is_not_given_an_outcome():
    """'Not decided yet' is the pipeline's state, never a thing the animal did."""
    assert list(_result([1], ["triaged"])) == [missing.UNDETERMINED]
    assert missing.UNDETERMINED not in OUTCOMES


def test_an_untouched_pellet_still_means_its_reaches_missed():
    """A reach at a pellet nothing moved is a miss, not a blank."""
    assert list(_result([1, 0], ["untouched", "untouched"])) == [rs.MISSED, rs.MISSED]


def test_nothing_outside_the_vocabulary_can_reach_the_column():
    pellets = ["retrieved", "displaced_sa", "displaced_outside", "untouched",
               "missed", "triaged", "uncertain", "abnormal_exception", "", "nonsense"]
    for causal in (0, 1):
        values = set(_result([causal] * len(pellets), pellets))
        assert values <= ALLOWED, values - ALLOWED


def test_the_phrase_that_was_wrong_is_gone_from_the_exporters():
    """Including the dictionary text, which is what a reader actually reads."""
    import inspect
    for module in (rs, ar):
        source = inspect.getsource(module)
        body = "\n".join(line for line in source.splitlines()
                         if not line.strip().startswith("#"))
        assert "Did not move the pellet" not in body, module.__name__


def test_the_dictionary_lists_exactly_these_values():
    permitted = {v.strip() for v in rs.REACH_RESULT_PV.split(";")}
    assert permitted == ALLOWED
    entry = [r for r in rs.build(_frame())[1] if r["VariableName"] == "reach_result"]
    assert entry, "reach_result has no dictionary row"
    described = entry[0]["PVDescribed"]
    for value in ALLOWED:
        assert value + "=" in described, value


def _frame():
    """The smallest complete-record frame reach_summary will build from."""
    return pd.DataFrame({
        "SubjectID": ["CNT_01_01"], "Session_ID": ["s"], "Test_Date": ["2026-01-01"],
        "video_name": ["v"], "segment_num": [1], "reach_num": [1],
        "segment_outcome": ["retrieved"], "reach_decided_segment_outcome": [1],
    })
