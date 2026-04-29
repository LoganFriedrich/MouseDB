# Quickstart

For someone who received this folder and wants to run the analysis.

The whole process takes about 10 minutes the first time. After that,
steps 1 and 2 are already done and every subsequent run takes seconds.

There are two ways to run the notebooks. Pick whichever fits your setup:

- **Path A (recommended for lab members and developers)**: open the
  folder in **Visual Studio Code** with the Jupyter extension. Already
  the lab standard.
- **Path B (recommended for non-technical handoff)**: double-click the
  `run_analysis.bat` (Windows) or `run_analysis.command` (macOS/Linux)
  launcher. Opens JupyterLab in your web browser.

Steps 1 and 2 are identical for both paths. Step 3 branches.

---

## Step 1. Install Python

If `python --version` already works at a Command Prompt and shows `3.11`
or higher, skip this step.

Otherwise, install Python 3.11+ from <https://www.python.org/downloads/>.
On Windows, **check "Add Python to PATH"** during the installer's first
screen. Missing that box is the #1 reason later steps fail.

---

## Step 2. Set up the tool (one-time)

This creates a private Python environment just for this tool so its
packages don't interfere with anything else on your computer, and
registers a Jupyter kernel that VS Code or JupyterLab will use.

- **Windows**: double-click `install.bat`. A command window will open
  and print what it is doing. It takes 2-3 minutes. When it says
  "Install complete", close the window.
- **macOS**: open Terminal, drag-and-drop the `endpoint_ck_analysis`
  folder onto it to `cd` there, then type `./install.sh` and press
  Enter.
- **Linux**: same as macOS.

If the installer fails, open [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).

---

## Step 3. Open the notebooks

### Path A: VS Code (recommended)

<details>
<summary>What to do if you do not have VS Code or the Jupyter extension yet</summary>

1. Install VS Code from <https://code.visualstudio.com/>.
2. Open VS Code, go to the Extensions sidebar (square icon, or
   `Ctrl+Shift+X`), search for "Jupyter", and install Microsoft's
   official "Jupyter" extension. This pulls in the Python extension as
   a dependency.
3. Restart VS Code.

</details>

1. In VS Code: `File -> Open Folder...` and select this
   `endpoint_ck_analysis` folder. Or drag the folder onto the VS Code
   window.
2. In the file explorer panel on the left, expand `notebooks/` and
   click `00_setup.ipynb`. The notebook opens as a notebook view (not
   raw JSON).

### Path B: JupyterLab launcher

- **Windows**: double-click `run_analysis.bat`. A command window will
  open and JupyterLab will open in your web browser.
- **macOS**: double-click `run_analysis.command`.
- **Linux**: `./run_analysis.command` in a terminal.

**Leave the command window open** while you are using JupyterLab --
closing it stops the server.

---

## Step 4. Pick the kernel

Whichever path you took, you need to tell the notebook which Python
environment to use.

### In VS Code

The kernel selector sits in the top-right corner of the notebook tab,
just under the tab title. Click it.

VS Code's kernel picker is a **two-level menu**: it does NOT immediately
show kernels. The first screen shows kernel *categories*:

- `Python Environments...` -- Python interpreters VS Code has discovered
- `Jupyter Kernel...` -- kernelspecs registered via `ipykernel install`
- `Existing Jupyter Server...` -- remote Jupyter servers
- (a few others depending on extensions)

To find **Python (mousedb)** (or **Python (endpoint_ck_analysis)** in the
dedicated-venv path), click `Jupyter Kernel...` -- that's where
registered kernelspecs live. The actual kernel names appear on the
*second* screen of the picker.

If you click `Python Environments...` instead and your env doesn't
appear there, see the troubleshooting section in
[`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) -- the conda env at
`C:\2_Connectome\envs\MouseDB\` is in a non-standard location and may
need to be added manually via `Python: Select Interpreter`.

### In JupyterLab

The first time you open a notebook, JupyterLab will pop up a "Select
Kernel" dialog. Choose **Python (endpoint_ck_analysis)**. If you miss
the dialog, the kernel name is shown in the top-right of the notebook
and you can click it to switch.

If **Python (endpoint_ck_analysis)** is not in the list in either
environment, re-run Step 2 -- the installer registers the kernel as
part of its work, and skipping it leaves no kernel to pick.

---

## Step 5. Run the notebooks in order

Open and run each notebook in order:

1. **`00_setup.ipynb`** -- always run first. It checks the environment
   and loads all data into a cache the other notebooks use.
2. **`01_connectivity_pca.ipynb`** -- connectivity PCA.
3. **`02_kinematic_pca.ipynb`** -- kinematic PCA across phases.
4. **`03_kinematic_clustering.ipynb`** -- kinematic feature clustering.
5. **`04_pls_variants.ipynb`** -- PLS: injury / deficit / recovery.
6. **`05_lmm_phase_effects.ipynb`** -- mixed models for phase effects.
   (Slowest one; allow ~10 minutes.)
7. **`06_pellet_validation.ipynb`** -- manual vs algorithmic pellet
   scoring.
8. **`07_connectivity_trajectory_linkage.ipynb`** -- ties it all
   together: per-subject kinematic trajectories colored by
   connectivity, plus an interaction-LMM template ready for when N
   grows.
9. **`08_hypothesis_informed_tests.ipynb`** -- tests whether the eLife
   grouping or region-priority ordering is throwing away structure:
   grouped-vs-ungrouped PCA, per-group drill-down, nested LMM,
   prior-weighted decomposition.
10. **`99_figure_gallery.ipynb`** -- every figure in one scrollable
    page.

In each notebook, run all cells:

- **VS Code**: click the "Run All" button at the top of the notebook,
  or press `Ctrl+Alt+Enter` (Cmd+Alt+Enter on macOS).
- **JupyterLab**: pick `Run -> Run All Cells` from the top menu.

Scroll through the output and inspect the figures that appear inline.

---

## Step 6. Tweak something (optional)

Each notebook has a "parameters" cell near the top, full of values like
`N_COMPONENTS = 3` or `TOP_N_REGIONS = 10`. Change these and re-run the
notebook to render with different settings.

See [`docs/customizing_figures.md`](docs/customizing_figures.md) for
the common knobs per notebook.

---

## Notes

- Everything happens **inside this folder**. Nothing writes to your
  Documents, Desktop, or anywhere else.
- The bundled database is at
  `endpoint_ck_analysis/_bundled_data/connectome.db`. If it is missing,
  step 5's first notebook will say so with a clear message.
- If you close VS Code or JupyterLab and come back later, you only need
  Step 3 and Step 5.
- If you move this folder to a different disk or computer, re-run
  Step 2. Everything else still works because all paths are computed
  relative to the folder.
