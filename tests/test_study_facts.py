"""Study-wide ODC facts live in a local file, never in source (mousedb.study_facts).

WHY: these facts (strain, supplier, study leader, injury device) belong to one
lab's study. They used to be typed into the sheet generators, which put a
person's name in a public repository and stamped the same strain on studies
that used a different line. An unset fact must come back EMPTY, not guessed.
"""
import json

import pytest

from mousedb import study_facts as sf


@pytest.fixture
def facts_file(tmp_path, monkeypatch):
    path = tmp_path / "study_facts.json"
    monkeypatch.setenv(sf.ENV_VAR, str(path))
    return path


def test_nothing_set_means_empty_not_a_default(facts_file):
    assert sf.facts("PROJA_01_02") == {}
    assert sf.get("PROJA", "SpeciesStrainTyp") == ""
    assert sf.unset_fields("PROJA") == list(sf.FIELDS)


def test_project_value_wins_over_default(facts_file):
    sf.set_fact("default", "SpeciesTyp", "species-x")
    sf.set_fact("default", "SpeciesStrainTyp", "strain-default")
    sf.set_fact("PROJA", "SpeciesStrainTyp", "strain-a")
    assert sf.facts("PROJA_02_07") == {"SpeciesTyp": "species-x", "SpeciesStrainTyp": "strain-a"}
    assert sf.facts("PROJB_01") == {"SpeciesTyp": "species-x", "SpeciesStrainTyp": "strain-default"}


def test_unset_removes_the_fact_and_an_empty_section(facts_file):
    sf.set_fact("PROJA", "StudyLeader", "someone")
    sf.set_fact("PROJA", "StudyLeader", None)
    assert json.loads(facts_file.read_text(encoding="utf-8")) == {}


def test_blank_value_in_file_counts_as_unset(facts_file):
    facts_file.write_text(json.dumps({"PROJA": {"StudyLeader": ""}}), encoding="utf-8")
    assert "StudyLeader" in sf.unset_fields("PROJA")


def test_unreadable_file_is_treated_as_empty(facts_file):
    facts_file.write_text("{not json", encoding="utf-8")
    assert sf.facts("PROJA") == {}


@pytest.mark.parametrize("ident,project", [
    ("PROJA_02_07", "PROJA"), ("ABC_01", "ABC"), ("ABC", "ABC"), ("", ""), (None, ""),
])
def test_project_of(ident, project):
    assert sf.project_of(ident) == project


def test_describe_names_unset_facts(facts_file):
    sf.set_fact("PROJA", "SpeciesTyp", "species-x")
    text = sf.describe()
    assert "[PROJA]" in text and "species-x" in text and "NOT SET" in text


def test_blank_sheet_protocol_comes_from_facts(facts_file):
    from mousedb.cohort_tools.make_sheets import _protocol
    assert _protocol(["PROJA_01_01"], "Anesthetic") is None      # unset -> empty cell
    sf.set_fact("PROJA", "Anesthetic", "drug-x")
    sf.set_fact("PROJA", "Intended_kd", "60")
    assert _protocol(["PROJA_01_01"], "Anesthetic") == "drug-x"
    assert _protocol("PROJA_01_02", "Intended_kd") == 60          # numbers stay numbers
    assert _protocol([], "Anesthetic") is None


def test_sheet_code_carries_no_study_facts():
    """The generators must read facts, not define them."""
    from mousedb.cohort_tools import make_sheets, odc_builder, update_sheets
    for module in (make_sheets, update_sheets, odc_builder):
        assert not hasattr(module, "PROJECT_DEFAULTS")
        assert not hasattr(module, "STUDY_CONSTANTS")
