"""Dimensionality reduction helpers: PCA per phase and PLS across blocks.

All functions are lifted from the original notebook's Sections 9 and 10
with comment preservation. The mathematical behavior is identical to the
monolithic notebook; only docstrings and type hints are new.

Functions:
    run_pca_for_phase        - fit PCA on kinematics aggregated to one phase
    align_signs_to_reference - flip PC signs if negatively correlated with reference
    build_y_phase            - build Y-block as kinematic snapshot for one phase
    build_y_shift            - build Y-block as delta between two phases
    run_pls                  - fit PLSCanonical on matched (X, Y) blocks
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

import numpy as np
import pandas as pd
import scipy.stats as stats
from sklearn.cross_decomposition import PLSCanonical
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler


def run_pca_for_phase(agg_flat_df: pd.DataFrame, phase_group: str, n_components: int = 3):
    """Filter to (phase_group, contacted), z-score, run PCA. Returns (pca, scores, eigen_summary, loadings, X).

    Expects ``agg_flat_df`` to be the flattened output of ``aggregate_kinematics_by_contact``
    (i.e. ``.reset_index()``-ed), with ``subject_id``, ``phase_group``, ``contact_group``
    as columns alongside the aggregated kinematic features.
    """
    # Filter to (phase_group, contacted) and keep one row per subject
    X = agg_flat_df[
        (agg_flat_df["phase_group"] == phase_group)
        & (agg_flat_df["contact_group"] == "contacted")
    ].set_index("subject_id").drop(columns=["phase_group", "contact_group"]).fillna(0)

    # Z-score every feature column
    scaler = StandardScaler()  # Fresh scaler per phase so the means/stds match the data we're actually fitting on
    X_scaled = scaler.fit_transform(X)

    # Fit PCA with components capped at N-1 (PCA cannot extract more components than N-1)
    n_comp = min(n_components, len(X) - 1)  # Cap component count at N-1 so PCA does not fail
    pca = PCA(n_components=n_comp)
    scores = pca.fit_transform(X_scaled)  # Each subject's position in component space

    # Build the eigenvalue summary table
    eigen_summary = pd.DataFrame({
        "Component": [f"PC{i+1}" for i in range(n_comp)],
        "Eigenvalue": pca.explained_variance_,
        "Variance": pca.explained_variance_ratio_,
        "Cumulative": np.cumsum(pca.explained_variance_ratio_),
    })

    # Build the loadings table with the feature names preserved
    loadings = pd.DataFrame(
        pca.components_,
        columns=X.columns,  # Reattach the feature names from the pre-scaled dataframe
        index=[f"PC{i+1}" for i in range(n_comp)],
    )

    return pca, scores, eigen_summary, loadings, X  # Returning X too for downstream reference


def align_signs_to_reference(loadings_df: pd.DataFrame, reference_df: pd.DataFrame) -> pd.DataFrame:
    """Flip each PC's sign if it correlates negatively with the reference.

    Per-phase PCA results come back with arbitrary sign per component; aligning
    to a reference phase (typically Baseline) removes spurious flips so that
    "this region loads positive on PC1" means the same thing across phases.
    """
    aligned = loadings_df.copy()  # Work on a copy so we do not mutate the original loadings DataFrame
    for pc in aligned.index:  # For each PC (PC1, PC2, PC3)
        if pc in reference_df.index:  # Safety check in case a phase has fewer components
            r, _ = stats.pearsonr(aligned.loc[pc], reference_df.loc[pc])  # Pearson correlation between this PC's loadings and the reference's same-numbered PC
            if r < 0:
                aligned.loc[pc] = -aligned.loc[pc]  # Flip the sign so this PC points the same way as the reference
    return aligned


def build_y_phase(agg_flat_df: pd.DataFrame, features: Iterable[str], phase: str) -> pd.DataFrame:
    """Build a Y-block: kinematic profile for one phase, filtered to ``features``.

    Rows are subjects; columns are ``features``. Used as the Y-block for the
    "injury snapshot" PLS question (what kinematic profile does each mouse
    show at a given phase?).
    """
    features = list(features)  # Materialize in case it's a set (pandas column selection doesn't accept sets)
    return agg_flat_df[
        (agg_flat_df["phase_group"] == phase) & (agg_flat_df["contact_group"] == "contacted")
    ].set_index("subject_id").drop(columns=["phase_group", "contact_group"])[features].fillna(0)


def build_y_shift(
    agg_flat_df: pd.DataFrame,
    features: Iterable[str],
    phase_from: str,
    phase_to: str,
) -> pd.DataFrame:
    """Build a Y-block: change in kinematic profile from ``phase_from`` to ``phase_to``.

    Subjects missing from either phase are dropped. Returned DataFrame has one
    row per subject with both phases, one column per feature, values are the
    element-wise delta (phase_to - phase_from).
    """
    features = list(features)
    from_df = agg_flat_df[
        (agg_flat_df["phase_group"] == phase_from) & (agg_flat_df["contact_group"] == "contacted")
    ].set_index("subject_id").drop(columns=["phase_group", "contact_group"])[features].fillna(0)
    to_df = agg_flat_df[
        (agg_flat_df["phase_group"] == phase_to) & (agg_flat_df["contact_group"] == "contacted")
    ].set_index("subject_id").drop(columns=["phase_group", "contact_group"])[features].fillna(0)
    common = from_df.index.intersection(to_df.index)  # Only subjects with both phases
    return to_df.loc[common] - from_df.loc[common]  # Element-wise subtraction gives per-feature delta


def priority_weights_from_prior(prior, columns, decay: float = 0.1) -> pd.Series:
    """Build per-column weights from a RegionPrior ordering.

    Columns are expected to be ``{region}_{hemisphere}`` strings. For each
    column, its weight is ``exp(-decay * rank)`` where ``rank`` is the
    region's index in ``prior.ordered_regions`` (0-indexed; unranked
    regions get ``rank = len(ordered_regions)``).

    Larger ``decay`` makes the weighting more aggressive (top-rank
    dominant); smaller ``decay`` spreads weight more evenly. ``decay=0``
    returns uniform weights.

    Returns a Series indexed by column name. Designed to multiply a
    z-scored feature matrix: ``X_weighted = apply_feature_weights(X, w)``.
    """
    ordered = list(prior.ordered_regions)
    rank_map = {r: i for i, r in enumerate(ordered)}
    n_ranks = len(ordered)
    weights = {}
    for col in columns:
        region = col.rsplit("_", 1)[0]
        rank = rank_map.get(region, n_ranks)
        weights[col] = float(np.exp(-decay * rank))
    return pd.Series(weights, name="priority_weight")


def apply_feature_weights(X: pd.DataFrame, weights: pd.Series) -> pd.DataFrame:
    """Multiply each column of X by its weight. Unlisted columns stay unchanged.

    Use after z-scoring to amplify high-priority features before PCA / PLS.
    """
    w = weights.reindex(X.columns, fill_value=1.0)
    return X.multiply(w, axis=1)


def run_pls(
    X_block: pd.DataFrame,
    Y_block: pd.DataFrame,
    n_components: int = 2,
    label: str = "",
    verbose: bool = True,
) -> Dict[str, Any]:
    """Match subjects between blocks, z-score, fit PLSCanonical, return everything useful.

    Args:
        X_block: Rows are subjects, columns are connectivity regions.
        Y_block: Rows are subjects, columns are kinematic features.
        n_components: Maximum number of latent variables to extract. Automatically
            capped at (N-1, X.shape[1], Y.shape[1]) whichever is smallest.
        label: Short tag printed alongside the shape summary.
        verbose: If True, print a one-line summary of N and feature counts.

    Returns:
        A dict with keys: ``pls``, ``X_scores``, ``Y_scores``, ``X_loadings``,
        ``Y_loadings``, ``subjects``, ``label``.
    """
    common = X_block.index.intersection(Y_block.index)  # Only subjects present in both blocks can participate
    X = X_block.loc[common]  # Align X to the matched subjects
    Y = Y_block.loc[common]  # Align Y to the same subjects in the same order

    x_scaler = StandardScaler()  # Independent scaler per block since X and Y have different units
    y_scaler = StandardScaler()
    X_scaled = x_scaler.fit_transform(X)  # Z-score each connectivity feature
    Y_scaled = y_scaler.fit_transform(Y)  # Z-score each kinematic feature

    n_comp = min(n_components, len(common) - 1, X.shape[1], Y.shape[1])  # PLS cannot extract more than N-1 components or more than the smaller block's feature count

    pls = PLSCanonical(n_components=n_comp)  # PLSCanonical finds symmetric pairs of components that maximize cross-block covariance
    pls.fit(X_scaled, Y_scaled)  # Fit the model to the scaled blocks

    X_scores, Y_scores = pls.transform(X_scaled, Y_scaled)  # Each subject's position on each latent variable, one score per block per LV

    X_loadings = pd.DataFrame(
        pls.x_loadings_,
        columns=[f"LV{i+1}" for i in range(n_comp)],
        index=X.columns,
    )  # Which connectivity regions drive each component
    Y_loadings = pd.DataFrame(
        pls.y_loadings_,
        columns=[f"LV{i+1}" for i in range(n_comp)],
        index=Y.columns,
    )  # Which kinematic features drive each component

    if verbose:
        print(f"{label}: N={len(common)}, X features={X.shape[1]}, Y features={Y.shape[1]}, components fit={n_comp}")
    return {
        "pls": pls,
        "X_scores": X_scores,
        "Y_scores": Y_scores,
        "X_loadings": X_loadings,
        "Y_loadings": Y_loadings,
        "subjects": common.tolist(),
        "label": label,
    }
