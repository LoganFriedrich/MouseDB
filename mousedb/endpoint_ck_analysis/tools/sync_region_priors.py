"""Refresh the frozen SKILLED_REACHING snapshot in config.py from the live mousedb module.

Prints a ready-to-paste Python block (and, for convenience, a diff-style
summary) comparing the live mousedb ordering to the frozen fallback. Does
NOT modify ``config.py`` automatically -- Logan or a reviewer should paste
the new block by hand so git history shows an intentional refresh.

Usage
-----
    python tools/sync_region_priors.py

Requires
--------
    mousedb installed in the active Python environment.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path


def main() -> int:
    try:
        from mousedb.region_priors import SKILLED_REACHING as LIVE
    except ImportError:
        print("ERROR: mousedb is not installed in this Python environment.")
        print("Activate the environment that has mousedb and rerun.")
        return 1

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from endpoint_ck_analysis.config import FALLBACK_SKILLED_REACHING as FROZEN

    live_regions = list(LIVE.ordered_regions)
    frozen_regions = list(FROZEN.ordered_regions)

    if live_regions == frozen_regions and LIVE.high_priority_cutoff == FROZEN.high_priority_cutoff:
        print("Frozen snapshot already matches live mousedb.region_priors.SKILLED_REACHING.")
        print("No changes needed.")
        return 0

    added = [r for r in live_regions if r not in frozen_regions]
    removed = [r for r in frozen_regions if r not in live_regions]
    print("Difference detected between live mousedb and frozen fallback:")
    if added:
        print("  Added in live:   ", added)
    if removed:
        print("  Removed in live: ", removed)
    if LIVE.high_priority_cutoff != FROZEN.high_priority_cutoff:
        print(f"  Cutoff changed:   frozen={FROZEN.high_priority_cutoff} -> live={LIVE.high_priority_cutoff}")
    print()
    today = _dt.date.today().isoformat()
    print("Paste the following into endpoint_ck_analysis/config.py,")
    print(f"replacing the existing FALLBACK_SKILLED_REACHING block and updating the")
    print(f'"Synced ... on ..." comment to mention {today}:')
    print()
    print("FALLBACK_SKILLED_REACHING = RegionPrior(")
    print(f'    activity="{LIVE.activity}",')
    print(f'    description="{LIVE.description}",')
    print(f"    high_priority_cutoff={LIVE.high_priority_cutoff},")
    print("    ordered_regions=(")
    for r in live_regions:
        print(f'        "{r}",')
    print("    ),")
    print(")")
    return 2  # Non-zero exit so CI notices the drift.


if __name__ == "__main__":
    sys.exit(main())
