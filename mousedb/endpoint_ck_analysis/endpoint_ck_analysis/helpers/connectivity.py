"""Long-to-wide pivoting of cell-count connectomics data.

Lifted from the original notebook's Section 6 with minimal edits. The only
change is the optional ``save_dir`` parameter; behavior is otherwise
identical.
"""
from __future__ import annotations  # postpone-annotation evaluation; lets us reference types without runtime cost

from pathlib import Path  # pathlib: object-oriented filesystem paths
from typing import Optional  # Optional[X] is shorthand for Union[X, None]; documents that None is a valid value

import pandas as pd  # pandas: dataframe library, treats data like a spreadsheet


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
    df = df.copy()                                                                 # copy so adding the combined column doesn't mutate the caller's dataframe
    df["region_hemi"] = df[region_col] + "_" + df["hemisphere"]                    # string-concatenate region label and hemisphere into one combined label like 'GRN_left' or 'M1_right'
    wide = df.pivot_table(                                                          # pivot_table: long-to-wide reshape; aggregates duplicates with aggfunc
        index="subject_id",         # one row per subject in the output
        columns="region_hemi",      # one column per (region, hemisphere) combination
        values=value_col,           # cell values come from this column
        aggfunc="sum",              # if the same (subject, region_hemi) appears more than once in the input, sum them; expected to be unique but defensive
    )
    if save_dir is not None:                                                        # caller wants a CSV side-effect
        save_dir = Path(save_dir)                                                   # cast string -> Path so the / operator works
        save_dir.mkdir(parents=True, exist_ok=True)                                 # ensure directory exists; parents=True creates intermediates; exist_ok=True is a no-op if already there
        wide.to_csv(save_dir / f"{name}.csv")                                       # write the pivoted dataframe; subject_id index is included by default
    return wide
