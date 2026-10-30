# Tracking Sheets: getting the lab's spreadsheets into the database

This page is for the person doing the work. No codebase knowledge assumed.

## What this is about

Each cohort has one tracking workbook, named by its project's sheet name
(for example `ProjectA_NN_Animal_Tracking.xlsx`, NN = cohort number -- each
project's name is set once for the whole lab, see "Which projects' sheets are
read" below), kept in a shared folder the lab chooses. It is the
hand-kept record of that cohort: the animals, weights, ramp days, manual
tray scores (which pellet was missed / displaced / retrieved), the injury,
the injection surgery, the virus batch. Nothing in mousedb ever edits it.

The database only knows what has been **imported** from those workbooks.
Importing reads a workbook and copies its contents into the database; it
never changes the workbook. You can import as often as you like.

**Importing only adds.** A row the database does not have yet lands. A row
it already has (the same animal on the same date) is left as it is -- even
if you have corrected that cell in the workbook since. Two exceptions:

- **Each animal's details** -- the `0a_Metadata` tab, the two surgery tabs,
  and each animal's sex from the tray tab -- are also kept as a copy that the
  exports read. That copy is thrown away and taken again from the sheet on
  every import made from the Tracking Sheets tab, the update button, the
  scheduled job or the commands on this page, so corrections there do show.
  Only an import that SAVED the workbook replaces it: when an import fails,
  the copy is left exactly as it was (see "When an import fails" below).
- **Replace**, in the Cohort Setup tab, takes a cohort's weights, scores,
  surgeries and the rest from the sheet again. See "Import or Replace" below.

Animals do NOT have to be in the sheet before their videos are processed.
`mousedb import-reaches` creates an animal the first time it sees a video
for it; the sheet import fills in the details later. (It is why a late sheet
cannot make video data disappear.)

## Where to do it

Open the mousedb GUI: activate the MouseDB conda environment
(`conda activate MouseDB`, or the full path of the environment if it was
created with `--prefix`) and run `mousedb-entry`. Find the
**10. Tracking Sheets** tab. Everything below is a button on that tab unless
it says otherwise.

## What the table tells you

One row per cohort:

| Column | Meaning |
|---|---|
| Cohort | e.g. CNT_NN |
| Status | **Up to date** -- the database matches the sheet's last edit. **Sheet edited since last import** -- someone changed the workbook after it was last imported; import again. **Never imported** -- import it. **LAST IMPORT FAILED** -- the last attempt hit an error; select the row to read it. **N files match -- choose** -- see "Which file is read" below. |
| Sheet file | The workbook being read as this cohort's sheet. `[pinned]` means a person chose it and that file is still there. `[PIN BROKEN -- see the red box]`, in red, means the file a person chose has gone (see "A broken pin"). |
| Sheet edited | When that workbook was last saved. |
| Last import | When it was last imported into the database (the log box shows whether that worked). |

Select a row and the line under the table explains the status in a sentence
(and repeats a broken pin's sentence, if the cohort has one).

**The red box** above the table appears only when something about WHICH file
is read needs you. It has one line for each such thing:

- `PIN BROKEN: ...` -- a file someone chose with **This is the sheet** has
  been renamed, moved or deleted, so the cohort now reads another file (see
  "A broken pin"). A cohort left with no file at all has no row in the
  table; this line is the one place it is named.
- `NOT READ AS A SHEET: <file> -- <reason>` -- a file that looks like a
  sheet but is never imported (see "Which file is read").

If the folder set for this machine holds no readable sheet at all (say every
sheet in it was renamed `Copy of ...`), the table stays empty, the line at the
top says so and names the folder, and the red box still lists what is in it.

## Importing

1. Press **Import all sheets**. (Or select rows and press **Import selected**.)
2. Watch the box at the bottom. It shows the same lines as the terminal
   command `mousedb-sheets import`: each cohort reports `OK` with what was
   imported, or `FAIL` with the reason, then `[N warnings]` and the first 3
   of them; then any file that is not read as a sheet; and a last line that
   sums up, `Sheets: ...`. If any cohort failed, or nothing could be
   imported, a box pops up with that last line too.
3. The table refreshes itself. Everything should read **Up to date**.

An import takes about a minute per cohort when the workbooks live on a
network share. If another database task (a scheduled import, a Replace, the
reach import) is writing at that moment, the import waits for it to finish
-- up to 25 minutes per cohort -- and the box shows nothing new until then.

**The background job.** Where the lab has scheduled it (see INSTALL.md,
"Scheduled jobs", the *sheet import* job), every sheet is imported on its own
on that schedule. This tab shows you whether that worked -- a cohort stuck on
*Never imported* or *LAST IMPORT FAILED* means the background job is failing
for it too, and the reason is the one shown.

## What the import takes, what it leaves out, and what it tells you

The import takes every row it can show to be real and leaves the rest where
it is. It does not guess: it does not make up a date, it does not file a row
under another cohort's animal, and it does not count a planned surgery as
one that happened. When something it left out needs a person, it says so in
a **warning**: one line per problem, once per import.

A warning is not a failure. Everything else in the workbook was imported.

### Surgery rows: done, planned, or waiting for a fix

This is about the two surgery tabs, `4_Contusion_Injury_Details` (the injury)
and `5_SC_Injection_Details` (the injection).

**A row counts as a surgery that happened only when two things are typed on
it:**

- the animal's **body weight on the day**, in grams, under
  `Subject_Weight (g)`; and
- the **surgery date**, under `Surgery_Date` (or `Date`).

Why the weight: the animal is weighed on the table to dose its drugs, so that
number cannot exist before the day. Everything else on the row -- virus,
titre, target, depths, coordinates, drugs, doses, device settings, Survived --
can be typed in ahead of time, or comes along when a workbook is made by
copying the previous cohort's, so none of it counts. (This also means a
planned row can no longer mark a living animal as dead through its Survived
cell.)

How to type them so they count:

- **Weight:** `22`, `22.4`, `22,4`, `22 g` or `22 grams`. Blank, `?` or `0`
  means "not weighed yet". (The weight may also sit under `Subject_Weight_g`,
  `Subject_Weight`, `Weight (g)` or `Weight_g`; the first of these that is
  filled in is the one read.)
- **Date:** a real Excel date, or text that names a whole day, such as
  `2026-03-01` or `1 Mar 2026`. These are NOT dates: a time alone (`13:00`),
  a month or a year alone (`Mar 2026`, `2026`), words (`today`, `TBD`), or a
  plain number. Text such as `03/04/2026` is read month first, so use a real
  Excel date or the `YYYY-MM-DD` form.

What happens to each row:

| The row has | What the import does | Are you told? |
|---|---|---|
| a weight on the day and a date | imports it as a surgery (an injection row also stores that weight) | no |
| no weight and no date | treats it as a **plan**; imports nothing from it | never |
| no weight, and a date in the future, today, or 14 days ago or less | a **plan**; imports nothing from it | no -- weights are often typed in a few days late |
| no weight, and a date more than 14 days ago | an **overdue plan**; imports nothing from it | yes: one line per tab listing each such animal with its planned date, on every import until the row is fixed |
| something that looks done but cannot be taken as done: a weight that is not grams (`22 kg`, `abc`, a negative number); a device reading (`Actual_kd`, `Actual_displacement`) but no weight; a weight but no date; a date cell that is not a date | **held**; imports nothing from it | yes: one line for each such row, on every import, saying what to fix |

Fix the row in the workbook and the next import takes it. There is nothing
else to do and nothing to undo.

Two more things you may see:

- If a surgery tab has lost its `Subject_Weight (g)` column heading, **nothing**
  is imported from that tab (every row would look like a plan), and one line
  tells you to put the heading back.
- A row with a weight on the day but a blank animal cell is named by its
  sheet row number, so you can find it and type the animal id.

### No date is made up

The sheet import never invents a date. A record whose date is missing is not
imported until the date is typed:

- A surgery is stored with the date typed on its row and nothing else (see
  above).
- A virus calculation on `0_Injection_Calculations` that has no date is not
  imported. The warning tells you to add a date row (a parameter named like
  `Prep_Date`) or enter it as a dated batch block (a title row ending in
  `YYYYMMDD`).
- A cohort's **start date** (day 0) is read from the sheet when an import
  first creates the cohort: the earliest dated weigh-in on `3d_Weights` /
  `3e_Weights` or `3a_Manual_Ramp`; if neither has a date, 4 days before the
  earliest dated row on `3b_Manual_Tray`. An import never changes the start
  date of a cohort that already has a real one. (Replace may; it shows you
  first.)

**A placeholder start.** The database needs a start date for every cohort.
When an import meets a cohort that is not in the database yet, the sheet
gives no start date by the rule above, and some other row does have a date
(a surgery, say):

- the cohort is created with a **placeholder start**: the earliest date of
  any record imported for it. It is a real date from the sheet, but it is
  NOT the day food restriction began;
- the cohort's notes say so in words. In the **1. Cohort Setup** tab, the
  *Existing Cohorts* table shows the placeholder under *Start Date*, and its
  *Notes* column begins `auto-created by the tracking-sheet import`. (The
  whole note, which says "start date NOT known", is in **7. Database
  Browser**, table *Cohorts*.);
- the warnings of the import that created it name the placeholder date.
  Later imports do not repeat this; the note stays until it is replaced;
- **it is replaced on its own.** The first import that finds a dated day-0
  weigh-in (or a dated tray row) writes the real start date, and the note
  changes to `Imported from <workbook> (start date read from the sheet,
  replacing a placeholder ...)`. What you do: type the day-0 weigh-in into
  the sheet.

If the sheet has no date at all for a cohort that is not in the database yet,
nothing is filed under it: its rows wait, and a warning says they will land
on the first import after a dated day-0 weigh-in is typed.

A cohort first seen in a video (videos usually arrive before the sheet) is
created the same way, with that video's recording date as its placeholder
start and a note beginning `auto-created from video`; the first sheet import
that finds a start date replaces it in the same way. (One exception remains,
on the video side: if a video's file name carried no date, the video import
would use the day it ran. If a cohort's start date looks wrong, read its
note.)

### A year typed wrong on the tray-scoring tab is read as meant

On `3b_Manual_Tray`, a session typed with last year's year (`2025-09-29`
for `2026-09-29`) would be filed a year away from the rest of the cohort.
That does more damage than losing the day: phases are worked out from the
gaps between a cohort's sessions, so a day a year early makes every real
session look as if it came after a year-long break, and every one of them
gets the wrong phase. The hand-score check also cannot find that day's
scores for its videos.

So the import checks the tab's dates first. It takes the year most rows
carry. A date in any other year counts as a typo only if changing just its
year puts it between that year's first and last session. A cohort that runs
over New Year keeps its December and January sessions as typed, because
neither lands inside the other year's span.

When it finds one, the import:

- reads the row under the corrected date;
- moves scores an earlier import already filed under the typed date onto
  the corrected date (only scores the sheet import wrote; if a score is
  already on the corrected date, the old one is left where it is and a
  warning names it);
- works the cohort's phases out again from the corrected dates;
- lists a warning starting `REPAIRED`, naming the tab, the date as typed and
  the date it was read as. The warning appears on every import until the
  date is corrected in the sheet. **What you do:** correct the date in the
  sheet.

Only the tray-scoring tab is checked this way.

### A weekend date among one day's rows is read as that day

Some workbooks do not type the dates on `3b_Manual_Tray`. Each date cell is a
formula such as `=A419+1`, which means "the date a fixed number of rows up the
sheet, plus one day". That only works while every day's block has exactly the
same number of rows. **Cut a row (an animal leaves the cohort) or leave one
blank, and every formula below it counts from the wrong row** -- its rows can
end up dated on another day. The scores then sit on a day with no videos, and
that day's videos find no hand scores.

The import catches the case it can line up:

- a run of rows dated on a **Saturday or Sunday** (recording never happens at
  weekends),
- sitting between rows that all carry **one and the same weekday** date,
- with none of its animal/tray pairs already on that weekday,

is read as that weekday. As with a year typo, scores an earlier import filed
under the weekend date are moved to it, and a warning starting `REPAIRED`
names the rows, for example: `REPAIRED CNT_NN 3b_Manual_Tray: rows CNT_NN_02 P1,
CNT_NN_03 P1 are dated 2025-10-25 (a Saturday, ...) but sit among rows dated
2025-10-28, and are read as 2025-10-28.` It shows in the red panel and on the
cohort's FIX THESE file until the sheet is fixed. **What you do:** type the
date into those cells (a typed date never moves).

What it leaves alone, on purpose:

- a **weekday** date out of place among the rows -- that may be a real make-up
  session typed among the regular rows;
- a **whole day's block** on a weekend between two different days -- with no
  neighbours to read it from, the real day cannot be lined up, and the block
  is imported as typed.

**When you remove an animal from a cohort's sheet,** the safest way is to leave
its rows where they are and clear them, or to type the dates in the rows
below; cutting the rows is what moves formula dates.

### Another cohort's animal is never filed here

An animal belongs to the cohort its id says: in `CNT_NN_SS` the middle number
is the cohort. A row in this cohort's workbook that names **another cohort's
animal** (an animal borrowed between studies, a row left over in a workbook
copied from the previous cohort, or a typo) is not imported from this
workbook -- on any tab. Filed here it would become a second, wrong animal, or
overwrite the real animal's record.

What you see instead: one line per tab, listing those ids:
`<tab>: rows not imported: these animals belong to cohort <other cohort>, not
to this workbook's cohort <this cohort>, ...`. It appears on every import
while the rows are there.

What to do: if the id is a typo, correct it. If the animal really is the
other cohort's, its rows are imported from that cohort's own workbook -- make
sure they are recorded there. A cell that is not an animal id at all is
listed too, quoted, so you can correct it. An animal cell may also hold just
the animal's number, `5`; it is read as this workbook's cohort. A blank animal
cell under a table -- averages, padding -- is skipped without a word.

**Each such cell is named once per import.** Usually the line above names it.
On `0a_Metadata` and the two surgery tabs, the step that copies each animal's
details reads the same cells, and it names a row itself only where the import
does not read that cell:

- the animal column has a heading the import does not look under. The import
  reads the animal from `SubjectID` or `Animal` on `0a_Metadata`; from
  `Subject_ID`, `Animal`, `SubjectID` or `Subject` on the contusion tab; and
  from `Subject_ID`, `Animal`, `SubjectID` or `Mouse ID` on the injection
  tab. Under any other heading (`Animal ID`, say) the import reads no animal
  from that tab at all -- on a surgery tab it then reports every weighed row
  as having a blank animal id -- so head the column with one of these;
- or the import reads the cell differently: text such as `n/a`, which it
  takes as an empty cell on `0a_Metadata`, or an id written without
  underscores (`CNT0103`), which it accepts but the copy does not.

That line reads `<workbook>: '<tab>' row with unrecognised animal ...
skipped`, or, for another cohort's id, `<workbook>: '<tab>' row for animal
<id> skipped: it belongs to cohort <other cohort> ...`. The fix is the same as
above. On a surgery tab only a row that counts as done is ever named for its
animal cell: the id on a plan is not a problem until the surgery happens.

### The ladder tab is ignored when it is empty

The ladder test was abandoned. On `6_Ladder`, rows without step counts, and a
tab with nothing but its heading, are ignored without a word. A row that does
have step counts is still imported, but only for an animal the database
already knows -- a ladder row never creates an animal -- and anything wrong
with such a row is named.

### Every way to start an import

| How you start it | Recorded in the history as | Waits for the lock | When it fails |
|---|---|---|---|
| **Import all sheets** / **Import selected** (Tracking Sheets tab) | `gui` | yes | a `FAIL` line in the log box and a box with the summing-up line; the row turns *LAST IMPORT FAILED* |
| **Update the database now** (11. Where Is My Data tab), or `mousedb update` | `update` | yes | the sheets step shows `[FAIL]` and the whole update reports FAILED |
| the scheduled sheet import (INSTALL.md, "Scheduled jobs") | `scheduled` | yes | the job ends with exit code 1, which the scheduler shows as a failure |
| `mousedb-sheets import [--cohort CNT_NN]` | `manual` | yes | exit code 1 |
| `mousedb import --all` | `mousedb-import` | yes | exit code 1, after `[FAIL] the import did not complete for every cohort` |
| `mousedb import --file <workbook>` | `mousedb-import --file` | yes | exit code 1 |
| `mousedb-refresh-snapshot --import-sheets` | `hourly-refresh` | yes | a `[warn] ... FAILED` line; the snapshot carries on with what the database already holds |
| `python manual_setup_check.py`, the install check in the repository's top folder (step 4: it offers an import only into a database that holds no animals yet, a dry run first) | `manual_setup_check` | yes | `FAIL` lines, then `[!] Import finished with failures` |
| **Import from Excel...** (1. Cohort Setup tab) | `gui_cohort_setup` | yes, and asks you first | an error box |

**All of these except the last are one and the same import.** They choose the
file the same way (see "Which file is read"; `--file` reads the file you name,
but refuses the files the folder route never reads), import it the same way,
refresh each animal's details (only after an import that saved -- see "When
an import fails"), write one line per cohort into the import history, wait
for the same lock, and report a failure as a failure. Only the label in the
history differs. (The install check used to read every matching file itself,
outside all of this; it now goes through the same import.)

**Import from Excel... (Cohort Setup) differs in three ways:** it reads the
one file you pick (any file whose name says which cohort -- it does not apply
the naming rule below and does not refuse an archive copy, so pick the
cohort's live sheet from the sheets folder); it does not refresh each
animal's details (the next import from the Tracking Sheets tab, or the
scheduled one, does); and it offers Replace.

**The lock** is one file under `<mousedb_root>/logs` that every job writing
the database takes in turn (INSTALL.md, "What happens when another program
has the database locked"). An import waits for it up to 25 minutes per
cohort; after that, that cohort FAILS with `could not acquire the
database-task lock after 1500s`, and the others carry on.

**A dry run** (`--dry-run`) reads everything and writes nothing: not the
database, not the import history, and it does not wait for the lock. (One
detail: `mousedb import --dry-run` first brings the database's table layout
up to date, as `mousedb init` does. It imports nothing.)

### When an import fails, nothing from that workbook changes

One cohort's import is two steps on one workbook: the import of its tables
(animals, weights, scores, surgeries, ...), then the copy of each animal's
details. The first saves the workbook whole or not at all: if it records an
error, nothing from that workbook is saved. The second then does not run, so
each animal's details stay exactly as they were and still agree with the rest
of the database. (They used to be replaced even when nothing else was saved,
so for that cohort the two described different versions of the sheet.)

You see it on the line under the cohort's `FAIL` line:

```
FAIL CNT_NN Connectome_NN_Animal_Tracking.xlsx <the error>  [N warnings]
       [!] animal details left as they were: nothing from this workbook was saved (see the error), ...
```

- In a dry run the line reads `[!] animal details would be left as they are:
  ...`, so you can see beforehand what a real import would do.
- When the import crashed, or never got the lock, it reads `[!] animal
  details left as they were: the import stopped on the error before it
  reached them`.
- The import history records the same sentence for that cohort, under
  `animal_records_left_unchanged`.
- It appears wherever the `FAIL` line does: the terminal, the Tracking
  Sheets tab's log box, the install check.

What to do: fix the error named on the `FAIL` line and import again. The
animal details are refreshed by the next import that saves. Nothing else is
needed.

### Where the warnings appear, and what "[N warnings]" means

| Where | What you see |
|---|---|
| `mousedb-sheets import`, `mousedb import --all`, the install check, and the scheduled job's own output | one line per cohort: `OK` or `FAIL`, the cohort, its sheet, what landed (or why it failed), then `[N warnings]`; under it the first 3 warnings and `... and N more (all of them: ...)`. A last line sums up: `Sheets: X of Y cohorts imported, 0 failed, W warnings` |
| `mousedb import --file <workbook>` | the same, with the first 10 warnings |
| **Update the database now** | only the summing-up `Sheets: ...` line |
| the Tracking Sheets tab's log box | the same as `mousedb-sheets import` (the first 3 warnings per cohort); if anything failed, a box repeats the summing-up line |
| Cohort Setup, **Import from Excel...** | the preview (a dry run that writes nothing) lists the first 8 warnings |
| the import history, `<mousedb_root>/logs/sheet_imports.jsonl` | one line per cohort per import, holding up to 50 of its warnings and `warning_count`, the true total |

**"[N warnings]"** is how many warning lines that cohort's import produced.
One line covers one tab and one reason and names every animal it applies to
(the first 40, then `... and N more`); a held surgery row gets a line of its
own. Rows left out that need nothing from you -- plans that are not overdue,
the ladder's empty rows, empty lines under a table -- are not counted and
not mentioned.

To read every warning of one cohort:

- without a terminal: **1. Cohort Setup** -> **Import from Excel...** -> pick
  the cohort's sheet in the sheets folder -> read the preview (the first 8)
  -> press **Cancel**. Nothing is written;
- in a terminal: `mousedb-sheets import --cohort CNT_NN --dry-run --json`.
  Nothing is written.

### After every import: the hand scores for the reaching pipeline

Before the reaching pipeline files a video, it compares the video's pellets
with the session's hand scores. It does not read the database for this (it
never depends on mousedb); it reads one file mousedb writes:

- `<mousedb_root>/exports/hand_scores/hand_pellet_scores.csv`: every hand
  pellet score, one row per pellet, keyed by the session the way the
  pipeline names its video (`20250709_CNT0207_P1`: date, animal without
  underscores, tray type and number);
- `hand_pellet_scores.json` beside it: when the file was written; for each
  cohort, when its last successful sheet import finished; which cohorts the
  sheets folder has a sheet for; and when the last import of EVERY sheet
  finished. The pipeline uses these to tell "the sheet has not been imported
  since this video was ready" (the video waits) from "it has been, and has no
  scores for this session" (the session was never scored by hand: there is
  nothing to compare, so the video is filed without the comparison). A cohort
  with no sheet at all is settled by the next import of every sheet.

Every import that writes (the scheduled job, the Tracking Sheets tab,
**Update the database now**, `mousedb-sheets import`) rewrites the file when
it finishes, and says so in its result: `OK   hand scores for the reaching
pipeline: N pellets of M sessions`. If the file could not be written, the
line starts `FAIL`, and the import counts as failed (the scheduled job's run
shows as failed), because the pipeline would go on reading the old scores.
To write it by hand: `mousedb hand-scores`. Dry runs never write it.

### Things to fix: the red panel and the FIX THESE file

Some things only a person can fix, mostly in the sheet:

- a video the reaching pipeline cannot file because its check against the
  hand scores cannot run (a setting or file to fix). A session with NO hand
  scores is not listed: once the sheet has been imported after the video was
  ready and still has none, it was never scored by hand -- there is nothing
  to compare, so nothing can disagree -- and the video is filed;
- a date the import had to read as a typo (`REPAIRED ...`, see "A year typed
  wrong on the tray-scoring tab"): correct it in the sheet;
- a sheet import that failed.

They are shown in two places:

- **Where Is My Data**: a red **Needs your attention (N)** box at the top,
  one line each (hidden when there is nothing). It is gathered again each
  time the tab refreshes.
- **Beside the sheet**: after every import that writes, each cohort with
  anything listed gets `!! <cohort> - FIX THESE.xlsx` in the sheets folder,
  next to its workbook, with one line per thing to fix. It is rewritten after
  every import and **removed by itself once nothing is left**. Do not edit
  it; it is not read. The import's output names it (`[!] CNT_05: things to fix
  in the sheet -- see '!! CNT_05 - FIX THESE.xlsx' ...`), and says so if the
  file could not be written (for example because it was open in Excel at
  the time).

The letter (ASPA) workbooks are frozen, so nothing is listed for them.

### Which file is read: the name, and pins

**The name.** A file is a cohort's sheet only if its name STARTS with its
project's sheet name (see "Which projects' sheets are read") up to `.xlsx`,
and ends `.xlsx`; the cohort is then that project's cohort of the number in
the name. Anything may sit between:
`..._Animal_Tracking1.xlsx`, `..._Animal_Tracking (2).xlsx`,
`..._Animal_Tracking(<a note>).xlsx`, a copy the sync program made. These are
never read as a sheet, whatever else they are called:

- an Excel lock file, `~$...` (Excel makes one while the workbook is open);
- a name that only CONTAINS the sheet's name, such as `Copy of <the sheet's name>`;
- a name that fits two projects' sheet names at once (rename it so it fits one);
- a file a mousedb tool wrote from a sheet: `_ODC`, `_summary_matrix`,
  `_updated` or `_fixed` anywhere after the sheet's name;
- anything inside a folder named `archive`, `_archive`, `backup`, `generated`
  or `old_cohort_scripts` (in any letter case), however deep -- copies kept
  there are out of date.

A file that looks like a sheet but is not read is never dropped in silence:
`mousedb-sheets status` and the import commands list it as
`[!] not read as a sheet: <file> -- <reason>`, and `mousedb cohort-sheets`
lists it as `NOT read: <file> -- <reason>`. The Tracking Sheets tab lists it in
its red box as `NOT READ AS A SHEET: <file> -- <reason>`. If such a file
really is a cohort's sheet, rename it so its
name starts with its project's sheet name. Until then, if it is that
cohort's only file, the cohort has no row in the table and is not imported.

**When several files match a cohort** -- a `(2)` copy, a dated backup, a draft
with a `1` on the end -- the system does not guess silently: the status says
**N files match -- choose**, and until you choose, it reads the most recently
edited one and says so. The import does not stop.

To choose: press **Refresh**, select the row, press **This is the sheet**,
pick the file from the list. That choice is called a **pin**. It is shown as
`[pinned]` and kept in `~/.mousedb/config.json` on THIS machine only -- so
pin on the machine that runs the scheduled import, too. To forget a pin,
press **Forget a pin...** and pick the cohort (the list shows every pinned
cohort, broken pins included); the cohort's newest file is read again. From a
terminal: `mousedb-sheets pin CNT_NN "<exact file name>"`; to forget it,
`mousedb-sheets unpin CNT_NN`.

A pin must name a file that is in the sheets folder now. Pinning another
cohort's file, a name that is never read as a sheet (see above), or a name
that is not in the folder (a typo, a file not synced yet) is refused in one
line saying why, and nothing is saved; for a name not in the folder the line
lists the files that cohort does have. A name that differs from a real file
only in capital letters is accepted and saved with the file's real spelling.

In the tab, if the file you pick was renamed, moved or deleted after the
table was last read, a *Not pinned* box gives that reason and says *Nothing
was pinned*; the table is then read again, so select the row and choose from
the files that are there now. (This used to close the whole mousedb window.)
An ASPA row has nothing to choose -- one workbook per cohort letter -- and the
button says so.

**A broken pin.** If the pinned file is later renamed, moved or deleted, the
pin is broken. The cohort is then read from its newest file, as if nothing
were pinned; a cohort with no other file has no sheet at all and is not
imported. This is reported, not hidden:

- `mousedb-sheets status` prints one `[!]` line under that cohort's row,
  naming the pinned file, the file read instead, and how to fix it;
- `mousedb cohort-sheets` prints the same as a `PIN BROKEN: ...` line;
- the Tracking Sheets tab names it in its red box as `PIN BROKEN: <the same
  sentence>`, and the cohort's *Sheet file* cell reads `[PIN BROKEN -- see
  the red box]` in red. A cohort with no file left has no row in the table;
  the red box still names it.

To fix it, pin the right file again (above), or unpin (**Forget a pin...** in
the tab). If the cohort has no file left, put the workbook back in the
folder, or unpin.

**Naming a cohort in a command.** `--cohort`, `pin`, `unpin` and
`mousedb cohort-sheets --cohort` take a cohort as `CNT_NN`, `cntNN`, `CNTNN`,
`NN` or `N` (for example `CNT_05`, `cnt05`, `05` or `5`). Another project's
id (`PROJB_05`), an animal id (`CNT_05_01`) or anything else is refused in
one line, and nothing is imported or pinned for it. An ASPA cohort is named
`ASPA_NN`, or by its letter, `ASPA_J`.

**Importing one named file** (`mousedb import --file <workbook>`). The file is
imported exactly like a cohort from the folder, and -- if the import saves --
that cohort's animal details are replaced with what the file holds. It is
REFUSED -- nothing is
changed, not even the import history -- when the folder route would never
read it: a copy inside one of the folders listed above, or a name that is
never read as a sheet. You then see three lines:

```
REFUSED: <the file> was not imported.
  Why: <which folder, or what is wrong with the name>.
  Instead: import CNT_NN's live sheet, Connectome_NN_Animal_Tracking.xlsx, from the sheets folder: mousedb-sheets import --cohort CNT_NN
```

Run the command on the *Instead* line. If the refused copy really is the
newest record of its cohort, move it into the sheets folder under the sheet's
own name first. A correctly named copy kept somewhere else (Downloads, a USB
stick) IS imported -- and afterwards the Tracking Sheets tab may call the
cohort *Up to date*, because it compares only the file's name and when it
was edited. Import from the sheets folder when you can.

### Import or Replace (Cohort Setup tab)

**1. Cohort Setup** -> **Import from Excel...** -> pick a cohort's workbook.
A preview comes first and writes nothing: what would be imported, the first
warnings, and -- for a cohort already in the database -- what a Replace would
remove and keep. If the preview shows ERRORS, only *Close* is offered.
Otherwise choose:

- **Import (add-only)** -- adds the rows the database does not have yet.
  Nothing already in the database changes.
- **Replace (delete + re-import)** -- only for a cohort already in the
  database. It removes the rows that earlier imports of this sheet wrote and
  imports the sheet again, so corrected or deleted cells take effect.

What a Replace removes: the cohort's weights, pellet scores, ramp entries,
surgeries, ladder entries and virus preps **that the sheet import wrote**,
and the cohort's archived summary values (only the sheet import writes those).

**What a Replace keeps: every row typed in by hand in this program.** The
entry tabs (Surgery Entry, Weight Entry, Ramp Entry, Testing Entry, Virus
Prep, ...) record the name of the person who saved each row, and a Replace
never removes a row that carries a person's name -- nor one with no record of
who entered it. Animals, the cohort (apart from its start date, below), the
animal-details copy, video data and tissue data are not touched.

- Where a kept row and the sheet have the same thing (the same animal on the
  same date; for a pellet score, also the same tray and pellet; for a
  surgery, also the same type; for a virus prep, the same prep date), the
  kept row stands and the sheet's value is NOT imported. You
  get one notice for each, e.g. `Replace kept 1 body weight row for <animal>
  on <date>, typed in by <name>: ... the sheet's value was NOT imported --
  the kept value stands. Correct it by hand if the sheet is right.`
- Saving a day in the Ramp Entry tab marks that day's ramp rows as entered by
  you, even when you changed nothing, so a Replace keeps them (and says so).
- Where the sheet gave back fewer rows of a kind than were removed, a notice
  says how many (`Replace removed N ... rows ..., and the sheet gave back
  M ...`): either rows you took out of the sheet, or rows skipped for a reason
  the other warnings give.
- The confirm box says whether the cohort's start date will change (to the
  start date the sheet gives, by the rule under "No date is made up").
- **All or nothing.** If the re-import fails, nothing at all is changed --
  not the removal, not the start date -- and the box says `Replace of <cohort>
  undone -- nothing in the database was changed`.
- Both Import and Replace wait for a database task that is already writing.
  You are asked first: *Database busy ... Wait for it to finish, then
  import?* -- *Yes* waits (the window does not respond for up to 25 minutes),
  *No* changes nothing.
- When a Replace finishes, a box lists what was removed, imported and kept,
  and the first notices; the import history lists every kept row.

### Messages and what to do

`<tab>`, `<animal>`, `<date>` and `<cohort>` stand for what the message names.

| The message starts with ... | What to do |
|---|---|
| `<tab>: surgery planned but not recorded: its date is more than 14 days past ...` | If the surgery was done, type the weight on the day; if not, change the date to the new planned day, or clear it. |
| `<tab>: surgery for <animal> (<date>) not imported -- its 'Subject_Weight (g)' cell reads ...` | Type the weight in grams: `22`, `22.4` or `22 g`. |
| `... -- it has a device reading (...) but no body weight ...` | Type the animal's weight on the day of the surgery. |
| `... -- it is weighed (N g) but has no Surgery_Date ...` | Type the surgery date. |
| `... -- it is weighed (N g) but its 'Surgery_Date' cell reads ..., which is not a date ...` | Replace the cell with a real date (`2026-03-01`). |
| `<tab>: no 'Subject_Weight (g)' column ... (restore that column heading)` | Put the heading back exactly as `Subject_Weight (g)`. |
| `<tab>: surgery not imported: the weight on the day is typed but the animal id is blank ...` | Type the animal id in the sheet row it names. |
| `<tab>: rows not imported: these animals belong to cohort <other cohort> ...` | Correct the id if it is a typo; otherwise record those rows in that cohort's own workbook. |
| `<tab>: rows not imported: the animal cell is not an animal id ...` | Correct the quoted cell to the animal's id (`CNT_NN_SS`) or its number. |
| `<workbook>: '<tab>' row with unrecognised animal ... skipped` | Named instead of the line above where the import itself does not read that cell: another heading, or text it reads differently such as `n/a` or `CNT0103` (see "Another cohort's animal is never filed here"). Correct the cell to the animal's id (`CNT_NN_SS`). |
| `<workbook>: '<tab>' row for animal <animal> skipped: it belongs to cohort <other cohort> ...` | As for `... these animals belong to cohort ...`: correct the id if it is a typo; otherwise record those rows in that cohort's own workbook. |
| `Animal <animal> was NOT filed under cohort <cohort> ...` | Correct the id, or record the rows in the animal's own cohort's workbook. |
| `Cohort <cohort> was not in the database ... PLACEHOLDER start of <date> ...` | Type the day-0 weigh-in into the sheet; the next import replaces the placeholder. |
| `Could not determine a start date for cohort <cohort> from the sheet ...` | The same: type the dated day-0 weigh-in. (It comes with the line above, or the one below.) |
| `Cohort <cohort> is not in the database and its tracking sheet gives no start date, so nothing could be filed ...` | Type a dated day-0 weigh-in; the waiting rows land on the next import. |
| `0_Injection_Calculations: the virus prep for '...' was NOT imported -- this calculator has no date ...` | Add a date row (e.g. `Prep_Date`) to the calculator, or enter the prep as a dated batch block. |
| `0_Injection_Calculations: vertical format is empty template` | Nothing: the calculator is still empty. |
| `6_Ladder: ladder result not imported: the animal has no other record ...` | Record the animal elsewhere in the sheet first (`0a_Metadata`, a weight or a tray row); the next import takes the ladder row. |
| `6_Ladder: ladder result not imported: a step-count cell holds something that is not a number` | Correct the cell to a number. |
| `6_Ladder: ladder result not imported: the row has step counts but no date` (or `... but no animal id`) | Fill in the missing cell. |
| `Nothing from <workbook> was saved: the import stopped on the error(s) above ...` | Fix the error listed with it and import again; none of that workbook's weights, scores, surgeries and so on were written. |
| `[!] animal details left as they were: ...` (in a dry run, `... would be left as they are: ...`) | Nothing more than for the `FAIL` line above it: fix that error and import again. The animal details are refreshed by the next import that saves. |
| `REFUSED: <file> was not imported.` | Run the command on its *Instead* line. |
| `FAIL ... no tracking sheet for <id>: no sheet name is set for project <P> in the lab settings ...` | If `<P>` is a typo, name the cohort correctly (`PROJECT_NN`). If it is a real project whose sheets should be read, set its sheet name (see "Which projects' sheets are read"). |
| `FAIL ... '<NN>' names no project, and the sheets folder holds more than one project's sheets ...` | Give the whole cohort id, for example `PROJA_05`: with two projects a bare number could mean either. |
| `FAIL ... '<text>' is not a cohort id ...` | The same: `CNT_NN` or `NN`, not an animal id. |
| `FAIL ... no tracking sheet for CNT_NN in the sheets folder (cohorts with a sheet: ...)` | Check the number; `mousedb-sheets status` lists every cohort's sheet. |
| `FAIL ... Could not read the workbook: it is open in Microsoft Excel (process N) on this computer ...` | Someone has the workbook open in Excel on the computer that runs the import (open in a web browser does not count; open in the Excel program on that computer does). Save and close it there. Nothing from that workbook was imported this time; the next import (the nightly one, or **Import selected**) reads it. Any other program named: close that one. |
| `FAIL ... Failed to open Excel file: [Errno 13] Permission denied ...` | The same cause, when Windows could not say which program: close the workbook wherever it is open on that computer. |
| `FAIL ... could not acquire the database-task lock after 1500s ...` | Another database task held the lock for 25 minutes. Import again later; if it keeps happening, find the task that is stuck. |
| `Sheets: NOTHING IMPORTED -- ...` | Read the reason on that line; usually the sheets folder is not set or not found (see "Setting the folder"). |
| `[!] not read as a sheet: <file> -- <reason>` (or `NOT read: ...`) | If it is a cohort's real sheet, rename it so the name starts with its project's sheet name; otherwise nothing. If the reason is that the name fits more than one project, rename it so it fits one. |
| `[!] CNT_NN is pinned to <file>, which ...` (or `PIN BROKEN: ...`) | Pin the right file again, or unpin (see "A broken pin"). |
| `[FAIL] cannot pin '<file>' as the sheet for cohort NN: ... -- nothing was pinned` (in the tab, a *Not pinned* box) | Copy the exact name (from the list in the message, or `mousedb-sheets status`) and pin again. In the tab the table is read again: select the row and choose from the files that are there. |
| `[FAIL] <folder> ...; the sheets folder setting was not changed` (in the tab, a *Not the sheets folder* box) | Give the folder that holds the live tracking workbooks. If it does hold them, check the sheet names are set (`mousedb sheet-projects --show`): with none set, no file there counts as a sheet. |
| *Recorded -- but another folder is in use* | The `MOUSEDB_COHORT_SHEETS` environment variable names another folder, and it comes first. Clear or change it. |
| `'<text>' is not a cohort name ...` or `<P> is not a project with sheets here ...` (New cohort sheet) | Type the new cohort's name as `PROJECT_NN`, for example `PROJA_07`. A project missing from the list needs its sheet name set first ("Which projects' sheets are read"). |
| `project <P> has no template yet ...` or `project <P>'s template ... is not there` | Make it: `mousedb sheet-template make <P> "<a cohort's workbook>" --cohort <P>_NN` ("Making or changing a project's template"). |
| `<cohort> already has a workbook in <folder>: <files> ...` | Nothing was written. For a new cohort, use its own number; if this cohort really needs a fresh sheet, move its existing file(s) out of the folder first. |
| `'<text>' is not a subject number or range ...` / `a subject number is given twice` | Type the subject numbers as a range or a list: `01-16`, or `3,4,16,17,19`. |
| `The sheets folder <folder> is not there any more ...` | Press **Refresh**; if the folder moved, press **Set sheets folder...** first. |
| `REFUSED: ...` then `Nothing was written.` (`mousedb new-sheet` or `mousedb-make-sheets`, exit code 3) | The same refusals as the button; the line says which. |
| `[!] <tab>!<cell>: ... left as written` (after a new sheet is created) | That formula could not be pointed at the new animal's row; it is as it was in the template. Check it in the new sheet, and fix the template if it should be different. |
| `Replace kept N ... rows for <animal> ...` | If the sheet's value is the right one, correct the kept row by hand in the entry tab it came from. |
| `Replace removed N ... rows ..., and the sheet gave back M ...` | Nothing, if you removed rows from the sheet; otherwise read the other warnings for why rows were skipped. |
| `Replace of <cohort> undone -- nothing in the database was changed ...` | Fix the error listed under it and Replace again. |

## Which projects' sheets are read (once for the whole lab)

Every project that keeps a tracking workbook per cohort has a **sheet name**:
the pattern its workbooks are named by, with `{NN}` where the cohort number
goes. Only files that start with a project's sheet name are read as sheets,
and the project of the name decides which cohort the workbook's animals go
under.

The names live in ONE settings file for the whole lab, beside the database
on the shared storage: `<mousedb_root>/config/mousedb_lab_config.json` (the
`MOUSEDB_LAB_CONFIG` environment variable can point elsewhere). Every
machine and every login reads that same file, so a project is added once,
from any machine, and nothing is set per person.

See what is set:

```bash
mousedb sheet-projects
```

Add a project, or change its name (for example a second project whose
workbooks are named `ProjectB_01_Animal_Tracking.xlsx`,
`ProjectB_02_Animal_Tracking.xlsx`, ...):

```bash
mousedb sheet-projects --set PROJB "ProjectB_{NN}_Animal_Tracking.xlsx"
```

- `PROJB` is the project's code: the capital letters its cohort ids start
  with (`PROJB_01`, `PROJB_02`). It must be exactly that, because the
  cohorts and animals are filed under it.
- The name must hold `{NN}` exactly once, have text before it, and end in
  `.xlsx`; anything else is refused and nothing is changed.
- After setting it, press **Refresh** in the Tracking Sheets tab: that
  project's cohorts appear as rows and are imported like every other.

**There is no built-in name.** With no project set, NO file in the folder
counts as a sheet: the tab's table is empty, the import says *no tracking
sheet* and names the command above, and nothing is imported. This is
deliberate -- a guessed name is a wrong import waiting to happen.

**Two projects, the same cohort number.** `PROJA_02` and `PROJB_02` are two
cohorts with two workbooks; neither is ever given the other's. A bare number
(`02`) names a cohort only while one project is set; with more, give the
whole id.

**A name that fits two projects' sheet names** (say one name is the start
of the other) is not read as either: it is listed as *not read as a sheet*
with that reason. Rename the file so it fits one.

## Setting the folder (once per machine, and if it ever moves)

Press **Set sheets folder...** and pick the folder that contains the
tracking workbooks. **Open folder** opens the current
one in Explorer.

A folder that holds no file read as a sheet, or that is inside an archive or
snapshot folder (the folder names listed under "Which file is read"), is
refused: a *Not the sheets folder* box says why and lists any files there
that look like sheets but are not read, and the setting stays as it was.
From a terminal, `mousedb-sheets set-dir "<folder>"` applies the same tests
and also refuses a folder that does not exist. Both record the folder in
full, so a folder typed relative to where you stand still works for the
scheduled job and the GUI.

An environment variable, `MOUSEDB_COHORT_SHEETS`, comes before the recorded
setting when it names a usable folder. If it is set on the machine, the tab
says *Recorded -- but another folder is in use* and names the folder still
being read; clear or change the variable for your choice to take effect.

If the shared folder is ever moved or renamed, every machine that imports
needs this done again -- the tab will show *no tracking-sheet folder is
configured* until it is.

The folder path is deliberately never written into the code (it typically
contains a username and the organisation's folder names, and the code is
public); it lives only in that local config file. The same is true of every
other location mousedb uses -- run `mousedb config --show` to see them.

## Starting a new cohort

A new cohort's workbook is made from its project's **template**: an ordinary
workbook, made once from a cohort sheet the lab is happy with, that says
what every new sheet of that project looks like -- its tabs, columns,
formulas, colours and the schedule of rows laid out ahead (the days, the
phase of each day, the trays). Whatever the template looks like, a new sheet
looks like. A project with no template cannot get a new sheet; the button
says so and names the command that makes one (below).

Press **New cohort sheet...**, type the new cohort's name, then its subject
numbers -- a range, a list, or both: `01-16`, or `3,4,16,17,19` (animals keep
the numbers they were given, so a cohort need not start at 01). A box then
shows the exact file name, the folder, how many animals and the template,
and asks *Yes* or *No*; *No* changes nothing. On *Yes* the workbook is
created in the sheets folder. The tab stays usable while it is built (a
minute or less). Fill it in as the cohort runs; import whenever you like.

**What the new sheet holds.** Every tab of the template. In every tab with an
animal column, the template's rows for one model animal are repeated for
each new animal, in the order you typed them, day by day as in the template.
Each animal's formulas point at its OWN rows: its weight percent at its own
starting weight, its tray totals at its own tray rows; a mean "over every
animal on a day" covers exactly the new animals. No dates are filled in
(they are typed as each day happens), and no measurements. If a formula
could not be pointed at the right row, the log box lists it (`[!] ...`) and
the formula is left as it was in the template, never guessed.

This button is the only thing in the mousedb window that writes into the
sheets folder, and it only ever creates a NEW file. It never replaces a file
and never closes anything:

- **The name must be exactly `PROJECT_NN`** -- a project with sheets here
  (see "Which projects' sheets are read"), one underscore, two digits, for
  example `PROJA_07`. Small letters and stray spaces are forgiven
  (` proja_07 ` is taken as `PROJA_07`). Anything else is refused at once,
  and nothing is written: `PROJA_7`, `07`, `PROJA07`, an animal id such as
  `PROJA_05_01`, a project with no sheet name set. The cohort number is never
  guessed from part of what you typed. The file is named by the project's
  sheet name.
- **A cohort that already has a workbook is refused**, and nothing is
  written: `PROJA_07 already has a workbook in <folder>: <file names>`. This
  counts every file the folder would read as that cohort's sheet, and every
  file that only looks like it -- a `..._Animal_Tracking1.xlsx`, a `Copy of`,
  the number without its zero -- because an empty file beside the real sheet
  would be the newest one and be imported in its place. If the cohort truly
  needs a fresh sheet, move its old file(s) out of the folder first. The
  check runs again at the moment of saving, so a file of that name that
  turns up meanwhile is left untouched too.
- **A project with no template is refused** (`project PROJA has no template
  yet ...`), with the command that makes one.
- **Subject numbers that cannot be meant are refused**: not a number or
  range, a range written high to low, a number outside 1-99, or the same
  number twice.
- **It refuses when the sheets folder has gone** (moved, renamed, its drive
  offline) since the last **Refresh**, rather than recreate the old folder
  with one empty sheet in it. Press **Refresh**, and **Set sheets folder...**
  if it moved.
- **It never closes Excel.**

At the end one of three boxes appears: *Created <file>*; *not created*, with
the reason (for example, a workbook of that name turned up in the meantime --
it is left exactly as it was); or *failed*, which says that no existing
workbook was touched. Every refusal ends *Nothing was written.*

From a terminal: `mousedb new-sheet PROJA_07 --animals 01-16` (add
`--output-dir "<folder>"` to write somewhere other than the sheets folder)
follows the same rules. A refusal prints `REFUSED: <reason>` and `Nothing was
written.` and ends with exit code 3; any other non-zero code means it failed.
`mousedb-make-sheets --new --cohort PROJA_07 --animals 01-16 --output-dir
"<folder>"` does exactly the same (`--mice N` means subjects 01 to N;
`--start-date` is accepted and ignored). `mousedb-make-sheets --fix
<workbook> --output-dir <folder>` writes a tidied copy under the input's own
name and never writes over a file that is already there -- not even its own
input -- so give it a working folder, not the sheets folder.

### Making or changing a project's template (once per project)

Pick the cohort sheet the lab is happy with -- the one whose tabs, columns and
schedule new cohorts should copy -- and run, in the MouseDB environment:

```bash
mousedb sheet-template make PROJA "<path to that cohort's workbook>" --cohort PROJA_05
```

`--cohort` is the cohort that workbook belongs to. The workbook is only read.
This writes `PROJA.xlsx` and `PROJA_template_report.txt` into the
`sheet_templates` folder beside the lab settings file
(`<mousedb_root>/config/sheet_templates/`), records it in the lab settings as
the project's template (every machine uses it from then on), and prints the
report. If the project already had a template, the old one is kept beside it
as `PROJA_replaced_<date and time>.xlsx`; nothing is overwritten.

**What the template keeps and clears.** It keeps every tab, column, header,
formula, colour and column width, and each tab's rows for every animal. It
keeps the SCHEDULE -- typed values that are the same for every animal on a
day and change from day to day (the phase, the tray, the day number) -- and,
in tabs with no animal column (a planning or calculations tab), the typed
values. It clears every date, everywhere, and every other value typed in an
animal's row (scores, weights, sex, notes). A value that was the same for
every animal of a one-row-per-animal tab (an anaesthetic, a target level) is
cleared too, because "the same for everyone so far" is not the same as "the
same for every cohort"; the report lists each one. To keep one, make the
template again with `--keep "<tab>:<column header>"` (repeat for each), or
type it into the template in Excel. It moves a frozen pane far down a tab
(where someone last scrolled) to the first animal row, and drops colour
rules a person put on particular cells outside the tables.

**The model animal** is the animal with the most rows in the workbook -- the
one that went through every part of the schedule. Its rows are what each new
animal gets; a day where it has no row (a group of other animals only) is
left out of new sheets, and the report says how many.

**Read the report, then open the template in Excel.** It is the format of
every new sheet: change it there if anything is wrong (delete a laid-out day,
type a value every animal should start with, fix a header). The builder
reads its structure every time; nothing else needs changing.

## If something goes wrong

- **LAST IMPORT FAILED** with a database message (e.g. *NOT NULL constraint
  failed*, *no such column*): a mismatch between the workbook and the
  database rules. Nothing from that workbook was written (an import is
  all-or-nothing per cohort), and each animal's details were left as they
  were. Copy the message and report it.
- **LAST IMPORT FAILED** with *could not acquire the database-task lock*:
  another database task held the lock for 25 minutes. Import again later.
- **A sheet is open in Excel on another machine**: import still works (it
  reads a shared copy); if it ever refuses with *Permission denied*, retry
  in a minute.
- **A cohort's row is missing from the table**: it has no file that is read
  as its sheet. The red box above the table says why -- a `NOT READ AS A
  SHEET` or `PIN BROKEN` line (see "Which file is read"). From a terminal,
  `mousedb-sheets status` and `mousedb cohort-sheets` say the same.
- **A file named `.mousedb-new-workbook-<letters>.partial`** in the sheets
  folder was left by **New cohort sheet...** when it was stopped in the
  middle of saving. Nothing reads it as a sheet; delete it.

## ASPA cohorts (a second project, a second folder)

ASPA's animals live in one frozen workbook per cohort letter (`I.xlsx`,
`J.xlsx`, ...) in a folder of their own (`aspa_data_dir` in the config), and
their manual tray scores sit in a wide tab called `1 - ENTER DATA HERE`:
one row per animal per day, with up to four trays side by side. The import
takes each tray's number from its place in the row -- the first block of
twenty pellets is tray 1, the fourth is tray 4 -- because the `Tray #` cell
above the later blocks is sometimes blank or holds the animal's name. If that
cell holds a number that does not match the block's place, that tray is not
imported and the import lists it as a warning.

A date cell that holds only a day number (typed `30`, Excel shows
`1900-01-30`) is read as that day in the month of the rows around it, when
it falls between the dated rows before and after it; the import lists it as
a `REPAIRED` warning, moves any scores an earlier import filed under 1900 to
that date, and replaces a cohort start date of 1900 with the earliest scored
session. A day that fits neither month is left out and listed as a warning.
They appear in the same table as the other cohorts (project ASPA, ids like
`ASPA_10` for cohort J) with the same status words. Import their scores with
**Import selected** on their rows, or from a terminal:

```
mousedb-sheets import --cohort ASPA_10 --dry-run   # what it would import
mousedb-sheets import --cohort ASPA_10             # one cohort (or --cohort ASPA_J)
```

These wait for the database lock like every other import.
`mousedb-import-aspa-scores --apply` also imports ASPA scores and records the
import, but it does NOT wait for the lock: use it only when nothing else is
writing to the database. **Import all sheets** does not import ASPA scores;
it only refreshes each ASPA animal's details from its workbook's `ODC` tab.
The ASPA workbooks are frozen; if one ever shows *Sheet edited since last
import*, someone changed a finished record, which is worth knowing.

## Where the import history lives

Every import that writes to the database -- from this tab, the update button,
the scheduled job, `mousedb-sheets import`, `mousedb import`, the install
check, and Cohort Setup's Import and Replace -- appends one line per cohort,
success or failure
with the error text and the warnings, to
`<mousedb_root>/logs/sheet_imports.jsonl`. Each line says what started it
(`triggered_by`, see "Every way to start an import"). Dry runs, and requests
refused before any sheet was read (a wrong cohort id, a refused `--file`),
are not recorded. The Status column is computed from that file plus the
workbook's last-edit time.

From a terminal (MouseDB environment active):

```
mousedb-sheets status                             # which file, edited when, imported when
mousedb-sheets import [--cohort CNT_NN] [--dry-run] [--json]
mousedb-sheets pin CNT_NN "Connectome_NN_Animal_Tracking.xlsx"
mousedb-sheets unpin CNT_NN
mousedb-sheets set-dir "<folder>"
mousedb cohort-sheets                             # the folder, files not read, broken pins
```
