"""Study-wide ODC facts live in a local file, never in source (mousedb.study_facts).

WHY: these facts (strain, supplier, study leader, injury device) belong to one
lab's study. They would otherwise be typed into the sheet generators, which put a
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


# ---------------------------------------------------------------------------
# A damaged file is refused, never read as "nothing set" and never written over.
# WHY: it would otherwise read as empty, so every export wrote its study-fact columns
# blank without a word, and the next Set wrote back a file holding only that
# one fact -- every other project's facts and exception rules gone.
# ---------------------------------------------------------------------------

DAMAGED = ('{"default": {"SpeciesTyp": "species-x"},\n'
           ' "PROJA": {"SpeciesStrainTyp": "strain-a",,\n'
           '           "_rules": [{"field": "SpeciesStrainTyp", "value": "strain-b",\n'
           '                       "when_any_value_contains": "marker"}]}}\n')


def test_a_damaged_file_is_refused_by_every_reader(facts_file):
    from mousedb import config
    facts_file.write_text(DAMAGED, encoding="utf-8")
    for read in (lambda: sf.facts("PROJA"), sf.read_all, lambda: sf.get("PROJA", "SpeciesTyp"),
                 lambda: sf.rules("PROJA"), lambda: sf.unset_fields("PROJA"), sf.describe):
        with pytest.raises(config.SettingsFileDamaged) as e:
            read()
        assert str(facts_file) in str(e.value)
    # A ConfigError, so the mousedb command prints it as one [FAIL] line.
    assert issubclass(config.SettingsFileDamaged, config.ConfigError)


def test_a_damaged_file_is_never_written_over(facts_file):
    from mousedb import config
    facts_file.write_text(DAMAGED, encoding="utf-8")
    for write in (lambda: sf.set_fact("PROJB", "StudyLeader", "someone"),
                  lambda: sf.set_fact("PROJA", "SpeciesStrainTyp", None),
                  lambda: sf.set_rules("PROJB", [{"field": "SpeciesTyp", "value": "v",
                                                  "when_any_value_contains": "t"}])):
        with pytest.raises(config.SettingsFileDamaged):
            write()
        assert facts_file.read_text(encoding="utf-8") == DAMAGED
    copies = list(facts_file.parent.glob(facts_file.name + ".damaged-*"))
    assert copies and copies[0].read_text(encoding="utf-8") == DAMAGED


def test_a_missing_or_empty_file_is_nothing_set(facts_file):
    assert sf.read_all() == {}
    facts_file.write_text("", encoding="utf-8")
    assert sf.read_all() == {} and sf.facts("PROJA") == {}


def test_saving_one_fact_keeps_every_other_project_and_rule(facts_file):
    rule = {"field": "SpeciesStrainTyp", "value": "strain-b", "when_any_value_contains": "m"}
    sf.set_fact("default", "SpeciesTyp", "species-x")
    sf.set_fact("PROJA", "SpeciesStrainTyp", "strain-a")
    sf.set_rules("PROJA", [rule])
    sf.set_fact("PROJB", "StudyLeader", "someone")
    data = json.loads(facts_file.read_text(encoding="utf-8"))
    assert data == {"default": {"SpeciesTyp": "species-x"},
                    "PROJA": {"SpeciesStrainTyp": "strain-a", "_rules": [rule]},
                    "PROJB": {"StudyLeader": "someone"}}
    # No temporary file is left beside it.
    assert [p.name for p in facts_file.parent.iterdir()] == [facts_file.name]


def test_a_section_that_is_not_named_values_is_refused_not_replaced(facts_file):
    from mousedb import config
    facts_file.write_text(json.dumps({"PROJA": "oops"}), encoding="utf-8")
    with pytest.raises(config.SettingsFileDamaged):
        sf.set_fact("PROJA", "StudyLeader", "someone")
    assert json.loads(facts_file.read_text(encoding="utf-8")) == {"PROJA": "oops"}


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
