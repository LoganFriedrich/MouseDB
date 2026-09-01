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
| `MANIFEST.json` | -- | when the files were written, from which snapshot, row counts, and any problems |
| `README.txt` | -- | the same explanation as this table |

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
folder above in Explorer. **Refresh exports now** rewrites `reach_data.csv`
and `manual_scores.csv` immediately from the latest snapshot (the per-cohort
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
