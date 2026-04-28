"""Kinematic feature selection, aggregation, and proportion helpers.

These functions are lifted from the original notebook's Section 6 with
minimal edits. The behavior is preserved so results match the monolithic
notebook byte-for-byte. Changes from the notebook version:

- ``prefer_calibrated_units`` and ``get_kinematic_cols`` read METADATA_COLS
  and UNIT_SUFFIX_PREFERENCE from ``config`` instead of module-level sets.
- ``aggregate_*`` and ``compute_*_proportions`` take an optional
  ``save_dir`` argument to control where the per-result CSV side-effects
  land. ``None`` (default) skips writing; the notebooks pass a path when
  they want the CSV for reference.

Everything else is unchanged from Logan's notebook code.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import pandas as pd

from ..config import METADATA_COLS, UNIT_SUFFIX_PREFERENCE


def prefer_calibrated_units(columns):
    """Given a list of column names, drop any whose unit is a less-preferred duplicate of another column."""
    by_base = {}  # Map from semantic base name (column name with unit suffix stripped) to list of (column, suffix) tuples
    for col in columns:
        matched = False
        for suffix in UNIT_SUFFIX_PREFERENCE:  # Check each recognized unit suffix in preference order
            if col.endswith(suffix):
                base = col[:-len(suffix)]  # Strip the suffix to get the semantic base, e.g. 'max_extent_mm' -> 'max_extent'
                by_base.setdefault(base, []).append((col, suffix))  # Group this column under its semantic base
                matched = True
                break
        if not matched:
            by_base.setdefault(col, []).append((col, None))  # No recognized unit suffix; use the column name itself as the base
    keep = []  # Columns that survive the deduplication
    for base, entries in by_base.items():
        if len(entries) == 1:
            keep.append(entries[0][0])  # Only one column maps to this base, no conflict
        else:
            # Sort by preference order; lower index wins
            entries.sort(key=lambda e: UNIT_SUFFIX_PREFERENCE.index(e[1]) if e[1] else len(UNIT_SUFFIX_PREFERENCE))
            keep.append(entries[0][0])  # Keep only the most-preferred unit for this base
    return keep


def get_kinematic_cols(df: pd.DataFrame) -> List[str]:
    """Return every numeric column that is a kinematic feature (not metadata, not a redundant-unit duplicate)."""
    numeric_cols = df.select_dtypes(include="number").columns.tolist()  # All numeric columns in the dataframe
    non_metadata = [c for c in numeric_cols if c not in METADATA_COLS]  # Filter out anything classified as metadata
    return prefer_calibrated_units(non_metadata)  # Additionally drop less-preferred unit duplicates (keep _mm over _pixels, etc.)


def _agg_dict_for(kinematic_cols: List[str]) -> dict:
    """Build the mean/std/median/q25/q75 aggregation spec for a column list.

    Broken out so ``aggregate_kinematics`` and ``aggregate_kinematics_by_contact``
    share one source of truth for what gets computed per kinematic feature.
    """
    return {col: [
        ("mean", "mean"),
        ("std", "std"),
        ("median", "median"),
        ("q25", lambda x: x.quantile(0.25)),
        ("q75", lambda x: x.quantile(0.75)),
    ] for col in kinematic_cols}


def aggregate_kinematics(df: pd.DataFrame, name: str, save_dir: Optional[Path] = None) -> pd.DataFrame:
    """Aggregate kinematics at the three-way outcome grain (missed/displaced/retrieved).

    Grouped by (subject_id, phase_group, outcome_group). Returns a multi-indexed
    DataFrame with per-feature mean/std/median/q25/q75 columns.
    """
    kinematic_cols = get_kinematic_cols(df)
    agg_dict = _agg_dict_for(kinematic_cols)
    aggregated = df.groupby(["subject_id", "phase_group", "outcome_group"]).agg(**{
        f"{col}_{stat_name}": (col, stat_func)
        for col, stat_list in agg_dict.items()
        for stat_name, stat_func in stat_list
    })
    if save_dir is not None:
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        aggregated.to_csv(save_dir / f"{name}.csv")
    return aggregated


def aggregate_kinematics_by_contact(df: pd.DataFrame, name: str, save_dir: Optional[Path] = None) -> pd.DataFrame:
    """Same aggregation grouped by contact_group (missed vs contacted).

    Useful when retrieved reaches are too sparse for stable per-subject summary
    statistics. Grouped by (subject_id, phase_group, contact_group).
    """
    kinematic_cols = get_kinematic_cols(df)
    agg_dict = _agg_dict_for(kinematic_cols)
    aggregated = df.groupby(["subject_id", "phase_group", "contact_group"]).agg(**{
        f"{col}_{stat_name}": (col, stat_func)
        for col, stat_list in agg_dict.items()
        for stat_name, stat_func in stat_list
    })
    if save_dir is not None:
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        aggregated.to_csv(save_dir / f"{name}.csv")
    return aggregated


def compute_outcome_proportions(df: pd.DataFrame, name: str, save_dir: Optional[Path] = None) -> pd.DataFrame:
    """Proportion of reaches per subject per phase in each outcome_group.

    Task-success summary independent of kinematic quality. Columns:
    missed / displaced / retrieved (any subset present in the data).
    """
    counts = df.groupby(["subject_id", "phase_group", "outcome_group"]).size()  # Count reaches per subject per phase_group per outcome group
    totals = df.groupby(["subject_id", "phase_group"]).size()  # Count total reaches per subject per phase_group
    proportions = counts / totals  # Divide per-group count by total to get proportions
    proportions = proportions.unstack("outcome_group", fill_value=0)  # Pivot outcome_group from rows to columns so each outcome becomes its own variable
    if save_dir is not None:
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        proportions.to_csv(save_dir / f"{name}.csv")
    return proportions


def compute_contact_proportions(
    df: pd.DataFrame,
    name: str,
    group_col: str = "contact_group",
    save_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """Two-level contact proportions per subject per phase.

    ``group_col`` picks which contact rollup to aggregate:
    - ``contact_group`` (default): per-reach -- did this reach touch the pellet?
    - ``segment_contact_group``: per-segment -- was the pellet ever touched?
    """
    counts = df.groupby(["subject_id", "phase_group", group_col]).size()  # Count reaches per subject per phase_group per contact group
    totals = df.groupby(["subject_id", "phase_group"]).size()  # Count total reaches per subject per phase_group
    proportions = counts / totals  # Divide per-group count by total to get proportions
    proportions = proportions.unstack(group_col, fill_value=0)  # Pivot the contact column from rows to columns
    if save_dir is not None:
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        proportions.to_csv(save_dir / f"{name}.csv")
    return proportions
