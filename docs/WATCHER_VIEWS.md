# The watcher views: `mousedb video-status` and `mousedb archive-status`

This page is for the person reading the numbers. No codebase knowledge assumed.

**Looking for where your videos are?** Use the **9. Where Is My Data** tab of
the MouseDB window. It reads the pipeline's folders, so it shows every video
wherever it is (see [Where is my data?](WHERE_IS_MY_DATA.md)). The views on
this page show something narrower: one computer's watcher records.

MouseDB has three places that show what a MouseReach **watcher** has recorded:

| where | how to open it |
|---|---|
| `mousedb video-status` | a terminal with the MouseDB environment active (add `--by-animal`, `--cohort <id>`, `--show-errors` or `--json`) |
| `mousedb archive-status` | the same terminal (add `--cohort <id>`, `--verbose` or `--json`) |

The web dashboard (`/dashboard/` of the MouseDB web server, if your lab runs
it: start it with `mousedb-web` in a terminal with the MouseDB environment
active, then open `http://<that computer>:8000/dashboard/`. By default it
listens on every network address of that computer, port 8000 --
`MOUSEDB_WEB_HOST` / `MOUSEDB_WEB_PORT`, or `--host` / `--port`, change that
-- so other computers on the network can open it; it has no login) shows the
same records, with fewer counts: Total, Processed %, Failed,
Archived, In Review, Quarantined and Not on this computer. Its counts do not
add up to the total (Archived is one part of Processed); use
`mousedb video-status` for the full split.

**The MouseDB window has no Video Pipeline or Archive Status tab.** It used
to have both (tabs 8 and 9). They laid one computer's watcher records out as
if they were the pipeline's state, so a person reading them saw videos that
another computer had handled, or that waited in a review queue, as not
started or missing. They were removed; the tabs after them were renumbered
(Tracking Sheets is now 8, Where Is My Data 9, Study Facts 10). The two
commands below still show the same records, each with the line saying whose
records they are.

## What these views are -- and are not

Each MouseReach watcher keeps a small database of what **its own computer's**
watcher has done with each video. These views read that database, on the
computer you are sitting at. So:

- **They show this computer's watcher records only.** Work done on other
  computers is not in them. A video another computer posed, analyzed or filed
  may show here as not started, or not at all.
- **The whole pipeline's state is on the Where Is My Data tab.** That tab's
  pipeline counts come from the pipeline census, which reads the pipeline's
  folders -- where every video really is. When the two disagree, the census
  is right. See [Where is my data?](WHERE_IS_MY_DATA.md).

Every one of the views says this above its numbers, in a line like:

```
Records of this computer's watcher only: <file> (last written 2099-01-31 14:05).
Work done by other watchers is not in these records. The whole pipeline's
state, read from its folders, is on the Where Is My Data tab.
```

That first line appears only when the file was found through this computer's
own MouseReach settings (steps 2 and 3 under "Which file is read" below), the
only way MouseDB knows it is the file this computer's watcher writes. When the
file was found another way, the line says how, and that it may be another
watcher's:

```
Records of one watcher database only, named by the MOUSEDB_WATCHER_DB setting:
<file> (last written ...). It may not be this computer's watcher.
```

(or "found in mousedb's MouseReach pipeline folder, because this computer's
MouseReach settings name none" for step 4). Such a file may be another
computer's watcher database, or one on a shared drive.

**"last written"** is when the watcher last recorded anything. If that time is
hours old while the watcher should be running, the watcher on this computer is
stopped or stuck -- the counts below are from that time.

In `--json` output the same facts are the keys `watcher_db` (the file),
`watcher_db_last_written` and `scope`: `this_computer_watcher_records_only`
when the file was found through this computer's MouseReach settings,
`one_watcher_database_records_only` when it was found another way.

## What each count means

The watcher gives every video one state. Each view sorts the states into a
few counts. In `mousedb video-status` and `mousedb archive-status`, every
video is in exactly one count, so the counts add up to the total -- in the
summary and on every row of the commands' per-animal tables. A video held
for a person (In Review, Quarantined) is always in a
count of its own, never hidden in another; a video in a state MouseDB does
not know is in the Other count (see below).

(The commands' by-cohort lines of `mousedb archive-status` are a progress bar
plus a list of the non-zero counts, not a table; the web dashboard's counts
do not add up, see above.)

**`mousedb video-status`**. The summary, and the by-cohort and `--by-animal`
tables, all have these counts (`Not Here` is short for Not on this
computer). The tables call the first count `Done`; the summary line reads
`Completed: <percent>% (<N> done, <N> in progress, <N> waiting for review,
<N> not on this computer, <N> failed, <N> quarantined)`:

| count | watcher states | meaning |
|---|---|---|
| Done | processed, archiving, archived, crystallized | analyzed (and filed, or being filed). Crystallized = filed and locked for a publication. On a computer that only poses videos and hands them on (a graphics-card computer set up not to analyze), Done means **posed and handed on**, not analyzed: its watcher marks a video archived as soon as it has passed the pose on. |
| In Progress | discovered, validated, dlc_queued, dlc_running, dlc_complete, processing, outdated | work the pipeline still has to do on this computer. Outdated = filed with older tool versions; it will be run again. |
| In Review | triage, deep_review | waiting for a **person** in a review queue (triage, or deep review). Nothing happens to it until someone reviews it. |
| Not on this computer | unresolvable | this computer has no file for the video -- another computer holds it. Not a failure. |
| Failed | failed | a step failed. |
| Quarantined | quarantined | held until a person deals with the recording (for example, its name could not be read and it must be renamed). Nothing happens to it until then. |
| Other | any state MouseDB does not know | see below. |

**`mousedb archive-status`**. The summary and the `--verbose` / `--cohort`
per-animal table have these counts. The table's headings are short:
`Crystal` = Crystallized, `Pipeline` = In Pipeline, `Review` = In Review,
`NotHere` = Not on this computer, `Quarant` = Quarantined. The summary line
reads `Version compliance: <archived>/<total> current`:

| count | watcher states |
|---|---|
| Archived | archived -- on a computer that only poses and hands videos on, this means posed and handed on, not analyzed |
| Outdated | outdated |
| Crystallized | crystallized |
| In Pipeline | discovered, validated, dlc_queued, dlc_running, dlc_complete, processing, processed, archiving |
| In Review | triage, deep_review |
| Not on this computer | unresolvable |
| Quarantined | quarantined -- held until a person deals with the recording; not a failure |
| Failed | failed |
| Other | any state MouseDB does not know (listed by name, see below) |

**A state MouseDB does not know** (a newer MouseReach may add one) is never
folded into one of the counts above. The views list it by its own name, for
example `States mousedb does not know: some_state=2` (`mousedb video-status`
adds `(counted in none of the above)` before the colon). If you see that line,
MouseDB needs updating to describe the new state.

(Before this was fixed, In Review and Not on this computer did not exist:
those videos were counted as In Progress / In Pipeline, and crystallized
videos as In Progress. `mousedb archive-status` also showed quarantined
videos as Failed, and had no Other count, so a row with an unknown state did
not add up to its Total.)

In `--json` output: `summary`, each cohort and each animal carry
`in_review`, `not_on_this_node`, `quarantined` and `other` (an object mapping
each unknown state to its count, for example `{"some_state": 2}`).
`archive-status` also carries `failed` (failed only, quarantined is apart) in
all three. `video-status` also carries `in_progress`; its summary carries
`failed`, each animal `failed_videos`, each cohort `failed`.

## Which file is read

MouseDB reads the file this computer's watcher **writes**, found in this order:

1. the `MOUSEDB_WATCHER_DB` environment variable, if set;
2. `db_path` in the `watcher` section of `~/.mousereach/config.json`, if set --
   a computer is set up this way when its watcher must keep its database on a
   local disk. When this is set but the file does not exist, the views say so
   and show nothing: an older `watcher.db` that may still sit in the
   processing folder is NOT shown instead, because the watcher no longer
   writes it and its numbers are out of date;
3. `watcher.db` in the `processing_root` folder named in the same file;
4. `watcher.db` in the MouseReach pipeline folder of mousedb's own settings
   (`mousedb config --show`).

(Before this was fixed, step 2 did not exist, so on a computer set up as in
step 2 these views showed the old file's numbers.)

The views only read. Each refresh opens the file read-only, runs a few
counting queries over its videos table, and closes it again; nothing is kept
open between refreshes, and nothing is ever written to the watcher's
database. The web dashboard repeats this every 30 seconds while it is open.

**The web dashboard's scope line is refreshed too.** Every 30 seconds the
page re-fetches both the counts and the line above them, so the file named
and its "last written" time stay current without reloading the page.
