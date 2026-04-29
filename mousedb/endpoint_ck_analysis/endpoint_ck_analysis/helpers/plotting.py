"""Figure helpers shared across notebooks.

Keeps plotting logic out of the notebook cells so the notebooks read as
analysis narrative rather than matplotlib boilerplate. Functions here are
intentionally thin wrappers -- a user who wants to tweak a figure opens the
notebook and passes different keyword arguments, or overrides the helper
entirely with a local redefinition in that notebook.

See ``docs/customizing_figures.md`` for the common tweaks.
"""
from __future__ import annotations  # postpone-annotation evaluation

from typing import Any, Dict, Optional  # type-hint primitives

import matplotlib.pyplot as plt  # matplotlib.pyplot: standard 2D plotting; alias plt is conventional
import numpy as np  # numpy: arrays + math (linspace, polyfit, etc.)
import pandas as pd  # pandas: dataframe library
import scipy.stats as stats  # scipy.stats: Pearson r, p-values, etc.

from .. import __version__  # pull the package's version string for figure footers


def stamp_version(fig, label: Optional[str] = None) -> None:
    """Add a small footer to a figure with the tool version and an optional label.

    Useful so printed-out figures are traceable back to the code version that
    produced them. Append this to every figure the notebooks export.
    """
    text = f"endpoint_ck_analysis {__version__}"                                  # base footer text: package + version
    if label:                                                                      # caller passed an additional label (e.g. "08 drill-down")
        text = f"{text} | {label}"                                                # append it after a separator
    fig.text(0.99, 0.01, text, ha="right", va="bottom", fontsize=7, alpha=0.5)    # figure-level text: x=0.99 right edge, y=0.01 bottom edge; ha/va are alignment; alpha=0.5 makes it subtle


def plot_pca_for_phase(pca, eigen_summary: pd.DataFrame, loadings: pd.DataFrame, phase_label: str):
    """Produce a scree plot and R-style horizontal loading bars for one PCA result.

    Lifted verbatim from notebook section 9; no behavior changes.
    """
    # Scree plot
    plt.figure(figsize=(6, 4))                                                    # new figure; figsize is (width_inches, height_inches)
    plt.bar(range(1, len(pca.explained_variance_ratio_) + 1), pca.explained_variance_ratio_)  # bar per PC; x positions 1..k, heights are variance ratios
    plt.xticks(range(1, len(pca.explained_variance_ratio_) + 1), eigen_summary["Component"])  # use 'PC1', 'PC2', ... labels from the eigen summary
    plt.xlabel("Principal Component")
    plt.ylabel("Variance Explained")
    plt.title(f"Kinematics PCA ({phase_label}): Scree Plot")
    plt.show()                                                                    # render inline in the notebook

    # Loading bars, one panel per component
    fig, axes = plt.subplots(1, len(loadings), figsize=(18, 10), sharey=True)     # one row, n_components columns; sharey so loadings axes line up vertically
    if len(loadings) == 1:                                                        # subplots returns a scalar Axes when n=1; wrap it so the loop below still works
        axes = [axes]                                                              # wrap scalar ax into list for uniform iteration
    for i, pc in enumerate(loadings.index):                                       # iterate PCs (PC1, PC2, ...) with index
        sorted_loadings = loadings.loc[pc].sort_values()                          # sort this PC's loadings ascending so the barh chart reads small-to-large bottom-to-top
        axes[i].barh(sorted_loadings.index, sorted_loadings.values)               # horizontal bar: y=feature names, width=loading values
        axes[i].set_xlabel("Loading")
        axes[i].set_title(f"{pc} ({pca.explained_variance_ratio_[i]:.1%} var)")   # ":.1%" formats as percent with 1 decimal
        axes[i].axvline(0, color="black", linewidth=0.5)                          # zero reference line so positive vs negative loadings are obvious
    fig.suptitle(f"Kinematics PCA ({phase_label}): Loadings", y=1.02)             # figure-level title; y=1.02 nudges it above the subplots
    plt.tight_layout()                                                            # auto-adjust margins so labels don't clip
    plt.show()


def plot_pls(result: Dict[str, Any], top_n: int = 15, save_dir=None, slug: Optional[str] = None):
    """Three-panel figure per latent variable: X-loadings, Y-loadings, cross-scores scatter.

    Lifted from notebook section 10. Expects the dict returned by
    ``helpers.dimreduce.run_pls``.

    If ``save_dir`` and ``slug`` are both provided, each per-LV figure is also
    written to ``{save_dir}/{slug}_{LV}.png`` before display. Useful for the
    figure gallery and supplementary materials.
    """
    X_loadings = result["X_loadings"]                                             # connectivity-side loading matrix (region x LV)
    Y_loadings = result["Y_loadings"]                                             # kinematic-side loading matrix (feature x LV)
    X_scores = result["X_scores"]                                                 # subject scores on X-block LVs (N x LV)
    Y_scores = result["Y_scores"]                                                 # subject scores on Y-block LVs
    label = result["label"]                                                       # caller-provided figure label

    for i, lv in enumerate(X_loadings.columns):                                   # one figure per latent variable; lv is 'LV1', 'LV2', ...
        fig, axes = plt.subplots(1, 3, figsize=(22, 8))                           # 1 row x 3 columns: loadings X, loadings Y, scatter

        # X-loadings panel: top N connectivity regions by magnitude
        x_sorted = X_loadings.loc[X_loadings[lv].abs().nlargest(top_n).index, lv].sort_values()  # |loading| -> top N -> grab signed values -> sort for clean bar order
        axes[0].barh(x_sorted.index, x_sorted.values)                             # horizontal bars: regions on y-axis, loadings on x-axis
        axes[0].axvline(0, color="black", linewidth=0.5)                          # zero reference line
        axes[0].set_xlabel("Loading")
        axes[0].set_title(f"{lv}: top connectivity loadings")

        # Y-loadings panel: kinematic features (Y already filtered to important ones upstream)
        y_sorted = Y_loadings[lv].sort_values()                                   # sort full Y loadings (Y is already filtered to important features upstream)
        axes[1].barh(y_sorted.index, y_sorted.values)
        axes[1].axvline(0, color="black", linewidth=0.5)
        axes[1].set_xlabel("Loading")
        axes[1].set_title(f"{lv}: kinematic loadings")

        # Cross-score scatter: each subject as a point
        x_vals = X_scores[:, i]                                                   # column i of X_scores: each subject's value on LV_i
        y_vals = Y_scores[:, i]                                                   # column i of Y_scores: each subject's value on the matching Y LV
        axes[2].scatter(x_vals, y_vals)                                           # scatter; default marker
        for j, subj in enumerate(result["subjects"]):                             # annotate each point with its subject ID
            axes[2].annotate(subj, (x_vals[j], y_vals[j]))                        # text label at the point coordinates

        # Best-fit line and correlation stats
        r, p = stats.pearsonr(x_vals, y_vals)                                     # Pearson correlation between paired latent scores; r in [-1, 1]
        slope, intercept = np.polyfit(x_vals, y_vals, 1)                          # least-squares fit of a degree-1 polynomial (line) to the points
        line_x = np.array([x_vals.min(), x_vals.max()])                           # endpoints to draw the line: data range
        line_y = slope * line_x + intercept                                       # apply the line equation y = mx + b
        axes[2].plot(line_x, line_y, "r--", linewidth=1)                          # dashed red line; "r--" is matplotlib shorthand for red dashed
        axes[2].set_xlabel(f"X score ({lv})")
        axes[2].set_ylabel(f"Y score ({lv})")
        axes[2].set_title(f"{lv}: subject positions (r={r:.2f}, p={p:.3f})")     # title includes correlation stats
        axes[2].axhline(0, color="gray", linewidth=0.5, linestyle="--")           # horizontal zero-reference dashed line
        axes[2].axvline(0, color="gray", linewidth=0.5, linestyle="--")           # vertical zero-reference dashed line

        fig.suptitle(f"{label} - {lv}", y=1.02)                                   # figure-level title; y=1.02 nudges above subplots
        stamp_version(fig, label=f"PLS {lv}")                                     # version footer for traceability
        plt.tight_layout()                                                        # auto-fit margins
        if save_dir is not None and slug:                                         # caller wants the figure persisted; both args required to disambiguate from accidental partials
            from pathlib import Path                                              # local import keeps the top-of-file imports lean for the no-save path
            save_path = Path(save_dir) / f"{slug}_{lv}.png"                       # filename pattern: {slug}_LV1.png, {slug}_LV2.png, ...
            save_path.parent.mkdir(parents=True, exist_ok=True)                   # ensure target directory exists
            plt.savefig(save_path, dpi=150, bbox_inches="tight")                  # write before show(); bbox_inches='tight' trims whitespace, dpi=150 matches the rest of the pipeline
        plt.show()
