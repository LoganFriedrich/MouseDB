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

# 2c. ...and where the tracking workbooks (Connectome_NN_Animal_Tracking.xlsx) are
mousedb-sheets set-dir <folder holding the tracking workbooks>

# 3. Initialize the database
mousedb init

# 4. Import the tracking sheets (preview first: reads every cohort's sheet, imports nothing)
mousedb import --all --dry-run

# 5. Import for real (one line per cohort, OK or FAIL; exit code 1 if any cohort failed)
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

`manual_setup_check.py`, in the repo root, is an interactive check of an
install, not a test: `python manual_setup_check.py`. It opens the configured
database, shows its row counts, lists the tracking sheets the way
`mousedb-sheets status` does and -- only into a database that holds no
animals yet -- offers to import them, a dry run first. That import is the same
recorded import as `mousedb-sheets import`: it waits for the database lock and
is written to `logs/sheet_imports.jsonl` as `manual_setup_check`.

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
| `mousedb import --all` | Import each cohort's tracking sheet from the configured sheets folder: ONE file per cohort (the pinned one, else the newest), the same import as the Tracking Sheets tab. Each cohort's outcome goes into `logs/sheet_imports.jsonl`; exit code 1 if any cohort failed. See [`docs/TRACKING_SHEETS.md`](docs/TRACKING_SHEETS.md) |
| `mousedb import --file path/to/file.xlsx` | Import one cohort's workbook, the same way (when the import saves, it also replaces that cohort's copy of the animal details; a failed import leaves them as they were). Refused, with what to run instead, if it sits in an archive/snapshot folder or is not named as a sheet (a copy, an Excel lock file, a tool's export) |
| `mousedb import --all --dry-run` | Read every sheet and report what would be imported, without importing |
| `mousedb-sheets status` | Per cohort: which file is its sheet, when it was edited, when it was imported, whether that worked |
| `mousedb-make-sheets --new --cohort CNT_07 --start-date 2026-01-31 --output-dir <folder>` | Create a NEW cohort's empty tracking workbook (the Tracking Sheets tab's "New cohort sheet..." button). It never writes over a file: unless the name is exactly `PREFIX_NN` and that cohort has no workbook in the folder yet, it prints `REFUSED: <reason>`, writes nothing and exits with code 3. See [`docs/TRACKING_SHEETS.md`](docs/TRACKING_SHEETS.md), "Starting a new cohort" |
| `mousedb export --cohort CNT_05` | Export to legacy Excel format |
| `mousedb export --cohort CNT_05 --odc` | Export ODC format (calculated stats) |
| `mousedb export --cohort CNT_05 --all-formats` | Export all formats |
| `mousedb export ... --output <path>` | Write the export to `<path>`. Refused before the database is opened (`REFUSED` / `Why` / `Instead`, exit code 1) when `<path>` is inside the tracking-sheets folder or the ASPA folder, or is named like a cohort's tracking sheet: the export would empty the workbook that is there |
| `mousedb export --unified` | Export unified reaches parquet |
| `mousedb entry` | Launch GUI |
| `mousedb import-reaches --pellets-only` | Fill the per-pellet table (`video_pellets`: one row per pellet the pipeline cut each video into, reached for or not) from every features file, touching nothing else. Run once after upgrading; the nightly import keeps it current afterwards |
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
| `<mousedb_root>/logs/` | Audit trail (JSONL) and the import ledgers (`sheet_imports.jsonl`, `reach_imports.json`, `analysis_imports.json`) |
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
- Imports started from mousedb (the GUI's import buttons, `mousedb import`,
  `mousedb-sheets import`, the scheduled jobs) take turns and wait for one
  another -- see "What happens when another program has the database
  locked" below
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

### Study facts (once per study)

ODC-SCI records repeat facts that are the same for every animal in a study:
species, strain, supplier, study leader, injury device. They belong to your
study, not to the tool, so mousedb has none built in. Set them per project
(the letters before the first underscore of a cohort id), or under `default`
for every project:

```
mousedb study-facts --set <PROJECT> SpeciesTyp "<species>"
mousedb study-facts --set <PROJECT> SpeciesStrainTyp "<strain, as the supplier names it>"
mousedb study-facts --set <PROJECT> AnimalSourceNam "<supplier or colony>"
mousedb study-facts --set <PROJECT> StudyLeader "<name as it should appear in shared records>"
mousedb study-facts --set <PROJECT> Injury_device "<device model and version>"
mousedb study-facts --show
```

The same command also holds the surgery protocol a NEW blank tracking sheet
starts with (`Contusion_Location`, `Intended_kd`, `Anesthetic`,
`Anesthetic_Dose`, `Analgesic`, `Analgesic_Dose`, `Injection_Location`,
`Depths (D/V)`, `Coordinates (M/L)`); each animal's real values are still
what is typed into its sheet.

They are saved in `~/.mousedb/study_facts.json` (or the file named by
`MOUSEDB_STUDY_FACTS`). A fact that is not set is left as an empty cell in
generated sheets and exports -- never guessed.

## Scheduled jobs (the processing machine)

mousedb is an integrator: it PULLS from the tools on a schedule. Register these
with the operating system's scheduler (Task Scheduler / cron), all in the MouseDB
environment:

| job | command | cadence | what it does |
|---|---|---|---|
| snapshot | `python -m mousedb.exporters.refresh_snapshot` | hourly | takes the parquet snapshot of the database and rewrites `exports/current/` (one folder per project, one per cohort inside it, and a combined file per kind per project), including the per-cohort `ODC_sessions_*.csv` (those read the database, which a successful snapshot has just shown to be readable; `MANIFEST.json` says whether they were refreshed); then retakes the pipeline census the Where Is My Data tab shows (`--skip-census` to leave it) |
| sheet import | `python -m mousedb.sheet_sync import --triggered-by scheduled` | every 4 h | imports the tracking workbooks (they are filled in by hand, so hourly only adds load) |
| reach import | `python -m mousedb.import_reaches` | hourly | pulls new or changed MouseReach `*_features.json` results into `reach_data`, then re-derives the test-phase labels of every cohort a new video touched, writing only the rows whose label changes. Also records every pellet of each video in `video_pellets`, so the exports can tell how many pellets a tray presented |
| analysis import | `python -m mousedb.import_analyses` | hourly | mirrors MouseBrain's analysis registry (exports, figures, logs, provenance) into the mousedb folders and writes `exports/ANALYSES_MANIFEST.json` |
| bench-sheet check | `python -m mousedb.bench_scan --route` | every 2 h | finds never-reviewed pellets where the hand score and the pipeline disagree, writes the worklist to `logs/never_reviewed_worklist.json` under `mousedb_root` (`--out` to put it elsewhere) and asks MouseReach (`mousereach-route-to-queue`) to hold those videos for a person |

Cadence is a judgement about how fresh the data needs to be, not a fixed part of
the design. Pick it from how long each job actually takes on YOUR corpus: they
share one database lock, so a job scheduled more often than it takes to run will
queue behind itself, starve the others, and eventually be refused outright while
still reporting a failure. Measure first, then schedule. Run them in dependency
order with the snapshot LAST, because the snapshot is built from the database the
imports fill -- on independent clocks it will regularly rebuild pre-import data.

The cadences in the table are a starting suggestion, not a recommendation for
every installation. Where a job takes tens of minutes, daily is the right
answer; a corpus small enough that each job finishes in seconds can run them
hourly.

The jobs that write the database do not run while a MouseReach watcher is
running on the same machine; they report that and exit. The analysis import
writes files only (never the database), so it runs regardless.

### What happens when another program has the database locked

The database is a single SQLite file and anything that can see it can open it:
these jobs, this tool's own windows, and a person's analysis session -- on this
machine or any other that reaches the same file. While one of them writes, the
rest are refused with "database is locked".

Two separate mechanisms deal with that, and it is worth knowing which is which:

- **Between these jobs**: a lockfile under `<mousedb_root>/logs`
  (`mousedb/task_mutex.py`). Whoever gets it runs; the others wait and run
  straight after. They never lock each other out, however they are scheduled.
  The lock is never taken away from a job that is still running, however long
  it has held it (a long rewrite over a slow share is slow, not stuck); it is
  taken only from a holder that no longer exists. A job that waited its full
  allowance (25 minutes) logs who is still holding the lock and exits non-zero
  without touching anything.
- **Against everything else**: `mousedb/db_contention.py`. A job that meets an
  outside lock waits and retries across a bounded window, then gives up, logs
  one plain-English line, and exits non-zero.

  **Size that window to the clear air after the job, minus how long the job
  takes when it succeeds** -- a retry restarts the job from the beginning, so a
  window that looks generous can leave it still running when the next job is
  due. The short imports use the 45-minute default; the snapshot refresh takes
  `--retry-minutes` (default 25) because a full refresh of a multi-gigabyte
  database over a network share can take over an hour on its own.

  Retrying only ever rescues a lock that clears in minutes. A session left open
  for an hour will outlast any window worth having, and that is what the
  snapshot-age reporting below is for -- not more waiting.

Neither mechanism ever breaks the other program's lock, and there is no option
to: a lock means a write is in flight, and taking it away corrupts the file. A
job that keeps giving up means a session somewhere is holding the database
open, and the fix is to close that session.

**Let the scheduler retry a job that gave up.** Both mechanisms make the job
exit non-zero when it could not run, and that is deliberate: it lets the
operating system's scheduler run it again later. Set "restart on failure"
(Task Scheduler: *If the task fails, restart every 30 minutes, up to 8
times*; cron: a wrapper that re-queues on a non-zero exit) on every job, and
above all on the snapshot, because the snapshot is what makes the exports. With
that in place a slow night DELAYS the exports instead of losing them until the
next night. Without it, one long-running import means no exports that day.

Keep the scheduler's own "stop the task if it runs longer than" limit
generous (hours, not minutes) for the jobs that write the database. That limit
stops the task's wrapper, not necessarily the Python process underneath, so a
job cut off mid-write by the scheduler can go on running unsupervised while
the scheduler reports it as terminated -- the worst of both.

### Why the first read of the database is slow, and what the jobs do about it

SQLite reads a table 4 KB at a time, one request per page. Over a network
share that is a fraction of a megabyte per second until the file is in the
operating system's cache, and tens of MB/s after; a scan of a multi-gigabyte
table is hours cold and minutes warm. The jobs that scan whole tables -- the
snapshot refresh, the phase re-labelling the reach import runs, `mousedb
backfill-phases` -- therefore read the database file once sequentially before
they start and keep it open until they finish, because the cache is dropped
when the last handle on the file closes (`mousedb/prefetch.py`; the log line
`read connectome.db once (...)` is that step). On a local disk it costs
seconds. `MOUSEDB_PREFETCH=0` turns it off.

**How you find out it happened.** The failing job's log says so in one sentence,
and `mousedb-data-status` and the GUI's Where Is My Data tab both report the
snapshot's AGE in words and turn it red past `STALE_SNAPSHOT_HOURS`
(`mousedb/data_status.py`, 36 h by default -- raise it if the snapshot job runs
less often than daily). That age line is the thing that catches a job that has
quietly not worked for days; the log alone does not, because nobody reads logs
that have not announced a problem.

### The snapshot job also retakes the pipeline census

The pipeline counts on the Where Is My Data tab ("should exist", "finished on
disk", where every unfinished video sits) come from a census of the MouseReach
pipeline folders, cached in `logs/pipeline_census.json` under `mousedb_root`.
After writing the exports, `refresh_snapshot` retakes it
(`pipeline_census.refresh`, through the configured `mousereach_env`): a
read-only folder scan of 5-25 minutes, holding no database lock. **Why:** it
used to be retaken only by the tab's "Refresh pipeline view" button, and a
census weeks old was shown as the current counts with nothing but a timestamp
to say so. The tab and `mousedb-data-status` now print the census's age and
flag it once it is older than `data_status.STALE_CENSUS_HOURS` (36). If the
census cannot be taken, the job prints `[FAIL] pipeline census not retaken` and
still exits 0 -- a failing exit would make the scheduler re-run the whole
snapshot -- and the tab keeps the previous census with its age shown. Pass
`--skip-census` when running the job by hand and the census is not wanted.

### The snapshot job also rewrites the exports -- including the per-reach files

`refresh_snapshot` ends by calling `exporters.current.refresh_current()`, so it
is the thing that regenerates `exports/current/` on a schedule. Two consequences
that are easy to be caught by:

- **Whatever code that machine has installed is the code that writes the
  exports.** Correcting the exporter and regenerating by hand from a working copy
  does not stick: the next scheduled snapshot rewrites those files using the
  INSTALLED copy. Deploy first, then regenerate.
- **The per-reach files are skipped when their inputs are unchanged**
  (`_odc_reaches_csvs`, signature in `exports/current/.odc_reaches_signature.json`).
  The signature covers the inputs -- snapshot table sizes and row counts, and the
  study-facts file -- so it cannot notice that the exporter now writes different
  columns or different words. When the OUTPUT format changes, bump
  `sig["output_format"]` in `exporters/current.py`, or a machine still holding the
  previous code will skip the rewrite and leave superseded files looking current.
