# CFS Analysis

A self-contained tool that reproduces the kinematics-vs-connectivity analysis
originally developed in
[`analyze_kinematics_v_connectomics.ipynb`](../../../..)
on spinal cord injury (SCI) recovery data.

The tool packages the analytical pipeline -- data loading, PCA, PLS, linear
mixed models, and pellet-scoring validation -- so that someone who isn't
the original author can run the analyses, inspect the figures, and tweak
them without opening the monolithic development notebook.

---

## Who this is for

- **Reviewers / graders**: you were given this folder and want to see the
  analysis run end-to-end with its figures. Open
  [`QUICKSTART.md`](QUICKSTART.md) and follow the numbered steps.
- **Lab members who want to adapt a figure**: open the notebook that
  produces the figure (e.g. `notebooks/04_pls_variants.ipynb`), edit the
  parameters cell at the top, re-run.
- **Developers integrating into mousedb**: this lives at
  `mousedb/cfs_analysis/` and is importable as the `cfs_analysis` Python
  package when installed.

---

## What the tool produces

Running the notebooks in order generates every figure from the analysis:

| Notebook | Figures |
|----------|---------|
| [`00_setup`](notebooks/00_setup.ipynb) | Environment health check; loads data; writes parquet cache |
| [`01_connectivity_pca`](notebooks/01_connectivity_pca.ipynb) | Scree plot, loading bars, subject x region heatmap |
| [`02_kinematic_pca`](notebooks/02_kinematic_pca.ipynb) | Per-phase PCAs with sign-aligned loadings heatmap |
| [`03_kinematic_clustering`](notebooks/03_kinematic_clustering.ipynb) | Feature dendrogram, 2D PC cluster plot, 3D interactive |
| [`04_pls_variants`](notebooks/04_pls_variants.ipynb) | PLS for injury snapshot / deficit delta / recovery delta |
| [`05_lmm_phase_effects`](notebooks/05_lmm_phase_effects.ipynb) | Per-feature LMM p-values with FDR correction |
| [`06_pellet_validation`](notebooks/06_pellet_validation.ipynb) | Manual vs algorithmic confusion matrix |
| [`07_connectivity_trajectory_linkage`](notebooks/07_connectivity_trajectory_linkage.ipynb) | End-to-end pipeline: cluster subjects on connectivity, profile + auto-name each cluster, permutation-validate, alluvial flow of per-phase kinematic clustering, trajectories colored by connectivity PC1 and by named cluster, interaction LMM. Supports ``USE_SYNTHETIC=True`` for pipeline validation at realistic N. |
| [`08_hypothesis_informed_tests`](notebooks/08_hypothesis_informed_tests.ipynb) | Hypothesis-informed alternative analyses: grouped-vs-ungrouped PCA, per-eLife-group drill-down, nested LMM comparison (phase vs phase+priors), prior-weighted PCA. Tests whether the eLife grouping and the region prior throw away structure. |
| [`99_figure_gallery`](notebooks/99_figure_gallery.ipynb) | All saved PNGs in one place |

Pre-rendered versions of every figure are committed under
[`example_output/`](example_output/) for visual comparison against a fresh
run.

---

## Quickstart (five steps)

See [`QUICKSTART.md`](QUICKSTART.md) for the annotated version.

1. Install Python 3.11 or newer from <https://www.python.org/downloads/>.
2. Double-click `install.bat` (Windows) or run `./install.sh` (macOS/Linux).
3. Double-click `run_analysis.bat` / `run_analysis.command` to open JupyterLab.
4. Open `notebooks/00_setup.ipynb` and run every cell.
5. Open `notebooks/01_connectivity_pca.ipynb` through `99_figure_gallery.ipynb`
   in order and run each one.

Troubleshooting: see [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

---

## Folder layout

```
cfs_analysis/
  README.md               <- you are here
  QUICKSTART.md           <- non-technical step-by-step
  TROUBLESHOOTING.md      <- fixes for the most common errors
  CHANGELOG.md            <- version history
  pyproject.toml          <- declares dependencies
  install.bat / .sh       <- run once to set up the Python environment
  run_analysis.bat / .command  <- opens JupyterLab in notebooks/
  cfs_analysis/           <- the importable Python package
    __init__.py           <- soft-imports mousedb.region_priors, falls back if absent
    config.py             <- paths, analyzable phases, frozen region priors
    data_loader.py        <- SQL + aggregation + pivoting; returns LoadedData
    doctor.py             <- environment health check
    helpers/              <- PCA/PLS/LMM/plotting helpers
    _bundled_data/        <- connectome.db + parquet cache (git-ignored)
  notebooks/              <- analysis notebooks, run in numeric order
  docs/                   <- method primers and data dictionary
  example_output/         <- committed PNGs of expected figures
  tests/                  <- nbmake smoke test
  tools/                  <- developer scripts (sync_region_priors, build_notebooks)
```

For a reader walking the folder without running it, see
[`docs/reading_order.md`](docs/reading_order.md).

---

## Configuration

Three tiers, in order of precedence:

1. **Parameters cell at the top of each notebook** for per-figure tweaks
   (n_clusters, figsize, color palette). Edit here for one-off changes.
2. **`cfs_analysis/config.py`** for analysis-wide defaults (analyzable
   phases, FDR alpha, default component counts, frozen region-prior
   fallback).
3. **`CFS_ANALYSIS_DB` environment variable** to point at a non-default
   database location. Set this if you want to run against the live Y:
   drive database instead of the bundled copy.

---

## Data

The tool ships with a bundled copy of `connectome.db` in `_bundled_data/`,
git-ignored so it can exceed normal git size limits. To refresh the bundled
copy from the Y: drive, copy the file manually.

If `_bundled_data/connectome.db` is missing, `00_setup.ipynb` will fail
fast with a clear error message pointing at the missing file.

---

## Attribution

Analytical decisions and code structure were developed by Logan Friedrich
in the original monolithic notebook at
`g:/.../Classes/CFS_Project/analyze_kinematics_v_connectomics.ipynb`.
This refactor packages that work into a distributable form without changing
any analytical choices.
