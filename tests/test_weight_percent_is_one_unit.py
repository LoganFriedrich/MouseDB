"""The body-weight percentage comes out in one unit, whatever unit it went in as.

WHY THIS TEST EXISTS
--------------------
The weights table holds the percentage of free-feeding weight in TWO units,
depending on which import path wrote it: a fraction (0.87) for cohorts whose
weights came through the weight grid and the tray tab, and the sheet's own
percentage (87) for cohorts imported through the vertical weight tab.

The export multiplied every value by 100. That was right for the fractions and
turned one cohort's 90% into 9,000% -- in a column that was shipped. Nothing
caught it, because the number was plausible-looking to code that never asked
what range a body weight percentage can occupy.

What the test is FOR is a unit error, and a unit error is not subtle: a fraction
left unscaled reads about 1, a percentage scaled twice reads about 9,000. So the
bound only has to be wide enough to leave the biology alone.

The biology is wider than it looks. The percentage is relative to the
free-feeding weight measured when restriction began, and young mice keep
growing -- in cohort 03, animals climb smoothly to 115-129% over the ten weeks
after injury, many of them together, in step with their grams. That is growth,
not error, and a test that called it an error was asserting something about
mice that nobody had checked (2026-10-15: the first version used 125% and
failed on exactly this).
"""
from pathlib import Path

import pandas as pd
import pytest

from mousedb.exporters import odc_reaches

# Wide on purpose: these catch a unit error (about 1 or about 9,000), not growth.
PLAUSIBLE = (30.0, 200.0)
TYPICAL_MEDIAN = (70.0, 130.0)


def _weights(tmp_path, values):
    frame = pd.DataFrame({
        "subject_id": ["CNT_01_%02d" % (i + 1) for i in range(len(values))],
        "date": ["2026-01-01"] * len(values),
        "weight_grams": [20.0] * len(values),
        "weight_percent": values,
    })
    frame.to_parquet(tmp_path / "weights.parquet", index=False)
    return odc_reaches.session_weights(tmp_path)


def test_a_fraction_becomes_a_percentage(tmp_path):
    out = _weights(tmp_path, [0.87, 0.92])
    assert list(out["weight_percent"].round(2)) == [87.0, 92.0]


def test_a_percentage_is_left_as_it_is(tmp_path):
    """The bug: 90 was multiplied to 9,000."""
    out = _weights(tmp_path, [90.0, 87.5])
    assert list(out["weight_percent"].round(2)) == [90.0, 87.5]


def test_both_units_in_one_table_come_out_in_one(tmp_path):
    out = _weights(tmp_path, [0.9, 90.0, 0.85, 85.0])
    assert list(out["weight_percent"].round(2)) == [90.0, 90.0, 85.0, 85.0]


def test_an_absent_percentage_stays_absent(tmp_path):
    out = _weights(tmp_path, [None, 0.9])
    assert pd.isna(out["weight_percent"].iloc[0])
    assert out["weight_percent"].iloc[1] == pytest.approx(90.0)


def _snapshot_weights():
    from mousedb.config import snapshot_dir
    try:
        root = Path(snapshot_dir())
    except Exception:
        return None
    if not (root / "weights.parquet").exists():
        return None
    return odc_reaches.session_weights(root)


def test_every_real_cohort_lands_where_a_mouse_can_be():
    """On the live snapshot, not on a fixture: this is the check that would have
    caught the shipped 9,000%."""
    weights = _snapshot_weights()
    if weights is None or weights.empty:
        pytest.skip("no weights in the snapshot on this machine")
    weights = weights.dropna(subset=["weight_percent"])
    weights["cohort"] = weights["subject_id"].str.rsplit("_", n=1).str[0]
    problems = []
    for cohort, group in weights.groupby("cohort"):
        median = group["weight_percent"].median()
        if not TYPICAL_MEDIAN[0] <= median <= TYPICAL_MEDIAN[1]:
            problems.append("%s median %.1f%%" % (cohort, median))
        outside = group[(group["weight_percent"] < PLAUSIBLE[0])
                        | (group["weight_percent"] > PLAUSIBLE[1])]
        if len(outside):
            problems.append("%s has %d values outside %s-%s%% (e.g. %.1f)"
                            % (cohort, len(outside), PLAUSIBLE[0], PLAUSIBLE[1],
                               outside["weight_percent"].iloc[0]))
    assert not problems, "; ".join(problems)
