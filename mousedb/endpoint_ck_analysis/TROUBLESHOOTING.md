# Troubleshooting

Common errors and fixes. Ordered by what you are most likely to hit.

---

## "Python is not recognized as an internal or external command"

The Python installer didn't add Python to your PATH.

**Fix**: Re-run the Python installer from <https://www.python.org/downloads/>
and **check "Add Python to PATH"** at the first screen. Then run
`install.bat` again.

---

## `install.bat` fails with "Failed to create virtual environment"

Usually means Python is installed but broken, or Windows Defender blocked
the `.venv` creation.

**Fix**: Delete any `.venv` folder that was partially created in this
directory, then re-run `install.bat`. If it still fails, open a Command
Prompt in this folder and run `python --version` -- if that errors, see
the previous entry.

---

## "No module named 'endpoint_ck_analysis'" in a notebook cell

The kernel you selected is not the `Python (endpoint_ck_analysis)` kernel.

**Fix**: In JupyterLab, click the kernel name in the top-right of the
notebook (probably says "Python 3" or similar) and choose **Python
(endpoint_ck_analysis)** from the dropdown.

If that option is not listed, re-run `install.bat`. The kernel
registration happens at the end of the installer.

---

## "Database file not found at ..."

The tool couldn't find `connectome.db`.

**Fix 1 (recommended)**: Copy `connectome.db` into
`endpoint_ck_analysis/_bundled_data/`. The file should be at
`.../endpoint_ck_analysis/endpoint_ck_analysis/_bundled_data/connectome.db` (note the
nested folder name).

**Fix 2 (power user)**: Set the `CFS_ANALYSIS_DB` environment variable to
the full path of the database file before launching JupyterLab. On
Windows, you can do this in a Command Prompt:

```
set CFS_ANALYSIS_DB=Y:\2_Connectome\Databases\connectome.db
run_analysis.bat
```

Leave out the `set` line to fall back to the bundled copy.

---

## "Cell is missing an id field" warning

This is a harmless warning from the nbformat library about notebook
metadata. The analysis runs fine. You can ignore it.

---

## JupyterLab opens but the notebooks fail to connect to a kernel

Some kind of Python/Jupyter conflict on your machine.

**Fix**: In JupyterLab, in the top menu, pick **Kernel -> Restart Kernel**.
If that doesn't work, close JupyterLab (including the command window),
re-run `install.bat`, then `run_analysis.bat`.

---

## "Permission denied" when writing cache files

Something has the cache directory locked. Most common cause on Windows:
OneDrive is syncing the folder and has a file open.

**Fix**: Pause OneDrive sync on this folder (right-click the OneDrive icon
in the system tray -> Pause syncing -> 2 hours), then re-run the notebook
cell. Resume sync when you're done.

---

## "File ... is locked by another process"

Usually means you have a parquet or CSV file open in Excel.

**Fix**: Close Excel. Re-run the cell.

---

## 05_lmm_phase_effects takes forever

It fits one linear mixed model per kinematic feature (roughly 30 models),
each with nested random effects. Expect 5-15 minutes depending on machine
speed. This is not a bug.

If you truly need to skip it, you can set `FEATURE_LIST` in the
parameters cell to a shortlist of features.

---

## Figures look different from the committed PNGs in `example_output/`

A few plausible causes:

- **Different matplotlib version**: tiny differences in colors and
  kerning are normal across versions.
- **Different data**: someone swapped the bundled database for a newer
  version. The committed PNGs were rendered with the connectome.db that
  shipped with this release. Check the version stamp in the figure
  footers (bottom right of each figure) against the `__version__` in
  `endpoint_ck_analysis/__init__.py`.
- **Different parameters**: you changed something in the parameters cell.

---

## Still stuck?

Open `docs/reading_order.md` for a map of the folder. If you are a
developer who was given this folder, the source of truth for helper code
is `endpoint_ck_analysis/helpers/`; notebook code is intentionally thin and calls
into those modules.
