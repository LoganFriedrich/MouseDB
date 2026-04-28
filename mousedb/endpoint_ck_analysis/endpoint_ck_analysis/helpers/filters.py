"""Subject-intersection filtering between data blocks.

Used to restrict a DataFrame to subjects that appear in another set (for
example, restricting the full kinematic dataframe to only the mice that
also have connectivity data).
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import pandas as pd


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
    filtered = df[df["subject_id"].isin(shared_subjects)]
    if verbose:
        print(f"{name} includes these subjects: {filtered['subject_id'].unique()}")
    if save_dir is not None:
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        filtered.to_csv(save_dir / f"{name}.csv", index=False)
    return filtered
