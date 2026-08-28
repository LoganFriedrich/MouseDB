# mousedb

Database and data management tools for mouse experiments. SQLAlchemy ORM
on top of `connectome.db` (the lab's single source of truth), Excel/Parquet
import + export, a PyQt5 GUI for data entry, a FastAPI web dashboard, and
analytical sub-packages.

## Subpackages

| Subpackage | Purpose |
|---|---|
| [`mousedb/`](mousedb/) | Core database, importers, exporters, GUI, web, figures, validators, CLI. |
| [`mousedb/endpoint_ck_analysis/`](mousedb/endpoint_ck_analysis/) | Self-contained endpoint statistical analysis pipeline (PCA, PLS, LMM, clustering) on the SCI cohort. Has its own `pyproject.toml` and notebooks. |

## Install

See [`INSTALL.md`](INSTALL.md) for the full step-by-step. Short version:

```
conda env create -f environment.yml -p <env_dir>
conda activate <env_dir>
pip install -e ".[dev]"
mousedb config --set mousedb_root <folder for connectome.db, exports/, logs/>
mousedb status
```

## Run the tests

```
pip install -e ".[dev]"
pytest
```

The suite covers:

- `tests/test_phases.py` — phase-assignment regression tests against the
  empirically-correct CNT_01 / CNT_04 fixtures.
- `tests/test_region_priors.py` — predicted-importance ordering invariants.
- `tests/test_validators.py` — ID-format and data-validator coverage; this
  is the front line for every import path.
- `mousedb/endpoint_ck_analysis/tests/test_smoke.py` — integration test that
  executes `00_setup.ipynb` end-to-end against the bundled database.

`testpaths` in `pyproject.toml` keeps discovery scoped to those directories;
running `pytest` from the repo root will not crawl `.venv/` or any of the
stray `pytest-cache-files-*/` directories.

## Repository layout

```
mousedb/
├── README.md            # this file
├── INSTALL.md           # install + GUI usage walkthrough
├── CHANGELOG.md         # high-level change history
├── docs/                # operator guides (Where is my data?, Tracking Sheets)
├── environment.yml      # conda env spec
├── pyproject.toml       # pip / pytest / ruff / coverage config
├── manual_setup_check.py  # interactive smoke-script (not collected by pytest)
├── tests/               # unit tests
├── mousedb/             # core package source
│   └── endpoint_ck_analysis/   # statistical analysis sibling package
└── .github/workflows/   # CI
```

## For first-time contributors

1. `pip install -e ".[dev]"` so `pytest`, `nbclient`, `papermill`, `black`,
   and `ruff` are available.
2. `pytest` — full suite should pass against a populated `connectome.db`.
   Tests that require the database (the smoke notebook) skip gracefully when
   `nbclient` isn't installed.
3. Read the module docstrings for orientation: every module under `mousedb/`
   opens with WHY it exists and what it touches, and `INSTALL.md`
   ("Configuration") explains the per-machine settings.
4. Read `mousedb/endpoint_ck_analysis/QUICKSTART.md` if you're going to
   touch the analysis pipeline.

## Reporting bugs

Open an issue with the failing import or query, the cohort it affects, and
either a minimal reproduction or the exact CLI invocation that broke. For
GUI bugs, a screenshot helps a lot.

## Operator guides (no codebase knowledge assumed)

- [Where is my data?](docs/WHERE_IS_MY_DATA.md) -- the current exports folder (CSV + data dictionaries, ODC-SCI shape), the per-cohort status tab, and how reach data gets into the database (`mousedb import-reaches`).
- [Tracking Sheets](docs/TRACKING_SHEETS.md) -- importing the lab's tracking workbooks from a button, choosing between duplicate files, creating a new cohort sheet.
- Tissue analyses -- MouseBrain's analysis outputs (measurements, figures, `registry.json` provenance) are mirrored into `<mousedb_root>/exports/<analysis>` and `figures/<analysis>` by `mousedb import-analyses` (hourly where scheduled); `exports/ANALYSES_MANIFEST.json` and the Where Is My Data tab show one line per analysis. See "How tissue analysis outputs get here" in [Where is my data?](docs/WHERE_IS_MY_DATA.md).
- `mousedb config --show` -- every machine-specific location (database, snapshot, pipeline folders, sheets folder, lab name) and where each value came from. Nothing lab-specific is in the source; everything is set once per machine with `mousedb config --set <key> <value>`.
