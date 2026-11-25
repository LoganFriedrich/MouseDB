# MouseDB - Installation & Usage

## Quick Setup

```bash
# 1. Create the conda environment (<repo> = this checkout, <env_dir> = wherever you keep environments)
cd <repo>
conda env create -f environment.yml -p <env_dir>

# 2. Activate it
conda activate <env_dir>

# 2b. Tell mousedb where things are on this machine (see "Configuration" below)
mousedb config --set mousedb_root <folder for connectome.db, exports/, logs/>

# 2c. ...what each project's tracking workbooks are called -- ONCE for the whole lab,
#     not per machine: it is saved beside the database on the shared storage
#     ({NN} = where the cohort number goes; docs/TRACKING_SHEETS.md, "Which
#     projects' sheets are read"). Repeat for each project that keeps sheets.
mousedb sheet-projects --set PROJA "ProjectA_{NN}_Animal_Tracking.xlsx"

# 2d. ...and where the tracking workbooks are
mousedb-sheets set-dir <folder holding the tracking workbooks>

# 3. Initialize the database
mousedb init

# 4. Import the tracking sheets (preview first: reads every cohort's sheet, imports nothing)
mousedb import --all --dry-run

# 5. Import for real (one line per cohort, OK or FAIL; exit code 1 if any cohort failed)
mousedb import --all

# 6. Shortcuts, so nobody needs this terminal again (see "Shortcuts" below)
mousedb-make-shortcuts            # for you;  --all-users for everyone (administrator)

# 7. Launch the GUI (or double-click "MouseDB" on the desktop)
mousedb entry
```

## Opening MouseDB (for everyone, no terminal)

- **MouseDB** on the desktop, or Start menu -> **MouseDB** folder -> **MouseDB**:
  the MouseDB window (tracking sheets, Where Is My Data, data entry).
- **MouseDB - Browse the data**, in the same two places: the read-only data
  browser (docs/DATA_BROWSER.md).

Double-click and wait a few seconds; no console window appears. If MouseDB
cannot start, a message box says why and names its log file
(`%LOCALAPPDATA%\mousedb\gui.log`). If the shortcuts are not there, ask
whoever looks after the computer to make them (next section).

## Shortcuts (once per computer)

The shortcuts run `pythonw -m mousedb.gui` and `pythonw -m mousedb.data_browser`
of the environment mousedb is installed in -- never an `.exe` in `Scripts\`,
because a running `.exe` blocks the next `pip install`. Nothing is copied, so
they keep working after every update. Make them with the MouseDB environment
active:

```bash
# for EVERYONE who logs in to this computer: Public Desktop + all-users Start menu
#   -> run from an ADMINISTRATOR PowerShell (Windows refuses these folders otherwise)
mousedb-make-shortcuts --all-users

# for yourself only (your Desktop + your Start menu); no administrator needed
mousedb-make-shortcuts

# both shortcuts into one existing folder you may write to (e.g. one person's Desktop)
mousedb-make-shortcuts --folder <folder>
```

`--no-desktop` / `--no-start-menu` leave one place out. Running it again
replaces the shortcuts in place. It refuses, in words and before writing
anything, a folder it may not write to, and it never creates a folder that is
not already there (other than the MouseDB folder in the Start menu). The same
command without the console script: `python -m mousedb.shortcuts`.

A shortcut only works for a person if mousedb knows, for that person, where
the database and the snapshot are. On a computer several people use, set the
locations once for everyone with `--machine` (see "Configuration" below), not
in each person's own settings.

## Data browser (optional)

The "Browse the data" button (Where Is My Data tab) and `mousedb-data-browser`
show the database's snapshot in each person's web browser, read-only. They need
one extra package set, installed once in the MouseDB environment:

```bash
conda activate <env_dir>
pip install -e ".[browse]"      # from the mousedb checkout; adds Datasette
```

Nothing else is installed system-wide. Its shortcut, **MouseDB - Browse the
data**, is made for everyone with the MouseDB shortcut by
`mousedb-make-shortcuts --all-users` (see "Shortcuts"); a person can also
make their own by opening the browser once and pressing "Put a shortcut on my
desktop" in its small window (or `mousedb-data-browser --make-shortcut`). Each person's private
copy (about half a gigabyte) lives in their own `%LOCALAPPDATA%\mousedb\data_browser`.
Operator guide, and why the copy: [docs/DATA_BROWSER.md](docs/DATA_BROWSER.md).

## Running the tests

```bash
# Install the dev extras (pytest, nbclient, papermill, black, ruff)
pip install -e ".[dev]"

# Full suite from the repo root
pytest
```

`testpaths` in `pyproject.toml` keeps `pytest` scoped to `tests/`, so it
won't crawl `.venv/` or stray cache directories.

### The tests never touch your real data (the live-folder guard)

The tests run on whatever computer you start them on, and that computer may
hold the lab's real database, exports and pipeline folders. So the suite
does two things before any test runs (in `tests/conftest.py`):

1. **It hides this computer's settings from the code under test.** Every
   test gets its own empty home folder and empty settings files, and every
   `MOUSEDB_*` / `MOUSEREACH_*` variable is removed, so the code never finds
   the real folders on its own.
2. **It guards the real folders anyway.** It finds out where the real folders
   are (see "Where the guard learns the real folders" below) and watches
   every file the tests touch:
   - a **write** into a real folder is **refused on the spot**: opening a
     file to write or append, creating a file or folder (including temporary
     ones), renaming or replacing a file there, copying onto it, removing
     it, connecting to a database there, writing a parquet file there. The
     call fails with `LiveFolderRefused` and **nothing is written**;
   - a **read** or a folder listing there is recorded;
   - either way the test that did it **fails**, and pytest's report lists
     every path, for example:

     ```
     this test reached this computer's live folders (it must use synthetic
     inputs under tmp_path; REFUSED = a write the guard stopped, read = a
     read it recorded):
       REFUSED open('w') <the real folder>\exports\new.csv
     ```

     A test like that is wrong: it must build its own inputs in pytest's
     `tmp_path` folder. Fix the test, never the guard.

The guard recognizes a real folder **however it is written**: upper or lower
case, `/` or `\`, the Windows short name of any part of it (`PROGRA~1`), the
server path of a mapped network drive (`\\server\share\...` for a drive
letter), or a folder link (junction or symbolic link) in the configured
folder's own path. All of these are worked out once, when the tests start.
A folder link that a test itself creates under `tmp_path` and points at a
real folder is **not** followed, so a write through it is not refused.

The guard sees the files that the test code and the code under test open
through Python. It does not see files opened by a child process the tests
start or by native (non-Python) code, so do not assume those are guarded.

**The code's own folder is not lab data.** If you run the tests from a
working copy that sits *inside* a real folder (for example a copy kept inside
the database folder), the files of that working copy are allowed -- the
tests read them and Python writes its `__pycache__` there -- while
everything *beside* it (the exports, the logs, the database file) stays
guarded. The working copy is the folder that holds `tests/`.

pytest's own cache is switched off for this repo (`-p no:cacheprovider` in
`pyproject.toml`), so running the tests leaves no `.pytest_cache` or
`pytest-cache-files-*` folder in the working copy.

#### Where the guard learns the real folders

You normally do nothing: the guard reads the same places the tools do, on a
normal login of this computer, **even when the person or script running the
tests has pointed HOME, USERPROFILE, APPDATA, LOCALAPPDATA and PROGRAMDATA
at a scratch folder and unset every tool variable** first:

- `.mousedb\config.json` and `.mousereach\config.json` in your real home
  folder and in the (possibly redirected) home folder, every value in them,
  sections inside sections included;
- the machine settings file `mousedb\config.json` in the real program-data
  folder (`%ProgramData%`) and in the redirected one;
- the variables of this process and the variables **saved** for your login
  and for the machine (on Windows: what "Edit environment variables" shows),
  whose names start with `MOUSEDB_`, `MOUSEREACH_`, `MOUSEBRAIN_` or
  `CONNECTOME_ROOT` (in any upper/lower case) or are the OneDrive folders;
- **your own list**, if you want to add folders the settings do not name:
  - `TEST_GUARD_REAL_ROOTS`: folders separated by `;` (Windows) or `:`; or
  - `TEST_GUARD_REAL_ROOTS_FILE`: the path of a text file with one folder per
    line (`#` starts a comment). A file named here that cannot be read stops
    the test run.

  These two names deliberately contain neither tool's name, so a runner that
  checks "no tool variable is set" before starting is not tripped by them.
  Example (placeholders): `set TEST_GUARD_REAL_ROOTS=<your data drive>\<lab folder>`
  before `pytest`.

Never write a real folder into the test code itself.

#### Running mousedb outside pytest during development

The guard exists only inside pytest. If you run any mousedb command or
`python -c "import mousedb ..."` by hand while developing, point
`PROGRAMDATA` (as well as HOME/USERPROFILE/APPDATA/LOCALAPPDATA) at a scratch
folder first: the machine settings file in `%ProgramData%\mousedb\config.json`
names the real database folder, and mousedb reads it when imported.

`manual_setup_check.py`, in the repo root, is an interactive check of an
install, not a test: `python manual_setup_check.py`. It opens the configured
database, shows its row counts, lists the tracking sheets the way
`mousedb-sheets status` does and -- only into a database that holds no
animals yet -- offers to import them, a dry run first. That import is the same
recorded import as `mousedb-sheets import`: it waits for the database lock and
is written to `logs/sheet_imports.jsonl` as `manual_setup_check`.

## GUI Features

The window (`mousedb-entry`, or the **MouseDB** shortcut) has these tabs, in
this order:

| tab | what it is for |
|---|---|
| **0. Setup** | where this computer keeps things: every machine-specific location, shown, checked and editable |
| **1. Cohort Setup** | create and manage cohorts, with their starting weights |
| **2. Ramp Entry** | the food-restriction days before testing: body and tray weights |
| **3. Testing Entry** | pellet scores for testing days, several animals on one screen |
| **4. Surgery Entry** | surgery records (see below) |
| **5. Virus Prep** | injection calculations for a tracing surgery |
| **6. Dashboard** | statistics per cohort and animal (see below) |
| **7. Database Browser** | the raw database tables |
| **8. Tracking Sheets** | import the lab's tracking workbooks; start a new cohort sheet ([docs/TRACKING_SHEETS.md](docs/TRACKING_SHEETS.md)) |
| **9. Where Is My Data** | per-cohort data status, the whole pipeline's counts, the current exports, "Update the database now" ([docs/WHERE_IS_MY_DATA.md](docs/WHERE_IS_MY_DATA.md)) |
| **10. Study Facts** | the facts no sheet records, and the exceptions to them |
| **Extra: Weight Entry** | weights for a whole cohort on one date |
| **Extra: Single Animal** | pellet scores for one animal (see below) |

A tab that cannot be built on this computer (for example because a setting
is missing) is left out, and a line starting `[!]` says why: in the terminal
when the window was started from one, otherwise in
`%LOCALAPPDATA%\mousedb\gui.log`. The other tabs keep their numbers.

There is no Video Pipeline or Archive Status tab. Those two tabs showed one
computer's MouseReach watcher records as if they were the pipeline's state,
and were removed: the whole pipeline is on **9. Where Is My Data**. The same
watcher records are still in `mousedb video-status` and `mousedb
archive-status` ([docs/WATCHER_VIEWS.md](docs/WATCHER_VIEWS.md)).

### Extra: Single Animal (pellet scores)
- **For undergrad data entry** (bulletproof validation)
- Select Cohort → Animal → Date
- Weight entry with range validation
- 4 trays × 20 pellets = 80 total
- Click buttons to cycle 0/1/2, or type directly
- Color-coded: Red=Miss, Yellow=Displaced, Green=Retrieved
- Auto-save on "Next Animal"

### 4. Surgery Entry
- **For PI data entry** (contusion, tracing, perfusion)
- Select Animal → Fill form → Save
- Shows existing records for the animal
- Contusion: Force (kDyn), Displacement (µm), Velocity (mm/s)
- Tracing: Virus name, Volume (nL)
- Perfusion: Date, Notes

### 6. Dashboard
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
| `mousedb new-cohort PROJA_06 --start-date 2025-02-01 --mice 16` | Create new cohort |
| `mousedb import --all` | Import each cohort's tracking sheet from the configured sheets folder: ONE file per cohort (the pinned one, else the newest), the same import as the Tracking Sheets tab. Each cohort's outcome goes into `logs/sheet_imports.jsonl`; exit code 1 if any cohort failed. See [`docs/TRACKING_SHEETS.md`](docs/TRACKING_SHEETS.md) |
| `mousedb import --file path/to/file.xlsx` | Import one cohort's workbook, the same way (when the import saves, it also replaces that cohort's copy of the animal details; a failed import leaves them as they were). Refused, with what to run instead, if it sits in an archive/snapshot folder or is not named as a sheet (a copy, an Excel lock file, a tool's export) |
| `mousedb import --all --dry-run` | Read every sheet and report what would be imported, without importing |
| `mousedb-sheets status` | Per cohort: which file is its sheet, when it was edited, when it was imported, whether that worked |
| `mousedb new-sheet PROJA_07 --animals 01-16` | Create a NEW cohort's empty tracking workbook in the sheets folder, made from the project's template (the Tracking Sheets tab's "New cohort sheet..." button; `mousedb-make-sheets --new --cohort PROJA_07 --animals 01-16 --output-dir <folder>` does the same). It never writes over a file: unless the name is exactly `PROJECT_NN`, the project has a template, and that cohort has no workbook in the folder yet, it prints `REFUSED: <reason>`, writes nothing and exits with code 3. See [`docs/TRACKING_SHEETS.md`](docs/TRACKING_SHEETS.md), "Starting a new cohort" |
| `mousedb sheet-template make PROJA "<cohort workbook>" --cohort PROJA_05` | Make (or remake) a project's template -- what its new cohort sheets look like -- from one of its cohort workbooks (only read). Writes the template and a report into `<mousedb_root>/config/sheet_templates/` and records it in the lab settings; an older template is kept, renamed. See "Making or changing a project's template" in docs/TRACKING_SHEETS.md |
| `mousedb export --cohort PROJA_05` | Export to legacy Excel format |
| `mousedb export --cohort PROJA_05 --odc` | Export ODC format (calculated stats) |
| `mousedb export --cohort PROJA_05 --all-formats` | Export all formats |
| `mousedb export ... --output <path>` | Write the export to `<path>`. Refused before the database is opened (`REFUSED` / `Why` / `Instead`, exit code 1) when `<path>` is inside the tracking-sheets folder or the ASPA folder, or is named like a cohort's tracking sheet: the export would empty the workbook that is there |
| `mousedb export --unified` | Export unified reaches parquet |
| (where the files go) | Without `--output`, into `<mousedb_root>/exports/`: `<cohort>_tracking.xlsx` (the plain export), `<cohort>_ODC.xlsx` (`--odc`), `unified_reaches.parquet` (`--unified`). `--all-formats` writes the first two (not the parquet); with `--output` it is the folder they go into |
| (every `mousedb export` above) | A cohort that is still running gets an EMPTY phase in these files (`Test_Phase` in the two workbooks, `test_phase` in the unified parquet), the same rule as the nightly exports, decided from the database's reach rows; the command prints `Phase left empty (cohort still running): <cohorts>`. Why, how "running" is decided, and how to release a finished cohort's labels early: docs/WHERE_IS_MY_DATA.md, "A cohort that is still running has no phase labels yet" |
| `mousedb entry` | Launch GUI |
| `mousedb check [--cohort <cohort>] [-v] [--json]` | Data-completeness report per cohort (what each cohort is missing in the database). Like every command that opens the database, it first brings the table layout up to date (as `mousedb init` does); otherwise it only reads |
| `mousedb dump [<table>] [-o <folder>]` | Copy database tables (projects, cohorts, subjects, weights, pellet scores, ramp entries, surgeries, virus preps, audit log) to CSV files for a person to look at, by default into `<mousedb_root>/database_dump`. Apart from bringing the table layout up to date (as `mousedb init` does), the database is only read |
| `mousedb config --show` / `--set <key> <value>` / `--unset <key>` | Show, set or remove one machine-specific setting (add `--machine` for the settings file every login shares). See "Configuration" below |
| `mousedb cohort-sheets` | Show the tracking-sheets folder, the files in it that are not read as sheets, and broken pins (`--cohort <cohort>` also shows that cohort's sheet). `--set <folder>` records the folder (refused if it holds no project's sheet, or looks like a snapshot copy); `--set-aspa <folder>` records the ASPA animal-sheets folder (workbooks named for a cohort letter, `<cohort letter>.xlsx`); `--discover` looks for the folder on this computer and records it when exactly one is found. All three write YOUR OWN settings file only |
| `mousedb import-reaches` options | `--all` ignores the ledger and imports every file again (slow); `--include-processing` also reads results still in the pipeline's Processing folder; `--limit N` imports at most N files; `--force` writes even while a MouseReach watcher that writes the database is running (see "Scheduled jobs"); `--verbose` prints more |
| `mousedb-update-sheets <workbook> [--output-dir <folder>]` | Fill the `2_ODC_Animal_Tracking` tab of a tracking workbook and write the result as a COPY, `<name>_updated.xlsx`, into `--output-dir` (default: a `processed` folder in the folder you run it from). The original workbook is never changed. With no options it asks in the terminal which workbook to use. (`--all` processes only workbooks named in one fixed built-in pattern, so on most computers it finds none -- give the workbook instead) |
| `mousedb-reorganize [--dir <folder>]` | Asks in the terminal which Excel workbooks and tabs in the folder to use, and writes their pellet scores one row per pellet to `reorganized_data_long_format.csv` in that folder (replacing it). The database is not touched. Close the workbooks in Excel first |
| `mousedb-quick-export [--dir <folder>]` | Reads `generated/all_cohorts_pellet_level.csv` and `generated/all_cohorts_tray_summaries.csv` under the folder (default: the folder you run it from) and writes `unified_pellet_level.csv`, `unified_tray_level.csv` and `unified_behavioral_data.xlsx` beside them (replacing them). The database is not touched |
| `mousedb-web [--host <address>] [--port <n>]` | Start the web dashboard (docs/WATCHER_VIEWS.md). By default it listens on every network address of the computer, port 8000 (`MOUSEDB_WEB_HOST` / `MOUSEDB_WEB_PORT` change the defaults), so other computers on the network can open it; it has no login |
| `mousedb-status`, `mousedb-init`, `mousedb-new-cohort`, `mousedb-import`, `mousedb-export`, `mousedb-entry`, `mousedb-browse`, `mousedb-check`, `mousedb-video-status`, `mousedb-archive-status`, `mousedb-import-brains`, `mousedb-import-reaches`, `mousedb-import-analyses` | The same as `mousedb <command>` (for example `mousedb-check` = `mousedb check`), with the same options |
| `mousedb import-reaches --pellets-only` | Fill the per-pellet table (`video_pellets`: one row per pellet the pipeline cut each video into, reached for or not) and the per-session table (`video_sessions`: one row per tray session, including a tray at which the animal never reached) from every features file, touching nothing else. A filed video whose newer run waits in a review queue is skipped (the output counts them): it is not done, and the nightly import keeps it out of the database until the new run is filed. Run once after upgrading; the nightly import keeps it current afterwards |
| `mousedb import-reaches` | Pull new or changed MouseReach `*_features.json` results into `reach_data` (`--dry-run` to count only) |
| `mousedb import-analyses` | Mirror MouseBrain's analysis registry (exports, figures, logs, provenance) into `<mousedb_root>/exports/<analysis>`, `figures/<analysis>`, `logs/`; writes `exports/ANALYSES_MANIFEST.json` (`--dry-run` to count only). Only one runs at a time: a second one (the nightly task and the "Update the database now" button on the Where Is My Data tab (`mousedb update`) at once) waits for the first, using the lock file `logs/.import-analyses.task.lock`, and ends with `[FAIL] import-analyses not run: ...` if it waited too long |
| `mousedb import-brains --all` | Import BrainGlobe region counts, calibration runs and eLife group counts from MouseBrain's `3D_Cleared/2_Data_Summary` (`--summary-dir` to point elsewhere). A brain whose counts file changed (a recount) has its stored counts REPLACED; an unchanged one is left alone; `--update` replaces even unchanged ones (docs/WHERE_IS_MY_DATA.md, "How brain counts get here"). If anything did not land -- a brain the database refused, a file whose name holds no brain ID, the calibration runs, a brain's eLife counts -- the LAST line starts with `[FAIL]` and names each one, and the command exits 1 (so the scheduler shows the job as failed). `--csv` and `--calibration` exit 1 the same way. A brain that is refused writes nothing at all -- not even its animal or its brain row -- so fixing the cause and running the import again is all there is to do |

## Export Formats

### Legacy Excel (`--cohort PROJA_05`)
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
| `<mousedb_root>/logs/` | Audit trail (JSONL) and the import ledgers (`sheet_imports.jsonl`, `reach_imports.json`, `analysis_imports.json`), and `reach_imports_phases_pending.json` (cohorts the reach import still has to re-label; do not delete), and `reach_rows_without_file.json` (videos with reaches in the database whose results file is gone from the pipeline -- reported, never removed; shown on the Where Is My Data tab), and `brain_counts_without_file.json` (brains with final counts in the database whose counts file is not in MouseBrain's summary folder -- reported, never removed; shown on the Where Is My Data tab; an unreadable one only shows as a problem there). If `reach_imports.json` or `reach_imports_phases_pending.json` exists but cannot be read, the reach import stops with a `[FAIL]` line saying what to do (docs/WHERE_IS_MY_DATA.md, "An unreadable ledger stops the import"); an unreadable `reach_rows_without_file.json` never stops it -- it only shows as a problem on the Where Is My Data tab. Likewise, if `analysis_imports.json` exists but cannot be read, `mousedb import-analyses` stops with a `[FAIL]` line and copies or archives nothing (docs/WHERE_IS_MY_DATA.md, "The mirror's ledger") |
| `<mousedb_root>/exports/` | Generated exports; `exports/current/` is the folder people open |
| `<mousedb_root>/exports/<analysis>/`, `figures/<analysis>/` | Mirrors of MouseBrain's analysis registry (measurements, figures, `registry.json` provenance); summarized in `exports/ANALYSES_MANIFEST.json` |
| `<mousedb_root>/_archived/` | Mirrored files whose source was withdrawn -- moved here, never deleted |

## Validation Rules

| Field | Constraint | Error |
|-------|------------|-------|
| Subject ID | `PROJECT_NN_NN` format (letters, then two 2-digit numbers) | "Subject ID must be format XXX_NN_NN (e.g., CNT_05_01), got: <value>" |
| Pellet score | 0, 1, or 2 only | "Score must be 0 (miss), 1 (displaced), or 2 (retrieved), got: <value>" |
| Tray number | 1-4 | "Tray number must be 1-4" |
| Pellet number | 1-20 | "Pellet number must be 1-20" |
| Weight | 10-50g | "Weight seems too low (check units), got: <value>g" or "Weight seems too high for a mouse, got: <value>g"; 100 g or more: "Weight must be less than 100g (check units), got: <value>" |
| Sex | M or F | "Sex must be M or F" |

## Updating

If you modify the package code:
```bash
# No reinstall needed - it's installed in editable mode (-e)
# Just restart Python/GUI to pick up changes
```

**After installing new code, close and reopen every open MouseDB window**
(every person logged in to that computer). A window keeps the code it was
started with. In particular a Where Is My Data tab opened before an install
that added the current-exports lock still runs the OLD in-window export: it
takes no lock and empties the shared staging folder, so a press of
**Refresh exports now** there can spoil a nightly refresh running at the same
time. Restarted, the button runs the installed exporter as a program of its
own and respects the lock.

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

mousedb has NO built-in paths. After installing, tell it where things are.

**On a computer several people log in to, add `--machine` to every `--set`
line below.** That writes the computer's settings file
(`%ProgramData%\mousedb\config.json` on Windows, `/etc/mousedb/config.json`
elsewhere; `MOUSEDB_MACHINE_CONFIG` names another file), which everyone who
logs in reads. Without `--machine` a value is saved in your own
`~/.mousedb/config.json` and nobody else gets it: the next person to
double-click a MouseDB shortcut would be told mousedb does not know where the
snapshot is. A person's own file wins over the machine file, key by key, so
one person can still point somewhere else. Only put locations every login can
reach in the machine file (a folder inside one person's OneDrive is not one).
If Windows refuses to write the machine file, run the command from an
administrator PowerShell.

```
mousedb config --set mousedb_root <folder holding connectome.db, exports/, logs/>
mousedb config --set snapshot_dir <local folder for the parquet snapshot>
mousedb config --set mousereach_pipeline_root <MouseReach's shared pipeline folder>   # optional if ~/.mousereach/config.json exists
mousedb config --set mousebrain_pipeline_root <MouseBrain's pipeline folder>
mousedb config --set lab_name "<your lab, as it should appear in exports>"
mousedb config --set mousereach_env <MouseReach env's Scripts or bin dir>             # needed for the pipeline census (the nightly snapshot job, "Refresh pipeline view") and a by-hand bench-scan --route
mousedb config --set db_path <the database file>                                      # only if it is not <mousedb_root>/connectome.db
mousedb config --set mousebrain_registry_root <folder>                                # only if MouseBrain records its analyses somewhere other than <mousebrain_pipeline_root>/Registry
mousedb-sheets set-dir <folder holding the tracking workbooks>
mousedb config --show
```

Any command that needs a location that is not set stops with a message naming
the exact `mousedb config --set` line to run. The data-entry window
(`mousedb-entry`) opened before the database folder is set shows a smaller
window holding only the Setup tab, with that message above it: set the
folders there with **Choose...**, close the window and open it again. Values can also be given per run
as environment variables (`mousedb config --show` lists them). Order of
precedence: environment variable, then your own file, then the machine file;
`mousedb config --show` names both files and, for every key, which one its
value came from (`config file` = yours, `machine config file` = the
computer's).

**Every setting is read the same way, the sheets settings included.** The
tracking-sheets folder (`cohort_sheets_dir`), the ASPA sheets folder
(`aspa_data_dir`), the sheet pins, and the bench-scan routing settings
(`mousereach_route_cmd`, `mousereach_env`) are all read from your own file
laid over the machine file. `mousedb-sheets set-dir`, the Tracking Sheets
tab's **Set sheets folder...** and **This is the sheet** write to your own
file; to give the sheets folder to everyone who logs in to the computer
(including a scheduled job running as another login), set it in the machine
file instead:

```
mousedb config --machine --set cohort_sheets_dir "<folder holding the tracking workbooks>"
```

The ASPA sheets folder is the exception: it is set with
`mousedb cohort-sheets --set-aspa <folder>`, which writes YOUR OWN settings
file only. `aspa_data_dir` is not a `mousedb config` key, so
`mousedb config --set aspa_data_dir ...` does not work (it stops with an
error and saves nothing). To give it to every login, set the environment
variable `MOUSEDB_ASPA_DATA` machine-wide, or add `"aspa_data_dir": "<folder>"`
to the machine settings file in a text editor.

**Other environment variables** (not `mousedb config` keys, so
`mousedb config --show` does not list them):

| variable | what it changes | default |
|---|---|---|
| `MOUSEDB_DB_TIMEOUT` | how many seconds a database call waits for another program's write before it fails with "database is locked" | 600; the window (`mousedb-entry`) uses 30 so it never freezes for minutes |
| `MOUSEDB_FIELD_DEFINITIONS` | the file holding the lab's own descriptions of tracking-sheet columns, used in the data dictionaries | `~/.mousedb/sheet_field_definitions.json` |
| `MOUSEDB_VERSIONS_JSON` | the `pipeline_versions.json` that `mousedb archive-status` compares against | found through `~/.mousereach/config.json`, then `mousereach_pipeline_root` |
| `MOUSEDB_ASPA_DATA` | the ASPA animal-sheets folder (see above) | your own settings file, then the machine file |
| `MOUSEDB_WEB_HOST`, `MOUSEDB_WEB_PORT` | where `mousedb-web` listens | every network address (`0.0.0.0`), port 8000 |

**A damaged settings file is never read as empty, and never written over.**
If either file is not valid JSON (say it was edited by hand and a comma went
missing), anything that needs a setting stops with a message naming the file
-- it does not carry on as if nothing were set. `mousedb config --show`
still prints, and lists the file first under *Problems*. The data-entry
window (`mousedb-entry`) cannot open the database without its location, so
when a settings file is already damaged as it starts, the full window does
not open: a smaller window titled *mousedb cannot open its database yet*
opens instead, with a message box naming the file, and the Setup tab alone
inside it, listing the file first under its problems. (The same smaller
window opens when no database folder is set at all.) Put the file right,
close that window and open `mousedb-entry` again. If the file is damaged
while the full window is already open, the window stays open and each
button that needs a setting says so in a box. A `--set`, a
**Set sheets folder...** or a **This is the sheet** aimed at a damaged file
saves nothing: it keeps a copy beside it as `config.json.damaged-<date-time>`
and says where. Open the file in a text editor and put it right (it must
look like `{"key": "value", ...}`), or delete it and set the values again. (A
file saved by Notepad with a byte-order mark is read normally.) Each save
writes a temporary file beside the settings file and swaps it in, so a save
cut off half-way leaves the old file whole.

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

The same command also holds surgery-protocol defaults (`Contusion_Location`,
`Intended_kd`, `Anesthetic`, `Anesthetic_Dose`, `Analgesic`,
`Analgesic_Dose`, `Injection_Location`, `Depths (D/V)`, `Coordinates (M/L)`).
They are used only by `mousedb-make-sheets` run with no options (its
questions in the terminal, which build a workbook from mousedb's own built-in
layout) and by `mousedb-make-sheets --fix` when it adds a missing tab. A new
cohort sheet made with **New cohort sheet...** or `mousedb new-sheet` comes
from the project's template (docs/TRACKING_SHEETS.md, "Starting a new
cohort") and starts with whatever the template holds. Each animal's real
values are still what is typed into its sheet.

They are saved in `~/.mousedb/study_facts.json` (or the file named by
`MOUSEDB_STUDY_FACTS`). A fact that is not set is left as an empty cell in
generated sheets and exports -- never guessed.

## Scheduled jobs (the processing machine)

mousedb is an integrator: it PULLS from the tools on a schedule. Register these
with the operating system's scheduler (Task Scheduler / cron), all in the MouseDB
environment, in the order listed (the imports first, then the snapshot, which is built
from what they imported, then the bench check, which reads the snapshot):

| job | command | cadence | what it does |
|---|---|---|---|
| sheet import | `python -m mousedb.sheet_sync import --triggered-by scheduled` | daily | imports the tracking workbooks (they are filled in by hand, so more often only adds load) |
| reach import | `python -m mousedb.import_reaches` | daily | pulls new or changed MouseReach `*_features.json` results into `reach_data`, then re-derives the test-phase labels of every cohort a new video touched, writing only the rows whose label changes. Also records every pellet of each video in `video_pellets`, so the exports can tell how many pellets a tray presented, and the session itself in `video_sessions`, so a tray with no reach still gets its row in the sessions files. Saves its ledger every few minutes while it runs, so a run stopped by the time limit and restarted carries on where it stopped (docs/WHERE_IS_MY_DATA.md, "A stopped import carries on") |
| brain import | `python -m mousedb.cli import-brains --all` | daily | pulls BrainGlobe region counts, calibration runs and eLife group counts from MouseBrain's `3D_Cleared/2_Data_Summary` into the brain tables; a recounted brain's counts replace its old ones. Exits 1, with a last line starting `[FAIL]` that names what was left out, when any brain (or the calibration runs) did not land, or when it gave up waiting for the database. A brain the database refuses keeps this job failing every night until the cause is fixed -- that is on purpose |
| analysis import | `python -m mousedb.import_analyses` | daily | mirrors MouseBrain's analysis registry (exports, figures, logs, provenance) into the mousedb folders and writes `exports/ANALYSES_MANIFEST.json` |
| snapshot | `python -m mousedb.exporters.refresh_snapshot` | daily | takes the parquet snapshot of the database (all tables written aside first, then put in place together as one numbered generation -- see "The snapshot is replaced in one piece" below) and rewrites `exports/current/` (one folder per project, one per cohort inside it, and a combined file per kind per project), including the per-cohort `ODC_sessions_*.csv` (those read the database, which a successful snapshot has just shown to be readable; `MANIFEST.json` says whether they were refreshed); then, only when those exports are complete, remakes every saved limited export not marked "not nightly" (docs/WHERE_IS_MY_DATA.md, "Limited exports"; one that cannot be made also makes the job exit 1); then retakes the pipeline census the Where Is My Data tab shows (`--skip-census` to leave it), and, when the census was retaken, brings the database's record of failed recordings up to date from it (`Failed recordings imported: ...`) and rewrites `exports/missing_videos/missing_session_videos.csv` (`List of sessions without a video written: ...`) -- a failure of these last three prints `[FAIL]` but does not change the exit code. Exits 1 with a `[FAIL]` line when the snapshot or the exports could not be written, or the exports came out incomplete. `--lock-wait-minutes` (default 165) is how long it waits behind another mousedb job |
| bench-sheet check | `python -m mousedb.bench_scan` | daily | REPORTS only: finds never-reviewed pellets where the hand score and the pipeline disagree and writes the worklist to `logs/never_reviewed_worklist.json` under `mousedb_root` (`--out` to put it elsewhere). It sends nothing to review. Do not schedule it with `--route`: MouseReach now compares each video with its hand scores BEFORE filing it, so disagreements go to review on their own, and a scheduled `--route` would send finished, filed videos back to the review queue (and so out of the database). `--route` is for a deliberate run by hand only |

Cadence is a judgement about how fresh the data needs to be, not a fixed part of
the design. Pick it from how long each job actually takes on YOUR corpus: they
share one database lock, so a job scheduled more often than it takes to run will
queue behind itself, starve the others, and eventually be refused outright while
still reporting a failure. Measure first, then schedule. Run them in dependency
order with the snapshot after every import, because the snapshot is built from the
database the imports fill -- on independent clocks it will regularly rebuild
pre-import data. The bench check reads the snapshot, so it goes after that.

The cadences in the table are a starting suggestion, not a recommendation for
every installation. Where a job takes tens of minutes, daily is the right
answer; a corpus small enough that each job finishes in seconds can run them
hourly.

The snapshot refresh (and its `--import-sheets` step) and the reach import
refuse to run while a MouseReach watcher that can WRITE the database is
running on the same computer (one with `central_db` set in its
`~/.mousereach/config.json`, or `MOUSEREACH_CENTRAL_DB` set); they report that
and exit. The bench check refuses the same only when it would read the live
database (when there is no snapshot to read). A watcher that does not write
the database does not stop them. The sheet import, the brain import and the
analysis import do not check for a watcher at all.

**mousedb ships no job that checks whether the scheduled jobs succeeded.**
(The tab does show the snapshot's age, failed sheet imports and failed limited
exports.) The Where Is My Data tab and `mousedb-data-status` also read a
health report,
`<mousedb_root>/logs/health.json`, that a scheduled check of your own writes
(mousedb does not ship one). Without that file they show the problem
"nothing has checked whether the overnight jobs succeeded: no health report
at ..."; the task name that message gives is one lab's own, not something
mousedb installs. The report is a JSON file: `checked_at` (when the check ran;
older than 48 hours is reported as "has not run"), `failing` (true when a job
failed), `detail` (one line per failed run, `<job log name> | ...`) and `jobs`
(each task's `task` name and `last_result`; 267014 is reported as "stopped at
its time limit"). Without such a check that problem line stays, and the
snapshot's age line is what tells you the jobs stopped working.

### What happens when another program has the database locked

The database is a single SQLite file and anything that can see it can open it:
these jobs, this tool's own windows, and a person's analysis session -- on this
machine or any other that reaches the same file. While one of them writes, the
rest are refused with "database is locked".

Two separate mechanisms deal with that, and it is worth knowing which is which:

- **Between these jobs**: a lockfile under `<mousedb_root>/logs`
  (`mousedb/task_mutex.py`). With only `db_path` set (no `mousedb_root`) it
  goes in the `logs` folder beside the database file; with neither set a job
  stops and names the `mousedb config --set` command to run. It is never put
  in the folder a job was started from: jobs started from different folders
  would each find their own lockfile free and run together. Whoever gets it runs; the others wait and run
  straight after. They never lock each other out, however they are scheduled.
  The lock is never taken away from a job that is still running, however long
  it has held it (a long rewrite over a slow share is slow, not stuck); it is
  taken only from a holder that no longer exists, and only one waiter can take
  it (the break is a single step under a short-lived token,
  `.central-db.task.lock.breaking`). Every lock taken from a dead holder is
  written to the job's log and to `logs/.central-db.task.lock.breaks.log`. A
  job gives the lock back only if it is still its own.
  Whether a holder is still running is looked up with the `psutil` package,
  which installs with mousedb (`pip install -e .`). If it is ever missing,
  every lock is judged by its age alone -- check with
  `python -c "import psutil"` in the MouseDB environment.

  **How long a job waits.** While the job holding the lock is running AND
  getting somewhere (its CPU time and read/write count move; a holder whose
  counters are refused -- for example one started in another person's login --
  is judged by its lockfile, which a running holder touches once a minute),
  the waiting job
  waits -- up to its **wait budget** -- and says so in its log every ten
  minutes. So a slow import DELAYS the next job instead of cancelling it. It
  stops waiting, loudly, in two cases: the budget runs out, or the holder has
  shown no progress for 30 minutes (it may be hung; its lock is still never
  broken while it runs -- a person has to look at it). Either way the log gets a
  `[FAIL]` line or a `LockWaitTimeout` naming the holder, nothing was changed,
  and the job exits 1. A job that takes the lock once per item (the sheet import:
  once per cohort) does not wait again for a holder it already gave up on.
  A job whose login cannot WRITE in the lock folder does not wait at all: it
  stops at once with an error starting `the lock folder cannot be written`
  that names the folder -- no job holds the lock, so fix that login's write
  access to the folder. (On Windows a just-deleted lockfile can refuse to be
  re-created for a moment; that refusal is waited out for up to 30 seconds,
  and only while something is still at the lock's path.)

  | job | wait budget | set with |
  |---|---|---|
  | snapshot refresh | 165 minutes | `--lock-wait-minutes` |
  | snapshot refresh, waiting for another refresh of the current exports (the "Refresh exports now" button, a terminal command) | 15 minutes | fixed (`EXPORTS_LOCK_WAIT_MINUTES` in `mousedb/exporters/refresh_snapshot.py`) |
  | every other scheduled job | 40 minutes | `MOUSEDB_LOCK_WAIT_MINUTES` |
  | the GUI, and each step of "Update the database now" | 25 minutes | (a person is waiting) |

  `MOUSEDB_LOCK_WAIT_MINUTES` overrides any of them for one task. A scheduled
  task can set it in its own command, e.g.
  `cmd /c "set MOUSEDB_LOCK_WAIT_MINUTES=150&& <MouseDB env>\python.exe -m mousedb.cli import-brains --all"`.
  **Size every budget together with the task's time limit:** time limit >= wait
  budget + how long a successful run of that job takes. A budget longer than
  the time limit means the scheduler stops the job while it is still waiting,
  which loses the run just the same (and shows as "terminated", not as a
  `[FAIL]` line).
- **Against everything else**: `mousedb/db_contention.py`. A job that meets an
  outside lock waits and retries across a bounded window, then gives up, logs
  one plain-English line, and exits non-zero.

  **Size that window to the clear air after the job, minus how long the job
  takes when it succeeds** -- a retry restarts the job from the beginning, so a
  window that looks generous can leave it still running when the next job is
  due. The short imports use the 45-minute default; the snapshot refresh takes
  `--retry-minutes` (default 25) because a full refresh of a multi-gigabyte
  database over a network share can take over an hour on its own.

  **When the window starts.** For the short imports it starts when the job
  starts. For the snapshot refresh it starts the first time the refresh finds
  the database locked by another program -- NOT when the job starts -- because
  the refresh may first wait up to `--lock-wait-minutes` for an import ahead of
  it and then read for an hour, and counting that used up the whole window
  before a single retry. The refresh takes the lock between mousedb jobs once
  and keeps it through its retries and while it writes the exports, so a retry
  never queues behind another import a second time. It refuses a running
  MouseReach watcher before it starts waiting for that lock, not after.

  So size the snapshot task's time limit for the worst case: lock wait
  (`--lock-wait-minutes`, default 165) + the prefetch (reading the database
  file once to warm the cache) + the refresh that met the lock + retry
  window (`--retry-minutes`, default 25) + the refresh that succeeds + the
  wait for another refresh of the current exports (15 minutes, fixed) + the
  exports and the census. If that does not fit, lower `--lock-wait-minutes` or
  raise the limit. (The default lock wait leaves room for those 15 minutes; if you set `--lock-wait-minutes` yourself, leave room for them too.) While the refresh pauses between retries it keeps the lock,
  so another mousedb job waiting behind it may give up after 30 minutes with
  no sign of progress ("may be hung"); that only happens while another program
  keeps the database locked, which would refuse that job's own work anyway.

  Retrying only ever rescues a lock that clears in minutes. A session left open
  for an hour will outlast any window worth having, and that is what the
  snapshot-age reporting below is for -- not more waiting.

Neither mechanism ever breaks the other program's lock, and there is no option
to: a lock means a write is in flight, and taking it away corrupts the file. A
job that keeps giving up means a session somewhere is holding the database
open, and the fix is to close that session.

**Nothing re-runs a job that gave up -- so the waiting is the retry.** Task
Scheduler's "restart on failure" (*If the task fails, restart every ...*) only
re-runs a task that could not be STARTED. A job that starts and then exits
with an error is recorded as "completed with return code 1" and is never
restarted; it waits for its next scheduled time (a whole day, for a daily job).
That is why a job waits for the job ahead of it for as long as its budget
allows (above), instead of giving up after a fixed short wait and hoping to be
restarted. The setting does no harm and covers a task that cannot start at all
(a missing interpreter, the mapped drive not there yet); it is not a substitute
for the wait budget. With cron, a wrapper that re-queues on a non-zero exit is
the equivalent. When a job does give up, its log says so with `[FAIL]`; run
it by hand once the holder has finished (or press **Update the database now**
on the Where Is My Data tab).

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
read-only folder scan of 5-25 minutes, holding no database lock. **Why:** a census retaken only by hand could be weeks old yet be shown as the current counts. The tab and `mousedb-data-status` print the census's age and
flag it once it is older than `data_status.STALE_CENSUS_HOURS` (36). If the
census cannot be taken, the job prints `[FAIL] pipeline census not retaken` and
does not change the job's exit code for that alone -- the snapshot and the
exports it is there to produce are fine -- and the tab keeps the previous census
with its age shown. Pass
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
  (`odc_reaches_signature` / `_odc_reaches_csvs` in `exporters/current.py`,
  signature in `exports/current/.odc_reaches_signature.json`). The signature
  covers EVERY input those files and `analysis_EXCLUSIONS.csv` are built from:
  the content (SHA-256) of the snapshot tables `reach_data`, `subjects`,
  `pellet_scores`, `animal_records`, `video_pellets`, `video_sessions` and `weights`; the
  study-facts file, the lab's field definitions, grants, lab settings and lab
  name; the exclusion checks' outside inputs (the collage names in the
  MouseReach watcher databases, the name-audit record, which cohorts have a
  tracking workbook -- or that the folder could not be reached); and the ASPA
  workbook names. Anything unreadable is recorded as such, so the day it can be
  read the files are rebuilt. The signature is saved only after a run in which
  nothing failed; a check that could not run (listed in the manifest's
  problems) does not stop it being saved, because it follows from those same
  inputs, and it is carried over word for word when a later run is skipped. A
  skipped run says so in `MANIFEST.json` (`odc_reaches_skipped`). The exporter's
  own code is part of the signature too: `sig["output_format"]` is a SHA-256 of
  every `.py` file of the installed mousedb package (`exporter_code_hash`), so
  ANY change to the code rebuilds the files on the next run -- expect the first
  nightly run after each install of new code to rewrite every per-reach file
  (hours, within the job's limit). Nothing is bumped by hand.
  The "Refresh exports now" button runs the exporter as a fresh program
  (`python -m mousedb.exporters.current --no-wait`), so a window left open
  since before an install still exports with the installed code. A new input
  the exporter starts reading must be added to the signature in the same change.
- **When the signature HAS changed, only the cohorts that changed are rewritten**
  (`exporters/incremental.py`). Each cohort's files are kept
  when its fingerprint -- its reach rows, its animals' subject and sheet-record
  rows, its hand scores, its per-pellet rows (weights ride on the reach rows),
  what the exclusion checks say about its videos and the cohort, the
  study-facts sections that apply to it, whether it is still running, its
  folder and label, and the lab name and field definitions -- is the one
  recorded last time, AND its files on disk have the size and time recorded
  then. Otherwise all of that cohort's files are rebuilt. Each project's
  combined files are rewritten only when one of its cohorts was (or one was
  archived or failed), and keep the bytes of the old combined file up to the
  first changed cohort. Small shared files (the dictionaries,
  `analysis_EXCLUSIONS.csv`) are rewritten only when their bytes change, so
  their times -- which the limited exports go by -- only move when they do.
  The record of all this is `exports/current/.export_units.json` (the
  "ledger"), written only after every file is in place; a run that stops
  part-way leaves the old ledger, whose recorded sizes and times then no
  longer match the files that were replaced, so those cohorts are rebuilt
  next time. **A full rebuild happens when** the exporter code hash changed
  (any install of new code), the ledger is missing or unreadable, or
  `mousedb-current-exports --full` is run. Deleting the ledger is a safe way
  to force one. What each night did is in `MANIFEST.json` under
  `incremental`. A named-cohort export (`mousedb export-odc-reaches --cohort`)
  is never incremental and does not touch the ledger.
- **Checking that incremental equals full, on real data:**
  `mousedb export-incremental-check --snapshot-dir <COPY of a snapshot>
  --work <empty scratch folder> --previous <copy of last night's exports>`
  (or `--base-snapshot <copy of an earlier snapshot>` to build "last night"
  first). It builds the exports incrementally and from nothing in two folders
  under `--work`, compares every file byte for byte (MANIFEST.json without its
  times and run record), prints both times, and exits 0 only when they are
  identical. Give it COPIES: the export writes its working files beside the
  snapshot. It refuses the live exports folder as `--work`. It never reads
  connectome.db.
- **A failed or incomplete export fails the job.** If rewriting
  `exports/current/` raises, or any part of it failed (a cohort's file, a
  combined file, the exclusion checks), or `MANIFEST.json` comes out with
  `"complete": false`, the job prints `[FAIL]` and exits 1. This is on purpose: a job that only warned would look healthy while people kept reading old files.

### The snapshot is replaced in one piece

The refresh reads every table into `<snapshot_dir>/_snapshot_staging/` first.
Nothing a reader sees changes during that read, however long it takes. Only
when every table is written does it put them all in place together (renames
on the same disk: seconds) and write `<snapshot_dir>/SNAPSHOT_GENERATION.json`:
the generation id, `"state": "complete"`, and for each table its rows, size,
time and SHA-256. During the swap the stamp says `"swapping"`.

**Why:** replacing the tables one at a time as they are read would mean that a run stopped part way (a locked database, the time limit, a person) left
some tables from tonight and the rest from an earlier night, and every reader would join them as one snapshot.

What readers do (`mousedb/snapshot_generation.py`): the exports, the data
browser, the Where Is My Data tab, the bench check and the example export check
the stamp before and after they read. A new generation landing mid-read makes
them read again; a snapshot that holds two generations (a refresh stopped
during the swap, or a table replaced by something other than the refresh) is
refused with a message saying it is whole again after the next successful
refresh. `exports/current/MANIFEST.json` and the data browser's
`about_this_copy` table record the generation they were built from. A script
of your own should do the same: `snapshot_generation.read_consistent(folder,
read_function)` returns what it read and the generation id, or raises rather
than hand back a mix. Disk: the staging folder needs as much free space as the
snapshot itself while a refresh runs.
