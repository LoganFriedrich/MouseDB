"""Per-animal exceptions to a study fact are configuration, never code.

A study fact is only nearly constant: a colony can be one strain except for the animals
on a transgenic line. Which line, what it is called, and how it is spelled in that lab's
sheets are facts about ONE lab. The tool must therefore know only the SHAPE of an
exception and nothing about any particular one -- otherwise the next lab to use this
finds someone else's strain names compiled into it.
"""
import json

import pytest

from mousedb import study_facts as sf


@pytest.fixture
def facts_file(tmp_path, monkeypatch):
    path = tmp_path / "study_facts.json"
    monkeypatch.setenv(sf.ENV_VAR, str(path))

    def write(data):
        path.write_text(json.dumps(data), encoding="utf-8")
        return path
    return write


def test_the_usual_value_stands_when_nothing_matches(facts_file):
    facts_file({"PROJ": {"SpeciesStrainTyp": "usual-strain", sf.RULES_KEY: [
        {"field": "SpeciesStrainTyp", "value": "other-strain",
         "when_any_value_contains": "marker"}]}})
    got = sf.apply_rules("PROJ_02", sf.facts("PROJ_02"), {"Line": "nothing special"})
    assert got["SpeciesStrainTyp"] == "usual-strain"


def test_the_exception_wins_for_an_animal_whose_records_carry_the_marker(facts_file):
    facts_file({"PROJ": {"SpeciesStrainTyp": "usual-strain", sf.RULES_KEY: [
        {"field": "SpeciesStrainTyp", "value": "other-strain",
         "when_any_value_contains": "marker"}]}})
    got = sf.apply_rules("PROJ_02", sf.facts("PROJ_02"),
                         {"Line": "Marker-F46", "Cage": "3"})
    assert got["SpeciesStrainTyp"] == "other-strain", (
        "the marker appears in this animal's own records, so the exception applies")


def test_matching_ignores_case_because_sheets_are_typed_by_hand(facts_file):
    facts_file({"PROJ": {sf.RULES_KEY: [
        {"field": "F", "value": "V", "when_any_value_contains": "MaRkEr"}]}})
    assert sf.apply_rules("PROJ", {}, {"any": "xxmarkerxx"})["F"] == "V"


def test_a_rule_can_be_restricted_to_named_columns(facts_file):
    facts_file({"PROJ": {sf.RULES_KEY: [
        {"field": "F", "value": "V", "when_any_value_contains": "marker",
         "in_fields": ["Line"]}]}})
    assert "F" not in sf.apply_rules("PROJ", {}, {"Comments": "marker"}), (
        "the marker was in a column the rule does not look at"
    )
    assert sf.apply_rules("PROJ", {}, {"Line": "marker"})["F"] == "V"


def test_a_later_rule_wins_so_a_narrow_exception_can_follow_a_broad_one(facts_file):
    facts_file({"PROJ": {sf.RULES_KEY: [
        {"field": "F", "value": "broad", "when_any_value_contains": "x"},
        {"field": "F", "value": "narrow", "when_any_value_contains": "xy"}]}})
    assert sf.apply_rules("PROJ", {}, {"a": "xy"})["F"] == "narrow"
    assert sf.apply_rules("PROJ", {}, {"a": "x"})["F"] == "broad"


def test_rules_are_not_exported_as_a_fact(facts_file):
    facts_file({"PROJ": {"SpeciesTyp": "species", sf.RULES_KEY: [
        {"field": "F", "value": "V", "when_any_value_contains": "m"}]}})
    assert sf.RULES_KEY not in sf.facts("PROJ"), (
        "the rule list is configuration about facts, not a fact -- it must never "
        "reach a column")


def test_a_malformed_rule_is_ignored_rather_than_raising(facts_file):
    facts_file({"PROJ": {sf.RULES_KEY: [
        "not a dict", {}, {"field": "F"},
        {"field": "G", "value": "V", "when_any_value_contains": ""}]}})
    got = sf.apply_rules("PROJ", {"keep": "me"}, {"a": "b"})
    assert got == {"keep": "me"}, (
        "a study-facts file edited by hand must never stop an export")


def test_no_lab_specific_value_is_compiled_into_the_module():
    """The module may describe the shape of an exception; it must not contain one."""
    from pathlib import Path
    source = Path(sf.__file__).read_text(encoding="utf-8")
    for leaked in ("C57BL", "Emx", "Jackson", "Infinite Horizon"):
        assert leaked not in source, (
            "%s is a lab's own fact and belongs in that lab's config file" % leaked)
