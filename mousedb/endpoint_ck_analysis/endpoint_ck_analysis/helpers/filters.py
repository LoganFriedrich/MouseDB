"""Subject-intersection filtering between data blocks.

Used to restrict a DataFrame to subjects that appear in another set (for
example, restricting the full kinematic dataframe to only the mice that
also have connectivity data).
"""
from __future__ import annotations  # postpone-annotation evaluation; allows forward refs in type hints

from pathlib import Path  # pathlib: object-oriented filesystem paths
from typing import Iterable, Optional  # Iterable: any object you can loop over (list, set, generator); Optional[X] = X or None

import pandas as pd  # pandas: dataframe library


def filter_to_shared(
    df: pd.DataFrame,
    shared_subjects: Iterable[str],
    name: str,
    save_dir: Optional[Path] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """Filter ``df`` to rows whose subject_id is in ``shared_subjects``.

    Args:
        df: DataFrame with a 'subject_id' column.
        shared_subjects: Iterable of subject IDs to keep.
        name: Short label for reporting and optional CSV filename.
        save_dir: Optional directory to write ``<name>.csv`` into.
        verbose: If True, print the included subject IDs (ASCII-safe for
            Windows consoles per mousedb convention).

    Returns:
        Filtered DataFrame.
    """
    filtered = df[df["subject_id"].isin(shared_subjects)]                          # boolean indexing: .isin() returns True for rows whose subject_id is in the keep-set; df[bool_mask] selects matching rows
    if verbose:                                                                    # caller wants a console summary
        print(f"{name} includes these subjects: {filtered['subject_id'].unique()}")  # .unique() returns the distinct subject IDs as a numpy array
    if save_dir is not None:                                                       # caller wants CSV side-effect
        save_dir = Path(save_dir)                                                  # cast to Path
        save_dir.mkdir(parents=True, exist_ok=True)                                # ensure directory exists
        filtered.to_csv(save_dir / f"{name}.csv", index=False)                     # write CSV; index=False skips pandas' default 0,1,2,... row numbers
    return filtered
