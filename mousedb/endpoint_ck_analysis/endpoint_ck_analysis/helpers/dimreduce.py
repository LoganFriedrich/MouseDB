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
from __future__ import annotations  # postpone-annotation evaluation; lets type hints reference forward names without runtime cost

from typing import Any, Dict, Iterable, List  # type-hint primitives; document expected argument shapes

import numpy as np  # numpy: numerical arrays library used for fast math
import pandas as pd  # pandas: dataframe library, treats data like a spreadsheet
import scipy.stats as stats  # scipy.stats: classical statistical tests (Pearson r etc.)
from sklearn.cross_decomposition import PLSCanonical  # PLSCanonical: symmetric two-block PLS that finds covariance-maximizing latent variables in both X and Y
from sklearn.decomposition import PCA  # PCA: classic principal-components analysis
from sklearn.preprocessing import StandardScaler  # StandardScaler: subtracts mean and divides by std per column (z-score)


def run_pca_for_phase(agg_flat_df: pd.DataFrame, phase_group: str, n_components: int = 3):
    """Filter to (phase_group, contacted), z-score, run PCA. Returns (pca, scores, eigen_summary, loadings, X).

    Expects ``agg_flat_df`` to be the flattened output of ``aggregate_kinematics_by_contact``
    (i.e. ``.reset_index()``-ed), with ``subject_id``, ``phase_group``, ``contact_group``
    as columns alongside the aggregated kinematic features.
    """
    # Filter to (phase_group, contacted) and keep one row per subject
    X = agg_flat_df[                                                              # boolean indexing: select rows matching both criteria
        (agg_flat_df["phase_group"] == phase_group)                               # rows from the requested phase
        & (agg_flat_df["contact_group"] == "contacted")                           # only reaches that contacted the pellet (kinematics meaningful)
    ].set_index("subject_id").drop(columns=["phase_group", "contact_group"]).fillna(0)  # subject_id -> index; drop the two filter columns; fill NaN with 0 since PCA cannot accept NaN

    # Z-score every feature column
    scaler = StandardScaler()                                                     # fresh scaler per phase so the means/stds match the data we're actually fitting on
    X_scaled = scaler.fit_transform(X)                                            # fit_transform: compute mean/std per column and return the standardized array

    # Fit PCA with components capped at N-1 (PCA cannot extract more components than N-1)
    n_comp = min(n_components, len(X) - 1)                                        # cap component count at N-1 so PCA does not fail; len(X) is number of subjects
    pca = PCA(n_components=n_comp)                                                # construct PCA estimator; choosing n_components_ here defines model size
    scores = pca.fit_transform(X_scaled)                                          # fit and project: returns N x n_comp array of subject positions on each PC

    # Build the eigenvalue summary table
    eigen_summary = pd.DataFrame({                                                # tabular summary of variance per component for printing/plotting
        "Component": [f"PC{i+1}" for i in range(n_comp)],                         # PC1, PC2, ... labels
        "Eigenvalue": pca.explained_variance_,                                    # raw eigenvalue (variance in standardized units)
        "Variance": pca.explained_variance_ratio_,                                # eigenvalue / total variance -> proportion of variance explained
        "Cumulative": np.cumsum(pca.explained_variance_ratio_),                   # cumulative sum of variance shares; useful for scree-plot inspection
    })

    # Build the loadings table with the feature names preserved
    loadings = pd.DataFrame(
        pca.components_,                                                          # components_ is (n_comp, n_features); each row is a PC's loadings vector
        columns=X.columns,                                                        # reattach the feature names from the pre-scaled dataframe so the table is readable
        index=[f"PC{i+1}" for i in range(n_comp)],                                # row labels matching the PC numbering
    )

    return pca, scores, eigen_summary, loadings, X                                # returning X too for downstream reference (raw values, pre-scaling)


def align_signs_to_reference(loadings_df: pd.DataFrame, reference_df: pd.DataFrame) -> pd.DataFrame:
    """Flip each PC's sign if it correlates negatively with the reference.

    Per-phase PCA results come back with arbitrary sign per component; aligning
    to a reference phase (typically Baseline) removes spurious flips so that
    "this region loads positive on PC1" means the same thing across phases.
    """
    aligned = loadings_df.copy()                                                  # work on a copy so we do not mutate the caller's loadings DataFrame
    for pc in aligned.index:                                                      # iterate PCs; index labels are 'PC1', 'PC2', ...
        if pc in reference_df.index:                                              # safety check in case a phase has fewer components than the reference
            r, _ = stats.pearsonr(aligned.loc[pc], reference_df.loc[pc])          # Pearson correlation between this PC's loadings and the reference's same-numbered PC; ignore the p-value
            if r < 0:                                                             # negative r means the PC points the opposite direction
                aligned.loc[pc] = -aligned.loc[pc]                                # flip the sign; the eigenvector direction is mathematically arbitrary so flipping is benign
    return aligned


def build_y_phase(agg_flat_df: pd.DataFrame, features: Iterable[str], phase: str) -> pd.DataFrame:
    """Build a Y-block: kinematic profile for one phase, filtered to ``features``.

    Rows are subjects; columns are ``features``. Used as the Y-block for the
    "injury snapshot" PLS question (what kinematic profile does each mouse
    show at a given phase?).
    """
    features = list(features)                                                     # materialize in case it's a set or generator (pandas column selection requires a list)
    return agg_flat_df[                                                           # boolean filter then column selection
        (agg_flat_df["phase_group"] == phase) & (agg_flat_df["contact_group"] == "contacted")  # rows for this phase, contacted reaches only
    ].set_index("subject_id").drop(columns=["phase_group", "contact_group"])[features].fillna(0)  # one row per subject; restrict to chosen features; NaN -> 0 for PLS compatibility


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
    features = list(features)                                                     # materialize iterable for pandas column selection
    from_df = agg_flat_df[                                                        # snapshot for the source phase
        (agg_flat_df["phase_group"] == phase_from) & (agg_flat_df["contact_group"] == "contacted")
    ].set_index("subject_id").drop(columns=["phase_group", "contact_group"])[features].fillna(0)
    to_df = agg_flat_df[                                                          # snapshot for the destination phase
        (agg_flat_df["phase_group"] == phase_to) & (agg_flat_df["contact_group"] == "contacted")
    ].set_index("subject_id").drop(columns=["phase_group", "contact_group"])[features].fillna(0)
    common = from_df.index.intersection(to_df.index)                              # only subjects with both phases; .intersection() returns shared index labels
    return to_df.loc[common] - from_df.loc[common]                                # element-wise subtraction gives per-feature delta; pandas aligns on subject_id


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
    ordered = list(prior.ordered_regions)                                         # materialize the prior's ordered region list (e.g. ['CST', 'RuST', 'ReST', ...])
    rank_map = {r: i for i, r in enumerate(ordered)}                              # dict comprehension: map each region name to its 0-indexed rank
    n_ranks = len(ordered)                                                        # use this as the rank for any region not in the prior (worst-case rank)
    weights = {}                                                                  # accumulate {column_name: weight} for the output Series
    for col in columns:                                                           # iterate input column names
        region = col.rsplit("_", 1)[0]                                            # strip the trailing '_left'/'_right'/'_both' to recover the bare region name
        rank = rank_map.get(region, n_ranks)                                      # dict.get with default: unranked region gets the max rank (smallest weight)
        weights[col] = float(np.exp(-decay * rank))                               # exp(-decay*rank): rank 0 -> weight 1.0; higher ranks -> smaller weights; cast to plain float
    return pd.Series(weights, name="priority_weight")                             # return as a Series so it can be aligned/multiplied against a DataFrame


def apply_feature_weights(X: pd.DataFrame, weights: pd.Series) -> pd.DataFrame:
    """Multiply each column of X by its weight. Unlisted columns stay unchanged.

    Use after z-scoring to amplify high-priority features before PCA / PLS.
    """
    w = weights.reindex(X.columns, fill_value=1.0)                                # align weight Series to X's columns; columns missing from weights get weight 1.0 (no scaling)
    return X.multiply(w, axis=1)                                                   # broadcast-multiply each column by its weight; axis=1 means "apply along columns"


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
    common = X_block.index.intersection(Y_block.index)                            # only subjects present in both blocks can participate in joint analysis
    X = X_block.loc[common]                                                       # align X to the matched subjects
    Y = Y_block.loc[common]                                                       # align Y to the same subjects in the same order

    x_scaler = StandardScaler()                                                   # independent scaler per block since X and Y have different units (cell counts vs. kinematic features)
    y_scaler = StandardScaler()                                                   # separate Y scaler
    X_scaled = x_scaler.fit_transform(X)                                          # z-score each connectivity feature
    Y_scaled = y_scaler.fit_transform(Y)                                          # z-score each kinematic feature

    n_comp = min(n_components, len(common) - 1, X.shape[1], Y.shape[1])           # PLS cannot extract more than N-1 components or more than the smaller block's feature count

    pls = PLSCanonical(n_components=n_comp)                                       # PLSCanonical: finds symmetric pairs of components that maximize cross-block covariance
    pls.fit(X_scaled, Y_scaled)                                                   # fit the model to the scaled blocks

    X_scores, Y_scores = pls.transform(X_scaled, Y_scaled)                        # each subject's position on each latent variable, one score per block per LV

    X_loadings = pd.DataFrame(
        pls.x_loadings_,                                                          # x_loadings_ is (n_features_X, n_comp); rows are X features, columns are LVs
        columns=[f"LV{i+1}" for i in range(n_comp)],                              # latent-variable labels: LV1, LV2, ...
        index=X.columns,                                                          # connectivity region names so the loading table reads cleanly
    )                                                                             # which connectivity regions drive each component
    Y_loadings = pd.DataFrame(
        pls.y_loadings_,                                                          # y_loadings_ is (n_features_Y, n_comp); rows are Y features, columns are LVs
        columns=[f"LV{i+1}" for i in range(n_comp)],
        index=Y.columns,                                                          # kinematic feature names
    )                                                                             # which kinematic features drive each component

    if verbose:                                                                   # caller wants a console summary
        print(f"{label}: N={len(common)}, X features={X.shape[1]}, Y features={Y.shape[1]}, components fit={n_comp}")
    return {                                                                       # bundled result dict; keys mirror the docstring
        "pls": pls,                                                               # fitted estimator (so caller can call .predict if desired)
        "X_scores": X_scores,                                                     # subject scores on X-block LVs
        "Y_scores": Y_scores,                                                     # subject scores on Y-block LVs
        "X_loadings": X_loadings,                                                 # X loadings DataFrame (region x LV)
        "Y_loadings": Y_loadings,                                                 # Y loadings DataFrame (feature x LV)
        "subjects": common.tolist(),                                              # subject IDs in the row order used; .tolist() converts pandas Index to a plain list
        "label": label,                                                           # echo back the caller-provided label for downstream plotting
    }
