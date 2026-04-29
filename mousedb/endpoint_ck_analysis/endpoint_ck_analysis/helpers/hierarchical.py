"""Hierarchical (grouped vs ungrouped) connectomics analysis helpers.

The eLife grouping in notebooks 01 / 04 lumps regions like "all Reticular
Nuclei" into one number. If a group contains subregions with opposite
functional roles, that lumping hides variance that might matter for
prediction. These helpers let the pipeline drill into each group and
compare grouped-vs-ungrouped decompositions side by side.

Functions:

    build_group_region_map         - map each eLife group to its atomic regions
    drill_down_pca                 - within-group PCA on atomic regions
    grouped_vs_ungrouped_summary   - side-by-side variance comparison
"""
from __future__ import annotations  # postpone-annotation evaluation; lets type hints reference forward names

import json  # json: standard library; parse the constituent_regions JSON-list strings stored in the DB
from dataclasses import dataclass  # dataclass: lightweight class decorator that auto-generates __init__/__repr__
from typing import Dict, List, Optional  # type-hint primitives

import numpy as np  # numpy: arrays + math
import pandas as pd  # pandas: dataframe library
from sklearn.decomposition import PCA  # PCA: principal-components decomposition
from sklearn.preprocessing import StandardScaler  # StandardScaler: per-column z-score


def build_group_region_map(counts_groupeddf: pd.DataFrame) -> Dict[str, List[str]]:
    """Parse ``constituent_regions`` JSON strings to group -> [region acronym] map.

    ``counts_groupeddf`` is expected to have a ``group_name`` column and a
    ``constituent_regions`` column containing JSON-list strings like
    ``'["FN", "IP"]'``. Duplicate group rows (one per brain) are collapsed.
    """
    if "constituent_regions" not in counts_groupeddf.columns:                     # the column is required for this whole module to work; fail fast with a clear message
        raise KeyError("counts_groupeddf must include 'constituent_regions' -- rerun data_loader against a DB that includes it.")
    out: Dict[str, List[str]] = {}                                                # accumulator: {group_name: [atomic_region_acronyms]}
    seen = set()                                                                  # track groups already processed; the input has one row per (group, brain) so the same group repeats
    for _, row in counts_groupeddf.iterrows():                                    # iterrows yields (index, Series) pairs; we only need the row
        group = row["group_name"]                                                 # the eLife group label
        if group in seen:                                                         # already processed this group from another brain; skip
            continue
        raw = row["constituent_regions"]                                          # the JSON-list string (or list, depending on DB driver)
        if raw is None:                                                           # missing constituent regions -> skip
            continue
        if isinstance(raw, (list, tuple)):                                        # some DB drivers return native Python lists already
            regions = list(raw)                                                   # cast to list (in case it's a tuple)
        else:                                                                     # assume it's a JSON-list string
            try:
                regions = json.loads(raw)                                         # json.loads parses '["FN", "IP"]' to ['FN', 'IP']
            except (TypeError, json.JSONDecodeError):                             # malformed JSON or wrong type -> skip this row
                continue
        out[group] = regions                                                      # record the mapping
        seen.add(group)                                                           # mark group as processed
    return out


@dataclass
class DrillDownResult:
    """Per-group PCA on atomic regions."""
    group: str                                                                    # eLife group name
    n_atomic_regions: int                                                         # how many atomic-region columns fed into this PCA
    explained_variance_ratio: np.ndarray                                          # variance share per PC, length n_components
    cumulative_variance: np.ndarray                                               # cumulative sum of variance shares (cumsum)
    pca: PCA                                                                      # fitted estimator (so caller can inspect components_, etc.)
    X: pd.DataFrame                                                               # subjects x atomic_region_hemi columns that went in (raw, pre-scaling)


def _atomic_cols_for_group(
    group: str,
    group_region_map: Dict[str, List[str]],
    available_cols: List[str],
) -> List[str]:
    """Return the atomic region_hemi columns that belong to ``group``.

    ``available_cols`` is the set of ``{region_acronym}_{hemisphere}`` columns
    actually present in ACDUdf_wide (filtered to imaging-valid brains).
    """
    acronyms = set(group_region_map.get(group, []))                               # set lookup is O(1); .get returns [] for missing groups
    if not acronyms:                                                              # group has no atomic constituents -> nothing to find
        return []
    out = []                                                                      # accumulator for matching columns
    for col in available_cols:                                                    # iterate every column in the wide ungrouped matrix
        base = col.rsplit("_", 1)[0]                                              # drop the hemisphere suffix (last "_left"/"_right"/"_both") to recover the bare acronym
        if base in acronyms:                                                      # this column's region is part of the group
            out.append(col)
    return out


def drill_down_pca(
    ungrouped_wide: pd.DataFrame,
    group: str,
    group_region_map: Dict[str, List[str]],
    n_components: int = 3,
) -> Optional[DrillDownResult]:
    """Run a PCA on the atomic regions inside one eLife group.

    Returns None if the group has fewer than 2 atomic-region columns available
    in ``ungrouped_wide`` (no decomposition possible).
    """
    atomic_cols = _atomic_cols_for_group(group, group_region_map, ungrouped_wide.columns.tolist())  # restrict to columns belonging to this group
    if len(atomic_cols) < 2 or ungrouped_wide.shape[0] < 2:                       # need >=2 features and >=2 subjects for PCA to be meaningful
        return None
    X = ungrouped_wide[atomic_cols].fillna(0)                                     # restrict to those columns; NaN -> 0 since PCA can't accept NaN
    n_comp = min(n_components, len(X) - 1, X.shape[1])                            # PCA cannot extract more components than min(N-1, n_features); cap to avoid sklearn errors
    scaled = StandardScaler().fit_transform(X)                                    # z-score columns so units don't dominate
    pca = PCA(n_components=n_comp)                                                # construct PCA estimator
    pca.fit(scaled)                                                               # fit (no transform needed -- caller only inspects components_)
    return DrillDownResult(                                                        # bundle results into the dataclass
        group=group,
        n_atomic_regions=len(atomic_cols),
        explained_variance_ratio=pca.explained_variance_ratio_,
        cumulative_variance=np.cumsum(pca.explained_variance_ratio_),
        pca=pca,
        X=X,
    )


def grouped_vs_ungrouped_summary(
    grouped_wide: pd.DataFrame,
    ungrouped_wide: pd.DataFrame,
    n_components: int = 5,
) -> pd.DataFrame:
    """Fit PCA on both the grouped and ungrouped connectomics matrices.

    Returns a DataFrame with one row per component and columns for the
    variance explained at each level. Useful for eyeballing whether the
    eLife grouping is throwing away variance structure.
    """
    rows = []                                                                     # accumulator: one row per (level, component)
    for label, mat in [("grouped", grouped_wide), ("ungrouped", ungrouped_wide)]:  # iterate the two matrices with their human-readable labels
        if mat.empty:                                                             # if a level has no data (e.g., no ungrouped pivot built yet), skip
            continue
        X = mat.fillna(0)                                                         # NaN -> 0 since PCA can't accept NaN
        n_comp = min(n_components, len(X) - 1, X.shape[1])                        # cap components at min(N-1, n_features)
        scaled = StandardScaler().fit_transform(X)                                # z-score columns
        pca = PCA(n_components=n_comp)                                            # construct PCA estimator
        pca.fit(scaled)                                                           # fit on scaled data
        for i, var in enumerate(pca.explained_variance_ratio_):                   # iterate components with their variance shares
            rows.append({                                                          # one summary row per component
                "level": label,                                                   # 'grouped' or 'ungrouped'
                "component": f"PC{i+1}",                                          # PC1, PC2, ... label
                "variance_explained": var,                                        # share of variance for this PC
                "cumulative": float(np.cumsum(pca.explained_variance_ratio_)[i]), # cumulative variance up through this PC
                "n_features": mat.shape[1],                                       # number of input columns at this level
                "n_subjects": mat.shape[0],                                       # number of input rows (subjects) at this level
            })
    return pd.DataFrame(rows)                                                     # convert list-of-dicts to DataFrame
