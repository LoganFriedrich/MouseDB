"""Long-to-wide pivoting of cell-count connectomics data.

Lifted from the original notebook's Section 6 with minimal edits. The only
change is the optional ``save_dir`` parameter; behavior is otherwise
identical.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd


def pivot_connectivity(
    df: pd.DataFrame,
    name: str,
    value_col: str,
    region_col: str,
    save_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """Pivot a long-format connectomics DataFrame into wide (subject x region_hemi).

    Args:
        df: Long-format input with columns subject_id, hemisphere, ``region_col``,
            and ``value_col``.
        name: Short label used when saving the resulting CSV.
        value_col: Name of the column holding the cell counts (e.g. 'cell_count').
        region_col: Name of the column holding the region label (e.g.
            'region_acronym' for ungrouped data, 'group_name' for eLife-grouped).
        save_dir: Optional directory to write ``<name>.csv`` into.

    Returns:
        Wide-format DataFrame: one row per subject, one column per
        ``{region}_{hemisphere}`` combination, cell counts as values.
    """
    df = df.copy()  # Copy so we can add a combined column without modifying the original
    df["region_hemi"] = df[region_col] + "_" + df["hemisphere"]  # Combine region and hemisphere into a single label like 'GRN_left'
    wide = df.pivot_table(  # Pivot from long to wide format
        index="subject_id",         # One row per subject
        columns="region_hemi",      # Each region_hemisphere combination becomes its own column
        values=value_col,           # Cell counts fill the cells
        aggfunc="sum",              # Sum if any duplicates exist
    )
    if save_dir is not None:
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        wide.to_csv(save_dir / f"{name}.csv")
    return wide
