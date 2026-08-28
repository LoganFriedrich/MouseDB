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

## Why the numbers can lag by up to an hour

The tab and the exports read an hourly *snapshot* of the database, not the
live database. The live file may sit on a network share, and reading it
while something writes to it can corrupt the read -- so everything
human-facing works from the last safe copy. The tab shows the snapshot's
time at the top.

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
mousedb-current-exports          # rewrite reach_data + manual_scores (+ dictionaries)
mousedb-current-exports --db-ok  # also the ODC session files (only when nothing is writing)
```
