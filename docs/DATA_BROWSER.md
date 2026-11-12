# Browse the data

This page is for anyone who wants to look at what the database holds. No code,
no SQL, nothing to install on your side.

## Opening it

Any of these:

- Double-click **MouseDB - Browse the data** on the desktop.
- Or open the Windows **Start** menu, find the **MouseDB** folder, and click
  **MouseDB - Browse the data**.
- Or, inside MouseDB (double-click **MouseDB** on the desktop), open the
  **Where Is My Data** tab and press **Browse the data** (the purple button).

The desktop and Start-menu shortcuts are there for everyone who logs in to a
computer that has been set up for it (see "For whoever looks after mousedb"
below). Not on your desktop? Open the browser once from inside MouseDB, then
press **Put a shortcut on my desktop** in its small window.

What happens next:

1. A small window called **MouseDB data browser** appears.
2. The first time each day it says *Making your private copy of the
   snapshot...* and lists the tables as it copies them. This takes under a
   minute. Later the same day it is instant.
3. Your web browser opens on the data browser's front page.

**Keep the small window open while you browse.** You can minimize it. When you
are finished, press **Close the data browser** (or close the small window);
that stops it. Closing the web browser tab alone leaves it running, which is
harmless, but it keeps running until you close the small window or log off.

Pressing the button or the shortcut again while it is open does not start a
second one: it just opens the same page again.

## What you see

The front page lists the places to start:

| table | one row per | for questions like |
|---|---|---|
| **animals** | animal | Which animals are in cohort X? When was each injured? How much data do we hold for each? |
| **sessions** | animal, testing day and tray type | What did this animal do day by day? Hand scores and the pipeline's pellet results side by side, with the day's body weight |
| **reaches** | reach the pipeline detected | The reaches of one animal or one day, with the most-used measurements |
| **brain_region_counts** | brain and atlas region | Cell counts per region, per brain |
| **brain_group_counts** | brain and functional group | Cell counts in the 25 groups of Wang et al. 2022 |

Below them are three **ready-made questions**: a small form where you type an
animal id (or a cohort id, or an animal and a date) and press **Run**:

- *One animal, day by day*
- *One animal's reaches on one day*
- *One cohort, totalled per day*

Every other table the database holds is listed underneath -- `reach_data`
(every column held for each reach), `pellet_scores` (every hand score),
`video_pellets`, `weights`, `subjects`, `cohorts`, `surgeries`,
`animal_records` (every animal-level value from the tracking sheets, as
written), the brain tables -- and `about_this_copy`, which says when each table
was taken.

## Finding what you want

On any table:

- **Sort:** click a column heading, then *Sort ascending* / *Sort descending*.
- **Keep only some rows, the quick way:** the boxes at the top (*facets*) list
  the values of a few useful columns -- cohort, tray type, phase -- with how
  many rows have each. Click a value to keep only those rows; click values in
  other boxes to narrow further. To undo a choice, click it again. Any other
  column can get a box too: click its heading and choose *Facet by this*.
- **Keep only some rows, any rule:** in the *Filter* row choose a column, a
  test (`=`, `contains`, `>`, `<`, ...), type a value, and press **Apply**.
  A new empty filter row then appears for a second rule. Example: `subject_id` `=` `PROJ_05_03`, then
  `session_date` `>` `2026-07-01`.
- **What does a column mean?** Click **What each column means** above the table
  for every column's description, or click a column heading: its description is
  at the top of the menu. These are the same descriptions the data dictionaries
  beside the exported CSVs give.
- **Long tables:** 100 rows show per page; *Next page* is at the bottom. The
  count at the top says how many rows match your filters.

## Saving what you see

Below the table is a line **This data as json, CSV (advanced)**. Click **CSV**
to save the rows exactly as filtered and sorted.

- That saves the rows on the current page. To save **every** matching row,
  click **advanced** instead: under **Advanced export**, tick **stream all
  rows** and press **Export CSV**. The file opens in Excel.
- A CSV of a big selection (all reaches of a project) can be hundreds of
  megabytes. For whole-project tables, use the ready-made exports in the
  current exports folder instead (Where Is My Data -> **Open exports folder**;
  see [Where is my data?](WHERE_IS_MY_DATA.md)). Those are what you send to
  someone else.

## How fresh is it? Why is it a copy?

The data browser shows a **copy of the database's nightly snapshot**, made for
you alone. The line at the foot of every page, and the small window, say when
that snapshot was written and how old it was when you opened the browser.

- Anything imported into the database today appears **after the next nightly
  snapshot**. Then close the data browser and open it again; it makes a fresh
  copy. If a new snapshot is written while it is open, the small window tells
  you.
- Videos still waiting for review in MouseReach are not in the database at all,
  so they are not here either. The **Where Is My Data** tab lists them.

Why a copy and not the database itself: people leave viewers open for hours or
days. A viewer holding the live database open makes the importers wait, and
that once stopped the nightly imports for days ("database is locked"). A viewer
holding the snapshot files open would stop the nightly job from replacing them.
So the data browser opens neither: it reads the snapshot for a few seconds,
once per new snapshot, makes its own copy in your own local folder, and shows
only that. Leaving it open overnight affects nothing.

It is **read-only**. Nothing you click or type in it can change the database,
the snapshot, or the copy. (The page has a box for SQL queries, for people who
want it; it refuses anything that is not a question.)

## Not here, on purpose

- The per-reach **extended features** (about 165 extra measurements per reach)
  are stored in the database as one block of text per reach, which cannot be
  sorted or filtered. They are in the exported reach files, one column each.
- The data browser has no "download the whole database" link. The files to keep
  or send are the exports.

## When something goes wrong

Every problem is reported in a message box saying what to do. The common ones:

| message | what to do |
|---|---|
| *The data browser is not installed in this Python environment* | Whoever looks after mousedb installs it once (see INSTALL.md, "Data browser"). |
| *There is no snapshot to browse yet* | The snapshot job has not run on this computer yet. After it runs (nightly, or **Update the database now** on the Where Is My Data tab), try again. |
| *The snapshot is being rewritten right now ...* | The nightly job is mid-write. The window waits for it (up to 15 minutes) and carries on. |
| *Open the data browser from MouseDB* (in the web browser) | You opened an old link or bookmark. Use the button or the desktop shortcut. |
| The web browser did not open | Press **Open in my web browser** in the small window. |

## For whoever looks after mousedb

- **Install** (once per Python environment): `pip install -e ".[browse]"` from
  the mousedb checkout (or `pip install "mousedb[browse]"`). This adds
  Datasette and its dependencies; nothing system-wide.
- **Shortcuts for everyone on a computer:** run `mousedb-make-shortcuts
  --all-users` once, from an administrator PowerShell with the MouseDB
  environment active. It puts **MouseDB** and **MouseDB - Browse the data** on
  the Public Desktop and in a **MouseDB** folder of the all-users Start menu,
  so every login sees both. Without `--all-users` it does the same for you
  only (no administrator needed). Also set the locations once for the whole
  computer (`mousedb config --machine --set ...`, see INSTALL.md,
  "Configuration"): a shortcut only helps a person if mousedb knows, for them,
  where the snapshot is. Full steps: INSTALL.md, "Shortcuts".
- **Commands:** `mousedb-data-browser` opens it from a terminal;
  `--build-only` makes (or reuses) your copy and prints where it is and when
  each table was taken; `--make-shortcut` puts your own desktop shortcut in place;
  `--no-browser` opens the window without starting the web browser. (Not to be
  confused with the older `mousedb browse`, a terminal table printer that reads
  the live database.)
- **Where the copies live:** `%LOCALAPPDATA%\mousedb\data_browser\<fingerprint>\mousedb.sqlite`
  per person (`MOUSEDB_BROWSER_CACHE` overrides the folder). About half a
  gigabyte per person; the previous copy is deleted when a new one is made.
  A window-less run's messages go to `data_browser.log` in the same folder.
  The copy may not be put inside mousedb's own folder or the snapshot folder;
  the browser refuses.
- **What it reads:** only `<snapshot_dir>/<table>.parquet` for the tables the
  snapshot job writes (`mousedb.exporters.refresh_snapshot.TABLES`). It never
  opens the live database; it refuses to serve any file but its own copy.
- **The desktop shortcut** runs `pythonw.exe -m mousedb.data_browser` of the
  environment it was made from, not an `.exe` in `Scripts\`, so an open browser
  never blocks a `pip install` of mousedb, and the shortcut keeps working after
  updates.
- **Network:** the page is served on `127.0.0.1` only -- no other computer can
  reach it. On a computer several people are logged in to at once, each launch
  makes a random key; a request without it gets the "Open the data browser from
  MouseDB" page, so nobody lands on another person's copy.
- **The snapshot job and readers:** on Windows a file cannot be replaced while
  any program has it open. The snapshot job therefore waits up to two minutes
  for a reader to let go before swapping a table in, and the data browser reads
  each file for only a second or two.

## Why this tool

The viewer is [Datasette](https://datasette.io) (Apache 2.0, actively
maintained; mousedb requires 0.65.5 or later, below 1.0). It was chosen over the
alternatives for this job -- people who do not write SQL, on a shared Windows
computer, looking at a database that other programs must keep writing:

- **Datasette** shows each table as a web page with click-to-sort, value
  counts you click to filter (facets), filter rows built from drop-downs, a CSV
  link for exactly what is on screen, a description for every table and column,
  and named questions with a form. It opens a file immutable (no writes, no
  locks), installs with `pip` into the existing environment, and runs inside the
  launcher's own process, so nothing outlives the window.
- **DB Browser for SQLite** (a desktop program) has good per-column filter boxes
  and a read-only mode, but no way to show column descriptions, an interface
  built around editing (most of its buttons are grayed out in read-only mode
  and confuse people), no named questions with forms, and a system-wide
  installer.
- **sqlite-web** is a small web viewer with a read-only switch, but it has no
  facets and no column descriptions, and its filtering is close to writing SQL.
- **SQLiteStudio, DBeaver, Beekeeper Studio** are database administration
  tools: powerful, aimed at people who write SQL, and each a separate
  installation. Beekeeper's free edition is licensed only to small
  organizations.

Whichever viewer, it had to open a copy rather than the database: none of them
can avoid holding the file open, and an open file is the problem (see "Why a
copy").
