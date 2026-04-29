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
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist
from sklearn.cluster import KMeans
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler


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
    if X.empty:
        raise ValueError("Cluster input X is empty.")
    if X.isna().any().any():
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
    _check_inputs(X)
    data = StandardScaler().fit_transform(X.values) if scale else X.values

    if method == "ward":
        link = linkage(pdist(data), method="ward")
        labels = fcluster(link, t=k, criterion="maxclust")
        extras = {"linkage": link}
    elif method == "kmeans":
        km = KMeans(n_clusters=k, n_init=10, random_state=random_state)
        labels = km.fit_predict(data) + 1  # 1..K instead of 0..K-1
        extras = {"inertia": km.inertia_, "centers": km.cluster_centers_}
    elif method == "gmm":
        gmm = GaussianMixture(n_components=k, random_state=random_state)
        labels = gmm.fit_predict(data) + 1
        extras = {"proba": gmm.predict_proba(data)}
    elif method == "consensus":
        labels, extras = _consensus_cluster(
            data, k=k, n_resamples=consensus_n_resamples,
            frac=consensus_frac, random_state=random_state,
        )
    else:
        raise ValueError(f"Unknown method {method!r}. Use ward / kmeans / gmm / consensus.")

    labels_series = pd.Series(labels, index=X.index, name="cluster")
    return ClusterResult(labels=labels_series, method=method, k=int(labels_series.nunique()), extras=extras)


def _consensus_cluster(
    data: np.ndarray, k: int, n_resamples: int, frac: float, random_state: int
) -> Tuple[np.ndarray, Dict[str, object]]:
    """Bootstrap-resample k-means and return the modal label per subject.

    For each resample, fit k-means on ``frac`` of the subjects, then predict
    labels for everyone. Over the ``n_resamples`` rounds, each subject has
    a distribution of predicted labels; the mode is their final assignment.
    Label identity is resolved by Hungarian matching to the first fit.
    """
    from scipy.stats import mode as _mode
    rng = np.random.default_rng(random_state)
    n = data.shape[0]
    all_labels = []
    reference = None
    for round_idx in range(n_resamples):
        idx = rng.choice(n, size=max(2, int(frac * n)), replace=False)
        km = KMeans(n_clusters=k, n_init=5, random_state=round_idx + random_state)
        km.fit(data[idx])
        labels = km.predict(data)
        if reference is None:
            reference = labels
        else:
            # Align labels to reference via pairwise overlap (cheap Hungarian)
            aligned = _align_labels(labels, reference, k)
            labels = aligned
        all_labels.append(labels)
    arr = np.array(all_labels)               # shape (n_resamples, n_subjects)
    modal, _ = _mode(arr, axis=0, keepdims=False)
    modal = (modal.astype(int) + 1)          # 1..K
    agreement = (arr == (modal - 1)).mean(axis=0)
    return modal, {"agreement": agreement, "raw_labels": arr}


def _align_labels(to_align: np.ndarray, reference: np.ndarray, k: int) -> np.ndarray:
    """Greedy cluster-label alignment (for small k this is good enough)."""
    mapping = {}
    for new_label in range(k):
        mask = to_align == new_label
        if mask.sum() == 0:
            continue
        overlaps = np.array([np.sum((to_align == new_label) & (reference == r)) for r in range(k)])
        mapping[new_label] = int(overlaps.argmax())
    return np.array([mapping.get(l, l) for l in to_align])


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
    if isinstance(labels, pd.Series):
        labels = labels.reindex(X.index)
    else:
        labels = pd.Series(labels, index=X.index)
    grand_mean = X.mean(axis=0)
    grand_std = X.std(axis=0).replace(0, 1.0)  # guard zero-variance columns
    rows = {}
    for cid, group in X.groupby(labels):
        rows[cid] = (group.mean(axis=0) - grand_mean) / grand_std
    profile = pd.DataFrame(rows).T
    profile.index.name = "cluster"
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
    names: Dict[int, str] = {}
    for cid in profile.index:
        row = profile.loc[cid]
        ranked = row.reindex(row.abs().sort_values(ascending=False).index)
        ranked = ranked[ranked.abs() >= threshold].head(top_n)
        if len(ranked) == 0:
            names[int(cid)] = f"cluster{int(cid)}: central"
            continue
        parts = []
        for feat, val in ranked.items():
            direction = "up" if val > 0 else "down"
            parts.append(f"{direction}-{feat}")
        names[int(cid)] = f"cluster{int(cid)}: " + " ".join(parts)
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
    if isinstance(labels, pd.Series):
        labels = labels.reindex(target.index)
    else:
        labels = pd.Series(labels, index=target.index)
    total = 0.0
    for cid, group in target.groupby(labels):
        if len(group) > 1:
            total += group.var(axis=0, ddof=1).sum()
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
    rng = np.random.default_rng(random_state)
    labels = pd.Series(labels, index=target.index) if not isinstance(labels, pd.Series) else labels.reindex(target.index)
    observed = _within_cluster_variance(target, labels)

    # LOO
    loo_vals = []
    for subj in target.index:
        loo_target = target.drop(index=subj)
        loo_labels = labels.drop(index=subj)
        loo_vals.append(_within_cluster_variance(loo_target, loo_labels))

    # Random-equal-N: permute labels, preserving group sizes
    random_vals = []
    label_array = labels.values.copy()
    for _ in range(n_random):
        permuted = rng.permutation(label_array)
        random_vals.append(_within_cluster_variance(target, pd.Series(permuted, index=target.index)))

    # p: fraction of random draws at least as small as observed (lower is better for real)
    p_random = float(np.mean(np.array(random_vals) <= observed))

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
    records = []
    for src_phase, dst_phase in zip(phase_order[:-1], phase_order[1:]):
        src_labels = labels_by_phase[src_phase]
        dst_labels = labels_by_phase[dst_phase]
        common = src_labels.index.intersection(dst_labels.index)
        for subj in common:
            records.append({
                "source_phase": src_phase,
                "target_phase": dst_phase,
                "source": f"{src_phase}::cluster{src_labels[subj]}",
                "target": f"{dst_phase}::cluster{dst_labels[subj]}",
            })
    if not records:
        return pd.DataFrame(columns=["source_phase", "target_phase", "source", "target", "value"])
    df = pd.DataFrame(records)
    return df.groupby(["source_phase", "target_phase", "source", "target"], as_index=False).size().rename(columns={"size": "value"})
