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
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler


def build_group_region_map(counts_groupeddf: pd.DataFrame) -> Dict[str, List[str]]:
    """Parse ``constituent_regions`` JSON strings to group -> [region acronym] map.

    ``counts_groupeddf`` is expected to have a ``group_name`` column and a
    ``constituent_regions`` column containing JSON-list strings like
    ``'["FN", "IP"]'``. Duplicate group rows (one per brain) are collapsed.
    """
    if "constituent_regions" not in counts_groupeddf.columns:
        raise KeyError("counts_groupeddf must include 'constituent_regions' -- rerun data_loader against a DB that includes it.")
    out: Dict[str, List[str]] = {}
    seen = set()
    for _, row in counts_groupeddf.iterrows():
        group = row["group_name"]
        if group in seen:
            continue
        raw = row["constituent_regions"]
        if raw is None:
            continue
        if isinstance(raw, (list, tuple)):
            regions = list(raw)
        else:
            try:
                regions = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
        out[group] = regions
        seen.add(group)
    return out


@dataclass
class DrillDownResult:
    """Per-group PCA on atomic regions."""
    group: str
    n_atomic_regions: int
    explained_variance_ratio: np.ndarray
    cumulative_variance: np.ndarray
    pca: PCA
    X: pd.DataFrame                     # subjects x atomic_region_hemi columns that went in


def _atomic_cols_for_group(
    group: str,
    group_region_map: Dict[str, List[str]],
    available_cols: List[str],
) -> List[str]:
    """Return the atomic region_hemi columns that belong to ``group``.

    ``available_cols`` is the set of ``{region_acronym}_{hemisphere}`` columns
    actually present in ACDUdf_wide (filtered to imaging-valid brains).
    """
    acronyms = set(group_region_map.get(group, []))
    if not acronyms:
        return []
    out = []
    for col in available_cols:
        base = col.rsplit("_", 1)[0]  # drop the hemisphere suffix
        if base in acronyms:
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
    atomic_cols = _atomic_cols_for_group(group, group_region_map, ungrouped_wide.columns.tolist())
    if len(atomic_cols) < 2 or ungrouped_wide.shape[0] < 2:
        return None
    X = ungrouped_wide[atomic_cols].fillna(0)
    n_comp = min(n_components, len(X) - 1, X.shape[1])
    scaled = StandardScaler().fit_transform(X)
    pca = PCA(n_components=n_comp)
    pca.fit(scaled)
    return DrillDownResult(
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
    rows = []
    for label, mat in [("grouped", grouped_wide), ("ungrouped", ungrouped_wide)]:
        if mat.empty:
            continue
        X = mat.fillna(0)
        n_comp = min(n_components, len(X) - 1, X.shape[1])
        scaled = StandardScaler().fit_transform(X)
        pca = PCA(n_components=n_comp)
        pca.fit(scaled)
        for i, var in enumerate(pca.explained_variance_ratio_):
            rows.append({
                "level": label,
                "component": f"PC{i+1}",
                "variance_explained": var,
                "cumulative": float(np.cumsum(pca.explained_variance_ratio_)[i]),
                "n_features": mat.shape[1],
                "n_subjects": mat.shape[0],
            })
    return pd.DataFrame(rows)
