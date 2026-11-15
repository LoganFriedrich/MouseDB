"""
Tests for mousedb.phases.assign_phases_for_cohort.

Fixtures are two realistic cohort test-day patterns (invented dates). These
tests freeze the expected phase assignment so future regressions are caught.
"""

from datetime import date

import pytest

from mousedb.phases import (
    PRE_INJURY_TEST_COUNT,
    POST_REHAB_TEST_COUNT,
    PhaseAssignment,
    assign_phases_for_cohort,
)


COHORT_A = [
    (date(2025, 1, 23), "F"), (date(2025, 1, 24), "F"), (date(2025, 1, 27), "F"),
    (date(2025, 1, 28), "P"), (date(2025, 1, 29), "P"), (date(2025, 1, 30), "P"),
    (date(2025, 1, 31), "P"), (date(2025, 2, 3), "P"), (date(2025, 2, 4), "P"),
    (date(2025, 2, 14), "P"), (date(2025, 2, 21), "P"), (date(2025, 2, 28), "P"),
    (date(2025, 3, 7), "P"),
    (date(2025, 3, 10), "E"), (date(2025, 3, 11), "E"), (date(2025, 3, 12), "E"),
    (date(2025, 3, 13), "E"), (date(2025, 3, 14), "E"), (date(2025, 3, 17), "E"),
    (date(2025, 3, 18), "F"), (date(2025, 3, 19), "F"), (date(2025, 3, 20), "F"),
    (date(2025, 3, 21), "F"), (date(2025, 3, 24), "F"),
    (date(2025, 3, 25), "P"), (date(2025, 3, 26), "P"), (date(2025, 3, 27), "P"),
    (date(2025, 3, 28), "P"),
]

COHORT_B = [
    (date(2025, 5, 22), "F"), (date(2025, 5, 23), "F"), (date(2025, 5, 26), "F"),
    (date(2025, 5, 27), "P"), (date(2025, 5, 28), "P"), (date(2025, 5, 29), "P"),
    (date(2025, 5, 30), "P"), (date(2025, 5, 31), "P"), (date(2025, 6, 2), "P"),
    (date(2025, 6, 3), "P"), (date(2025, 6, 4), "P"), (date(2025, 6, 5), "P"),
    (date(2025, 6, 6), "P"),
    (date(2025, 6, 20), "P"), (date(2025, 6, 27), "P"), (date(2025, 7, 2), "P"),
    (date(2025, 7, 11), "P"),
    (date(2025, 7, 14), "E"), (date(2025, 7, 15), "E"), (date(2025, 7, 16), "E"),
    (date(2025, 7, 17), "E"), (date(2025, 7, 18), "E"), (date(2025, 7, 21), "E"),
    (date(2025, 7, 22), "F"), (date(2025, 7, 23), "F"), (date(2025, 7, 24), "F"),
    (date(2025, 7, 25), "F"), (date(2025, 7, 27), "F"),
    (date(2025, 7, 28), "P"), (date(2025, 7, 29), "P"), (date(2025, 7, 30), "P"),
    (date(2025, 7, 31), "P"),
]


def _by_date(assignments):
    return {a.session_date: a for a in assignments}


class TestCohortAEndToEnd:
    """Cohort A: short pre-injury P block (6 days), 4 weekly post-injury tests, full rehab."""

    @pytest.fixture
    def assignments(self):
        return _by_date(assign_phases_for_cohort(COHORT_A))

    def test_flat_training(self, assignments):
        for d in [date(2025, 1, 23), date(2025, 1, 24), date(2025, 1, 27)]:
            assert assignments[d].test_phase == "Flat_Training"
            assert assignments[d].phase_group == "Training"

    def test_pillar_training(self, assignments):
        for d in [date(2025, 1, 28), date(2025, 1, 29), date(2025, 1, 30)]:
            assert assignments[d].test_phase == "Pillar_Training"
            assert assignments[d].phase_group == "Training"

    def test_pillar_baseline_is_last_three(self, assignments):
        for d in [date(2025, 1, 31), date(2025, 2, 3), date(2025, 2, 4)]:
            assert assignments[d].test_phase == "Pillar"
            assert assignments[d].phase_group == "Baseline"

    def test_post_injury_tests(self, assignments):
        assert assignments[date(2025, 2, 14)].test_phase == "Post_Injury_1"
        assert assignments[date(2025, 2, 14)].phase_group == "Post_Injury_1"
        for d, n in [(date(2025, 2, 21), 2), (date(2025, 2, 28), 3), (date(2025, 3, 7), 4)]:
            assert assignments[d].test_phase == f"Post_Injury_{n}"
            assert assignments[d].phase_group == "Post_Injury_2-4"

    def test_rehab_easy(self, assignments):
        for d in [date(2025, 3, 10), date(2025, 3, 11), date(2025, 3, 12),
                  date(2025, 3, 13), date(2025, 3, 14), date(2025, 3, 17)]:
            assert assignments[d].test_phase == "Rehab_Easy"
            assert assignments[d].phase_group == "Rehab_Easy"

    def test_rehab_flat(self, assignments):
        for d in [date(2025, 3, 18), date(2025, 3, 19), date(2025, 3, 20),
                  date(2025, 3, 21), date(2025, 3, 24)]:
            assert assignments[d].test_phase == "Rehab_Flat"
            assert assignments[d].phase_group == "Rehab_Flat"

    def test_rehab_pillar_last_three_are_post_rehab_test(self, assignments):
        assert assignments[date(2025, 3, 25)].phase_group == "Rehab_Pillar_Early"
        for d in [date(2025, 3, 26), date(2025, 3, 27), date(2025, 3, 28)]:
            assert assignments[d].test_phase == "Rehab_Pillar"
            assert assignments[d].phase_group == "Post_Rehab_Test"


class TestCohortBEndToEnd:
    """Cohort B: longer pre-injury P block (10 days) and irregular Post_Injury_3 (day 45 not 46)."""

    @pytest.fixture
    def assignments(self):
        return _by_date(assign_phases_for_cohort(COHORT_B))

    def test_pillar_baseline_still_last_three(self, assignments):
        for d in [date(2025, 6, 4), date(2025, 6, 5), date(2025, 6, 6)]:
            assert assignments[d].test_phase == "Pillar"
            assert assignments[d].phase_group == "Baseline"

    def test_irregular_post_injury_3(self, assignments):
        """Day 45 (2025-07-02) is 1 day before scheduled day 46. Should still be PI_3."""
        a = assignments[date(2025, 7, 2)]
        assert a.test_phase == "Post_Injury_3"
        assert a.phase_group == "Post_Injury_2-4"

    def test_last_dates_are_post_rehab_test(self, assignments):
        """The last dates, after a one-day gap in the rehab flat block, fall in the final P block."""
        for d in [date(2025, 7, 29), date(2025, 7, 30), date(2025, 7, 31)]:
            assert assignments[d].test_phase == "Rehab_Pillar"
            assert assignments[d].phase_group == "Post_Rehab_Test"


class TestStructuralInvariants:
    """Properties that must hold for any well-formed cohort."""

    @pytest.mark.parametrize("fixture", [COHORT_A, COHORT_B])
    def test_exactly_three_baseline_days(self, fixture):
        assignments = assign_phases_for_cohort(fixture)
        baseline = [a for a in assignments if a.phase_group == "Baseline"]
        assert len(baseline) == PRE_INJURY_TEST_COUNT

    @pytest.mark.parametrize("fixture", [COHORT_A, COHORT_B])
    def test_exactly_three_post_rehab_test_days(self, fixture):
        assignments = assign_phases_for_cohort(fixture)
        prt = [a for a in assignments if a.phase_group == "Post_Rehab_Test"]
        assert len(prt) == POST_REHAB_TEST_COUNT

    @pytest.mark.parametrize("fixture", [COHORT_A, COHORT_B])
    def test_exactly_four_post_injury_tests(self, fixture):
        assignments = assign_phases_for_cohort(fixture)
        pi = [a for a in assignments if a.test_phase.startswith("Post_Injury_")]
        assert len(pi) == 4

    @pytest.mark.parametrize("fixture", [COHORT_A, COHORT_B])
    def test_one_assignment_per_unique_date(self, fixture):
        assignments = assign_phases_for_cohort(fixture)
        unique_dates = {d for d, _ in fixture}
        assert len(assignments) == len(unique_dates)

    @pytest.mark.parametrize("fixture", [COHORT_A, COHORT_B])
    def test_no_unscheduled_in_clean_cohort(self, fixture):
        """Clean cohort data should not produce any Unscheduled labels."""
        assignments = assign_phases_for_cohort(fixture)
        unscheduled = [a for a in assignments if a.test_phase == "Unscheduled"]
        assert unscheduled == []


class TestEdgeCases:
    def test_empty_input(self):
        assert assign_phases_for_cohort([]) == []

    def test_pre_injury_only_cohort(self):
        """Cohort still in pre-injury phase (no injury gap yet)."""
        early = [
            (date(2026, 1, 5), "F"), (date(2026, 1, 6), "F"),
            (date(2026, 1, 9), "P"), (date(2026, 1, 10), "P"), (date(2026, 1, 11), "P"),
        ]
        out = assign_phases_for_cohort(early)
        assert out[0].test_phase == "Flat_Training"
        for a in out[2:]:
            assert a.test_phase == "Pillar"
            assert a.phase_group == "Baseline"

    def test_dedupe_dominant_tray(self):
        """Same date with multiple tray entries collapses to dominant tray."""
        mixed = [
            (date(2026, 1, 5), "P"), (date(2026, 1, 5), "P"), (date(2026, 1, 5), "F"),
        ]
        out = assign_phases_for_cohort(mixed)
        assert len(out) == 1
        assert out[0].session_date == date(2026, 1, 5)

    def test_fewer_than_three_pillar_days_all_baseline(self):
        """If pre-injury P block has < 3 days, all become Baseline."""
        sparse = [
            (date(2026, 1, 5), "P"), (date(2026, 1, 6), "P"),
            (date(2026, 1, 20), "P"),
        ]
        out = assign_phases_for_cohort(sparse)
        baselines = [a for a in out if a.phase_group == "Baseline"]
        assert len(baselines) == 2
