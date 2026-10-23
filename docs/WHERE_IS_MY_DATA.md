# Where is my data?

This page is for the person doing the work. No codebase knowledge assumed.

## The short answer

`<mousedb_root>/exports/current/` (run `mousedb config --show` to see the
folder on this machine) -- rewritten by the scheduled job with everything the
database currently holds, as CSV files you can open in Excel, R or Python,
each with a data dictionary beside it:

| file | one row per | definitions |
|---|---|---|
| `reach_data.csv` | reach the pipeline detected (kinematics, the pellet outcome of its segment, and where that outcome came from) | `reach_data_DATA_DICTIONARY.csv` |
| `manual_scores.csv` | pellet scored by hand from the tray (0 missed / 1 displaced / 2 retrieved) with the session's phase | `manual_scores_DATA_DICTIONARY.csv` |
| `ODC_sessions_<cohort>.csv` | animal per session, in the ODC-SCI `2_ODC_Animal_Tracking` shape (per-tray and daily counts and percentages, weight, injury) | `ODC_sessions_DATA_DICTIONARY.csv` |
| `ODC_reaches_<cohort>.csv` | reach -- **the complete record**: every measurement held for that reach, that animal's details (strain, sex, every surgery column of the tracking sheet), that session's details, the tray totals, AND how the result was produced (tool versions, which machine, who reviewed it) | `ODC_reaches_DATA_DICTIONARY.csv` |
| `reaches_<cohort>_summary.csv` | reach -- **the shareable table**: the same reaches and every measurement of them, with the process columns removed and everything named in plain words | `reaches_summary_DATA_DICTIONARY.csv` |
| `reaches_<cohort>_analysis.csv` | reach -- **the analysis table**: the same reaches in the shape a statistics package wants (numbers in numeric columns, `NA` for absent, the session's totals on every row). An entry with an identified problem is left out of it until its source is fixed | `reaches_analysis_DATA_DICTIONARY.csv` |
| `sessions_<cohort>_analysis.csv` | animal per session, totalled from the analysis table. Every percentage (retrieved, displaced, contacted, missed; algorithm and hand scored) is out of `pellets_presented`, the tray's 20. Two columns MEASURE it: `pellets_in_video` (how many pellets the pipeline found in the video) and `pellets_hand_scored` (how many pellets of that tray have a hand score); both should be 20, and a session where either is not is treated as an error (see below). `pellets_reached_for` counts the pellets the animal reached for at least once and is never a denominator | `sessions_analysis_DATA_DICTIONARY.csv` |
| `analysis_EXCLUSIONS.csv` | entry left out of the analysis tables, and why | -- |
| `MANIFEST.json` | -- | when the files were written, from which snapshot, row counts, and any problems |
| `README.txt` | -- | the same explanation as this table |

**A tray presents 20 pellets; anything else is an error, and it is handled like one.** A video the pipeline cut into some other number of pellets has the wrong pellet number on its reaches, so that session is left out of the analysis files. A tray whose hand scores number other than 20 keeps its reaches but has its hand-score columns left empty. Both are listed in `analysis_EXCLUSIONS.csv` with the count that was found, and both come back on their own once the video is reprocessed or the sheet is corrected.

**A recording whose name the archive's own cuts contradict is left out whole.** Some archives kept their own single-animal cut of every seat beside each recording, so every animal is named twice, typed separately. MouseReach's name audit (`mousereach-aspa-name-audit`) traces each of those cuts to the recording it came from and writes the recordings whose seats the cuts name for an animal the recording's name does not list (`pipeline_records/archive_name_conflicts.json` in the MouseReach pipeline folder). Every video cut from such a recording is left out of the analysis files -- whose video each seat holds is in doubt -- and listed in `analysis_EXCLUSIONS.csv` with the cut names that disagree. Nothing is renamed: once a person has said which name is right and the recording has been brought in under it, rerunning the audit brings the videos back. With no audit record, `MANIFEST.json` says the check did not run.

**One dictionary per kind of file, not one per cohort.** Every cohort's file of
a kind has the same columns, so one dictionary describes them all; send it
alongside whichever cohorts' files you send. The one exception is the complete
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
| `reach_result` | what THIS reach did to the pellet: `Retrieved`, `Displaced into the scoring area`, `Displaced out of reach`, `Did not move the pellet`, or `Undetermined` |
| `pellet_result` | what became of the pellet this reach was aimed at, repeated on every reach aimed at it, so you never have to join anything |

`Did not move the pellet` is an **answer**, not a missing value: the pellet's
fate was decided by a different reach, or by none. It is deliberately not
"missed" -- contact is not measured for a reach that did not decide the
outcome, so claiming the paw missed would assert something nobody looked at.

`reach_result` never says where the verdict came from. A verdict is a verdict;
whether a human or an algorithm produced it is in the complete record.

### No cell is ever empty -- and the word tells you why

ODC-SCI rejects a dataset with empty cells, so both files fill every one. The
filler is not a single "NA", because a blank means four different things and
treating them alike would throw away what you actually know:

| you will see | it means |
|---|---|
| `Not applicable` | the question cannot apply to this row -- days-post-injury for a session that happened before the injury |
| `Not measured` | no code computes this value (the column exists so the column set stays stable) |
| `Not recorded` | a source should carry it and does not -- nobody wrote it in the sheet |
| `Undetermined` | it was looked at and could not be decided |

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
file has a dictionary entry -- if it says false, the problems list names the
undocumented columns, and an upload would be rejected until they are added.

## The longer answer: the "Where Is My Data" tab

Open the mousedb GUI (MouseDB environment active, then `mousedb-entry`) and
find **11. Where Is My Data**. One row per cohort:

| column | meaning |
|---|---|
| Animals | animals the database knows for the cohort. `[N from video only]` means N of them were created from a video before the tracking sheet named them -- import the sheet to fill in their details |
| Sheet | the tracking sheet's import status (worked in the **Tracking Sheets** tab): Up to date / Sheet edited since last import / Never imported / LAST IMPORT FAILED |
| Sessions scored | hand-scored animal-days in the database |
| Videos in DB | videos whose reaches are in the database (i.e. are in `reach_data.csv`) |
| In review (triage / deep) | videos waiting for a person in MouseReach's review queues. **Their data is not final until they are reviewed and released.** They are worked in MouseReach (its Review Queues tab) |
| Outcomes algo / human | pellet outcomes resting on the algorithm alone vs confirmed or corrected by a person |
| Reaches | rows the cohort contributes to `reach_data.csv` |

Below the table: the export folder, when it was last written, whether it is
complete for an ODC upload, the row count of each file, and any problems.

Buttons: **Refresh** re-reads everything. **Open exports folder** opens the
folder above in Explorer. **Refresh exports now** rewrites `reach_data.csv`,
`manual_scores.csv` and the `ODC_reaches_<cohort>.csv` files immediately from the latest snapshot (the per-cohort
ODC session files refresh on the scheduled run, which is the only time the
database may be read safely).

## How reach data gets into the database

MouseReach writes one `<video>_features.json` per video into its Analyzed
tree and never touches this database. `mousedb import-reaches` (run on a schedule
where scheduled, or by hand) scans that tree, imports files that are new or
changed, creates any animal it has not seen, and re-derives the protocol
phase of every cohort it touched. `mousedb import-reaches --dry-run` says
what it would do.

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

`ANALYSES_MANIFEST.json` has one row per analysis: how many samples are
registered, how many are **current**, how many are **stale vs approved**
(current, but produced with a method other than the one the analysis now
approves -- re-run them before using them), how many were **invalidated**,
and when the mirror was taken. The Where Is My Data tab shows the same
numbers as one line per analysis under the export files.

`mousedb import-analyses --dry-run` says what would be copied or archived
without writing anything (no files, no ledger, no manifest).

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
   authoritative but late)
2. MouseReach results -> reach_data (every new or changed video)
3. MouseBrain analysis registry -> exports
4. Brain region counts -> database
5. Snapshot + current exports rewritten (so the tab and the CSVs show what
   just landed)

Press it whenever you are about to look at the data and want it current
instead of as old as the last scheduled run. It takes a few minutes; the tab refreshes
itself when it finishes, and a dialog lists every step with **[OK]** or
**[FAIL]** and the step's own last line. The line under the snapshot time
always shows when the last update ran, who started it, and whether
everything landed.

Only one update runs at a time -- if a scheduled run or someone
else's button press is still going, yours says so and does nothing. A
failed step never stops the later ones (sheets failing must not keep a day's
reaches out), and the update as a whole is marked failed if any step failed.

From a terminal the same thing is `mousedb update` (`--skip sheets` etc.
leaves a step out). Its full record is `logs/updates.jsonl` beside the
database; the newest entry holds each step's last 15 lines of output.

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
are never the cause.

## Why videos appear in the review queue overnight (the bench check)

Every night a scheduled job, `mousedb-bench-scan --route`, compares the hand
scores from the tracking sheets (each pellet: missed, displaced or retrieved)
with what the video pipeline decided for the same pellet. Where they disagree
and no person has looked at that pellet yet, it asks MouseReach to put the
video in its triage queue with exactly those pellets marked. The reviewer then
watches those pellets in the review tool and decides.

What the reviewer sees (since 2026-10-23): the review tool's banner says the
video was sent back for a bench disagreement, and beside each marked pellet what
the two disagree about -- "the hand score says retrieved; the algorithm says
untouched". The video may be one the reviewer finished long ago: the question is
about a different pellet from the ones they answered. Before this, nothing on
screen said why, and finished videos coming back looked like answers being lost.

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
  database records as decided by a person, and MouseReach's route command
  checks the review itself before marking anything, so a pellet already
  answered is never sent back.

The list it made is written to `<mousedb_root>/logs/never_reviewed_worklist.json`,
and the job's output (how many videos and pellets, and each video's result) is
in `<mousedb_root>/logs/tasks/bench_scan.log`.

## When something looks wrong

- **A cohort's videos are in the pipeline but "Videos in DB" is low**: check
  *In review* first -- held videos are not final yet. Then run
  `mousedb import-reaches --dry-run` to see whether files are waiting to be
  imported.
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
```
