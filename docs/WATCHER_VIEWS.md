# The watcher views: Video Pipeline and Archive Status

This page is for the person reading the numbers. No codebase knowledge assumed.

MouseDB has four places that show what a MouseReach **watcher** has recorded:

| where | how to open it |
|---|---|
| **8. Video Pipeline** tab | MouseDB window, tab 8 |
| **9. Archive Status** tab | MouseDB window, tab 9 |
| `mousedb video-status` | a terminal with the MouseDB environment active (add `--by-animal`, `--cohort <id>`, `--show-errors` or `--json`) |
| `mousedb archive-status` | the same terminal (add `--cohort <id>`, `--verbose` or `--json`) |

The web dashboard (`/dashboard/` of the MouseDB web server, if your lab runs
it) shows the same records, with fewer counts: Total, Processed %, Failed,
Archived, In Review, Quarantined and Not on this computer. Its counts do not
add up to the total (Archived is one part of Processed); use tab 8 or
`mousedb video-status` for the full split.

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

Every one of the four views says this above its numbers, in a line like:

```
Records of this computer's watcher only: <file> (last written 2099-01-31 14:05).
Work done on other computers is not in these records. The whole pipeline's
state, read from its folders, is on the Where Is My Data tab.
```

**"last written"** is when the watcher last recorded anything. If that time is
hours old while the watcher should be running, the watcher on this computer is
stopped or stuck -- the counts below are from that time.

In `--json` output the same facts are the keys `watcher_db` (the file),
`watcher_db_last_written` and `scope` (always
`this_computer_watcher_records_only`).

## What each count means

The watcher gives every video one state. Each view sorts the states into a
few counts. In tab 8, tab 9, `mousedb video-status` and `mousedb
archive-status`, every video is in exactly one count, so the counts add up to
the total -- on the summary cards, and on every row of the By Cohort and By
Animal tables. A video held for a person (In Review, Quarantined) is always in
a count of its own, never hidden in another.

**Video Pipeline** (tab 8, `mousedb video-status`). The summary cards, the By
Cohort and By Animal tables of tab 8, and the by-cohort and `--by-animal`
tables of the command all have these columns (`Not Here` is short for Not on
this computer):

| count | watcher states | meaning |
|---|---|---|
| Completed | processed, archiving, archived, crystallized | analyzed (and filed, or being filed). Crystallized = filed and locked for a publication. |
| In Progress | discovered, validated, dlc_queued, dlc_running, dlc_complete, processing, outdated | work the pipeline still has to do on this computer. Outdated = filed with older tool versions; it will be run again. |
| In Review | triage, deep_review | waiting for a **person** in a review queue (triage, or deep review). Nothing happens to it until someone reviews it. |
| Not on this computer | unresolvable | this computer has no file for the video -- another computer holds it. Not a failure. |
| Failed | failed | a step failed. |
| Quarantined | quarantined | held until a person deals with the recording (for example, its name could not be read and it must be renamed). Nothing happens to it until then. |
| Other | any state MouseDB does not know | see below. |

**Archive Status** (tab 9, `mousedb archive-status`):

| count | watcher states |
|---|---|
| Current (Archived) | archived |
| Outdated | outdated |
| Crystallized | crystallized |
| In Pipeline | discovered, validated, dlc_queued, dlc_running, dlc_complete, processing, processed, archiving |
| In Review | triage, deep_review |
| Not on this computer | unresolvable |
| Failed | failed, quarantined |

**A state MouseDB does not know** (a newer MouseReach may add one) is never
folded into one of the counts above. The views list it by its own name, for
example `States mousedb does not know: some_state = 2`. If you see that line,
MouseDB needs updating to describe the new state.

(Before this was fixed, In Review and Not on this computer did not exist:
those videos were counted as In Progress / In Pipeline, and crystallized
videos as In Progress.)

In `--json` output: `summary`, each cohort and each animal carry
`in_review`, `not_on_this_node` and `other` (an object mapping each unknown
state to its count, for example `{"some_state": 2}`); `video-status` also
carries `in_progress`, and `quarantined` in its summary, each cohort and each
animal (its summary also carries `failed`; each animal carries
`failed_videos`, each cohort `failed`).

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

The views only read. Each refresh runs a few short queries (each well under a
second on a database of the usual size); nothing is kept open between them,
and nothing is ever written to the watcher's database.
