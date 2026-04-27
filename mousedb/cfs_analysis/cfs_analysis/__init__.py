"""CFS Analysis: connectivity-vs-kinematics in SCI recovery.

Importing this package soft-imports ``mousedb.region_priors`` when available
and falls back to a frozen snapshot in ``config.py`` otherwise. Downstream
code should import ``SKILLED_REACHING`` and ``ordered_hemisphere_columns``
from this package, not from mousedb directly, so that the tool works in
environments where mousedb is not installed (e.g., when someone receives
the folder as a handoff and opens it on a fresh machine).
"""
from __future__ import annotations

__version__ = "0.1.0"

# Attempt to use the live mousedb region priors. If mousedb is not installed,
# use the frozen copy pinned in config.py. Callers import via this module.
try:
    from mousedb.region_priors import (
        SKILLED_REACHING,
        ordered_hemisphere_columns,
        RegionPrior,
        HEMISPHERES,
    )
    _REGION_PRIOR_SOURCE = "mousedb"
except ImportError:
    from .config import (
        FALLBACK_SKILLED_REACHING as SKILLED_REACHING,
        fallback_ordered_hemisphere_columns as ordered_hemisphere_columns,
        RegionPrior,
        HEMISPHERES,
    )
    _REGION_PRIOR_SOURCE = "fallback"


def region_prior_source() -> str:
    """Return 'mousedb' if live module was used, 'fallback' if frozen snapshot."""
    return _REGION_PRIOR_SOURCE


__all__ = [
    "__version__",
    "SKILLED_REACHING",
    "ordered_hemisphere_columns",
    "RegionPrior",
    "HEMISPHERES",
    "region_prior_source",
]
