"""A blank cause of death is not the same as nobody knowing.

Most colonies euthanise at the end of study, so an empty cell in the records
means something definite -- the lab knows what it means even though nobody typed
it per animal. Writing "Not recorded" into a shared dataset asserts the opposite,
that the fate of the animal is unknown, which is a stronger and wronger claim
than saying nothing.

What a blank means is a fact about one laboratory, so it is configuration, not
code. And a death during surgery is READ, from the per-surgery survival column
the sheet already carries -- never inferred from a date falling near a surgery
day, which would be us deciding how an animal died.
"""
import pytest

from mousedb.exporters import odc_reaches


def animal(sheet=None, facts=None):
    return odc_reaches._animal(
        "PROJ_01_01", "PROJ_01", sheet or {}, {}, facts or {}, "A Laboratory")


def test_the_sheet_wins_when_it_names_a_cause():
    out = animal(sheet={"ODC_Cause_of_Death": "Found deceased in cage"})
    assert out["Cause_of_Death"] == "Found deceased in cage"


def test_a_surgery_the_animal_did_not_survive_is_read_from_the_records():
    """Not guessed from dates: the sheet records survival per surgery."""
    out = animal(sheet={"Contusion_Survived": "N"})
    assert "survive" in out["Cause_of_Death"].lower()


def test_a_blank_takes_the_laboratory_s_declared_meaning():
    out = animal(facts={"Cause_of_Death": "Euthanasia"})
    assert out["Cause_of_Death"] == "Euthanasia"


def test_a_named_cause_is_not_overwritten_by_the_default():
    out = animal(sheet={"ODC_Cause_of_Death": "Found deceased in cage"},
                 facts={"Cause_of_Death": "Euthanasia"})
    assert out["Cause_of_Death"] == "Found deceased in cage"


def test_a_surgical_death_is_not_overwritten_by_the_default():
    out = animal(sheet={"Contusion_Survived": "N"},
                 facts={"Cause_of_Death": "Euthanasia"})
    assert "survive" in out["Cause_of_Death"].lower()


def test_a_lab_that_has_declared_nothing_still_gets_an_empty_cell():
    """The tool must not invent a meaning for a blank on a lab's behalf.

    Empty here flows on to the exporter's own no-value wording; what it must
    never be is a cause of death this tool made up.
    """
    out = animal()
    assert out["Cause_of_Death"] == ""


def test_the_surviving_animal_is_not_given_a_cause_of_death():
    out = animal(sheet={"Contusion_Survived": "Y"},
                 facts={"Cause_of_Death": "Euthanasia"})
    assert out["Cause_of_Death"] == "Euthanasia", (
        "the end-of-study default still applies; it is not a surgical death")
    assert "survive" not in out["Cause_of_Death"].lower()
