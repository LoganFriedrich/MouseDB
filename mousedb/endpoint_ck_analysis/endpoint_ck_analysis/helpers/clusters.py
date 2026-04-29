"""Cluster analysis helpers: method choice, profiling, naming, validation.

Used by notebook 07 to group subjects on connectivity, describe what each
group is "about", and test whether the clustering captures real structure
beyond chance.

Functions:

    cluster_subjects           -- Ward / k-means / GMM / consensus, pick one
    profile_clusters           -- per-cluster mean deviation z-scores per region
    auto_name_clusters         -- build short labels from top defining regions
    permutation_validate       -- LOO + random-subsample null tests
    alluvial_source_records    -- flatten subject x cluster x phase assignments
                                  for plotly Sankey input

Everything below works at any N; at small N the output is trivial but
correctly-structured (no crashes, no silent shape drift).
"""
from __future__ import annotations  # postpone annotation evaluation so type hints don't need to be importable at runtime

from dataclasses import dataclass  # decorator for typed return-value containers
from typing import Callable, Dict, List, Optional, Tuple  # type hints used in signatures below

import numpy as np  # array math; used for permutation arrays, modal labels, etc.
import pandas as pd  # main dataframe library
from scipy.cluster.hierarchy import fcluster, linkage  # ward linkage tree builder + tree-cut helper
from scipy.spatial.distance import pdist  # condensed pairwise distance vector for linkage()
from sklearn.cluster import KMeans  # k-means clustering
from sklearn.mixture import GaussianMixture  # GMM clustering with soft probabilities
from sklearn.preprocessing import StandardScaler  # z-score scaler used before clustering


# ---------------------------------------------------------------------------
# Clustering method selection
# ---------------------------------------------------------------------------


@dataclass
class ClusterResult:
    """What every ``cluster_subjects`` method returns."""
    labels: pd.Series         # subject_id -> integer cluster ID (1..K)
    method: str               # 'ward', 'kmeans', 'gmm', 'consensus'
    k: int                    # number of clusters actually produced
    extras: Dict[str, object] # method-specific details (linkage matrix, GMM probs, etc.)


def _check_inputs(X: pd.DataFrame) -> None:
    if X.empty:  # empty dataframe is never valid clustering input
        raise ValueError("Cluster input X is empty.")
    if X.isna().any().any():  # double .any() because X.isna() returns a 2D mask; clustering will error on NaN so we surface it early
        raise ValueError("Cluster input contains NaN; fill or drop first.")


def cluster_subjects(
    X: pd.DataFrame,
    method: str = "ward",
    k: int = 4,
    scale: bool = True,
    random_state: int = 42,
    consensus_n_resamples: int = 50,
    consensus_frac: float = 0.8,
) -> ClusterResult:
    """Cluster rows of X (subjects) into k groups using the chosen method.

    Args:
        X: DataFrame with subjects as rows and features as columns.
        method: ``'ward'`` (Ward linkage + fcluster), ``'kmeans'``,
            ``'gmm'`` (Gaussian Mixture), or ``'consensus'``
            (bootstrap-resampled k-means + modal label).
        k: target number of clusters.
        scale: z-score columns before clustering (recommended).
        random_state: RNG seed for stochastic methods.
        consensus_n_resamples: how many bootstrap draws for consensus.
        consensus_frac: fraction of subjects sampled per bootstrap.

    Returns:
        :class:`ClusterResult` with per-subject labels.
    """
    _check_inputs(X)  # guard against empty / NaN input before doing real work
    data = StandardScaler().fit_transform(X.values) if scale else X.values  # z-score features so high-magnitude regions don't dominate the distance metric; toggle scale=False if features are already comparable

    if method == "ward":  # deterministic hierarchical clustering: best when clusters might be elongated, no random_state needed
        link = linkage(pdist(data), method="ward")  # build the full linkage tree (one merge step per pair); pdist gives the condensed distance vector linkage expects
        labels = fcluster(link, t=k, criterion="maxclust")  # cut the tree to produce exactly k flat clusters; criterion='maxclust' interprets t as a target cluster count
        extras = {"linkage": link}  # linkage matrix is useful for downstream dendrogram plots, so pass it back
    elif method == "kmeans":  # spherical-cluster assumption; fast at large N
        km = KMeans(n_clusters=k, n_init=10, random_state=random_state)  # n_init=10 runs k-means 10 times with different seeds and keeps the best inertia (sklearn default); raise this if results vary across runs
        labels = km.fit_predict(data) + 1  # sklearn returns 0..K-1 labels; +1 normalizes to 1..K so all four methods share a label convention
        extras = {"inertia": km.inertia_, "centers": km.cluster_centers_}  # inertia for scree-style elbow plots; centers for cluster-prototype interpretations
    elif method == "gmm":  # soft probabilistic clustering; useful when cluster boundaries aren't sharp
        gmm = GaussianMixture(n_components=k, random_state=random_state)  # default covariance_type='full' lets each cluster have its own shape; restrict to 'diag' or 'spherical' if N is small
        labels = gmm.fit_predict(data) + 1  # same 1..K convention as kmeans
        extras = {"proba": gmm.predict_proba(data)}  # per-subject membership probabilities (rows sum to 1); useful for "this subject is mostly cluster 2 but partly cluster 3" reasoning
    elif method == "consensus":  # bootstrap k-means with modal voting; most stable but slowest
        labels, extras = _consensus_cluster(  # delegate to the helper since the loop is non-trivial
            data, k=k, n_resamples=consensus_n_resamples,
            frac=consensus_frac, random_state=random_state,
        )
    else:
        raise ValueError(f"Unknown method {method!r}. Use ward / kmeans / gmm / consensus.")  # explicit error so typos don't silently fall through

    labels_series = pd.Series(labels, index=X.index, name="cluster")  # wrap labels into a Series so they can be merged on subject_id downstream
    return ClusterResult(labels=labels_series, method=method, k=int(labels_series.nunique()), extras=extras)  # k is the *actual* number of clusters produced (could be < requested k if some came back empty)


def _consensus_cluster(
    data: np.ndarray, k: int, n_resamples: int, frac: float, random_state: int
) -> Tuple[np.ndarray, Dict[str, object]]:
    """Bootstrap-resample k-means and return the modal label per subject.

    For each resample, fit k-means on ``frac`` of the subjects, then predict
    labels for everyone. Over the ``n_resamples`` rounds, each subject has
    a distribution of predicted labels; the mode is their final assignment.
    Label identity is resolved by Hungarian matching to the first fit.
    """
    from scipy.stats import mode as _mode  # local import keeps the module load light if consensus path is never used
    rng = np.random.default_rng(random_state)  # fresh RNG seeded for reproducibility
    n = data.shape[0]  # subject count
    all_labels = []  # accumulates one label vector per resample round
    reference = None  # first round's labels become the reference for label-id alignment
    for round_idx in range(n_resamples):  # number of bootstrap draws; tune for stability/speed tradeoff
        idx = rng.choice(n, size=max(2, int(frac * n)), replace=False)  # subsample without replacement; max(2,...) prevents degenerate single-subject draws at tiny N
        km = KMeans(n_clusters=k, n_init=5, random_state=round_idx + random_state)  # fewer n_init than the main path because we're aggregating across rounds anyway; offsetting by round_idx ensures each round has a different seed
        km.fit(data[idx])  # fit on the subsample only
        labels = km.predict(data)  # predict for ALL subjects (including the held-out ones) using the fitted centroids
        if reference is None:  # first round establishes the canonical label IDs
            reference = labels
        else:
            # Align labels to reference via pairwise overlap (cheap Hungarian)
            aligned = _align_labels(labels, reference, k)  # k-means cluster IDs are arbitrary, so different rounds may name the same cluster differently; this step renames so cluster 0 means the same thing across rounds
            labels = aligned
        all_labels.append(labels)
    arr = np.array(all_labels)               # shape (n_resamples, n_subjects)
    modal, _ = _mode(arr, axis=0, keepdims=False)  # per-subject most-frequent label across rounds; this is the consensus assignment
    modal = (modal.astype(int) + 1)          # 1..K
    agreement = (arr == (modal - 1)).mean(axis=0)  # per-subject "what fraction of rounds agreed with the consensus"; high agreement = stable, low = boundary subject
    return modal, {"agreement": agreement, "raw_labels": arr}  # raw_labels lets callers inspect round-by-round behavior if they want


def _align_labels(to_align: np.ndarray, reference: np.ndarray, k: int) -> np.ndarray:
    """Greedy cluster-label alignment (for small k this is good enough)."""
    mapping = {}  # old_label -> new_label remap
    for new_label in range(k):  # iterate over each label in to_align
        mask = to_align == new_label  # boolean mask of subjects assigned this label in to_align
        if mask.sum() == 0:  # cluster came back empty in this round; nothing to align
            continue
        overlaps = np.array([np.sum((to_align == new_label) & (reference == r)) for r in range(k)])  # for each reference cluster r, count how many subjects to_align labeled new_label AND reference labeled r; whichever has the most overlap wins the rename
        mapping[new_label] = int(overlaps.argmax())  # pick the reference cluster with maximum overlap as the new identity
    return np.array([mapping.get(l, l) for l in to_align])  # apply the remap; default to identity if a label wasn't in mapping (shouldn't happen but safe)


# ---------------------------------------------------------------------------
# Cluster profiling + naming
# ---------------------------------------------------------------------------


def profile_clusters(X: pd.DataFrame, labels: pd.Series) -> pd.DataFrame:
    """Per-cluster z-score of each feature vs the overall population.

    Returns a DataFrame where rows are cluster IDs and columns are features.
    A cell's value is ``(cluster_mean - grand_mean) / grand_std`` -- how
    many standard deviations the cluster's mean sits above or below the
    overall mean. Absolute magnitude => feature defines that cluster.

    Args:
        X: subjects x features.
        labels: subject_id -> cluster ID (Series or array aligned to X.index).
    """
    if isinstance(labels, pd.Series):  # if labels are already a Series, just align its index to X
        labels = labels.reindex(X.index)
    else:  # otherwise wrap a raw array as a Series with X's index
        labels = pd.Series(labels, index=X.index)
    grand_mean = X.mean(axis=0)  # population mean per feature; baseline for the z-score
    grand_std = X.std(axis=0).replace(0, 1.0)  # guard zero-variance columns; .replace(0, 1.0) avoids divide-by-zero (a feature that's constant across subjects produces z=0 instead of NaN)
    rows = {}  # cluster ID -> Series of per-feature z-scores
    for cid, group in X.groupby(labels):  # one iteration per cluster; group is a DataFrame of just that cluster's subjects
        rows[cid] = (group.mean(axis=0) - grand_mean) / grand_std  # z-score: how far this cluster's mean sits from the grand mean, in units of grand_std
    profile = pd.DataFrame(rows).T  # transpose so cluster IDs are rows and features are columns
    profile.index.name = "cluster"  # label the row axis for readability when printed
    return profile


def auto_name_clusters(
    profile: pd.DataFrame,
    top_n: int = 2,
    threshold: float = 0.5,
) -> Dict[int, str]:
    """Generate short string labels from the top defining features of each cluster.

    Args:
        profile: output of :func:`profile_clusters`.
        top_n: how many features to mention in the label.
        threshold: |z-score| below this is treated as "not meaningfully
            different" and ignored. If no feature crosses the threshold
            the label is "central" (no standout regions).

    Returns:
        Dict mapping cluster ID to a label string like
        ``"cluster1: up-Corticospinal_both down-Red_Nucleus_both"`` that
        can be displayed on a plot or used as a legend. Callers can
        override with biological names.
    """
    names: Dict[int, str] = {}  # cluster ID -> human-readable label
    for cid in profile.index:  # one label per cluster
        row = profile.loc[cid]  # this cluster's per-feature z-scores
        ranked = row.reindex(row.abs().sort_values(ascending=False).index)  # reorder by descending |z| so the most-defining features come first
        ranked = ranked[ranked.abs() >= threshold].head(top_n)  # drop features that don't pass the threshold; keep only top_n that do
        if len(ranked) == 0:  # cluster has no standout features -> all values near the population mean
            names[int(cid)] = f"cluster{int(cid)}: central"
            continue
        parts = []  # accumulates "up-Region" / "down-Region" tokens
        for feat, val in ranked.items():  # iterate over the surviving top features
            direction = "up" if val > 0 else "down"  # positive z = above population mean; negative = below
            parts.append(f"{direction}-{feat}")
        names[int(cid)] = f"cluster{int(cid)}: " + " ".join(parts)  # final label format; edit the f-string here to change the rendering convention
    return names


# ---------------------------------------------------------------------------
# Permutation / resampling cluster-validity test
# ---------------------------------------------------------------------------


def _within_cluster_variance(
    target: pd.DataFrame, labels: pd.Series
) -> float:
    """Sum of within-cluster variances across all features.

    Used as the statistic for cluster validity: clusters that capture real
    structure have lower within-cluster variance than random groups of the
    same size.
    """
    if isinstance(labels, pd.Series):  # reindex to target's index so groupby aligns correctly
        labels = labels.reindex(target.index)
    else:
        labels = pd.Series(labels, index=target.index)
    total = 0.0  # accumulator
    for cid, group in target.groupby(labels):  # one cluster at a time
        if len(group) > 1:  # variance is undefined for singletons; skip them so they don't contribute NaN
            total += group.var(axis=0, ddof=1).sum()  # ddof=1 for unbiased sample variance; .sum() collapses across features so the statistic is one number total
    return total


def permutation_validate(
    target: pd.DataFrame,
    labels: pd.Series,
    n_random: int = 500,
    random_state: int = 42,
) -> Dict[str, object]:
    """Test whether a clustering reduces within-cluster variance vs. chance.

    Two null distributions are computed:

    - **LOO**: one subject removed per iteration, recompute
      within-cluster variance on the reduced cohort. Low spread means the
      result is robust to any single subject.
    - **Random-equal-N**: shuffle the cluster labels (preserving group
      sizes), recompute within-cluster variance. Standard permutation
      null for group structure.

    Args:
        target: the dataframe whose within-cluster variance we measure
            (typically connectivity features, or a phase-specific
            kinematic subset).
        labels: subject_id -> cluster ID.
        n_random: number of shuffled permutations.
        random_state: RNG seed.

    Returns:
        Dict with:

        - ``observed``: within-cluster variance on the real clustering.
        - ``loo``: list of N LOO variances.
        - ``random``: list of ``n_random`` shuffled variances.
        - ``p_random``: fraction of ``random`` >= observed.
          Small => real clusters are tighter than chance.
    """
    rng = np.random.default_rng(random_state)  # reproducible RNG for the shuffle null
    labels = pd.Series(labels, index=target.index) if not isinstance(labels, pd.Series) else labels.reindex(target.index)  # normalize to a Series aligned with target's index
    observed = _within_cluster_variance(target, labels)  # the statistic on the real clustering

    # LOO
    loo_vals = []  # one variance per subject removed
    for subj in target.index:  # iterate over subjects
        loo_target = target.drop(index=subj)  # everyone except this subject
        loo_labels = labels.drop(index=subj)  # corresponding label vector
        loo_vals.append(_within_cluster_variance(loo_target, loo_labels))  # variance on the reduced cohort

    # Random-equal-N: permute labels, preserving group sizes
    random_vals = []  # one variance per shuffled draw
    label_array = labels.values.copy()  # copy because rng.permutation reorders in-place if you don't
    for _ in range(n_random):  # number of shuffled nulls; bigger = smoother histogram, slower
        permuted = rng.permutation(label_array)  # shuffle labels; group sizes are preserved automatically (we just reassigned subjects to the same set of labels)
        random_vals.append(_within_cluster_variance(target, pd.Series(permuted, index=target.index)))  # statistic on the shuffled labeling

    # p: fraction of random draws at least as small as observed (lower is better for real)
    p_random = float(np.mean(np.array(random_vals) <= observed))  # left-tail p-value: how many shuffled clusterings were at least as tight as the real one

    return {
        "observed": observed,
        "loo": loo_vals,
        "random": random_vals,
        "p_random": p_random,
    }


# ---------------------------------------------------------------------------
# Alluvial / Sankey source-record builder
# ---------------------------------------------------------------------------


def alluvial_source_records(
    labels_by_phase: Dict[str, pd.Series],
    phase_order: List[str],
) -> pd.DataFrame:
    """Flatten per-phase cluster assignments into ``(source, target, value)`` rows.

    Input: mapping ``{phase: labels_series}`` where each labels_series is
    indexed by subject_id. Output is a DataFrame with columns ``source``,
    ``target``, ``value`` suitable for plotly.graph_objects.Sankey.

    At small N with the default 4 one-subject clusters, every column has 4
    distinct labels and the Sankey is a trivial left-to-right routing.
    At larger N and fewer clusters the flow shows migration patterns.
    """
    records = []  # one record per (subject, phase-pair) -- we collapse to (source, target, count) at the end
    for src_phase, dst_phase in zip(phase_order[:-1], phase_order[1:]):  # iterate over consecutive phase pairs (Baseline->Post_Injury_1, etc.)
        src_labels = labels_by_phase[src_phase]  # cluster labels at the source phase
        dst_labels = labels_by_phase[dst_phase]  # cluster labels at the target phase
        common = src_labels.index.intersection(dst_labels.index)  # subjects with assignments at both phases (others are dropped from the flow)
        for subj in common:  # one record per subject per phase-pair transition
            records.append({
                "source_phase": src_phase,
                "target_phase": dst_phase,
                "source": f"{src_phase}::cluster{src_labels[subj]}",  # node ID format must be unique across the whole Sankey, so prefix with phase name
                "target": f"{dst_phase}::cluster{dst_labels[subj]}",
            })
    if not records:  # no phase pairs had any common subjects
        return pd.DataFrame(columns=["source_phase", "target_phase", "source", "target", "value"])
    df = pd.DataFrame(records)  # one row per (subject, transition)
    return df.groupby(["source_phase", "target_phase", "source", "target"], as_index=False).size().rename(columns={"size": "value"})  # collapse duplicates: count how many subjects took each (source, target) edge; this is the "value" plotly Sankey wants per link
