# Where is my data?

This page is for the person doing the work. No codebase knowledge assumed.

**Just want to look?** The **Browse the data** button on the Where Is My Data
tab opens every table the database holds in your web browser -- sort, filter,
save as CSV, no code. See [Browse the data](DATA_BROWSER.md). The files below
are what you keep, analyze and send.

## The short answer

`<mousedb_root>/exports/current/` (run `mousedb config --show` to see the
folder on this machine) -- rewritten by the scheduled job with everything the
database currently holds, as CSV files you can open in Excel, R or Python,
each with a data dictionary beside it:

| file | one row per | definitions |
|---|---|---|
| `reach_data.csv` | reach the pipeline detected (kinematics, the pellet outcome of its segment, and where that outcome came from) | `reach_data_DATA_DICTIONARY.csv` |
| `manual_scores.csv` | pellet scored by hand from the tray (0 untouched / 1 displaced / 2 retrieved) with the session's phase -- on every tray, easy and flat included | `manual_scores_DATA_DICTIONARY.csv` |
| `ODC_sessions_<cohort>.csv` | animal per session, in the ODC-SCI `2_ODC_Animal_Tracking` shape (per-tray and daily counts and percentages, weight, injury) | `ODC_sessions_DATA_DICTIONARY.csv` |
| `ODC_reaches_<cohort>.csv` | reach -- **the complete record**: every measurement held for that reach, that animal's details (strain, sex, every surgery column of the tracking sheet), that session's details, the tray totals, AND how the result was produced (tool versions, which machine, who reviewed it) | `ODC_reaches_DATA_DICTIONARY.csv` |
| `reaches_<cohort>_summary.csv` | reach -- **the shareable table**: the same reaches and every measurement of them, with the process columns removed and everything named in plain words | `reaches_summary_DATA_DICTIONARY.csv` |
| `reaches_<cohort>_analysis.csv` | reach -- **the analysis table**: the same reaches in the shape a statistics package wants (numbers in numeric columns, `NA` for absent, the session's totals on every row). An entry with an identified problem is left out of it until its source is fixed | `reaches_analysis_DATA_DICTIONARY.csv` |
| `sessions_<cohort>_analysis.csv` | animal per session, totalled from the analysis table. Every percentage (retrieved, displaced, contacted, missed; algorithm and hand scored) is out of `pellets_presented`, the tray's 20. Two columns MEASURE it: `pellets_in_video` (how many pellets the pipeline found in the video) and `pellets_hand_scored` (how many pellets of that tray have a hand score); both should be 20, and a session where either is not is treated as an error (see below). `pellets_reached_for` counts the pellets the animal reached for at least once and is never a denominator | `sessions_analysis_DATA_DICTIONARY.csv` |
| `analysis_EXCLUSIONS.csv` | entry left out of the analysis tables, and why | -- |
| `MANIFEST.json` | -- | when the files were written, from which snapshot (`snapshot_generation`), row counts, any problems, notes that need no action, and whether the per-reach files were kept from an earlier run because nothing they are built from changed (`odc_reaches_skipped`) |
| `README.txt` | -- | the same explanation as this table |

### How the folder is arranged

```
exports/current/
    reach_data.csv, manual_scores.csv     every project at once
    *_DATA_DICTIONARY.csv                 one per kind of file, for every project and cohort
    analysis_EXCLUSIONS.csv, MANIFEST.json, README.txt
    GrantA/                               a grant's projects sit together (see below)
        PROJA/                            one folder per project
            ODC_reaches_PROJA_all_cohorts.csv   every PROJA cohort in one file, one per kind
            reaches_PROJA_all_cohorts_summary.csv, reaches_PROJA_all_cohorts_analysis.csv,
            sessions_PROJA_all_cohorts_analysis.csv, ODC_sessions_PROJA_all_cohorts.csv
            PROJA_05/                     one folder per cohort
                ODC_reaches_PROJA_05.csv, reaches_PROJA_05_summary.csv,
                reaches_PROJA_05_analysis.csv, sessions_PROJA_05_analysis.csv,
                ODC_sessions_PROJA_05.csv
        PROJAB/                           the grant's second project, laid out the same way
    PROJC/                                a project in no grant: at the top, laid out the same way
```

- **One cohort:** open its folder, e.g. `GrantA/PROJA/PROJA_05/`. The cohort
  is in every file name as well, so a file copied out of its folder still says
  which cohort it is.
- **Grants.** One grant can fund several projects (two wings of one study);
  its projects' folders sit inside the grant's folder. Which projects belong to
  which grant is set once for the whole lab, in the lab settings file
  (`<mousedb_root>/config/mousedb_lab_config.json`):

  ```bash
  mousedb grants                                  # what is set
  mousedb grants --set GrantA PROJA PROJAB        # GrantA holds PROJA and PROJAB
  mousedb grants --set GrantA                     # remove GrantA (its projects go back to the top)
  ```

  A project can be in one grant only; a grant name must be usable as a folder
  name (letters, digits, `-`, `_`). The next export writes the files in the new
  place and moves the project's previous folder into
  `<mousedb_root>/_archived/exports_current/<date>_grant_layout/` -- moved,
  never deleted, so nothing old looks current. A project in no grant keeps its
  folder at the top.
- **A whole project at once:** the `*_all_cohorts*` files beside the cohort
  folders. Each is that project's cohort files of one kind put one after the
  other, in cohort order, after all of them have been written: the same rows and
  the same cells, nothing recomputed. Two things differ, both on purpose:
  - a column only some cohorts' files have (a surgery detail one cohort's sheet
    records) is filled for the other cohorts with `Not recorded` (`NA` in the
    analysis files). `MANIFEST.json` lists, under the combined file, which
    columns were filled for which cohorts;
  - `entry_index` in the analysis files is counted again over the combined rows
    (it is a row counter for the file it is in, never an id).
- **Project folders are by exact project code.** A project whose code starts
  with another's (`PROJAB` beside `PROJA`) has its own folder and its own
  combined files; it is never folded into the shorter one.
- **Sizes.** A project's combined complete record can run to gigabytes: open
  it in R or Python, not Excel. The per-cohort files are what
  Excel can manage.
- **In a script or notebook, never write a file's path out -- ask mousedb for it.**
  A path written into a script breaks the next time the folder is rearranged
  (one did on 2026-10-29). In the MouseDB environment:

  ```python
  from mousedb.exporters.layout import locate
  path = locate("reaches_analysis", "CNT_05")   # one cohort's file
  path = locate("reaches_analysis", "CNT")      # the whole project, combined
  ```

  The kinds are `ODC_reaches`, `reaches_summary`, `reaches_analysis`,
  `sessions_analysis` and `ODC_sessions`. If the file has not been written yet,
  it stops with a message saying which file and where it looked.
- Until 2026-10-29 every cohort's file sat at the top of the folder. Those are
  moved to `<mousedb_root>/_archived/exports_current/` (never deleted) the first
  time the new layout is written.
- **A cohort that no longer has any reaches loses its reach files.** If every
  reach of a cohort leaves the database -- a cohort invented by a typo in a
  video name and since renamed, or videos withdrawn back into review -- its
  four reach files (`ODC_reaches_`, `reaches_..._summary`, `reaches_..._analysis`,
  `sessions_..._analysis`) are moved to
  `<mousedb_root>/_archived/exports_current/<date>_cohorts_without_reaches/`
  (never deleted) on the next refresh, and the project's combined files are
  rebuilt without it. If no cohort of a project is left, its combined files go
  to the archive too (`<date>_combined_without_cohorts`). `MANIFEST.json`
  lists what was moved (`archived_cohorts_without_reaches`,
  `archived_combined_files`). Before, the old files stayed and looked current,
  and the combined file kept counting them. Two cases never archive anything:
  a cohort whose files failed to build this time (the failure is reported and
  its old files stay), and a snapshot with no reaches at all (that is a broken
  snapshot, not a lab with no reaches). A one-cohort export (`--cohort`)
  archives only a named cohort that came out empty. If one of those files is
  open in another program (Excel, for example) it cannot be moved: that cohort
  is listed under `failures` in `MANIFEST.json` ("could not all be moved to the
  archive -- close any program that has one open"), its files stay and are
  still counted in the combined file, and the job reports itself as failed.
  Everything else -- the other cohorts, every project's combined files, the
  dictionaries -- is still written. Close the file; the next nightly run, or
  **Refresh exports now**, moves it.

**A tray presents 20 pellets; anything else is an error, and it is handled like one.** A video the pipeline cut into some other number of pellets has the wrong pellet number on its reaches, so that session is left out of the analysis files. A tray whose hand scores number other than 20 keeps its reaches but has its hand-score columns left empty. Both are listed in `analysis_EXCLUSIONS.csv` with the count that was found, and both come back on their own once the video is reprocessed or the sheet is corrected.

**A recording whose name the archive's own cuts contradict is left out whole.** Some archives kept their own single-animal cut of every seat beside each recording, so every animal is named twice, typed separately. MouseReach's name audit (`mousereach-aspa-name-audit`) traces each of those cuts to the recording it came from and writes the recordings whose seats the cuts name for an animal the recording's name does not list (`pipeline_records/archive_name_conflicts.json` in the MouseReach pipeline folder). Every video cut from such a recording is left out of the analysis files -- whose video each seat holds is in doubt -- and listed in `analysis_EXCLUSIONS.csv` with the cut names that disagree. Nothing is renamed: once a person has said which name is right and the recording has been brought in under it, rerunning the audit brings the videos back. With no audit record, `MANIFEST.json` says the check did not run.

**A check that cannot read its input stops the analysis files; a check whose input does not exist says so.** The "left out" checks read three things outside the snapshot: the recording names in MouseReach's watcher databases, the name-audit record above, and the tracking-sheet folder. If one of them is simply not there -- no audit record yet, no sheet folder set -- that check does not run, and `MANIFEST.json` lists it under problems ("... not run"). With no central watcher database the recording checks still run, from the local watcher database only; `MANIFEST.json` lists that under problems ("recording checks (identity, collision) ran without the central watcher database ..."), because a recording known only to the central database cannot be checked. If one of them IS there but cannot be read tonight -- a watcher database that is damaged, missing its table of recordings, or still locked after being tried three times, which takes a little over two minutes per database (a watcher writing at that moment is simply waited for; a refresh reads the watcher databases twice, so one that stays locked can hold the refresh up for about four and a half minutes per database before it gives up), an audit record cut off half-written, a sheet folder that is set but on a drive that cannot be reached -- no per-reach cohort file is rewritten -- not the analysis files (`reaches_*_analysis.csv`, `sessions_*_analysis.csv`), nor the complete record and summary written with them, nor `analysis_EXCLUSIONS.csv`: the previous ones stay, `MANIFEST.json` says `"complete": false` with the reason under `failures`, and the nightly job reports itself as failed. Writing them anyway would have put the entries that check exists to leave out in front of the people analysing them. What to do: the message names the file or folder. Reconnect the drive or fix the setting (`mousedb cohort-sheets --set <folder>`), or rerun `mousereach-aspa-name-audit` to rewrite a damaged record; the next nightly run, or **Refresh exports now**, then writes the files.

**One dictionary per kind of file, not one per cohort.** Every cohort's file of
a kind has the same columns, so one dictionary describes them all -- the
project's combined file included. The dictionaries sit at the top of the
folder; send the one for the kind alongside whichever cohorts' files you send. The one exception is the complete
record: a cohort's tracking sheet can record surgery details another cohort's
does not, so some of its columns exist only in some cohorts' files. The
dictionary describes every one of them, and `MANIFEST.json` lists, under
`ODC_reaches_DATA_DICTIONARY.csv`, which cohorts have each such column (the
problems list says how many there are). That is a difference between the
sheets, to be resolved there; it does not stop an upload, because every
column in every file still has its definition.

### Which of the two per-reach files do I send someone?

**`reaches_<cohort>_summary.csv`.** The other one is for you.

They contain the same reaches. The difference is what each is *for*:

- The **complete record** describes the reach AND how we arrived at it: which
  version of each algorithm ran, which machine did it, which person reviewed
  it, what the algorithm had said before they corrected it. That is how you
  audit your own pipeline, and it is nobody else's business.
- The **shareable table** answers the question a collaborator actually has and
  nothing else. It contains no tool versions, no file paths, no machine names,
  no reviewer names, and none of the columns that no code computes.

**What is trimmed is the process, not the measurements.** Every measurement held
for a reach is in the shareable table -- not a selection of them. The familiar
ones come first, under plain names (`extension_past_nose_mm`,
`peak_speed_mm_per_frame`), and the rest of the per-paw-point block follows.

That is deliberate, and it was the other way round for a while. Which measures
matter is the collaborator's question to ask; a measure left out is one they
cannot ask about at all, and they would have to come back for it or re-derive it
from video nobody is sending them. So the trimming is of columns that describe
*our* work -- how the number was produced, by which version, checked by whom --
not of the numbers themselves. Every column, including each measurement, has its
own row in the dictionary beside it, with its unit and what it means.

Two columns in the shareable table carry the point:

| column | what it says |
|---|---|
| `reach_result` | what THIS reach did to the pellet: `Missed`, `Displaced into the scoring area`, `Displaced out of reach`, `Retrieved`, `Undetermined`, or `Not applicable` (easy and flat trays, below) |
| `pellet_result` | what became of the pellet this reach was aimed at, repeated on every reach aimed at it, so you never have to join anything: `Retrieved`, `Displaced into the scoring area`, `Displaced out of reach`, `Never touched`, `Moved by something other than a reach` (a person reviewing the video saw it; no reach is credited, so every reach at that pellet reads `Missed`), `Undetermined` or `Not applicable` |

`Missed` is an **answer**, not a missing value: the animal reached and the
pellet stayed where it was, because a different reach decided its fate or
because none did. It is not a claim about contact -- whether the paw touched a
pellet it did not move is not measured.

**Easy and flat training trays have no pellet answers.** On those trays the
pellets are not tracked, no outcome is computed, and the video is not cut into
one stretch per pellet. So on every reach from one of them, every column that
asks about a pellet -- `reach_result`, `pellet_result`, which reach decided it,
the pellet number, how many reaches were aimed at it, the per-video pellet
counts -- reads `Not applicable` (in the analysis files, a number column reads
`NA` instead, which is those files' word for an absent number). The reach
itself, its measurements and the session's attention score are all there. Use
`tray_type` (P = pillar, E = easy, F = flat) to pick the pillar rows when you
want pellet outcomes. The same holds in `reach_data.csv` and in the database
itself: on easy and flat trays every column holding the ALGORITHM's pellet or
reach outcome is empty in the database and reads `Not applicable` in the files.
The HAND scores are different: they are kept exactly as scored on every tray.

**Pellet words and reach words.** A pellet is retrieved, displaced or untouched;
a reach retrieved it, displaced it, or missed it. So the database's per-pellet
contact columns (`segment_contact_group`, and `contact_group` in the hand scores)
say `untouched` for a pellet nothing moved, and only the per-reach
`contact_group` says `missed` (since 2026-10-26; before that the pellet columns
said `missed` too).

`reach_result` never says where the verdict came from. A verdict is a verdict;
whether a human or an algorithm produced it is in the complete record.

### No cell is ever empty -- and the word tells you why

ODC-SCI rejects a dataset with empty cells, so both files fill every one. The
filler is not a single "NA", because a blank means four different things and
treating them alike would throw away what you actually know:

| you will see | it means |
|---|---|
| `Not applicable` | the question cannot apply to this row -- days-post-injury for a session that happened before the injury; every pellet column on an easy or flat training tray |
| `Not measured` | no code computes this value (the column exists so the column set stays stable) |
| `Not recorded` | a source should carry it and does not -- nobody wrote it in the sheet |
| `Undetermined` | in `reach_result` / `pellet_result`: a pellet that reached the files without anyone deciding it. That should not happen -- a pellet nobody decided keeps its video in review -- and until 2026-10-26 it did, because the review window let a reviewer move on without picking an outcome and counted that as an answer. Those videos are sent back to review, and leave the exports until they are answered (see "Only filed videos are in the database" below) |

If a column you expected is full of `Not recorded`, that is a gap in the
records, not a bug: something has to be entered in the tracking sheet, or
declared once with `mousedb study-facts` (below). `MANIFEST.json` lists any
animal column blank for a whole cohort, so you can see it without opening the
files.

Where the `ODC_reaches` animal columns come from: each sheet import copies
the animal-level tabs of the tracking sheet (metadata, contusion, spinal
injection; a frozen letter cohort's `ODC` tab) into the database as written,
and study-wide facts no sheet holds (strain, supplier, study leader, injury
device) come from `mousedb study-facts`. A value no source records is never
guessed; `MANIFEST.json` lists any animal column that is blank for a whole
cohort. To write them for one cohort into another folder:
`mousedb export-odc-reaches --cohort <cohort id> --out-dir <folder>`.

### Filling a column that reads `Not recorded` everywhere

Some facts are true of every animal in a study and appear in no sheet -- the
species, the supplier, who led the study. Declare each one **once** and it
fills that column in every file, for every cohort, forever:

```
mousedb study-facts --show                                   # what is set now
mousedb study-facts --set <PROJECT> SpeciesTyp "<species>"
mousedb study-facts --set default AnimalSourceNam "<supplier>"
```

`default` applies to every project; a project's own value wins over it. A
recorded value in a sheet always beats both -- these only fill gaps.

**When the fact has exceptions.** A study fact is often only *nearly*
constant: a colony can be one strain except for the animals on a transgenic
line. Rather than leaving the column wrong or blank, add an exception rule to
the study-facts file (`mousedb study-facts --show` prints its path). A rule
says: *when this text appears anywhere in an animal's recorded values, this
column takes this value.*

```json
"<PROJECT>": {
  "SpeciesStrainTyp": "<the usual strain>",
  "_rules": [
    {"field": "SpeciesStrainTyp",
     "value": "<the strain for the exceptional animals>",
     "when_any_value_contains": "<text that marks them in the sheets>"}
  ]
}
```

Useful to know:

- Matching ignores capitals, because sheets are typed by hand.
- Add `"in_fields": ["<column>", "<column>"]` to search only named columns --
  safer when the text might appear in a comment.
- Later rules win, so a broad rule can be followed by a narrower exception.
- A rule only ever fires on values that were actually **imported**. If the
  marker lives in a sheet tab the importer does not read, the rule is correct
  and still does nothing. Check with `mousedb study-facts --show` plus a look
  at the exported column.
- The same mechanism fills a column that FOLLOWS from another, such as the
  spinal level of an injury following from the injury type.

Nothing about your lab belongs in the code. The tool supplies the mechanism;
the file supplies your facts.

**ASPA file names.** A frozen ASPA cohort's files are named `..._ASPA_NN_X`,
e.g. `ODC_reaches_ASPA_04_D.csv`: `X` is the cohort letter the lab knows it by
(its workbook and folder), `NN` is the same cohort as the pipeline encodes it
(the letter's position in the alphabet). `MANIFEST.json` names the workbook
each one came from. Files written before this naming are moved (not deleted)
to `<mousedb_root>/_archived/exports_current/`.

An ODC-SCI submission is a dataset file **plus** its data dictionary; both
are here. `MANIFEST.json` says `"complete": true` when every column in every
file has a dictionary entry and every file was written -- if it says false, the
problems list names the undocumented columns (an upload would be rejected until
they are added) or the file that failed (listed again under `failures`). The
nightly job then reports itself as failed, so it shows up as a failing job too.

**Every file here comes from one snapshot.** The snapshot is replaced in one
piece each night, and `MANIFEST.json` names the generation the files were built
from. If the snapshot was left half-replaced (a refresh stopped in the middle
of the swap), the exports are not rewritten at all -- the files stay those of
the last good night -- and the job says so.

**When the big per-reach files are not rewritten.** They are kept from the
previous night when nothing they are built from has changed: the reaches, the
animals, the hand scores, the sheet records, the per-pellet table, the weights,
the lab's settings, everything the "left out" checks look at, and mousedb's
own code. A change to any one of those rebuilds them, so a corrected entry
always comes back the next night -- and the first night after new mousedb code
is installed rewrites every one of them, which takes hours. `MANIFEST.json` says when the files were kept
(`odc_reaches_skipped`).

## The longer answer: the "Where Is My Data" tab

Open the mousedb GUI (double-click **MouseDB** on the desktop, or Start menu ->
**MouseDB** -> **MouseDB**; from a terminal: MouseDB environment active, then
`mousedb-entry`) and find **11. Where Is My Data**. One row per cohort:

| column | meaning |
|---|---|
| Animals | animals the database knows for the cohort. `[N from video only]` means N of them were created from a video before the tracking sheet named them -- import the sheet to fill in their details. Once the sheet names such an animal, the next import replaces its "created from video" note and it stops being counted here |
| Sheet | the tracking sheet's import status (worked in the **Tracking Sheets** tab): Up to date / Sheet edited since last import / Never imported / LAST IMPORT FAILED / NOT SYNCED (red: the last import found the sheet looking broken and removed and changed nothing -- see below) |
| Sessions scored | hand-scored animal-days in the database |
| Videos in DB | videos whose reaches are in the database (i.e. are in `reach_data.csv`) |
| In review (triage / deep) | videos waiting for a person in MouseReach's review queues. **Their data is not final until they are reviewed and released.** They are worked in MouseReach (its Review Queues tab) |
| Outcomes algo / human | pellet outcomes resting on the algorithm alone vs confirmed or corrected by a person |
| Reaches | rows the cohort contributes to `reach_data.csv` |

Below the table: the export folder, when it was last written, whether it is
complete for an ODC upload, the row count of each file, and any problems.

Buttons: **Refresh** re-reads everything. **Open exports folder** opens the
folder above in Explorer. **Refresh exports now** rewrites `reach_data.csv`,
`manual_scores.csv`, every cohort's four reach and session-analysis files and
each project's combined files of those kinds, immediately from the latest
snapshot. It takes as long as the scheduled run's reach part: hours for the
whole lab. The per-cohort ODC session files, and their combined file, refresh
only on the scheduled run, which is the only time the database may be read
safely.

**One job of the tab at a time.** Refresh, Update the database now, Refresh
pipeline view and Refresh exports now each run in the background while the
window stays usable. Pressing any of them while another is still running
starts nothing and shows a box: "A refresh is still running -- try again in a
moment". When a job finishes, the tab re-reads itself.

**Only one refresh writes the exports at a time.** The nightly job, this
button and the terminal commands all write the same folder, and two at once
would spoil each other's half-written files. So each takes a lock first: the
file `.current-exports.task.lock` in the folder mousedb keeps its task locks in
(normally `<mousedb_root>/logs`). What each one does when another refresh is
already writing:

- **Refresh exports now** does not wait. A box says "The exports are already
  being written -- try again when it finishes. Nothing was changed." Usually
  the other one is the scheduled nightly job.
- **The nightly job and the terminal commands wait** while the other refresh
  is working: the nightly job up to 15 minutes (a fixed bound of its own, so a
  button press cannot push the job past its time limit -- see INSTALL.md,
  "size the snapshot task's time limit"), `mousedb-current-exports` and
  `mousedb export-odc-reaches` up to 40 minutes (change those two with
  `MOUSEDB_LOCK_WAIT_MINUTES`). `mousedb export-odc-reaches` waits whenever it
  is about to write into the live exports folder -- also when `--out-dir`
  names that folder; into a folder of your own it does not wait. If the other
  refresh is still writing when that time is up, or shows no sign of progress
  for 30 minutes (it may be stuck), they stop without changing anything, and
  the files stay those of the last refresh that finished. The nightly job then
  prints `[FAIL] current exports not refreshed` and reports itself as failed.
  The other refresh is NOT a substitute for it: the nightly job reaches its
  exports only after replacing the snapshot, so a refresh still writing at
  that point almost always started from the PREVIOUS snapshot, and the
  night's imports are not in the exports (see the paragraph below for what
  to do). A terminal command prints one line starting
  `[!] another export refresh is still writing the current exports` and exits
  with code 4. Run it again once the other one is done.
- "No sign of progress" is judged by the other refresh's CPU time and
  read/write count when it runs on the same computer, and otherwise by its
  lockfile, which a running refresh touches once a minute. A refresh started in
  another person's login may refuse its counters to a job running as someone
  else; then its lockfile is used too, so a healthy refresh is not mistaken
  for a stuck one.
- `mousedb-current-exports --no-wait` does not wait either: it stops at once
  with exit code 3.
- **"Busy" is said only when someone really is writing.** If the lock file
  cannot be created because your login is not allowed to write in the lock
  folder (wrong permissions, a read-only share), that is NOT reported as busy
  and nothing waits for it. The button shows a warning: "your login cannot
  write to the folder that holds mousedb's lock files. Nobody else is writing
  the exports, and pressing again will not help." A terminal command prints a
  line starting `[!] the lock folder cannot be written` and exits with code 5;
  a scheduled job stops at once with an error that starts `the lock folder
  cannot be written` and names the folder. Fix: give that login write access
  to the folder named (normally `<mousedb_root>/logs`). (A lock file that was
  deleted a moment ago can, on Windows, refuse to be re-created for a second
  or two while the delete finishes; that short refusal is waited out, for up
  to 30 seconds.)

While the nightly job waits for a button-started export, it still holds the
database-task lock it took for the snapshot, so the other scheduled database
jobs wait behind it too -- for at most those 15 minutes. If a button-started
export is still running 15 minutes after the nightly job has reached its
exports (after the snapshot, which itself takes a while), the nightly job
reports `[FAIL]` for its exports and leaves them to the button's export. That
export does finish, but it does NOT bring in the night's imports: it started
before the nightly job replaced the snapshot, so it was building from the
previous one. When it ends it either holds the previous snapshot's data, or --
because the snapshot was replaced while it was writing -- its `MANIFEST.json`
says `"complete": false` ("the snapshot was replaced while these exports were
being written ... write them again") and the button shows "The exports were
not fully rewritten". Either way the exports are a day behind until they are
written again. What to do: once the button's export has finished (and the
nightly job is done), press **Refresh exports now** again; or leave it, and
the next nightly run writes them. Nothing is lost -- the data is in the
snapshot and the database -- but it is not in the exports until then.

**When "Refresh exports now" shows a warning instead.** If the refresh ran but
did not finish cleanly -- a cohort's file could not be written, the snapshot
was taken half before and half after an import, one of the "left out" checks
could not read its input -- a box
says "The exports were not fully rewritten:" followed by the end of what the
exporter printed. Files that could not be rewritten keep their previous
version; `MANIFEST.json` lists what went wrong under `problems` and
`failures`. Read the message: it names the file, folder or cohort and what to
do (close a file that is open in another program, reconnect a drive, wait for
an import to finish). Then press the button again, or leave it to the nightly
run. (When another refresh is already writing, the button never waits for it:
it shows the "already being written" box above instead, and changes nothing.)

## The weekly page: the answer already written down

`mousedb-weekly-report` writes the answer to a page you open by double-clicking,
so it is ready before anyone asks (for a meeting, say). Run it on a schedule
(Task Scheduler, weekly) or by hand:

    conda activate <your MouseDB environment>
    mousedb-weekly-report                 # into <mousedb_root>/reports/Where is my data/
    mousedb-weekly-report --out <folder>  # somewhere else

Each run writes two files in that folder:

- `Where is my data YYYY-MM-DD.html` -- that run's page, kept.
- `Where is my data - LATEST.html` -- the same page, replaced every run.

The page shows, from the pipeline census and the database snapshot:

1. **Videos by stage**, per project and per cohort: done, waiting for triage,
   waiting for deep review, being posed, not started (pillar trays and
   Easy/Flat trays separately), set aside.
2. **Why videos have not started** (pillar trays only -- trays your work order
   leaves until machines are idle are behind on purpose). Each not-started
   session falls in one of these:
   - *Queued for cutting* -- its recording is in the intake folder, waiting its
     turn. If your work order (`priority_order.json` in the MouseReach pipeline
     folder) has a priority list for that project, the row says whether the
     session is on it. The work order's own note is quoted above the table.
   - *Not queued: the recording's name does not read as animal IDs* -- nothing
     will ever pick it up. The recording needs a person to rename it.
   - *Not queued: no recording for it in the intake folder* -- the census
     expects the session but its recording is not waiting anywhere. The names
     are listed; each one needs a look.
3. When the machines and the review backlog should finish at recent pace.
4. **Machines right now**, from each machine's own status report.
5. **In the database**: the per-cohort table from the snapshot.
6. **Problems mousedb knows about** (stale snapshot, failing overnight jobs...).

A section whose source cannot be read says so on the page. It never shows zero
in place of an unknown number. The page reads only files mousedb already keeps,
so it does not slow the database or the pipeline. It takes a few minutes, mostly
reading the snapshot.

## How reach data gets into the database

MouseReach writes one `<video>_features.json` per video into its Analyzed
tree and never touches this database. `mousedb import-reaches` (run on a schedule
where scheduled, or by hand) scans that tree, imports files that are new or
changed, creates any animal it has not seen, and re-derives the protocol
phase of every cohort it touched. `mousedb import-reaches --dry-run` says
what it would do.

**Moving folders does not re-import anything.** The import remembers each
file it has imported by its place in the pipeline folder. When the finished
folders are reorganised (for example each project's folder put inside its
grant's folder), a file found in a new place with the same name, size and
time as a remembered file whose old place is now empty is recognised as the
same file: it is not imported again, and the import's first line says how
many moved (`... N unchanged (M of them moved folders since the last
import)`). A moved file whose content changed is imported as usual. If two
remembered files share the moved file's name, the import does not guess and
imports it again, which is safe, only slower.

**A stopped import carries on where it stopped.** The import writes down
which files it has imported (its ledger, `<mousedb_root>/logs/reach_imports.json`)
every few minutes while it runs, not only at the end. So when a run is ended
part-way -- the scheduled task's time limit, End in Task Scheduler, the
computer restarting -- the next run (the task's automatic restart, or the
next night's) skips everything already imported and does only the rest. At
most the last few minutes of work are done again, which is safe: importing a
video again replaces its rows, it never doubles them. You do not need to do
anything; the restarted run's first line says how many files are still to
import. Three rules keep a stopped run from leaving anything wrong behind:

- A video is written into the ledger only after its reaches are in the
  database, never before. A run stopped in between imports that video again.
- The ledger file is never left half-written: each save is written beside it
  and swapped in whole.
- The cohorts whose protocol phases still have to be re-derived are kept in
  `<mousedb_root>/logs/reach_imports_phases_pending.json` until that is done.
  A run stopped before re-deriving them has the next run do it, even though
  that run imports nothing new for those cohorts; its output then says
  `N cohort(s) still to re-label from an earlier run that stopped`. Do not
  delete this file: a cohort left out of it keeps reaches with no phase.
  A save in the MouseDB window (or a sheet import) whose own re-derivation of
  a cohort's phases failed also puts that cohort on this list, so the next
  import puts its labels right; the window says so in a warning box. The
  import adds to the list and takes off only the cohorts it has re-labelled,
  so a cohort put there while it runs is kept. That includes a cohort saved
  in the window WHILE the import is re-labelling that same cohort: the
  re-labelling may have read the cohort's dates before the save, so the
  cohort stays on the list and the next run re-labels it again. (The file
  records, beside each cohort, how many times a save has put it there; the
  import takes a cohort off only when that number has not changed since it
  began re-labelling it. A list written by an older version -- a plain list
  of names -- is read as before.) Each change to the list takes
  a short lock first (the file `.reach-phases-list.task.lock` in the same
  `logs` folder, there only for the second or so the change takes), so a save
  in the window and the running import can never overwrite each other's
  change. A lock left behind by a program that crashed is cleared by the next
  writer, as for the database lock; if a writer still cannot get it within a
  minute it changes nothing and says so (the window's warning box then tells
  you to run `mousedb backfill-phases`).

**An unreadable ledger stops the import; it is never started again from
nothing by itself.** The import reads its ledger (`reach_imports.json`) and
the list of cohorts still to re-label (`reach_imports_phases_pending.json`)
when it starts. If either is there but cannot be read, it stops at once,
before it imports anything; its last line says `nothing was imported`, which
file it was and what to do (below), and the job fails. A `--dry-run` reads
only the ledger, not the list of cohorts still to re-label (a dry run
re-labels nothing), so a dry run does not find a damaged list; the next real
import does.

What happens when something goes wrong WHILE the import is running:

- **The ledger is damaged during the run.** Nothing visible happens: the
  import reads the ledger only when it starts, and its next save replaces the
  file whole from what it holds in memory. The run finishes normally.
- **The list of cohorts still to re-label is damaged during the run.** The
  import reads that list again at every save (another program adds to it, see
  above). Each save along the way fails and is logged as
  `[!] ledger save during the run failed: ...`, and the import goes on. The
  next save that has to succeed stops the run with `[FAIL] the list of
  cohorts still to re-label ... the run stopped part-way; videos imported
  since its last save are imported again by the next run`. That save is the
  one made just BEFORE taking out videos sent back to review, whenever the
  ledger still lists any video now in review (it is made before any row is
  taken out, so the ledger never claims rows that are gone); otherwise it is
  the final one. When the run stops at that earlier save, nothing is taken
  out that night: the videos sent back to review keep their rows until the
  next run, which takes them out.
- **The shared drive drops during the run.** The saves fail the same way and
  are logged the same way, and the run stops at the next save that has to
  succeed. Its last line is then `[FAIL]` followed by the error the drive
  gave (for example `[WinError 53] The network path was not found`) -- or,
  when the drive answered everything except the read of the list, the
  "stopped part-way" line above.

In the last two cases the rows already imported stay in the database, but the
videos imported since the last save that worked are not recorded in the
ledger. The next run imports them again (safe: their rows are replaced, never
doubled), and the cohorts they belong to are re-labelled by that next run
instead of this one. A stop at the start looks like this:

```
[FAIL] the reach import ledger <path> cannot be read (...); nothing was imported. If the share was briefly unavailable, run again; otherwise rename that file (keep it) and run again -- the run then imports every results file again, which takes hours
```

- Most often the shared drive was unavailable for a moment. The import already
  tries again for about ten seconds; if the drive is back, simply run
  `mousedb import-reaches` again (or wait for the next night). This includes
  the case where the drive answers "network path not found": the import
  believes a ledger is missing only when it can list the `logs` folder (or,
  for a new database with no `logs` folder yet, the folder above it) and the
  ledger is not in the listing. When it cannot list the folder either, the
  drive is not answering, and it stops as above rather than importing
  everything again.
- If the file itself is damaged ("is not valid JSON"), rename it -- for
  example add `.damaged` to its name; do not delete it -- and run the import
  again. With no ledger the import brings in every results file once more.
  That is safe (a video imported again has its rows replaced, never doubled)
  but takes hours, which is exactly why the import no longer does it on its
  own. Its first line then says `no import ledger yet ... every results file
  will be imported` -- a line it prints only when there is no ledger file at
  all, never for a ledger that is there but empty.
- If the damaged file is `reach_imports_phases_pending.json`, rename it, run
  `mousedb backfill-phases` once (it re-derives every cohort's phases, since
  nobody can tell any more which ones were waiting), then run the import.

Why: until this was changed, a ledger that could not be read was treated as
empty and the import silently re-imported every file -- hours of work over the
share, holding the database lock, with nothing saying why.

**Only filed videos are in the database.** Filing means a video is done. When
a filed video is sent back to a review queue -- a pellet nobody answered, a
disagreement with the hand scores, a person asked "are you sure?" -- it is not
done any more, so the next import takes its reaches out of the database, and
out of every export after it. It comes back by itself, with whatever the
review decided, on the first import after it is filed again. What happens,
exactly:

- A video is taken out only when its results file is in a review queue
  (`Processing/Review/...`) and either not in Analyzed, or in Analyzed from an
  OLDER run. (The pipeline re-runs a filed video without touching the filed
  copy, which is kept until the new run is filed; while the new run waits for a
  person, the video is not done.) A video that is merely missing -- halfway
  back from review, or renamed -- is left alone, and so is a leftover copy in a
  queue that is older than the filed one.
- Its rows are copied first, to
  `_archived/reach_withdrawals/<date_time>/<video>_reach_data.csv` and
  `<video>_video_pellets.csv`, then deleted. Before any row is deleted the
  import forgets that it had imported the video (its ledger entry goes, and
  the ledger is saved). So an import stopped in the middle of this never
  leaves a video gone from the database while the ledger still says it was
  imported -- which would make the import skip it when it is filed again.
- The import's log says so, one line per video:
  `[withdrawn] <video>: back in the triage queue, so not done -- N reach rows taken out until it is filed again`
  (or `a newer run is in the triage queue`),
  and its summary line counts them ("N taken out").
- While a newer run of a filed video waits in a review queue, the import does
  not import the filed copy at all. It does not put it in the database, does
  not record it as imported, and does not re-derive its cohort's phases
  because of it. Why: the step above would only take those rows out again,
  so before this the same videos were imported and taken out again every
  night, and their cohorts re-labelled for nothing. The import's first lines
  say how many it left out (`N filed videos skipped: a newer run is in
  review`); `mousedb import-reaches --verbose` also names each one. Once the
  new run is filed, the next import brings the video in as usual. Nothing
  needs doing by hand: the video is waiting for its review, not for the
  import.
- `mousedb import-reaches --pellets-only` (which refills only the per-pellet
  table) applies the same test: it does not write the pellets of a filed
  video whose newer run waits in review, and its output says how many it
  skipped (`N filed videos skipped: a newer run is in review`). Before this it
  wrote them, putting the pellet answers under review back into the exports
  until the next import took them out again.

**Reaches whose results file has gone are reported, never removed.** A video
leaves the database only through a review queue (above). If a filed video's
results file disappears from the pipeline any other way -- deleted, moved to a
folder the import does not look in -- its reaches would otherwise
stay in the database and in every export as if the video were still filed,
with nothing saying so. So every import, after taking out the videos sent
back to review, compares the videos that have rows
in the database with the results files it found, and lists every video that
has rows but no file and is not in a review queue:

- in its output, before its summary line:
  `[!] N videos have rows in the database but no results file in the pipeline and are not in review -- nothing was taken out; a person should look (<first 20 names>)`;
- in `<mousedb_root>/logs/reach_rows_without_file.json` (every name, and when
  it checked, `checked_at`). The file is rewritten only by an import that
  writes, finds filed results files, and gets past taking out the videos sent
  back to review; an empty list there means THAT import found none. Every
  other run leaves the last list as it was: a `--dry-run` (it prints the
  `[!]` line but writes no file), `--pellets-only` (it does not make this
  check at all), a run whose listing found no filed results at all (its
  output says `no filed results found; nothing taken out` -- an empty listing
  is treated as a broken listing, never as "every file is gone"), and a run
  that stopped earlier (an unreadable ledger or list, a failed save). So the
  list can be older than the last import; its date tells you how old;
- on the **Where Is My Data** tab, among the problems at the top, with that
  date (`found by the reach import of <checked_at>`).

Nothing is deleted, and the import still succeeds: a listing that missed some
files must never cost data. What to do: find out where the file went. If it
was moved or renamed by mistake, put it back -- the next import clears the
line. A video that was being moved
between folders while the import ran can show up for one night and then
disappear from the list by itself, and so can a video imported by hand with
`--include-processing` while it is still being processed, until it is filed.

How the import decides a video's file is there: every row in the database
records the name of the results file it was imported from. The import
compares those file names with the names of the results files it finds. So a
results file that was renamed (a typo in the name corrected, for example) is
NOT listed, as long as the renamed file is in the pipeline -- even though the
name written inside it is still the old one, and the rows still carry that
old name. (Rows imported before that file name was recorded fall back to the
video name written inside the file.)

**If the video really should no longer be in the database** (it was withdrawn
on purpose, or recorded by mistake), that is a decision for a person, and
there is deliberately no button or command for it in this tool. Do not delete
rows by hand. Hand the line from the list to whoever looks after the
database, and leave the line on the list until they have decided; it does no
harm there.

Never try to take out the rows of a video that IS filed (its results file is
in the pipeline): the next import would bring them back anyway, and a video
that needs another look goes back through a review queue instead.

## How tissue analysis outputs get here

MouseBrain keeps its own record of every analysis output it produces -- the
per-sample measurements, the figures, and `registry.json` saying for each
sample which method and parameters made it, from which source files, when,
and whether it is still current -- in a `Registry/` folder inside its own
pipeline folder. It never writes into this tool's folders.

`mousedb import-analyses` (run on a schedule where set up, or by hand) mirrors
that registry here:

| what | where it lands |
|---|---|
| measurements and other data files, plus `registry.json` (the provenance) | `<mousedb_root>/exports/<analysis>/...` |
| figures | `<mousedb_root>/figures/<analysis>/...` |
| the analysis's registration log | `<mousedb_root>/logs/<analysis>.log` |
| one summary of every analysis | `<mousedb_root>/exports/ANALYSES_MANIFEST.json` |

Relative paths and modification times are kept, so a path recorded in
`registry.json` (`exports/<analysis>/<sample>/measurements.csv`) resolves
under `<mousedb_root>` exactly as it does in MouseBrain's registry. Only new
or changed files are copied. A file MouseBrain withdraws is not deleted here:
it is moved to `<mousedb_root>/_archived/analyses/<date_time>/`.

A file is moved to the archive only when the run is sure MouseBrain withdrew
it. When it cannot be sure, it leaves the mirror exactly as it is, prints a
`[FAIL]` line and the job fails (exit code 1), so the problem is seen; the
next run that can see the files carries on as normal. The cases:

| what the run finds | what it does |
|---|---|
| the analysis is listed in full (its `registry.json` is there) and a file it mirrored before is no longer there | archives that file |
| the whole analysis folder `exports/<analysis>` is gone, and other analyses are still there | archives every mirrored file of that analysis (MouseBrain withdrew it) |
| the folder `exports/<analysis>` is there but has no `registry.json` | archives nothing of it, copies nothing of it: `[FAIL] <analysis>: exports/<analysis> has no registry.json ...`. Usually MouseBrain is part-way through rebuilding the registry, or the share answered short; run again later. If the analysis really was withdrawn, MouseBrain removes the whole folder. |
| a folder of the analysis cannot be opened (a network error, no permission) | archives nothing of it: `[FAIL] <analysis>: its files could not be listed ...`. The other analyses are mirrored as usual. |
| no analysis at all is listed, yet the mirror holds files | archives nothing: `[FAIL] no analysis registry was listed ...`. Run `mousedb config --show` and check that `mousebrain_registry_root` points at MouseBrain's registry folder (when it is not set, the mirror uses the `Registry` folder inside `mousebrain_pipeline_root`, so check that one instead) and that the share is reachable. If every analysis really was withdrawn, move the mirrored files under `_archived/analyses/` by hand and rename `logs/analysis_imports.json` (keep it). |

`ANALYSES_MANIFEST.json` has one row per analysis: how many samples are
registered, how many are **current**, how many are **stale vs approved**
(current, but produced with a method other than the one the analysis now
approves -- re-run them before using them), how many were **invalidated**,
and when the mirror was taken. The Where Is My Data tab shows the same
numbers as one line per analysis under the export files, for example:

    Analysis <analysis>: 40 current, 3 stale vs approved (approved method as recorded 2026-01-31 14:05), 2 invalidated, imported ...

**Read the date in brackets.** "Stale vs approved" compares each sample
with the approved method written in MouseBrain's `registry.json`, as
MouseBrain last recorded it -- this tool never looks at MouseBrain's
approved-method settings itself. MouseBrain rewrites that record every time
it registers a sample in the analysis. So if the approved method was changed
AFTER the date shown, the count is still judged against the old method and
can say 0 while samples need re-running. To bring it up to date, have
MouseBrain rewrite the record (`mousebrain-registry --name <analysis>
--refresh-approved`, see MouseBrain's registry guide), then run
`mousedb import-analyses` (or wait for the nightly run). Other things you may
see in that place:

- `approved method as recorded at an unknown date (registry written before
  the date was kept)` -- the registry was last written by an older MouseBrain
  that did not keep the date. Treat the count with the same caution;
  refreshing it as above adds the date.
- `no approved method` -- no approved method is declared for this analysis,
  so none of its samples is compared or counted as stale.
- `? current, ? stale vs approved (registry not read this run), ? invalidated,
  imported ... -- [FAIL] <the reason>` -- this run could not read the
  analysis's `registry.json`, so it has no counts to show. The part after
  `[FAIL]` says why, in the same words as the job's own `[FAIL]` line (see
  the table above): the folder has no `registry.json`, its files could not be
  listed, or `registry.json` could not be read. When the reason ends "the
  mirror is kept as it was", the mirrored files of that analysis were left
  exactly as the last good run left them -- nothing was copied or archived.
  The line comes back to normal on the first run that can read the registry.
  If there is more than one problem, the line shows the first and says how
  many more are listed in `ANALYSES_MANIFEST.json`.

Two cases read differently on that line:

- **no approved method** in place of the stale count: the analysis is one no
  method approval judges (MouseBrain records it with no approved method, for
  example an orientation check). There is nothing for its outputs to be stale
  against, so none is counted as stale.
- **[!] N damaged manifest(s) set aside** at the end of the line: MouseBrain
  found this analysis's record damaged, set the damaged copy aside for a person
  and started a fresh one. Until a person repairs it, the samples recorded only
  in the damaged copy are not counted, so the numbers are lower than they
  should be. Repair it on the MouseBrain side (its registry documentation says
  how); the line goes away at the next import after that.

`mousedb import-analyses --dry-run` says what would be copied or archived
without writing anything (no files, no ledger, no manifest).

**Only one mirror runs at a time.** The nightly task and the **Update
everything** button both start `mousedb import-analyses`. When one is already
running, the second waits for it (its output says what it is waiting for),
then runs and normally finds nothing left to copy. If it waits longer than
the lock's usual limit (the same rules as the database jobs' lock: it keeps
waiting while the first run is still getting somewhere), it stops with
`[FAIL] import-analyses not run: ...`, changes nothing, and the job fails;
run it again later. The lock is the file
`<mousedb_root>/logs/.import-analyses.task.lock` while a run holds it. Do not
delete it by hand: a lock left by a run that crashed is taken over
automatically. A `--dry-run` writes nothing and does not wait.

**The mirror's ledger.** `<mousedb_root>/logs/analysis_imports.json` is the
mirror's own record of every file it has copied. It is how the next run knows
which files are unchanged, and which files MouseBrain has since withdrawn (and
so must be archived). Do not edit or delete it.

- No ledger at all (a new machine, a first run): the run checks every file
  and starts a new ledger. That is normal.
- A ledger that is there but cannot be read (damaged, half written): the run
  stops before copying or archiving anything. Its last line starts with
  `[FAIL] import-analyses not run:`, names the file and says what to do, and
  the job fails (exit code 1). If the network share was only briefly
  unavailable, run it again. Otherwise rename the file (keep it, for example
  `analysis_imports.json.damaged`) and run again: the run starts a new
  ledger, re-checks every file, and copies nothing that is already there. The
  one thing lost is this: files MouseBrain withdrew BEFORE the rename are not
  archived automatically; they stay in the mirror until someone moves them.
- The ledger and `ANALYSES_MANIFEST.json` are written whole or not at all (a
  temporary file swapped in), so a run that is stopped part-way -- by the
  scheduler's time limit, or by End in Task Scheduler -- leaves the previous
  version of each, never half a file.

## How far a study's list of sessions has got

A study usually needs particular sessions -- one animal's tray run on one
day -- spread over many cohorts and recordings, so the per-cohort table
cannot say how far they have got. MouseReach can: its coverage report
(`mousereach-session-coverage --sessions-file <list>`) places every session of
a list in exactly one stage (analysed, in review, waiting to be posed, being
posed, recording waiting to be cut, not imported, ...) and writes
`<list>.coverage.json` beside the list.

The tab reads every such report in the `priority_lists` folder of the
MouseReach pipeline root (`mousereach_pipeline_root` in mousedb's config) and
shows one line per list under the export files, for example:

    Sessions list study_a: 120 sessions -- 80 recording waiting to be cut, 30 analysed, 10 in review (report written ...)

The line is only as new as the report: run the coverage command after a list
changes, or schedule it nightly (the time it was written is on the line). A
report that cannot be read is shown as such, never left out. `mousedb-data-status`
prints the same lines in a terminal.

## Recordings still only on an old storage

If a lab moved its pipeline to a new shared folder, recordings can be left in
the old one, where no watcher looks. MouseReach's inventory
(`mousereach-legacy-inventory --from <old root>`) lists every video under an
old root and says whether the pipeline already has it; it writes
`<label>.csv` (every video with its status) and `<label>.summary.json` (counts)
into the `legacy_inventory` folder of the MouseReach pipeline root.

The tab reads every summary there and shows one line per old root under the
export files, for example:

    Old storage <old root>: 120 recording(s), 5.0 GB, exist ONLY there; 31 more need a person's look (inventory ...; report ...)

"Exist ONLY there" are pipeline recordings no machine can process until they
are brought in (`--bring-in` on the same command copies them into the
pipeline, verified, never overwriting; the old root is only ever read). "Need a
person's look" are files whose names are not pipeline names, or that differ in
size from the pipeline's copy -- the report's status column says which. The old
root can be retired once the first number reads 0. Like the session lines, the
line is only as new as the inventory (its date is on the line); re-run the
command after moving anything. `mousedb-data-status` prints the same lines.

## The "Update the database now" button

On the Where Is My Data tab, the green button **Update the database now**
pulls everything that is new into the database and rewrites what you see,
in this fixed order:

1. Tracking sheets -> database (the hand-entered data; the sheet is
   authoritative but late). **The sheet wins:** a value corrected in the
   sheet is corrected in the database, and a row removed from the sheet is
   removed from the database at this step -- except rows typed in by hand in
   mousedb, which an import never touches
2. MouseReach results -> reach_data (every new or changed video)
3. MouseBrain analysis registry -> exports
4. Brain region counts -> database
5. Snapshot + current exports rewritten (so the tab and the CSVs show what
   just landed)

Press it whenever you are about to look at the data and want it current
instead of as old as the last scheduled run. On a large database it can take an
hour or more -- the last step, rewriting the snapshot and the exports, is the long
one. Each import step is stopped if it runs over an hour (it carries on from where
it stopped next time). The snapshot step is stopped only after 12 hours --
far longer than a working refresh takes -- because one stopped part way would
leave the exports half rewritten; the 12-hour limit is only there so a refresh
that has hung (for example on an unreachable network share) does not block
the button for days. The tab refreshes
itself when it finishes, and a dialog lists every step with **[OK]** or
**[FAIL]** and the step's own last line. The line under the snapshot time
always shows when the last update ran, who started it, and whether
everything landed.

Only one update runs at a time -- if a scheduled run or someone
else's button press is still going, yours says so and does nothing. It
names the update that is running (its process number, when it started, and
its command). An update keeps that place for as long as its program is still
running, however long that is; if the program that started it has gone (the
window was closed or the computer restarted mid-update), the next press
takes over at once. That check works only for an update started on the same
computer as the press: an update started on another computer cannot be looked
up from here, so if it dies its place is taken over only once its marker
(below) has gone 45 minutes untouched -- a running update touches it once a
minute. Until then a press is refused, and the refusal names that other
computer. The marker is `<mousedb_root>/logs/.update.task.lock`
(the `logs` folder under the configured `mousedb_root`). While an update
runs, an `update.lock` is written beside it too and touched every minute
until the update ends, so a window still running an earlier version of this
tool -- which treats an `update.lock` older than two hours as left over --
also sees it and refuses, however long the update takes. An update
started by such an earlier-version window is respected too: its
`update.lock` does not say which computer wrote it, so it is respected while
its program is still running on this computer, and otherwise for up to 2
hours from its start (the earlier versions' own rule) -- it may be running
on another computer. If that update crashed, a press is refused until those
2 hours have passed. A
failed step never stops the later ones (sheets failing must not keep a day's
reaches out), and the update as a whole is marked failed if any step failed.

From a terminal the same thing is `mousedb update` (`--skip sheets` etc.
leaves a step out). Its full record is `<mousedb_root>/logs/updates.jsonl`;
the newest entry holds each step's last 15 lines of output.

**When a step shows [FAIL]:** read its last line in the dialog. "database is
locked" means something else was writing at that moment -- press the button
again after a minute (and see *When the database is locked* below). Anything
else, pass the line to whoever looks after this installation; the full output
is in `logs/updates.jsonl`.

## Why the numbers can lag

The tab and the exports read a *snapshot* of the database, not the live
database. The live file may sit on a network share, and reading it while
something writes to it can corrupt the read -- so everything human-facing
works from the last safe copy.

The snapshot is replaced in one piece, so everything on the tab comes from the
same night. If a refresh was stopped in the middle of replacing it, the tab
says the snapshot is not consistent and shows no counts until the next
successful refresh (or **Update the database now**) -- counts mixed from two
nights would describe neither.

So the numbers are as old as the last scheduled refresh. **The line at the top
of the tab says exactly how old**, in words: "taken 2026-01-02T00:14 -- 7 hours
old". If the refresh has not succeeded for longer than a day and a half, that
line turns red and says so outright, because at that point work that has been
analysed is missing from everything below it.

If you need the numbers current right now, press **Update the database now**
(previous section) instead of waiting for the next scheduled run.

## When the database is locked

The database is one file, and more than one program can open it: the scheduled
jobs, this tool's own windows, a notebook someone left open, and the same
things on other people's computers. While one of them is writing, everybody
else is refused with "database is locked". That is normal and it is not a
fault.

What it means for you:

- **A job you pressed** says the database is locked: wait a minute and press
  again. Nothing was changed by the attempt.
- **A scheduled job** that meets a lock no longer gives up at once. It waits
  and tries again a few times. How long it keeps trying depends on the job: the
  short ones try for about three quarters of an hour, the snapshot refresh for
  about twenty minutes, because a retry starts it over from the beginning and
  it must not still be running when the next job is due. This rescues a lock
  that clears in minutes. It will not rescue one held for an hour, and is not
  meant to -- the age line at the top of the tab is what tells you about those.
- **If it does not clear**, the job writes one line in its log saying so in
  plain words, and does not run that time. Nothing is half-written: a job that
  could not start the database work changed nothing at all. The numbers people
  see stay as of the last successful run, and the tab's age line is what tells
  you that happened.
- **If that repeats for several runs**, somebody has a session open with a
  write in progress -- a notebook, a console, or a window of this tool, on any
  computer that can see the database. Find it and close it. The jobs will never
  break someone else's lock to get in, because taking a lock away from a write
  that is in flight is how a database file gets corrupted.

The scheduled jobs also take turns with each other through a lockfile, so they
are never the cause. A job that finds another mousedb job working waits for it
to finish -- the snapshot refresh for up to three hours, the others for up to
forty minutes -- and writes a line every ten minutes while it waits. If it
still cannot start, or the job ahead of it seems stuck (no progress for half an
hour), it writes a `[FAIL]` line naming that job and does not run that night.
**Nothing runs it again automatically**: press **Update the database now**, or
run the job by hand, once the other job has finished.

## The bench check (a nightly report)

Every night a scheduled job, `mousedb-bench-scan`, compares the hand scores
from the tracking sheets (each pellet: missed, displaced or retrieved) with
what the video pipeline decided for the same pellet, and lists every pellet
where they disagree and no person has looked at it yet. It only REPORTS: it
sends nothing to review and changes nothing.

Disagreements reach a reviewer another way, earlier: MouseReach compares each
video with its hand scores BEFORE filing it, so a video that disagrees goes to
its triage queue instead of being filed. That is why the nightly job must not
be scheduled with `--route`. With `--route` it hands its list to MouseReach's
route command, which sends those videos -- finished and filed -- back to the
triage queue, and a video sent back to review leaves the database until it is
filed again (see "Only filed videos are in the database" above). `--route` is
for a deliberate run by hand, when someone has decided those videos should be
looked at again.

What the reviewer sees when a video is in triage for a bench disagreement: the
review tool's banner says so, and beside each marked pellet what the two
disagree about -- "the hand score says retrieved; the algorithm says
untouched".

What it compares:

- Only finished videos, and only those analysed by the outcome detector
  MouseReach currently declares (read from MouseReach's
  `pipeline_versions.json` each night). A video waiting to be re-run is left
  until it has been, so nobody is asked to judge output that is about to change.
- Every pellet with a hand score -- including pellets where the pipeline found
  no reach at all (since 2026-10-22). "The sheet says the pellet moved, and the
  pipeline saw no reach" is one of the most useful things to show a person;
  before that date those pellets were never compared.
- Never a pellet a person has already answered. The scan skips pellets the
  database records as decided by a person; on a `--route` run by hand,
  MouseReach's route command also checks the review itself before marking
  anything, so a pellet already answered is never sent back.

The list it made is written to `<mousedb_root>/logs/never_reviewed_worklist.json`,
and the job's output (how many videos and pellets, and each video's result) is
in `<mousedb_root>/logs/tasks/bench_scan.log`.

## When something looks wrong

- **A cohort's videos are in the pipeline but "Videos in DB" is low**: check
  *In review* first -- held videos are not final yet. Then run
  `mousedb import-reaches --dry-run` to see whether files are waiting to be
  imported.
- **"N video(s) have reaches in the database but no results file"** at the top
  of the tab: see "Reaches whose results file has gone are reported, never
  removed" above.
- **"complete for ODC upload: False"**: a new column reached the exports
  without a dictionary entry. The problems list names it; the definition is
  added in `mousedb/exporters/data_dictionary.py`.
- **Nothing in the exports folder**: the scheduled refresh has not run yet on
  this database; press *Refresh exports now*.

From a terminal (MouseDB environment active):

```
mousedb-data-status              # the table above, as text
mousedb import-reaches           # pull new MouseReach results into reach_data
mousedb import-analyses          # mirror MouseBrain's analysis registry (exports, figures, provenance)
mousedb-current-exports          # rewrite reach_data + manual_scores (+ dictionaries)
mousedb-current-exports --db-ok  # also the ODC session files (only when nothing is writing)
mousedb-current-exports --no-wait  # stop at once (exit 3) if another refresh is writing
```

## The database follows the sheet

The tracking sheets are the record; the database is a copy of what they say.
Every sheet import (the nightly job, the Tracking Sheets tab, the update
button) therefore does three things for the rows earlier imports of that
cohort's sheet wrote -- pellet scores, weights, ramp days and surgeries:

- **a value corrected in the sheet is corrected in the database;**
- **a row removed from the sheet** -- or moved into another cohort's
  workbook -- **is removed from the database at the next import;**
- **a row typed in by hand in mousedb is never changed or removed.**

An animal's date of birth, date of death and sex are filled in from the
sheet's metadata tab only where the database has nothing for them; a value
already there is kept, and a difference from the sheet is listed in the red
attention box for a person to settle.

When a removal takes a session date out of a cohort's schedule (or an import
adds one), the cohort's phases -- training, baseline, post-injury tests --
are worked out again in the same import, for the hand scores and the reaches
alike; the hand-scores file is rewritten at the end of that import and the
exports at the next nightly refresh. Every change is listed, with the old
value, in the import's output, in the import history
(`logs/sheet_imports.jsonl`, under `sheet_sync`) and in the audit log.

**If an import refuses.** A sheet that reads as broken -- a tab missing or
unreadable, or holding far fewer rows than the database has from it --
removes and changes nothing. You see it as **NOT SYNCED** in red in the
Sheet column, as a line in the red **Needs your attention** box
(`NOT SYNCED <table>: ...`), and in the cohort's FIX THESE file beside its
sheet. Check the sheet: put a renamed tab or cut rows back and the next
import syncs. If the sheet really did lose those rows on purpose, see
docs/TRACKING_SHEETS.md, "The sheet wins", for how to let that one cohort's
import go ahead.
