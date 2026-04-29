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
        ## 3. Quick-look previews

        Spot-check that the data resembles what you expect before running the
        analytical notebooks. Uncomment any lines that would be useful to see.
    """),
    ("code", """
        # data.AKDdf.head()
        # data.FCDGdf_wide
        # data.AKDdf_agg_contact.head()
        pass
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
        ## 2. Per-phase scree + loading figures

        ``plot_pca_for_phase`` emits one scree plot and a loading-bars figure
        per phase. Saves PNGs for the gallery.
    """),
    ("code", """
        for phase, (pca, _, eigen, loadings, _) in pcas.items():     # iterate phase by phase
            plot_pca_for_phase(pca, eigen, loadings, phase)          # helper renders scree + loading bars; uses plt.show() so each phase appears as its own pair of inline figures
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
        import pandas as pd
        import matplotlib.pyplot as plt

        from endpoint_ck_analysis.config import CACHE_DIR, EXAMPLE_OUTPUT_DIR
        from endpoint_ck_analysis.data_loader import load_all
        from endpoint_ck_analysis.helpers.dimreduce import build_y_phase, build_y_shift, run_pls
        from endpoint_ck_analysis.helpers.plotting import plot_pls, stamp_version

        EXAMPLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        data = load_all()
        agg_flat = data.AKDdf_agg_contact_flat()

        # Pull the important regions / features written by notebooks 01 and 02.
        important_regions = pd.read_parquet(CACHE_DIR / 'important_regions.parquet')['region_hemi'].tolist()
        important_features = pd.read_parquet(CACHE_DIR / 'important_features.parquet')['feature'].tolist()

        X_block = data.FCDGdf_wide[important_regions].fillna(0)
    """),
    ("md", """
        ## 1. Build the three Y-blocks

        These share the same X-block (connectivity) so results compare directly.
    """),
    ("code", """
        Y_injury = build_y_phase(agg_flat, important_features, 'Post_Injury_2-4')
        Y_deficit = build_y_shift(agg_flat, important_features, 'Baseline', 'Post_Injury_2-4')
        Y_recovery = build_y_shift(agg_flat, important_features, 'Post_Injury_2-4', 'Post_Rehab_Test')

        Y_BLOCKS = {
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
        variants = list(Y_BLOCKS.keys()) if VARIANT == 'all' else [VARIANT]
        if VARIANT != 'all' and VARIANT not in Y_BLOCKS:
            raise ValueError(f"Unknown VARIANT {VARIANT!r}. Choose one of {list(Y_BLOCKS) + ['all']}.")

        results = {}
        for variant in variants:
            Y, label = Y_BLOCKS[variant]
            print(f'\\n=== {variant} ===')
            results[variant] = run_pls(X_block, Y, n_components=N_COMPONENTS, label=label)
            plot_pls(results[variant], top_n=TOP_N)
    """),
    ("md", """
        ## 3. Export latent-variable scores for the gallery

        Each variant's subject scores are written to the cache so the figure
        gallery can assemble a summary figure without re-fitting.
    """),
    ("code", """
        for variant, r in results.items():
            out = pd.DataFrame(r['X_scores'], index=r['subjects'],
                               columns=[f'LV{i+1}' for i in range(r['X_scores'].shape[1])])
            out.to_parquet(CACHE_DIR / f'pls_{variant}_X_scores.parquet')
            print(f'Wrote pls_{variant}_X_scores.parquet, N={len(out)}')
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
        import numpy as np
        import pandas as pd
        import matplotlib.pyplot as plt

        from endpoint_ck_analysis.config import ANALYZABLE_PHASES, EXAMPLE_OUTPUT_DIR, CACHE_DIR, FDR_ALPHA
        from endpoint_ck_analysis.data_loader import load_all
        from endpoint_ck_analysis.helpers.kinematics import get_kinematic_cols
        from endpoint_ck_analysis.helpers.models import run_phase_lmm_for_features
        from endpoint_ck_analysis.helpers.plotting import stamp_version

        EXAMPLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        data = load_all()
    """),
    ("md", """
        ## 1. Prepare the reach-level dataframe

        One row per contacted reach. Phase is an ordered Categorical with
        Baseline as the reference level, so every fitted coefficient reads as
        "phase X - Baseline".
    """),
    ("code", """
        contacted = data.AKDdf[data.AKDdf['contact_group'] == 'contacted'].copy()
        contacted['phase_group'] = pd.Categorical(
            contacted['phase_group'],
            categories=list(ANALYZABLE_PHASES),
            ordered=True,
        )

        features = FEATURE_LIST or get_kinematic_cols(contacted)
        print(f'Features to test: {len(features)}')
    """),
    ("md", """
        ## 2. Omnibus across all four phases
    """),
    ("code", """
        omnibus = run_phase_lmm_for_features(contacted, features, fdr_alpha=FDR_ALPHA)
        print(omnibus.head(15)[['feature', 'phase_p', 'phase_p_adj', 'n_reaches', 'n_subjects', 'converged']])
        omnibus.to_parquet(CACHE_DIR / 'lmm_omnibus.parquet', index=False)
    """),
    ("md", """
        ## 3. Deficit delta (Baseline vs Post_Injury_2-4)

        Same model structure but restricted to two phases, so the phase
        coefficient IS the delta.
    """),
    ("code", """
        deficit_df = contacted[contacted['phase_group'].isin(['Baseline', 'Post_Injury_2-4'])].copy()
        deficit_df['phase_group'] = pd.Categorical(deficit_df['phase_group'], categories=['Baseline', 'Post_Injury_2-4'])
        deficit = run_phase_lmm_for_features(deficit_df, features, fdr_alpha=FDR_ALPHA)
        print(deficit.head(15)[['feature', 'phase_p', 'phase_p_adj', 'n_reaches', 'n_subjects', 'converged']])
        deficit.to_parquet(CACHE_DIR / 'lmm_deficit.parquet', index=False)
    """),
    ("md", """
        ## 4. Recovery delta (Post_Injury_2-4 vs Post_Rehab_Test)
    """),
    ("code", """
        recovery_df = contacted[contacted['phase_group'].isin(['Post_Injury_2-4', 'Post_Rehab_Test'])].copy()
        recovery_df['phase_group'] = pd.Categorical(recovery_df['phase_group'], categories=['Post_Injury_2-4', 'Post_Rehab_Test'])
        recovery = run_phase_lmm_for_features(recovery_df, features, fdr_alpha=FDR_ALPHA)
        print(recovery.head(15)[['feature', 'phase_p', 'phase_p_adj', 'n_reaches', 'n_subjects', 'converged']])
        recovery.to_parquet(CACHE_DIR / 'lmm_recovery.parquet', index=False)
    """),
    ("md", """
        ## 5. -log10(adjusted p) summary

        Three stacked bar charts -- one per analysis -- of the top features by
        significance. Red dashed line marks the FDR cutoff.
    """),
    ("code", """
        combined = pd.concat([
            omnibus.head(TOP_N_FIGURE).assign(analysis='Omnibus (all phases)'),
            deficit.head(TOP_N_FIGURE).assign(analysis='Deficit (Baseline -> Post_Injury_2-4)'),
            recovery.head(TOP_N_FIGURE).assign(analysis='Recovery (Post_Injury_2-4 -> Post_Rehab_Test)'),
        ])
        combined['neg_log_p'] = -np.log10(combined['phase_p_adj'].clip(lower=1e-30))

        sig_threshold = -np.log10(FDR_ALPHA)
        fig, axes = plt.subplots(3, 1, figsize=FIGSIZE_BARS)
        for ax, (label, group) in zip(axes, combined.groupby('analysis', sort=False)):
            plot_df = group.dropna(subset=['neg_log_p']).sort_values('neg_log_p')
            colors = ['steelblue' if v >= sig_threshold else 'lightgray' for v in plot_df['neg_log_p']]
            ax.barh(plot_df['feature'], plot_df['neg_log_p'], color=colors)
            ax.axvline(sig_threshold, color='red', linestyle='--', linewidth=1, label=f'FDR q={FDR_ALPHA}')
            ax.set_xlabel('-log10(FDR-adjusted p)')
            ax.set_title(label)
            ax.legend(loc='lower right')
        plt.tight_layout()
        stamp_version(fig, label='05 LMM summary')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '05_lmm_summary.png', dpi=150, bbox_inches='tight')
        plt.show()
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
        import pandas as pd
        import matplotlib.pyplot as plt
        import seaborn as sns
        from sklearn.metrics import cohen_kappa_score, confusion_matrix

        from endpoint_ck_analysis.config import EXAMPLE_OUTPUT_DIR
        from endpoint_ck_analysis.data_loader import load_all
        from endpoint_ck_analysis.helpers.plotting import stamp_version

        EXAMPLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        data = load_all()
    """),
    ("md", """
        ## 1. Build the per-pellet validation dataframe

        Inner-join manual and algorithmic scores on shared per-pellet keys.
    """),
    ("code", """
        manual_pellet_pillar = data.manual_pelletdf[data.manual_pelletdf['tray_type'] == 'P']
        kinematics_pillar = data.kinematicsdf[data.kinematicsdf['tray_type'] == 'P']

        algo_per_segment = kinematics_pillar[
            ['subject_id', 'session_date', 'tray_type', 'run_number', 'segment_num', 'segment_outcome']
        ].drop_duplicates().rename(columns={'run_number': 'tray_number', 'segment_num': 'pellet_number'})

        validation = manual_pellet_pillar[
            ['subject_id', 'session_date', 'tray_type', 'tray_number', 'pellet_number', 'score']
        ].merge(
            algo_per_segment,
            on=['subject_id', 'session_date', 'tray_type', 'tray_number', 'pellet_number'],
            how='inner',
        )
        print(f'Matched {len(validation)} pillar pellets between manual and algorithmic scoring')

        manual_cat_map = {0: 'missed', 1: 'displaced', 2: 'retrieved'}
        algo_cat_map = {
            'untouched': 'missed', 'uncertain': 'missed',
            'displaced_sa': 'displaced', 'displaced_outside': 'displaced',
            'retrieved': 'retrieved',
        }
        validation['manual_cat'] = validation['score'].map(manual_cat_map)
        validation['algo_cat'] = validation['segment_outcome'].map(algo_cat_map)
        validation['manual_contacted'] = validation['manual_cat'] != 'missed'
        validation['algo_contacted'] = validation['algo_cat'] != 'missed'
    """),
    ("md", """
        ## 2. Summary statistics
    """),
    ("code", """
        three_way = (validation['manual_cat'] == validation['algo_cat']).mean()
        binary = (validation['manual_contacted'] == validation['algo_contacted']).mean()
        kappa = cohen_kappa_score(validation['manual_cat'], validation['algo_cat'])
        print(f'Three-way exact agreement:     {three_way:.3%}')
        print(f'Binary (contacted vs missed):  {binary:.3%}')
        print(f"Cohen's kappa (three-way):     {kappa:.3f}")
        print('Interpretation of kappa: <0.4 poor, 0.4-0.6 moderate, 0.6-0.8 substantial, >0.8 almost perfect')
        print('\\nConfusion matrix (rows=manual, cols=algorithmic):')
        print(pd.crosstab(validation['manual_cat'], validation['algo_cat'], margins=True))
    """),
    ("md", """
        ## 3. Confusion matrix heatmap (counts + row-normalized)
    """),
    ("code", """
        cats = ['missed', 'displaced', 'retrieved']
        cm = confusion_matrix(validation['manual_cat'], validation['algo_cat'], labels=cats)
        cm_norm = cm / cm.sum(axis=1, keepdims=True)

        fig, axes = plt.subplots(1, 2, figsize=FIGSIZE_CM)
        sns.heatmap(cm, annot=True, fmt='d', cmap='Blues', xticklabels=cats, yticklabels=cats,
                    ax=axes[0], cbar_kws={'label': 'count'})
        axes[0].set_xlabel('Algorithmic classification')
        axes[0].set_ylabel('Manual classification')
        axes[0].set_title('Pillar confusion matrix (raw counts)')
        sns.heatmap(cm_norm, annot=True, fmt='.2%', cmap='Blues', xticklabels=cats, yticklabels=cats,
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
        ## 4. Per-phase agreement

        Does the algorithm's accuracy drift across the experimental phases?
    """),
    ("code", """
        validation_with_phase = validation.merge(
            manual_pellet_pillar[['subject_id', 'session_date', 'tray_number', 'pellet_number', 'phase_group']],
            on=['subject_id', 'session_date', 'tray_number', 'pellet_number'],
            how='left',
        )
        per_phase = validation_with_phase.groupby('phase_group').apply(
            lambda g: pd.Series({
                'n': len(g),
                'three_way_agreement': (g['manual_cat'] == g['algo_cat']).mean(),
                'binary_agreement': (g['manual_contacted'] == g['algo_contacted']).mean(),
            })
        ).sort_values('n', ascending=False)
        print(per_phase)

        fig, ax = plt.subplots(figsize=FIGSIZE_PER_PHASE)
        x = range(len(per_phase))
        ax.bar([i - 0.2 for i in x], per_phase['three_way_agreement'], width=0.4,
               label='Three-way agreement', color='steelblue')
        ax.bar([i + 0.2 for i in x], per_phase['binary_agreement'], width=0.4,
               label='Binary agreement', color='orange')
        ax.set_xticks(list(x))
        ax.set_xticklabels(per_phase.index, rotation=45, ha='right')
        ax.set_ylabel('Agreement rate')
        ax.set_ylim(0, 1)
        ax.axhline(0.9, color='green', linestyle='--', linewidth=0.7, label='0.90 reference')
        ax.legend()
        ax.set_title('Manual vs algorithmic agreement by phase (pillar trays only)')
        for i, (phase, row) in enumerate(per_phase.iterrows()):
            ax.text(i, 0.02, f"N={int(row['n'])}", ha='center', fontsize=8)
        plt.tight_layout()
        stamp_version(fig, label='06 per phase')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '06_agreement_by_phase.png', dpi=150, bbox_inches='tight')
        plt.show()
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
        import numpy as np
        import pandas as pd
        import matplotlib.pyplot as plt
        from matplotlib.cm import get_cmap
        import seaborn as sns
        from sklearn.decomposition import PCA
        from sklearn.preprocessing import StandardScaler

        from endpoint_ck_analysis import SKILLED_REACHING, ordered_hemisphere_columns
        from endpoint_ck_analysis.config import CACHE_DIR, EXAMPLE_OUTPUT_DIR, ANALYZABLE_PHASES
        from endpoint_ck_analysis.data_loader import load_all
        from endpoint_ck_analysis.helpers.clusters import (
            cluster_subjects, profile_clusters, auto_name_clusters,
            permutation_validate, alluvial_source_records,
        )
        from endpoint_ck_analysis.helpers.plotting import stamp_version

        EXAMPLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        data = load_all(
            use_synthetic=USE_SYNTHETIC,
            synthetic_n=SYNTHETIC_N,
            synthetic_seed=SYNTHETIC_SEED,
            synthetic_conn_noise=SYNTHETIC_CONN_NOISE,
            synthetic_kine_noise=SYNTHETIC_KINE_NOISE,
            use_cache=not USE_SYNTHETIC,     # synthetic always rebuilds
            write_cache=not USE_SYNTHETIC,
            verbose=False,
        )
        print(f"Running on {'SYNTHETIC' if USE_SYNTHETIC else 'REAL'} data.  "
              f"N={len(data.matched_subjects)} subjects.")
    """),
    ("md", """
        ## 1. Compute connectivity coordinates

        Re-fit PCA on the full connectivity matrix (same matrix as notebook
        01). Each subject ends up with PC1/PC2/PC3 scores that serve as a
        continuous coordinate for coloring trajectories.
    """),
    ("code", """
        X_conn = data.FCDGdf_wide.fillna(0)
        canonical_cols = ordered_hemisphere_columns(SKILLED_REACHING, available=X_conn.columns.tolist())
        X_conn = X_conn[canonical_cols]

        X_scaled = StandardScaler().fit_transform(X_conn)
        conn_pca = PCA(n_components=min(N_CONN_PCS, len(X_conn) - 1))
        conn_scores = conn_pca.fit_transform(X_scaled)
        conn_scores_df = pd.DataFrame(
            conn_scores,
            index=X_conn.index,
            columns=[f'PC{i+1}' for i in range(conn_scores.shape[1])],
        )
        conn_scores_df.index.name = 'subject_id'
        print('Per-subject connectivity PC scores:')
        print(conn_scores_df)

        # Cache these so downstream notebooks can also colour by them
        conn_scores_df.to_parquet(CACHE_DIR / 'connectivity_pc_scores.parquet')
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
        cluster_result = cluster_subjects(
            X_conn, method=CLUSTER_METHOD, k=N_CLUSTERS, random_state=SYNTHETIC_SEED,
        )
        cluster_by_subject = cluster_result.labels.rename('conn_cluster').astype(int)
        print(f'Method: {cluster_result.method}, k={cluster_result.k}')
        print('\\nCluster assignments:')
        print(cluster_by_subject)
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
        profile = profile_clusters(X_conn, cluster_by_subject)
        auto_names = auto_name_clusters(profile, top_n=PROFILE_TOP_N, threshold=PROFILE_THRESHOLD)
        names = dict(auto_names)
        names.update(MANUAL_CLUSTER_NAMES)
        print('Cluster names:')
        for cid in sorted(names):
            print(f'  {names[cid]}')

        # Heatmap: clusters (rows) x top defining regions (cols)
        top_cols = (profile.abs().max(axis=0).sort_values(ascending=False).head(20).index.tolist())
        fig, ax = plt.subplots(figsize=FIGSIZE_PROFILE)
        sns.heatmap(profile[top_cols].rename(index=names), cmap='RdBu_r', center=0,
                    annot=True, fmt='.1f', ax=ax, cbar_kws={'label': 'z-score'})
        ax.set_title('Cluster profile: top 20 discriminating regions')
        plt.tight_layout()
        stamp_version(fig, label='07 cluster profile')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '07_cluster_profile.png', dpi=150, bbox_inches='tight')
        plt.show()
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
        pv = permutation_validate(X_conn, cluster_by_subject,
                                  n_random=N_PERMUTATIONS, random_state=SYNTHETIC_SEED)
        print(f'Observed within-cluster variance: {pv[\"observed\"]:.2f}')
        print(f'LOO range:                        [{min(pv[\"loo\"]):.2f}, {max(pv[\"loo\"]):.2f}]')
        print(f'Random null mean:                 {float(np.mean(pv[\"random\"])):.2f}')
        print(f'p (random <= observed):           {pv[\"p_random\"]:.3f}')

        fig, ax = plt.subplots(figsize=FIGSIZE_PERMUTATION)
        ax.hist(pv['random'], bins=50, alpha=0.7, label='Random-label null', color='grey')
        ax.axvline(pv['observed'], color='red', linewidth=2, label=f'Observed ({pv[\"observed\"]:.1f})')
        for v in pv['loo']:
            ax.axvline(v, color='blue', alpha=0.3, linewidth=0.8)
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
        agg_flat = data.AKDdf_agg_contact_flat()
        kine_feature_cols = [c for c in agg_flat.columns if c.endswith(f'_{AGG_STAT}')]

        per_phase_labels = {}
        for phase in PHASES:
            phase_slice = agg_flat[
                (agg_flat['phase_group'] == phase) & (agg_flat['contact_group'] == 'contacted')
            ]
            if phase_slice.empty:
                continue
            mat = (
                phase_slice.set_index('subject_id')[kine_feature_cols]
                .fillna(0)
            )
            try:
                k_phase = min(N_CLUSTERS, len(mat) - 1) if len(mat) > 1 else 1
                cr = cluster_subjects(mat, method=CLUSTER_METHOD, k=max(k_phase, 2), random_state=SYNTHETIC_SEED)
                per_phase_labels[phase] = cr.labels
            except Exception as e:
                print(f'Phase {phase}: clustering failed ({e})')

        sankey_df = alluvial_source_records(per_phase_labels, PHASES)
        print(f'Sankey edges: {len(sankey_df)} between {len(per_phase_labels)} phases.')
    """),
    ("code", """
        import plotly.graph_objects as go

        if len(sankey_df) == 0:
            print('Not enough per-phase clustering data to draw the Sankey.')
        else:
            nodes = pd.unique(pd.concat([sankey_df['source'], sankey_df['target']]))
            node_index = {n: i for i, n in enumerate(nodes)}
            fig_sankey = go.Figure(data=[go.Sankey(
                node=dict(label=list(nodes), pad=12, thickness=14),
                link=dict(
                    source=sankey_df['source'].map(node_index).tolist(),
                    target=sankey_df['target'].map(node_index).tolist(),
                    value=sankey_df['value'].tolist(),
                ),
            )])
            fig_sankey.update_layout(
                title_text='Kinematic-cluster flow across phases',
                width=FIGSIZE_ALLUVIAL[0], height=FIGSIZE_ALLUVIAL[1],
            )
            try:
                fig_sankey.write_image(str(EXAMPLE_OUTPUT_DIR / '07_alluvial.png'))
            except Exception as e:
                print(f'(Could not save Sankey PNG -- kaleido missing or failed: {e})')
            fig_sankey.show()
    """),
    ("md", """
        ## 6. Build the per-subject per-phase trajectory table

        Pull ``{FEATURE}_{AGG_STAT}`` from ``data.AKDdf_agg_contact`` for the
        contacted reaches, restricted to the analyzable phase set. One row
        per subject per phase.
    """),
    ("code", """
        agg_flat = data.AKDdf_agg_contact_flat()
        feature_col = f'{TRAJECTORY_FEATURE}_{AGG_STAT}'
        if feature_col not in agg_flat.columns:
            raise KeyError(
                f'{feature_col!r} not in AKDdf_agg_contact columns. '
                f'Set TRAJECTORY_FEATURE / AGG_STAT to a valid combination. '
                f'First few available: {[c for c in agg_flat.columns if c.endswith("_" + AGG_STAT)][:10]}'
            )

        traj = agg_flat[
            (agg_flat['contact_group'] == 'contacted')
            & (agg_flat['phase_group'].isin(PHASES))
        ][['subject_id', 'phase_group', feature_col]].copy()
        traj['phase_order'] = traj['phase_group'].apply(lambda p: PHASES.index(p) if p in PHASES else -1)

        # Attach connectivity PC1 score and cluster to each row, drop subjects without connectivity
        traj = traj.merge(conn_scores_df[['PC1']].reset_index(), on='subject_id', how='left')
        traj = traj.merge(cluster_by_subject.to_frame().reset_index(), on='subject_id', how='left')
        traj = traj.dropna(subset=['conn_cluster']).copy()
        traj['conn_cluster'] = traj['conn_cluster'].astype(int)
        print(traj.sort_values(['subject_id', 'phase_order']).head(20))
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
        fig, ax = plt.subplots(figsize=FIGSIZE_TRAJ)
        pc1_vals = conn_scores_df['PC1']
        vmin, vmax = pc1_vals.min(), pc1_vals.max()
        cmap = get_cmap('viridis')
        for subj, grp in traj.groupby('subject_id'):
            grp = grp.sort_values('phase_order')
            color = cmap((grp['PC1'].iloc[0] - vmin) / (vmax - vmin + 1e-9))
            ax.plot(grp['phase_order'], grp[feature_col], '-o', color=color, label=subj, linewidth=2)
            ax.annotate(subj, (grp['phase_order'].iloc[-1], grp[feature_col].iloc[-1]),
                        fontsize=8, xytext=(4, 0), textcoords='offset points')
        ax.set_xticks(range(len(PHASES)))
        ax.set_xticklabels(PHASES, rotation=20, ha='right')
        ax.set_ylabel(feature_col)
        ax.set_xlabel('Phase')
        ax.set_title(f'Trajectory of {feature_col} colored by connectivity PC1')
        sm = plt.cm.ScalarMappable(cmap=cmap,
                                   norm=plt.Normalize(vmin=vmin, vmax=vmax))
        plt.colorbar(sm, ax=ax, label='Connectivity PC1 score')
        plt.tight_layout()
        stamp_version(fig, label='07 continuous')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '07_trajectories_continuous.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        ## 8. Grouped view: trajectories by named connectivity cluster

        Same trajectories, colored by cluster membership and labeled with
        the auto-generated or manual names from section 3.
    """),
    ("code", """
        fig, ax = plt.subplots(figsize=FIGSIZE_TRAJ)
        palette = get_cmap('tab10')
        already_labeled = set()
        for cluster_id in sorted(traj['conn_cluster'].unique()):
            grp = traj[traj['conn_cluster'] == cluster_id].sort_values(['subject_id', 'phase_order'])
            color = palette((int(cluster_id) - 1) % 10)
            cluster_name = names.get(int(cluster_id), f'cluster{cluster_id}')
            for subj, sub in grp.groupby('subject_id'):
                label = cluster_name if cluster_name not in already_labeled else None
                ax.plot(sub['phase_order'], sub[feature_col], '-o', color=color, alpha=0.85,
                        label=label, linewidth=2)
                already_labeled.add(cluster_name)
        ax.set_xticks(range(len(PHASES)))
        ax.set_xticklabels(PHASES, rotation=20, ha='right')
        ax.set_ylabel(feature_col)
        ax.set_xlabel('Phase')
        ax.set_title(f'Trajectory of {feature_col} by connectivity cluster (k={N_CLUSTERS})')
        ax.legend(loc='best', fontsize=7)
        plt.tight_layout()
        stamp_version(fig, label='07 grouped')
        plt.savefig(EXAMPLE_OUTPUT_DIR / '07_trajectories_by_cluster.png', dpi=150, bbox_inches='tight')
        plt.show()
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
        import warnings
        if RUN_INTERACTION_LMM and traj['conn_cluster'].nunique() > 1:
            from statsmodels.formula.api import mixedlm

            reach_level = data.AKDdf[data.AKDdf['contact_group'] == 'contacted'].copy()
            reach_level['phase_group'] = pd.Categorical(
                reach_level['phase_group'], categories=PHASES, ordered=True
            )
            # Attach connectivity cluster ID by subject_id
            reach_level = reach_level.merge(cluster_by_subject.to_frame().reset_index(),
                                            on='subject_id', how='left')
            # Drop subjects without a cluster assignment (no connectivity data)
            subset = reach_level.dropna(subset=['conn_cluster', TRAJECTORY_FEATURE])
            subset['conn_cluster'] = subset['conn_cluster'].astype(int).astype(str)
            if subset['subject_id'].nunique() >= 2 and subset['conn_cluster'].nunique() >= 2:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore')
                        model = mixedlm(
                            formula=f"Q('{TRAJECTORY_FEATURE}') ~ C(phase_group) * C(conn_cluster)",
                            data=subset,
                            groups='subject_id',
                            vc_formula={'session': '0 + C(session_date)'},
                        )
                        result = model.fit(reml=True, method='lbfgs', disp=False)
                    wald = result.wald_test_terms().table
                    print(wald)
                    print('\\nInteraction p-value for phase x conn_cluster:')
                    interaction_rows = [i for i in wald.index if 'phase_group' in i and 'conn_cluster' in i]
                    for i in interaction_rows:
                        print(f'  {i}: {wald.loc[i, "P>chi2"]:.4f}')
                except Exception as e:
                    print(f'Interaction LMM failed to fit (expected at very small N): {e}')
            else:
                print('Too few subjects or clusters to fit the interaction LMM.')
        else:
            print('Skipping interaction LMM (RUN_INTERACTION_LMM=False or only one cluster present).')
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
        import numpy as np
        import pandas as pd
        import matplotlib.pyplot as plt
        import seaborn as sns
        from sklearn.preprocessing import StandardScaler
        from sklearn.decomposition import PCA

        from endpoint_ck_analysis import SKILLED_REACHING, ordered_hemisphere_columns
        from endpoint_ck_analysis.config import CACHE_DIR, EXAMPLE_OUTPUT_DIR, ANALYZABLE_PHASES
        from endpoint_ck_analysis.data_loader import load_all
        from endpoint_ck_analysis.helpers.hierarchical import (
            build_group_region_map, drill_down_pca, grouped_vs_ungrouped_summary,
        )
        from endpoint_ck_analysis.helpers.dimreduce import (
            priority_weights_from_prior, apply_feature_weights,
        )
        from endpoint_ck_analysis.helpers.models import compare_nested_lmms
        from endpoint_ck_analysis.helpers.plotting import stamp_version

        EXAMPLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        data = load_all(
            use_synthetic=USE_SYNTHETIC,
            synthetic_n=SYNTHETIC_N,
            synthetic_seed=SYNTHETIC_SEED,
            use_cache=not USE_SYNTHETIC,
            write_cache=not USE_SYNTHETIC,
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
        summary = grouped_vs_ungrouped_summary(
            data.ACDGdf_wide.fillna(0), data.ACDUdf_wide.fillna(0), n_components=5,
        )
        print(summary)

        fig, ax = plt.subplots(figsize=FIGSIZE_VAR)
        for level, grp in summary.groupby('level'):
            ax.plot(grp['component'], grp['variance_explained'], marker='o', label=level)
        ax.set_ylabel('Variance explained')
        ax.set_xlabel('Component')
        ax.set_title('Connectivity PCA: grouped (eLife) vs ungrouped (atomic regions)')
        ax.legend()
        ax.grid(alpha=0.3)
        stamp_version(fig, label='08 grouped vs ungrouped')
        plt.tight_layout()
        plt.savefig(EXAMPLE_OUTPUT_DIR / '08_grouped_vs_ungrouped.png', dpi=150, bbox_inches='tight')
        plt.show()
    """),
    ("md", """
        ## 2. Per-group drill-down

        For each eLife group, run a mini-PCA on its atomic-region
        columns. A group whose PC1 captures most of its variance is a
        coherent bundle; a group where variance is spread across many
        PCs has subregional heterogeneity the grouping is hiding.
    """),
    ("code", """
        group_region_map = build_group_region_map(data.counts_groupeddf)
        drill_rows = []
        for group in data.FCDGdf_wide.columns.map(lambda c: c.rsplit('_', 1)[0]).unique():
            result = drill_down_pca(data.ACDUdf_wide.fillna(0), group, group_region_map, n_components=3)
            if result is None:
                continue
            drill_rows.append({
                'group': group,
                'n_atomic': result.n_atomic_regions,
                'PC1_var': float(result.explained_variance_ratio[0]) if len(result.explained_variance_ratio) else np.nan,
                'PC2_var': float(result.explained_variance_ratio[1]) if len(result.explained_variance_ratio) > 1 else np.nan,
                'PC3_var': float(result.explained_variance_ratio[2]) if len(result.explained_variance_ratio) > 2 else np.nan,
            })

        drill_df = pd.DataFrame(drill_rows).sort_values('n_atomic', ascending=False)
        print(drill_df.to_string(index=False))

        # Plot: stacked bar of PC1/PC2/PC3 variance per group
        fig, ax = plt.subplots(figsize=FIGSIZE_DRILL)
        x = range(len(drill_df))
        bottom = np.zeros(len(drill_df))
        for pc_col, color, label in [('PC1_var', 'steelblue', 'PC1'),
                                     ('PC2_var', 'coral', 'PC2'),
                                     ('PC3_var', 'seagreen', 'PC3')]:
            vals = drill_df[pc_col].fillna(0).values
            ax.bar(x, vals, bottom=bottom, color=color, label=label)
            bottom = bottom + vals
        ax.set_xticks(list(x))
        ax.set_xticklabels(drill_df['group'], rotation=60, ha='right', fontsize=8)
        ax.set_ylabel('Variance explained within group')
        ax.set_title('Per-eLife-group drill-down: variance across top 3 within-group PCs')
        ax.axhline(0.9, color='black', linestyle='--', linewidth=0.6, alpha=0.5)
        ax.legend()
        stamp_version(fig, label='08 drill-down')
        plt.tight_layout()
        plt.savefig(EXAMPLE_OUTPUT_DIR / '08_drill_down.png', dpi=150, bbox_inches='tight')
        plt.show()
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
        reach_level = data.AKDdf[data.AKDdf['contact_group'] == 'contacted'].copy()
        reach_level['phase_group'] = pd.Categorical(
            reach_level['phase_group'], categories=list(ANALYZABLE_PHASES), ordered=True,
        )

        # Attach top-K prior-ranked connectivity region values to every reach
        # by merging on subject_id from the wide connectivity matrix.
        canonical_cols = ordered_hemisphere_columns(
            SKILLED_REACHING, available=data.FCDGdf_wide.columns.tolist(),
        )
        top_cols = [c for c in canonical_cols if c.endswith('_both')][:TOP_K_PRIORS]
        conn_wide = data.FCDGdf_wide[top_cols].fillna(0)
        # Rename to safe python identifiers for patsy formula
        safe_names = {c: f"conn_{i}" for i, c in enumerate(top_cols)}
        conn_wide = conn_wide.rename(columns=safe_names).reset_index()
        reach_level = reach_level.merge(conn_wide, on='subject_id', how='left')

        rhs_parts = list(safe_names.values())
        model_specs = [
            ('baseline (phase only)', ''),
            (f'+top{TOP_K_PRIORS}_priors', ' + '.join(rhs_parts)),
        ]

        nested_results = compare_nested_lmms(
            reach_level.dropna(subset=rhs_parts + [TARGET_FEATURE]),
            target_feature=TARGET_FEATURE,
            model_specs=model_specs,
            groups='subject_id',
            vc_formula={'session': '0 + C(session_date)'},
        )
        print(nested_results.to_string(index=False))
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
        X_conn = data.FCDGdf_wide.fillna(0)
        X_ordered = X_conn[canonical_cols]
        X_scaled = pd.DataFrame(
            StandardScaler().fit_transform(X_ordered),
            columns=X_ordered.columns, index=X_ordered.index,
        )

        weights = priority_weights_from_prior(SKILLED_REACHING, X_ordered.columns, decay=PRIOR_DECAY)
        X_weighted = apply_feature_weights(X_scaled, weights)

        pca_raw = PCA(n_components=min(3, len(X_ordered) - 1)).fit(X_scaled.values)
        pca_weighted = PCA(n_components=min(3, len(X_ordered) - 1)).fit(X_weighted.values)

        loadings_raw = pd.Series(pca_raw.components_[0], index=X_scaled.columns)
        loadings_weighted = pd.Series(pca_weighted.components_[0], index=X_scaled.columns)

        top_raw = loadings_raw.abs().nlargest(10).index
        top_weighted = loadings_weighted.abs().nlargest(10).index

        print('Top 10 regions on PC1 (unweighted):')
        print(loadings_raw.loc[top_raw].sort_values())
        print()
        print('Top 10 regions on PC1 (prior-weighted):')
        print(loadings_weighted.loc[top_weighted].sort_values())

        # Side-by-side horizontal bar chart of top 15 regions by |loading|, both versions
        union = list(pd.Index(top_raw).union(pd.Index(top_weighted))[:15])
        comp = pd.DataFrame({
            'unweighted': loadings_raw.loc[union],
            'prior-weighted': loadings_weighted.loc[union],
        }).reindex(union)
        fig, ax = plt.subplots(figsize=FIGSIZE_WEIGHTED)
        comp.plot(kind='barh', ax=ax, width=0.8)
        ax.axvline(0, color='black', linewidth=0.5)
        ax.set_xlabel('PC1 loading')
        ax.set_title(f'PC1 loadings: unweighted vs prior-weighted (decay={PRIOR_DECAY})')
        ax.invert_yaxis()
        stamp_version(fig, label='08 prior weighted')
        plt.tight_layout()
        plt.savefig(EXAMPLE_OUTPUT_DIR / '08_prior_weighted.png', dpi=150, bbox_inches='tight')
        plt.show()
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
        from pathlib import Path
        from IPython.display import Image, Markdown, display

        from endpoint_ck_analysis.config import EXAMPLE_OUTPUT_DIR

        expected = [
            ('01 Connectivity PCA - Scree',                '01_scree.png'),
            ('01 Connectivity PCA - Loadings (all)',       '01_loadings_all.png'),
            ('01 Connectivity PCA - Loadings (important)', '01_loadings_important.png'),
            ('01 Connectivity PCA - Loadings heatmap',     '01_loadings_heatmap.png'),
            ('01 Connectivity cell counts heatmap',        '01_connectivity_heatmap.png'),
            ('02 Kinematics PCA - Loadings by phase',      '02_loadings_by_phase.png'),
            ('02 Kinematics PCA - Important features',     '02_loadings_important_features.png'),
            ('03 Feature dendrogram',                      '03_dendrogram.png'),
            ('03 Feature clusters in PC1-PC2',             '03_feature_clusters_2d.png'),
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

        for title, filename in expected:
            path = EXAMPLE_OUTPUT_DIR / filename
            display(Markdown(f'### {title}'))
            if path.exists():
                display(Image(filename=str(path)))
            else:
                display(Markdown(f'*Not found: ``{path}``. Re-run the notebook that produces this figure.*'))
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

    # Append the "How to read this notebook's output" interpretation cells.
    # Kept in a separate script (and a separate dict of long markdown blobs)
    # so this builder file doesn't double in size. Idempotent -- safe to run
    # repeatedly; appends only when the marker isn't already present.
    interp_script = HERE / "add_interpretation_sections.py"
    if interp_script.exists():
        import subprocess
        result = subprocess.run(
            [sys.executable, str(interp_script), str(NOTEBOOKS_DIR)],
            capture_output=True, text=True,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
    else:
        print(f"WARN: {interp_script} missing; interpretation sections not appended.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
