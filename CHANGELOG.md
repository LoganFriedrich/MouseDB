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
- `mousedb import-analyses` runs one at a time: it takes its own lock
  (`logs/.import-analyses.task.lock`), so the nightly task and the Update
  everything button can no longer run it together and drop each other's
  ledger entries. A run that cannot get the lock in time ends with
  `[FAIL] import-analyses not run: ...` and changes nothing. `--dry-run`
  takes no lock.
- `mousedb import-analyses` archives a mirrored file only when it is sure the
  file was withdrawn: its analysis was listed in full this run, or the
  analysis folder is gone. It used to archive every mirrored file of an
  analysis whose `registry.json` was not seen on one run, and listing errors
  read as "no files". Now an analysis folder without `registry.json`, a
  folder that cannot be listed, or a run that lists no registry at all while
  the mirror holds files, archives nothing and fails with a `[FAIL]` line.
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
