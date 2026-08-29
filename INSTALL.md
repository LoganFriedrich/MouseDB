# MouseDB - Installation & Usage

## Subpackages

`mousedb` ships two installable Python packages:

- **`mousedb`** itself — the core package described in this document
  (database, importers, exporters, GUI, web, validators, CLI).
- **`mousedb/endpoint_ck_analysis/`** — a self-contained statistical
  analysis pipeline (PCA, PLS, LMM, clustering) on the SCI cohort. It
  ships its own `pyproject.toml`, notebooks, and a `QUICKSTART.md`. See
  [`mousedb/endpoint_ck_analysis/QUICKSTART.md`](mousedb/endpoint_ck_analysis/QUICKSTART.md)
  for that subpackage's setup; it can be installed and run independently
  of the rest of `mousedb`.

The instructions below cover the core `mousedb` package only.

## Quick Setup

```bash
# 1. Create the conda environment (<repo> = this checkout, <env_dir> = wherever you keep environments)
cd <repo>
conda env create -f environment.yml -p <env_dir>

# 2. Activate it
conda activate <env_dir>

# 2b. Tell mousedb where things are on this machine (see "Configuration" below)
mousedb config --set mousedb_root <folder for connectome.db, exports/, logs/>

# 3. Initialize the database
mousedb init

# 4. Import existing Excel data (preview first)
mousedb import --all --dry-run

# 5. Import for real
mousedb import --all

# 6. Launch the GUI
mousedb entry
```

## Running the tests

```bash
# Install the dev extras (pytest, nbclient, papermill, black, ruff)
pip install -e ".[dev]"

# Full suite from the repo root
pytest
```

`testpaths` in `pyproject.toml` keeps `pytest` scoped to `tests/` and
`mousedb/endpoint_ck_analysis/tests/`, so it won't crawl `.venv/` or stray
cache directories. The endpoint_ck_analysis smoke test executes a notebook
against the bundled database and is slower than the unit tests; if you
just want fast feedback while editing the core package, run
`pytest tests/`.

## GUI Features

The GUI has three tabs:

### Tab 1: Pellet Scores
- **For undergrad data entry** (bulletproof validation)
- Select Cohort → Animal → Date
- Weight entry with range validation
- 4 trays × 20 pellets = 80 total
- Click buttons to cycle 0/1/2, or type directly
- Color-coded: Red=Miss, Yellow=Displaced, Green=Retrieved
- Auto-save on "Next Animal"

### Tab 2: Surgery Records
- **For PI data entry** (contusion, tracing, perfusion)
- Select Animal → Fill form → Save
- Shows existing records for the animal
- Contusion: Force (kDyn), Displacement (µm), Velocity (mm/s)
- Tracing: Virus name, Volume (nL)
- Perfusion: Date, Notes

### Tab 3: Dashboard
- **For viewing stats** (like Excel formulas calculated)
- Select Cohort → See overview
- Subject summary table with:
  - Sessions, Pellets scored
  - Miss/Displaced/Retrieved/Contacted %
  - Injury force
- Click subject to see session details:
  - Date, Phase, Days Post-Injury
  - Weight %, Per-session stats

## Commands

| Command | Description |
|---------|-------------|
| `mousedb status` | Show database stats |
| `mousedb init` | Initialize/create database |
| `mousedb new-cohort CNT_06 --start-date 2025-02-01 --mice 16` | Create new cohort |
| `mousedb import --all` | Import all Excel files |
| `mousedb import --file path/to/file.xlsx` | Import single file |
| `mousedb import --all --dry-run` | Validate without importing |
| `mousedb export --cohort CNT_05` | Export to legacy Excel format |
| `mousedb export --cohort CNT_05 --odc` | Export ODC format (calculated stats) |
| `mousedb export --cohort CNT_05 --all-formats` | Export all formats |
| `mousedb export --unified` | Export unified reaches parquet |
| `mousedb entry` | Launch GUI |
| `mousedb import-reaches` | Pull new or changed MouseReach `*_features.json` results into `reach_data` (`--dry-run` to count only) |
| `mousedb import-analyses` | Mirror MouseBrain's analysis registry (exports, figures, logs, provenance) into `<mousedb_root>/exports/<analysis>`, `figures/<analysis>`, `logs/`; writes `exports/ANALYSES_MANIFEST.json` (`--dry-run` to count only) |
| `mousedb import-brains --all` | Import BrainGlobe region counts, calibration runs and eLife group counts from MouseBrain's `3D_Cleared/2_Data_Summary` (`--summary-dir` to point elsewhere) |

## Export Formats

### Legacy Excel (`--cohort CNT_05`)
Matches the old tracking sheet structure:
- `0a_Metadata` - Subject info
- `1_Weight` - Daily weights
- `3b_Manual_Tray` - Pellet scores in 20-column format
- `4_Contusion_Injury_Details` - Surgery info

### ODC Format (`--odc`)
203-column format with calculated stats per session:
- Per-tray: Presented, Miss, Displaced, Retrieved, Contacted (counts + %)
- Daily totals and percentages
- Averages across trays
- Days post-injury, Weight %

### Unified Parquet (`--unified`)
All subjects with session summaries for analysis:
- Subject metadata
- Injury details
- Session-level aggregates

## Files

`<mousedb_root>` is the folder set with `mousedb config --set mousedb_root <path>`
(`mousedb config --show` prints it).

| Location | Purpose |
|----------|---------|
| `<mousedb_root>/connectome.db` | SQLite database (single source of truth) |
| `<mousedb_root>/logs/` | Audit trail (JSONL) and the import ledgers (`reach_imports.json`, `analysis_imports.json`) |
| `<mousedb_root>/exports/` | Generated exports; `exports/current/` is the folder people open |
| `<mousedb_root>/exports/<analysis>/`, `figures/<analysis>/` | Mirrors of MouseBrain's analysis registry (measurements, figures, `registry.json` provenance); summarised in `exports/ANALYSES_MANIFEST.json` |
| `<mousedb_root>/_archived/` | Mirrored files whose source was withdrawn -- moved here, never deleted |

## Validation Rules

| Field | Constraint | Error |
|-------|------------|-------|
| Subject ID | `CNT_XX_YY` format | "Subject ID must be PROJECT_COHORT_SUBJECT" |
| Pellet score | 0, 1, or 2 only | "Score must be 0=miss, 1=displaced, 2=retrieved" |
| Tray number | 1-4 | "Tray number must be 1-4" |
| Pellet number | 1-20 | "Pellet number must be 1-20" |
| Weight | 10-50g | "Weight must be in valid range" |
| Sex | M or F | "Sex must be M or F" |

## Updating

If you modify the package code:
```bash
# No reinstall needed - it's installed in editable mode (-e)
# Just restart Python/GUI to pick up changes
```

If you add new CLI commands to pyproject.toml:
```bash
conda activate <env_dir>
pip install -e <repo>
```

## Troubleshooting

### "PyQt5 not found"
```bash
conda activate <env_dir>
pip install PyQt5
```

### "Module not found: mousedb"
```bash
pip install -e <repo>
```

### Database locked
- Only one user should run `mousedb import` at a time
- GUI can have multiple users reading simultaneously
- Writes are serialized automatically

## Configuration (once per machine)

mousedb has NO built-in paths. After installing, tell it where things are:

```
mousedb config --set mousedb_root <folder holding connectome.db, exports/, logs/>
mousedb config --set snapshot_dir <local folder for the parquet snapshot>
mousedb config --set mousereach_pipeline_root <MouseReach's shared pipeline folder>   # optional if ~/.mousereach/config.json exists
mousedb config --set mousebrain_pipeline_root <MouseBrain's pipeline folder>
mousedb config --set lab_name "<your lab, as it should appear in exports>"
mousedb config --set mousereach_env <MouseReach env's Scripts or bin dir>             # only for bench-scan routing
mousedb-sheets set-dir <folder holding the tracking workbooks>
mousedb config --show
```

Any command that needs a location that is not set stops with a message naming
the exact `mousedb config --set` line to run. Values can also be given per run
as environment variables (`mousedb config --show` lists them).

## Scheduled jobs (the processing machine)

mousedb is an integrator: it PULLS from the tools on a schedule. Register these
with the operating system's scheduler (Task Scheduler / cron), all in the MouseDB
environment:

| job | command | cadence | what it does |
|---|---|---|---|
| snapshot | `python -m mousedb.exporters.refresh_snapshot` | hourly | takes the parquet snapshot of the database and rewrites `exports/current/`, including the per-cohort `ODC_sessions_*.csv` (those read the database, which a successful snapshot has just shown to be readable; `MANIFEST.json` says whether they were refreshed) |
| sheet import | `python -m mousedb.sheet_sync import --triggered-by scheduled` | every 4 h | imports the tracking workbooks (they are filled in by hand, so hourly only adds load) |
| reach import | `python -m mousedb.import_reaches` | hourly | pulls new or changed MouseReach `*_features.json` results into `reach_data` |
| analysis import | `python -m mousedb.import_analyses` | hourly | mirrors MouseBrain's analysis registry (exports, figures, logs, provenance) into the mousedb folders and writes `exports/ANALYSES_MANIFEST.json` |
| bench-sheet check | `python -m mousedb.bench_scan --route` | every 2 h | finds never-reviewed pellets where the hand score and the pipeline disagree, writes the worklist to `logs/never_reviewed_worklist.json` under `mousedb_root` (`--out` to put it elsewhere) and asks MouseReach (`mousereach-route-to-queue`) to hold those videos for a person |

The jobs that write the database do not run while a MouseReach watcher is
running on the same machine; they report that and exit. The analysis import
writes files only (never the database), so it runs regardless.
