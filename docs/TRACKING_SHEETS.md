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

**The sheet wins.** Every import makes the database match the sheet for
the rows the import itself wrote:

- a row the database does not have yet lands;
- a value you corrected in the sheet (a pellet score, a weight, a ramp
  weight, a surgery's readings) is corrected in the database;
- a row you **removed** from the sheet -- or moved to another cohort's
  workbook -- is **removed from the database at the next import**;
- a row **typed in by hand** in this program (the entry tabs record who
  saved it) is **never** changed or removed by an import;
- a sheet that reads as broken -- a tab missing or unreadable, or far fewer
  rows than the database holds -- removes and changes **nothing**, and says
  so loudly. See "The sheet wins" below for exactly what is synced, how you
  see what changed, and what to do when an import refuses.

Each animal's details -- the `0a_Metadata` tab, the two surgery tabs, and
each animal's sex from the tray tab -- are also kept as a copy that the
exports read. That copy is thrown away and taken again from the sheet on
every import made from the Tracking Sheets tab, the update button, the
scheduled job or the commands on this page. Only an import that SAVED the
workbook replaces it: when an import fails, the copy is left exactly as it
was (see "When an import fails" below). **Replace**, in the Cohort Setup
tab, still rebuilds a whole cohort from its sheet on purpose (see "Import or
Replace" below).

Animals do NOT have to be in the sheet before their videos are processed.
`mousedb import-reaches` creates an animal the first time it sees a video
for it; the sheet import fills in the details later. (It is why a late sheet
cannot make video data disappear.)

## Where to do it

Open the mousedb GUI: double-click **MouseDB** on the desktop (or Start menu
-> **MouseDB** folder -> **MouseDB**). From a terminal instead: activate the
MouseDB conda environment (`conda activate MouseDB`, or the full path of the
environment if it was created with `--prefix`) and run `mousedb-entry`. Find the
**10. Tracking Sheets** tab. Everything below is a button on that tab unless
it says otherwise.

## What the table tells you

One row per cohort:

| Column | Meaning |
|---|---|
| Cohort | e.g. CNT_NN |
| Status | **Up to date** -- the database matches the sheet's last edit. **Sheet edited since last import** -- someone changed the workbook after it was last imported; import again. **Never imported** -- import it. **LAST IMPORT FAILED** -- the last attempt hit an error; select the row to read it. **NOT SYNCED** (red) -- the last import found the sheet looking broken and removed and changed nothing; select the row to read why, and see "The sheet wins". **N files match -- choose** -- see "Which file is read" below. |
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

### The sheet wins: corrections and removals reach the database

**Why.** Until this was added the import only ever ADDED rows. Two things
went wrong because of it, and both stayed wrong for weeks:

- a session typed into the wrong cohort's workbook, imported, and then moved
  to the right workbook stayed in the database under the wrong cohort. Its
  date became part of that cohort's schedule, and because each session's
  phase (training, baseline, post-injury test ...) is worked out from the
  cohort's whole set of session dates, one extra date moved the labels of
  the real sessions: baseline read as training, the first post-injury tests
  read as baseline -- in the database and in every export;
- a new cohort's workbook, made by copying the previous cohort's, was
  imported while it still held the previous cohort's weights and surgeries
  under the new animals' ids. Clearing them from the sheet did nothing.

**What every import now does,** for each cohort, for the rows earlier
imports of that cohort's sheet wrote:

| Table | A row is the same row when it has the same ... | Compared and corrected | Removed when |
|---|---|---|---|
| pellet scores (`3b_Manual_Tray`) | animal, date, tray, pellet | the score | its tray (animal, date, tray) is no longer in the sheet, or its cell is now empty |
| weights (`3d`/`3e_Weights`, `1_Weight`, and the weights on `3a_Manual_Ramp`) | animal, date | grams, and % of body weight (see below) | that animal and date has no weight in the sheet any more |
| ramp days (`3a_Manual_Ramp`) | animal, date | weight, % of body weight, food put in and taken out, food eaten | the row is gone |
| surgeries (`4_Contusion_Injury_Details`, `5_SC_Injection_Details`) | animal, date, kind (contusion / tracing) | the readings the import stores (force, displacement, velocity, dwell, surgeon, anaesthetic, notes; for a tracing injection virus, titre, volume, target, weight, survived) | the sheet no longer has a performed surgery of that kind on that date for that animal (a row that has become a plan -- its weight cleared -- counts as gone) |

- **The % of body weight on a weight row** is taken from the weight's own tab
  when that tab has a percentage column (`Weight %` on `1_Weight`,
  `Weight_Pct_Baseline` on the long `3d`/`3e_Weights` layout, `% body weight`
  on `3a_Manual_Ramp`), and otherwise from the `Weight %` column of
  `3b_Manual_Tray` -- the same order the import stores it in. A percentage
  corrected in the sheet is corrected in the database. `87` and `0.87` are the
  same value (a number above 2 is read as a percent), so a percentage typed in
  the other unit is not a change. An emptied percentage cell leaves the stored
  value as it is, because it may have come from the other tab. Percentages
  count toward the "more than 25% of the rows overwritten" guard below like
  any other value: if you correct the percentage on many rows at once, the
  import refuses the weights sync (`NOT SYNCED weights`) until you import that
  cohort with `--allow-large-removal` after checking the sheet.
- A cell that holds something that is not a value (`x`, `?`, `n/a` where a
  score belongs) is **not** an empty cell: the stored value is kept, and the
  usual warning names the cell.
- **Rows typed in by hand are never touched.** Only rows marked as written by
  the sheet import (`entered_by = excel_import`) are compared or removed. If
  the sheet says something different for a hand-typed row, the hand value
  stands and a warning says so (`... typed in by hand (by <name>) and differs
  from the sheet; the hand-typed values stand ...`): correct it by hand if
  the sheet is right.
- **Animals are never removed** by an import, even when the sheet no longer
  lists them: an animal can exist because of its videos alone. One thing
  about an animal IS corrected: a contusion row saying the animal did not
  survive marks it dead on that day, so when such a row is removed (a row
  copied in from another cohort's workbook, say) and nothing left in the
  sheet says the animal died, the death is undone and listed
  (`N deaths undone`).
- **An animal's date of birth, date of death and sex** come from
  `0a_Metadata` into the database where the database has NOTHING for them
  (an animal first created from a video, or from another tab, before its
  metadata row was filled in). A value the database already holds is never
  overwritten -- nothing records whether a person typed it in on purpose --
  so where the sheet says something else, the import warns
  (`0a_Metadata: <animal>'s date of death is <date> in the database but
  <date> in the sheet ...`) and the line also goes into the red attention box
  and the cohort's FIX THESE file. Decide which is right and correct the
  sheet, or the animal in the GUI. A date of death later than today is a
  plan, and is not taken. Every fill is in the audit log.
- When an import removes a session date, or adds one, the cohort's **phases
  are worked out again in the same import**, for its pellet scores and its
  reaches, and the import says how many rows were relabelled. The hand-scores
  file for the reaching pipeline is rewritten at the end of the import as
  always, and the next night's export refresh rebuilds the exports with the
  new labels (it notices relabelled rows even when no row was added).
- **A dry run** (`--dry-run`, or the Cohort Setup preview) says what an
  import WOULD change and remove, and writes nothing.

**How you see what changed.** Every change and removal is recorded three
times:

1. in the import's output, under the cohort's `OK` line, for example

   ```
   OK   PROJA_NN <sheet> {...}  [2 warnings]
          changed from the sheet -- pellet_scores: 40 rows removed in 2 trays no longer in the sheet; session date no longer in the sheet: 2026-03-19
          phases re-derived for PROJA_NN: 120 pellet and 950 reach rows relabelled
   ```

2. in the import history (`<mousedb_root>/logs/sheet_imports.jsonl`), on that
   cohort's line, under `sheet_sync`: every changed value with its old and
   new value, every removed row (a removed tray with its 20 old scores), the
   dates that left the cohort's schedule, and under `phases_rederived` how
   many rows were relabelled. This list is kept whole, never cut short;
3. in the database's own audit log (the `audit_log` table and
   `<mousedb_root>/logs/<date>_changes.jsonl`), one entry per row, with the
   row's old values. A removed row can be typed back from there if a removal
   was ever wrong -- or simply put back in the sheet; the next import adds it.

**When the import refuses (a sheet that reads as broken).** A tab renamed or
missing, a file caught half-saved, a column moved: each of these would look
to the import like rows deleted from the sheet. So an import removes and
changes NOTHING in a table when:

- a tab that feeds it could not be read, or has no usable heading;
- the sheet reads as holding none of that table's rows for the cohort while
  the database holds some;
- it would remove more than a quarter of the rows (trays, for pellet scores)
  earlier imports wrote for the cohort, or overwrite stored values in more
  than a quarter of them -- unless that is 5 or fewer, which is always an
  ordinary edit. Filling in a value that was stored EMPTY never counts: a
  column typed in days later (the food weighed on a ramp day, say) fills
  blanks, while a shifted column overwrites values.

New rows are still added. The cohort's line then carries a warning that
starts `NOT SYNCED <table>: ...`, printed under the cohort's line with
`[!]`; the closing line says `N NOT SYNCED with the sheet -- it read as
broken (<cohorts>)`; the scheduled job ends with exit code 1, so the
scheduler shows a failure; the Tracking Sheets tab shows the cohort as
**NOT SYNCED** in red; and the line is listed in the red **Needs your
attention** box of the Where Is My Data tab and in the cohort's FIX THESE
file beside the sheet.

What to do:

1. Open the sheet and look at the tab the line names. If a tab was renamed,
   a heading changed or rows were cut by mistake, put it right and save. The
   next import syncs normally and the warning goes away.
2. If the sheet is right -- you really did take that many rows out (a
   workbook copied from the previous cohort, say) -- import that cohort on
   its own, saying so:

   ```
   mousedb-sheets import --cohort PROJA_NN --dry-run --allow-large-removal   # see what would go
   mousedb-sheets import --cohort PROJA_NN --allow-large-removal             # do it
   ```

   The flag works only with `--cohort` (never for every sheet at once), is
   recorded in the history line (`allow_large_removal`), and still removes
   nothing from a tab that could not be read or reads as empty. Without a
   terminal, **Replace** in the Cohort Setup tab does the same job: it
   rebuilds the cohort from the sheet, keeping hand-typed rows.

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

Only the tray-scoring tab is checked this way. While several files match the
cohort and none is pinned (see "When several files match a cohort"), the rows
READ are still repaired -- so a day an earlier import already moved to the
date meant is matched there, not stored a second time under the typo -- but no
score already stored is moved. This is decided tray by tray (one animal's
tray on one day), never for the whole day at once: a tray that an earlier
sheet import still has stored under the typed date (an import made before this
repair existed), and not under the corrected date, is read as typed so it
matches those scores, and the warning
`HELD BACK until the cohort's sheet is pinned: re-dating N 3b_Manual_Tray
trays ...` says so. Every other tray of that day is read under the corrected
date. A score a person typed in under the typed date does not count: the
repair never moves it, so it says nothing about where the import filed the
tray. Each tray is therefore stored once, under one date. The import after
you pin the sheet moves the held-back trays. The `Weight %` column of the tab
follows the same tray-by-tray choice.

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
date into those cells (a typed date never moves). While several files match
the cohort and none is pinned, the rows read are still read as the weekday,
but stored scores are not moved: as for a year typo above, a tray the import
still has stored under the weekend date (and not under the weekday) is read
as dated, and a score a person typed in under the weekend date does not
count.

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
| **Import from Excel...** (1. Cohort Setup tab) | `gui_cohort_setup` | yes, and asks you first | a box with the same lines the terminal prints, `FAIL` among them |

**All of these are one and the same import.** They choose the file the same
way (see "Which file is read"; `--file` and **Import from Excel...** read the
file you name, but refuse the files the folder route never reads, such as a
copy in an archive folder), import it the same way, refresh each animal's
details (only after an import that saved -- see "When an import fails"),
write one line per cohort into the import history, wait for the same lock,
rewrite the hand-scores file for the reaching pipeline and the FIX THESE
files (see "After every import"), and report a failure as a failure. Only the
label in the history differs. (The install check used to read every matching
file itself, outside all of this; it now goes through the same import.)

**Import from Excel... (Cohort Setup)** also offers **Replace**, which deletes
what earlier imports of the sheet wrote and imports it again in one step it
can undo whole. A Replace that went through ends like every other import:
the animal details are copied (a tab that is there but now holds no animal
is taken as empty, because a Replace rebuilds the cohort from the sheet on
purpose; a tab that is missing or renamed, or whose table the import refused,
is still left as it was and reported as `NOT SYNCED animal details '<tab>'`,
so the Replace is not called complete -- rename the tab back, or, if it is
meant to be gone, import the cohort with `--allow-large-removal`), one line
goes into the import history (marked `"mode": "replace"`),
and the hand-scores and FIX THESE files are rewritten. A Replace that was
undone changed nothing, and nothing else runs. The box that closes a Replace
says **Import complete** only when the import counts as succeeded the same
way the terminal counts it: a kept animal-details tab or a hand-scores file
not rewritten gives the **Replace saved, with problems** box instead, with
the reason in it. So does an import-history line that could not be written
(`[!] import history not written: ...`): the hand-scores file reads that
line to know the cohort's scores were saved, so until a later import of the
cohort writes one, videos waiting for those scores keep waiting. (Neither button used to
copy the animal details or rewrite those two files, so the reaching
pipeline compared videos with the previous scores until the nightly import.)

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

### When one tab reads as broken, that tab's animal details stay as they were

Even when the import saves, the copy of each animal's details replaces only
the tabs it could really read, one tab at a time (`0a_Metadata`,
`4_Contusion_Injury_Details`, `5_SC_Injection_Details`, the `Sex` column of
`3b_Manual_Tray`, and on a letter workbook its `ODC` tab). A tab's stored
details are left exactly as they were when:

- the tab is **missing** from the workbook, is empty, or has no animal id
  column (a renamed tab used to empty its animal details);
- the tab is there but gives **no animal's details at all** while the
  database holds some from it -- most likely a tab caught mid-save or shifted
  columns, not every row deleted on purpose;
- the import **refused to sync** the table fed by the same tab (see "The
  sheet wins"): the two surgery tabs stay as they were when the surgeries were
  refused, the tray tab's `Sex` when the pellet scores were refused.

This holds for a tab that is missing or renamed just as for one that reads as
empty: a missing tab whose details the database holds is reported too, never
kept in silence (a renamed tab used to keep its old details in the exports
for good while every import said OK).

Each such tab is listed with the other refusals, under the cohort's line:

```
       [!] NOT SYNCED animal details '0a_Metadata': the '0a_Metadata' tab read as holding no animal's details, ...
```

and it counts as a refusal: the cohort is listed as NOT SYNCED in the
closing line and the scheduled job's run shows as failed. What to do: if the
sheet is wrong (a tab renamed, a column moved), fix it and import again. If
the tab really is empty -- or gone -- now on purpose, import that cohort alone
with `mousedb-sheets import --cohort <cohort> --allow-large-removal`: a tab
that reads as empty then has its details replaced by none, and a tab that is
gone has its stored details removed.

On a letter workbook (the `ODC` tab, refreshed by **Import all sheets** and
by the ASPA score import) a kept tab is reported the same way -- a
`[!] NOT SYNCED animal details 'ODC'` line under that cohort's
`animal_records` line, `N NOT SYNCED (...)` in the closing line, and a failed
run (`mousedb-import-aspa-scores` exits with 1). Those workbooks are frozen,
so there is no "on purpose" here: check that the workbook opens and that its
`ODC` tab still has its `Animal ID` header row; the next import then syncs it.

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
  cohort, when its last sheet import that SAVED the cohort's scores finished
  (an import whose scores were saved counts here even when a later step of
  it failed -- copying the animal details, for example -- because the scores
  are in; each line of the import history says so as `"scores_saved": true`,
  and the cohort still shows `FAIL` until that step works -- while an import
  of a file read on a guess, with several files matching the cohort and none
  pinned, does NOT count, see "When several files match a cohort"); which cohorts the
  sheets folder has a sheet for; and when the last import of EVERY sheet
  finished. The pipeline uses these to tell "the sheet has not been imported
  since this video was ready" (the video waits) from "it has been, and has no
  scores for this session" (the session was never scored by hand: there is
  nothing to compare, so the video is filed without the comparison). A cohort
  with no sheet at all is settled by the next import of every sheet. The list
  of cohorts with a sheet only ever GROWS: once a cohort's sheet has been seen
  in the sheets folder, the cohort stays on it, so a sheet that is missing
  from the folder for a moment (the sync program updating it, a rename) can
  never make a not-yet-imported cohort look like one with no sheet. If a
  never-imported cohort's sheet is really gone, its videos keep waiting --
  and are listed as waiting -- until a person puts the sheet back or sees to
  them. An import of a folder you name by hand (`mousedb import --all
  --directory <folder>`) does not touch this list or the "import of every
  sheet" time: only the configured sheets folder speaks for the sheets. It also
  carries `scores_sha256`, a fingerprint of the exact `.csv` it was written
  with: the two files are replaced one after the other, so a program reading
  them at that instant can catch a new `.csv` beside the old `.json` (or the
  reverse). The fingerprint is there so that the reading program CAN check
  that the two belong together and read both again when they do not. mousedb
  only writes it: whether a mismatched pair is actually noticed depends on the
  version of the reaching pipeline you run. A pipeline version that does not
  look at `scores_sha256` does not detect a mismatched pair (it simply ignores
  the extra entry); check that tool's own documentation for whether it does.

The scores are read from the database after the import has saved. If some
other program was stopped part-way through saving to the database (a job
ended at its time limit, a window closed mid-save), it leaves its unfinished
save beside the database. This read gets past it: it undoes the unfinished
save the way the next program to save would, and changes nothing else. Before
this, the line read `FAIL hand scores ... attempt to write a readonly database`
and the file stayed as it was until the next night. The nightly bench
disagreement scan reads the database the same way.

If a program (the pipeline, or a spreadsheet program you opened the file in)
has one of the two files open at the moment it is replaced, Windows refuses
the replacement; the import tries again for up to ten seconds before it
reports the write as failed. Close the file if the `FAIL hand scores` line
below keeps appearing.

Only one import writes the two files at a time. If two imports finish
together (for example a Cohort Setup button import while the nightly import
of every sheet is running), the second waits until the first has written
both files, then starts from what the first left -- so neither drops a cohort
the other has just added to the list of cohorts with a sheet. While a write
is under way a small file named `.hand_pellet_scores_write.task.lock` sits
beside the two files; it is removed when the write ends. Leave it alone: if
an import was stopped part-way and left it behind, the next write sees that
the program holding it has gone and takes it over by itself.

The list of cohorts with a sheet is carried over from the `.json` already
there, so a `.json` that cannot be read is never taken as an empty one (that
would drop cohorts from the list). A read refused for a moment is retried the
same way; if the `.json` is damaged (not readable as JSON), neither file is
rewritten and the line says `FAIL hand scores ... is damaged`. What you do:
restore `hand_pellet_scores.json` from a backup copy and import again. Only if
there is no backup, delete it: the next import starts the list again from the
sheets it finds then, so first check that every cohort's sheet is in the
folder.

Every import that writes (the scheduled job, the Tracking Sheets tab,
**Update the database now**, `mousedb-sheets import`, `mousedb import
--all` / `--file`, and Cohort Setup's **Import** and **Replace**) rewrites
the file when it finishes, and says so in its result: `OK   hand scores for the reaching
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
- **Beside the sheet**: after every import that writes (any route, Cohort
  Setup's buttons included), each cohort with anything listed gets
  `!! <cohort> - FIX THESE.xlsx` in the configured sheets folder, next to its
  workbook, with one line per thing to fix. Every cohort of that folder is
  brought up to date, whichever cohort was imported. They are written only
  into the configured sheets folder -- never beside a workbook you imported
  from somewhere else -- and not at all on a computer with no sheets folder
  configured. It is rewritten after
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
edited one and says so. The import does not stop, but until you choose it
only ADDS for that cohort: new rows from the newest file are added, and
empty values are filled in (a weight's % of body weight; and, when the
cohort has no planning note yet, the planning tab's text is added to the end
of the cohort's notes as a `[PLANNING] ...` entry -- the note text already
there is kept, and a cohort that already has a planning note keeps it). Apart
from those additions, nothing already stored is changed, moved or removed to
match that file, in any table (otherwise a draft or an old copy could take
rows out). That covers
more than "The sheet wins": these steps wait for the pin too, and each one
it held back is listed as a warning starting `HELD BACK until the cohort's
sheet is pinned:`

- moving scores already STORED under a date typed a year off or on a
  weekend (the rows read are still re-dated as usual, tray by tray; only a
  tray that an earlier import still has stored under the wrong date, and not
  under the right one, is read as typed, so it matches those scores. A score
  a person typed in under the wrong date does not count. Each tray is stored
  once);
- removing an earlier import of a virus batch the file shows as copied from
  another cohort's workbook;
- replacing the placeholder note of an animal or the placeholder start date
  of a cohort first created from a video;
- recording an animal as dead from a `Survived` cell.

The phase labels are worked out again as after any import that adds rows.
The animal details are left as they were. The cohort's line says why:

```
       [!] animal details left as they were: 2 files match CNT_NN's sheet name (...) and none is pinned, ...
       [!] NOT SYNCED any table: 2 files match CNT_NN's sheet name (...) ... Say which file is the sheet with 'mousedb-sheets pin <cohort> "<file name>"' ...
```

The cohort counts as NOT SYNCED, so the scheduled job's run shows as failed
every night until a file is pinned -- on the machine that runs the scheduled
import (pins are kept per machine login, see below). The same holds when a
pin is broken (see "A broken pin"). The closing line names such a cohort
apart: `N NOT SYNCED with the sheet -- several files match, pin one with
'mousedb-sheets pin <cohort> <file>' (CNT_NN)`.

**What this means for the reaching pipeline.** The scores the newest file
adds are in the database, so they are in the hand-scores file and a video
whose session they cover is compared with them as usual. But the import does
NOT count as the cohort's last scores import (its history line says
`"scores_saved": false`), because the file was a guess: a draft or an old copy
may lack a session, and the pipeline files a video whose session has no hand
scores without the comparison once the cohort's scores count as imported --
for good. So a video whose session is not in that file keeps waiting, listed
as waiting, until you pin the sheet and the next import has run.

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
were pinned (and, while it has more than one file, only ADDS -- see "When
several files match a cohort" above); a cohort with no other file has no
sheet at all and is not imported. This is reported, not hidden:

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
A file the folder route would never read -- a copy inside a snapshot or
archive folder, a "Copy of ...", an export written from the sheet -- is
refused at once with a `REFUSED: ...` box saying why and what to do instead,
before any preview, for Import and Replace alike (the same rule as
"Importing one named file" above). WHY Replace too: it deletes what earlier
imports wrote and imports again from the file you picked, so an old copy
would put old values back over good ones.
Otherwise a preview comes first and writes nothing: what would be imported, the first
warnings, and -- for a cohort already in the database -- what a Replace would
remove and keep. If the preview shows ERRORS, only *Close* is offered.
Otherwise choose:

- **Import (sheet wins)** -- adds the rows the database does not have yet,
  corrects values the sheet now gives differently, and removes rows earlier
  imports of this sheet wrote that the sheet no longer has (see "The sheet
  wins"). Rows typed in by hand are never touched. The preview says what it
  would change or remove (`If you choose Import: ...`).
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
who entered it. Animals, the cohort (apart from its start date, below), video
data and tissue data are not touched.

The removal step does not delete the animal details either. But once the
Replace is saved, they are copied again from the sheet, one tab at a time,
like after every import (see "Every way to start an import" above, the
paragraph on Replace). That copy takes a tab that is there
but now holds no animal as empty, so its stored details are removed. A tab
that is missing or renamed is kept as it was and reported
(`NOT SYNCED animal details '<tab>'`), and the Replace is not called complete.

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
| `0_Injection_Calculations: batch '...' not imported: dated ..., before this cohort began` or `... already recorded for <cohort>` | The block was copied from an earlier cohort's workbook, so it is not this cohort's prep. Clear the block in the sheet, or give it this cohort's own date if the prep really was made for it. If an earlier import had recorded it, that record has been removed; one entered by a person is never removed. |
| `HELD BACK until the cohort's sheet is pinned: ...` | Several files match the cohort and none is pinned, so the import changed nothing already stored (see "When several files match a cohort"). Pin the cohort's sheet and import again; that import does what the line names. |
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
| `<workbook> is open in another program: read its last saved version` | Nothing to do. This is not a failure: you may leave a workbook open in Excel, and the import reads what was last saved. Changes you have not saved are not in the file; save them and the next import picks them up. |
| `FAIL ... Could not read the workbook: it is open in <program> (process N) on this computer ...` | Rare. Excel does not cause this; it means the program named is holding the file so that nothing at all can read it. Close that program. Nothing from that workbook was imported this time; the next import (the nightly one, or **Import selected**) reads it. |
| `FAIL ... Failed to open Excel file: [Errno 13] Permission denied ...` | The same, when Windows could not say which program. Also check that your account can read the file. |
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
| `NOT SYNCED <table>: ...` | The sheet read as broken, so nothing in that table was removed or changed. Check the tab it names; if the sheet is right, import that cohort with `--allow-large-removal` or use Replace (see "The sheet wins"). |
| `<table>: <animal> <date> ... was typed in by hand (by <name>) and differs from the sheet ...` / `3b_Manual_Tray: <animal> <date> <tray> has scores typed in by hand ...` | Nothing is wrong with the import. Decide which is right: correct the sheet, or correct the hand-typed row in the entry tab it came from. |
| `0a_Metadata: <animal>'s <date of birth / date of death / sex> is <value> in the database but <value> in the sheet ...` | Decide which is right. If the sheet is wrong, correct it there; if the database is wrong, correct the animal in the GUI. The import never overwrites the database's value itself. |
| `3b_Manual_Tray: <animal> <date> <tray> pellet N is on two rows with different scores ...` | The same tray is typed twice. Delete the extra row in the sheet. |
| `changed from the sheet -- ...` (under a cohort's `OK` line) | Nothing: it lists what the import corrected or removed because the sheet changed. Check it matches what you meant to change. |
| `phases re-derived for <cohort>: ...` | Nothing: the cohort's session dates changed, so its phases were worked out again. |
| `FAIL ... the sheet's rows were saved, but re-deriving <cohort>'s phases failed ...` | The rows landed, but phase labels may be stale. The line ends the same way whether the import ran on its schedule or from the window. If it ends `the next reach import re-labels it`, nothing more is needed: the cohort is on the reach import's list and the next nightly import re-derives it. Otherwise run `mousedb backfill-phases` (MouseDB environment active); if it fails again, the message says why. |
| A box **Saved -- but test phases not updated** in the MouseDB window | Your change is saved, but working out that cohort's test phases again afterwards failed (most often: another job had the database busy). The box says whether the next nightly reach import will re-derive them by itself (the usual case) or whether `mousedb backfill-phases` has to be run. Until then that cohort's labels, and the exports, may be from before your change. |
| `Replace kept N ... rows for <animal> ...` | If the sheet's value is the right one, correct the kept row by hand in the entry tab it came from. |
| `Replace removed N ... rows ..., and the sheet gave back M ...` | Nothing, if you removed rows from the sheet; otherwise read the other warnings for why rows were skipped. |
| `Replace of <cohort> undone -- nothing in the database was changed ...` | Fix the error listed under it and Replace again. |
| `[!] import history not written: ...` (in the box after a Replace) | The Replace is saved, but its line in the import history is missing, so the reaching pipeline's videos for that cohort keep waiting for its scores. Make sure the import history folder (`<mousedb_root>/logs`) can be reached and written, then import the cohort again (any route); that import writes the line. |
| `[!] import history not written: ...` (in the **Replace undone** box) | Nothing was changed by the Replace, and its failed attempt is not in the import history either. Fix the error listed above it, make sure the import history folder (`<mousedb_root>/logs`) can be reached and written, and Replace again. |

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

**Virus batches are emptied when a template is made.** A batch on `0_Injection_Calculations` records one prep for one cohort, so the template keeps the block's layout and formulas but not its title date, viruses or amounts. Formula cells in an empty block can show an error until the block is filled in; that is expected.

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
- **A sheet is open in Excel, on this computer or another**: import still
  works and reads what was last saved. Leave it open. Unsaved changes arrive
  with the first import after you save.
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
mousedb-sheets import --cohort <cohort> --allow-large-removal   # after checking: see "The sheet wins"
mousedb-sheets pin CNT_NN "Connectome_NN_Animal_Tracking.xlsx"
mousedb-sheets unpin CNT_NN
mousedb-sheets set-dir "<folder>"
mousedb cohort-sheets                             # the folder, files not read, broken pins
```
