"""Canonical predicted-importance orderings of eLife brain-region groups, by activity.

One source of truth for "which regions might matter for X?" questions. Any downstream
plot, recipe, or CLI should import from here instead of hardcoding its own list, so
that re-ordering a heatmap in one place re-orders it everywhere.

Today: skilled forelimb reaching. Future: add new `RegionPrior` constants in this file
and register them in `PRIORS`. No schema change, no downstream code touch.

The names in `ordered_regions` must match `mousebrain.region_mapping.ELIFE_GROUPS` keys
(validated by `_validate_against_elife()`). Only `[Unmapped]` is allowed as a sentinel
for acronyms that didn't aggregate into any eLife group.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

# Sentinel names that may appear in an ordering but are NOT eLife group names.
_SENTINELS = frozenset({"[Unmapped]"})

HEMISPHERES: Tuple[str, ...] = ("both", "left", "right")


@dataclass(frozen=True)
class RegionPrior:
    """Predicted-importance ordering of eLife region groups for one activity.

    Attributes:
        activity: Short identifier used as the key in `PRIORS` (e.g. "skilled_reaching").
        description: Human-readable description of the activity.
        ordered_regions: eLife group names in predicted-importance order, highest first.
            May end with sentinels ("[Unmapped]", "Unused"). "Unused" is itself a real
            eLife group for regions eLife explicitly excluded.
        high_priority_cutoff: Index at which predicted-important ends and
            "kept-for-contrast" begins. Plots can draw a visual separator here.
    """
    activity: str
    description: str
    ordered_regions: Tuple[str, ...]
    high_priority_cutoff: int


SKILLED_REACHING = RegionPrior(
    activity="skilled_reaching",
    description="Skilled unilateral forelimb reaching (single-pellet task).",
    high_priority_cutoff=10,
    ordered_regions=(
        # -- predicted important for skilled reaching --
        "Corticospinal",                       # primary driver, voluntary motor output
        "Red Nucleus",                          # rubrospinal, skilled distal limb control
        "Magnocellular Reticular Nucleus",      # reticulospinal
        "Gigantocellular Reticular Nucleus",    # reticulospinal, proximal/postural
        "Medullary Reticular Nuclei",           # reticulospinal
        "Lateral Reticular Nuclei",             # cerebellar-reticular relay
        "Cerebellospinal Nuclei",               # deep cerebellar output, timing
        "Vestibular Nuclei",                    # postural stability during reach
        "Pontine Reticular Nuclei",             # reticulospinal axis
        "Raphe Nuclei",                         # modulatory, motor recovery
        # -- kept for contrast (lower predicted importance) --
        "Pontine Trigeminal Area",
        "Medullary Trigeminal Area",
        "Perihypoglossal Area",
        "Superior Olivary Complex",
        "Parabrachial / Pedunculopontine",
        "Pontine Central Gray Area",
        "Midbrain Reticular Nuclei",
        "Midbrain Midline Nuclei",
        "Periaqueductal Gray",
        "Dorsal Reticular Nucleus",
        "Solitariospinal Area",
        "Hypothalamic Lateral Area",
        "Hypothalamic Medial Area",
        "Hypothalamic Periventricular Zone",
        "Thalamus",
        # -- sentinel and explicit-exclude bucket last --
        "[Unmapped]",
        "Unused",
    ),
)


PRIORS: "dict[str, RegionPrior]" = {
    SKILLED_REACHING.activity: SKILLED_REACHING,
}


def ordered_hemisphere_columns(
    prior: RegionPrior,
    available: Optional[List[str]] = None,
    hemispheres: Tuple[str, ...] = HEMISPHERES,
) -> List[str]:
    """Build `{region}_{hemisphere}` column names in prior order.

    Useful for sorting columns of a pivoted DataFrame (e.g. one whose columns are
    `Red Nucleus_both`, `Red Nucleus_left`, ...) so plots show rows/columns in
    predicted-importance order.

    Args:
        prior: The ordering to use.
        available: If given (e.g. `df.columns.tolist()`), filters to columns that
            actually exist. Order is still driven by `prior`.
        hemispheres: Which hemispheres to include, in the order they should appear
            within each region. Defaults to ("both", "left", "right").
    """
    cols = [f"{r}_{h}" for r in prior.ordered_regions for h in hemispheres]
    if available is not None:
        avail = set(available)
        cols = [c for c in cols if c in avail]
    return cols


def _validate_against_elife() -> List[str]:
    """Return list of region names across all PRIORS that aren't in ELIFE_GROUPS.

    Soft import: returns [] when mousebrain isn't installed, so mousedb stays
    importable in envs without mousebrain. Used by the accompanying unit test;
    not called at module import so installation order doesn't matter.
    """
    try:
        from mousebrain.region_mapping import ELIFE_GROUPS
    except ImportError:
        return []
    known = set(ELIFE_GROUPS.keys())
    bad = []
    for prior in PRIORS.values():
        for name in prior.ordered_regions:
            if name in _SENTINELS:
                continue
            if name not in known:
                bad.append(f"{prior.activity}: {name!r}")
    return bad
