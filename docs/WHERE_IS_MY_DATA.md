# Where is my data?

This page is for the person doing the work. No codebase knowledge assumed.

## The short answer

`<mousedb_root>/exports/current/` (run `mousedb config --show` to see the
folder on this machine) -- rewritten by the hourly job with everything the
database currently holds, as CSV files you can open in Excel, R or Python,
each with a data dictionary beside it:

| file | one row per | definitions |
|---|---|---|
| `reach_data.csv` | reach the pipeline detected (kinematics, the pellet outcome of its segment, and where that outcome came from) | `reach_data_DATA_DICTIONARY.csv` |
| `manual_scores.csv` | pellet scored by hand from the tray (0 missed / 1 displaced / 2 retrieved) with the session's phase | `manual_scores_DATA_DICTIONARY.csv` |
| `ODC_sessions_<cohort>.csv` | animal per session, in the ODC-SCI `2_ODC_Animal_Tracking` shape (per-tray and daily counts and percentages, weight, injury) | `ODC_sessions_DATA_DICTIONARY.csv` |
| `ODC_reaches_<cohort>.csv` | reach -- **the complete record**: every measurement held for that reach, that animal's details (strain, sex, every surgery column of the tracking sheet), that session's details, the tray totals, AND how the result was produced (tool versions, which machine, who reviewed it) | `ODC_reaches_<cohort>_DATA_DICTIONARY.csv` (one per cohort: sheets differ) |
| `reaches_<cohort>_summary.csv` | reach -- **the shareable table**: the same reaches, trimmed to what someone outside the lab needs, in plain words | `reaches_<cohort>_summary_DATA_DICTIONARY.csv` |
| `MANIFEST.json` | -- | when the files were written, from which snapshot, row counts, and any problems |
| `README.txt` | -- | the same explanation as this table |

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
ODC session files refresh on the hourly run, which is the only time the
database may be read safely).

## How reach data gets into the database

MouseReach writes one `<video>_features.json` per video into its Analyzed
tree and never touches this database. `mousedb import-reaches` (run hourly
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

`mousedb import-analyses` (run hourly where scheduled, or by hand) mirrors
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
instead of up to an hour old. It takes a few minutes; the tab refreshes
itself when it finishes, and a dialog lists every step with **[OK]** or
**[FAIL]** and the step's own last line. The line under the snapshot time
always shows when the last update ran, who started it, and whether
everything landed.

Only one update runs at a time -- if the scheduled hourly run or someone
else's button press is still going, yours says so and does nothing. A
failed step never stops the later ones (sheets failing must not keep a day's
reaches out), and the update as a whole is marked failed if any step failed.

From a terminal the same thing is `mousedb update` (`--skip sheets` etc.
leaves a step out). Its full record is `logs/updates.jsonl` beside the
database; the newest entry holds each step's last 15 lines of output.

**When a step shows [FAIL]:** read its last line in the dialog. "database is
locked" means something else was writing at that moment -- press the button
again after a minute. Anything else, send the line to Logan; the full output
is in `logs/updates.jsonl`.

## Why the numbers can lag by up to an hour

The tab and the exports read an hourly *snapshot* of the database, not the
live database. The live file may sit on a network share, and reading it
while something writes to it can corrupt the read -- so everything
human-facing works from the last safe copy. The tab shows the snapshot's
time at the top. If you need it current right now, press **Update the
database now** (previous section) instead of waiting for the hour.

## When something looks wrong

- **A cohort's videos are in the pipeline but "Videos in DB" is low**: check
  *In review* first -- held videos are not final yet. Then run
  `mousedb import-reaches --dry-run` to see whether files are waiting to be
  imported.
- **"complete for ODC upload: False"**: a new column reached the exports
  without a dictionary entry. The problems list names it; the definition is
  added in `mousedb/exporters/data_dictionary.py`.
- **Nothing in the exports folder**: the hourly refresh has not run yet on
  this database; press *Refresh exports now*.

From a terminal (MouseDB environment active):

```
mousedb-data-status              # the table above, as text
mousedb import-reaches           # pull new MouseReach results into reach_data
mousedb import-analyses          # mirror MouseBrain's analysis registry (exports, figures, provenance)
mousedb-current-exports          # rewrite reach_data + manual_scores (+ dictionaries)
mousedb-current-exports --db-ok  # also the ODC session files (only when nothing is writing)
```
