"""Exceptions can be created, reordered and removed without editing a file by hand.

The GUI tab edits them through set_rules, so these pin the behaviour the tab depends
on. The ordering cases matter most: a later exception wins over an earlier one, so an
editor that could not reorder would make a wrong order impossible to correct.
"""
import json

import pytest

from mousedb import study_facts as sf


@pytest.fixture
def facts_file(tmp_path, monkeypatch):
    path = tmp_path / "study_facts.json"
    monkeypatch.setenv(sf.ENV_VAR, str(path))
    return path


def _rule(field="F", value="V", needle="marker", only=None):
    r = {"field": field, "value": value, "when_any_value_contains": needle}
    if only:
        r["in_fields"] = only
    return r


def test_rules_round_trip(facts_file):
    sf.set_rules("PROJ", [_rule(), _rule(field="G", value="W", needle="other")])
    got = sf.rules("PROJ")
    assert [r["field"] for r in got] == ["F", "G"]
    assert got[0]["when_any_value_contains"] == "marker"


def test_order_is_preserved_because_later_wins(facts_file):
    sf.set_rules("PROJ", [_rule(value="broad", needle="x"),
                          _rule(value="narrow", needle="xy")])
    assert sf.apply_rules("PROJ", {}, {"a": "xy"})["F"] == "narrow"
    # Swap them the way the tab's "move up" does, and the answer must change.
    swapped = list(reversed(sf.rules("PROJ")))
    sf.set_rules("PROJ", swapped)
    assert sf.apply_rules("PROJ", {}, {"a": "xy"})["F"] == "broad"


def test_an_empty_list_removes_them(facts_file):
    sf.set_rules("PROJ", [_rule()])
    sf.set_rules("PROJ", [])
    assert sf.rules("PROJ") == []


def test_a_rule_that_could_never_match_is_not_stored(facts_file):
    """Storing one would look like a setting that does something."""
    sf.set_rules("PROJ", [_rule(needle=""), _rule(field=""), _rule()])
    assert len(sf.rules("PROJ")) == 1


def test_setting_rules_does_not_disturb_the_facts(facts_file):
    sf.set_fact("PROJ", "SpeciesTyp", "species")
    sf.set_rules("PROJ", [_rule()])
    assert sf.facts("PROJ")["SpeciesTyp"] == "species"
    sf.set_rules("PROJ", [])
    assert sf.facts("PROJ")["SpeciesTyp"] == "species", (
        "removing every exception must not remove the fact it was an exception to")


def test_the_file_stays_readable_json(facts_file):
    sf.set_fact("PROJ", "SpeciesTyp", "species")
    sf.set_rules("PROJ", [_rule(only=["ColumnA", "ColumnB"])])
    data = json.loads(facts_file.read_text(encoding="utf-8"))
    assert data["PROJ"][sf.RULES_KEY][0]["in_fields"] == ["ColumnA", "ColumnB"]
    assert data["PROJ"]["SpeciesTyp"] == "species"


def test_the_tab_can_be_imported_without_a_display():
    """The tab must not do any work at import time -- a headless machine imports the
    GUI module during tests and packaging."""
    pytest.importorskip("PyQt5")
    from mousedb.gui import study_facts_tab
    assert hasattr(study_facts_tab, "StudyFactsTab")
    assert "terminal" in study_facts_tab.__doc__, (
        "the module should say why it exists: the command line must never be the "
        "only way to do something")
