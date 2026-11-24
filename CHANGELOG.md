# Changelog

Convention: most-recent first. Entries describe what changed in the
checked-in code, not what's planned.

## Unreleased

### Added
- **Every filed tray has a session row, also one at which the animal never
  reached** (owner, 2026-11-24: "No reaches means no REACH rows. Session rows
  should still exist in the session files."). The reach import now records
  every results file as one row of a new table, `video_sessions` (animal,
  date, tray, segments, reaches, the session's attention score, the session
  flag), in the same transaction as its reaches and pellets; `--pellets-only`
  fills it for files imported before. The snapshot carries it, and the
  exporter gives each filed tray with no reach one session-only row that only
  `sessions_<cohort>_analysis.csv` keeps: 0 reaches, 0 pellets reached for,
  and every other session number filled as on any tray (hand scores, pellets
  cut and their outcomes, percentages, attention, flag, day totals). The
  per-reach files are unchanged byte for byte. An Easy or Flat tray with no
  reach now also counts as filed when judging whether a day is complete. The
  exclusion checks judge such trays too (death, hand-score count, no sheet);
  one left out is reported in `analysis_EXCLUSIONS.csv` as a session row.
  Every sessions dictionary row says what the column holds on a tray with no
  reach.
- **A person's Flag Session note is carried as a marker** (`import_reaches`,
  per-reach exports). The features file's `session_flag` is imported onto every
  reach row (`session_flag`, `_reason`, `_by`, `_at`; added to an older
  database by `ensure_columns`) and exported as `session_flag` Yes/No with who,
  when and why. Nothing excludes or treats a flagged row differently (owner
  decision D5-10). The export fingerprint covers it.

### Changed
- **Every per-reach measurement is published as `reach_<name>`, and the
  animal-day file is `ODC_days_<cohort>.csv`** (owner's decisions,
  2026-11-19). Frames, kinematics, tracking confidence and the contact frame
  start with `reach_` in every per-reach file and dictionary (each entry names
  its old name). `ODC_sessions_*.csv` files are archived once their
  `ODC_days_*` replacement is written; a saved limited-export definition that
  names the kind `ODC_sessions` is read as `ODC_days` (`layout.KIND_ALIASES`).
- **Every per-reach row carries four levels under level-first names**
  (`mousedb/exporters/levels.py`; Where Is My Data guide, "Four levels on every
  reach row"). Every reach says what it did: the credited reach its outcome,
  every other reach at that pellet `missed` (naming the reach that moved a
  pellet answers the others), every reach at an untouched pellet `missed`;
  `Undetermined` where a pellet moved and no reach is credited (before, the
  complete record and `reach_data.csv` left non-credited reaches empty and the
  other files called the uncredited case `Missed`). The per-reach contact
  column is derived from the reach outcome (it meant "a contact frame was
  recorded", so person-picked credited reaches read `missed`). New tray
  untouched counts and day totals (pellets, retrieved, displaced, contacted,
  untouched; algorithm and hand scores, pillar trays). Columns above the reach
  are named `reach_`/`segment_`/`session_`/`day_` first; the guide has the
  old-to-new table and each dictionary entry names its old name. Changes the
  exporter code, so the next nightly export is a full rebuild.

### Added
- **The nightly export rewrites only the cohorts that changed**
  (`mousedb/exporters/incremental.py`, `odc_reaches.export(incremental=...)`,
  `layout.combine`). When anything the per-reach files are built from changed,
  every cohort's files and every combined file used to be rebuilt -- hours a
  night. Now each cohort has a fingerprint (its reach rows, animals,
  sheet records, hand scores, per-pellet rows, exclusions, study facts,
  running status, folder, and the lab-wide settings); a cohort whose
  fingerprint and files on disk match the ledger
  (`exports/current/.export_units.json`) keeps its files, and what it adds to
  the manifest, the shared dictionaries and the exclusions report is replayed
  from the ledger. A project's combined files are rewritten only when one of
  its cohorts was, keeping the old file's bytes up to the first changed
  cohort. A full rebuild happens when the exporter code changes, the ledger
  is missing, or `mousedb-current-exports --full` is run. MANIFEST.json's
  `incremental` record names the cohorts rewritten (why, and the videos
  appended, replaced and removed) and kept. `mousedb export-incremental-check`
  builds incrementally and from nothing on a snapshot copy and compares every
  file byte for byte; the test suite does the same for a new video, a new
  tray on an exported day, a changed, withdrawn or excluded video, a phase
  relabel, a cohort that is still running, a corrected hand score, a new
  measurement, a new sheet column, a new weight, an emptied cohort, a column
  type change, new code, two nights in a row, a hand-edited file and a run
  stopped before its ledger was saved.

- **Limited exports: a per-day yes/no rule, `day_window`**
  (`mousedb/exporters/subsets.py`). The yes/no column of a limited export
  could only say yes or no per animal, so a column marking which DAYS of each
  animal an analysis uses was filled by a one-off script. A rule of type
  `day_window` (`n`, `tray_type`, `before_first`) says yes on every row of
  each animal's last `n` test days -- dates with hand scores for that tray
  type in the tracking sheet -- before its cohort's first surgery of that
  type, and no on every other row; never blank. The days are chosen exactly
  as the "last N before the injury" window chooses them (one shared
  function), from the same snapshot generation as the files, also when the
  window is "all". Each file's manifest entry lists the chosen days that have
  no rows (`flag_days_with_no_rows`); the manifest's `flag_days` says how
  they were chosen. A sheet correction remakes such a slice at night.
  `--list` now also prints the rules, and a rules entry stored as null no
  longer stops a rule being saved. Guide: docs/WHERE_IS_MY_DATA.md, "The
  yes/no column".

### Fixed
- **A cohort's sessions file no longer survives a night on which all its
  reaches were left out** (`odc_reaches.export`). write_cohort archived such a
  file only when it was beside the files being written -- the staging folder,
  where an earlier night's file never is -- so the old sessions file stayed in
  the exports folder claiming sessions that were not there. A rebuilt cohort's
  files of a kind not written this run are now archived after the copy.
- **reach_data.csv and manual_scores.csv were copied into the exports folder
  twice each night** (`exporters.current`): the per-reach export copied every
  CSV in the shared staging folder, theirs included. It has its own staging
  folder now. Unchanged small files (dictionaries, the exclusions report) are
  no longer rewritten with the same bytes, so the limited exports cut from
  them are not remade for nothing.
- **`mousedb missing-videos` and `mousedb sheet-progress` take their options
  again** (`mousedb/cli.py`, `missing_videos.build_parser`,
  `session_states.build_parser`). Both subcommands handed everything after
  their name on as one block, which argparse refuses when the block starts
  with an option, so every option -- `--help`, `--cohort`, `--write`,
  `--json`, `--list`, and the `mousedb missing-videos --acknowledge ...` line
  the FIX THESE files print -- failed with "unrecognized arguments". The
  subcommands now carry the modules' own options (one parser-building
  function each, used by `mousedb <subcommand>` and by `python -m`).
  `export-subset` and `recording-issues` were checked the same way and were
  not affected. Tests: tests/test_subcommand_options.py runs the printed
  acknowledge lines as a person would.

### Changed
- **Progress is counted against the tracking sheets**
  (`mousedb/session_states.py`, new). Every hand-scored session (one animal,
  tray and day in the hand-scores file) is put in exactly one state -- filed,
  in processing, held for review (triage, deep review, quarantine, or a
  pipeline hold outside the queues such as a label mismatch), recording
  failed, or NOT ON THE SERVER YET -- from the hand-scores file, the pipeline
  census, the acknowledgements file and the pipeline's attention lists, all
  read only. Done % is filed / sheet-scored (pillar trays also on their own).
  "Not on the server yet" is red only when overdue, live and unacknowledged:
  the same sessions `mousedb missing-videos` lists. Videos the pipeline holds
  with no sheet row are listed separately, never dropped. WHY: a progress
  figure counted from the pipeline cannot see a recording that never reached
  it. The Where Is My Data tab (a line above the table and four columns),
  `mousedb-data-status` and the weekly page now give this figure; the
  census-based "finished on disk %" and the weekly page's census done % are
  gone. New command: `mousedb sheet-progress [--list] [--json] [--cohort]`.
  Guide: docs/WHERE_IS_MY_DATA.md, "How far along are we?".
- **`mousedb export` leaves the phase empty for a running cohort**
  (`mousedb/exporters/cohort_reports.py`). The nightly files already left a
  running cohort's phase empty (its labels can still change as later dates
  arrive), but the exports run by hand -- the tracking-sheet workbook, the ODC
  workbook, `--all-formats` and `--unified` -- read the database and wrote the
  stored labels. They now make the same decision
  (`odc_reaches.running_cohorts`: the lab's `Study_Status` for the cohort,
  else a last session less than 30 days old) from the database's reach rows,
  leave `Test_Phase` / `test_phase` empty, and say which cohorts they held
  back. The nightly session files pass their snapshot decision in, so one
  refresh never mixes two decisions. Guide: docs/WHERE_IS_MY_DATA.md.
- **Test suite's live-folder guard refuses writes and knows every spelling**
  (`tests/conftest.py`). A write into a real lab folder during a test (open
  for writing, file or folder creation, rename, replace, copy onto, remove,
  database connect, parquet write) now raises `LiveFolderRefused` and nothing
  is written; before, it was only reported after the file had been written.
  A real folder is recognized under every spelling (case, slashes, Windows
  short names, the server path of a mapped drive, realpath). The real folders
  are found even when the runner has redirected HOME/PROGRAMDATA and unset
  the tool variables (real home and program-data folders, saved environment
  variables in any case, and a runner list: `TEST_GUARD_REAL_ROOTS` /
  `TEST_GUARD_REAL_ROOTS_FILE`). The working copy's own files are exempt even
  when the copy sits inside a real folder; the folders beside it stay
  guarded. Guide: INSTALL.md, "The tests never touch your real data".

### Added
- **Recordings that failed** (`mousedb/recording_issues.py`,
  `mousedb recording-issues [--import]`, table `session_recording_status`).
  The reaching pipeline now sets aside a recording that is empty, a fraction
  of a second long or unreadable ("recording issue") and lists it in its
  census with the reason and its sessions. mousedb reads that list: the
  sessions are `recording_failed` (not `missing`) in the missing-videos list;
  the Where Is My Data tab and the FIX THESE file say "recording failed:
  <recording>, <reason>", acknowledged once with `mousedb missing-videos
  --acknowledge "<recording>"`; the nightly census step imports one row per
  session into the database; and each `ODC_sessions_<cohort>.csv` row carries
  `Recording_Status` and `Recording_Issue_Reason` (the per-reach files have no
  rows for such a session; the dictionary says so). Guide: WHERE_IS_MY_DATA.md,
  "A recording that failed".
- **Hand-scored sessions with no video** (`mousedb/missing_videos.py`,
  `mousedb missing-videos`). Every session the hand-scores file holds for a
  cohort with a live tracking sheet is looked for in the pipeline census;
  one more than `missing_video_grace_days` (default 3) days old at the time of
  the census with no video anywhere is shown as one red line per cohort on
  the Where Is My Data tab, as one line per missing recording in the cohort's
  FIX THESE file (what to look for and where to send it), and as a row in
  `exports/missing_videos/missing_session_videos.csv` (rewritten after every
  sheet import and after the nightly census). A gap a person cannot close is
  acknowledged once (`--acknowledge ... --by ... --reason ...`), session by
  session, so it stops being listed without silencing later gaps. Guide:
  docs/WHERE_IS_MY_DATA.md, "Sessions scored by hand that have no video".
- **Limited exports** (`mousedb export-subset`, and the **Make a limited
  export...** button on the Where Is My Data tab): a filtered copy of part of
  `exports/current` -- chosen cohorts (or a whole project), tray types,
  sessions (all; a date range; or each animal's last N sessions of one tray
  type before its cohort's first injury, from the sheet's records of the same
  snapshot generation as the files), kinds of file and columns, plus an
  optional per-animal yes/no column filled by a named rule or left blank.
  Nothing is recomputed: every value is copied unchanged. Written to
  `exports/subsets/<name>/` with the data dictionaries cut to the kept
  columns, `analysis_EXCLUSIONS.csv` filtered the same way, a
  `SUBSET_MANIFEST.json` (the definition, the source export and snapshot
  generation, row counts, the sessions chosen per animal) and a README. A
  column the full export does not have is refused by name before anything is
  written. Definitions are saved by name in the lab settings file
  (`export_subsets`, rules under `export_subset_flag_rules`), never in the
  code. Making one again moves the previous files to
  `_archived/exports_subsets/<name>/<time>/`. The manifest lists, per file,
  the chosen animal-days that have no rows. The nightly snapshot job remakes
  every saved one after a complete full export, once it has released the
  database lock; it skips one whose inputs are unchanged and keeps the files
  already there when the new ones come out byte for byte the same, so the
  archive does not fill with identical copies. A failure prints `[FAIL]`,
  fails the job and shows on the Where Is My Data tab
  (`exports/subsets/SUBSETS_STATUS.json`). "Check only" in the window checks
  without saving the definition. Guide: docs/WHERE_IS_MY_DATA.md, "Limited
  exports".
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
- The MouseDB window no longer has the **Video Pipeline** and **Archive
  Status** tabs (were 8 and 9). They laid out one computer's MouseReach
  watcher records as if they were the pipeline's state, so videos another
  computer had handled, or that waited in a review queue, read as not started
  or missing. The whole pipeline is on the Where Is My Data tab. The tabs
  after them are renumbered: **8. Tracking Sheets**, **9. Where Is My Data**,
  **10. Study Facts**. `mousedb video-status`, `mousedb archive-status` and
  the web dashboard are unchanged and still show one watcher's records, with
  the line saying whose. Guides: INSTALL.md ("GUI Features"),
  docs/WATCHER_VIEWS.md.
- Exports: no phase labels for a cohort that is still running. A session's
  phase is derived from its cohort's whole date set, so a later date could
  change the label of a session already exported, and the earlier file
  carried the old label unmarked. Every current export now leaves the phase
  empty for every session of a running cohort -- `test_phase` / `phase_group`
  in `reach_data.csv` and `manual_scores.csv`, `Test_Phase` in the ODC
  session files, `Test_Type` / `Test_Type_Grouped` in the complete record
  and the summary (`Undetermined`), `test_type`, `test_type_grouped` and
  `experiment_stage` in the analysis files (`NA`) -- and fills it the first
  export after the cohort is over. "Running" is the decision the exports
  already made for the cause of death: the cohort's own `Study_Status` in the
  study-facts file, else a last session under 30 days old. Which cohorts are
  running is now part of the per-reach skip signature, so a cohort that has
  just ended is rebuilt that night. `MANIFEST.json` lists the cohorts as
  `phase_withheld_for_running_cohorts`.
- Data dictionaries: the nine `legacy_swipe_*` values the kinematics
  extractor writes from version 2.6.0 (an earlier program's swipe measures,
  carried for continuity) each have a written definition and their real unit.
  Before, all nine read "No written definition yet", and
  `ext_legacy_swipe_speed_px_div4_per_sec` was described as seconds; its unit
  is "(pixels / 4) per second". The `extended_features` entry counts 174
  values (165 on rows from an extractor before 2.6.0, 161 before 2.3.0).
- **Analysis table (`reaches_<cohort>_analysis.csv`): the swipe names now hold
  the earlier program's swipe measures.** `swipe_length`, `swipe_breadth`,
  `swipe_area`, `swipe_speed`, `path_length` and `swipe_duration_frames` are
  that program's names, but held the pipeline's own measures, which are
  different quantities in different units. They now hold the extractor's
  `legacy_swipe_*` values (pixels; speed in (pixels / 4) per second), and
  `path_over_frames` is added beside them. The pipeline's own measures keep
  the extractor's names. To read an older file: `swipe_length` was
  `righthand_extension_past_nose_mm`, `swipe_breadth` was
  `righthand_lateral_deviation_mm`, `swipe_area` was
  `righthand_swept_area_mm2`, `swipe_speed` was
  `righthand_peak_speed_mm_per_frame`, `path_length` was
  `righthand_total_path_mm`, `swipe_duration_frames` was `duration_frames`
  (each dictionary entry says so too). A reach filed before extractor 2.6.0
  reads NA in the swipe columns, never the pipeline's measure; the columns
  are written even when no row has a value yet.
- **Reach counts per video and per testing day, each named for what it
  counts.** The per-video count `Total_Swipes_AI` (complete record) is renamed
  `Video_Reaches_AI`, same values: an earlier program's Total Swipes counted
  one animal's reaches over a whole testing day, every tray, not one video.
  In the shareable table `reaches_in_session` is renamed `reaches_in_video`,
  same values; in the analysis tables `reaches_in_session` is renamed
  `reaches_in_video` and the duplicate `total_swipes_ai` is removed (it held
  the same number). New in all three: `Day_Total_Reaches_AI` /
  `day_total_reaches`, the animal's reaches on that date over every tray of
  the day, and `Day_Total_Reaches_Complete` / `day_total_reaches_complete`,
  yes when every tray the hand-score sheet records for that animal and date
  is filed. When it is no, the total is left empty (`Undetermined`, or NA in
  the analysis tables) rather than reporting a partial count; a day with no
  hand scores reads no. In the analysis tables a day whose tray was left out
  as a known problem also reads no.
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
- Brain import: a counts file with a region whose acronym mousedb's acronym
  lookup does not know is refused (stored counts kept, `[FAIL]`, exit 1)
  when it would change what the database holds. Such a region used to be
  skipped with a warning, and because the import replaces a brain's counts
  when its file changes, its stored cells were deleted while the eLife groups
  still counted them, and the job exited 0. When the database already holds
  exactly the file's other rows it stays a `[!]` warning.
- Calibration runs: a stored run is updated only from columns the tracker's
  file actually has, and a `calibration_runs.csv` without a `status`, a
  `marked_best` (or `is_best`) or a `brain` (or `brain_id`) column is refused
  whole. A file missing those columns (an older tracker, a hand edit) used to
  turn every stored best run into "not best" and every status into "unknown"
  in one night.
- `mousedb import-brains --all` reports every brain with final counts in the
  database and no counts file in MouseBrain's summary folder (renamed or
  withdrawn in MouseBrain) -- in its output, in
  `logs/brain_counts_without_file.json` and on the Where Is My Data tab. Such
  counts used to stay in the database and every brain export unnoticed (a
  renamed brain twice). Report only: nothing is removed and the exit code is
  unchanged.
- Limited exports: a remake puts its files in place in three undoable steps
  (copy in under temporary names, archive the previous files, rename the new
  ones in, manifest and README last). A remake that failed part way (a full
  share, a file open in another program) used to have archived the previous
  slice already and left part of a new set with no manifest or README, while
  the window said nothing was changed. Now the previous files are put back;
  if even that fails, `NOT_COMPLETE.txt` in the folder and a `[FAIL]` line
  say so. The window says "Nothing was changed" only for a refusal.
- Limited-export window: a saved definition opened and saved again comes
  back as it was. A column or cohort the current exports no longer have is
  listed (marked "not in the current exports") instead of being dropped; a
  column choice whose ticks were not changed is kept exactly, order
  included, and an unreadable dictionary no longer turns it into "every
  column"; a project definition stays one while its cohort ticks are left
  as they were (and a definition naming both a project and cohorts ticks
  only its cohorts); the description is kept; a rule missing from the lab
  settings is shown, not swapped for blank.
- Limited exports: saving under a name that already holds a different
  definition is refused unless replacing is asked for (the window asks;
  the command needs `--replace`), and a replaced or forgotten definition is
  kept in the lab settings file under `export_subsets_replaced`. Typing a
  taken name used to overwrite the saved definition with no question and no
  copy.
- The lab settings file (`mousedb_lab_config.json`: sheet projects, grants,
  limited-export definitions) is saved in one locked read-change-write
  (`sheet_projects.update_settings`), through a temporary file of its own.
  Two saves at once used to end with the later one writing back what it had
  read, losing the other's change, and every writer shared one fixed
  `<file>.tmp` name.
- `mousedb export-example` writes into the live exports folder
  (`exports/current/Example`, its default) only while holding the
  current-exports lock, like every other writer of that folder: it waits for
  a running refresh (nightly job, Where Is My Data button, export command) to
  finish and keeps the next one out for the few seconds it writes. A wait
  that runs out writes nothing and exits 4 (5 when the lock folder cannot be
  written), with the same message as `mousedb export-odc-reaches`. A folder
  given with `--out-dir` outside the live exports takes no lock.
- Analysis exclusions: a MouseReach pipeline folder that is set but cannot be
  reached (share dropped, folder moved) now stops the checks, like a sheet
  folder that is set but gone. Before, every watcher database in it read as
  "absent" (a note only), the recording checks ran with no recording list,
  and a contested video was left out alone while the other animals of its
  recording went into the analysis files. The skip signature records whether
  the folder could be reached, so the files are rebuilt once it can.
- Study facts: a damaged study-facts file (not valid JSON) is refused by
  every reader and writer, naming the file, instead of reading as "nothing
  set". Exports used to write the study-fact columns blank without a word,
  and the next Set wrote back a file holding only that one fact, losing every
  other project's facts and exception rules. Saves now go through the same
  writer as the other settings files: a damaged file is never written over
  (a copy is kept as `<name>.damaged-<time>`), and a new file is swapped in
  whole. The Study Facts tab shows the damaged file in red and saves nothing.
- Sheet import, animal details: a tab that reads only part of its animals
  (more than 5 and more than a quarter of those the database holds details
  for from it are left out) is kept as it was and reported as
  `NOT SYNCED animal details '<tab>'`, naming the left-out animals, like a
  tab that reads as empty. It used to replace the cohort's details, so the
  left-out animals' details vanished without a warning. The two numbers are
  now one setting shared with the importer's own broken-read guard.
- `mousedb-import-aspa-scores --apply` now waits for the cross-task database
  lock and, once its cohorts are imported, rewrites the hand-scores file and
  the FIX THESE files, like `mousedb-sheets import --cohort ASPA_xx`. It
  used to do neither, so the reaching pipeline compared videos with the
  previous hand scores until the next scheduled import. A hand-scores file
  that could not be rewritten makes the command exit 1.
- Sheet import: a contusion row's `Survived` cell corrected from `N` to `Y`
  now also brings the animal back (active, no date of death), listed under
  `death_undone` and in the audit log. It used to correct only the
  surgery's flag, leaving the animal dead on the day of a surgery the
  database said it survived. Not when another contusion row still says `N`,
  nor when `0a_Metadata` gives the animal a date of death: then a warning
  names the two cells that disagree.
- Sheet import: an animal the database holds, that its cohort's sheet no
  longer names and that nothing is recorded for (an id typed into the sheet,
  imported, then taken out again) is named in a warning, on the cohort's
  line, in the red attention box and in the cohort's FIX THESE file. It used
  to stay in the cohort silently. It is still never removed by an import.
- Sheet import: a contusion's weight on the day (`Subject_Weight (g)`) is
  stored on the contusion (`surgeries.pre_surgery_weight_grams`), as an
  injection's always was, and kept in step with the sheet. Contusions
  imported earlier are filled in by the next import (a fill, so never a
  refusal).
- Sheet import: a contusion's `Survived` cell is stored on the contusion
  (`surgeries.survived`), and kept in step with the sheet like its other
  readings. It used to only mark the animal dead, so every contusion read as
  survived. A blank or unreadable cell leaves the stored flag alone. The
  first import after this turns each `N` contusion from 1 to 0; that counts
  as an overwrite, so a cohort with many deaths can be refused once
  (`NOT SYNCED surgeries`) and then needs `--allow-large-removal`.
- A damaged settings file (`~/.mousedb/config.json` or the machine file, not
  valid JSON) is no longer read as empty and then written back holding one
  value: anything that needs a setting stops with a message naming the file,
  `mousedb config --show` still prints and lists it first under Problems, and
  a `--set`, set-dir or pin saves nothing, keeping a copy as
  `config.json.damaged-<date-time>`. Before, every other setting and every pin
  in it was lost at the next save. A file with a byte-order mark is now read,
  and every save is a temporary file swapped in (mousedb/config.py).
- `mousedb-entry` no longer dies with a traceback (or shows nothing, when
  started from a shortcut) when the settings cannot give it the database --
  a damaged settings file, or no database folder set. It opens a window
  holding only the Setup tab, with the reason above it and in a message box;
  once the setting is put right, it is closed and opened again
  (mousedb/gui/app.py, `open_main_window`).
- The tracking-sheets folder, the pins, the ASPA folder and the bench-scan
  routing settings are read from your own settings laid over the machine
  file, like every other setting. Before, they were read from your own file
  only, so a sheets folder set with `mousedb config --machine --set
  cohort_sheets_dir` was invisible to every other login. Writes still go to
  your own file only.
- A tracking-sheets (or ASPA) folder that is set but cannot be reached -- a
  drive not connected, a renamed folder -- is said as such, with the folder
  set, by the Tracking Sheets tab, `mousedb-sheets status` / `import`,
  `mousedb cohort-sheets` and `mousedb new-sheet`. Before, it was reported as
  "no tracking-sheet folder is configured", sending people to set again a
  setting that was right. The reasons (not set / unreachable / no sheet in it
  / settings file damaged) come from `cohort_sheets.cohort_sheets_dir_state`.
  With a damaged settings file, `mousedb-sheets status` and `import` (the
  scheduled sheet import) now name the file in one sentence -- and any
  `mousedb-sheets` command that still needs a location from it stops with
  one `[FAIL]` line and exit 1 -- instead of ending in a traceback.
- Brain import: a brain's counts follow its counts file. The import only
  added counts a brain did not have yet, so a brain recounted (for example
  after its registration was corrected) kept its first counts in the
  database, marked final, and nothing said so. Each run now compares every
  brain's file with the database and, when they differ, replaces that brain's
  region counts and eLife groups together in one transaction; each brain's
  line says `added`, `unchanged` or `REPLACED`. A file changed in the last
  two minutes, unreadable, without region rows, or cut off (no line break at
  its end, a region row without a readable count, or -- for a file cut off
  between two rows -- another total or other regions than MouseBrain's
  `region_counts.csv` written after it) is left for the next run with the
  stored counts kept; a file that is gone removes nothing. The
  `region_counts.csv` comparison refuses only a file that would change what
  the database holds: for a brain whose stored counts already equal the
  file it prints a `[!]` line instead, so an unchanged brain never fails the
  run. Two counts files
  naming one brain are neither imported (`[FAIL]` names both). When a
  recounted brain's groups cannot be had -- its row is missing from
  `elife_counts.csv`, that file looks cut off, or it was last written before
  the counts file, so its row is from the earlier count -- the old groups
  are removed with the old counts and the run ends with `[FAIL]`; when the
  counts did not change, the stored groups stay. Warnings (`Unknown
  acronym`: a region left out) are printed under each brain's line. Not
  detected: a stale row in an `elife_counts.csv` that was rewritten later for
  another brain, and a counts file cut off between two rows when step 6
  stopped before writing `region_counts.csv`.
  `--update` now replaces in that same single transaction (it used to delete
  first and import after, so a failed import left the brain with nothing).
  The first run after this change replaces every brain recounted since its
  first import.
- Brain import: calibration runs follow the tracker. A run already in the
  database was skipped by every later import, so a run first imported while
  `started`, or before it was marked best, kept that state for good; it is now
  updated from `calibration_runs.csv` (status, cells found, duration, best,
  settings, date). The importer also reads the tracker's own column names
  (`det_ball_xy`, `det_threshold`, `duration_seconds`, `marked_best`,
  `created_at` ...); it read names the tracker never writes, so every run
  arrived without its detection settings, duration or date and was never
  marked best. Runs without a tracker id are left as they are. The first
  run after this change updates nearly every stored run once (its date,
  and often its duration, settings or best flag); `mousedb import-brains
  --all --dry-run` shows the count as `calibration_runs_updated`.
- Brain import: a brain that is refused writes nothing. A counts file whose
  columns could not be read was refused only after a placeholder animal and
  the brain's row had been added, and those were saved although the import
  printed `[FAIL]`. Every brain import (counts, eLife groups, calibration runs,
  detected cells) now saves all of its rows or none, and a dry run saves none.
- Data browser: the brain region and group tables no longer show
  `registration_approved`. No import fills it, so it was 0 for every brain and
  read as "no registration was approved"; its description said 1 meant a
  person approved it. The descriptions of `brain_samples.registration_approved`
  and `status` now say what they really hold (never filled; `imported` for
  every brain).
- Cohort Setup's **Replace** refuses an archive or snapshot copy, a "Copy of
  ..." or an export, as Import already did, and the refusal is shown before the
  preview. Before, Replace used such a file and put its old values back over the
  database's.
- Where Is My Data: an analysis with no approved method is shown as "no approved
  method" instead of every current output counted as stale, and an analysis
  whose record the tool set aside as damaged says so on its line ("[!] N damaged
  manifest(s) set aside") instead of just showing fewer entries.
- A read of the database no longer fails right after another program was
  stopped part-way through saving (`attempt to write a readonly database`):
  the hand-scores file and the bench disagreement scan now undo the stopped
  save and then read, without being able to change anything (mousedb/db_read.py).
  The bench scan used to take that failure for 'this database has no
  per-pellet table' and go on without those rows, saying nothing.
- Sheet import follow-ups (docs/TRACKING_SHEETS.md):
  - Cohort Setup's **Import** and **Replace** end like every other import:
    the animal details are copied, and the hand-scores file and the FIX THESE
    files are rewritten (one after-import step for every route, also run by
    `mousedb import --file`). Import goes through the same route as
    `--file`, so it now refuses an archive copy. FIX THESE files are written
    only into the configured sheets folder.
  - With several files matching a cohort and none pinned, the import only
    adds for that cohort and leaves its animal details as they were; the
    cohort is NOT SYNCED until a file is pinned. Nothing stored is changed,
    moved or removed on such a guess (adding is still done: new rows and a
    weight's empty % of body weight; the planning tab's note waits for the
    pin too, because once written it is never replaced) -- the move of scores stored under a
    year-typo or weekend date (the rows read are still re-dated, decided
    tray by tray: only a tray the import still has stored under the wrong
    date, and not under the right one, is read as typed -- a score typed in
    by a person under that date does not count -- so no tray is stored
    twice), the removal of a copied virus batch, the replacement of a
    placeholder note or start, and a death from `Survived` are each HELD
    BACK and listed -- and the import does not count as the cohort's scores
    import for the hand-scores file (`scores_saved` false), so videos whose
    session the guessed file lacks wait instead of being filed unchecked.
    The closing line tells the person to pin a file.
  - The animal details replace only the tabs really read; a tab that reads as
    empty, or is missing or renamed, while the database holds some of its
    details, or whose table the import refused, is left as it was and
    reported with the refusals (with `--allow-large-removal` a gone tab's
    details are removed; Cohort Setup's Replace takes a tab that is there
    but empty as empty, and still keeps and reports a missing one). The
    letter workbooks' `ODC` pass and
    `mousedb-import-aspa-scores` report a kept tab and fail too, and Cohort
    Setup's Replace no longer says "Import complete" over one.
  - A weight's percentage of body weight is corrected when the sheet's
    changes (compared in one unit: 87 and 0.87 are the same).
  - Each import-history line records `scores_saved`; the hand-scores file
    counts an import whose scores were saved even when a later step (the
    animal details) failed, so those videos no longer wait. A Replace whose
    import-history line could not be written now says so in its box
    (**Replace saved, with problems**, or **Replace undone** for one that
    was undone) instead of losing the line in silence.
  - The hand-scores file's list of cohorts with a sheet only grows, and only
    an import of the configured folder adds to it or counts as an import of
    every sheet; its manifest carries `scores_sha256` of the scores file (so
    a reader can check the pair belongs together; a reader that does not look
    at it detects nothing new), and
    both renames are retried briefly while the file is open elsewhere. A
    manifest that cannot be read (after retries) or is damaged fails the
    write and leaves both files as they were, instead of being taken as
    empty and shrinking that list. The whole write (manifest read, database
    read, both files) is done by one writer at a time, under a lock file
    beside them, so two imports finishing together cannot drop each other's
    cohorts from that list or leave a scores file that does not match its
    manifest's hash.
- `mousedb import-analyses`: a run that stops before it finishes -- an
  unreadable ledger, a lock that did not come free in time, a registry folder
  it cannot reach -- now shows on the Where Is My Data tab as `[!] analysis
  mirror: the last run stopped before mirroring anything (<time>): <reason>`
  until a run finishes. Such a run writes no manifest, so the tab used to show
  the last good analysis lines with the reason only in the task log. The stop
  is kept in `logs/analysis_import_last_failure.json`, written whole or not at
  all, and removed by the next run that finishes. It is written and removed
  only while the mirror's lock is held (a run that gave up waiting for the
  lock records that itself), and a record the tab cannot look at shows as
  `[!] analysis mirror: the record of its last stopped run cannot be read`,
  never as "no run stopped".
- `mousedb import-analyses`: when a file is to be archived but the mirror's own
  copy cannot be looked at (a share or permission error), the run now reports
  an error and keeps the file in its ledger. It used to read that as "already
  gone" and drop it from the ledger, so the copy was never archived.
- The "stale vs approved" count of a tissue analysis (Where Is My Data tab,
  `mousedb-data-status`, the import's own output) now shows the date the approved
  method it is judged against was recorded in MouseBrain's registry:
  `... stale vs approved (approved method as recorded <date>)`, or `... as
  recorded at an unknown date -- registry written before the date was kept)`
  for a registry an older MouseBrain wrote. The manifest
  row carries it as `approved_method_recorded_at`. An analysis with no
  approved method shows no date, only `no approved method`.
- `mousedb import-analyses` runs one at a time: it takes its own lock
  (`logs/.import-analyses.task.lock`), so the nightly task and the "Update
  the database now" button on the Where Is My Data tab (`mousedb update`)
  can no longer run it together and drop each other's
  ledger entries. A run that cannot get the lock in time ends with
  `[FAIL] import-analyses not run: ...` and changes nothing. `--dry-run`
  takes no lock.
- `mousedb import-analyses`: an analysis it keeps back instead of archiving is
  now an error with its reason, and the Where Is My Data tab says why, when it
  is held because its folder has no `registry.json` while the mirror holds its
  files, its folder cannot be listed, its missing folder could not be confirmed
  gone, no analysis was listed at all, or a ledger line names no analysis. A
  mirror copy that cannot itself be looked at when it is due to be archived is
  kept too, but that shows only in the job's output, as a `[FAIL] archive
  <path>` line and exit code 1, not on the tab. A run
  that listed no analysis at all used to leave the tab with no analysis lines
  and no reason; it now shows a `[FAIL]` line per analysis the mirror holds and
  an `[!] analysis mirror: ...` line (the manifest's new top-level `problems`).
  An analysis folder missing from the listing whose absence could not be
  confirmed was kept with exit code 0 and no message; it is now an error naming
  the analysis.
- `mousedb import-analyses` archives a mirrored file only when it is sure the
  file was withdrawn: its analysis was listed in full this run, or the
  analysis folder is gone. It used to archive every mirrored file of an
  analysis whose `registry.json` was not seen on one run, and listing errors
  read as "no files". Now an analysis folder without `registry.json` whose
  files the mirror holds, a folder that cannot be listed, or a run that lists
  no registry at all while the mirror holds files, archives nothing and fails
  with a `[FAIL]` line. A folder without `registry.json` of which the mirror
  holds nothing -- judged from the mirror's own folders as well as the
  ledger, so a new or set-aside ledger cannot hide it (MouseBrain makes the
  folder before anything is registered in it) -- is a `[note]`, not an error.
  Files kept under the same name on the mousedb side that nothing marks as
  the mirror of that folder do not count as "of it": they are left alone and
  the `[note]` says so.
- `mousedb import-analyses --confirm-withdrawn <analysis>` (repeatable):
  archives, through the usual archive step, every mirrored file of an
  analysis that MouseBrain no longer has, where the run would otherwise hold
  it back (every analysis gone at once, a folder left without its
  `registry.json`, files withdrawn before a new ledger was started). For an
  analysis still registered only the files MouseBrain dropped are archived;
  for a folder left without its `registry.json`, everything the mirror holds
  of it, even files still at the source. Refuses, with nothing moved, a
  name nothing identifies as an analysis -- a mistyped name, or the name of
  a folder or log mousedb keeps for another purpose in the same places. A
  name counts as an analysis when the ledger lists it, when MouseBrain's
  registry has a folder of that name that is registered or that the mirror
  holds a copy from, or when the mirror holds its `registry.json` copy; the
  nightly run holds a folder without `registry.json`, and names this option,
  by the same rule. Also refuses a path and a source it cannot list. An
  analysis whose mirror
  folders cannot be listed is held whole -- nothing of it is archived -- with a
  `[FAIL]` line that says so.
- `mousedb import-analyses --set-aside-ledger`: keeps an unusable ledger
  (not JSON, or lines that name no analysis) beside the new one as
  `analysis_imports.json.set-aside-<time>` and starts a new ledger. The
  messages and docs no longer tell anyone to move mirrored files or rename
  the ledger by hand.
- `mousedb import-analyses`: its ledger (`logs/analysis_imports.json`) and
  `exports/ANALYSES_MANIFEST.json` are written whole or not at all, and a
  ledger that is there but cannot be read stops the run with a `[FAIL]` line
  naming the file and what to do. It used to be read as empty: the run then
  started a new ledger and could never archive a file MouseBrain withdrew.
  The ledger reader and the atomic writer the reach import already used now
  live in `mousedb/state_files.py`, shared by both imports.
- `mousedb import-reaches` reports every video that has rows in the database
  but no results file in the pipeline and is not in a review queue -- in its
  output (before the summary line), in `logs/reach_rows_without_file.json`
  and on the Where Is My Data tab. Such rows (a results file deleted or moved
  away without going through review) used to stay in the database and the
  exports unnoticed. Report only: nothing is removed and the exit code is
  unchanged. A results file renamed without re-processing is matched by the
  file name its rows record, so it is not listed while it is in the pipeline.
- `mousedb import-reaches --pellets-only` skips a filed video whose newer run
  waits in a review queue, as the import itself does, and says how many it
  skipped. It used to write that video's filed pellet answers -- the ones
  being reviewed -- back into `video_pellets`, where they stayed in the
  exports until the next import took them out. Rows such a video already
  has are left for the import, which copies them aside before removing them.
- A save whose automatic re-derivation of a cohort's test phases fails (in the
  MouseDB window, or a sheet import) no longer leaves the labels wrong with only
  a console line to show for it: the cohort goes on the reach import's list of
  cohorts still to re-label (`logs/reach_imports_phases_pending.json`), so the
  next nightly import re-derives it; the window shows a warning box; and a
  sheet import made from the window records the failure in its ledger line, as
  the scheduled one does. Both routes now end that line the same way: `the
  next reach import re-labels it` when the cohort is on that list, and `run
  'mousedb backfill-phases'` only when it could not be put there. The reach
  import now adds to that list instead of overwriting it, so a cohort put
  there while it runs is kept -- including a cohort saved while the import is
  re-labelling that same cohort, which stays on the list for the next run.
  Every change to that list (by the import, or by the window) is one locked
  read-and-write, so a cohort the window adds between the import's read and
  its write is no longer overwritten. The list is now written as
  `{cohort: times queued}`; a plain list written by an older version is still
  read. Only the configured database's list is used; a database opened on
  another file never writes to it.
- `mousedb import-reaches`: a ledger (`logs/reach_imports.json`) or a list of
  cohorts still to re-label (`logs/reach_imports_phases_pending.json`) that is
  there but cannot be read when the run starts now stops the run with a
  `[FAIL]` line naming the file and what to do, and exit code 1. It used to be
  read as empty, so the run silently imported every results file again
  (hours, under the database lock) or forgot which cohorts still needed their
  phases re-derived. A read that fails with a share error is retried for
  about ten seconds first. A ledger that does not exist yet still means
  "import everything", and the run now says so -- but only when its folder
  can be listed and the listing shows no ledger, and never for a ledger that
  is there but empty. A mapped network drive that is briefly unreachable
  makes Windows answer "network path not found", which reads exactly like a
  missing file; that answer alone no longer starts a full re-import, and no
  longer empties the list of cohorts still to re-label (a save that adds a
  cohort to the list now refuses, and says so, instead of replacing the list
  with that one cohort). The list is read again at every save during the
  run, so one that becomes unreadable then stops the run part-way and says
  so; the ledger is read only at the start.
- Where Is My Data tab: Refresh and "Refresh exports now" pressed while
  another job of the tab is running now say "A refresh is still running" and
  start nothing (they did nothing silently, and the exports button first
  wrote that it was rewriting). A finished job's result now arrives after its
  thread has stopped, so the refresh an Update starts when it ends is no
  longer turned away.
- Current exports: a cohort that no longer has any reaches has its reach
  files archived (never deleted) and leaves its project's combined files; a
  project with no cohort file of a kind left has that combined file archived.
  The old files used to stay, looking current, and the combined file was
  built from whatever was on disk. Not done for a cohort whose build failed,
  or when the snapshot's reach table is empty. A file that cannot be moved
  (open in another program) is a failure for that cohort only: everything
  else is still written, and the next run tries again. The first refresh
  after this change archives any such files already there.
- Analysis exclusions fail closed. A watcher database that exists but cannot
  be read, a name-audit record that does not parse, or a tracking-sheet folder
  that is set but cannot be reached used to skip its check (a collage
  database with no message at all), so the entries it would have left out
  went into the analysis files and the nightly job stayed green. Now the
  per-reach cohort files are not rewritten (the previous ones stay), the export is
  incomplete and the job fails, naming the input. An input that does not
  exist is still a note; a missing central watcher database is now noted too.
  A watcher database that is only busy is waited for and read again (three
  tries) before it counts as unreadable; one without its table of recordings
  counts as unreadable, and the message gives the database's own error.
- Current exports: the exporter's own version is no longer a number bumped by
  hand. It is a SHA-256 of the installed package's code, so any code change
  rebuilds the per-reach files on the next run (a change made without bumping
  the number used to be skipped, and the old files kept looking current). The
  first nightly run after each install rewrites every per-reach file. The
  "Refresh exports now" button runs the exporter as a fresh program, so a
  window opened before an install never exports with the old code.
- Current exports: only one refresh writes `exports/current` at a time. The
  nightly job, the "Refresh exports now" button, `mousedb-current-exports` and
  `mousedb export-odc-reaches` (into the live folder) shared one staging folder
  and fixed temporary names with no lock, so two at once could delete or
  publish each other's half-written files. They now take a lock
  (`.current-exports.task.lock` in mousedb's task-lock folder;
  `mousedb export-odc-reaches` takes it whenever it writes into the live
  folder, also when `--out-dir` names that folder): the nightly
  job and the commands wait while the other refresh is working, up to their
  wait budget (15 minutes for the nightly job -- a bound of its own, taken out
  of its database-lock wait, whose default is now 165 minutes instead of 180,
  so the job's worst case still fits its time limit; 40 for the commands;
  30 minutes with no progress counts as stuck, judged by the holder's
  lockfile heartbeat when its CPU and read/write counters are refused), then
  stop with nothing changed -- the nightly job
  with `[FAIL]`, a command with a one-line `[!]` message naming the exports
  lock and exit code 4 (it used to be a traceback that named the database-task
  lock). The button says "already being written -- try again when it
  finishes" and changes nothing, and shows a warning with the exporter's
  output when a refresh it started did not finish cleanly;
  `mousedb-current-exports --no-wait` stops at once with exit code 3. Restart
  every open MouseDB window after installing: one opened before still runs
  the old unlocked export.
- Task locks: a lockfile that Windows refuses to create because the previous
  one's delete is still pending is "not free yet", and the waiter tries again
  (it used to end the wait with an error) -- but only for up to 30 seconds and
  only while something is still at the lock's path. A lock folder this login
  cannot write is an error at once (`the lock folder cannot be written`,
  naming the folder), never "busy" and never a full wait;
  `mousedb-current-exports` exits 5 for it, and the button says the folder
  cannot be written instead of "already being written".
  The button also waits out a pending delete (the lockfile, still there,
  cannot even be read) instead of saying "already being written" just after
  the previous refresh finished.
- Where Is My Data: "Refresh exports now" starts its exporter program in the
  Python environment's own folder, not the window's working folder, so a
  window opened from a folder holding a mousedb source tree cannot make the
  program run that tree instead of the installed code. A broken or released lock is
  logged under its own name (a broken current-exports lock was logged as the
  database-task lock).
- Current exports: when the per-reach skip record
  (`.odc_reaches_signature.json`) has to be removed and cannot be (another
  program has it open), `MANIFEST.json` lists it as a problem. The failure
  used to be ignored silently, night after night.
- Docs: a busy watcher database holds up the analysis exports for a little
  over two minutes per database before it counts as unreadable (about four
  and a half per refresh, which reads it twice), not "about a minute and a
  half". The Where Is My Data quick guide now says what the exports button
  does when another refresh is writing or when a refresh did not finish.
- Protocol Manager: Save on an existing protocol no longer throws phase
  changes away while saying "updated". A name/description change is still
  saved in place; a phase change (added, removed or edited, including
  fields the table does not show) is offered as a new protocol, which keeps
  the phases' hidden details, needs a name of its own, and leaves the original
  untouched. Declining saves nothing and says so. The phases table can no
  longer be typed into (a value typed into a cell used to be dropped on Save);
  phases are changed with Edit Phase, and Save compares what the table shows.
  Guide: docs/PROTOCOL_MANAGER.md.
- Videos held for review (triage, deep review) and videos this computer has
  no file for (unresolvable) are no longer counted as "In Pipeline" /
  "In Progress" in the Video Pipeline and Archive Status tabs and in
  `mousedb video-status` / `archive-status`. They have their own counts (In
  Review, Not on this computer); crystallized videos count as completed; a
  watcher state mousedb does not know is listed under its own name instead of
  being folded into a count. `--json` gains `in_review`, `not_on_this_node`,
  `other` (and `in_progress` for video-status). The Video Pipeline tab gains
  Quarantined and Other cards, and its By Cohort and By Animal tables (and
  the command's tables) gain In Progress, Not Here, Quarantined and Other
  columns, so on every card row and table row the counts add up to the total:
  a quarantined video was counted nowhere. The web dashboard gains In Review,
  Quarantined and Not on this computer cards. `video-status --json` gains
  `quarantined` (summary, cohorts, animals) and `failed` (summary). The
  Archive Status tab and `mousedb archive-status` no longer show quarantined
  videos as Failed (a quarantined video is held until a person fixes the
  recording; nothing failed): they get their own Quarantined count, and tab 9
  gains Quarantined and Other cards and columns and the command's per-animal
  table Quarant and Other columns, so every row adds up to its Total.
  `archive-status --json` gains `quarantined` (summary, cohorts, animals).
  Guide: docs/WATCHER_VIEWS.md, "What each count means".
- The Video Pipeline and Archive Status tabs, `mousedb video-status`,
  `mousedb archive-status` and the web dashboard read the watcher database
  the watcher actually writes: a `db_path` set in the `watcher` section of
  `~/.mousereach/config.json` now comes before `<processing_root>/watcher.db`
  (the same order MouseReach uses). They read the default file before, which
  on a computer set up that way is an old copy. Each view now also says, above
  its numbers, that these are this computer's watcher records only, names the
  file and when it was last written, and points to the Where Is My Data tab for
  the whole pipeline. When the file was found through MOUSEDB_WATCHER_DB or
  mousedb's own pipeline folder instead of this computer's MouseReach
  settings, the line says so and that it may be another watcher's, instead of
  calling it this computer's. `--json` gains `scope` (which of the two) and
  `watcher_db_last_written`. On the web dashboard that line is re-fetched
  every 30 seconds with the counts, so its "last written" time stays current.
  Guide: docs/WATCHER_VIEWS.md.
- "Update the database now" / `mousedb update`: the snapshot step is no
  longer stopped after an hour. A full refresh can take longer than that on
  a large database, so the update could never finish and left the current
  exports half rewritten. The import steps keep their one-hour limit; the
  snapshot step's is 12 hours, only a backstop against a hung refresh. The
  window's text no longer says the update takes "a few minutes".
- "Update the database now" / `mousedb update`: only one update can run. Its
  lock was checked and then written, so two presses at once could both run;
  and it was taken over after 2 hours whatever the first update was doing,
  which then deleted the second one's lock. The lock
  (`logs/.update.task.lock`) is now taken in one step, kept while the update's
  program is running, and taken over at once only when that program is gone.
  An update started on another computer cannot be looked up from the computer
  where the button is pressed, so if it dies its place is taken over only
  after 45 minutes without its once-a-minute touch; until then a press is
  refused and names that computer.
  The old `update.lock` is still written, and touched every minute while the
  update runs, so a window running an earlier version (which takes over an
  `update.lock` older than 2 hours) refuses for the whole update.
  An update started by an earlier version is respected while its program
  runs on this computer, and otherwise for up to 2 hours from its start: its
  `update.lock` does not name its computer, so a program missing here may be
  running on another one.
- The snapshot refresh's `--retry-minutes` window now starts at the first
  "database is locked", not when the job starts. Time spent waiting for an
  import to finish (up to `--lock-wait-minutes`) used to spend the window, so
  the first outside lock afterwards failed the night's refresh at once while
  the message said it had waited the whole window. The refresh also takes the
  lock between mousedb jobs once and keeps it through its retries, so a retry
  no longer queues behind an import a second time; the per-cohort session
  exports are written under that lock too. Size the task's time limit as lock
  wait + the prefetch (reading the database file once to warm the cache) +
  two refreshes + retry window + exports and census (INSTALL.md).
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
