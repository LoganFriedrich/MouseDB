"""A recording is attributed to its cohort, including when it holds several animals.

A multi-animal recording names every animal in it, one after another. The
original parser pulled all the letters out of the name and all the digits out
of the name and glued them together, which turned a collage of seven animals
from one cohort into a cohort of its own with a name like
"PROJPROJPROJPROJPROJPROJPROJ_05".

The visible symptom was a nonsense row in the status table. The real cost was
underneath it: those recordings were counted against a cohort that does not
exist, so the workload figures for the cohort they actually belong to were
short by that many.
"""
import pytest

from mousedb.data_status import _cohort_of_video as cohort_of


def test_a_single_animal_recording():
    assert cohort_of("20250624_PROJ0115_P2") == "PROJ_01"


def test_a_collage_belongs_to_its_animals_cohort_not_a_cohort_of_its_own():
    """The defect this exists for."""
    name = "20220811_PROJ0501PROJ0502PROJ0503_P1"
    assert cohort_of(name) == "PROJ_05"


def test_a_long_collage_is_still_one_cohort():
    animals = "".join("PROJ05%02d" % n for n in range(1, 8))
    assert cohort_of("20220811_%s_P1" % animals) == "PROJ_05"


def test_a_single_letter_project_is_read_as_written():
    """Recordings named before ids were normalised still have to land somewhere.

    They are not wrong, they are just not normalised yet, and showing them as
    themselves is how anyone notices they exist.
    """
    assert cohort_of("20220811_D13_P3") == "D_13"


def test_animals_from_different_cohorts_in_one_name_refuse_to_guess():
    """No single answer exists, so there must not be a single answer.

    Picking the first would silently credit one cohort with another's work.
    """
    assert cohort_of("20220811_PROJ0501PROJ0601_P1") == "?"


@pytest.mark.parametrize("name", [
    "20220811_0001_P1",      # digits but no project
    "20220811__P1",          # nothing between the separators
    "nodatehere",            # no second field at all
    "20220811",
    "",
])
def test_a_name_with_no_readable_animal_refuses_rather_than_inventing_a_cohort(name):
    assert cohort_of(name) == "?"


def test_a_refusal_is_a_value_the_callers_already_drop():
    """"?" is the established "unknown" in this module, not a new convention."""
    import inspect

    from mousedb import data_status
    source = inspect.getsource(data_status.status)
    assert 'c != "?"' in source, (
        "status() must keep excluding unknown cohorts from the per-cohort table")
