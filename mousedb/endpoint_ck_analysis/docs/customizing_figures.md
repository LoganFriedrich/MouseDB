# Customizing figures

Each notebook has a **parameters cell** near the top. Every value you
might want to change for a one-off render is there. You do not need to
edit the helper modules or anywhere else -- just tweak the parameter
cell and re-run the notebook.

---

## What lives in each parameters cell

### `01_connectivity_pca.ipynb`

| Parameter | What it controls |
|-----------|------------------|
| `N_COMPONENTS` | How many PCs to extract. Capped at N-1 by PCA math. |
| `TOP_N_REGIONS` | Regions to flag as "important" per PC. Union across PCs feeds the filtered plots. |
| `FIGSIZE_SCREE` | (width, height) in inches for the scree plot. |
| `FIGSIZE_LOADINGS` | Size of the full per-PC loading bars figure. |
| `FIGSIZE_LOADINGS_FILTERED` | Size of the filtered-regions version. |
| `FIGSIZE_HEATMAP` | Size of the region x PC loadings heatmap. |
| `FIGSIZE_SUBJECT_REGION` | Size of the subject x region cell-count heatmap. |

### `02_kinematic_pca.ipynb`

| Parameter | What it controls |
|-----------|------------------|
| `PHASES` | Which phases to run the per-phase PCAs on. Default is the four analyzable phases. |
| `N_COMPONENTS` | Components per phase. |
| `TOP_N_FEATURES` | Features flagged as important per PC (mean across phases). |
| `FIGSIZE_HEATMAP` | Size of the cross-phase loadings heatmap. |

### `03_kinematic_clustering.ipynb`

| Parameter | What it controls |
|-----------|------------------|
| `N_CLUSTERS` | How many groups the dendrogram is cut into. Tune to the visual break point. |
| `FIGSIZE_DENDRO` | Dendrogram size. |
| `FIGSIZE_2D` | Size of the feature-cluster scatter in PC1-PC2 space. |

### `04_pls_variants.ipynb`

| Parameter | What it controls |
|-----------|------------------|
| `VARIANT` | Which PLS question to run: `'injury_snapshot'`, `'deficit_delta'`, `'recovery_delta'`, or `'all'`. |
| `N_COMPONENTS` | Latent variables per fit (capped at N-1, X.shape[1], Y.shape[1]). |
| `TOP_N` | Top connectivity regions to label per LV. |

### `05_lmm_phase_effects.ipynb`

| Parameter | What it controls |
|-----------|------------------|
| `FEATURE_LIST` | Override the default kinematic feature list. Set to a list of column names if you want to run on a shortlist instead of all kinematic features. |
| `TOP_N_FIGURE` | How many features to show on the -log10(p) summary bar chart. |
| `FIGSIZE_BARS` | Figure size for the summary. |

### `06_pellet_validation.ipynb`

| Parameter | What it controls |
|-----------|------------------|
| `FIGSIZE_CM` | Confusion matrix figure size. |
| `FIGSIZE_PER_PHASE` | Per-phase agreement bar chart size. |

### `07_connectivity_trajectory_linkage.ipynb`

| Parameter | What it controls |
|-----------|------------------|
| `USE_SYNTHETIC` | If True, swap real data for a cloned-and-perturbed synthetic cohort. Used to validate the pipeline at realistic N while real data is N=4. |
| `SYNTHETIC_N` | Number of synthetic subjects when `USE_SYNTHETIC=True`. Default 30. |
| `SYNTHETIC_SEED` | RNG seed for reproducibility of the synthetic cohort. |
| `SYNTHETIC_CONN_NOISE` | Connectivity perturbation as fraction of cross-subject std. Lower = tighter clones. |
| `SYNTHETIC_KINE_NOISE` | Kinematic perturbation as fraction of per-feature std. |
| `CLUSTER_METHOD` | `'ward'`, `'kmeans'`, `'gmm'`, or `'consensus'`. All produce 1..K labels. |
| `N_CLUSTERS` | Target cluster count. At real N=4 this defaults to 4 (one per subject). |
| `N_CONN_PCS` | Number of connectivity PCs extracted for coordinate axes. |
| `PROFILE_TOP_N` | Number of regions mentioned in each auto-generated cluster name. |
| `PROFILE_THRESHOLD` | Minimum absolute z-score for a region to be considered distinctive. |
| `MANUAL_CLUSTER_NAMES` | Dict `{cluster_id: 'biological name'}` to override auto labels. |
| `N_PERMUTATIONS` | Random-shuffle draws for the cluster-validity null. |
| `TRAJECTORY_FEATURE` | Kinematic feature tracked across phases. Combined with `AGG_STAT` to name the column (e.g. `max_extent_mm_mean`). |
| `AGG_STAT` | Which summary statistic suffix to use (`_mean`, `_std`, `_median`, `_q25`, `_q75`). |
| `PHASES` | Phase order for the trajectory axis. |
| `RUN_INTERACTION_LMM` | If False, skip the `phase * cluster` mixed-model fit. |
| `FIGSIZE_TRAJ` / `FIGSIZE_PROFILE` / `FIGSIZE_PERMUTATION` / `FIGSIZE_ALLUVIAL` | Per-figure dimensions. |

### `08_hypothesis_informed_tests.ipynb`

| Parameter | What it controls |
|-----------|------------------|
| `USE_SYNTHETIC` / `SYNTHETIC_N` / `SYNTHETIC_SEED` | Same meaning as in notebook 07; at N=4 the nested LMM section is underdetermined, so synthetic mode is the right test bench. |
| `TOP_K_PRIORS` | How many top-priority connectivity regions (by `SKILLED_REACHING` order, both-hemisphere columns) to add as covariates in the nested LMM. |
| `TARGET_FEATURE` | Kinematic column used as the dependent variable in the nested LMM. |
| `PRIOR_DECAY` | Exponential-decay rate for `priority_weights_from_prior`. Larger = more aggressive weighting toward high-priority regions. `0` = uniform (no weighting). |
| `FIGSIZE_VAR` / `FIGSIZE_DRILL` / `FIGSIZE_WEIGHTED` | Per-figure dimensions. |

---

## Common tweaks beyond the parameters cell

Most of these require editing one line in the notebook (not in the
helpers).

- **Change a color palette**. The 2D cluster scatter uses `cmap='tab10'`
  in notebook 03 and `px.colors.qualitative.T10` for the 3D plot.
  Matching these keeps the two views visually consistent.
- **Change a plot title**. Every `plt.title(...)` or `ax.set_title(...)`
  call is in the notebook, not the helpers. Edit in place.
- **Save in a different format**. The notebooks save PNGs via
  `plt.savefig(...)`. Change the file extension to `.pdf` or `.svg` for
  vector output.
- **Increase DPI for publication**. The default is `dpi=150`. Bump to
  `dpi=300` or higher and pass `bbox_inches='tight'` to trim whitespace.

---

## Changing analytical behavior (not just aesthetics)

If you need to change what the analysis actually computes:

- **Different phase reference**. Edit `ANALYZABLE_PHASES` in
  `endpoint_ck_analysis/config.py`. Affects every notebook that filters on
  phase.
- **Different excluded cohorts**. Edit `COHORTS_TO_EXCLUDE` in
  `config.py`.
- **Different FDR alpha**. Edit `FDR_ALPHA` in `config.py`.
- **Different region prior**. Edit the `FALLBACK_SKILLED_REACHING` block
  in `config.py`, or update the live `mousedb.region_priors` module and
  re-run `tools/sync_region_priors.py` to refresh the snapshot.
- **New kinematic aggregation statistic**. Edit `_agg_dict_for` in
  `endpoint_ck_analysis/helpers/kinematics.py`. Currently computes
  mean/std/median/q25/q75.

These are analytical choices, not aesthetic ones -- if you change any of
them, bump the version in `endpoint_ck_analysis/__init__.py` and add a line to
`CHANGELOG.md` so anyone looking at saved figures can tell which code
produced them (every figure footer carries the version).
