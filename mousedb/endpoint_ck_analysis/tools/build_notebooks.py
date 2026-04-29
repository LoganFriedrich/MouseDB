"""Build all analysis notebooks from in-script cell definitions.

Developer script (not shipped with the tool). Keeps notebook content under
version control as readable Python-and-string blocks rather than as
unreadable JSON.

Run:
    python tools/build_notebooks.py

Writes every notebook under ``notebooks/`` based on the NOTEBOOKS list below.
Each notebook entry has a filename and a list of (cell_type, source) cells.
``cell_type`` is 'md' or 'code'.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from textwrap import dedent

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent
NOTEBOOKS_DIR = PROJECT_ROOT / "notebooks"


def make_cell(cell_type: str, source: str) -> dict:
    # Translate the short tag we use in-script to the nbformat cell-type name.
    nb_type = {"md": "markdown", "code": "code"}.get(cell_type, cell_type)
    src_lines = source.splitlines(keepends=True)
    cell = {
        "cell_type": nb_type,
        "metadata": {},
        "source": src_lines,
    }
    if nb_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    if nb_type == "code" and "# parameters" in source:
        cell["metadata"]["tags"] = ["parameters"]
    return cell


def make_notebook(cells: list[tuple[str, str]]) -> dict:
    """Build a valid .ipynb JSON structure from a list of (type, source) cells."""
    return {
        "cells": [make_cell(t, dedent(s).strip("\n") + "\n") for t, s in cells],
        "metadata": {
            "kernelspec": {
                "display_name": "Python (endpoint_ck_analysis)",
                "language": "python",
                "name": "endpoint_ck_analysis",
            },
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


# ============================================================================
# 00_setup.ipynb
# ============================================================================

SETUP_NB = [
    ("md", """
        # 00 - Setup and data load

        **Purpose**: verify the environment is healthy, load every dataframe the
        other notebooks need, and write them to a parquet cache so the
        downstream notebooks open fast.

        Run this notebook **first**. After the final cell finishes, open any of
        the 01-06 notebooks in order -- they all read from the cache produced
        here and never touch the SQLite database directly.
    """),
    ("md", """
        ## 1. Environment health check

        Runs ``endpoint_ck_analysis.doctor.doctor()`` which prints a table of required
        pieces (Python version, packages, database file, writable cache,
        region-prior source). Everything should be ``[OK]``.

        If anything is ``[FAIL]``:
          - Missing package? The "detail" column gives the ``pip install`` command.
          - Database file not found? Copy ``connectome.db`` into
            ``endpoint_ck_analysis/_bundled_data/`` (the folder you see next to this
            notebook's parent) or set ``ENDPOINT_CK_ANALYSIS_DB`` to its location.
          - Still stuck? Open ``TROUBLESHOOTING.md`` in the project root.
    """),
    ("code", """
        from endpoint_ck_analysis.doctor import doctor # entry point that runs every diagnostic check and prints a status table
        doctor() # prints the [OK]/[FAIL]/[INFO] table; returns True when there are no blockers
    """),
    ("md", """
        **What you just saw.** Every required check should show `[OK]`. Optional
        items (like `jupyterlab` if you're using VS Code) may show `[INFO]` --
        that's fine and expected. Any `[FAIL]` line blocks the analysis; the
        detail column on that line tells you what to do (usually a single
        `pip install ...` command). If everything is green, you can move on.
    """),
    ("md", """
        ## 2. Load all dataframes

        ``load_all`` reads ``connectome.db`` (location resolved by
        ``config.get_db_path()``), runs every filter/aggregation/pivot from
        Section 6 of the original notebook, and returns a :class:`LoadedData`
        container with everything downstream code needs.

        On first run this takes ~30 seconds (SQL + aggregation). Subsequent
        runs read the cache in under a second unless you delete it or pass
        ``use_cache=False`` to force a rebuild.
    """),
    ("code", """
        from endpoint_ck_analysis.data_loader import load_all # one-shot loader that returns a LoadedData with every dataframe downstream notebooks consume

        data = load_all(use_cache=False, write_cache=True) # use_cache=False forces a fresh DB rebuild; write_cache=True saves parquet so notebooks 01-08 don't re-touch the DB. Set use_cache=True after the first run for fast reloads.

        print()
        print('Base dataframes:') # AKDdf and friends are the per-reach / per-region tables; everything else is derived from these
        for name in ['AKDdf', 'FKDdf', 'ACDUdf', 'ACDGdf', 'FCDUdf', 'FCDGdf']: # iterate by name string so the loop also prints the variable name alongside the shape
            print(f'  {name}: {getattr(data, name).shape}') # getattr(data, name) pulls the dataframe attribute matching the loop variable; .shape gives (rows, columns)

        print()
        print('Connectomics wide pivots:') # one row per subject, one column per region_hemi; produced by helpers.connectivity.pivot_connectivity
        for name in ['ACDUdf_wide', 'ACDGdf_wide', 'FCDUdf_wide', 'FCDGdf_wide']:
            print(f'  {name}: {getattr(data, name).shape}')

        print()
        print(f'Matched subjects (both kinematics and connectomics): {list(data.matched_subjects)}') # the analyzable cohort: only subjects in this list participate in PLS or any cross-block analysis
    """),
    ("md", """
        **What you just saw.** Every dataframe shape should be non-empty. The
        first block is the base data (`AKDdf` is the all-mice kinematic
        dataframe; `F*` versions are filtered to the matched cohort, `*Udf` are
        ungrouped/atomic-region, `*Gdf` are eLife-grouped). The wide pivots
        are subject x region_hemi matrices used by every connectomics analysis
        downstream. The `Matched subjects` line is the most consequential:
        only mice with both kinematics AND connectomics data appear there, and
        every cross-block question in 04 / 07 is answered from that cohort.

        If any shape is `(0, 0)` or the matched-subjects list is empty, the
        upstream pipeline didn't produce what we expected -- usually means the
        wrong DB is configured, recent backfills weren't applied, or the
        imaging-parameter filter excluded everything. Check `connectome.db`
        before continuing.
    """),
    ("md", """
        ## 3. Quick-look previews

        Spot-check that the data resembles what you expect before running the
        analytical notebooks. Uncomment any lines that would be useful to see.
    """),
    ("code", """
        from IPython.display import display                                                          # display(): force-renders any object as cell output. Plain `df.head()` only shows when it's the LAST line of the cell, so for multi-preview cells we wrap each in display() instead.
        # Uncomment any of the lines below to peek at the loaded data. Each one renders a different dataframe so you can sanity-check shape and contents before continuing.
        # display(data.AKDdf.head())                                                                 # first 5 rows of All Kinematic Data: per-reach kinematic features for every analyzable phase
        # display(data.FCDGdf_wide)                                                                  # Filtered Connectivity Data Grouped (eLife) -- wide subjects x region_hemi matrix; matched-subject rows only
        # display(data.AKDdf_agg_contact.head())                                                     # first 5 rows of the (subject, phase, contact_group) aggregation: mean/std/median/q25/q75 per kinematic feature
        pass                                                                                          # 'pass' is a no-op placeholder; required because Python disallows an empty cell body when all real lines are commented out. With display(), the position of pass no longer matters: every uncommented display() call still prints.
    """),
    ("md", """
        ## Next

        - **01_connectivity_pca.ipynb** -- PCA on the connectivity matrix.
        - **02_kinematic_pca.ipynb** -- PCA on kinematics, one per phase, with sign alignment.
        - **03_kinematic_clustering.ipynb** -- hierarchical clustering of kinematic features.
        - **04_pls_variants.ipynb** -- PLS across connectivity and kinematics (injury / deficit / recovery).
        - **05_lmm_phase_effects.ipynb** -- Linear mixed models for per-feature phase effects.
        - **06_pellet_validation.ipynb** -- manual vs algorithmic pellet scoring agreement.
        - **99_figure_gallery.ipynb** -- all saved figures in one place.
    """),
]


# ============================================================================
# 01_connectivity_pca.ipynb
# ============================================================================

CONNECTIVITY_PCA_NB = [
    ("md", """
        # 01 - Connectivity PCA

        **Purpose**: reduce the eLife-grouped connectivity matrix (subjects x regions)
        to a few principal components and inspect which regions drive each.

        **Input**: ``data.FCDGdf_wide`` -- connectomics filtered to matched subjects,
        eLife-grouped, wide format.

        **Caveat**: N=few matched subjects. PCA results are shown to
        validate the pipeline end-to-end; interpretation at small N is
        suggestive, not statistical. Re-run as N grows.

        Figures written to ``example_output/``:
          - ``01_scree.png``
          - ``01_loadings_all.png``
          - ``01_loadings_important.png``
          - ``01_loadings_heatmap.png``
          - ``01_connectivity_heatmap.png``
    """),
    ("code", """
        # parameters
        # Tweak these values to re-render with different settings. No other edits needed.
        N_COMPONENTS = 3       # how many principal components to extract
        TOP_N_REGIONS = 10     # how many regions to mark as "important" per component
        FIGSIZE_SCREE = (8, 6)
        FIGSIZE_LOADINGS = (30, 30)
        FIGSIZE_LOADINGS_FILTERED = (20, 10)
        FIGSIZE_HEATMAP = (6, 10)
        FIGSIZE_SUBJECT_REGION = (24, 4)
    """),
    ("code", """
        import numpy as np                              # numpy: fast numerical arrays + math; aliased as np by convention
        import pandas as pd                             # pandas: dataframe library (rows + named columns, like a spreadsheet); aliased as pd
        import matplotlib.pyplot as plt                 # matplotlib's plotting interface; plt.figure(), plt.bar(), etc.
        import seaborn as sns                           # seaborn: statistical plotting layer on top of matplotlib; gives nicer heatmaps
        from sklearn.preprocessing import StandardScaler # z-scorer: subtracts mean, divides by std so each feature has comparable magnitude
        from sklearn.decomposition import PCA           # principal component analysis: finds axes of maximum variance in a dataset

        import endpoint_ck_analysis                     # our package; importing it triggers the soft-import of mousedb.region_priors with a frozen fallback
        from endpoint_ck_analysis import SKILLED_REACHING, ordered_hemisphere_columns # canonical region prior + helper that builds {region}_{hemisphere} column names in priority order
        from endpoint_ck_analysis.config import EXAMPLE_OUTPUT_DIR # where saved PNGs go; resolves to ../example_output/ relative to the package
        from endpoint_ck_analysis.data_loader import load_all # one-shot loader; returns a LoadedData container with every base/derived dataframe
        from endpoint_ck_analysis.helpers.plotting import stamp_version # adds a small "tool version" footer to a figure for traceability

        EXAMPLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True) # ensure the example_output folder exists before we try to save into it

        data = load_all()  # default uses the cache produced by 00_setup; pass use_cache=False to force a fresh DB rebuild
    """),
    ("md", """
        ## 1. Prepare the matrix and run PCA

        Apply the canonical region ordering from ``mousedb.region_priors`` so
        every subsequent plot shares the same row/column order -- predicted-important
        regions sit together at the top.
    """),
    ("code", """
        X = data.FCDGdf_wide.fillna(0)  # FCDGdf_wide is the matched-subjects connectivity matrix (rows=subjects, cols=region_hemi); fillna(0) replaces missing cells with 0 because PCA cannot handle NaNs

        # Canonical column order: predicted importance for skilled reaching, both/left/right per region.
        canonical_cols = ordered_hemisphere_columns(           # build the ordered list of {region}_{hemi} column names...
            SKILLED_REACHING, available=X.columns.tolist()      # ...filtered to only those columns the dataframe actually has (some hemispheres may be absent)
        )
        X = X[canonical_cols]  # reorder X's columns; every downstream plot inherits this column ordering automatically

        scaler = StandardScaler()            # set up the z-scorer (does nothing yet, just holds the transformation)
        X_scaled = scaler.fit_transform(X)   # fit() learns each column's mean+std, transform() applies the z-score; result is a numpy array

        pca = PCA(n_components=N_COMPONENTS) # PCA decomposer; n_components caps how many principal components to extract (parameters cell sets this)
        scores = pca.fit_transform(X_scaled) # fit() finds the components, transform() projects each subject onto them; scores[i, j] = subject i's coordinate on PCj

        eigen_summary = pd.DataFrame({                                                     # build a small table summarizing the components
            'Component': [f'PC{i+1}' for i in range(N_COMPONENTS)],                        # PC1, PC2, ... names (i+1 because humans count from 1, Python from 0)
            'Eigenvalue': pca.explained_variance_,                                         # raw variance captured by each PC (in z-scored units)
            'Variance': pca.explained_variance_ratio_,                                     # same thing as a fraction of total variance (sums to <=1)
            'Cumulative': np.cumsum(pca.explained_variance_ratio_),                        # running total -- shows how much variance the first k PCs together capture
        })
        print(eigen_summary) # print the summary table; useful first sanity check on the decomposition
    """),
    ("md", """
        ## 2. Scree plot

        Variance explained by each principal component. If PC1 and PC2 together
        explain most of the variance, a 2D plot captures the dominant structure.
    """),
    ("code", """
        fig = plt.figure(figsize=FIGSIZE_SCREE)                                          # new matplotlib figure; figsize is (width, height) in inches
        plt.bar(range(1, N_COMPONENTS + 1), pca.explained_variance_ratio_)               # bar chart: x = PC index 1..K, y = variance fraction explained by each PC
        plt.xticks(range(1, N_COMPONENTS + 1), eigen_summary['Component'])               # replace numeric x-tick labels with the PC1/PC2/... strings from eigen_summary
        plt.xlabel('Principal Component')                                                # x-axis label (edit the string to change what shows on the plot)
        plt.ylabel('Variance Explained')                                                 # y-axis label
        plt.title('Connectivity PCA: Variance per PC')                                   # plot title
        stamp_version(fig, label='01 scree')                                             # adds the small tool-version footer; helps trace which run produced this PNG
        plt.savefig(EXAMPLE_OUTPUT_DIR / '01_scree.png', dpi=150, bbox_inches='tight')   # write to disk; dpi controls resolution, bbox_inches='tight' trims whitespace
        plt.show()                                                                       # render in the notebook output cell
    """),
    ("md", """
        **What this figure shows.** Variance explained per principal component
        on the residual-connectivity matrix. Bar height = fraction of total
        cohort variance captured by that PC.

        **What different patterns mean.** A tall PC1 alone (>50% variance) =
        one shared axis dominates how mice differ in residual connectivity.
        Mice are roughly orderable along that axis. PC1 + PC2 together
        capturing most variance = two-axis structure; the residual landscape
        is mostly 2D. Variance spread evenly across many PCs = mice differ on
        many independent dimensions; no single axis summarizes the cohort.

        **What this means scientifically.** Residual connectivity here is the
        joint product of innate variation and a uniform injury procedure
        producing non-uniform outcomes. PC1 is NOT lesion severity; it's the
        dominant direction along which what's-still-connected differs across
        mice. Whatever shape this scree shows, the PCs above feed into the
        PLS in 04 as the X-block axes for the kinematics question.
    """),
    ("md", """
        ## 3. Loadings: all regions, in canonical order

        Horizontal bars per PC. A region with a long bar (positive or negative)
        contributes strongly to that component's differentiation between subjects.
    """),
    ("code", """
        loadings = pd.DataFrame(                                              # wrap the raw numpy components matrix into a labeled DataFrame
            pca.components_,                                                  # shape (n_components, n_features); each row is one PC's contribution from each feature
            columns=X.columns,                                                # reattach the region_hemi column names so we can read them on the plot
            index=[f'PC{i+1}' for i in range(N_COMPONENTS)],                  # name each row PC1, PC2, ...
        )

        fig, axes = plt.subplots(1, N_COMPONENTS, figsize=FIGSIZE_LOADINGS, sharey=True) # one row, N_COMPONENTS columns of subplots; sharey=True so y-axis labels (region names) stay aligned across PCs
        for i, pc in enumerate(loadings.index):                                          # i is the integer index, pc is the string 'PC1'/'PC2'/...; loop fills each subplot
            ordered = loadings.loc[pc, canonical_cols]  # pull this PC's row, restricted+reordered to canonical_cols (predicted-importance order)
            axes[i].barh(ordered.index, ordered.values)                                  # horizontal bars: y=region names, x=loading magnitude
            axes[i].set_xlabel('Loading')                                                # x-axis label
            axes[i].set_title(f'{pc} ({pca.explained_variance_ratio_[i]:.1%} var)')      # title shows PC name + how much variance it captured (.1%) formats as a percentage with 1 decimal place
            axes[i].axvline(0, color='black', linewidth=0.5)                             # thin vertical line at zero so positive vs negative loadings are easy to see
            axes[i].invert_yaxis()  # default barh draws first row at the bottom; invert so the highest-priority region (first in canonical_cols) sits on top
        stamp_version(fig, label='01 loadings (all)')                                    # version footer
        plt.savefig(EXAMPLE_OUTPUT_DIR / '01_loadings_all.png', dpi=150, bbox_inches='tight') # save the figure to disk
        plt.show()                                                                        # render inline in the notebook
    """),
    ("md", """
        **What these figures show.** One subplot per PC. Each horizontal bar
        is one region's loading -- how much that region contributes to
        differentiating mice along that PC. Long bar = strong contribution;
        short bar = weak. Sign is arbitrary on its own; what matters is
        magnitude and relative ordering. Regions are stacked top-to-bottom in
        prior-priority order (high-priority on top).

        **Top loadings -- what they mean.** A region with a long bar on PC1 is
        where residual connectivity varies most across this cohort. *Example:*
        if `Corticospinal_both` has a long bar on PC1, mice retained very
        different amounts of CST connectivity post-injury, and that region is
        a useful axis for distinguishing one mouse from another.

        **Low loadings -- what they mean.** A region with a near-zero bar
        doesn't vary much across mice on that PC. *Example:* if
        `Vestibular Nuclei_both` has a tiny PC1 bar, every mouse retained
        similar vestibular connectivity -- this region can't distinguish
        between mice along PC1. Not a defect; the variance just lives
        elsewhere (a different PC, or genuinely small).

        **Scientific reading.** If the long bars cluster in the top rows
        (high-priority reaching regions per `SKILLED_REACHING`), residual
        variation is concentrated where the literature says reaching is
        controlled -- predictive coupling to kinematics has a fighting chance
        in 04. If the long bars are concentrated in the bottom rows,
        cohort-wide residual variation is in tracts the prior de-emphasizes;
        either the prior misses something or the variance is in regions the
        reaching circuit doesn't lean on.
    """),
    ("md", """
        ## 4. Identify the top-loading regions per PC

        For each component, pick the ``TOP_N_REGIONS`` regions by absolute
        loading magnitude. The union across components defines the
        "important regions" that carry forward into PLS as X-block features.
    """),
    ("code", """
        important_regions = {}                                                  # dict mapping PC name to its top-loading regions
        for pc in loadings.index:                                               # iterate over PC1, PC2, ... PC{N_COMPONENTS}
            region_importance = loadings.loc[pc].abs()                          # absolute value of this PC's loadings (sign doesn't matter for "is this region influential")
            top_regions = region_importance.nlargest(TOP_N_REGIONS)             # keep the TOP_N_REGIONS regions with the largest |loading|
            important_regions[pc] = top_regions                                 # store this PC's top set
            print(f"\\nTop {TOP_N_REGIONS} regions on {pc} by |loading|:")     # \\n inserts a blank line so successive PCs are visually separated
            print(top_regions)

        all_important_regions = set()                                           # collect the union of important regions across PCs (deduplicated)
        for pc, series in important_regions.items():                            # walk through each PC's top set
            all_important_regions.update(series.index)                          # add this PC's region names to the union (set.update accepts any iterable)
        print(f"\\n{len(all_important_regions)} unique important regions:")
        print(sorted(all_important_regions))                                    # alphabetical for stable display across runs
    """),
    ("md", """
        **What you just saw.** Per-PC top region lists, then their union. The
        union is what gets carried forward as the X-block for PLS in 04 --
        only regions whose residual connectivity has appreciable variance on
        SOME PC participate in coupling to kinematics. Regions that never make
        the list contribute mostly noise from this cohort's perspective and
        are dropped.

        **Reading the per-PC lists.** A region appearing in PC1's top set
        means it drives the dominant axis of variation. A region appearing
        only in PC2 or PC3 contributes to a secondary axis -- still useful
        but not dominant. A region appearing in all three suggests it varies
        on multiple independent axes simultaneously.
    """),
    ("md", """
        ## 5. Loadings: important regions only (canonical order)
    """),
    ("code", """
        important_cols_ordered = ordered_hemisphere_columns(                    # rebuild the column ordering, but restricted to just the important regions...
            SKILLED_REACHING, available=list(all_important_regions)             # ...still keeping prior-priority order within that subset
        )
        fig, axes = plt.subplots(1, N_COMPONENTS, figsize=FIGSIZE_LOADINGS_FILTERED) # one subplot per PC, narrower than the all-regions version because there are fewer rows
        for i, pc in enumerate(loadings.index):
            pc_top = loadings.loc[pc, important_cols_ordered]                   # pull this PC's loadings, filtered+ordered to important regions
            axes[i].barh(pc_top.index, pc_top.values)                           # horizontal bars: region names on y, loading magnitude on x
            axes[i].axvline(0, color='black', linewidth=0.5)                    # zero reference line
            axes[i].set_xlabel('Loading')
            axes[i].set_title(f'{pc} (important regions only)')
            axes[i].invert_yaxis()                                              # priority-region-on-top convention again
        stamp_version(fig, label='01 loadings (important)')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '01_loadings_important.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        **What this figure shows.** Same loadings as the previous bar chart,
        but restricted to only the regions that made any PC's top-N list.
        Cleaner view of where the action is once we drop the regions that
        contribute little variance.

        **What to read into it.** The same shape as the all-regions plot, just
        with the noise-floor regions filtered out. Sign and magnitude tell
        you which regions drive each PC most strongly within the
        already-filtered subset. If most of these surviving regions are
        prior-priority (top of `SKILLED_REACHING`), the data and the
        literature agree on what matters in this cohort. If they're
        prior-low-priority, the data is pointing somewhere the prior didn't
        anticipate -- worth flagging for the discussion.
    """),
    ("md", """
        ## 6. Loadings heatmap
    """),
    ("code", """
        important_loadings = loadings[important_cols_ordered].T  # select important columns then transpose (.T) so regions are rows and PCs are columns; matches the conventional heatmap orientation
        fig = plt.figure(figsize=FIGSIZE_HEATMAP)                # new figure
        ax = sns.heatmap(important_loadings, cmap='RdBu_r', center=0,  # red-blue diverging colormap centered at 0 so positive/negative loadings render as red/blue
                         cbar_kws={'label': 'Loading'})          # cbar_kws labels the colorbar legend
        ax.invert_yaxis()                                        # priority-region-on-top
        plt.title('Connectivity loadings (important regions only)')
        stamp_version(fig, label='01 loadings heatmap')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '01_loadings_heatmap.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        **What this figure shows.** The same loadings as the bar chart, but
        as a heatmap. Rows are regions (high-priority on top), columns are
        PCs. Red = positive loading, blue = negative, intensity = magnitude.

        **What to read into it.** Look at each region's row across columns.
        A row that's strongly red on PC1 and pale on PC2/PC3 = this region
        contributes to PC1 only. A row that's red on PC1 and blue on PC2 =
        this region contributes to multiple axes (PC1 and PC2 separate mice
        in different ways using this region's residual connectivity).

        **Why this view helps.** The bar chart shows magnitude clearly; the
        heatmap shows multi-PC patterns clearly. Regions with strong
        signals across multiple PCs are doing the most work in the
        decomposition, so they're the regions whose residual connectivity
        most distinguishes mice in this cohort.
    """),
    ("md", """
        ## 7. Subject x region cell-count heatmap

        Each row is a subject, each column is a region (canonical order). The
        red dashed line marks the boundary between predicted-important regions
        (above) and kept-for-contrast regions (below).
    """),
    ("code", """
        fig = plt.figure(figsize=FIGSIZE_SUBJECT_REGION)                                       # new figure; FIGSIZE_SUBJECT_REGION is set in the parameters cell to the wide aspect this heatmap needs
        ax = sns.heatmap(X, cmap='viridis', cbar_kws={'label': 'Cell count'})                  # heatmap of raw cell counts (rows=subjects, cols=regions); viridis is a perceptually-uniform colormap

        high_priority_region_names = set(                                                      # build a set of the top-priority region names...
            SKILLED_REACHING.ordered_regions[:SKILLED_REACHING.high_priority_cutoff]            # ...by slicing the prior at its declared cutoff index
        )
        cutoff_cols = sum(                                                                     # count how many columns in canonical_cols belong to a high-priority region...
            1 for c in canonical_cols if c.rsplit('_', 1)[0] in high_priority_region_names     # ...c.rsplit('_', 1)[0] strips the trailing _both/_left/_right suffix to recover the region name
        )                                                                                       # this gives the x-axis position to draw the priority/contrast divider
        ax.axvline(cutoff_cols, color='red', linestyle='--', linewidth=1.5)                    # red dashed vertical line marking the priority cutoff
        plt.title('Subject x region cell counts (canonical order; red = priority cutoff)')
        stamp_version(fig, label='01 cell counts')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '01_connectivity_heatmap.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        **What this figure shows.** The raw residual cell-count matrix. Each
        row is one mouse, each column is one region in canonical
        prior-priority order. Bright cells = mouse retained lots of cells
        connected to that region; dark cells = few cells. The red dashed
        vertical line is the priority cutoff (`SKILLED_REACHING.high_priority_cutoff`):
        regions to the LEFT of the line are predicted-important for skilled
        reaching; regions to the RIGHT are kept for contrast.

        **What different patterns mean.** Bright cells clustered to the LEFT
        of the line = mice retained substantial connectivity in regions the
        literature flags for reaching, AND that connectivity varies across
        mice -- the prior aligns with what's structurally present in this
        cohort. Bright cells spread evenly across both sides, or
        concentrated to the RIGHT, = the cohort's residual connectivity is
        elsewhere. Either the literature is missing relevant tracts, or this
        cohort happens to retain connectivity in regions less central to
        reaching.

        **Reading rows.** Rows that are very different from each other
        (mostly bright vs mostly dark) reveal the mice that retained
        dramatically different amounts of total descending connectivity --
        these are likely the mice driving PC1. Rows that look similar means
        those mice have similar overall residual profiles even if they
        differ region-by-region.
    """),
    ("md", """
        ## 8. Export the important-regions list for the PLS notebook

        The set is written to the cache as ``important_regions.parquet`` so
        ``04_pls_variants`` can read it without recomputing.
    """),
    ("code", """
        from endpoint_ck_analysis.config import CACHE_DIR                          # cache dir lives next to the bundled DB; downstream notebooks read parquet files from here
        pd.Series(sorted(all_important_regions), name='region_hemi').to_frame().to_parquet( # Series -> DataFrame so parquet has a named column; sorted for deterministic ordering
            CACHE_DIR / 'important_regions.parquet', index=False                   # parquet is a fast columnar format; index=False because we don't need the integer row index here
        )
        print(f'Wrote {len(all_important_regions)} important regions to cache.')   # short status so the user can confirm the export happened
    """),
]


# ============================================================================
# 02_kinematic_pca.ipynb
# ============================================================================

KINEMATIC_PCA_NB = [
    ("md", """
        # 02 - Kinematic PCA per phase

        **Purpose**: fit PCA separately on each experimental phase's
        (subject, contacted) kinematic summary and compare loading patterns
        across phases.

        **Input**: ``data.AKDdf_agg_contact`` (aggregated kinematics by
        contact_group), flattened.

        **Caveat**: at small N each per-phase PCA has the matched-subject count as its row count x many features --
        PC1 is well-estimated, higher PCs are noisy.
    """),
    ("code", """
        # parameters
        PHASES = ['Baseline', 'Post_Injury_1', 'Post_Injury_2-4', 'Post_Rehab_Test']
        N_COMPONENTS = 3
        TOP_N_FEATURES = 5  # Features to count as "important" per PC when pooling across phases
        FIGSIZE_HEATMAP = (24, 10)
    """),
    ("code", """
        import numpy as np                                                       # numerical arrays
        import pandas as pd                                                      # dataframe library
        import matplotlib.pyplot as plt                                          # plot interface
        import seaborn as sns                                                    # statistical plotting (heatmaps here)

        from endpoint_ck_analysis.config import CACHE_DIR, EXAMPLE_OUTPUT_DIR    # cache_dir for parquet writes; example_output_dir for saved PNGs
        from endpoint_ck_analysis.data_loader import load_all                    # one-shot data loader
        from endpoint_ck_analysis.helpers.dimreduce import run_pca_for_phase, align_signs_to_reference # per-phase PCA helper + cross-phase sign alignment
        from endpoint_ck_analysis.helpers.plotting import plot_pca_for_phase, stamp_version            # per-phase scree+loadings plot helper + figure version footer

        EXAMPLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)                    # ensure output folder exists
        data = load_all()                                                        # load (uses cache from 00_setup)
        agg_flat = data.AKDdf_agg_contact_flat()                                 # flatten the multi-index AKDdf_agg_contact so we can filter on phase_group + contact_group as regular columns
    """),
    ("md", """
        ## 1. Fit PCA per phase

        ``run_pca_for_phase`` filters to the phase + contacted reaches, scales,
        runs PCA, and returns the fit plus its scores / loadings.
    """),
    ("code", """
        pcas = {phase: run_pca_for_phase(agg_flat, phase, n_components=N_COMPONENTS) for phase in PHASES} # dict-comprehension: one PCA fit per phase, key = phase name, value = the helper's tuple return
        for phase, (pca, _, eigen, _, _) in pcas.items():                                                  # iterate over each phase's result; the underscores discard the scores/loadings/X we don't need for this print
            print(f'{phase}: variance explained = {pca.explained_variance_ratio_.round(3)}')               # .round(3) limits to 3 decimal places for readability
    """),
    ("md", """
        **What you just saw.** The per-phase variance-explained ratios. Each
        line is one phase, with the variance share captured by PC1, PC2,
        PC3 within that phase.

        **What different patterns mean.** Similar ratios across all four
        phases = the dimensionality of how mice differ in reaching is stable
        across the experiment; the same number of "main directions" exists
        before injury, post-injury, and post-ABT. Very different ratios =
        the kinematic state space changes shape with phase. *Example:* if
        Baseline shows PC1=0.6 / PC2=0.2 / PC3=0.1 but Post_Injury_2-4
        shows PC1=0.3 / PC2=0.3 / PC3=0.2, injury produced a wider spectrum
        of compensatory reaching styles, with each mouse exploring its own
        independent strategy.
    """),
    ("md", """
        ## 2. Per-phase scree + loading figures

        ``plot_pca_for_phase`` emits one scree plot and a loading-bars figure
        per phase. Saves PNGs for the gallery.
    """),
    ("code", """
        for phase, (pca, _, eigen, loadings, _) in pcas.items():     # iterate phase by phase
            plot_pca_for_phase(pca, eigen, loadings, phase)          # helper renders scree + loading bars; uses plt.show() so each phase appears as its own pair of inline figures
    """),
    ("md", """
        **What these figures show.** For each phase, two figures: a scree plot
        (variance per PC) and a loading bars panel (which kinematic features
        drive each PC at that phase).

        **Top loadings -- what they mean.** A feature with a long bar on
        PC1 at a given phase is one along which mice differ a lot at that
        phase. *Example:* if `peak_velocity_mm_per_sec` loads strongly on
        PC1 at Post_Injury_1 but weakly at Baseline, injured mice differ
        greatly in how fast they reach (some retain near-baseline speed,
        others are dramatically slowed) while baseline mice all reach at
        similar speeds.

        **Low loadings -- what they mean.** A feature with a near-zero bar
        doesn't separate mice at that phase along that PC. The feature is
        either uniformly performed across mice (all reaching alike) or its
        variance lives on a higher PC the loop didn't display. Either way,
        it can't distinguish individuals on the dominant axis at that phase.

        **Cross-phase comparison.** Compare the four scree plots side by
        side. If PC1's variance share is much smaller post-injury than at
        baseline, injury fragmented the cohort into more independent
        reaching styles. Compare the four loading bars: a feature that
        loads big on every phase is doing similar work throughout the
        experiment; a feature that swings between phases changes its role
        depending on injury state.
    """),
    ("md", """
        ## 3. Align PC signs across phases

        Per-phase PCA produces PCs with arbitrary sign. Aligning each
        non-baseline phase's PCs to Baseline (flipping if Pearson r < 0) so
        "positive PC1 loading" means the same thing across phases.
    """),
    ("code", """
        loadings_baseline = pcas['Baseline'][3]                                                # index 3 = the loadings DataFrame in the helper's tuple (pca, scores, eigen, loadings, X)
        loadings_by_phase = {'Baseline': loadings_baseline}                                    # baseline serves as the sign reference; other phases get aligned to it
        for phase in PHASES[1:]:                                                                # PHASES[1:] = every phase except Baseline
            loadings_by_phase[phase] = align_signs_to_reference(pcas[phase][3], loadings_baseline) # flip a non-baseline phase's PC sign if it correlates negatively with baseline's same-numbered PC

        loadings_concat = pd.concat(loadings_by_phase, axis=0)                                  # stack all phases vertically; result has a 2-level index (phase, PC) so we can pivot per-PC heatmaps later
    """),
    ("md", """
        ## 4. Heatmap: per-PC loadings across phases

        Each subplot is one PC. Rows are kinematic features, columns are phases.
        Red = positive loading, blue = negative.
    """),
    ("code", """
        fig, axes = plt.subplots(1, N_COMPONENTS, figsize=FIGSIZE_HEATMAP)         # one row of subplots, one per PC
        for i, pc in enumerate([f'PC{k+1}' for k in range(N_COMPONENTS)]):          # iterate PC1, PC2, ... PC{N_COMPONENTS}
            pc_loadings = loadings_concat.xs(pc, level=1).T                         # .xs cross-section: pull just this PC across all phases; .T transposes so features become rows and phases become columns
            sns.heatmap(pc_loadings, cmap='RdBu_r', center=0, ax=axes[i],           # red-blue colormap centered at 0 so positive vs negative loadings are visually distinct
                        cbar_kws={'label': 'Loading'})                              # colorbar legend label
            axes[i].set_title(f'{pc} loadings across phases')                       # subplot title
        plt.tight_layout()                                                          # auto-adjust subplot spacing so titles/axes don't overlap
        stamp_version(fig, label='02 loadings by phase')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '02_loadings_by_phase.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        **What this figure shows.** One subplot per PC, with a row per
        kinematic feature and a column per phase. Red = positive loading,
        blue = negative loading, intensity = magnitude. Signs are aligned to
        Baseline so a "red cell at Baseline" and a "red cell at Post_Rehab"
        on the same row mean the feature is contributing in the same
        direction at both phases.

        **What different patterns mean.**
        - A row that's consistently red (or blue) across all four columns =
          the feature plays the same role in the dominant variance axis at
          every phase. It's a stable kinematic axis.
        - A row that flips color between columns = the feature's role
          depends on phase. *Example:* `peak_velocity_mm_per_sec` red at
          Baseline but blue at Post_Injury_2-4 would mean fast-reaching
          mice are at one end of PC1 at baseline but at the other end
          post-injury -- the feature changed its diagnostic meaning across
          experimental phases.
        - A row that's pale (near-zero) at one or two phases but bold at
          others = the feature only contributes to PC structure at those
          specific phases. Useful when interpreting what each phase's PC1
          actually captures.

        **Scientific reading.** Stable rows are kinematic axes the paper
        can describe phase-invariantly. Flipping or swinging rows are
        features whose interpretation depends on when in the experiment
        you measure -- the paper either reports them per-phase or skips
        them as ambiguous.
    """),
    ("md", """
        ## 5. Identify important features (mean |loading| across phases)

        Pool each PC's absolute loadings across phases; the top ``TOP_N_FEATURES``
        per PC are the kinematic features most consistently structured by the
        dominant subject-level variance. Write their union to the cache for
        the PLS notebook.
    """),
    ("code", """
        important_features = {}                                                  # PC name -> Series of top features by mean |loading|
        for pc in [f'PC{k+1}' for k in range(N_COMPONENTS)]:                     # iterate over PC1, PC2, ... PC{N_COMPONENTS}
            pc_loadings = loadings_concat.xs(pc, level=1).T                       # features x phases for this PC (same pivot as the heatmap above)
            mean_abs = pc_loadings.abs().mean(axis=1)                             # collapse phases: each feature gets a single mean-absolute-loading score across phases
            top = mean_abs.nlargest(TOP_N_FEATURES)                               # keep only the TOP_N_FEATURES strongest contributors for this PC
            important_features[pc] = top
            print(f'\\nTop {TOP_N_FEATURES} features on {pc} by mean |loading|:')
            print(top)

        all_important = set()                                                    # union of important features across PCs
        for pc, series in important_features.items():
            all_important.update(series.index)
        print(f'\\n{len(all_important)} unique important features total')

        pd.Series(sorted(all_important), name='feature').to_frame().to_parquet(  # write the union to parquet for notebook 04 to consume; sorted for deterministic file content
            CACHE_DIR / 'important_features.parquet', index=False
        )
    """),
    ("md", """
        **What you just saw.** Per-PC top features, then their union. The
        union is what gets carried forward as the kinematic Y-block for PLS
        in 04 -- only features whose role in the variance structure is
        substantial on SOME PC participate in the coupling-to-connectivity
        question. Features that never make any PC's top list contribute
        mostly noise from this analysis's perspective.

        **Reading the lists.** A feature appearing in PC1's top set across
        multiple phases is one whose role in cohort variance is robust. A
        feature appearing only on a single PC is a secondary contributor.
        A feature appearing nowhere is dropped.
    """),
    ("md", """
        ## 6. Heatmap filtered to important features
    """),
    ("code", """
        fig, axes = plt.subplots(1, N_COMPONENTS, figsize=FIGSIZE_HEATMAP)         # one subplot per PC, same layout as the all-features heatmap
        for i, pc in enumerate([f'PC{k+1}' for k in range(N_COMPONENTS)]):
            pc_loadings = loadings_concat.xs(pc, level=1).T                         # features x phases for this PC
            pc_top = pc_loadings.loc[pc_loadings.index.isin(all_important)]         # filter rows to only the important-feature set computed in the previous cell
            sns.heatmap(pc_top, cmap='RdBu_r', center=0, ax=axes[i],
                        cbar_kws={'label': 'Loading'})
            axes[i].set_title(f'{pc} loadings (important features)')
        plt.tight_layout()
        stamp_version(fig, label='02 important features')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '02_loadings_important_features.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        **What this figure shows.** Same cross-phase heatmap as figure 4,
        but restricted to only the important-feature union we just
        identified. Cleaner view of what's actually doing variance work
        once we drop the noise-floor features.

        **What to read into it.** Same patterns as figure 4 -- stable rows
        are phase-invariant kinematic axes; flipping rows are
        phase-dependent. The point of this filtered version is that every
        row here is "important enough to track"; the rows that didn't
        survive the union are gone, so any pattern you see now is on a
        feature worth interpreting.
    """),
]


# ============================================================================
# 03_kinematic_clustering.ipynb
# ============================================================================

CLUSTERING_NB = [
    ("md", """
        # 03 - Kinematic feature clustering

        **Purpose**: understand how kinematic features relate to each other.
        Cluster them by pairwise correlation, then view the same clusters
        projected into PCA space.

        **Input**: raw ``data.AKDdf`` restricted to contacted reaches, using
        every numeric non-metadata column (including unit duplicates) so the
        dendrogram reveals which measurements are functionally redundant.

        Intuition target: confirm that ``prefer_calibrated_units`` is dropping
        the right duplicates (same measurement in different units should
        cluster together), and see which non-duplicate features carry
        overlapping information.
    """),
    ("code", """
        # parameters
        N_CLUSTERS = 8        # Tune to where the dendrogram visually splits
        FIGSIZE_DENDRO = (16, 6)
        FIGSIZE_2D = (12, 10)
    """),
    ("code", """
        import numpy as np                                                       # numerical arrays
        import pandas as pd                                                      # dataframes
        import matplotlib.pyplot as plt                                          # 2D plotting
        import plotly.express as px                                              # interactive plots; used here for the 3D scatter at the end
        from scipy.cluster.hierarchy import linkage, dendrogram, fcluster        # hierarchical clustering: linkage builds the merge tree, dendrogram draws it, fcluster cuts it into a target cluster count
        from scipy.spatial.distance import pdist                                 # condensed pairwise-distance vector that linkage() expects
        from sklearn.preprocessing import StandardScaler                         # z-scorer
        from sklearn.decomposition import PCA                                    # principal components

        from endpoint_ck_analysis.config import EXAMPLE_OUTPUT_DIR, METADATA_COLS # PNG output dir + the set of column names that are NOT kinematic features (used to subtract metadata from numeric columns)
        from endpoint_ck_analysis.data_loader import load_all                     # one-shot data loader
        from endpoint_ck_analysis.helpers.plotting import stamp_version           # figure version footer

        EXAMPLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)                    # ensure output folder exists
        data = load_all()                                                        # uses cache from 00_setup
    """),
    ("md", """
        ## 1. Build the feature matrix

        Contacted reaches only. Every numeric non-metadata column (including
        unit duplicates) is retained so the dendrogram can reveal redundancy.
    """),
    ("code", """
        contacted = data.AKDdf[data.AKDdf['contact_group'] == 'contacted']                                # boolean filter: keep only reaches where the mouse touched the pellet (uncontacted reaches don't have meaningful kinematics)
        kine_cols = [c for c in contacted.select_dtypes(include='number').columns if c not in METADATA_COLS] # list comprehension: pick numeric columns that are NOT in METADATA_COLS (METADATA_COLS lists ID/index columns we don't want to treat as features)
        feature_matrix = contacted[kine_cols].fillna(0)  # rows = one reach each, columns = one kinematic feature each; fillna(0) replaces missing values so PCA/clustering doesn't error

        # Convert correlation to distance (highly correlated -> short distance).
        # |r| treats negative correlations as similarity (same info, flipped sign).
        corr = feature_matrix.corr()                                                                       # pandas .corr() returns a square (features x features) Pearson correlation matrix
        dist = 1 - corr.abs()                                                                              # absolute value because a feature correlated -1 with another carries the same information; flip the sign and they're identical

        link = linkage(pdist(dist), method='ward')  # pdist condenses the square dist matrix into the upper-triangle vector linkage() needs; Ward = a hierarchical clustering criterion that minimizes within-cluster variance at each merge

        cluster_ids = fcluster(link, t=N_CLUSTERS, criterion='maxclust')                                   # cut the merge tree into exactly N_CLUSTERS flat clusters; criterion='maxclust' tells fcluster that t is a target cluster count (not a distance threshold)
        feature_clusters = pd.Series(cluster_ids, index=corr.columns, name='cluster')                      # wrap the integer label array as a Series indexed by feature name so we can join it with PC coordinates downstream
    """),
    ("md", """
        ## 2. Dendrogram
    """),
    ("code", """
        fig, ax = plt.subplots(figsize=FIGSIZE_DENDRO)                                                  # one figure, one axis; FIGSIZE_DENDRO is (16, 6) by default in the parameters cell
        dendrogram(link, labels=corr.columns.tolist(), leaf_rotation=90, ax=ax)                          # draw the merge tree; leaf_rotation=90 prints feature names vertically so they don't overlap
        ax.set_ylabel('Clustering distance')                                                             # height in the dendrogram = how dissimilar two clusters were at the moment they merged
        ax.set_title(f'Kinematic feature dendrogram (contacted reaches; cut at {N_CLUSTERS} clusters)')
        plt.tight_layout()                                                                               # prevent label overlap
        stamp_version(fig, label='03 dendrogram')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '03_dendrogram.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        **What this figure shows.** Hierarchical clustering of kinematic
        features by how much they correlate with each other across reaches.
        Height in the tree = clustering distance (1 - |Pearson r|, so
        height 0 means perfect correlation, height 1 means uncorrelated).
        Features are joined into clusters from bottom up; horizontal cuts
        across the tree at higher distances yield fewer, broader clusters.

        **What different patterns mean.**
        - Pairs of features at distance ~0 = essentially the same
          measurement. *Example:* `max_extent_mm` and `max_extent_pixels`
          should sit at distance ~0; this is the unit-deduplication pass
          validating itself.
        - Tight cluster of 3-4 features at low height = those features
          carry overlapping information; the paper can pick one
          representative per cluster without losing biology.
        - Features sitting alone at the right edge until very late merges
          = they carry independent information not captured by anything
          else. *Example:* if `trajectory_smoothness` only joins the rest
          of the tree at distance ~0.8, no other feature in the dataset
          is restating it -- it's a unique kinematic axis the paper
          should report.
        - Big chunks of the dendrogram at similar heights = the kinematic
          feature space has clear functional groupings (e.g., velocity
          features clustering together, separate from path-shape features).

        **Scientific reading.** Use this to decide which features to keep
        in the paper. Within a tight cluster, picking one representative
        is defensible. Across distant clusters, all of them deserve
        reporting because they each capture something independent.
    """),
    ("md", """
        ## 3. PCA on the same feature matrix

        Fit a feature-space PCA (features as the items, reaches as the
        observations) so we can plot features in 2D/3D.
    """),
    ("code", """
        X_scaled = StandardScaler().fit_transform(feature_matrix)                # z-score every column so PC distances are unit-agnostic
        pca_feat = PCA(n_components=3)                                           # request 3 components -- enough to position features in 3D
        pca_feat.fit(X_scaled)                                                   # fit only (no .transform here -- we want the components matrix, not subject scores)

        loadings_xyz = pd.DataFrame(                                             # build a feature-coordinate table from the components matrix
            pca_feat.components_.T,                                              # .components_ is shape (n_components, n_features); .T flips so rows=features, cols=PCs
            index=feature_matrix.columns,                                        # keep the feature names as row labels
            columns=['PC1', 'PC2', 'PC3'],
        )
        loadings_xyz['cluster'] = feature_clusters                               # attach each feature's cluster ID as a column for color coding below
    """),
    ("md", """
        ## 4. 2D view with one label per cluster

        Labels land on the feature closest to each cluster's 2D centroid to
        keep the plot legible.
    """),
    ("code", """
        representatives = []                                                                     # list of feature names, one per cluster, that we'll use as visible labels on the scatter
        for cid, group in loadings_xyz.groupby('cluster'):                                       # iterate cluster by cluster; group is a DataFrame of features in this cluster
            centroid = group[['PC1', 'PC2']].mean()                                              # 2D centroid of the cluster (mean PC1 and mean PC2)
            dists = ((group[['PC1', 'PC2']] - centroid) ** 2).sum(axis=1)                        # squared Euclidean distance from each feature to the centroid (no sqrt needed since we just want the minimum)
            representatives.append(dists.idxmin())                                               # idxmin returns the feature name with the smallest distance -> closest to the centroid

        fig, ax = plt.subplots(figsize=FIGSIZE_2D)                                               # one figure, one axis
        scatter = ax.scatter(                                                                    # 2D scatter
            loadings_xyz['PC1'], loadings_xyz['PC2'],                                            # x, y coords
            c=loadings_xyz['cluster'], cmap='tab10', alpha=0.8, s=60,                            # color by cluster ID using tab10 (10 distinct colors); s = marker size in points
        )
        for feature in representatives:                                                          # one annotation per cluster representative
            row = loadings_xyz.loc[feature]
            ax.annotate(feature, (row['PC1'], row['PC2']), fontsize=9, fontweight='bold')        # label only the representatives so the plot stays readable
        ax.axhline(0, color='gray', linewidth=0.5, linestyle='--')                               # reference line at PC2=0
        ax.axvline(0, color='gray', linewidth=0.5, linestyle='--')                               # reference line at PC1=0
        ax.set_xlabel(f"PC1 ({pca_feat.explained_variance_ratio_[0]:.1%} variance)")             # axis label includes variance fraction; .1% formats as percentage with 1 decimal
        ax.set_ylabel(f"PC2 ({pca_feat.explained_variance_ratio_[1]:.1%} variance)")
        ax.set_title(f'Kinematic features in PC1-PC2 space ({N_CLUSTERS} clusters)')
        plt.colorbar(scatter, ax=ax, label='Cluster ID', ticks=range(1, N_CLUSTERS + 1))         # colorbar for the cluster legend; ticks=1..K (skip 0 since cluster IDs start at 1)
        plt.tight_layout()
        stamp_version(fig, label='03 2D PCA')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '03_feature_clusters_2d.png', dpi=150, bbox_inches='tight')
        plt.show()

        print(f'\\nCluster membership ({N_CLUSTERS} clusters):')                                 # print which features ended up in which cluster -- crucial for interpreting the plot
        for cid, group in loadings_xyz.groupby('cluster'):
            print(f'  Cluster {cid} ({len(group)} features): {sorted(group.index.tolist())}')
    """),
    ("md", """
        **What this figure + printout show.** The 2D scatter is each feature
        positioned in PC1-PC2 space (using a feature-space PCA, which is
        different from the per-phase PCA in notebook 02). Color = cluster
        ID from the dendrogram cut. The bold-labeled feature in each
        cluster is the one closest to its cluster's centroid -- a
        reasonable representative. The printout below the figure lists
        every feature's cluster assignment.

        **What different patterns mean.**
        - Tight, well-separated clusters in 2D = the dendrogram structure
          survives the projection to 2D; the clustering is geometrically
          coherent in feature space.
        - Spread or overlapping clusters in 2D = the clustering forced
          structure that the 2D PC view doesn't see clearly. Could mean
          PC1+PC2 don't capture the relevant feature relationships --
          consider the 3D view.
        - A cluster with many features piled on each other in 2D = those
          features are tightly equivalent; the bold representative is a
          good stand-in for the whole bundle.
        - A cluster spread out along PC1 or PC2 = the cluster's features
          cover a range; the centroid is a less faithful representative.

        **Scientific reading.** The printout is the practical takeaway --
        it tells the paper "if you want to summarize the kinematics with
        N axes, here are the natural N groups, and here are the features
        in each one." The 2D view is a sanity check on cluster geometry.
    """),
    ("md", """
        ## 5. 3D view (plotly -- interactive)

        Hover a dot to see its feature name; drag to rotate.
        Not saved as a PNG because the interactivity is the point.
    """),
    ("code", """
        fig3d = px.scatter_3d(                                                                # plotly's interactive 3D scatter (matplotlib's 3D doesn't get hover-labels)
            loadings_xyz.reset_index().rename(columns={'index': 'feature'}),                  # reset_index() pulls the feature names off the index into a column; rename so plotly knows what to call it
            x='PC1', y='PC2', z='PC3',                                                        # axis assignments
            hover_name='feature',                                                             # what shows in the tooltip when you hover a dot
            color=loadings_xyz['cluster'].astype(str).values,                                 # cast cluster IDs to strings so plotly treats them as discrete categories (not a continuous gradient)
            color_discrete_sequence=px.colors.qualitative.T10,                                # plotly's "T10" qualitative palette = the same 10 colors as matplotlib's tab10, so 2D and 3D views are visually consistent
            title='Kinematic features in PC1-PC2-PC3 space (colored by cluster)',
            labels={                                                                          # axis-label overrides: include variance fraction in each label
                'PC1': f"PC1 ({pca_feat.explained_variance_ratio_[0]:.1%})",
                'PC2': f"PC2 ({pca_feat.explained_variance_ratio_[1]:.1%})",
                'PC3': f"PC3 ({pca_feat.explained_variance_ratio_[2]:.1%})",
                'color': 'Cluster',                                                           # legend title for the color dimension
            },
        )
        fig3d.update_traces(marker=dict(size=5, opacity=0.85))                                # tweak marker visuals after plot construction; size in pixels, opacity 0..1
        fig3d.show()                                                                          # render inline; rotate the plot by drag, hover a dot for its label
    """),
    ("md", """
        **What this figure shows.** Same data as the 2D scatter, plus PC3 as
        a third axis. Drag to rotate; hover any dot to see the feature
        name. Color matches the 2D plot's cluster colors.

        **When to use this view.** When clusters look ambiguous in 2D
        because PC1+PC2 alone don't separate them, the third dimension
        often does. *Example:* if cluster A and cluster B overlap in the
        2D scatter but pull apart along PC3, the cluster structure is
        real -- it just doesn't project cleanly onto the first two
        principal axes.

        **Reading the rotation.** Watch which dots stay close together as
        you rotate; those are genuinely similar features regardless of
        viewing angle. Dots that swap apparent neighbors as the camera
        moves are positioned in 3D such that 2D projections can be
        misleading.
    """),
]


# ============================================================================
# 04_pls_variants.ipynb  (parameterized: injury / deficit / recovery)
# ============================================================================

PLS_VARIANTS_NB = [
    ("md", """
        # 04 - PLS variants

        **Purpose**: PLSCanonical fits between connectivity (X-block) and
        kinematics (Y-block) for each of three research questions:

        - ``injury_snapshot`` -- kinematic profile at Post_Injury_2-4.
        - ``deficit_delta``  -- change from Baseline to Post_Injury_2-4.
        - ``recovery_delta`` -- change from Post_Injury_2-4 to Post_Rehab_Test.

        Set ``VARIANT`` in the parameters cell to pick which one to run, or
        leave it as ``'all'`` to run them sequentially.

        **Input**: ``data.FCDGdf_wide`` and ``data.AKDdf_agg_contact_flat()``
        restricted to the important regions / features identified in 01 and 02.
    """),
    ("code", """
        # parameters
        VARIANT = 'all'  # 'injury_snapshot', 'deficit_delta', 'recovery_delta', or 'all'
        N_COMPONENTS = 2
        TOP_N = 15       # Top connectivity regions to label per LV
    """),
    ("code", """
        import pandas as pd                                                                          # dataframes
        import matplotlib.pyplot as plt                                                              # plotting

        from endpoint_ck_analysis.config import CACHE_DIR, EXAMPLE_OUTPUT_DIR                        # cache dir for the parquet files we read; example_output dir for saved figures
        from endpoint_ck_analysis.data_loader import load_all                                        # one-shot loader
        from endpoint_ck_analysis.helpers.dimreduce import build_y_phase, build_y_shift, run_pls     # Y-block builders (snapshot vs phase-delta) + PLS fitter
        from endpoint_ck_analysis.helpers.plotting import plot_pls, stamp_version                    # PLS three-panel figure helper + figure version footer

        EXAMPLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)                                        # ensure output folder exists
        data = load_all()                                                                            # load (uses cache from 00_setup)
        agg_flat = data.AKDdf_agg_contact_flat()                                                     # flatten the multi-index aggregated kinematics so we can filter on phase + contact_group as columns

        # Pull the important regions / features written by notebooks 01 and 02.
        important_regions = pd.read_parquet(CACHE_DIR / 'important_regions.parquet')['region_hemi'].tolist() # parquet was written by notebook 01; .tolist() converts the column into a Python list of strings
        important_features = pd.read_parquet(CACHE_DIR / 'important_features.parquet')['feature'].tolist()    # parquet from notebook 02; same shape

        X_block = data.FCDGdf_wide[important_regions].fillna(0)                                      # X-block for PLS: subjects x important connectivity regions; fillna(0) since PLS doesn't accept NaNs
    """),
    ("md", """
        ## 1. Build the three Y-blocks

        These share the same X-block (connectivity) so results compare directly.
    """),
    ("code", """
        Y_injury = build_y_phase(agg_flat, important_features, 'Post_Injury_2-4')                    # snapshot Y: subject x feature kinematics at the post-injury phase
        Y_deficit = build_y_shift(agg_flat, important_features, 'Baseline', 'Post_Injury_2-4')        # delta Y: how much each kinematic feature shifted from baseline to post-injury (per subject)
        Y_recovery = build_y_shift(agg_flat, important_features, 'Post_Injury_2-4', 'Post_Rehab_Test') # delta Y: how much each feature recovered from post-injury to post-rehab

        Y_BLOCKS = {                                                                                  # dispatch table: maps the variant string to (Y_block, label) pair so the loop below can pick which to run
            'injury_snapshot': (Y_injury, 'Injury snapshot (Post_Injury_2-4 kinematics)'),
            'deficit_delta':   (Y_deficit, 'Injury deficit (Post_Injury_2-4 - Baseline)'),
            'recovery_delta':  (Y_recovery, 'ABT recovery (Post_Rehab_Test - Post_Injury_2-4)'),
        }
    """),
    ("md", """
        ## 2. Run the selected variant(s)

        If ``VARIANT == 'all'`` every variant runs in order; otherwise only the
        selected one. Each call produces a three-panel figure per LV
        (X-loadings, Y-loadings, score-vs-score scatter).
    """),
    ("code", """
        variants = list(Y_BLOCKS.keys()) if VARIANT == 'all' else [VARIANT]                       # if VARIANT='all', loop over all three; otherwise just the chosen one
        if VARIANT != 'all' and VARIANT not in Y_BLOCKS:                                          # guard against typos in the parameters cell
            raise ValueError(f"Unknown VARIANT {VARIANT!r}. Choose one of {list(Y_BLOCKS) + ['all']}.")

        results = {}                                                                              # variant name -> dict returned by run_pls (contains pls model, X_scores, Y_scores, X_loadings, Y_loadings, subjects, label)
        for variant in variants:
            Y, label = Y_BLOCKS[variant]                                                          # tuple unpack: Y is the Y-block dataframe, label is the human-readable string
            print(f'\\n=== {variant} ===')                                                        # section divider in the printed output
            results[variant] = run_pls(X_block, Y, n_components=N_COMPONENTS, label=label)        # fit PLSCanonical between connectivity X-block and this variant's Y-block
            plot_pls(                                                                              # render the three-panel figure (X-loadings, Y-loadings, cross-score scatter) per LV
                results[variant], top_n=TOP_N,                                                     # top_n caps how many connectivity loadings to label
                save_dir=EXAMPLE_OUTPUT_DIR, slug=f"04_pls_{variant}",                             # also persist each LV figure to example_output/04_pls_{variant}_{LV}.png so the gallery (notebook 99) can show it later without re-running 04
            )
    """),
    ("md", """
        **What you just saw.** Three figures per variant (one per latent
        variable). Each figure has three panels:
        - **Left (X-loadings):** which residual-connectivity regions drive
          this LV. Long bar = the region's residual connectivity is part of
          the coupling axis.
        - **Middle (Y-loadings):** which kinematic features drive this LV.
          Long bar = the feature's variation is part of the coupling axis.
        - **Right (cross-score scatter):** each subject as a point at
          (X-side score, Y-side score). The dashed line is a least-squares
          fit; r and p in the title quantify how well the two sides line up.

        **Cross-score scatter -- the headline.** A clean diagonal scatter
        with high r (>0.8) means the X-block and Y-block share a real
        coupled axis: subjects who score high on the connectivity-side LV
        also score high on the kinematic-side LV. *Example:* a clean
        positive scatter on LV1 of `recovery_delta` would mean "mice with
        more residual connectivity on the LV1 axis recovered more reaching
        capacity post-ABT" -- the headline scientific claim of this
        analysis. A spread cloud with low r means no coherent coupling
        was found at this LV.

        **Top vs low loadings -- what they mean.** A region with a long
        positive bar on the X side AND a feature with a long positive bar
        on the Y side covary positively. Same signs across blocks =
        positive coupling; opposite signs = inverse coupling. A near-zero
        bar means the region/feature isn't contributing to this LV.

        **Variant-specific interpretation.**
        - *Injury snapshot:* X-loadings = which residual-connectivity
          regions go with which post-injury reaching style. Heads up the
          "given what residual connectivity each mouse had, here's the
          reaching style it produces" story.
        - *Deficit delta:* X-loadings = which residual-connectivity
          regions track the magnitude/direction of the immediate kinematic
          shift baseline -> post-injury. The regions whose residual
          connectivity best predicts the deficit.
        - *Recovery delta:* X-loadings = which residual-connectivity
          regions track post-ABT change in kinematics. The regions whose
          residual connectivity best predicts ABT response. This is the
          headline "does residual connectivity explain differential ABT
          response" question.

        **At small cohort sizes, every PLS will look highly correlated by
        construction.** With few subjects and many features per side, PLS
        has enough freedom to always find a fit. Treat the cross-score r
        as descriptive (here's what the data shows), NOT inferential
        (there's a real underlying relationship). At larger N this
        becomes a real test.
    """),
    ("md", """
        ## 3. Export latent-variable scores for the gallery

        Each variant's subject scores are written to the cache so the figure
        gallery can assemble a summary figure without re-fitting.
    """),
    ("code", """
        for variant, r in results.items():                                                         # iterate over the variants that ran (depending on VARIANT setting, this is 1 or 3)
            out = pd.DataFrame(r['X_scores'], index=r['subjects'],                                 # subjects' positions on each LV in connectivity-side latent space
                               columns=[f'LV{i+1}' for i in range(r['X_scores'].shape[1])])       # LV1, LV2, ... column names; .shape[1] = number of latent variables actually fit
            out.to_parquet(CACHE_DIR / f'pls_{variant}_X_scores.parquet')                          # one parquet per variant; figure gallery / future analyses can read these without re-fitting
            print(f'Wrote pls_{variant}_X_scores.parquet, N={len(out)}')                           # status; N here is matched-subject count for this variant
    """),
]


# ============================================================================
# 05_lmm_phase_effects.ipynb
# ============================================================================

LMM_NB = [
    ("md", """
        # 05 - Linear mixed models for phase effects

        **Purpose**: per-feature test of whether kinematic measurements differ
        across phases, accounting for:
        - within-subject correlation (subject random intercept)
        - within-session correlation (nested session random intercept)
        - reach-level noise (residual)

        **Input**: ``data.AKDdf`` (raw reaches) restricted to contacted reaches
        and the analyzable phase set.

        **Output**: three FDR-adjusted results tables, one per analysis:
        - Omnibus 4-phase test
        - Deficit (Baseline -> Post_Injury_2-4)
        - Recovery (Post_Injury_2-4 -> Post_Rehab_Test)

        **Small-sample caveat**: statsmodels uses a chi-square Wald
        approximation, not Satterthwaite/Kenward-Roger. At small N subjects this
        is mildly anti-conservative. Results here are suggestive of direction
        and structure; revisit when N grows.
    """),
    ("code", """
        # parameters
        FEATURE_LIST = None   # None -> use helpers.kinematics.get_kinematic_cols(AKDdf) (deduped list)
        TOP_N_FIGURE = 20     # How many features to show on the -log10(p) bar chart
        FIGSIZE_BARS = (14, 16)
    """),
    ("code", """
        import numpy as np                                                                # numerical arrays
        import pandas as pd                                                               # dataframes
        import matplotlib.pyplot as plt                                                   # plotting

        from endpoint_ck_analysis.config import ANALYZABLE_PHASES, EXAMPLE_OUTPUT_DIR, CACHE_DIR, FDR_ALPHA # phase set + output paths + FDR alpha for multiple-testing correction
        from endpoint_ck_analysis.data_loader import load_all                                              # one-shot loader
        from endpoint_ck_analysis.helpers.kinematics import get_kinematic_cols                             # returns the deduplicated list of kinematic feature column names
        from endpoint_ck_analysis.helpers.models import run_phase_lmm_for_features                          # fits an LMM per feature, returns FDR-corrected results
        from endpoint_ck_analysis.helpers.plotting import stamp_version                                     # figure version footer

        EXAMPLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)                             # ensure output folder exists
        data = load_all()                                                                  # uses cache from 00_setup
    """),
    ("md", """
        ## 1. Prepare the reach-level dataframe

        One row per contacted reach. Phase is an ordered Categorical with
        Baseline as the reference level, so every fitted coefficient reads as
        "phase X - Baseline".
    """),
    ("code", """
        contacted = data.AKDdf[data.AKDdf['contact_group'] == 'contacted'].copy()        # only reaches that touched the pellet (uncontacted reaches lack meaningful kinematics); .copy() avoids a SettingWithCopyWarning when we mutate columns below
        contacted['phase_group'] = pd.Categorical(                                       # convert phase_group to an ordered Categorical...
            contacted['phase_group'],
            categories=list(ANALYZABLE_PHASES),                                          # ...explicit category order so Baseline is the reference level...
            ordered=True,                                                                # ...ordered=True so contrasts read as "later phase vs Baseline"
        )

        features = FEATURE_LIST or get_kinematic_cols(contacted)                         # FEATURE_LIST is a parameter override; if None/empty, fall back to the deduplicated kinematic-feature list
        print(f'Features to test: {len(features)}')                                      # status; this is the family size FDR will correct over
    """),
    ("md", """
        ## 2. Omnibus across all four phases
    """),
    ("code", """
        omnibus = run_phase_lmm_for_features(contacted, features, fdr_alpha=FDR_ALPHA)               # one LMM per feature, all four phases included; helper internally FDR-corrects across the feature family
        print(omnibus.head(15)[['feature', 'phase_p', 'phase_p_adj', 'n_reaches', 'n_subjects', 'converged']]) # show the 15 most-significant features (sorted by p_adj inside the helper); column subset keeps the print readable
        omnibus.to_parquet(CACHE_DIR / 'lmm_omnibus.parquet', index=False)                            # cache the full results table for downstream notebooks / re-analysis
    """),
    ("md", """
        **What you just saw.** The top 15 features by FDR-adjusted p-value.
        Columns: `phase_p` is the raw chi-square Wald p; `phase_p_adj` is
        after Benjamini-Hochberg correction across the feature family;
        `n_reaches` and `n_subjects` are the data going into each fit;
        `converged` says whether the LMM optimizer succeeded.

        **What different patterns mean.**
        - Features with `phase_p_adj < 0.05` (the FDR cutoff) reliably
          differ across the four phases once subject and session
          random effects are accounted for. The cohort, on average,
          reaches differently at different phases on these features.
        - `phase_p_adj` close to 1 = no detectable phase effect; the
          feature looks the same on average before and after injury.
        - `converged = False` = the LMM optimizer failed; the
          corresponding p-value is unreliable and that row should be
          treated as missing.

        **Reading the rows.** *Example:* if `head_angle_at_apex_deg`
        leads the omnibus with adjusted p in the range of 1e-3 or
        smaller, the cohort's head orientation at the apex of the reach
        is changing across phases -- a real phase-dependent kinematic
        signature. Worth tracking through the deficit and recovery
        tests next.
    """),
    ("md", """
        ## 3. Deficit delta (Baseline vs Post_Injury_2-4)

        Same model structure but restricted to two phases, so the phase
        coefficient IS the delta.
    """),
    ("code", """
        deficit_df = contacted[contacted['phase_group'].isin(['Baseline', 'Post_Injury_2-4'])].copy() # restrict to the two-phase deficit comparison so the LMM coefficient IS the delta
        deficit_df['phase_group'] = pd.Categorical(deficit_df['phase_group'], categories=['Baseline', 'Post_Injury_2-4']) # rebuild the Categorical with just these two levels; ordering ensures Baseline is the reference
        deficit = run_phase_lmm_for_features(deficit_df, features, fdr_alpha=FDR_ALPHA)               # same per-feature LMM as the omnibus, but on the restricted dataframe
        print(deficit.head(15)[['feature', 'phase_p', 'phase_p_adj', 'n_reaches', 'n_subjects', 'converged']])
        deficit.to_parquet(CACHE_DIR / 'lmm_deficit.parquet', index=False)
    """),
    ("md", """
        **What you just saw.** Same per-feature LMM but restricted to two
        phases (Baseline and Post_Injury_2-4). The phase coefficient now
        IS the deficit -- the magnitude and direction of the average
        kinematic change between baseline and the established post-injury
        state.

        **What different patterns mean.**
        - Features with `phase_p_adj < 0.05` here = injury reliably
          shifted that aspect of reaching. These are the kinematic axes
          the C5 contusion produced a measurable change on.
        - Features that survive the deficit test but NOT the omnibus
          are uncommon (the omnibus pools more phases) but possible if
          the deficit reverts at later phases.
        - Features that survive the omnibus but NOT the deficit changed
          across phases generally, but the change isn't concentrated at
          the immediate post-injury timepoint -- it's at Post_Injury_1
          (early subacute) or Post_Rehab_Test instead.

        **Scientific reading.** This list is the candidate set of
        injury-affected aspects of reaching. The recovery test below
        then asks which of these came back after ABT.
    """),
    ("md", """
        ## 4. Recovery delta (Post_Injury_2-4 vs Post_Rehab_Test)
    """),
    ("code", """
        recovery_df = contacted[contacted['phase_group'].isin(['Post_Injury_2-4', 'Post_Rehab_Test'])].copy() # two-phase recovery comparison
        recovery_df['phase_group'] = pd.Categorical(recovery_df['phase_group'], categories=['Post_Injury_2-4', 'Post_Rehab_Test']) # Post_Injury_2-4 is the reference here; coefficient reads as "Post_Rehab - Post_Injury"
        recovery = run_phase_lmm_for_features(recovery_df, features, fdr_alpha=FDR_ALPHA)
        print(recovery.head(15)[['feature', 'phase_p', 'phase_p_adj', 'n_reaches', 'n_subjects', 'converged']])
        recovery.to_parquet(CACHE_DIR / 'lmm_recovery.parquet', index=False)
    """),
    ("md", """
        **What you just saw.** Same per-feature LMM restricted to
        Post_Injury_2-4 vs Post_Rehab_Test. The phase coefficient is the
        change attributable to ABT.

        **What different patterns mean.**
        - Features with `phase_p_adj < 0.05` here = ABT reliably shifted
          that aspect of reaching. These are the ABT-responsive aspects.
        - Features that survive both the deficit AND recovery tests are
          the strongest candidates for the paper's headline narrative:
          injury reliably perturbed them, ABT reliably restored some of
          them.
        - Features that survive deficit but NOT recovery dropped at
          injury and stayed dropped (no ABT response) -- itself a
          publishable finding pointing to deficit aspects ABT didn't
          restore.
        - Features that survive recovery but NOT deficit improved with
          ABT but didn't clearly drop at injury. Could be late-phase
          task-learning rather than injury-specific recovery; interpret
          with caution.

        **Scientific reading.** Cross-reference this list with the
        deficit list to bin features into deficit-only / recovery-only /
        both / neither. The "both" bin is the headline result.
    """),
    ("md", """
        ## 5. -log10(adjusted p) summary

        Three stacked bar charts -- one per analysis -- of the top features by
        significance. Red dashed line marks the FDR cutoff.
    """),
    ("code", """
        combined = pd.concat([                                                                       # stack the top features from all three analyses with an 'analysis' label column for groupby
            omnibus.head(TOP_N_FIGURE).assign(analysis='Omnibus (all phases)'),                       # .assign() adds a constant-valued column to the dataframe; .head(N) takes the top N rows already sorted by p_adj
            deficit.head(TOP_N_FIGURE).assign(analysis='Deficit (Baseline -> Post_Injury_2-4)'),
            recovery.head(TOP_N_FIGURE).assign(analysis='Recovery (Post_Injury_2-4 -> Post_Rehab_Test)'),
        ])
        combined['neg_log_p'] = -np.log10(combined['phase_p_adj'].clip(lower=1e-30))                  # -log10 inflates small p-values into tall bars; .clip(lower=1e-30) avoids -log10(0)=inf when p_adj rounds to zero

        sig_threshold = -np.log10(FDR_ALPHA)                                                          # vertical reference line for the conventional 0.05 cutoff
        fig, axes = plt.subplots(3, 1, figsize=FIGSIZE_BARS)                                          # 3 stacked panels (one per analysis)
        for ax, (label, group) in zip(axes, combined.groupby('analysis', sort=False)):                # iterate panels and groups in lockstep; sort=False preserves insertion order (omnibus -> deficit -> recovery)
            plot_df = group.dropna(subset=['neg_log_p']).sort_values('neg_log_p')                     # drop features that didn't fit (NaN p_adj) and sort ascending so the strongest bar ends up on top of the horizontal chart
            colors = ['steelblue' if v >= sig_threshold else 'lightgray' for v in plot_df['neg_log_p']] # list comprehension: significant bars get a bold color, non-significant ones gray
            ax.barh(plot_df['feature'], plot_df['neg_log_p'], color=colors)                           # horizontal bars with feature names on y-axis
            ax.axvline(sig_threshold, color='red', linestyle='--', linewidth=1, label=f'FDR q={FDR_ALPHA}') # red dashed line marking the FDR cutoff
            ax.set_xlabel('-log10(FDR-adjusted p)')
            ax.set_title(label)
            ax.legend(loc='lower right')
        plt.tight_layout()
        stamp_version(fig, label='05 LMM summary')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '05_lmm_summary.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        **What this figure shows.** Three stacked bar charts, one per
        analysis (omnibus / deficit / recovery). Each bar is one
        kinematic feature; bar length is `-log10(FDR-adjusted p)`. Bars
        right of the red dashed line cleared the FDR cutoff -- they're
        statistically significant after multiple-testing correction.
        Bars are colored: blue = significant, gray = not.

        **What different patterns mean.**
        - Many tall blue bars on the omnibus = reaching is broadly
          affected by phase across the experiment.
        - Tall blue bars on the deficit panel but gray on the recovery
          panel = injury affected this feature, ABT didn't restore it.
        - Tall blue bars on both deficit and recovery panels = headline
          features for the paper -- injury changed them, ABT restored
          some of the change.
        - All bars short / gray everywhere = no detectable phase effects
          after FDR. Either the cohort is too small for the effect size
          or the cohort genuinely doesn't show injury/recovery on these
          features.

        **Scientific reading.** This figure is the per-feature inferential
        summary of the whole pipeline. Combined with the deficit-vs-
        recovery cross-reference above, it tells the paper exactly which
        features to feature in the kinematic narrative.

        **Small-N caveat.** The chi-square Wald approximation
        statsmodels uses is mildly anti-conservative at small inferential
        N. FDR correction partially compensates; at expanded cohort
        sizes, switch to Kenward-Roger via `pymer4` for paper-ready
        inference.
    """),
]


# ============================================================================
# 06_pellet_validation.ipynb
# ============================================================================

VALIDATION_NB = [
    ("md", """
        # 06 - Pellet scoring validation (manual vs algorithmic)

        **Purpose**: measure how well the algorithmic pellet-outcome classifier
        agrees with manual scoring, as a prerequisite for trusting the automated
        outcomes in the main analysis.

        **Restricted to pillar trays** because the algorithm is designed for
        that tray type only.

        **Input**: ``data.manual_pelletdf`` and ``data.kinematicsdf``.
    """),
    ("code", """
        # parameters
        FIGSIZE_CM = (14, 5)
        FIGSIZE_PER_PHASE = (9, 5)
    """),
    ("code", """
        import pandas as pd                                              # dataframes
        import matplotlib.pyplot as plt                                  # plotting
        import seaborn as sns                                            # heatmap helper used for the confusion matrix
        from sklearn.metrics import cohen_kappa_score, confusion_matrix  # chance-corrected agreement statistic + raw count matrix

        from endpoint_ck_analysis.config import EXAMPLE_OUTPUT_DIR       # PNG output dir
        from endpoint_ck_analysis.data_loader import load_all            # one-shot loader
        from endpoint_ck_analysis.helpers.plotting import stamp_version  # figure version footer

        EXAMPLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)            # ensure output folder exists
        data = load_all()                                                # uses cache from 00_setup
    """),
    ("md", """
        ## 1. Build the per-pellet validation dataframe

        Inner-join manual and algorithmic scores on shared per-pellet keys.
    """),
    ("code", """
        manual_pellet_pillar = data.manual_pelletdf[data.manual_pelletdf['tray_type'] == 'P']           # restrict to pillar trays only -- the algorithm wasn't designed for E/F trays
        kinematics_pillar = data.kinematicsdf[data.kinematicsdf['tray_type'] == 'P']                    # same restriction on the kinematics side

        algo_per_segment = kinematics_pillar[                                                            # pull just the columns we need to identify segments + their algorithmic outcome...
            ['subject_id', 'session_date', 'tray_type', 'run_number', 'segment_num', 'segment_outcome']
        ].drop_duplicates().rename(columns={'run_number': 'tray_number', 'segment_num': 'pellet_number'}) # ...drop_duplicates collapses multiple reaches in the same segment to one row, then rename to the column names manual scoring uses

        validation = manual_pellet_pillar[                                                              # build the validation join key from manual scoring's columns...
            ['subject_id', 'session_date', 'tray_type', 'tray_number', 'pellet_number', 'score']
        ].merge(                                                                                         # ...and inner-join against the algorithmic per-segment table
            algo_per_segment,
            on=['subject_id', 'session_date', 'tray_type', 'tray_number', 'pellet_number'],             # composite key uniquely identifies one pellet event
            how='inner',                                                                                 # 'inner' drops pellets that don't match in both sources; that's intentional -- only paired observations count for agreement
        )
        print(f'Matched {len(validation)} pillar pellets between manual and algorithmic scoring')

        manual_cat_map = {0: 'missed', 1: 'displaced', 2: 'retrieved'}                                  # manual scoring uses integer codes; map to readable strings for the confusion matrix
        algo_cat_map = {                                                                                 # algorithmic scoring has more granular categories; collapse to the same three buckets manual uses
            'untouched': 'missed', 'uncertain': 'missed',
            'displaced_sa': 'displaced', 'displaced_outside': 'displaced',
            'retrieved': 'retrieved',
        }
        validation['manual_cat'] = validation['score'].map(manual_cat_map)                              # .map applies the dict element-wise; result is a new Series of category strings
        validation['algo_cat'] = validation['segment_outcome'].map(algo_cat_map)
        validation['manual_contacted'] = validation['manual_cat'] != 'missed'                           # boolean: did the manual score say the pellet was at least touched?
        validation['algo_contacted'] = validation['algo_cat'] != 'missed'                               # same boolean for the algorithm; binary metric is easier to interpret than three-way
    """),
    ("md", """
        ## 2. Summary statistics
    """),
    ("code", """
        three_way = (validation['manual_cat'] == validation['algo_cat']).mean()                           # element-wise equality on the three categories -> boolean Series; .mean() of bools = fraction True = exact agreement rate
        binary = (validation['manual_contacted'] == validation['algo_contacted']).mean()                  # same logic on the binary contacted/missed collapse
        kappa = cohen_kappa_score(validation['manual_cat'], validation['algo_cat'])                       # Cohen's kappa: agreement adjusted for the rate you'd expect by chance given each rater's marginal distribution
        print(f'Three-way exact agreement:     {three_way:.3%}')                                          # .3% formats as percentage with 3 decimal places
        print(f'Binary (contacted vs missed):  {binary:.3%}')
        print(f"Cohen's kappa (three-way):     {kappa:.3f}")
        print('Interpretation of kappa: <0.4 poor, 0.4-0.6 moderate, 0.6-0.8 substantial, >0.8 almost perfect') # standard kappa-interpretation thresholds (Landis & Koch 1977)
        print('\\nConfusion matrix (rows=manual, cols=algorithmic):')
        print(pd.crosstab(validation['manual_cat'], validation['algo_cat'], margins=True))                # pandas crosstab counts co-occurrences; margins=True adds row/column totals
    """),
    ("md", """
        **What you just saw.** Three numbers and a count table.
        - **Three-way exact agreement:** raw rate of "manual and algorithm
          gave the same one of {missed, displaced, retrieved}".
        - **Binary agreement:** rate of "manual and algorithm agree on
          whether the pellet was touched at all" (collapsing displaced
          and retrieved into one bucket).
        - **Cohen's kappa:** chance-corrected agreement on the three-way
          classification. The legend below the print spells out the
          standard ranges.

        **What different patterns mean.**
        - Kappa > 0.8 = the algorithmic outcome labels are trustworthy
          for publication; downstream analyses that use them are on
          solid ground.
        - Kappa 0.6-0.8 = substantial agreement; algorithmic labels
          usable for most analyses, spot-check edge cases that hinge on
          retrieval-vs-displacement distinctions.
        - Kappa 0.4-0.6 = moderate; flag any conclusion that depends on
          three-way categorization.
        - Kappa < 0.4 = poor; default to manual scoring for the paper.
        - Binary agreement much higher than three-way = algorithm is
          good at detecting contact but bad at distinguishing displaced
          vs retrieved. **What this means for the paper:** analyses
          using `contact_group` are fine; analyses using `outcome_group`
          (the three-way split) need extra caution.

        **Reading the confusion matrix below.** The crosstab shows where
        the disagreements live. Off-diagonal mass concentrated in the
        displaced<->retrieved cells = the boundary the algorithm is
        struggling with. Off-diagonal mass in missed<->contacted cells
        = the algorithm is failing at contact detection itself, which
        is more concerning.
    """),
    ("md", """
        ## 3. Confusion matrix heatmap (counts + row-normalized)
    """),
    ("code", """
        cats = ['missed', 'displaced', 'retrieved']                                                     # explicit category order; passing labels= to confusion_matrix locks the row/col axis to this order regardless of which categories appear in the data
        cm = confusion_matrix(validation['manual_cat'], validation['algo_cat'], labels=cats)             # 3x3 raw-count matrix; rows=manual labels, cols=algorithmic labels
        cm_norm = cm / cm.sum(axis=1, keepdims=True)                                                     # row-normalize: divide each row by its sum -> rows now sum to 1.0; reads as "given the manual class, what fraction does the algorithm assign to each class"; keepdims=True keeps it as a (3,1) for proper broadcasting

        fig, axes = plt.subplots(1, 2, figsize=FIGSIZE_CM)                                              # two side-by-side panels: counts and normalized
        sns.heatmap(cm, annot=True, fmt='d', cmap='Blues', xticklabels=cats, yticklabels=cats,          # annot=True writes the count in each cell; fmt='d' formats as integer
                    ax=axes[0], cbar_kws={'label': 'count'})
        axes[0].set_xlabel('Algorithmic classification')
        axes[0].set_ylabel('Manual classification')
        axes[0].set_title('Pillar confusion matrix (raw counts)')
        sns.heatmap(cm_norm, annot=True, fmt='.2%', cmap='Blues', xticklabels=cats, yticklabels=cats,   # fmt='.2%' formats as percentage with 2 decimals; vmin/vmax fix the colormap range so two heatmaps with different absolute scales remain visually comparable
                    ax=axes[1], vmin=0, vmax=1, cbar_kws={'label': 'fraction'})
        axes[1].set_xlabel('Algorithmic classification')
        axes[1].set_ylabel('Manual classification')
        axes[1].set_title('Pillar confusion matrix (row-normalized)')
        plt.tight_layout()
        stamp_version(fig, label='06 confusion')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '06_confusion_matrix.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        **What this figure shows.** Two heatmaps, both with manual class
        on the y-axis and algorithmic class on the x-axis. The left panel
        shows raw counts; the right panel shows the same matrix with
        each row normalized to 1.0 (so the row reads as "given the
        manual class, what fraction did the algorithm assign to each
        bucket").

        **What different patterns mean.**
        - Strong diagonal in the row-normalized panel (each diagonal
          cell close to 100%) = the algorithm is rarely wrong about
          any of the three categories.
        - Off-diagonal mass concentrated in displaced<->retrieved cells
          = the boundary between successful retrieval and "knocked the
          pellet but didn't bring it in" is what's hard. *Example:* if
          the manual=retrieved row shows 70% retrieved, 25% displaced,
          5% missed in the algo classification, the algorithm is
          underestimating retrievals by treating them as displacements.
        - Off-diagonal mass in missed<->contacted cells = contact
          detection itself is failing. More concerning because contact
          is the precondition for any kinematic analysis.
        - The raw-counts panel reveals class imbalance: if "missed" has
          tiny bin counts compared to the others, the kappa for that
          row is unstable even if the percentages look fine.

        **Scientific reading.** Use the row-normalized panel to identify
        which boundary the algorithm needs to be retrained on. Use the
        raw-counts panel to know which classes have enough data to
        trust the agreement statistic.
    """),
    ("md", """
        ## 4. Per-phase agreement

        Does the algorithm's accuracy drift across the experimental phases?
    """),
    ("code", """
        validation_with_phase = validation.merge(                                                       # left-join phase_group onto each validated pellet so we can group by phase
            manual_pellet_pillar[['subject_id', 'session_date', 'tray_number', 'pellet_number', 'phase_group']],
            on=['subject_id', 'session_date', 'tray_number', 'pellet_number'],                          # composite key matches the validation table
            how='left',                                                                                  # keep every validation row even if phase isn't found (rare; would show as NaN phase_group)
        )
        per_phase = validation_with_phase.groupby('phase_group').apply(                                 # one row per phase
            lambda g: pd.Series({                                                                        # lambda runs on each phase's subset 'g'; returns a small Series of statistics
                'n': len(g),                                                                             # how many pellets contributed to this phase's number
                'three_way_agreement': (g['manual_cat'] == g['algo_cat']).mean(),                       # exact-agreement rate within phase
                'binary_agreement': (g['manual_contacted'] == g['algo_contacted']).mean(),              # contacted-vs-missed agreement within phase
            })
        ).sort_values('n', ascending=False)                                                              # sort by sample size so phases with the most data appear first
        print(per_phase)

        fig, ax = plt.subplots(figsize=FIGSIZE_PER_PHASE)                                               # one panel
        x = range(len(per_phase))                                                                        # integer x positions (one per phase)
        ax.bar([i - 0.2 for i in x], per_phase['three_way_agreement'], width=0.4,                        # offset by -0.2 so this bar sits to the LEFT of the phase tick
               label='Three-way agreement', color='steelblue')
        ax.bar([i + 0.2 for i in x], per_phase['binary_agreement'], width=0.4,                           # offset by +0.2 so this bar sits to the RIGHT, paired with the matching three-way bar
               label='Binary agreement', color='orange')
        ax.set_xticks(list(x))
        ax.set_xticklabels(per_phase.index, rotation=45, ha='right')                                    # rotate phase names so they don't overlap; ha='right' anchors text at the right edge
        ax.set_ylabel('Agreement rate')
        ax.set_ylim(0, 1)                                                                                # agreement is a fraction; lock axis to [0,1] so the y-axis is comparable across runs
        ax.axhline(0.9, color='green', linestyle='--', linewidth=0.7, label='0.90 reference')           # green dashed line at 0.9 -- a typical "good agreement" benchmark
        ax.legend()
        ax.set_title('Manual vs algorithmic agreement by phase (pillar trays only)')
        for i, (phase, row) in enumerate(per_phase.iterrows()):                                          # annotate each phase position with its N at the bottom of the plot
            ax.text(i, 0.02, f"N={int(row['n'])}", ha='center', fontsize=8)
        plt.tight_layout()
        stamp_version(fig, label='06 per phase')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '06_agreement_by_phase.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        **What this figure + printout show.** The print is per-phase
        agreement (n pellets, three-way rate, binary rate). The figure is
        the same data as side-by-side bars: blue = three-way, orange =
        binary. The green dashed line at 0.9 is a "good agreement"
        reference.

        **What different patterns mean.**
        - Steady high agreement across all phases (both colors near or
          above the green line) = the algorithm is robust across
          experimental phases. Use it freely throughout the analysis.
        - Agreement dropping post-injury = the injured cohort's reach
          kinematics differ from what the algorithm was trained on, so
          its outcome decisions are less reliable on those phases.
          *Example:* if Baseline kappa is 0.9 but Post_Injury_1 binary
          agreement falls to 0.6, post-injury outcome labels are the
          weakest part of the dataset; any kinematic finding that
          conditions on outcome at that phase needs a caveat.
        - Three-way bar consistently lower than the binary bar at every
          phase = the algorithm reliably detects contact but consistently
          struggles with the displaced/retrieved boundary. Stays
          phase-independent; not a phase-specific issue.
        - Phases with very small n (printed in the table) = agreement
          estimate at that phase is itself unreliable; ignore the bar
          and focus on phases with substantive sample sizes.

        **Scientific reading.** This figure tells the paper which phases
        you can trust the algorithm on. Where it dips, plan to either
        manually re-score that phase's data or footnote the
        phase-specific uncertainty in the discussion.
    """),
]


# ============================================================================
# 07_connectivity_trajectory_linkage.ipynb
# ============================================================================

TRAJECTORY_NB = [
    ("md", """
        # 07 - Connectivity groups vs. kinematic trajectories

        **Purpose**: close the analytical loop. Notebooks 01-06 identified
        which connectivity and kinematic features carry most of the variance
        and which kinematic features change across phases. This notebook
        asks the payoff question: **do mice with similar connectivity
        profiles show similar kinematic trajectories across phases?**

        Pipeline implemented here:

        1. Cluster subjects on connectivity (ward / kmeans / gmm / consensus).
        2. Profile each cluster: which regions define it, relative to the
           overall population? Auto-generate human-readable labels.
        3. Validate the clustering with LOO + random-permutation nulls.
        4. Alluvial / Sankey: do subjects clustered on baseline kinematics
           stay together across phases, or mix?
        5. Continuous trajectories colored by connectivity PC1.
        6. Grouped trajectories colored by named clusters.
        7. Interaction LMM: ``feature ~ phase * cluster`` with nested
           subject/session random effects.
        8. Ascending-connectivity placeholder: where the second-pass
           analysis plugs in when ascending tracing data arrives.

        **Synthetic-cohort mode**. When the real cohort is small the
        clustering or interaction statistics can be uninformative on
        their own. Set ``USE_SYNTHETIC = True`` in the parameters cell to
        swap in a cloned-and-perturbed synthetic cohort so the pipeline
        can be validated at realistic N. Synthetic runs produce output
        that exercises every code path but is explicitly NOT real data;
        every figure generated in this mode should be labeled as such
        in any writeup.

        See [`../docs/assumptions.md`](../docs/assumptions.md) for the
        N-scaling story and why the per-phase kinematic clustering used
        for the alluvial plot doesn't double-dip against connectivity.
    """),
    ("code", """
        # parameters
        #
        # --- Data source ---
        USE_SYNTHETIC = False                # True = pipeline validation mode; False = real data
        SYNTHETIC_N = 30                     # how many synthetic subjects to mint
        SYNTHETIC_SEED = 42                  # RNG seed for reproducibility
        SYNTHETIC_CONN_NOISE = 0.3           # cross-subject-std fraction added to connectivity clones
        SYNTHETIC_KINE_NOISE = 0.10          # per-feature-std fraction added to reach-level clones
        #
        # --- Clustering ---
        CLUSTER_METHOD = 'ward'              # 'ward', 'kmeans', 'gmm', 'consensus'
        N_CLUSTERS = 4                       # Bump up when N grows
        N_CONN_PCS = 3                       # Connectivity PCs used for the coordinate axis
        #
        # --- Cluster profiling ---
        PROFILE_TOP_N = 2                    # regions mentioned per auto-generated cluster name
        PROFILE_THRESHOLD = 0.5              # |z-score| below this is treated as "not distinctive"
        MANUAL_CLUSTER_NAMES = {}            # Optional user override: {int_cluster_id: 'biological name'}
        #
        # --- Permutation validation ---
        N_PERMUTATIONS = 500                 # random-label shuffles for the null
        #
        # --- Trajectory display ---
        TRAJECTORY_FEATURE = 'max_extent_mm'
        AGG_STAT = 'mean'                    # '_mean', '_std', '_median', '_q25', '_q75'
        PHASES = ['Baseline', 'Post_Injury_1', 'Post_Injury_2-4', 'Post_Rehab_Test']
        #
        # --- LMM ---
        RUN_INTERACTION_LMM = True
        #
        # --- Figure sizes ---
        FIGSIZE_TRAJ = (12, 6)
        FIGSIZE_PROFILE = (14, 6)
        FIGSIZE_PERMUTATION = (10, 5)
        FIGSIZE_ALLUVIAL = (900, 500)
    """),
    ("code", """
        import numpy as np                                                                          # numerical arrays
        import pandas as pd                                                                          # dataframes
        import matplotlib.pyplot as plt                                                              # 2D plotting
        from matplotlib.cm import get_cmap                                                           # named-colormap accessor (e.g. 'tab10', 'viridis')
        import seaborn as sns                                                                        # heatmap helper used for the cluster profile
        from sklearn.decomposition import PCA                                                        # connectivity PCA for the trajectory color axis
        from sklearn.preprocessing import StandardScaler                                             # z-scorer
        from IPython.display import display                                                          # display(): renders a dataframe as an HTML table inline; nicer than print() for tabular data

        from endpoint_ck_analysis import SKILLED_REACHING, ordered_hemisphere_columns                # canonical region prior + helper that produces region_hemi columns in priority order
        from endpoint_ck_analysis.config import CACHE_DIR, EXAMPLE_OUTPUT_DIR, ANALYZABLE_PHASES     # cache dir, output PNG dir, the analyzable phase set
        from endpoint_ck_analysis.data_loader import load_all                                        # one-shot loader; supports the synthetic-cohort path via flags
        from endpoint_ck_analysis.helpers.clusters import (                                          # clustering + validation toolkit
            cluster_subjects, profile_clusters, auto_name_clusters,                                  # method-agnostic clustering, per-cluster z-score profile, auto-generated cluster labels
            permutation_validate, alluvial_source_records,                                            # cluster-validity permutation test, source records for plotly Sankey
        )
        from endpoint_ck_analysis.helpers.plotting import stamp_version                              # figure version footer

        EXAMPLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)                                        # ensure output folder exists
        data = load_all(                                                                              # load data; the parameters cell controls whether this loads real or synthetic
            use_synthetic=USE_SYNTHETIC,
            synthetic_n=SYNTHETIC_N,
            synthetic_seed=SYNTHETIC_SEED,
            synthetic_conn_noise=SYNTHETIC_CONN_NOISE,
            synthetic_kine_noise=SYNTHETIC_KINE_NOISE,
            use_cache=not USE_SYNTHETIC,     # synthetic always rebuilds (cache reflects the LAST real load; can't safely mix with synthetic data)
            write_cache=not USE_SYNTHETIC,   # don't overwrite the real-data cache with synthetic content
            verbose=False,
        )
        print(f"Running on {'SYNTHETIC' if USE_SYNTHETIC else 'REAL'} data.  "                       # status banner so the run mode is visible at a glance
              f"N={len(data.matched_subjects)} subjects.")
    """),
    ("md", """
        ## 1. Compute connectivity coordinates

        Re-fit PCA on the full connectivity matrix (same matrix as notebook
        01). Each subject ends up with PC1/PC2/PC3 scores that serve as a
        continuous coordinate for coloring trajectories.
    """),
    ("code", """
        X_conn = data.FCDGdf_wide.fillna(0)                                                                    # connectivity matrix (subjects x region_hemi); fillna(0) so PCA doesn't error
        canonical_cols = ordered_hemisphere_columns(SKILLED_REACHING, available=X_conn.columns.tolist())       # priority-ordered column list, filtered to columns that actually exist
        X_conn = X_conn[canonical_cols]                                                                         # reorder X_conn to canonical column order

        X_scaled = StandardScaler().fit_transform(X_conn)                                                       # z-score so PCA isn't dominated by high-magnitude regions
        conn_pca = PCA(n_components=min(N_CONN_PCS, len(X_conn) - 1))                                          # cap n_components at min(N_CONN_PCS, N-1); PCA can't extract more components than N-1
        conn_scores = conn_pca.fit_transform(X_scaled)                                                          # subject coordinates in PC space
        conn_scores_df = pd.DataFrame(                                                                          # wrap into a labeled DataFrame
            conn_scores,
            index=X_conn.index,                                                                                 # row labels = subject_id (kept from X_conn)
            columns=[f'PC{i+1}' for i in range(conn_scores.shape[1])],                                          # column labels = PC1, PC2, ... PC{actual fit count}
        )
        conn_scores_df.index.name = 'subject_id'                                                                # make the index name explicit so to_parquet preserves it
        print('Per-subject connectivity PC scores:')
        display(conn_scores_df)                                                                                  # display() renders the dataframe as an HTML table; plain print() would dump it as plain text and lose the formatting

        # Cache these so downstream notebooks can also colour by them
        conn_scores_df.to_parquet(CACHE_DIR / 'connectivity_pc_scores.parquet')                                # other notebooks (98+future) can read this without redoing PCA
    """),
    ("md", """
        **What you just saw.** Each row is one mouse; each column is its
        score on a connectivity PC. These coordinates are how this
        notebook positions mice in residual-connectivity space for
        coloring trajectories and grouping by similarity.

        **Reading the table.** Mice with similar values on PC1 retained
        similar amounts of residual connectivity along the dominant
        cohort axis. Mice with very different PC1 scores sit at opposite
        ends of that axis -- their residual profiles look most unlike
        each other. PC2 and PC3 capture secondary/tertiary directions.

        Together, these scores feed every downstream visualization in
        this notebook -- the cluster colors, the continuous-trajectory
        gradient, and the synthetic-vs-real label rendering.
    """),
    ("md", """
        ## 2. Cluster subjects on connectivity

        Pick a clustering method via ``CLUSTER_METHOD`` in the parameters
        cell. All four options go through the same ``cluster_subjects``
        helper and return a 1..K label per subject.

        - ``ward`` -- deterministic hierarchical clustering.
        - ``kmeans`` -- fast, needs a seed for reproducibility.
        - ``gmm`` -- Gaussian mixture; produces soft probabilities too.
        - ``consensus`` -- bootstrap-resampled k-means with modal vote,
          more stable at higher N.

        At N_CLUSTERS equal to the matched-subject count each subject gets its own cluster;
        honest outcome. Under synthetic mode with the default noise scale,
        every method recovers the ground-truth prototype assignment at
        100% (verified during development).
    """),
    ("code", """
        cluster_result = cluster_subjects(                                              # method-agnostic clustering; returns a ClusterResult dataclass
            X_conn, method=CLUSTER_METHOD, k=N_CLUSTERS, random_state=SYNTHETIC_SEED,    # reuse the synthetic seed for reproducibility (also seeds the stochastic clusterers)
        )
        cluster_by_subject = cluster_result.labels.rename('conn_cluster').astype(int)    # rename Series to 'conn_cluster' for clearer downstream merges; cast to int (was numpy int)
        print(f'Method: {cluster_result.method}, k={cluster_result.k}')                  # echo what we asked for vs what came back (k can be < requested if some clusters are empty)
        print('\\nCluster assignments:')
        print(cluster_by_subject)
    """),
    ("md", """
        **What you just saw.** Each subject's cluster label (1..K). Mice
        sharing a label have similar residual-connectivity profiles by
        the chosen clustering method.

        **Reading the assignments.** Cluster identity isn't meaningful in
        isolation -- the next cell profiles each cluster to identify
        which regions define it. What matters here is just "do similar
        residuals get grouped together?" When K equals the matched-
        subject count, every mouse gets its own cluster (uninformative).
        When K is smaller, mice are forced into shared groups, and the
        next cell tells you what the groups stand for biologically.

        **In synthetic mode** (USE_SYNTHETIC=True), the cluster recovery
        rate against the prototype labels is a validation of the
        pipeline at realistic N -- if clustering fails to recover
        prototypes from synthetic data, something is wrong with the
        method or noise level.
    """),
    ("md", """
        ## 3. Cluster profiling + naming

        For each cluster, compute the z-score of each connectivity region
        relative to the overall population. Big absolute z => that region
        defines that cluster. Auto-generate short labels like
        ``"cluster3: up-Red_Nucleus_both down-Corticospinal_left"``.
        Override with biological names in ``MANUAL_CLUSTER_NAMES`` once
        they're known.
    """),
    ("code", """
        profile = profile_clusters(X_conn, cluster_by_subject)                                          # per-cluster x per-region z-score table; helper handles z-score vs population mean and std
        auto_names = auto_name_clusters(profile, top_n=PROFILE_TOP_N, threshold=PROFILE_THRESHOLD)      # build human-readable labels from the top defining features per cluster
        names = dict(auto_names)                                                                         # copy the dict so we can layer manual overrides on top
        names.update(MANUAL_CLUSTER_NAMES)                                                               # MANUAL_CLUSTER_NAMES (parameters cell) lets the user replace auto names with biological ones; overrides win
        print('Cluster names:')
        for cid in sorted(names):
            print(f'  {names[cid]}')

        # Heatmap: clusters (rows) x top defining regions (cols)
        top_cols = (profile.abs().max(axis=0).sort_values(ascending=False).head(20).index.tolist())     # find the 20 regions with the largest peak |z-score| across any cluster (.max(axis=0) per column)
        fig, ax = plt.subplots(figsize=FIGSIZE_PROFILE)
        sns.heatmap(profile[top_cols].rename(index=names), cmap='RdBu_r', center=0,                     # red-blue diverging cmap centered at 0 so positive/negative z-scores read as red/blue; rename(index=names) replaces numeric cluster IDs with the readable labels
                    annot=True, fmt='.1f', ax=ax, cbar_kws={'label': 'z-score'})                        # annot=True writes z-score values in the cells; fmt='.1f' = 1 decimal place
        ax.set_title('Cluster profile: top 20 discriminating regions')
        plt.tight_layout()
        stamp_version(fig, label='07 cluster profile')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '07_cluster_profile.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        **What you just saw.** First, auto-generated cluster names like
        `cluster3: up-Red_Nucleus_both down-Corticospinal_left` -- these
        describe each cluster by which regions it stands out on. Then a
        heatmap: rows are clusters (with their auto-names), columns are
        the top 20 discriminating regions, color = z-score of that
        region in that cluster relative to the population.

        **What different patterns mean.**
        - Strongly red OR strongly blue cells in distinct columns per
          row = clusters genuinely differ in residual-connectivity
          profile. The auto-names should reflect real defining regions.
          *Example:* cluster A red on `Corticospinal_both` and cluster
          B blue on the same column means A retained more residual CST
          connectivity than B -- "CST-spared" vs "CST-affected" is a
          fair plain-English description of the boundary.
        - A cluster row with mostly pale cells = subjects in that
          cluster are near population mean in most regions. The cluster
          is "central" -- not strongly defined.
        - A column that's pale across ALL rows = that region doesn't
          distinguish clusters from each other; mice retained similar
          amounts regardless of which cluster they belong to.

        **Scientific reading.** The auto-names are a first-pass label
        for each cluster; you can override them with biologically
        meaningful names in `MANUAL_CLUSTER_NAMES` once you've identified
        what the clusters represent. The heatmap tells you which regions
        you'd defend the names with.
    """),
    ("md", """
        ## 4. Permutation validation

        Two null distributions for the "are clusters real?" question:

        - **LOO**: recompute within-cluster variance with one subject
          removed at a time. Narrow spread => clustering is robust to
          any single subject.
        - **Random-equal-N**: shuffle the cluster labels ``N_PERMUTATIONS``
          times, preserving group sizes; recompute within-cluster
          variance. If the observed statistic falls in the left tail of
          this null, clusters are tighter than chance.

        ``p_random`` is the fraction of shuffled draws with
        within-cluster variance <= observed. A small p (<0.05) says the
        clustering captures real structure.
    """),
    ("code", """
        pv = permutation_validate(X_conn, cluster_by_subject,                                  # cluster-validity test; returns observed statistic + LOO + permutation null distributions
                                  n_random=N_PERMUTATIONS, random_state=SYNTHETIC_SEED)         # n_random sets shuffle count; bigger = smoother null but slower
        print(f'Observed within-cluster variance: {pv[\"observed\"]:.2f}')                      # observed = sum of within-cluster variances on the actual clustering
        print(f'LOO range:                        [{min(pv[\"loo\"]):.2f}, {max(pv[\"loo\"]):.2f}]')  # tight LOO range = robust to dropping any single subject
        print(f'Random null mean:                 {float(np.mean(pv[\"random\"])):.2f}')         # average within-cluster variance from shuffled label assignments
        print(f'p (random <= observed):           {pv[\"p_random\"]:.3f}')                       # left-tail p-value: how often did a shuffled clustering achieve as-tight or tighter clusters?

        fig, ax = plt.subplots(figsize=FIGSIZE_PERMUTATION)                                     # one panel
        ax.hist(pv['random'], bins=50, alpha=0.7, label='Random-label null', color='grey')      # histogram of the shuffled null; alpha makes the gray semi-transparent so other elements show through
        ax.axvline(pv['observed'], color='red', linewidth=2, label=f'Observed ({pv[\"observed\"]:.1f})') # red vertical line at the observed value -- left of the histogram bulk = clusters tighter than chance
        for v in pv['loo']:
            ax.axvline(v, color='blue', alpha=0.3, linewidth=0.8)                                # one thin blue tick per LOO iteration; clustered ticks mean robustness, spread ticks mean a few subjects drive the result
        ax.set_xlabel('Within-cluster variance (sum across features)')
        ax.set_ylabel('Count of null draws')
        ax.set_title(f'Cluster validity (p={pv[\"p_random\"]:.3f}; blue ticks = LOO)')
        ax.legend()
        plt.tight_layout()
        stamp_version(fig, label='07 permutation')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '07_permutation_validation.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        **What this figure shows.** The histogram is the null distribution
        of within-cluster variance under random label shuffling. The
        observed within-cluster variance (red vertical) shows where the
        actual clustering falls in that distribution. Blue ticks above
        the histogram are leave-one-out variants -- recompute the same
        statistic with each subject removed.

        **What different patterns mean.**
        - Observed (red) far below the null distribution mean = the
          clustering is tighter than random. Mice within each cluster
          really do look more alike than random groups of the same
          size. p_random small (<0.05) confirms the gap is unlikely
          by chance.
        - Observed near or above the null distribution mean = clusters
          are noise. The algorithm divided subjects but the within-
          cluster coherence is no better than random groups. **Don't
          take cluster names seriously when this is the case.**
        - LOO ticks (blue) clustered tightly around the observed line
          = the result is robust; no single subject is driving the
          structure.
        - LOO ticks spread widely = one or two subjects dominate the
          clustering. Drop them and the structure changes substantially.
          Treat as fragile; don't generalize beyond the current cohort
          without expanded N.

        **Scientific reading.** This is the "are these clusters real?"
        check. If the observed line sits in the left tail of the null
        with tight LOO ticks, the cluster names from the previous cell
        describe meaningful structure. If not, the rest of the notebook
        is descriptive only.
    """),
    ("md", """
        ## 5. Alluvial: do subjects cluster the same way per phase?

        Cluster subjects separately within each phase based on their
        kinematic profile at that phase. An alluvial / Sankey flow shows
        which subjects stay in the same kinematic cluster across phases
        and which migrate. Heavy flow between aligned clusters means
        subjects have consistent kinematic phenotypes through time;
        crossing flows mean kinematic clusters shuffle.

        Double-dipping note: this clustering uses kinematic data only,
        not connectivity. It is an independent structural question from
        the connectivity clustering above.
    """),
    ("code", """
        agg_flat = data.AKDdf_agg_contact_flat()                                                                     # flattened aggregated kinematics; per (subject, phase, contact_group)
        kine_feature_cols = [c for c in agg_flat.columns if c.endswith(f'_{AGG_STAT}')]                              # pick only columns ending with _{AGG_STAT} (e.g. *_mean) so we cluster on summary statistics not every individual reach

        per_phase_labels = {}                                                                                         # phase -> Series of subject -> cluster_id
        for phase in PHASES:                                                                                          # one clustering per phase
            phase_slice = agg_flat[                                                                                   # filter rows: this phase + contacted reaches only
                (agg_flat['phase_group'] == phase) & (agg_flat['contact_group'] == 'contacted')
            ]
            if phase_slice.empty:                                                                                     # some phases may have no contacted reaches; skip rather than crash
                continue
            mat = (
                phase_slice.set_index('subject_id')[kine_feature_cols]                                                # subject_id as row index, only the *_AGG_STAT columns
                .fillna(0)                                                                                            # PCA / clustering can't handle NaN
            )
            try:
                k_phase = min(N_CLUSTERS, len(mat) - 1) if len(mat) > 1 else 1                                        # cap k at N-1 (can't have more clusters than subjects-1)
                cr = cluster_subjects(mat, method=CLUSTER_METHOD, k=max(k_phase, 2), random_state=SYNTHETIC_SEED)     # max(k_phase, 2) ensures at least 2 clusters for the Sankey to have flow to draw
                per_phase_labels[phase] = cr.labels
            except Exception as e:
                print(f'Phase {phase}: clustering failed ({e})')                                                     # phase-level clustering can fail at degenerate data; print and continue

        sankey_df = alluvial_source_records(per_phase_labels, PHASES)                                                 # turn the per-phase label dict into (source, target, value) edge records for plotly Sankey
        print(f'Sankey edges: {len(sankey_df)} between {len(per_phase_labels)} phases.')
    """),
    ("code", """
        import plotly.graph_objects as go                                                                # plotly Sankey (alluvial flow) lives in graph_objects, not the simpler express interface

        if len(sankey_df) == 0:                                                                          # guard: no edges means nothing to draw
            print('Not enough per-phase clustering data to draw the Sankey.')
        else:
            nodes = pd.unique(pd.concat([sankey_df['source'], sankey_df['target']]))                     # build a deduplicated list of all node IDs (each phase x cluster combo)
            node_index = {n: i for i, n in enumerate(nodes)}                                             # plotly Sankey needs integer node indices, not strings; this dict maps name -> index
            fig_sankey = go.Figure(data=[go.Sankey(                                                      # one Sankey trace
                node=dict(label=list(nodes), pad=12, thickness=14),                                      # pad spaces nodes vertically; thickness is node bar width in pixels
                link=dict(
                    source=sankey_df['source'].map(node_index).tolist(),                                 # remap each source string to its integer index
                    target=sankey_df['target'].map(node_index).tolist(),                                 # same for targets
                    value=sankey_df['value'].tolist(),                                                   # link width = number of subjects taking that source->target path
                ),
            )])
            fig_sankey.update_layout(
                title_text='Kinematic-cluster flow across phases',
                width=FIGSIZE_ALLUVIAL[0], height=FIGSIZE_ALLUVIAL[1],                                   # Sankey size is in pixels (not inches), specified directly on the layout
            )
            try:
                fig_sankey.write_image(str(EXAMPLE_OUTPUT_DIR / '07_alluvial.png'))                      # write_image needs the kaleido package; wrap in try/except so a missing kaleido doesn't break the notebook
            except Exception as e:
                print(f'(Could not save Sankey PNG -- kaleido missing or failed: {e})')
            fig_sankey.show()                                                                            # render inline; interactive in Jupyter (hover for tooltips)
    """),
    ("md", """
        **What this figure shows.** A Sankey diagram. Columns are phases
        (Baseline -> Post_Injury_1 -> Post_Injury_2-4 -> Post_Rehab_Test).
        Each column has K kinematic-cluster nodes; the ribbons between
        columns show how subjects move between clusters phase-to-phase.
        Ribbon thickness = number of subjects taking that path.

        **What different patterns mean.**
        - Mostly horizontal flows (each subject's ribbon stays in
          column-aligned clusters across phases) = kinematic phenotype
          is stable. Subjects reach in their own characteristic style
          throughout the experiment regardless of injury and ABT.
        - Crossing flows = subjects change which other subjects they
          cluster with at different phases. The kinematic neighborhood
          reshuffles with injury/recovery. *Example:* a subject that
          clustered with another at baseline might cluster with a
          different mouse post-injury if injury brought their reaching
          styles into closer alignment.
        - Convergence (many ribbons fanning into the same cluster
          post-injury) = injury collapses kinematic diversity; mice
          start reaching more similarly. Divergence post-rehab =
          ABT brings out individual differences again.

        **At small cohort sizes** the Sankey degenerates to one subject
        per cluster per phase, which makes the diagram a smoke test
        rather than informative. The structural question (do subjects
        keep their kinematic identity across phases?) becomes
        meaningful as N grows past per-cluster singletons.
    """),
    ("md", """
        ## 6. Build the per-subject per-phase trajectory table

        Pull ``{FEATURE}_{AGG_STAT}`` from ``data.AKDdf_agg_contact`` for the
        contacted reaches, restricted to the analyzable phase set. One row
        per subject per phase.
    """),
    ("code", """
        agg_flat = data.AKDdf_agg_contact_flat()                                                                       # flattened aggregated kinematics (one row per subject x phase x contact_group)
        feature_col = f'{TRAJECTORY_FEATURE}_{AGG_STAT}'                                                               # build the actual column name from the parameter pair (e.g. max_extent_mm + mean -> max_extent_mm_mean)
        if feature_col not in agg_flat.columns:                                                                         # defensive: if the user picked a feature/stat combo that doesn't exist, fail loudly with the closest matches
            raise KeyError(
                f'{feature_col!r} not in AKDdf_agg_contact columns. '
                f'Set TRAJECTORY_FEATURE / AGG_STAT to a valid combination. '
                f'First few available: {[c for c in agg_flat.columns if c.endswith("_" + AGG_STAT)][:10]}'              # show 10 valid columns ending in the requested suffix as hints
            )

        traj = agg_flat[                                                                                                # build the trajectory table: one row per (subject, phase) for plotting
            (agg_flat['contact_group'] == 'contacted')
            & (agg_flat['phase_group'].isin(PHASES))
        ][['subject_id', 'phase_group', feature_col]].copy()                                                            # keep only the columns we'll actually plot; .copy() prevents SettingWithCopyWarning when we mutate below
        traj['phase_order'] = traj['phase_group'].apply(lambda p: PHASES.index(p) if p in PHASES else -1)               # numeric phase order for the x-axis (PHASES.index returns position in the list); the lambda runs once per row

        # Attach connectivity PC1 score and cluster to each row, drop subjects without connectivity
        traj = traj.merge(conn_scores_df[['PC1']].reset_index(), on='subject_id', how='left')                           # left-join PC1 onto each (subject, phase) row; reset_index() pulls subject_id off the index into a column for the merge
        traj = traj.merge(cluster_by_subject.to_frame().reset_index(), on='subject_id', how='left')                     # same join for the cluster ID Series
        traj = traj.dropna(subset=['conn_cluster']).copy()                                                              # drop subjects with no connectivity / no cluster assignment (left-join produced NaN for them)
        traj['conn_cluster'] = traj['conn_cluster'].astype(int)                                                         # cast back to int (merge may have left it as float because of the NaN that dropna just removed)
        print(traj.sort_values(['subject_id', 'phase_order']).head(20))                                                 # preview: 20 rows sorted so each subject's phases appear together in order
    """),
    ("md", """
        **What you just saw.** Preview rows of the trajectory table. Each
        row is one (subject, phase) point with its kinematic value, its
        connectivity PC1 score, and its connectivity cluster ID. This
        is the data structure the next two figures plot.

        **Reading the table.** Confirm each subject has a row for every
        phase you expect (or fewer if some phases are missing data).
        Confirm the `PC1` column has real values, not NaN -- a NaN
        means the subject was missing connectivity data and got
        dropped. The `conn_cluster` column should match the assignments
        we printed earlier.
    """),
    ("md", """
        ## 7. Continuous view: trajectories colored by connectivity PC1

        One line per subject, x = phase (ordered), y = chosen feature.
        Line color is a continuous mapping of that subject's connectivity
        PC1 score. No grouping imposed. At small N this is the most honest view
        -- it shows whether the connectivity gradient corresponds to any
        visible trajectory structure without pretending there are discrete
        groups.
    """),
    ("code", """
        fig, ax = plt.subplots(figsize=FIGSIZE_TRAJ)                                               # one panel
        pc1_vals = conn_scores_df['PC1']                                                            # connectivity PC1 per subject; used as the color axis
        vmin, vmax = pc1_vals.min(), pc1_vals.max()                                                 # min/max for normalizing PC1 into a 0..1 colormap input
        cmap = get_cmap('viridis')                                                                  # perceptually-uniform sequential colormap
        for subj, grp in traj.groupby('subject_id'):                                                # one line per subject
            grp = grp.sort_values('phase_order')                                                    # ensure x-axis is in phase order even if traj rows came in shuffled
            color = cmap((grp['PC1'].iloc[0] - vmin) / (vmax - vmin + 1e-9))                        # normalize PC1 to [0,1] then look up color; +1e-9 prevents divide-by-zero if all PC1 values happen to be identical
            ax.plot(grp['phase_order'], grp[feature_col], '-o', color=color, label=subj, linewidth=2) # line + markers, color per subject
            ax.annotate(subj, (grp['phase_order'].iloc[-1], grp[feature_col].iloc[-1]),             # subject ID label at the rightmost point of each line
                        fontsize=8, xytext=(4, 0), textcoords='offset points')                      # nudge label 4 pixels right to avoid overlap with the marker
        ax.set_xticks(range(len(PHASES)))                                                           # tick at every phase position
        ax.set_xticklabels(PHASES, rotation=20, ha='right')                                         # rotate so longer phase names don't collide
        ax.set_ylabel(feature_col)
        ax.set_xlabel('Phase')
        ax.set_title(f'Trajectory of {feature_col} colored by connectivity PC1')
        sm = plt.cm.ScalarMappable(cmap=cmap,                                                       # ScalarMappable + colorbar pattern: matplotlib needs an explicit mappable to render a colorbar when colors come from a manually-constructed function
                                   norm=plt.Normalize(vmin=vmin, vmax=vmax))
        plt.colorbar(sm, ax=ax, label='Connectivity PC1 score')                                     # colorbar legend for the PC1 mapping
        plt.tight_layout()
        stamp_version(fig, label='07 continuous')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '07_trajectories_continuous.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        **What this figure shows.** One line per subject. X-axis is
        experimental phase in temporal order. Y-axis is the chosen
        kinematic feature. Line color is a continuous mapping of the
        subject's connectivity PC1 score (viridis: dark = low PC1,
        bright yellow = high PC1).

        **What different patterns mean.**
        - Subjects with similar PC1 scores (similar colors) following
          similar trajectory shapes = residual connectivity predicts
          kinematic trajectory. This is the headline outcome the
          analysis is reaching for.
        - Subjects with similar colors diverging across phases = no
          consistent coupling between residual PC1 and trajectory
          shape, at least at this cohort size.
        - Color gradient running smoothly from one end of the y-axis
          to the other at any phase = PC1 is sorting subjects by their
          kinematic value at that phase. *Example:* if the
          Post_Rehab_Test column shows yellow (high PC1) lines at the
          top and dark (low PC1) lines at the bottom, mice with the
          most residual connectivity also have the highest post-ABT
          kinematic value -- direct support for "residual connectivity
          predicts recovery."
        - No relationship between color and y-position = PC1 is not
          coupling to this feature in this cohort.

        **Why "continuous" matters.** This view doesn't impose
        clusters; PC1 is a smooth gradient. At small cohort sizes
        clusters are unstable, but PC1 still has meaning. If you see
        a coherent color-to-position mapping here, that's a signal
        that doesn't depend on clustering survival.
    """),
    ("md", """
        ## 8. Grouped view: trajectories by named connectivity cluster

        Same trajectories, colored by cluster membership and labeled with
        the auto-generated or manual names from section 3.
    """),
    ("code", """
        fig, ax = plt.subplots(figsize=FIGSIZE_TRAJ)
        palette = get_cmap('tab10')                                                                              # 10 distinct discrete colors; cluster IDs 1..K cycle through them
        already_labeled = set()                                                                                  # track which cluster names have already been added to the legend so we don't repeat them
        for cluster_id in sorted(traj['conn_cluster'].unique()):                                                 # iterate clusters in numeric order so legend ordering is deterministic
            grp = traj[traj['conn_cluster'] == cluster_id].sort_values(['subject_id', 'phase_order'])             # this cluster's subjects, sorted so each subject's phases are consecutive
            color = palette((int(cluster_id) - 1) % 10)                                                          # cluster_id - 1 because tab10 indexes 0..9; modulo 10 wraps if clusters exceed 10
            cluster_name = names.get(int(cluster_id), f'cluster{cluster_id}')                                    # use the named version if present, else fall back to a numeric placeholder
            for subj, sub in grp.groupby('subject_id'):                                                          # one trajectory per subject within the cluster
                label = cluster_name if cluster_name not in already_labeled else None                            # only the first subject in each cluster contributes a legend entry; subsequent subjects pass label=None to suppress duplicates
                ax.plot(sub['phase_order'], sub[feature_col], '-o', color=color, alpha=0.85,
                        label=label, linewidth=2)
                already_labeled.add(cluster_name)
        ax.set_xticks(range(len(PHASES)))
        ax.set_xticklabels(PHASES, rotation=20, ha='right')
        ax.set_ylabel(feature_col)
        ax.set_xlabel('Phase')
        ax.set_title(f'Trajectory of {feature_col} by connectivity cluster (k={N_CLUSTERS})')
        ax.legend(loc='best', fontsize=7)                                                                        # 'best' lets matplotlib pick the least-overlapping legend location
        plt.tight_layout()
        stamp_version(fig, label='07 grouped')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '07_trajectories_by_cluster.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        **What this figure shows.** Same trajectories as the continuous
        view, but colored by discrete cluster membership instead of a
        continuous gradient. The legend names match the auto-names
        (or your manual overrides) from section 3.

        **What different patterns mean.**
        - Lines within a cluster following parallel paths across phases
          = the cluster has a coherent trajectory profile. Mice with
          similar residual-connectivity profiles really do reach
          similarly across the experiment.
        - Lines from different clusters tracking each other (one
          cluster's lines shadowing another's) = the clustering
          partition isn't separating kinematic trajectory groups; the
          cluster boundary doesn't match a kinematic boundary.
        - Lines within a cluster scattering wildly = the cluster
          contains kinematically heterogeneous subjects. The
          residual-connectivity grouping doesn't predict this kinematic
          feature; another feature might work better.

        **Comparison to the continuous view.** If the continuous PC1
        view shows a clean gradient but the cluster view looks
        scrambled, the discretization (forcing K clusters) is
        destroying signal that PC1 carries continuously. If both views
        agree, the structure is robust to the choice.
    """),
    ("md", """
        ## 9. Interaction LMM template

        Asks whether the phase effect on the feature differs by
        connectivity cluster, using the same nested random-effects
        structure as notebook 05.

        Formula: ``feature ~ C(phase_group) * C(conn_cluster)``
        with random intercept for subject_id and nested session-within-
        subject.

        When the cluster count equals the subject count this is statistically vacuous -- the
        interaction is a reparameterization rather than a tested effect.
        Set ``RUN_INTERACTION_LMM = False`` to skip. When N grows and
        clusters contain multiple subjects this becomes the primary
        inferential test that answers "do connectivity groups follow
        different kinematic trajectories?".
    """),
    ("code", """
        import warnings                                                                                       # suppress convergence warnings inside the fit; we check converged status from the result
        if RUN_INTERACTION_LMM and traj['conn_cluster'].nunique() > 1:                                          # only run if user opted in AND we have at least 2 clusters (interaction is meaningless with 1)
            from statsmodels.formula.api import mixedlm                                                         # local import keeps cell standalone

            reach_level = data.AKDdf[data.AKDdf['contact_group'] == 'contacted'].copy()                         # one row per contacted reach -- LMM works at the reach level for proper variance partitioning
            reach_level['phase_group'] = pd.Categorical(                                                        # ordered Categorical so contrasts read as "later phase vs Baseline"
                reach_level['phase_group'], categories=PHASES, ordered=True
            )
            # Attach connectivity cluster ID by subject_id
            reach_level = reach_level.merge(cluster_by_subject.to_frame().reset_index(),                        # left-join cluster ID onto each reach
                                            on='subject_id', how='left')
            # Drop subjects without a cluster assignment (no connectivity data)
            subset = reach_level.dropna(subset=['conn_cluster', TRAJECTORY_FEATURE])                            # require both cluster assignment and the target kinematic feature
            subset['conn_cluster'] = subset['conn_cluster'].astype(int).astype(str)                             # statsmodels' C() works best with string-typed categories; this avoids float-cluster-id issues
            if subset['subject_id'].nunique() >= 2 and subset['conn_cluster'].nunique() >= 2:                    # need at least 2 of each for the model to identify the interaction
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore')
                        model = mixedlm(                                                                         # MixedLM: linear mixed-effects model = OLS + random effects
                            formula=f"Q('{TRAJECTORY_FEATURE}') ~ C(phase_group) * C(conn_cluster)",            # Q() escapes feature name in case of special chars; * = main effects + interaction
                            data=subset,
                            groups='subject_id',                                                                 # random intercept per subject
                            vc_formula={'session': '0 + C(session_date)'},                                       # nested random intercept for session_within_subject; '0 +' suppresses the auto intercept
                        )
                        result = model.fit(reml=True, method='lbfgs', disp=False)                                # REML for unbiased variance estimates; lbfgs is a fast quasi-Newton optimizer
                    wald = result.wald_test_terms().table                                                        # joint Wald test of each fixed-effect term
                    print(wald)
                    print('\\nInteraction p-value for phase x conn_cluster:')
                    pcol = 'pvalue' if 'pvalue' in wald.columns else 'P>chi2'                                   # statsmodels 0.14+ renamed the p-value column to 'pvalue'; fall back to legacy 'P>chi2' for older statsmodels
                    interaction_rows = [i for i in wald.index if 'phase_group' in i and 'conn_cluster' in i]    # filter the Wald table to just the interaction row(s)
                    for i in interaction_rows:
                        print(f'  {i}: {float(wald.loc[i, pcol]):.4f}')                                         # cast to float since 0.14+ returns 0-d arrays; .4f formats with 4 decimal places
                except Exception as e:
                    print(f'Interaction LMM failed to fit (expected at very small N): {e}')                     # singular covariance matrices etc. show up here at small N
            else:
                print('Too few subjects or clusters to fit the interaction LMM.')
        else:
            print('Skipping interaction LMM (RUN_INTERACTION_LMM=False or only one cluster present).')
    """),
    ("md", """
        **What you just saw.** A Wald test table for the interaction LMM
        (`feature ~ phase * conn_cluster`). The interaction p-value
        tests whether the phase effect on the feature differs by
        connectivity cluster.

        **What different patterns mean.**
        - Small interaction p (<0.05) = the trajectories shown above
          really are different by cluster. Different residual-
          connectivity groups follow different recovery courses, which
          is the headline scientific claim.
        - Large interaction p = the clusters' average trajectories
          look similar across phases, even if individual subjects
          within clusters vary. No detectable group-level coupling.
        - "Interaction LMM failed to fit" = the model is degenerate at
          this cohort size. Common when each cluster contains one
          subject (the interaction collapses to a per-subject effect)
          or when categorical levels have no within-level replication.
        - "Skipping interaction LMM" = `RUN_INTERACTION_LMM=False` was
          set or only one cluster was found.

        **At small cohort sizes** with each cluster having ~1 subject,
        the interaction is a reparameterization rather than a tested
        effect. Treat the framework as in-place-for-when-N-grows: the
        same code becomes a real inferential test of "do connectivity
        groups follow different recovery trajectories?" once each
        cluster contains multiple subjects.
    """),
    ("md", """
        ## 10. Ascending connectivity placeholder

        When ascending-projection tracing data lands in mousedb (new
        table: ``ascending_region_counts`` or equivalent), the analysis
        plugs in here:

        **Question**: within each descending-connectivity cluster (i.e.
        for mice with similar motor-output sparing profiles), does
        variability in ascending connectivity predict kinematic
        trajectory differences?

        **Implementation sketch**:

        1. Load ascending counts alongside descending: add a
           ``data.ascendingdf`` section to ``data_loader``.
        2. For each ``conn_cluster`` in this notebook, subset subjects
           and compute an ascending-connectivity PC score.
        3. Regress the kinematic trajectory (e.g. per-phase PC1 scores)
           onto the ascending score with subject as random effect.
        4. Partial-out the descending contribution so residual kinematic
           differences are attributed only to ascending variability.

        At current N and without ascending data this remains a note.
        When the cohort and data are ready, the pattern above slots in
        without changing the earlier notebooks.
    """),
]


# ============================================================================
# 08_hypothesis_informed_tests.ipynb
# ============================================================================

HYPOTHESIS_NB = [
    ("md", """
        # 08 - Hypothesis-informed tests

        **Purpose**: run the canonical analysis pipeline (notebooks 01-07) in a
        way that explicitly leverages two sources of prior knowledge and
        tests their adequacy:

        1. **Literature-derived region prior**
           (``mousedb.region_priors.SKILLED_REACHING``). The field already
           has strong hypotheses about which descending regions matter for
           reaching. We test whether adding lower-priority regions gives
           extra predictive power beyond the prior.
        2. **eLife groupings**. The grouping lumps atomic regions into
           functional families. If a group's subregions have opposite
           functional roles, the grouping hides variance. We drill down
           per group to test this.

        Sections:

        1. Grouped vs ungrouped connectivity PCA side-by-side.
        2. Per-group drill-down: within each eLife group, PCA on its
           atomic-region constituents.
        3. Nested LMM comparison: does adding prior-ranked regions
           improve fit over phase-only?
        4. Prior-weighted PCA: does weighting features by predicted
           importance change what the decomposition sees?

        Supports ``USE_SYNTHETIC=True`` for pipeline validation at
        realistic N (see notebook 07).
    """),
    ("code", """
        # parameters
        USE_SYNTHETIC = False
        SYNTHETIC_N = 30
        SYNTHETIC_SEED = 42
        TOP_K_PRIORS = 5          # How many top-priority regions to add in the nested-LMM step
        TARGET_FEATURE = 'max_extent_mm'
        PRIOR_DECAY = 0.1         # Exponential-decay rate for prior weighting; 0 = uniform
        FIGSIZE_VAR = (10, 5)
        FIGSIZE_DRILL = (16, 8)
        FIGSIZE_WEIGHTED = (14, 6)
    """),
    ("code", """
        import numpy as np                                                                          # numerical arrays
        import pandas as pd                                                                          # dataframes
        import matplotlib.pyplot as plt                                                              # plotting
        import seaborn as sns                                                                        # heatmaps
        from sklearn.preprocessing import StandardScaler                                             # z-scorer (used twice in this notebook for PCA prep)
        from sklearn.decomposition import PCA                                                        # principal component analysis

        from endpoint_ck_analysis import SKILLED_REACHING, ordered_hemisphere_columns                # canonical region prior + helper for priority-ordered column lists
        from endpoint_ck_analysis.config import CACHE_DIR, EXAMPLE_OUTPUT_DIR, ANALYZABLE_PHASES     # cache dir + output dir + analyzable phase set
        from endpoint_ck_analysis.data_loader import load_all                                        # one-shot loader (supports synthetic mode via flags)
        from endpoint_ck_analysis.helpers.hierarchical import (                                      # tools for grouped-vs-ungrouped analysis
            build_group_region_map, drill_down_pca, grouped_vs_ungrouped_summary,                    # group->region map + per-group PCA + side-by-side variance summary
        )
        from endpoint_ck_analysis.helpers.dimreduce import (                                         # tools for prior-weighted decomposition
            priority_weights_from_prior, apply_feature_weights,                                       # build per-feature weights from a region prior + apply them to an X matrix
        )
        from endpoint_ck_analysis.helpers.models import compare_nested_lmms                          # nested LMM model-comparison helper (AIC/BIC/LRT)
        from endpoint_ck_analysis.helpers.plotting import stamp_version                              # figure version footer

        EXAMPLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)                                        # ensure output folder exists
        data = load_all(                                                                              # data load with synthetic flags wired through
            use_synthetic=USE_SYNTHETIC,
            synthetic_n=SYNTHETIC_N,
            synthetic_seed=SYNTHETIC_SEED,
            use_cache=not USE_SYNTHETIC,                                                             # synthetic always rebuilds (cache reflects last real load)
            write_cache=not USE_SYNTHETIC,                                                           # don't overwrite real-data cache with synthetic
            verbose=False,
        )
        print(f"Running on {'SYNTHETIC' if USE_SYNTHETIC else 'REAL'} data. "
              f"N={len(data.matched_subjects)} subjects.")
    """),
    ("md", """
        ## 1. Grouped vs ungrouped PCA

        Run connectivity PCA at both the eLife-group level
        (``ACDGdf_wide``, ~80 columns) and the atomic-region level
        (``ACDUdf_wide``, ~700 columns). Different variance-explained
        profiles imply the grouping is hiding or creating structure.
    """),
    ("code", """
        summary = grouped_vs_ungrouped_summary(                                                      # helper runs PCA on both matrices and returns a long-format summary
            data.ACDGdf_wide.fillna(0), data.ACDUdf_wide.fillna(0), n_components=5,                  # eLife-grouped matrix vs atomic-region matrix; n_components=5 is enough to show the scree shape
        )
        print(summary)                                                                                # tabular printout of variance per PC at both levels

        fig, ax = plt.subplots(figsize=FIGSIZE_VAR)
        for level, grp in summary.groupby('level'):                                                  # one line per level (grouped, ungrouped)
            ax.plot(grp['component'], grp['variance_explained'], marker='o', label=level)            # standard scree-style line plot
        ax.set_ylabel('Variance explained')
        ax.set_xlabel('Component')
        ax.set_title('Connectivity PCA: grouped (eLife) vs ungrouped (atomic regions)')
        ax.legend()
        ax.grid(alpha=0.3)                                                                            # faint grid for easier reading
        stamp_version(fig, label='08 grouped vs ungrouped')
        plt.tight_layout()
        plt.savefig(EXAMPLE_OUTPUT_DIR / '08_grouped_vs_ungrouped.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        **What you just saw + what the figure shows.** A long-format
        table of variance-per-PC at both granularity levels (grouped
        eLife regions vs atomic regions), and a scree-style line plot
        showing the same data. Two lines: one for grouped, one for
        ungrouped.

        **What different patterns mean.**
        - The two lines tracking each other closely = the eLife
          grouping captures the dominant variance structure. Aggregating
          to group level loses little. **What this means for the
          paper:** the upstream pipeline's choice to use grouped
          regions is defensible.
        - Ungrouped line consistently above grouped (more variance per
          PC) = atomic-region variance is being averaged out by the
          grouping. The drill-down (next panel) is justified to find
          which groups are hiding subregional differences.
        - Ungrouped line consistently below grouped = the grouping is
          creating structure that doesn't exist at the atomic level.
          Rare but possible -- typically indicates the grouping is
          summing weakly-correlated subregions in a way that
          amplifies their joint signal.
        - Both lines flat = no PC dominates at either granularity;
          residual variation is spread broadly. Reduces the value of
          PC-based summaries for downstream prediction at any
          granularity.
    """),
    ("md", """
        ## 2. Per-group drill-down

        For each eLife group, run a mini-PCA on its atomic-region
        columns. A group whose PC1 captures most of its variance is a
        coherent bundle; a group where variance is spread across many
        PCs has subregional heterogeneity the grouping is hiding.
    """),
    ("code", """
        group_region_map = build_group_region_map(data.counts_groupeddf)                                # build {group_name: [atomic_region_columns]} map from the long-format grouped counts table
        drill_rows = []                                                                                  # accumulator: one dict per group with that group's PCA result
        for group in data.FCDGdf_wide.columns.map(lambda c: c.rsplit('_', 1)[0]).unique():               # iterate unique group names; column names are 'GROUP_left'/'GROUP_right'/'GROUP_both' so rsplit('_',1)[0] strips the hemisphere suffix
            result = drill_down_pca(data.ACDUdf_wide.fillna(0), group, group_region_map, n_components=3) # helper: subset atomic columns belonging to this group and run a mini-PCA on them
            if result is None:                                                                            # helper returns None when group has fewer atomic regions than samples (PCA would be ill-posed)
                continue                                                                                  # skip this group; loop continues with the next
            drill_rows.append({                                                                           # build a flat record for the summary dataframe
                'group': group,                                                                           # group name (eLife label)
                'n_atomic': result.n_atomic_regions,                                                      # how many atomic regions fed into this group's mini-PCA
                'PC1_var': float(result.explained_variance_ratio[0]) if len(result.explained_variance_ratio) else np.nan,    # PC1 share of within-group variance; np.nan if no PCs returned
                'PC2_var': float(result.explained_variance_ratio[1]) if len(result.explained_variance_ratio) > 1 else np.nan,  # PC2 share; np.nan if fewer than 2 PCs
                'PC3_var': float(result.explained_variance_ratio[2]) if len(result.explained_variance_ratio) > 2 else np.nan,  # PC3 share; np.nan if fewer than 3 PCs
            })

        drill_df = pd.DataFrame(drill_rows).sort_values('n_atomic', ascending=False)                     # convert list-of-dicts to dataframe; sort biggest groups first so the bar chart reads left-to-right by size
        print(drill_df.to_string(index=False))                                                            # full table to stdout; to_string(index=False) suppresses the row-number column

        # Plot: stacked bar of PC1/PC2/PC3 variance per group
        fig, ax = plt.subplots(figsize=FIGSIZE_DRILL)                                                    # figsize from parameters cell so users can resize without editing here
        x = range(len(drill_df))                                                                         # x positions: one tick per group
        bottom = np.zeros(len(drill_df))                                                                 # running bottom for stacking; starts at zero for PC1
        for pc_col, color, label in [('PC1_var', 'steelblue', 'PC1'),                                    # iterate the three PCs; each adds another stacked layer
                                     ('PC2_var', 'coral', 'PC2'),
                                     ('PC3_var', 'seagreen', 'PC3')]:
            vals = drill_df[pc_col].fillna(0).values                                                     # variance shares; fillna(0) because groups with <3 atomic regions have NaN for higher PCs
            ax.bar(x, vals, bottom=bottom, color=color, label=label)                                     # draw this PC layer on top of the previous bottom
            bottom = bottom + vals                                                                        # advance bottom for the next iteration
        ax.set_xticks(list(x))                                                                            # explicit tick positions
        ax.set_xticklabels(drill_df['group'], rotation=60, ha='right', fontsize=8)                       # group labels at 60deg right-aligned; small font so they fit
        ax.set_ylabel('Variance explained within group')
        ax.set_title('Per-eLife-group drill-down: variance across top 3 within-group PCs')
        ax.axhline(0.9, color='black', linestyle='--', linewidth=0.6, alpha=0.5)                         # reference line at 90 percent: groups whose top-3 stack reaches it are "compact" in 3D
        ax.legend()
        stamp_version(fig, label='08 drill-down')                                                        # version stamp in the footer for traceability
        plt.tight_layout()                                                                                # tighten margins so labels don't clip
        plt.savefig(EXAMPLE_OUTPUT_DIR / '08_drill_down.png', dpi=150, bbox_inches='tight')              # write committed example PNG; dpi=150 for crisp output
        plt.show()
    """),
    ("md", """
        **What you just saw + what the figure shows.** A printed table
        listing each eLife group with its top-3 within-group PC variance
        shares, and a stacked horizontal bar chart of the same data.
        Each bar is one group; the three colored segments are PC1, PC2,
        PC3 within that group. The dashed line at 0.9 is a "almost
        everything captured" reference.

        **What different patterns mean.**
        - Bars dominated by blue (PC1) = the group is internally
          coherent; one within-group axis captures all subregional
          variation. The eLife grouping is defensible for that group --
          lumping its atomic regions together is consistent with the
          data.
        - Bars where PC1, PC2, PC3 are all substantial = the group has
          subregional heterogeneity the grouping is hiding. *Example:*
          if `Reticular Nuclei` splits its variance evenly across PC1,
          PC2, PC3, the subregions of the reticular formation are doing
          different things across mice; lumping them costs signal.
          Candidate to split in future work.
        - Bars below the dashed line = even three within-group PCs
          don't capture everything. Most variance is in higher-order
          PCs, which often means the within-group structure is mostly
          noise from a single dominant axis or that the group is too
          heterogeneous to summarize.

        **Scientific reading.** Use this to identify groups whose
        subregions deserve closer attention in the paper or in future
        cohorts. Internally-coherent groups can be reported at the
        group level safely; heterogeneous groups need explicit
        breakdowns or split before group-level claims are made.
    """),
    ("md", """
        ## 3. Nested LMM comparison

        Fit a sequence of LMMs on the chosen target kinematic feature,
        adding connectivity-region covariates in order of prior
        priority. Compare via AIC / BIC / LRT. A small ``p_vs_prior``
        means the step added predictive power beyond the previous model.

        At small N the LMMs are underdetermined whenever there are more
        covariates than subjects; synthetic mode (N=30) is the right
        test bench for this section.
    """),
    ("code", """
        reach_level = data.AKDdf[data.AKDdf['contact_group'] == 'contacted'].copy()                       # work on per-reach rows; restrict to reaches that actually contacted the pellet (kinematics need a contact event)
        reach_level['phase_group'] = pd.Categorical(                                                      # convert phase to ordered categorical so statsmodels treats it as a factor with intentional ordering
            reach_level['phase_group'], categories=list(ANALYZABLE_PHASES), ordered=True,                  # category order taken from the parameters cell list (Pre, post-injury, post-rehab, etc.)
        )

        # Attach top-K prior-ranked connectivity region values to every reach
        # by merging on subject_id from the wide connectivity matrix.
        canonical_cols = ordered_hemisphere_columns(                                                       # helper returns columns in the prior-defined region order, restricted to those actually present
            SKILLED_REACHING, available=data.FCDGdf_wide.columns.tolist(),                                 # SKILLED_REACHING: the prior list (CST > RuST > ReST etc.); available filters out missing columns
        )
        top_cols = [c for c in canonical_cols if c.endswith('_both')][:TOP_K_PRIORS]                       # keep bilateral '_both' versions only (avoids double-counting hemispheres) and slice the top-K from the parameters cell
        conn_wide = data.FCDGdf_wide[top_cols].fillna(0)                                                   # subset wide matrix to the chosen columns; fill missing with 0 since absent data ~= no traced cells
        # Rename to safe python identifiers for patsy formula
        safe_names = {c: f"conn_{i}" for i, c in enumerate(top_cols)}                                      # patsy chokes on column names containing dots/dashes; map to conn_0, conn_1, ...
        conn_wide = conn_wide.rename(columns=safe_names).reset_index()                                     # apply rename and lift subject_id from the index back to a column for the merge
        reach_level = reach_level.merge(conn_wide, on='subject_id', how='left')                            # left-join: every reach now carries its subject's connectivity values; subjects missing connectivity get NaN

        rhs_parts = list(safe_names.values())                                                              # list of patsy-safe predictor names (the conn_N columns)
        model_specs = [                                                                                    # nested model sequence: each spec is (label, additional_RHS_terms)
            ('baseline (phase only)', ''),                                                                 # baseline: phase fixed effect only (added by helper); empty string means no extra covariates
            (f'+top{TOP_K_PRIORS}_priors', ' + '.join(rhs_parts)),                                         # extended: baseline + all top-K connectivity regions joined with patsy '+'
        ]

        nested_results = compare_nested_lmms(                                                              # helper fits each model in sequence and returns AIC, BIC, log-likelihood, LRT p-value vs previous spec
            reach_level.dropna(subset=rhs_parts + [TARGET_FEATURE]),                                       # drop rows missing any predictor or the target so all models compare on the same data
            target_feature=TARGET_FEATURE,                                                                 # name of kinematic feature to model (from parameters cell)
            model_specs=model_specs,                                                                       # the nested sequence defined above
            groups='subject_id',                                                                           # outermost random-effect grouping: subject
            vc_formula={'session': '0 + C(session_date)'},                                                 # nested random effect: session_date within subject; '0 +' suppresses intercept so each session gets its own random slope
        )
        print(nested_results.to_string(index=False))                                                        # tabular AIC/BIC/LRT summary; small p_vs_prior column means the added regions matter
    """),
    ("md", """
        **What you just saw.** A table comparing two LMMs:
        - `baseline (phase only)`: kinematic feature ~ phase fixed
          effect, with subject and session random effects.
        - `+top{TOP_K_PRIORS}_priors`: same baseline plus the top-K
          prior-ranked connectivity regions added as fixed-effect
          covariates.

        Columns include `aic`, `bic`, `loglik`, `chi2_vs_prior`,
        `p_vs_prior` (LRT), and `converged`.

        **What different patterns mean.**
        - `+top{TOP_K_PRIORS}_priors` has a smaller AIC/BIC than
          baseline AND `p_vs_prior` < 0.05 = adding the top-priority
          connectivity regions improves the model fit beyond what
          phase alone explains. Residual connectivity at those
          regions is doing predictive work for this kinematic
          feature.
        - Smaller AIC but non-significant `p_vs_prior` = the AIC
          improvement is marginal and could be over-fitting; the LRT
          says the simpler model is sufficient. AIC and LRT can
          disagree at small N -- treat AIC favorability with caution.
        - Larger AIC for the extended model OR `p_vs_prior` > 0.05 =
          the prior-ranked regions don't help past phase. The
          literature priority list, at least at this top-K cutoff,
          isn't predictive in this cohort.
        - `converged = False` = the model failed to fit; the
          comparison is unreliable. Common when there are more
          covariates than subjects -- synthetic mode with N=30 is
          where this analysis becomes trustworthy.

        **Scientific reading.** This test is a direct check of "is
        residual connectivity in the literature-flagged regions
        helping us predict reaching, or are we just relying on
        phase?" A surviving p_vs_prior validates the prior; a failing
        one suggests we should let the data choose features instead
        of the prior.
    """),
    ("md", """
        ## 4. Prior-weighted PCA

        Re-run the connectivity PCA after multiplying each z-scored
        feature by ``exp(-PRIOR_DECAY * rank)`` where ``rank`` is the
        region's position in the prior. This amplifies variance from
        priority regions; PC1 is then forced to preferentially load on
        them unless a low-priority region truly dominates.

        Compare the weighted and unweighted top loadings side by side.
        Regions that appear in both are robust; those that appear only
        unweighted are data-driven additions the prior de-emphasized.
    """),
    ("code", """
        X_conn = data.FCDGdf_wide.fillna(0)                                                               # wide subject x region matrix; fill NaNs with 0 so PCA has no missing values
        X_ordered = X_conn[canonical_cols]                                                                # reorder columns into prior-priority order so weights align by position later
        X_scaled = pd.DataFrame(                                                                          # z-score each column (mean 0, std 1) so PCA isn't dominated by raw-count magnitude
            StandardScaler().fit_transform(X_ordered),                                                    # sklearn StandardScaler: subtracts column mean, divides by std
            columns=X_ordered.columns, index=X_ordered.index,                                              # rebuild a dataframe so column/index labels survive the scaler call
        )

        weights = priority_weights_from_prior(SKILLED_REACHING, X_ordered.columns, decay=PRIOR_DECAY)     # helper builds w_i = exp(-decay * rank_i); higher decay = more aggressive down-weighting of low-priority regions
        X_weighted = apply_feature_weights(X_scaled, weights)                                              # element-wise multiply each column by its weight; PCA on this is the prior-weighted version

        pca_raw = PCA(n_components=min(3, len(X_ordered) - 1)).fit(X_scaled.values)                       # sklearn PCA on raw z-scored matrix; n_components capped at min(3, N-1) since PCA can't exceed N-1 components
        pca_weighted = PCA(n_components=min(3, len(X_ordered) - 1)).fit(X_weighted.values)                # PCA on weighted matrix; same cap so the two are comparable

        loadings_raw = pd.Series(pca_raw.components_[0], index=X_scaled.columns)                          # PC1 loadings (eigenvector); components_[0] is the first principal component
        loadings_weighted = pd.Series(pca_weighted.components_[0], index=X_scaled.columns)                # weighted PC1 loadings; same shape, different values

        top_raw = loadings_raw.abs().nlargest(10).index                                                   # 10 regions with largest absolute loading on raw PC1 (sign-agnostic since PC sign is arbitrary)
        top_weighted = loadings_weighted.abs().nlargest(10).index                                         # same for weighted PC1

        print('Top 10 regions on PC1 (unweighted):')
        print(loadings_raw.loc[top_raw].sort_values())                                                    # sort by signed value so reader sees positive vs negative loadings clearly
        print()
        print('Top 10 regions on PC1 (prior-weighted):')
        print(loadings_weighted.loc[top_weighted].sort_values())

        # Side-by-side horizontal bar chart of top 15 regions by |loading|, both versions
        union = list(pd.Index(top_raw).union(pd.Index(top_weighted))[:15])                                # union of top-10 from each version, truncated to 15 to keep the chart readable
        comp = pd.DataFrame({                                                                             # build a 2-column comparison dataframe (rows=regions, columns=unweighted/prior-weighted)
            'unweighted': loadings_raw.loc[union],
            'prior-weighted': loadings_weighted.loc[union],
        }).reindex(union)                                                                                 # ensure row order matches the 'union' list (avoids alphabetical reshuffling)
        fig, ax = plt.subplots(figsize=FIGSIZE_WEIGHTED)                                                  # figsize from parameters cell
        comp.plot(kind='barh', ax=ax, width=0.8)                                                          # horizontal bars; pandas plots the two columns side-by-side per region
        ax.axvline(0, color='black', linewidth=0.5)                                                       # zero reference line so positive vs negative loadings are visually obvious
        ax.set_xlabel('PC1 loading')
        ax.set_title(f'PC1 loadings: unweighted vs prior-weighted (decay={PRIOR_DECAY})')                 # title includes the decay value so readers know which weighting regime they're seeing
        ax.invert_yaxis()                                                                                 # put highest-priority region at the top (matplotlib default puts row-0 at the bottom for barh)
        stamp_version(fig, label='08 prior weighted')                                                     # version stamp in the footer
        plt.tight_layout()                                                                                 # margin tightening
        plt.savefig(EXAMPLE_OUTPUT_DIR / '08_prior_weighted.png', dpi=150, bbox_inches='tight')           # write committed example PNG
        plt.show()
    """),
    ("md", """
        **What you just saw + what the figure shows.** Two printed top-10
        loading lists for PC1 (unweighted vs prior-weighted), then a
        side-by-side horizontal bar chart of those 15 union regions
        across both versions.

        **What different patterns mean.**
        - Top regions on PC1 (unweighted) and (prior-weighted) overlap
          heavily, with similar bar magnitudes = the dominant variance
          axis is already aligned with the prior; the prior didn't
          change what PC1 sees. **What this means for the paper:** the
          data and the literature agree on which regions matter; the
          analyses in 01-07 rest on solid ground.
        - Lists differ substantially = the unweighted PCA was driven
          by regions the prior de-emphasizes. *Example:* if unweighted
          PC1 is dominated by `Hypothalamic_both` but prior-weighted
          PC1 is dominated by `Corticospinal_both`, the cohort's
          largest residual variation is in a region the literature
          doesn't link to reaching. Either the literature is missing
          something, the variance is artifactual (e.g. immune
          response), or this cohort happens to vary on tracts the
          reaching circuit doesn't lean on.
        - Same regions but flipped magnitudes = the same biology
          shows up in both, but the prior amplifies different
          contributions. The prior is reweighting within an already-
          aligned axis -- an editorial change rather than a discovery.

        **Scientific reading.** Each interpretation has a different
        discussion implication. The matched case lets the paper claim
        the prior is doing real work; the divergent case is a finding
        in itself (data revising the literature) and warrants
        explicit treatment in the discussion.
    """),
    ("md", """
        ## Summary

        Together, the four tools above test whether the canonical
        pipeline's implicit choices -- eLife grouping for decomposition,
        unweighted PCA, phase-only LMMs -- lose information that prior
        knowledge or finer-grained data could recover. Interpretation
        guide:

        - If grouped and ungrouped scree plots match closely, the eLife
          grouping isn't discarding much. If they diverge, the atomic
          analysis deserves a seat at the table.
        - If within-group drill-downs show one group with distributed
          variance across PCs, that group has subregional heterogeneity
          the paper should call out.
        - If the nested LMM's ``p_vs_prior`` is small, the added
          regions carry real independent information; the prior alone
          wasn't enough.
        - If prior-weighted PC1 differs from unweighted PC1, the
          ordering matters and should be discussed. If they match,
          the dominant variance axes were already aligned with the
          prior.

        Paper-readiness roadmap (tensor decomposition, cross-validated
        prediction, supervised clustering, session-level growth curves,
        outlier-aware modeling) is tracked in
        [`project_analysis_roadmap`](../docs/assumptions.md) and in the
        user memory. Revisit when N scales.
    """),
]


# ============================================================================
# 99_figure_gallery.ipynb
# ============================================================================

GALLERY_NB = [
    ("md", """
        # 99 - Figure gallery

        Aggregates every PNG saved by the analytical notebooks (01-06) into
        one scrollable summary. Useful for showing someone the outputs at a
        glance without re-running anything.

        Each figure is loaded from ``example_output/``. If a file is missing,
        re-run the notebook that produces it.
    """),
    ("code", """
        from pathlib import Path                                                                          # pathlib: object-oriented file paths; safer than string concat
        from IPython.display import Image, Markdown, display                                              # IPython display helpers: Image renders a PNG inline, Markdown renders text, display is the function that emits to the cell output

        from endpoint_ck_analysis.config import EXAMPLE_OUTPUT_DIR                                        # canonical location of saved figures (set in config.py)

        expected = [                                                                                       # list of (title, filename) pairs in display order; edit here to add/remove gallery items
            ('01 Connectivity PCA - Scree',                '01_scree.png'),
            ('01 Connectivity PCA - Loadings (all)',       '01_loadings_all.png'),
            ('01 Connectivity PCA - Loadings (important)', '01_loadings_important.png'),
            ('01 Connectivity PCA - Loadings heatmap',     '01_loadings_heatmap.png'),
            ('01 Connectivity cell counts heatmap',        '01_connectivity_heatmap.png'),
            ('02 Kinematics PCA - Loadings by phase',      '02_loadings_by_phase.png'),
            ('02 Kinematics PCA - Important features',     '02_loadings_important_features.png'),
            ('03 Feature dendrogram',                      '03_dendrogram.png'),
            ('03 Feature clusters in PC1-PC2',             '03_feature_clusters_2d.png'),
            ('04 PLS - Injury snapshot LV1',               '04_pls_injury_snapshot_LV1.png'),
            ('04 PLS - Injury snapshot LV2',               '04_pls_injury_snapshot_LV2.png'),
            ('04 PLS - Deficit delta LV1',                 '04_pls_deficit_delta_LV1.png'),
            ('04 PLS - Deficit delta LV2',                 '04_pls_deficit_delta_LV2.png'),
            ('04 PLS - Recovery delta LV1',                '04_pls_recovery_delta_LV1.png'),
            ('04 PLS - Recovery delta LV2',                '04_pls_recovery_delta_LV2.png'),
            ('05 LMM summary',                             '05_lmm_summary.png'),
            ('06 Pellet scoring confusion matrix',         '06_confusion_matrix.png'),
            ('06 Agreement by phase',                      '06_agreement_by_phase.png'),
            ('07 Trajectories colored by connectivity PC1','07_trajectories_continuous.png'),
            ('07 Trajectories by connectivity cluster',    '07_trajectories_by_cluster.png'),
            ('07 Cluster profile heatmap',                 '07_cluster_profile.png'),
            ('07 Permutation validation',                  '07_permutation_validation.png'),
            ('07 Kinematic-cluster alluvial',              '07_alluvial.png'),
            ('08 Grouped vs ungrouped PCA',                '08_grouped_vs_ungrouped.png'),
            ('08 Per-eLife-group drill-down',              '08_drill_down.png'),
            ('08 Prior-weighted vs unweighted PC1',        '08_prior_weighted.png'),
        ]

        for title, filename in expected:                                                                   # iterate the list; each iteration emits one section heading + one image (or a missing-file note)
            path = EXAMPLE_OUTPUT_DIR / filename                                                           # pathlib '/' operator joins directory and filename portably across OSes
            display(Markdown(f'### {title}'))                                                              # render section heading as H3 markdown so the gallery has a navigable structure
            if path.exists():                                                                              # only display if the PNG was actually written by an earlier notebook run
                display(Image(filename=str(path)))                                                         # render the PNG inline; str(path) because Image expects a string filename
            else:
                display(Markdown(f'*Not found: ``{path}``. Re-run the notebook that produces this figure.*'))  # italic note pointing the user back to the producing notebook
    """),
]


NOTEBOOKS = {
    "00_setup.ipynb": SETUP_NB,
    "01_connectivity_pca.ipynb": CONNECTIVITY_PCA_NB,
    "02_kinematic_pca.ipynb": KINEMATIC_PCA_NB,
    "03_kinematic_clustering.ipynb": CLUSTERING_NB,
    "04_pls_variants.ipynb": PLS_VARIANTS_NB,
    "05_lmm_phase_effects.ipynb": LMM_NB,
    "06_pellet_validation.ipynb": VALIDATION_NB,
    "07_connectivity_trajectory_linkage.ipynb": TRAJECTORY_NB,
    "08_hypothesis_informed_tests.ipynb": HYPOTHESIS_NB,
    "99_figure_gallery.ipynb": GALLERY_NB,
}


def main() -> int:
    NOTEBOOKS_DIR.mkdir(parents=True, exist_ok=True)
    for name, cells in NOTEBOOKS.items():
        nb = make_notebook(cells)
        out = NOTEBOOKS_DIR / name
        out.write_text(json.dumps(nb, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {out} ({len(cells)} cells)")

    # Trailing collapsible interpretation blocks are no longer appended.
    # Interpretations now live INLINE after each output-producing cell so
    # the reader can connect each result to its meaning at the moment it
    # appears, instead of scrolling to the bottom and reverse-mapping.
    # The legacy add_interpretation_sections.py script is kept around in
    # tools/ for reference / revert path but is intentionally not invoked.
    return 0


if __name__ == "__main__":
    sys.exit(main())
