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
from __future__ import annotations  # postpone-annotation evaluation; lets us reference types like 'List[str]' without runtime cost

from pathlib import Path  # pathlib: object-oriented filesystem paths
from typing import List, Optional  # type hints; List[str] / Optional[Path] document expected argument types

import pandas as pd  # pandas: dataframe library, handles tabular data like a spreadsheet

from ..config import METADATA_COLS, UNIT_SUFFIX_PREFERENCE  # METADATA_COLS: set of column names to skip; UNIT_SUFFIX_PREFERENCE: ordered list of unit suffixes (mm preferred over pixels, etc.)


def prefer_calibrated_units(columns):
    """Given a list of column names, drop any whose unit is a less-preferred duplicate of another column."""
    by_base = {}                                                                # map: semantic base name -> list of (column, suffix) pairs that share that base
    for col in columns:                                                          # examine each input column
        matched = False                                                          # tracker: did we find a known unit suffix for this column?
        for suffix in UNIT_SUFFIX_PREFERENCE:                                    # iterate suffixes in preference order so we know the column's unit family
            if col.endswith(suffix):                                             # str.endswith: True if col's tail matches suffix
                base = col[:-len(suffix)]                                        # slice off the suffix to get the semantic base, e.g. 'max_extent_mm' -> 'max_extent'
                by_base.setdefault(base, []).append((col, suffix))               # dict.setdefault: get existing list or create empty one, then append this column
                matched = True                                                   # flag so we don't fall through to the no-suffix branch
                break                                                            # stop scanning suffixes; we found this column's unit family
        if not matched:                                                          # no known unit suffix matched
            by_base.setdefault(col, []).append((col, None))                      # use the full column name as its own base; suffix=None marks "no unit"
    keep = []                                                                    # accumulator for surviving columns
    for base, entries in by_base.items():                                        # iterate every base group; each may have one or more columns
        if len(entries) == 1:                                                    # only one column shares this base -> keep as-is
            keep.append(entries[0][0])                                           # entries[0] is the (col, suffix) tuple; index [0] grabs the col name
        else:                                                                    # multiple columns share this base -> pick the most-preferred unit
            # Sort by preference order; lower index wins
            entries.sort(key=lambda e: UNIT_SUFFIX_PREFERENCE.index(e[1]) if e[1] else len(UNIT_SUFFIX_PREFERENCE))  # lambda returns the suffix's preference rank; columns with None suffix sort to the end
            keep.append(entries[0][0])                                           # first entry after sorting is the most-preferred
    return keep                                                                  # final list of deduplicated column names


def get_kinematic_cols(df: pd.DataFrame) -> List[str]:
    """Return every numeric column that is a kinematic feature (not metadata, not a redundant-unit duplicate)."""
    numeric_cols = df.select_dtypes(include="number").columns.tolist()           # select_dtypes(include="number"): keep only numeric columns; .columns.tolist() converts the Index to a plain list
    non_metadata = [c for c in numeric_cols if c not in METADATA_COLS]           # list comprehension: drop columns that are bookkeeping (subject_id, session_id, etc.)
    return prefer_calibrated_units(non_metadata)                                  # additionally drop less-preferred unit duplicates (keep _mm over _pixels, etc.)


def _agg_dict_for(kinematic_cols: List[str]) -> dict:
    """Build the mean/std/median/q25/q75 aggregation spec for a column list.

    Broken out so ``aggregate_kinematics`` and ``aggregate_kinematics_by_contact``
    share one source of truth for what gets computed per kinematic feature.
    """
    return {col: [                                                                # dict comprehension: each kinematic column maps to its 5-stat aggregation spec
        ("mean", "mean"),                                                         # (output_name_suffix, pandas-recognized aggregation string)
        ("std", "std"),                                                           # standard deviation across the group
        ("median", "median"),                                                     # 50th percentile
        ("q25", lambda x: x.quantile(0.25)),                                      # 25th percentile via lambda since pandas doesn't accept a string for arbitrary quantiles
        ("q75", lambda x: x.quantile(0.75)),                                      # 75th percentile
    ] for col in kinematic_cols}


def aggregate_kinematics(df: pd.DataFrame, name: str, save_dir: Optional[Path] = None) -> pd.DataFrame:
    """Aggregate kinematics at the three-way outcome grain (missed/displaced/retrieved).

    Grouped by (subject_id, phase_group, outcome_group). Returns a multi-indexed
    DataFrame with per-feature mean/std/median/q25/q75 columns.
    """
    kinematic_cols = get_kinematic_cols(df)                                       # which columns count as kinematic features for this dataframe
    agg_dict = _agg_dict_for(kinematic_cols)                                      # pre-built mean/std/median/q25/q75 spec for each kinematic column
    aggregated = df.groupby(["subject_id", "phase_group", "outcome_group"]).agg(**{  # groupby returns a DataFrameGroupBy; agg() applies named aggregations across groups
        f"{col}_{stat_name}": (col, stat_func)                                    # named-aggregation tuple: (input column, aggregation function); output column = "<col>_<stat>"
        for col, stat_list in agg_dict.items()                                    # outer loop: each kinematic column
        for stat_name, stat_func in stat_list                                     # inner loop: each of the 5 stats
    })                                                                            # ** unpacks the dict-comprehension as keyword arguments to .agg()
    if save_dir is not None:                                                      # caller wants to side-effect a CSV
        save_dir = Path(save_dir)                                                 # cast to Path object so we can use the / operator
        save_dir.mkdir(parents=True, exist_ok=True)                               # create the directory if missing; parents=True creates intermediate dirs; exist_ok=True suppresses error if it already exists
        aggregated.to_csv(save_dir / f"{name}.csv")                               # f-string interpolates the name into the filename
    return aggregated


def aggregate_kinematics_by_contact(df: pd.DataFrame, name: str, save_dir: Optional[Path] = None) -> pd.DataFrame:
    """Same aggregation grouped by contact_group (missed vs contacted).

    Useful when retrieved reaches are too sparse for stable per-subject summary
    statistics. Grouped by (subject_id, phase_group, contact_group).
    """
    kinematic_cols = get_kinematic_cols(df)                                       # which columns count as kinematic features
    agg_dict = _agg_dict_for(kinematic_cols)                                      # 5-stat spec per column
    aggregated = df.groupby(["subject_id", "phase_group", "contact_group"]).agg(**{  # contact_group has 2 levels: 'missed' / 'contacted' -- coarser than outcome_group's 3-way split
        f"{col}_{stat_name}": (col, stat_func)                                    # named-aggregation: produces "<col>_<stat>" columns
        for col, stat_list in agg_dict.items()                                    # outer: each kinematic column
        for stat_name, stat_func in stat_list                                     # inner: each stat
    })
    if save_dir is not None:                                                      # save_dir provided -> write CSV
        save_dir = Path(save_dir)                                                 # cast to Path
        save_dir.mkdir(parents=True, exist_ok=True)                               # ensure directory exists
        aggregated.to_csv(save_dir / f"{name}.csv")                               # write multi-indexed dataframe to CSV
    return aggregated


def compute_outcome_proportions(df: pd.DataFrame, name: str, save_dir: Optional[Path] = None) -> pd.DataFrame:
    """Proportion of reaches per subject per phase in each outcome_group.

    Task-success summary independent of kinematic quality. Columns:
    missed / displaced / retrieved (any subset present in the data).
    """
    counts = df.groupby(["subject_id", "phase_group", "outcome_group"]).size()    # .size() counts rows per group; returns a Series indexed by (subject, phase, outcome)
    totals = df.groupby(["subject_id", "phase_group"]).size()                      # row count per (subject, phase) ignoring outcome -> denominator
    proportions = counts / totals                                                  # element-wise divide; pandas aligns on the shared (subject, phase) index levels
    proportions = proportions.unstack("outcome_group", fill_value=0)               # pivot the outcome_group level from rows to columns; fill_value=0 for outcomes that didn't occur for some subjects
    if save_dir is not None:                                                       # optional CSV side-effect
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        proportions.to_csv(save_dir / f"{name}.csv")
    return proportions


def compute_contact_proportions(
    df: pd.DataFrame,
    name: str,
    group_col: str = "contact_group",                                              # default: per-reach contact rollup; override with 'segment_contact_group' for per-segment rollup
    save_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """Two-level contact proportions per subject per phase.

    ``group_col`` picks which contact rollup to aggregate:
    - ``contact_group`` (default): per-reach -- did this reach touch the pellet?
    - ``segment_contact_group``: per-segment -- was the pellet ever touched?
    """
    counts = df.groupby(["subject_id", "phase_group", group_col]).size()           # per-(subject, phase, contact-bucket) row counts
    totals = df.groupby(["subject_id", "phase_group"]).size()                      # per-(subject, phase) totals -> denominator
    proportions = counts / totals                                                   # share of reaches in each contact bucket
    proportions = proportions.unstack(group_col, fill_value=0)                     # contact bucket from row level to column; missing combinations -> 0
    if save_dir is not None:                                                        # optional CSV side-effect
        save_dir = Path(save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        proportions.to_csv(save_dir / f"{name}.csv")
    return proportions
