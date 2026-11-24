"""A credited reach stays credited when causal_reach arrives as a float.

WHY (2026-11-24): the export adds one session-only row per filed tray with no
reach (odc_reaches.build_cohort). Concatenating those rows turns causal_reach
from 0/1 integers into 0.0/1.0 floats, and the reach outcome read only "1" as
true -- so in every cohort with such a tray every credited reach read
"Undetermined" in the per-reach files, and first-attempt success came out 0.
"""
import pandas as pd

from mousedb.exporters import reach_summary as rs


def test_float_causal_flags_count_as_credited():
    causal = pd.Series([1.0, 0.0, float("nan"), 0.0])
    pellet = pd.Series(["Retrieved", "Retrieved", "Missed", "Retrieved"])
    seg = pd.Series([("v", "1.0"), ("v", "1.0"), ("v", "nan"), ("w", "2.0")])
    out = rs._reach_result(causal, pellet, seg)
    assert out.tolist()[:2] == ["Retrieved", "Missed"]
    # a moved pellet with no credited reach is still undetermined
    assert out.iloc[3] != "Retrieved" and out.iloc[3] != "Missed"


def test_integer_and_float_flags_give_the_same_answer():
    pellet = pd.Series(["Retrieved", "Retrieved"])
    seg = pd.Series([("v", "1"), ("v", "1")])
    a = rs._reach_result(pd.Series([1, 0]), pellet, seg)
    b = rs._reach_result(pd.Series([1.0, 0.0]), pellet, seg)
    assert a.tolist() == b.tolist()
