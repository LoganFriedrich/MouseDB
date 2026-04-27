"""Figure helpers shared across notebooks.

Keeps plotting logic out of the notebook cells so the notebooks read as
analysis narrative rather than matplotlib boilerplate. Functions here are
intentionally thin wrappers -- a user who wants to tweak a figure opens the
notebook and passes different keyword arguments, or overrides the helper
entirely with a local redefinition in that notebook.

See ``docs/customizing_figures.md`` for the common tweaks.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy.stats as stats

from .. import __version__


def stamp_version(fig, label: Optional[str] = None) -> None:
    """Add a small footer to a figure with the tool version and an optional label.

    Useful so printed-out figures are traceable back to the code version that
    produced them. Append this to every figure the notebooks export.
    """
    text = f"cfs_analysis {__version__}"
    if label:
        text = f"{text} | {label}"
    fig.text(0.99, 0.01, text, ha="right", va="bottom", fontsize=7, alpha=0.5)


def plot_pca_for_phase(pca, eigen_summary: pd.DataFrame, loadings: pd.DataFrame, phase_label: str):
    """Produce a scree plot and R-style horizontal loading bars for one PCA result.

    Lifted verbatim from notebook section 9; no behavior changes.
    """
    # Scree plot
    plt.figure(figsize=(6, 4))
    plt.bar(range(1, len(pca.explained_variance_ratio_) + 1), pca.explained_variance_ratio_)
    plt.xticks(range(1, len(pca.explained_variance_ratio_) + 1), eigen_summary["Component"])
    plt.xlabel("Principal Component")
    plt.ylabel("Variance Explained")
    plt.title(f"Kinematics PCA ({phase_label}): Scree Plot")
    plt.show()

    # Loading bars, one panel per component
    fig, axes = plt.subplots(1, len(loadings), figsize=(18, 10), sharey=True)
    if len(loadings) == 1:
        axes = [axes]  # single-component edge case: wrap scalar ax into list for uniform iteration
    for i, pc in enumerate(loadings.index):
        sorted_loadings = loadings.loc[pc].sort_values()
        axes[i].barh(sorted_loadings.index, sorted_loadings.values)
        axes[i].set_xlabel("Loading")
        axes[i].set_title(f"{pc} ({pca.explained_variance_ratio_[i]:.1%} var)")
        axes[i].axvline(0, color="black", linewidth=0.5)
    fig.suptitle(f"Kinematics PCA ({phase_label}): Loadings", y=1.02)
    plt.tight_layout()
    plt.show()


def plot_pls(result: Dict[str, Any], top_n: int = 15):
    """Three-panel figure per latent variable: X-loadings, Y-loadings, cross-scores scatter.

    Lifted from notebook section 10. Expects the dict returned by
    ``helpers.dimreduce.run_pls``.
    """
    X_loadings = result["X_loadings"]
    Y_loadings = result["Y_loadings"]
    X_scores = result["X_scores"]
    Y_scores = result["Y_scores"]
    label = result["label"]

    for i, lv in enumerate(X_loadings.columns):  # One figure per latent variable
        fig, axes = plt.subplots(1, 3, figsize=(22, 8))

        # X-loadings panel: top N connectivity regions by magnitude
        x_sorted = X_loadings.loc[X_loadings[lv].abs().nlargest(top_n).index, lv].sort_values()
        axes[0].barh(x_sorted.index, x_sorted.values)
        axes[0].axvline(0, color="black", linewidth=0.5)
        axes[0].set_xlabel("Loading")
        axes[0].set_title(f"{lv}: top connectivity loadings")

        # Y-loadings panel: kinematic features (Y already filtered to important ones upstream)
        y_sorted = Y_loadings[lv].sort_values()
        axes[1].barh(y_sorted.index, y_sorted.values)
        axes[1].axvline(0, color="black", linewidth=0.5)
        axes[1].set_xlabel("Loading")
        axes[1].set_title(f"{lv}: kinematic loadings")

        # Cross-score scatter: each subject as a point
        x_vals = X_scores[:, i]
        y_vals = Y_scores[:, i]
        axes[2].scatter(x_vals, y_vals)
        for j, subj in enumerate(result["subjects"]):
            axes[2].annotate(subj, (x_vals[j], y_vals[j]))

        # Best-fit line and correlation stats
        r, p = stats.pearsonr(x_vals, y_vals)
        slope, intercept = np.polyfit(x_vals, y_vals, 1)
        line_x = np.array([x_vals.min(), x_vals.max()])
        line_y = slope * line_x + intercept
        axes[2].plot(line_x, line_y, "r--", linewidth=1)
        axes[2].set_xlabel(f"X score ({lv})")
        axes[2].set_ylabel(f"Y score ({lv})")
        axes[2].set_title(f"{lv}: subject positions (r={r:.2f}, p={p:.3f})")
        axes[2].axhline(0, color="gray", linewidth=0.5, linestyle="--")
        axes[2].axvline(0, color="gray", linewidth=0.5, linestyle="--")

        fig.suptitle(f"{label} - {lv}", y=1.02)
        stamp_version(fig, label=f"PLS {lv}")
        plt.tight_layout()
        plt.show()
