"""Every column we export must be a name ODC will accept.

WHY THIS TEST EXISTS
--------------------
ODC's guide to naming variables sets hard rules: a name starts with a letter,
contains only letters, digits, underscores and periods, has no spaces or special
characters, and is at most 64 characters. A name that breaks them is a name
their importer can refuse, and the refusal arrives at upload time -- after the
submission is assembled, when there is nothing left to do but rename columns and
start again.

Most of our columns are named in code and legal because they were written
legally. The complete per-reach record is the exception: its columns are copied
from each cohort's tracking workbook, so their names are whatever a person typed
into a spreadsheet header. Sixteen illegal ones were being exported before this
test existed -- "Contusion_Subject_Weight (g)", "ODC_#_of_surgeons",
"ODC_Group ID", "ODC_Test_score_post-injury_first" and the rest. They are
repaired on the way out now (data_dictionary.apply_odc_names), and this test is
what keeps the next workbook header from reintroducing the problem.

The last tests run against the FILES, not the intention: the written CSV and its
written dictionary, which is what ODC would receive.
"""
from pathlib import Path

import pandas as pd
import pytest

from mousedb.exporters import data_dictionary as dd
from mousedb.exporters import odc_reaches


def _illegal(name):
    """Which rule a name breaks, or None. Worded so a failure says why."""
    text = str(name)
    if not dd.ODC_NAME_PATTERN.match(text):
        if " " in text:
            return "contains a space (ODC: use underscores instead)"
        if not text[:1].isalpha():
            return "does not start with a letter"
        bad = sorted({c for c in text if not (c.isalnum() or c in "_.")})
        return "contains " + ", ".join('"%s"' % c for c in bad)
    if len(text) > dd.ODC_NAME_MAX:
        return "is %d characters (ODC allows %d)" % (len(text), dd.ODC_NAME_MAX)
    return None


# ---------------------------------------------------------------- the rule itself

def test_a_spreadsheet_header_becomes_a_legal_name():
    """The real headers that were going out, and what they must become."""
    assert dd.odc_name("Contusion_Subject_Weight (g)") == "Contusion_Subject_Weight_g"
    assert dd.odc_name("Injection_Coordinates (M/L)") == "Injection_Coordinates_M_L"
    assert dd.odc_name("ODC_Group ID") == "ODC_Group_ID"
    assert dd.odc_name("ODC_Test_score_post-injury_first") == "ODC_Test_score_post_injury_first"
    assert dd.odc_name("Injection_Signal Post Perfusion") == "Injection_Signal_Post_Perfusion"


def test_a_character_that_means_something_is_spelled_out_not_dropped():
    """"#" is "number" and "%" is "percent"; deleting them changes the meaning."""
    assert dd.odc_name("ODC_#_of_surgeons") == "ODC_number_of_surgeons"
    assert dd.odc_name("ODC_Analgesic_cat#") == "ODC_Analgesic_cat_number"
    assert dd.odc_name("Percent %") == "Percent_percent"
    assert dd.odc_name("Pre & Post") == "Pre_and_Post"


def test_a_legal_name_is_left_exactly_as_it_is():
    for name in ("SubjectID", "reach_duration_s", "entry_index", "Test_Date",
                 "pellet_state_at_this_reach", "a.b"):
        assert dd.odc_name(name) == name


def test_every_name_the_rule_produces_passes_the_rule():
    awkward = ["", "   ", "4_Contusion", "#", "%%", "___", "a" * 80,
               "Weight (g)/(kg)", "2026-02-08", "Pre-test Score (0-100)"]
    for raw in awkward:
        assert _illegal(dd.odc_name(raw)) is None, raw


def test_a_truncated_name_does_not_steal_another_columns_identity():
    """Two long headers that agree for 64 characters must not become one name."""
    stem = "Contusion_" + "x" * 60
    mapping = dd.odc_names([stem + "_first", stem + "_second"])
    assert len(set(mapping.values())) == 2
    for name in mapping.values():
        assert _illegal(name) is None


def test_renaming_a_column_renames_its_dictionary_row_too():
    """A legal column whose dictionary still says the old name is undocumented."""
    frame = pd.DataFrame({"Weight (g)": [1], "SubjectID": ["CNT_01_01"]})
    rows = [dd._row("Weight (g)", "Contusion: Weight (g)", "A weight."),
            dd._row("SubjectID", "Subject", "The animal.")]
    renamed, new_rows = dd.apply_odc_names(frame, rows)
    assert list(renamed.columns) == ["Weight_g", "SubjectID"]
    assert {r["VariableName"] for r in new_rows} == set(renamed.columns)


def test_the_original_header_is_still_findable_after_the_rename():
    """The rename is of the machine-readable name only; provenance survives."""
    frame = pd.DataFrame({"Weight (g)": [1]})
    rows = [dd._row("Weight (g)", "4_Contusion_Injury_Details: Weight (g)",
                    "A weight.", comments="Copied from the sheet.")]
    _renamed, new_rows = dd.apply_odc_names(frame, rows)
    row = new_rows[0]
    assert "Weight (g)" in row["Title"]
    assert "Weight (g)" in row["Comments"]


def test_the_rule_does_not_quietly_rewrite_module_level_dictionary_rows():
    """apply_odc_names must copy before it edits: these lists are shared constants."""
    before = dict(dd.ODC_REACH_ANIMAL[0])
    frame = pd.DataFrame({dd.ODC_REACH_ANIMAL[0]["VariableName"]: [1], "Bad Name": [2]})
    dd.apply_odc_names(frame, list(dd.ODC_REACH_ANIMAL))
    assert dd.ODC_REACH_ANIMAL[0] == before


def test_every_name_this_module_defines_is_already_legal():
    """The dictionaries written in code need no repair; they are written legally."""
    for name, rows in dd.DICTIONARIES.items():
        for row in rows:
            why = _illegal(row["VariableName"])
            assert why is None, "%s: %r %s" % (name, row["VariableName"], why)
    for rows in (dd.ODC_REACH_ANIMAL, dd.ODC_REACH_SESSION, dd.ODC_REACH_TOTALS):
        for row in rows:
            assert _illegal(row["VariableName"]) is None, row["VariableName"]


def test_the_write_path_applies_the_rule():
    """Not a claim about today's files -- a claim about the code that writes them."""
    import inspect
    source = inspect.getsource(odc_reaches._write)
    assert "apply_odc_names" in source
    cohort = inspect.getsource(odc_reaches.write_cohort)
    assert cohort.count("apply_odc_names") >= 2, (
        "the analysis documents are written by write_cohort itself, so they need "
        "the rule applied there too")


# ------------------------------------------------------- the files we actually ship

def _written_csvs():
    from mousedb.config import export_path
    out = export_path()
    if out is None:
        return []
    out = Path(out) / "current"
    if not out.exists():
        return []
    return sorted(out.glob("*.csv")) + sorted((out / "Example").glob("*.csv"))


@pytest.mark.parametrize("csv_path", _written_csvs(), ids=lambda p: p.name)
def test_an_exported_file_carries_no_illegal_column_name(csv_path):
    head = pd.read_csv(csv_path, nrows=0)
    problems = {c: _illegal(c) for c in head.columns if _illegal(c)}
    assert not problems, "%s: %s" % (
        csv_path.name, "; ".join("%r %s" % (c, why) for c, why in problems.items()))


@pytest.mark.parametrize(
    "csv_path", [p for p in _written_csvs() if p.name.endswith("DATA_DICTIONARY.csv")],
    ids=lambda p: p.name)
def test_a_written_dictionary_carries_no_illegal_variable_name(csv_path):
    frame = pd.read_csv(csv_path)
    assert "VariableName" in frame.columns, csv_path.name
    problems = {v: _illegal(v) for v in frame["VariableName"].astype(str)
                if _illegal(v)}
    assert not problems, "%s: %s" % (
        csv_path.name, "; ".join("%r %s" % (v, why) for v, why in problems.items()))
