# Changelog

Convention: most-recent first. Entries describe what changed in the
checked-in code, not what's planned.

## Unreleased

### Added
- `mousedb-make-shortcuts`: puts **MouseDB** (the window) and **MouseDB -
  Browse the data** on the Desktop and in a MouseDB folder of the Start menu,
  for you, or with `--all-users` for everyone who logs in to the computer
  (administrator). They run `pythonw -m mousedb.gui` / `-m mousedb.data_browser`,
  never an exe shim. `python -m mousedb.gui` is new: the window without a
  console, with a message box (and `%LOCALAPPDATA%\mousedb\gui.log`) when it
  cannot start.
- A machine settings file shared by every login on a computer
  (`%ProgramData%\mousedb\config.json`; `mousedb config --machine --set ...`).
  A person's own `~/.mousedb/config.json` still wins key by key. Without it,
  everyone but the person who installed mousedb was told "mousedb does not
  know where the snapshot is" on their first click.
- Data browser: the **Browse the data** button on the Where Is My Data tab (and
  `mousedb-data-browser`, and a desktop shortcut it can make) opens every table
  the database holds in the web browser, read-only, with sort, facets, filters,
  CSV of what is on screen, a description on every column (from the data
  dictionaries), ready-made tables (animals, sessions, reaches, brain region and
  group counts) and three fill-in questions. It shows each person's private
  copy of the snapshot, never the live database, and holds no snapshot file
  open, so it can be left open overnight. Served by Datasette on 127.0.0.1 only;
  optional extra `pip install -e ".[browse]"`. Guide: docs/DATA_BROWSER.md.

### Changed
- **The sheet wins.** The tracking-sheet import no longer only adds: for the
  rows earlier imports of a cohort's sheet wrote (pellet scores, weights, ramp
  days, surgeries) it corrects a value the sheet now gives differently and
  removes a row the sheet no longer holds -- a session moved to another
  cohort's workbook, or rows a copied workbook carried over from the previous
  cohort (and a death such a copied contusion row recorded is undone).
  Rows typed in by hand are never touched (a warning names any that
  disagree with the sheet); animals are never removed. Every change and
  removal is in the import's output, in its ledger line (`sheet_sync`, kept
  whole) and in the audit log, with the old values. A sheet that reads as
  broken (a tab missing or unreadable, nothing parsed, or more than a quarter
  of the cohort's rows gone, or their stored values overwritten -- filling
  an empty value never counts) removes and changes nothing and says
  so: a `NOT SYNCED <table>` warning, exit code 1, the cohort shown as NOT
  SYNCED in the Tracking Sheets and Where Is My Data tabs, and an item in the
  attention box and the FIX THESE file. `mousedb-sheets import --cohort <id>
  --allow-large-removal` lets one checked cohort through. Cohort Setup's
  **Import (add-only)** button is now **Import (sheet wins)**, and its preview
  shows what would change. Docs: TRACKING_SHEETS.md "The sheet wins",
  WHERE_IS_MY_DATA.md "The database follows the sheet".
- Scheduled jobs wait for the database-task lock while the job holding it is
  running and making progress, up to a wait budget (snapshot refresh 180
  minutes, `--lock-wait-minutes`; other jobs 40; the GUI and the update
  button's steps 25; `MOUSEDB_LOCK_WAIT_MINUTES` overrides), reporting every ten
  minutes, then fail loudly with `LockWaitTimeout`. A holder with no progress
  for 30 minutes is given up on early (never broken). The old fixed 25-minute
  wait lost a whole night whenever the job ahead ran long: the scheduler's
  "restart on failure" never re-runs a job that started and then failed.
- The snapshot is replaced as one generation: every table is written to
  `_snapshot_staging` first, then all are moved into place together and
  `SNAPSHOT_GENERATION.json` records the generation (state, and each table's
  rows, size, time and SHA-256). `mousedb.snapshot_generation.read_consistent`
  lets a reader prove it read one generation; the exports, the data browser,
  the Where Is My Data tab, the bench check and the example export use it, and
  refuse a snapshot left half-replaced. `MANIFEST.json` and the data browser's
  `about_this_copy` name the generation.
- The current-exports skip signature covers every input of the per-reach files
  and `analysis_EXCLUSIONS.csv`: snapshot tables by content (now including
  `video_pellets` and `weights`), the lab's field definitions, settings and
  name, the collage names, the name-audit record, the sheets folder and the
  ASPA workbook names (`output_format` 18). The data-dictionary note is a
  manifest `notes` entry, not a problem, so the signature can be saved again.
- The snapshot also copies `projects`, `surgeries`, `brain_samples`,
  `region_counts` and `elife_region_counts` (small tables the data browser
  shows; no export reads them).

### Fixed
- "Update the database now" / `mousedb update`: only one update can run. Its
  lock was checked and then written, so two presses at once could both run;
  and it was taken over after 2 hours whatever the first update was doing,
  which then deleted the second one's lock. The lock
  (`logs/.update.task.lock`) is now taken in one step, kept while the update's
  program is running, and taken over at once only when that program is gone.
- The snapshot refresh's `--retry-minutes` window now starts at the first
  "database is locked", not when the job starts. Time spent waiting for an
  import to finish (up to `--lock-wait-minutes`) used to spend the window, so
  the first outside lock afterwards failed the night's refresh at once while
  the message said it had waited the whole window. Size the task's time limit
  as lock wait + retry window + run (INSTALL.md).
- The database-task lock no longer lands in the working directory when
  `mousedb_root` is unset: with only `db_path` set it goes in the `logs`
  folder beside the database, and with neither set the job stops with the
  command to run. Jobs started from different folders used to each find
  their own lockfile free and run against the database together.
- `psutil` is now a declared dependency (it was only installed by chance).
  The database-task lock, the watcher check before a snapshot and the bench
  scan use it to see whether a process is still running; an environment built
  without it judged every lock by age alone.
- `mousedb <command>` now exits with the code the command returns. It used to
  drop it, so `import-brains` exited 0 after giving up on a locked database,
  and the scheduler and the "Update the database now" button showed success.
  `export-example` with nothing written now exits 1 too.
- `import-brains` exits 1 when a brain, the calibration runs, or a brain's
  eLife counts did not land, or a counts file's name holds no brain ID, and
  ends with one line starting `[FAIL]` that names each of them. Before, a
  refused brain was marked `[FAIL]` only in the middle of its own line and the
  job exited 0.
- A session removed from a cohort's sheet left its date in the cohort's
  schedule, so the cohort's phases were derived around a session that no
  longer exists (baseline read as training, early post-injury tests as
  baseline) in the database and every export. The import now re-derives a
  cohort's phases in the same run when its dates change, reports how many
  pellet and reach rows were relabelled (`phases_rederived`), and fails the
  cohort loudly if that re-derivation fails. The exports pick the new labels up
  at the next refresh (the skip signature hashes the tables' content).
- An existing animal's date of birth, date of death and sex on the sheet's
  `0a_Metadata` tab never reached the database (the tab was read only for
  animals being created). Each import now fills those fields where the
  database holds nothing, logs every fill in the audit log, and never
  overwrites a value already there: a difference is a warning, an item in the
  attention box and in the FIX THESE file, for a person to settle. A date of
  death later than today is not taken.
- An animal first created from a video keeps its "auto-created" note no
  longer: the first sheet import that names it replaces the note, so the data
  status stops counting it as "created from video only".
- Task mutex: breaking a dead holder's lock is one step under an exclusive
  break token, so two waiters can no longer both take the lock; `release()`
  removes the lockfile only if it still carries this job's identity; every
  break is logged (job log and `logs/.central-db.task.lock.breaks.log`). A lock
  held on another computer is no longer judged dead by its pid.
- The snapshot job exits 1 with `[FAIL]` when rewriting the exports failed or
  came out incomplete (it printed `[warn]` and exited 0). A part of the export
  that failed now makes `MANIFEST.json` `complete` false.
- `mousedb import-reaches` no longer imports a filed video whose newer run
  waits in a review queue. It used to import the old filed results and then
  take them out again in the same run, every night, re-labelling the cohort
  each time. Such a video is now left out and not recorded as imported (one
  summary line; `--verbose` names them), and its new run is imported once it
  is filed.
- `mousedb import-reaches` saves its ledger every few minutes during a run,
  not only at the end, so a run stopped by the scheduled task's time limit and
  restarted carries on where it stopped instead of importing every file again.
  A video enters the ledger only after its rows are committed; every save is
  written beside the ledger and swapped in whole, so a stop mid-save cannot
  corrupt it (a corrupt ledger used to mean a full re-import); the cohorts
  still to re-label are kept in `logs/reach_imports_phases_pending.json` until
  done, so a run stopped before re-labelling has the next run do it; and a
  video going back to review loses its ledger entry before its rows are
  deleted, so a stop in between can no longer leave it skipped when it is
  filed again.
- The snapshot refresh no longer fails when a reader has a table file open at
  the moment it swaps the new one in (on Windows a replace fails while the file
  is open). It waits up to two minutes for the reader to let go.

### Added
- `mousedb-weekly-report` writes a dated "Where is my data" HTML page (and a
  LATEST copy): videos by stage per project and cohort, why pillar-tray sessions
  have not started, finish estimates, machine status, the database table and
  known problems. Meant for a weekly scheduled task.

### Fixed
- `paw_leading_point` holds the name of a paw point, and the analysis file read it
  as a number, so it came out NA on every row. The analysis file now writes the name,
  and every data dictionary calls the column text and lists its four values.

### Changed
- The data dictionary now describes the path measures as MouseReach kinematics
  extractor 2.3.0 computes them: the path starts and ends at an anchor point
  calculated between the nose and the slit edge, not a detected hand position.
  Covers peak, mean and apex wrist speed, straightness, smoothness and every
  per-landmark path measure. The apex frame and extension past the nose are now
  said to use detected frames only. The new `anchor_*` columns have definitions,
  and the analysis file writes their values as words rather than NA.

### Added
- `reach_data` has an index on `(subject_id, session_date)`
  (`ix_reach_data_subject_session`). The phase re-labelling after each reach
  import selects a cohort's rows by subject and read the whole table once per
  touched cohort without it. New databases get it from the schema; an existing
  one gets it on the next `mousedb import-reaches` (about a minute, once, under
  the import's database lock).
- The bench scan compares every hand-scored pellet, including segments where the
  pipeline found no reach (from `video_pellets`), at the outcome detector
  version MouseReach declares current (read from `pipeline_versions.json`).
- `mousedb import-analyses` (`mousedb.import_analyses`, console script
  `mousedb-import-analyses`): mirrors MouseBrain's analysis registry
  (exports, figures, logs, `registry.json` provenance) into the mousedb
  folders, archives (never deletes) files withdrawn upstream, and writes
  `exports/ANALYSES_MANIFEST.json`. Files only -- no database writes.
- `mousedb-data-status` and the Where Is My Data tab show one line per
  mirrored analysis (current / stale vs approved / invalidated / imported).
- Console script `mousedb-import-brains` (the wrapper existed, the script
  entry did not).

### Fixed
- `mousedb import-brains` without `--summary-dir` raised NameError (a removed
  module constant). The default now comes from
  `mousedb config --set mousebrain_pipeline_root`.
- eLife group counts silently never imported when `mousebrain` was not
  importable. They are now read from MouseBrain's own
  `2_Data_Summary/elife_counts.csv`; when neither that file nor the
  recompute is possible the run prints an explicit error and exits 1.
- Top-level `README.md`, `CHANGELOG.md`, and `.github/workflows/test.yml` —
  human-facing project orientation and CI scaffolding that previously
  lived only in subpackages or AGENTS.md.
- `tests/test_validators.py` — first unit-test coverage for
  `mousedb.validators`. The module is the front line for every import path
  (ID format, weight ranges, pellet scores, surgery types, tray bounds);
  prior coverage was zero.
- `[tool.pytest.ini_options]` block in `pyproject.toml`: explicit
  `testpaths`, `addopts = "--strict-markers -ra"`, and `norecursedirs` so
  pytest from the repo root doesn't collect stray scripts or cache dirs.
- `[tool.coverage]` config (source + omit + exclude_lines) for opt-in
  `pytest --cov=mousedb` runs.
- `nbclient`, `nbformat`, `papermill`, `pytest-cov` added to the `dev`
  optional-dependency set so the endpoint_ck_analysis smoke test can run
  against a fresh dev install.

### Changed
- `requires-python` bumped to `>=3.10` to match the
  endpoint_ck_analysis sibling and drop the EOL'd 3.9.
- Author email placeholder removed from `pyproject.toml`.

### Renamed
- `test_setup.py` -> `manual_setup_check.py`. The original name made
  pytest auto-discover it during collection, but the script has
  interactive `input()` calls in its body and would hang any CI run.
  Renaming removes the landmine without losing the manual setup smoke.

### Removed
- Stale `unified_data.egg-info/` and `mousedb.egg-info/` directories from
  the working tree. Both were already gitignored; this just clears the
  on-disk clutter so contributors don't think `unified_data` is still a
  package name.

### Fixed (pre-cleanup, included for context)
- `helpers/models.py`: `wald_test_terms().table` column lookup updated for
  statsmodels 0.14+ (which renamed `"P>chi2"` -> `"pvalue"`). Affected
  every per-feature LMM in notebook 05; pre-fix output was all-NaN.
- `endpoint_ck_analysis` notebook 07's interaction-LMM cell received the
  same fix.
