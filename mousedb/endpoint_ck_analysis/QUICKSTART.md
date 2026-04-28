# Quickstart

For someone who received this folder and wants to run the analysis. Does
not assume you know what a virtual environment, kernel, or git is.

The whole process takes about 10 minutes the first time. After that, steps
1 and 2 are already done and every subsequent run takes seconds.

---

## Step 1. Install Python

If `python --version` already works at a Command Prompt and shows `3.11`
or higher, you can skip this step.

Otherwise, go to <https://www.python.org/downloads/> and install the
latest Windows (or macOS) installer. On Windows, **check the box that says
"Add Python to PATH"** during the install. Missing that box is the #1
reason later steps fail.

---

## Step 2. Set up the tool (one-time)

This creates a private Python environment just for this tool so its
packages don't interfere with anything else on your computer.

- **Windows**: double-click `install.bat`. A command window will open and
  print what it is doing. It takes 2-3 minutes. When it says "Install
  complete", close the window.
- **macOS**: open Terminal, drag-and-drop the `endpoint_ck_analysis` folder onto
  it to `cd` there, then type `./install.sh` and press Enter.
- **Linux**: same as macOS.

If the installer fails, open [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

---

## Step 3. Launch JupyterLab

- **Windows**: double-click `run_analysis.bat`. A command window will
  open, JupyterLab will open in your web browser.
- **macOS**: double-click `run_analysis.command`.
- **Linux**: `./run_analysis.command` in a terminal.

**Leave the command window open** while you are using JupyterLab --
closing it stops the server.

---

## Step 4. Pick the kernel

The first time you open a notebook, JupyterLab will ask "Select Kernel".
Choose **Python (endpoint_ck_analysis)**.

If that option is not in the list, re-run step 2; the installer registers
the kernel as part of its work.

---

## Step 5. Run the notebooks in order

In the file browser on the left side of JupyterLab, double-click each
notebook in order:

1. **`00_setup.ipynb`** -- always run first. It checks the environment
   and loads all data into a cache the other notebooks use.
2. **`01_connectivity_pca.ipynb`** -- connectivity PCA.
3. **`02_kinematic_pca.ipynb`** -- kinematic PCA across phases.
4. **`03_kinematic_clustering.ipynb`** -- kinematic feature clustering.
5. **`04_pls_variants.ipynb`** -- PLS: injury / deficit / recovery.
6. **`05_lmm_phase_effects.ipynb`** -- mixed models for phase effects.
   (This one is the slowest; allow ~10 minutes.)
7. **`06_pellet_validation.ipynb`** -- manual vs algorithmic pellet scoring.
8. **`07_connectivity_trajectory_linkage.ipynb`** -- ties it all together:
   per-subject kinematic trajectories colored by connectivity, plus an
   interaction-LMM template ready for when N grows.
9. **`08_hypothesis_informed_tests.ipynb`** -- tests whether the eLife
   grouping or the region-priority ordering is throwing away structure:
   grouped-vs-ungrouped PCA, per-group drill-down, nested LMM, prior-
   weighted decomposition.
10. **`99_figure_gallery.ipynb`** -- every figure in one scrollable page.

In each notebook: pick **Run All Cells** from the "Run" menu. Scroll
through and inspect the figures that appear inline.

---

## Step 6. Tweak something (optional)

Each notebook has a "parameters" cell near the top, full of values like
`N_COMPONENTS = 3` or `TOP_N_REGIONS = 10`. Change these to re-render
with different settings.

See [`docs/customizing_figures.md`](docs/customizing_figures.md) for the
common knobs per notebook.

---

## Notes

- Everything happens **inside this folder**. Nothing writes to your
  Documents, Desktop, or anywhere else.
- The bundled database is at `endpoint_ck_analysis/_bundled_data/connectome.db`.
  If it is missing, step 5's first notebook will say so with a clear
  message.
- If you close JupyterLab and come back later, you only need steps 3 and 5.
- If you move this folder to a different disk or computer, re-run step 2.
  Everything else still works because all paths are computed relative to
  the folder.
