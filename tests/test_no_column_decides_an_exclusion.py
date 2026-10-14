"""The export reports what happened; it never decides whether data should be used.

WHY THIS TEST EXISTS
--------------------
Until 2026-10-14 this export wrote "Total exclusion" into
Exclusion_in_origin_study for any animal whose tracking sheet recorded a surgery
it did not survive. That is a verdict, and it is not the exporter's to reach --
an animal that died during its injury surgery still made every reach it made
before that day, and in one cohort alone that was 6,124 ordinary baseline
measurements sitting under a label that invites a reader to drop them. The
project's whole approach depends on those rows staying usable.

The verdict was removed and the underlying fact carried plainly instead
(Survived_All_Surgeries, Yes or No). Two things have to stay true afterwards, and
both have been wrong at some point:

  1. the exporter must not write an exclusion it was not told about, and
  2. the DICTIONARY must not describe the rule that was removed. It did, for as
     long as the code did not: the row still told a reader that an animal who
     died was a total exclusion, which is how a reader ends up applying a rule
     the file does not follow.
"""
import pandas as pd

from mousedb.exporters import data_dictionary as dd
from mousedb.exporters import odc_reaches


def _entry(name):
    for row in (dd.ODC_REACH_ANIMAL + dd.ODC_REACH_SESSION + dd.ODC_REACH_TOTALS):
        if row["VariableName"] == name:
            return row
    raise AssertionError("no dictionary row for %s" % name)


def _animal(sheet):
    """One animal's identity block, built the way the exporter builds it."""
    return odc_reaches._animal("CNT_01_01", "CNT_01", sheet, {}, {}, pd.DataFrame())


def test_an_animal_that_died_in_surgery_is_not_declared_excluded():
    row = _animal({"Contusion_Survived": "N"})
    assert row["Survived_All_Surgeries"] == "No"
    assert row["Exclusion_in_origin_study"] == ""
    assert row["Exclusion_reason"] == ""


def test_an_animal_that_survived_is_not_declared_included_either():
    """Inclusion is a decision too; the sheet says nothing, so neither do we."""
    row = _animal({"Contusion_Survived": "Y"})
    assert row["Survived_All_Surgeries"] == "Yes"
    assert row["Exclusion_in_origin_study"] == ""


def test_an_exclusion_the_sheet_states_is_carried_through():
    """Not inferring one is not the same as ignoring one that was recorded."""
    row = _animal({"Contusion_Survived": "Y",
                   "Exclusion_in_origin_study": "Total exclusion",
                   "Exclusion_reason": "Apparatus failure"})
    assert row["Exclusion_in_origin_study"] == "Total exclusion"
    assert row["Exclusion_reason"] == "Apparatus failure"


def test_the_exporter_cannot_invent_an_exclusion_value():
    """No code path writes a verdict: the words appear nowhere in the module."""
    import inspect
    source = inspect.getsource(odc_reaches._animal)
    body = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("#"))
    assert '"Total exclusion"' not in body
    assert '"No exclusion"' not in body


def test_the_dictionary_does_not_describe_the_rule_that_was_removed():
    row = _entry("Exclusion_in_origin_study")
    text = (row["Description"] + " " + row["PermittedValues"] + " "
            + row["PVDescribed"]).lower()
    for claim in ("survived = n", "did not survive a surgery",
                  "no exclusion when every recorded surgery survived"):
        assert claim not in text, (
            "the dictionary still describes the exclusion rule this export no "
            "longer applies: %r" % claim)
    assert "not recorded" in text, (
        "a reader needs to be told that no sheet fills this column, or they will "
        "read an empty column as 'nobody was excluded'")


def test_the_surgery_fact_says_it_is_a_fact_and_not_a_judgement():
    row = _entry("Survived_All_Surgeries")
    assert set(v.strip() for v in row["PermittedValues"].split(";")) == {"Yes", "No"}
    assert "not a" in row["Description"].lower()
