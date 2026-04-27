"""Tests for mousedb.region_priors.

Validates that the curated predicted-importance orderings are internally consistent
and that region names match the canonical eLife taxonomy (when mousebrain is
installed in the current env).
"""

from mousedb.region_priors import (
    HEMISPHERES,
    PRIORS,
    SKILLED_REACHING,
    RegionPrior,
    _validate_against_elife,
    ordered_hemisphere_columns,
)


def test_priors_registry_contains_skilled_reaching():
    assert "skilled_reaching" in PRIORS
    assert PRIORS["skilled_reaching"] is SKILLED_REACHING


def test_all_priors_are_frozen_dataclasses():
    for prior in PRIORS.values():
        assert isinstance(prior, RegionPrior)
        assert isinstance(prior.ordered_regions, tuple)


def test_no_duplicate_regions_within_any_prior():
    for prior in PRIORS.values():
        assert len(set(prior.ordered_regions)) == len(prior.ordered_regions), (
            f"{prior.activity} has duplicates"
        )


def test_high_priority_cutoff_in_range():
    for prior in PRIORS.values():
        assert 0 <= prior.high_priority_cutoff <= len(prior.ordered_regions), (
            f"{prior.activity} cutoff out of range"
        )


def test_region_names_match_elife_groups():
    bad = _validate_against_elife()
    assert bad == [], f"unknown region names: {bad}"


def test_ordered_hemisphere_columns_preserves_prior_order():
    avail = ["Red Nucleus_both", "Corticospinal_left", "Thalamus_right"]
    cols = ordered_hemisphere_columns(SKILLED_REACHING, available=avail)
    # Corticospinal precedes Red Nucleus in the prior, which precedes Thalamus.
    assert cols == ["Corticospinal_left", "Red Nucleus_both", "Thalamus_right"]


def test_ordered_hemisphere_columns_filters_unknown():
    avail = ["Bogus_region_both", "Red Nucleus_left"]
    cols = ordered_hemisphere_columns(SKILLED_REACHING, available=avail)
    assert cols == ["Red Nucleus_left"]


def test_ordered_hemisphere_columns_no_filter_produces_full_cross_product():
    cols = ordered_hemisphere_columns(SKILLED_REACHING)
    assert len(cols) == len(SKILLED_REACHING.ordered_regions) * len(HEMISPHERES)
