# MouseReach Video Processing Progress

This page is for the person doing the work. No codebase knowledge assumed.

**Just want to look?** The **Browse the data** button on the MouseReach Video Processing Progress
tab opens every table the database holds in your web browser -- sort, filter,
save as CSV, no code. See [Browse the data](DATA_BROWSER.md). The files below
are what you keep, analyze and send.

## The short answer

`<mousedb_root>/exports/Behavior/All_Cohorts/` (run `mousedb config --show` to see the
folder on this machine) -- rewritten by the scheduled job with everything the
database currently holds, as CSV files you can open in Excel, R or Python,
each with a data dictionary beside it:

| file | one row per | definitions |
|---|---|---|
| `reach_data.csv` | reach the pipeline detected (kinematics, the pellet outcome of its segment, and where that outcome came from) | `reach_data_DATA_DICTIONARY.csv` |
| `manual_scores.csv` | pellet scored by hand from the tray (0 untouched / 1 displaced / 2 retrieved) with the session's phase -- on every tray, easy and flat included | `manual_scores_DATA_DICTIONARY.csv` |
| `ODC_days_<cohort>.csv` | animal per DAY (one date, all its trays; everywhere else a session is one tray), in the ODC-SCI `2_ODC_Animal_Tracking` shape (per-tray and daily counts and percentages, weight, injury) | `ODC_days_DATA_DICTIONARY.csv` |
| `ODC_reaches_<cohort>.csv` | reach -- **the complete record**: every measurement held for that reach, that animal's details (strain, sex, every surgery column of the tracking sheet), that session's details, the tray totals, AND how the result was produced (tool versions, which machine, who reviewed it) | `ODC_reaches_DATA_DICTIONARY.csv` |
| `reaches_<cohort>_summary.csv` | reach -- **the shareable table**: the same reaches and every measurement of them, with the process columns removed and everything named in plain words | `reaches_summary_DATA_DICTIONARY.csv` |
| `reaches_<cohort>_analysis.csv` | reach -- **the analysis table**: the same reaches in the shape a statistics package wants (numbers in numeric columns, `NA` for absent, the session's totals on every row). An entry with an identified problem is left out of it until its source is fixed | `reaches_analysis_DATA_DICTIONARY.csv` |
| `sessions_<cohort>_analysis.csv` | session = one tray (one video) of one animal, totaled from the analysis table. Every percentage (retrieved, displaced, contacted, missed; algorithm and hand scored) is out of `pellets_presented`, the tray's 20. Two columns MEASURE it: `pellets_in_video` (how many pellets the pipeline found in the video) and `pellets_hand_scored` (how many pellets of that tray have a hand score); both should be 20, and a session where either is not is treated as an error (see below). `pellets_reached_for` counts the pellets the animal reached for at least once and is never a denominator. **Every filed tray that is not left out has a row -- also a tray at which the animal never reached** (it has no rows in the per-reach files: no reaches means no reach rows). Such a row reads 0 reaches, 0 pellets reached for, and every other column filled exactly as on any tray: hand scores, pellets the pipeline cut and their outcomes (0 percent retrieved when all were untouched), attention (the mean over every segment of the video, since it is scored from the pose and not from reaches), session flag, day totals. Only the columns computed FROM reaches (latency to the first reach, gap between reaches, first-attempt success, and every per-session kinematics summary) read `NA` on such a row; each column's dictionary row says what it holds there. Since 2026-11-24 the file also carries the session measurements described under "Session measurements" below | `sessions_analysis_DATA_DICTIONARY.csv` |
| `analysis_EXCLUSIONS.csv` | entry left out of the analysis tables, and why | -- |
| `MANIFEST.json` | -- | when the files were written, from which snapshot (`snapshot_generation`), row counts, any problems, notes that need no action, and whether the per-reach files were kept from an earlier run because nothing they are built from changed (`odc_reaches_skipped`) |
| `README.txt` | -- | the same explanation as this table |

### How the folder is arranged

```
exports/Behavior/All_Cohorts/
    reach_data.csv, manual_scores.csv     every project at once
    *_DATA_DICTIONARY.csv                 one per kind of file, for every project and cohort
    analysis_EXCLUSIONS.csv, MANIFEST.json, README.txt
    GrantA/                               a grant's projects sit together (see below)
        PROJA/                            one folder per project
            ODC_reaches_PROJA_all_cohorts.csv   every PROJA cohort in one file, one per kind
            reaches_PROJA_all_cohorts_summary.csv, reaches_PROJA_all_cohorts_analysis.csv,
            sessions_PROJA_all_cohorts_analysis.csv, ODC_days_PROJA_all_cohorts.csv
            PROJA_05/                     one folder per cohort
                ODC_reaches_PROJA_05.csv, reaches_PROJA_05_summary.csv,
                reaches_PROJA_05_analysis.csv, sessions_PROJA_05_analysis.csv,
                ODC_days_PROJA_05.csv
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
  A path written into a script breaks the next time the folder is rearranged.
  In the MouseDB environment:

  ```python
  from mousedb.exporters.layout import locate
  path = locate("reaches_analysis", "PROJA_05")   # one cohort's file
  path = locate("reaches_analysis", "PROJA")      # the whole project, combined
  ```

  The kinds are `ODC_reaches`, `reaches_summary`, `reaches_analysis`,
  `sessions_analysis` and `ODC_days`. If the file has not been written yet,
  it stops with a message saying which file and where it looked.
- A file of an older layout that sits at the top of the folder is moved to `<mousedb_root>/_archived/exports_current/` (never deleted) the first time the current layout is written.
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

**A cohort that is still running has no phase labels yet.** A session's phase (training, baseline, post-injury test N, rehabilitation ...) is worked out from ALL of its cohort's dates, so while the cohort is still being tested a later date can change the label of a session that was already exported -- and a file you saved last week would carry the old label with nothing to say it changed. So every export leaves the phase EMPTY for every session of a running cohort: `test_phase` and `phase_group` in `reach_data.csv` and `manual_scores.csv`, `Test_Phase` in `ODC_days_<cohort>.csv`, `Test_Type` and `Test_Type_Grouped` in the complete record (written `Undetermined` there and in the summary's `test_phase` / `test_phase_group`, because those files carry no empty cells), and `test_type`, `test_type_grouped` and `experiment_stage` in the analysis files (`NA`). The first export after the cohort is over fills them in, with nothing for you to do. (The same columns are also empty, in the same way, for a session of any cohort whose phase has not been derived yet -- see `reach_imports_phases_pending.json` below; `mousedb backfill-phases` derives it.) "Running" is decided the same way the exports already decide whether to name an animal's cause of death: what the lab has written for that cohort -- `"Study_Status": "in_progress"` or `"completed"` for that exact cohort in the study-facts file -- and, where nothing is written, whether the cohort's last recorded session is less than 30 days old (a cohort with no dated session at all counts as running). To get a finished cohort's labels before the 30 days are up, run `mousedb study-facts --set PROJA_05 Study_Status completed` (with your cohort's id; in the MouseDB environment); to keep a paused cohort's labels empty, set it to `in_progress`; `mousedb study-facts --unset PROJA_05 Study_Status` goes back to deciding by date. The next nightly run, or **Refresh exports now**, uses it. The exports you run by hand with `mousedb export` (the tracking-sheet workbook, the ODC workbook, `--all-formats` and `--unified`) follow the same rule: they read the database instead of the snapshot, so they decide "running" from the database's own reach rows, leave `Test_Phase` (or `test_phase` in the unified parquet) empty for a running cohort, and print `Phase left empty (cohort still running): <cohorts>` so an empty column is not mistaken for missing data. `MANIFEST.json` lists the cohorts whose phase was left empty under `reach_data.csv` and `manual_scores.csv` (and under each running cohort's `ODC_days` file), as `phase_withheld_for_running_cohorts`; the same cohorts are left empty in every other file of that refresh.

**A tray presents 20 pellets; anything else is an error, and it is handled like one.** A video the pipeline cut into some other number of pellets has the wrong pellet number on its reaches, so that session is left out of the analysis files. A tray whose hand scores number other than 20 keeps its reaches but has its hand-score columns left empty. Both are listed in `analysis_EXCLUSIONS.csv` with the count that was found, and both come back on their own once the video is reprocessed or the sheet is corrected.

**A recording whose name the archive's own cuts contradict is left out whole.** Some archives kept their own single-animal cut of every seat beside each recording, so every animal is named twice, typed separately. MouseReach's name audit (`mousereach-aspa-name-audit`) traces each of those cuts to the recording it came from and writes the recordings whose seats the cuts name for an animal the recording's name does not list (`pipeline_records/archive_name_conflicts.json` in the MouseReach pipeline folder). Every video cut from such a recording is left out of the analysis files -- whose video each seat holds is in doubt -- and listed in `analysis_EXCLUSIONS.csv` with the cut names that disagree. Nothing is renamed: once a person has said which name is right and the recording has been brought in under it, rerunning the audit brings the videos back. With no audit record, `MANIFEST.json` says the check did not run.

**A recording whose name a person corrected counts once, under its corrected name.** When a typo in a multi-animal recording's name is fixed (two animals swapped, a missing digit), MouseReach's watcher databases still list the old name beside the new one. Read as two recordings, both would "produce" the animals they share, and those videos would be left out as a collision ("this video name was produced by 2 different recordings"). The correction itself says which name is right, so the exports read the correction records -- `<MouseReach pipeline>/Archive/manual_corrections/<folder>/record.json`, each listing `files` with an `original_name` and a `corrected_name` -- and leave every name listed as an `original_name` out of the recording list: the corrected name wins, the old name is history. Since 2026-11-20. Writing a record is all it takes: the next nightly run, or **Refresh exports now**, sees it (it is part of what decides whether the files are rebuilt) and the videos come back. A record that exists but cannot be read stops the analysis files like any unreadable input (below); the message names the file. A correction folder with no `record.json` is not read.

**A check that cannot read its input stops the analysis files; a check whose input does not exist says so.** The "left out" checks read four things outside the snapshot: the recording names in MouseReach's watcher databases, the name-correction records above, the name-audit record above, and the tracking-sheet folder. If one of them is simply not there -- no audit record yet, no sheet folder set -- that check does not run, and `MANIFEST.json` lists it under problems ("... not run"). With no central watcher database the recording checks still run, from the local watcher database only; `MANIFEST.json` lists that under problems ("recording checks (identity, collision) ran without the central watcher database ..."), because a recording known only to the central database cannot be checked. If one of them IS there but cannot be read tonight -- a watcher database that is damaged, missing its table of recordings, or still locked after being tried three times, which takes a little over two minutes per database (a watcher writing at that moment is simply waited for; a refresh reads the watcher databases twice, so one that stays locked can hold the refresh up for about four and a half minutes per database before it gives up), an audit record cut off half-written, a sheet folder that is set but on a drive that cannot be reached, a MouseReach pipeline folder that is set but cannot be reached (then every watcher database in it would look missing, and the recording checks would run with no list of recordings at all) -- no per-reach cohort file is rewritten -- not the analysis files (`reaches_*_analysis.csv`, `sessions_*_analysis.csv`), nor the complete record and summary written with them, nor `analysis_EXCLUSIONS.csv`: the previous ones stay, `MANIFEST.json` says `"complete": false` with the reason under `failures`, and the nightly job reports itself as failed. Writing them anyway would have put the entries that check exists to leave out in front of the people analysing them. What to do: the message names the file or folder. Reconnect the drive or fix the setting (`mousedb cohort-sheets --set <folder>` for the sheet folder, `mousedb config --set mousereach_pipeline_root <folder>` for the pipeline folder), or rerun `mousereach-aspa-name-audit` to rewrite a damaged record; the next nightly run, or **Refresh exports now**, then writes the files.

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
`contact_group` says `missed`.

`reach_result` never says where the verdict came from. A verdict is a verdict;
whether a human or an algorithm produced it is in the complete record.

### Four levels on every reach row, and what each column is called

Every row of a per-reach file (`reach_data.csv`, `ODC_reaches_<cohort>.csv`,
`reaches_<cohort>_summary.csv`, `reaches_<cohort>_analysis.csv`, and the same
kinds in a limited export) is **one reach**, and it repeats the three levels
above it so you never have to join files:

| level | what it is | example columns (analysis table) |
|---|---|---|
| reach | what THIS reach did | `reach_outcome`, `reach_number`, `reach_pellet_state` |
| segment | one pellet: the same on every reach aimed at it | `segment_number`, `segment_outcome`, `segment_reaches` |
| session | one tray = one video: its totals | `session_pellets_ai`, `session_retrieved_hand`, `session_untouched_ai` |
| day | one animal on one date, all its pillar trays | `day_reaches_ai`, `day_retrieved_ai`, `day_untouched_hand` |

**The naming rule.** A column that belongs to a level above the reach starts
with that level's word: `reach_`, `segment_`, `session_` or `day_`
(`Session_`/`Day_` in the complete record). "Session" means ONE TRAY (one
video) everywhere in these files. A column with no level word is either an
identity column (`subject_id`, `video_name`, `test_date`, `tray_type`,
`session_id`, `tray_id`: kept as they were, because limited exports, the
exclusion list and your own scripts find rows by them), an animal column, or a
provenance column of the complete record (tool versions, source file). **Every
measurement of the reach itself starts with `reach_` too**: its frames (`reach_start_frame`, `reach_end_frame`,
`reach_apex_frame`), every kinematic measure (`reach_swipe_length`,
`reach_speed_at_apex_mm_per_sec`, `reach_righthand_peak_speed_mm_per_frame`
...; in the complete record the extended block keeps its `ext_` after it:
`reach_ext_...`), tracking confidence and the contact frame. Each dictionary entry names the column's old name. The ODC-SCI animal fields (`SubjectID`, `SpeciesTyp`, `AgeVal`,
`BodyWgtMeasrVal`, `SexTyp`, `InjGroupAssignTyp`) keep ODC's exact spelling,
which ODC requires. `_ai` = counted by the pipeline from the video, `_hand` =
counted from the hand scores in the tracking sheet.

The one file whose row is a whole day is `ODC_days_<cohort>.csv` (one
animal, one date, Tray1..Tray4 across the row, `Daily_*` totals). Older exports named it
`ODC_sessions_<cohort>.csv`; the first export that writes the new name moves every file still under the old name (cohort
files, combined files, the dictionary) into the archive. **A saved limited
export that lists the kind `ODC_sessions` keeps working unchanged**: the old
kind name is read as `ODC_days` (an alias, `layout.KIND_ALIASES`), so nobody
has to edit a definition; its next slice holds `ODC_days_<name>.csv` and the
previous slice (with `ODC_sessions_<name>.csv`) goes to the archive as usual.

**What a reach did.** On every reach, not only the credited one:

- the reach credited with moving a pellet reads what it did (`Retrieved`,
  `Displaced ...`; `retrieved`/`displaced_sa`/`displaced_outside` in the
  complete record and `reach_data.csv`);
- every other reach at that pellet reads `Missed` -- naming the reach that moved
  a pellet answers every other reach at it, watched or not;
- every reach at an untouched pellet reads `Missed`, and so does every reach at a
  pellet a person saw moved by something other than a reach;
- `Undetermined` when the pellet was not decided, or when it moved and NO reach
  is credited with it (one of its reaches moved it and which one is not
  recorded; calling them all `Missed` would be false). These pellets are a
  defect to fix in the reaching pipeline, by a person naming the reach.

`reach_contact_group` (was `contact_group`) is worked out from the reach
outcome, so the two can never disagree: `contacted` when this reach retrieved or
displaced the pellet, `missed` when it did not. It does not mean "a contact frame was recorded": a credited reach a person picked has no contact frame, yet it reads `contacted` beside the pellet it retrieved.

**New totals.** Untouched pellets per tray (`session_untouched_ai`,
`session_untouched_hand`) and the day's pellets, retrieved, displaced, contacted
and untouched from both streams (`day_pellets_ai` ... `day_untouched_hand`).
The day totals count **pillar trays only** in both streams, so the algorithm's
and the hand scores' can be compared (the algorithm decides no pellet on easy or
flat trays; `ODC_days` `Daily_*` counts every tray). The algorithm's day
totals are empty exactly when `day_reaches_ai` is (a tray of the day not filed,
or no hand scores for the day -- below), and also when a filed pillar tray of
the day has no per-pellet record or the day had no pillar tray. The hand-score
day totals are given whenever the sheet holds them. In the analysis tables every
day column is emptied for a day that lost a tray to a known problem.

**Renamed columns** (an older file uses the left-hand names):

| file | was | now |
|---|---|---|
| complete record, `reach_data.csv` | `outcome`, `causal_reach`, `contact_group` | `reach_outcome`, `reach_causal`, `reach_contact_group` |
| complete record | `Session_Weight_g`, `Session_Weight_Pct` | `Day_Weight_g`, `Day_Weight_Pct` |
| complete record | `Video_Reaches_AI`, `Attention_AI` | `Session_Reaches_AI`, `Session_Attention_AI` |
| complete record | `Video_Pellets/Displaced/Retrieved/Contacted` | `Session_Pellets/Displaced/Retrieved/Contacted_AI` |
| complete record | `Manual_Pellets/Displaced/Retrieved/Contacted`, `Contacted_Match` | `Session_Pellets/Displaced/Retrieved/Contacted_Hand`, `Session_Contacted_Difference` |
| complete record | `Day_Total_Reaches_AI`, `Day_Total_Reaches_Complete` | `Day_Reaches_AI`, `Day_Complete` |
| analysis | `pellet_number`, `swipe_id` | `segment_number`, `reach_number` |
| analysis | `pellet_outcome`, `pellet_state_at_this_reach` | `segment_outcome`, `reach_pellet_state` |
| analysis | `reaches_at_this_pellet`, `is_first/last_reach_at_pellet`, `pellet_position_idealness` | `segment_reaches`, `reach_is_first/last_in_segment`, `segment_pellet_position_idealness` |
| analysis | `attention_at_this_reach`, `attention_ai` | `segment_attention`, `session_attention_ai` |
| complete record, `reach_data.csv` | `attention_score` | `segment_attention` |
| analysis | `body_weight_g`, `body_weight_percent_of_free_feeding` | `day_body_weight_g`, `day_body_weight_percent_of_free_feeding` |
| analysis | `pellets_in_video`, `video_displaced/retrieved/contacted` | `session_pellets_ai`, `session_displaced/retrieved/contacted_ai` |
| analysis | `pellets_hand_scored`, `manual_displaced/retrieved/contacted` | `session_pellets_hand`, `session_displaced/retrieved/contacted_hand` |
| analysis | `reaches_in_video`, `day_total_reaches`, `day_total_reaches_complete` | `session_reaches_ai`, `day_reaches_ai`, `day_complete` |
| sessions analysis | `pellets_presented`, `pellets_reached_for`, `reaches_that_decided_a_pellet`, `reaches_after_the_pellet_was_gone` | `session_` + the same name |
| sessions analysis | `percent_<x>_video`, `percent_<x>_hand_scored` (`missed` = untouched) | `session_percent_<x>_ai`, `session_percent_<x>_hand` (`untouched`) |
| shareable table | `pellet_number`, `reach_number_in_pellet`, `reaches_at_this_pellet`, `is_first/last_reach_at_pellet` | `segment_number`, `reach_number`, `segment_reaches`, `reach_is_first/last_in_segment` |
| shareable table | `reach_result`, `pellet_result`, `reach_decided_the_pellet` | `reach_outcome`, `segment_outcome`, `reach_causal` |
| shareable table | `mean_attention_score_in_session`, `reaches_in_video`, `day_total_reaches(_complete)` | `session_attention_ai`, `session_reaches_ai`, `day_reaches_ai`, `day_complete` |
| shareable table | `pellets_in_video`, `pellets_retrieved/displaced/moved_in_session` | `session_pellets_ai`, `session_retrieved/displaced/contacted_ai` |
| shareable table | `pellets_hand_scored`, `pellets_retrieved/displaced/moved_hand_scored` | `session_pellets_hand`, `session_retrieved/displaced/contacted_hand` |

**Attention is a segment value.** The outcome
detector scores attention once per pellet segment, from the pose: the percentage
(0-100) of the segment's frames in which the animal was attending to the tray. A
per-reach attention would mean nothing, because an animal that is reaching is
attending. So the per-reach files carry it as `segment_attention`, the same value
on every reach of a segment, and `session_attention_ai` is the mean over EVERY
segment of the tray -- a segment the animal never attended counts 0, and segments
with and without reaches count alike, on every tray. Averaging only the segments that had reaches would score a low-reaching animal on just the pellets it attended to. The session's mean is the one the reach
import records per video (`video_sessions`); only a snapshot too old to have that
table falls back to the mean over the reached segments. Scale: percent 0-100 (a
dataset that gives attention as a 0-1 fraction must be multiplied by 100 to
compare).

Every renamed column's dictionary entry says what it was called before. The
first nightly export after this change rewrites every file (a change to the
exporter's code always does), so all files carry the new names from that night.
The names in the sections below that predate this change are the old ones; this
table converts them.

### A session a person flagged

In the reaching pipeline's review tools a person can use **Flag Session** on a
video and write why. The note is saved in the video's features file
(`session_flag`), the nightly reach import copies it onto every reach row of
that session in the database (`reach_data.session_flag`, `_reason`, `_by`,
`_at`; the columns are added to an older database by the import itself), and
every per-reach file and the sessions analysis file carry it:

| column | what it says |
|---|---|
| `session_flag` | `Yes` when a person flagged this session (one tray, one video), `No` otherwise |
| `session_flag_reason` | the note as written |
| `session_flag_by` | who flagged it |
| `session_flag_at` | when |

Where the session is not flagged the three note columns read `Not applicable`
(`NA` in the analysis files; empty in `reach_data.csv`). **A flag is a marker
only**: nothing in the exports leaves out, blanks
or treats a flagged row differently; it is recorded so that flagged sessions can
be found later. A flag added or changed on a filed video changes its features
file, so the next nightly import and export pick it up.

### Session measurements in the sessions file

`sessions_<cohort>_analysis.csv` carries, beside the pellet counts, these
columns. They are worked out once, in the export itself, from the same rows as
the rest of the file; the per-reach files do not carry them. Absent values read
`NA`, never an empty cell. Each column's dictionary row repeats its definition.

| column(s) | what it is |
|---|---|
| `day_body_weight_g`, `day_body_weight_percent_of_free_feeding` | the animal's weight on the day (the value every reach row of that day carries; also on a tray with no reach) |
| `day_pillar_reaches_ai` | the animal's reaches on that day over all its pillar trays (an earlier program's "Total Swipes"); `NA` on an easy or flat tray's row |
| `session_duration_sec` | the video's length: total frames / frame rate from the results file |
| `session_reaches_per_minute` | `session_reaches_ai` / (length / 60); 0 on a tray with no reach |
| `session_median_latency_to_first_reach_sec` | median over the pellets that got a reach of the time from the pellet's first frame to its first reach |
| `session_mean_inter_reach_interval_sec` | mean over every gap (end of a reach to start of the next) between consecutive reaches at the same pellet |
| `session_percent_retrieved_of_contacted_ai` / `_hand` | success given contact: retrieved / (retrieved + displaced) x 100; `NA` when nothing was contacted |
| `session_first_attempt_retrieved_ai`, `session_percent_first_attempt_success_ai` | pellets retrieved by the first reach made at them, as a count and as a percentage of the pellets reached for |
| `session_median_<m>`, `session_mean_<m>`, `session_sem_<m>`, `session_n_<m>` | for EVERY per-reach kinematic measure `<m>` of the reach file (its `reach_...` measurements, without frame numbers and tracking-confidence columns): the session median, mean, standard error of the mean (sample SD / square root of n; `NA` when n < 2) and n, the reaches with a value. Every reach of the session counts, also one made after its pellet was gone; the session's total number of reaches is `session_reaches_ai` |
| `session_displaced_in_scoring_area_ai`, `session_displaced_outside_ai` | the algorithm's displaced pellets split by where they went (they add up to `session_displaced_ai`) |
| `session_moved_not_by_reach_ai` | pellets a person saw moved by something other than a reach (counted in `session_untouched_ai` too) |
| `session_mean_pellet_position_idealness` | mean over the tray's pellets of how well each sat relative to the slit (0-1) |
| `day_recording_status`, `day_recording_issue_reason` | whether a recording of one of the animal's trays that day failed (see "A recording that failed"); `NA` only when the snapshot has no record of failed recordings at all |

**On a tray with no reach** the latency, the gap between reaches, both
first-attempt columns and every kinematics column (n included) read `NA`
(there is no reach to measure); `session_reaches_ai` reads 0 and everything
else is filled. **On an easy or flat tray** every algorithm pellet measure
(latency, gap, first-attempt, success given contact from the algorithm,
displaced split, moved not by a reach, placement, day pillar reaches) reads
`NA`, as the algorithm's pellet counts do; the hand-scored success given
contact, length, reaches per minute, weight, recording status and the
kinematics are filled.

**Where the timing comes from.** The video's length and each pellet's latency,
gap and placement are in the pipeline's results file. The nightly reach import
copies them into the database (`video_sessions.total_frames`, `frame_rate`;
`video_pellets.time_to_first_reach_sec`, `mean_inter_reach_interval_sec`,
`pellet_position_idealness`); an older database gains these columns on its next import, and `mousedb import-reaches --pellets-only` fills
them for videos already imported. Until then those columns read `NA`.

### Swipe measures and reach counts: which column is which

An earlier reach program reported its own per-swipe measures (swipe length,
breadth, area, speed, path length, duration in frames, path over frames).
Analyses built on those numbers can only be continued if the same numbers are computed the same way, so the kinematics extractor
(MouseReach, version 2.6.0 and later) now also computes them on every reach, as
nine `legacy_swipe_*` values. They are in pixels, as the earlier program
reported them, and the speed is in "(pixels / 4) per second" -- the earlier
program's own unit, **not** millimetres. Each has a full definition in the
dictionaries.

**What changed in the analysis table (`reaches_<cohort>_analysis.csv`).** The
columns named after the earlier program hold the earlier program's measure. An older file may instead hold the pipeline's own measures there, which are different numbers in different units. If you have an older file, use this table to line the two up:

| name in the analysis table | holds | an older file held this, now found under |
|---|---|---|
| `swipe_length` | `legacy_swipe_length_px` (pixels) | `righthand_extension_past_nose_mm` |
| `swipe_breadth` | `legacy_swipe_breadth_px` (pixels) | `righthand_lateral_deviation_mm` |
| `swipe_area` | `legacy_swipe_area_px2` (pixels^2) | `righthand_swept_area_mm2` |
| `swipe_speed` | `legacy_swipe_speed_px_div4_per_sec` ((pixels / 4) per second) | `righthand_peak_speed_mm_per_frame` |
| `path_length` | `legacy_swipe_path_length_px` (pixels) | `righthand_total_path_mm` |
| `swipe_duration_frames` | `legacy_swipe_duration_frames` (frames) | `duration_frames` |
| `path_over_frames` (new) | `legacy_swipe_path_over_frames_px_per_frame` | -- |

**Reaches filed before extractor 2.6.0 have no earlier-definition values.**
On those rows the `swipe_*` columns read `NA`. In the complete record and the
shareable table the `legacy_swipe_*` columns appear only once some reach of the
cohort carries the values; until then they are not in those files at all, and
once they are, a reach without values reads `Not recorded`. They are never
filled in from the pipeline's own measure. They fill in when those videos are
measured again with extractor 2.6.0 and imported.

**Reach counts.** "Session" in these files means one tray's video. The earlier
program's Total Swipes counted the reaches of one animal over a whole testing
day, every tray. So there are two counts, each named for what it counts:

| complete record | analysis and shareable tables | what it counts |
|---|---|---|
| `Session_Reaches_AI` (older exports: `Video_Reaches_AI`, `Total_Swipes_AI`) | `session_reaches_ai` (older exports: `reaches_in_video`, `reaches_in_session`) | reaches in this video: one tray |
| `Day_Reaches_AI` | `day_reaches_ai` | reaches by this animal on this date, every tray of the day: the earlier program's Total Swipes |
| `Day_Complete` | `day_complete` | `yes` or `no`: is the day's total complete |

**A day's total is given only when the day is complete.** Only filed videos
have reach rows, so a day with a tray still in review would otherwise give a
smaller number that looks like a real one. The trays a day should have are the
ones the tracking sheet has hand scores for, for that animal and date. When
every one of them is filed, the day reads `yes` and the total is given. When
any is not, it reads `no` and the total is empty (`Undetermined` in the complete
record and the shareable table, `NA` in the analysis tables). A day with no hand
scores at all reads `no`: there is nothing to check against. A filed pillar tray
where the animal never reached counts, as 0. An easy or flat tray that is filed
but has no reaches leaves no rows to show it is filed, so its day reads `no`.
In the analysis tables a day also reads `no` when one of its trays was left out
as a known problem (`analysis_EXCLUSIONS.csv`), so the left-out tray's reaches
are not counted back in through the total.

### No cell is ever empty -- and the word tells you why

ODC-SCI rejects a dataset with empty cells, so both files fill every one. The
filler is not a single "NA", because a blank means four different things and
treating them alike would throw away what you actually know:

| you will see | it means |
|---|---|
| `Not applicable` | the question cannot apply to this row -- days-post-injury for a session that happened before the injury; every pellet column on an easy or flat training tray |
| `Not measured` | no code computes this value (the column exists so the column set stays stable) |
| `Not recorded` | a source should carry it and does not -- nobody wrote it in the sheet |
| `Undetermined` | in `reach_result` / `pellet_result`: a pellet that reached the files without anyone deciding it. That should not happen -- a pellet nobody decided keeps its video in review. If one is found anyway (a reviewer moved on without picking an outcome), the video is sent back to review and leaves the exports until they are answered (see "Only filed videos are in the database" below) |

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

**When the study-facts file is damaged.** The file is plain JSON, so a hand
edit with a stray comma, or a save cut off part-way, can leave it unreadable.
Such a file is refused, never read as "nothing set": `mousedb study-facts`
prints `[FAIL] the study-facts file <path> is damaged and was not read (...)`
and exits 1; the Study Facts tab shows `NOT READ -- ...` in red at the top
and no values; and the exports do not write any cohort whose rows need the
facts -- each such cohort is listed under `failures` in `MANIFEST.json` with
that same sentence, and the nightly job reports `[FAIL]`. Nothing is ever
written over the damaged file: a Set or Clear (tab or command) is refused,
and a copy of the file as it was is kept beside it as
`study_facts.json.damaged-<date>-<time>`. To mend it, open the file named in
the message in a text editor (Notepad is fine), correct it so it reads as
JSON (`{"<PROJECT>": {"<column>": "<value>"}, ...}`), save, and run
`mousedb study-facts --show`: when it lists your facts again, everything
works again; the next export rewrites the files. A damaged file is never read as empty (that would make every export write these columns blank without saying why, and the next Set write back a file holding only that one value -- every
other project's facts and exceptions would be lost). Saves are made whole: the new file is written beside the old one and swapped in, so a save
cut off part-way leaves the previous file as it was.

**ASPA file names.** A frozen ASPA cohort's files are named `..._ASPA_NN_X`,
e.g. `ODC_reaches_ASPA_04_D.csv`: `X` is the cohort letter the lab knows it by
(its workbook and folder), `NN` is the same cohort as the pipeline encodes it
(the letter's position in the alphabet). `MANIFEST.json` names the workbook
each one came from. Files under an older naming are moved (not deleted)
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

**Each night only the cohorts that changed are rewritten.**
The big per-reach files -- each cohort's complete record, summary, analysis
and sessions files, and each project's combined `*_all_cohorts*` files -- are
not rebuilt from nothing every night. What happens, in plain words:

1. **Nothing at all changed** (no new video, no sheet correction, no new
   setting): every per-reach file is kept as it was. `MANIFEST.json` says so
   (`odc_reaches_skipped`).
2. **Something changed**: mousedb works out, cohort by cohort, whether anything
   that cohort's files are made from is different from last night -- its
   reaches (a new video, a video analyzed again, a video withdrawn), its
   animals' sheet records and hand scores, its per-pellet rows and weights,
   what the "left out" checks say about it, the study facts for its project,
   whether it is still running (its phase labels are withheld until it is
   over, and that can change with the date alone), and where its folder is.
   **A cohort with any difference has all its files rewritten. A cohort with
   none keeps its files untouched** (same bytes, same time). A whole cohort is
   rewritten, not just its new rows, because one new tray changes other rows
   too -- the animal's day total, the running row number (`entry_index`),
   a new column one video brought in.
3. **The combined file of a project** is rewritten only when one of its
   cohorts was; the part of it before the first changed cohort is copied as it
   was, and only the rest is written again.
4. **Everything is rebuilt from nothing** only when the mousedb code that
   writes the files has changed (the first night after an install -- this
   takes hours), when the record of what was written last night
   (`.export_units.json`, a hidden file in this folder) is missing or
   unreadable, or when someone runs `mousedb-current-exports --full`. A
   change to a lab-wide setting (the lab name, the field definitions) touches
   every cohort, so every cohort is rewritten too.
5. **A file changed or deleted by hand** is noticed (its size or time is not
   what was written) and that cohort is rewritten.

The result is the same, byte for byte, as rebuilding everything: that is
checked by the test suite for every kind of change above, and can be checked
on real data with `mousedb export-incremental-check` (see INSTALL.md).

`MANIFEST.json` says what happened under `incremental`: `mode` (`incremental`
or `full`, and `why_full`), `at` (when), `cohorts_rewritten` (each with `why`
-- which inputs changed -- and the videos appended, replaced and removed),
`cohorts_kept` (each with when its files were written), `cohorts_removed`,
and which combined files were rewritten or kept. On a night when many videos
were analyzed again (a new MouseReach version), most cohorts change, and that
night takes about as long as a full rebuild.

## Limited exports: just part of the data

Often you need only part of the exports: a few cohorts, the last sessions
before the injury, the pillar trays, a handful of columns, and a column saying
which animals (or which days of each animal) go into one analysis. A **limited export** is exactly that: a
copy of part of `exports/Behavior/All_Cohorts/`, made by mousedb, saved by name so it can be
made again.

**Nothing is calculated again.** Every value in a limited export is copied,
unchanged, from the full export it was cut from. Only rows and columns are left
out. So it can never disagree with the full export, and a correction that
reaches the full export reaches the limited export the next time it is made.

### Making one (no terminal)

1. Open the MouseDB window, tab **9. MouseReach Video Processing Progress**, and press **Make a
   limited export...**. A window opens.
2. At the top, pick a saved limited export from the list to change or remake
   it, or leave it on "(a new limited export)" and type a **Name** (letters,
   digits, `-` and `_`, starting with a letter or digit; it becomes the
   folder name).
3. **Cohorts**: tick the cohorts you want. The list is the cohorts the current
   exports hold.
4. **Tray types**: tick Pillar, Easy and/or Flat. None ticked = every tray.
5. **Sessions**, one of:
   - **All sessions**;
   - **The last N [tray type] sessions before the cohort's first injury** --
     see "Which sessions 'the last N before the injury' picks" below;
   - **From** a date **to** a date (either may be left empty, not both).
6. **Kinds of file**: tick the files you want (the same kinds as the full
   export: per-reach complete record, per-reach shareable, per-reach analysis,
   per-session analysis, per-day ODC sessions). All are ticked to start with.
7. **Columns**: pick a kind under "Columns of", then untick the columns you do
   not want. All ticked means every column of that kind. Each kind keeps its
   own choice. Only columns the full export has are listed -- except one that
   a saved limited export names and the full export no longer has (for
   example a column renamed since): it is listed at the end, ticked, marked
   "(not in the current exports)". Untick it to take it out; while it is in,
   making the limited export is refused with its name.

   Opening a saved limited export and pressing Save without changing anything
   leaves it exactly as it was. In particular a limited export saved with
   `--project` (every cohort of a project, including cohorts added later)
   stays that way as long as you leave its cohort ticks as they are; change
   them and it becomes a fixed list of the cohorts you ticked (the box at the
   bottom says this when you open it). A cohort it names that the exports no
   longer hold is listed the same way as such a column. Its description, if it
   has one, is kept.
8. **Yes/no column (optional) -- name:** type a column name to add a column
   holding "yes" or "no" for each animal (or, with a day-window rule, for each
   animal on each day). Choose a rule in **filled by rule** to fill it, or leave
   it on "(none -- leave it blank)" to fill it in later (see "The yes/no
   column" below).
9. **Remake nightly** (ticked to start with): make it again after the nightly
   export, whenever anything it holds has changed.
10. Press **Save and make now** (or **Save** to save without making it). It is
    saved for everyone (in the lab settings file), then made. If the name is
    already saved with a different definition -- a name you typed that is
    taken, or a saved limited export you opened and changed -- a box asks
    whether to replace it: **No** leaves the saved
    one as it is (pick another name); **Yes** replaces it, and the replaced
    definition is kept in the lab settings file under `export_subsets_replaced`
    with the time it was replaced. Then the box at the bottom says what was
    written, with the row count of every file. A large set of cohorts takes a few minutes.
    **Check only (writes nothing)** counts the rows of what the window shows
    now without writing any file -- it does not save the definition either.
    **Open its folder** opens the result.

If something is wrong (a column the full export does not have, the exports
being rewritten right now, a full export that is not complete) a box says so
and **no file is written**. (With **Save and make now** the definition was
already saved before the making was refused; it stays saved. If the reason
lasts -- a column the full export does not have, say -- the nightly job then
reports that limited export as failed every night until it is fixed or
forgotten.)

**To forget a saved limited export**, open it in the window and press
**Forget this definition** (a box asks first), or run
`mousedb export-subset <name> --remove`. The files already made stay where
they are, it is no longer remade at night, and its line leaves the Where Is
My Data tab. There is no undo button or command: to bring it back, save it
again (in the window, or with `--save` and its options), or copy its
`definition` from `export_subsets_replaced` back under `export_subsets` in the
lab settings file (see "Where the definitions are kept").

**A full export that is not complete is never cut.** When a part of the
nightly (or a "Refresh exports now") export failed, `exports/Behavior/All_Cohorts/MANIFEST.json`
says `"complete": false` and lists the problems; the files of the part that
failed are an earlier run's, or missing. A limited export made from it would
mix two runs without showing it, so the button, `--run` and `--dry-run` all
refuse it, quoting the problems. Rewrite the full export (press **Refresh
exports now**, or wait for the nightly job), and make the limited export again
once the tab says the export is complete.

From a terminal, the same thing (MouseDB environment active):

```
mousedb export-subset <name> --save --run --cohorts PROJA_01 PROJA_02 --tray-types Pillar --last-sessions 3
mousedb export-subset <name> --run          # remake a saved one
mousedb export-subset <name> --dry-run      # check and count; write nothing
mousedb export-subset --list                # every saved one, and how its last attempt went
mousedb export-subset <name> --remove       # forget the definition; files already made stay
mousedb export-subset <name> --save --replace ...   # replace a saved definition of that name
```

`--save` refuses a name that already holds a different definition ("is
already saved ... nothing was changed"); add `--replace` to replace it.
Saving exactly what is already saved changes nothing. `--save --replace`
builds the whole definition from the options on that command line: anything
you leave out goes back to its default (for example, without `--not-nightly`
it is remade nightly again). `--run` with options but without `--save` makes
a one-off limited export under that name and saves nothing.

What the command prints and its exit code (the window shows the same text):

| exit code | meaning | the line starts |
|---|---|---|
| 0 | done (also: `--list`, `--save` alone, `--remove`, a dry run) | `Limited export <name>: ...`, `Saved ...`, `Forgot ...` |
| 6 | refused; no file was written (a taken name without `--replace`, no saved limited export of that name, a column the full export lacks, a full export that is not complete, the sessions from a different night, no action given). With `--save --run` the definition was saved before the making was refused, and stays saved | `[!] ` and the reason |
| 3 | busy: the full exports are being rewritten (only with `--no-wait`, which the window uses) | `[!] ` |
| 1 | the exports lock could not be taken (`[!] the exports lock could not be taken: ... Nothing was written.`), or the files could not be put in place (`[FAIL] limited export <name>: ...`, saying whether anything was changed) | `[!]` or `[FAIL]` |

Other options: `--project PROJA` (every cohort of a project), `--from` /
`--to` (a date window), `--session-tray` (which tray type's sessions
`--last-sessions` counts; default the first `--tray-types`), `--kinds`,
`--columns KIND=col1,col2`, `--flag-column`, `--flag-rule`, `--not-nightly`,
`--all-saved` (remake every saved one). `mousedb export-subset --help` lists
them all.

### What you get

`<mousedb_root>/exports/Behavior/Selected_Cohorts/<name>/`:

| file | what it is |
|---|---|
| `ODC_reaches_<name>.csv`, `reaches_<name>_summary.csv`, `reaches_<name>_analysis.csv`, `sessions_<name>_analysis.csv`, `ODC_days_<name>.csv` | the kinds you chose. Every chosen cohort is in the one file, cohort after cohort, rows in the same order as in the full export. In the analysis files, `entry_index` is counted again from 1 over the rows of this file |
| `<kind>_DATA_DICTIONARY.csv` | the full export's dictionary of that kind, with only the columns you kept (in the same order as the file), plus the yes/no column if you asked for one |
| `analysis_EXCLUSIONS.csv` | the entries the full export leaves out for a known problem, only those of your cohorts and sessions. One that covers a whole cohort (no video named) is kept for that cohort |
| `SUBSET_MANIFEST.json` | the definition, the full export it was cut from (`source_exports_written_at`) and that export's snapshot (`source_snapshot_generation`), when it was written, the rows of each file and of each cohort in it, which sessions were chosen for each animal, and any problems |
| `README.txt` | the same, in plain words |

A column one of your cohorts does not have (for example a surgery column only
some cohorts' sheets record) is filled for that cohort with the file's word
for absent: `Not recorded` in the ODC and summary files, `NA` in the analysis
files. `SUBSET_MANIFEST.json` lists which cohorts and columns, under
`columns_filled_for_cohorts_without_them`.

**Making it again never deletes anything.** The previous files are moved to
`<mousedb_root>/_archived/exports_subsets/<name>/<date_time>/` and the new
files are put in place.

**The folder always holds one complete set.** The new files are first copied
into the folder under temporary names (`_incoming_<file>.tmp`); only when all
of them are there are the previous files moved to the archive and the new ones
renamed into place, the manifest and README last. If anything fails on the way
-- the share is full, or a file of the limited export is open in another
program (a spreadsheet program holds the file it shows) -- what was done is
undone: the previous files are back where they were, and the window (or the
command) says "Nothing was changed" with the reason. Close the file, or make
room, and make it again.

Only if undoing ALSO fails is the folder left part-way. Then a file
`NOT_COMPLETE.txt` in it says so and where the previous files went, the
window shows the same message (starting `[FAIL]`), and the MouseReach Video Processing Progress
tab shows the failure. Do not use the files there; make the limited export
again -- that moves everything in the folder, the note included, to the
archive and writes a complete set.

### Which sessions "the last N before the injury" picks

The tracking sheet decides when things happened. For each animal:

1. take the dates on which the sheet records hand scores for a tray of the
   chosen type (for example Pillar);
2. keep the dates strictly before the cohort's **first injury**: the earliest
   contusion date of any animal of that cohort, from the sheet's injury tab
   (so an animal injured a day later than its cage mates still uses the
   cohort's first date);
3. keep the last N of those dates.

Then every row of those animal-days is kept -- of the tray types you ticked
(tick the same tray type to get only those trays). An animal with fewer than N
such dates keeps the ones it has; `SUBSET_MANIFEST.json` lists it under
`animals_with_fewer_sessions`. A cohort whose sheet has no injury date yet has
no such sessions, so its animals have no rows; the manifest lists it under
`cohorts_without_injury_date`, and README.txt says so.

A chosen session can still have no rows in a file: the sheet has the day, but
the full export does not (no recording of it was found, its video is still in
review, or the animal did not reach). Each file's entry in
`SUBSET_MANIFEST.json` lists those animal-days under
`chosen_sessions_with_no_rows`, and README.txt gives their number, so a slice
short of sessions is not mistaken for a complete one.

The sessions are read from the same snapshot the full export was made from.
Between the two steps of the nightly job (the snapshot is new, the full export
not rewritten yet) a limited export is refused with "the sessions would be
chosen from a different night's records than the files" -- try again once the
full export has been rewritten. A full export written from a snapshot made
before refreshes were numbered records no snapshot at all; it is refused the
same way ("the current exports do not record which snapshot they were made
from"): press **Refresh exports now** once, and try again.

### Which rows a filter keeps, file by file

- Per-reach and per-session files: a row is kept when its animal, date and tray
  type pass. The animal is read from the row's video name, so a cohort shown
  with letter ids (`D01`) is matched the same way as any other.
- `ODC_days` (one row per animal per day, up to four trays across the row; `ODC_sessions` is accepted as its old name):
  the day is kept when it is in the session window and ANY of its trays is of a
  ticked type. The row is copied whole, so it can still show a tray of another
  type that day.

### The yes/no column

A limited export can carry one extra column that says `yes` or `no` on every
row: which rows go into one analysis. What decides yes or no is a **rule**.
Its entry in the data dictionary says, in words, how it was filled. With no
rule chosen the column is left blank (written as the file's word for absent,
because an ODC file may not hold an empty cell) -- the column to fill once the
rule is decided.

There are two sorts of rule:

- **Per animal** (`listed`, `share_at_least`): one answer for each animal, the
  same on every row of that animal.
- **Per animal-day** (`day_window`): one answer for each animal on each test
  date, the same on every row of that animal on that date. Use this when the
  analysis takes some DAYS of every animal -- for example "each animal's last 3
  test days before the injury".

A rule is defined once in the lab settings file,
`<mousedb_root>/config/mousedb_lab_config.json`, under
`export_subset_flag_rules`, and then picked by name (in the window's "filled
by rule" list, or with `--flag-rule <name>`). The three kinds:

```
"export_subset_flag_rules": {
  "<rule name>": {"type": "listed", "animals": ["PROJA_01_03", "PROJA_02_11"]},
  "<rule name>": {"type": "share_at_least", "kind": "sessions_analysis",
                  "numerator": "manual_retrieved", "denominator": "pellets_hand_scored",
                  "percent": 30},
  "<rule name>": {"type": "day_window", "n": 3, "tray_type": "Pillar",
                  "before_first": "contusion"}
}
```

`listed`: yes for the animals named (the pipeline's ids, `PROJECT_CC_SS`), no
for every other. `share_at_least`: yes when the animal's total of one column
divided by its total of another, over the rows of the limited export (its
cohorts, sessions and trays) of that kind of file, is at least the percent; no
when below; blank for an animal with no such rows.

`day_window`: **yes on every row of each animal's last `n` test days before
its cohort's first `before_first` surgery; no on every row of any other day.
Never blank.**

- A **test day** of an animal is a date on which the cohort's tracking sheet
  has hand pellet scores for a tray of `tray_type` (`Pillar`/`P`,
  `Easy`/`E` or `Flat`/`F`). The tray type only decides which dates count as
  test days; once a day is chosen, EVERY row of that animal on that date is
  `yes`, whatever its tray.
- **Before the cohort's first `before_first`**: the earliest date of that
  surgery type (`contusion`, `tracing` or `perfusion`) for ANY animal of the
  cohort, in the tracking sheet; days on or after it never count. A cohort
  with no such date in the sheet gets `no` on every row; the README and
  `SUBSET_MANIFEST.json` (`flag_days`, `cohorts_without_event_date`) name it.
- **The sheet decides the days, not the export.** The days are chosen from the
  tracking sheet's records (the same snapshot the files were made from), so a
  chosen day whose videos are not filed yet still counts as one of the last
  `n`; an earlier day never takes its place. Such a day simply has no rows yet.
  Each file's entry in `SUBSET_MANIFEST.json` lists every chosen day that has
  no rows in that file (`flag_days_with_no_rows`), and the README gives the
  count, so a short set of `yes` rows is never mistaken for a complete one.
  Once the videos are filed and the nightly export has run, their rows appear
  with `yes` on their own.
- The manifest's `flag_days` lists, for each animal, the days chosen
  (`days_chosen`), animals with fewer than `n` such days
  (`animals_with_fewer_days`, which keep the ones they have), and each
  cohort's `first_event_date`.
- It is the same choice of days as the "last N sessions before the injury"
  session window, but it does not leave any rows out: the window cuts the
  file down to those days; a day-window rule keeps every row and marks them.

**Setting up a day-window rule, step by step** (no programming; a text editor
is enough):

1. Open `<mousedb_root>/config/mousedb_lab_config.json` in a text editor
   (Notepad is fine). Make a copy of the file first, beside it, with the date
   in its name.
2. Find `"export_subset_flag_rules"`. If it is missing, or says `null`, make it
   `"export_subset_flag_rules": {}` (mind the comma before it if it is not the
   first entry).
3. Inside its `{ }`, add one line, choosing your own name, number of days,
   tray type and surgery type:
   `"my_last3_pillar_days": {"type": "day_window", "n": 3, "tray_type": "Pillar", "before_first": "contusion"}`
   (separate it from any rule already there with a comma).
4. Save the file. Check it reads correctly:
   `mousedb export-subset --list` prints every rule (`yes/no rule ...`) and
   every saved limited export. A typing mistake in the file makes it refuse
   with the file's name; undo it from your copy. (`--list` checks only that
   the file can be read; a wrong value inside a rule -- say a tray type that
   does not exist -- is found when a limited export using it is made, or with
   **Check only**.)
5. In **Make a limited export...**, open (or make) the limited export, type
   the yes/no column's name, pick your rule in "filled by rule", and press
   **Check only** -- the summary counts the rows -- then **Save** or **Save
   and make now**. From a terminal:
   `mousedb export-subset <name> --save --replace --flag-column used_for_x --flag-rule my_last3_pillar_days ...`
   (with the same other options the definition already has).

A rule only reads the slice and the tracking sheet; it never changes a value
in it. Changing a rule (its number of days, say) is picked up the next time
the limited export is made -- by the nightly remake too, since a rule is part
of what the remake compares; so is a correction to the sheet's dates.

### When it is made again by itself

Each night, after the full export has been rewritten **completely** (and
after the job has let go of the database, so imports never wait for it),
every saved limited export with "Remake nightly" ticked is checked:

- if nothing it is cut from changed -- its source files, the dictionaries, the
  exclusions, the sheet's sessions and injury dates, its definition, its rule,
  mousedb's own code -- it is left as it is;
- otherwise it is built again. If every file comes out byte for byte the same
  as the one already there (the full export rewrites many of its files each
  night with the same content), the files already there are kept;
- only when a file comes out different are the previous files moved to the
  archive and the new ones put in place.

WHY: a limited export that is not remade goes stale the first night something
it holds changes -- a video leaves review, a sheet row is corrected -- while
still looking current. Putting new files in place only when they differ keeps
the archive from filling with identical copies. A night the full export is
incomplete, nothing is remade: a slice of a partial export would be partial
too. A remake you start yourself (the button or `--run`) always writes the
files.

The MouseReach Video Processing Progress tab shows one line per saved limited export below the
export files: when it was last made, or `[FAIL] <name>: ...` with the reason
(for example a column that the full export no longer has). A limited export
that cannot be made also makes the nightly job report a failure. The record
behind those lines is `exports/Behavior/Selected_Cohorts/SUBSETS_STATUS.json`.

### Where the definitions are kept

In the lab settings file, `<mousedb_root>/config/mousedb_lab_config.json` (or
the file named by `MOUSEDB_LAB_CONFIG`), under `export_subsets` -- one file
for every computer and every login, beside the database. Never in mousedb's
code. Two people saving at the same moment do not lose each other's change:
each save locks the file for the fraction of a second it takes (see "Which projects' sheets are read" in TRACKING_SHEETS.md). A definition looks like this:

```
"export_subsets": {
  "<name>": {
    "cohorts": ["PROJA_01", "PROJA_02"],
    "tray_types": ["P"],
    "window": {"type": "last_before_injury", "n": 3, "tray_type": "P"},
    "kinds": ["ODC_reaches", "reaches_summary", "reaches_analysis", "sessions_analysis", "ODC_days"],
    "columns": {"ODC_reaches": ["subject_id", "video_name", "reach_start_frame", "reach_end_frame"]},
    "flag": {"column": "<column name>", "rule": null},
    "nightly": true
  }
}
```

A column named here that the full export does not have is refused by name
when the limited export is made -- never silently left out.

**A definition is never simply overwritten or deleted.** One that is replaced
(Yes in the window, `--replace` on the command) or forgotten ("Forget this
definition", `--remove`) is kept in the same file under
`export_subsets_replaced`, by name, oldest first, each with `until` (when it
stopped being the saved one), `why` (`replaced` or `forgotten`) and the
`definition` itself. To bring one back, copy its `definition` into a new
limited export (or ask whoever looks after the lab settings).

## How far along are we? Counted against the tracking sheets

**Every "how far along" number mousedb gives is counted against the tracking
sheets, not against the videos.** The MouseReach Video Processing Progress tab, the weekly page and
`mousedb sheet-progress` all give the same figure:

> **done = sessions filed / sessions scored by hand in the sheets**

A *session* is one animal, on one tray, on one day.

### Why the sheets and not the videos

The person at the bench scores every tray in the tracking sheet on the day it
is recorded. So the sheet is the one complete list of what was really
recorded. The pipeline only knows the videos someone sent to it. If a
recording is never copied off the recording PC, the pipeline has never heard
of it, and a progress figure counted from the pipeline would say "100% done"
while that recording sits forgotten. Counted against the sheets, those sessions
show up in red as **not on the server yet**.

### The five states

Every session scored in a sheet is in exactly one of these:

| state | what it means | what to do |
|---|---|---|
| **filed** | the video was analyzed, passed every check, and is in the Analyzed folder | nothing -- it is done |
| **in processing** | the video is on the server and moving: waiting to be cut, being posed, in the analysis steps, or analyzed and waiting for the sheet's next import so it can be compared with its hand scores | nothing -- the machines are on it |
| **held for review** | waiting for a person: in the triage or deep-review queue (a quality check that could not run also holds a video in deep review), set aside as unprocessable (quarantine), or held by the pipeline outside the queues for something only a person can fix -- a **label mismatch** (the video's results match another tray's hand scores, so the sheet or the video's name has the trays swapped) or hand scores the pipeline could not find or compare | work the review queues in MouseReach; for a label mismatch or a hand-score hold, read the line in the cohort's FIX THESE file |
| **recording failed** | the recording itself failed (empty, a fraction of a second long, unreadable) and was set aside; there will never be a video | nothing can be fixed; see "A recording that failed" below |
| **NOT ON THE SERVER YET** | scored in the sheet, but no video of it is anywhere in the reaching pipeline | look on the recording PC (see "Sessions scored by hand that have no video" below) |

When a session is visible in two places at once -- a filed video that was sent
back to a review queue keeps its old results in Analyzed until the new run
replaces them -- it is counted where a person has to act: a failed recording
first, then a review queue, then filed. A hold outside the queues (label
mismatch, hand scores) counts only for a video that is not filed yet.

### "Not on the server yet": red, recent, or acknowledged

Not every missing video is an alarm. Each one is one of three kinds, and only
the first is shown in red:

- **Overdue (red).** More than the grace period old (3 days unless the setting
  `missing_video_grace_days` says otherwise; counted to when the pipeline view
  was taken, not to today), in a cohort whose tracking sheet is live, and not
  acknowledged. These are exactly the sessions `mousedb missing-videos` lists.
  Go and find the recording.
- **Recent.** Inside the grace period. A recording is named and sent some days
  after it is made, so this is normal.
- **Acknowledged as lost.** A person looked, the recording is gone for good,
  and they recorded that with `mousedb missing-videos --acknowledge`. It stays
  counted -- the session was recorded and will never be filed -- so that
  cohort's done stays below 100%, and the reason is on record.

A cohort whose workbook is frozen (a finished project imported from a fixed
workbook, not from the live sheets folder) is counted the same way, but its
missing sessions are never red: nobody is going to record those animals again.

When the pipeline holds the same animal and day under a tray the sheet does
NOT have, the session is still "not on the server" (nothing carries its own
name) but it is counted as **maybe under another name**: one of the two names
-- the sheet's or the video's -- is probably wrong.

### The other way round: videos with no sheet row

A video the pipeline holds that matches no session in any sheet is **listed
separately** ("No sheet row" on the tab; `unmatched_videos` in
`mousedb sheet-progress --json`), with its pipeline stage. It is never
dropped and never counted in done. A name the cohort cannot be read from is
listed under `?`. Such a video usually means a typo in the video's name or a
missing row in the sheet.

### Pillar trays on their own

Easy and flat trays are scored in the sheet too, but the pipeline processes
them last on purpose. So the done figure is also given for pillar trays alone
(`pillar_done_pct`, "Done, pillar trays" on the weekly page): a cohort can be
nearly done on pillar trays while its overall figure is low.

### Where you see it

- **MouseReach Video Processing Progress tab**: the bold line above the table ("PROGRESS AGAINST
  THE TRACKING SHEETS"), with "NOT ON THE SERVER YET" on a red background when
  any session is overdue; and per cohort the columns *Done (filed /
  sheet-scored)*, *Not on server yet* (red when any is overdue; the number in
  brackets is how many), *Processing / held / failed* and *No sheet row*.
- **The weekly page**: its first section, with every overdue recording listed
  by cohort, day and tray.
- **From a terminal** (MouseDB environment active):

      mousedb sheet-progress                 # one line per cohort
      mousedb sheet-progress --list          # also every overdue recording, by cohort
      mousedb sheet-progress --cohort <cohort> --list
      mousedb sheet-progress --json          # everything, one row per session

  It reads only; it writes nothing.

### What it reads, and when the numbers change

It reads the hand-scores file (`<mousedb_root>/exports/Behavior/Hand_Scores/`,
rewritten after every sheet import), the pipeline view
(`<mousedb_root>/logs/pipeline_census.json`, retaken every night), the
acknowledgements file, and the reaching pipeline's own attention lists (for
the holds outside the queues). So the figure moves when the sheets are
imported (nightly) and when the pipeline view is retaken (nightly, or the
tab's **Refresh pipeline view** button). If either file cannot be read, no
done percentage is shown at all -- never one counted against the pipeline
alone -- and the tab says why.

If the pipeline that took the pipeline view is too old to report failed
recordings, the tab says so under the line; failed recordings are then
counted where the pipeline view puts them (usually "not on the server yet")
until the pipeline is updated.

## The longer answer: the "MouseReach Video Processing Progress" tab

Open the mousedb GUI (double-click **MouseDB** on the desktop, or Start menu ->
**MouseDB** -> **MouseDB**; from a terminal: MouseDB environment active, then
`mousedb-entry`) and find **9. MouseReach Video Processing Progress**. One row per cohort:

| column | meaning |
|---|---|
| Animals | animals the database knows for the cohort. `[N from video only]` means N of them were created from a video before the tracking sheet named them -- import the sheet to fill in their details. Once the sheet names such an animal, the next import replaces its "created from video" note and it stops being counted here |
| Sheet | the tracking sheet's import status (worked in the **Tracking Sheets** tab): Up to date / Sheet edited since last import / Never imported / LAST IMPORT FAILED / NOT SYNCED (red: the last import found the sheet looking broken and removed and changed nothing -- see below) |
| Sessions scored | hand-scored animal-days in the database |
| Done (filed / sheet-scored) | sessions scored in the cohort's sheet that are filed, out of all of them, with the percentage (see "How far along are we?" above) |
| Not on server yet | sessions scored in the sheet with no video anywhere in the pipeline; red when any is overdue, with how many in brackets |
| Processing / held / failed | the other three states: in processing, held for review, recording failed |
| No sheet row | videos the pipeline holds that no sheet row matches |
| Videos in DB | videos whose reaches are in the database (i.e. are in `reach_data.csv`) |
| In review (triage / deep) | videos waiting for a person in MouseReach's review queues. **Their data is not final until they are reviewed and released.** They are worked in MouseReach (its Review Queues tab) |
| Outcomes algo / human | pellet outcomes resting on the algorithm alone vs confirmed or corrected by a person |
| Reaches | rows the cohort contributes to `reach_data.csv` |

Below the table: the export folder, when it was last written, whether it is
complete for an ODC upload, the row count of each file, one line per saved
limited export (when it was last made, or `[FAIL]` and why), and any problems.

Buttons: **Refresh** re-reads everything. **Open exports folder** opens the
folder above in Explorer. **Refresh exports now** rewrites `reach_data.csv`,
`manual_scores.csv`, every cohort's four reach and session-analysis files and
each project's combined files of those kinds, immediately from the latest
snapshot. It takes as long as the scheduled run's reach part: hours for a
large database. The per-cohort ODC session files, and their combined file, refresh
only on the scheduled run, which is the only time the database may be read
safely. **Make a limited export...** opens the window described in
"Limited exports: just part of the data" above; the tab re-reads itself when
that window is closed.

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
  names that folder; into a folder of your own it does not wait.
  `mousedb export-example` does the same for its example folder (by default
  `exports/Behavior/All_Cohorts/Example`, inside the live folder): it reads and builds
  first, then waits, holds the lock only for the few seconds it writes, and
  into a folder of your own (`--out-dir`) it does not wait. If the other
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
    mousedb-weekly-report                 # into <mousedb_root>/reports/MouseReach_Video_Processing_Progress/
    mousedb-weekly-report --out <folder>  # somewhere else

Each run writes two files in that folder:

- `MouseReach video processing progress YYYY-MM-DD.html` -- that run's page, kept.
- `MouseReach video processing progress - LATEST.html` -- the same page, replaced every run.

The page shows, from the tracking sheets, the pipeline census and the database
snapshot:

0. **How far along, counted against the tracking sheets** (the page's first
   section): filed of every session scored in a sheet, per cohort, with the
   other four states as counts and every overdue "not on the server yet"
   recording listed by cohort, day and tray. See "How far along are we?" above.
1. **Where the pipeline's sessions are**, per project and per cohort (no done
   percentage here -- the pipeline's own list cannot see what never reached
   it): done, waiting for triage,
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
folders are reorganized (for example each project's folder put inside its
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
computer restarting -- the next run (the next night's, or one you start by
hand) skips everything already imported and does only the rest. (Task
Scheduler's restart setting does not re-run a task stopped at its time limit
or one that exited with an error -- INSTALL.md, "Nothing re-runs a job that
gave up".) At
most the last few minutes of work are done again, which is safe: importing a
video again replaces its rows, it never doubles them. You do not need to do
anything; the next run's first line says how many files are still to
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
  began re-labelling it. An older plain list of names is read as before.) Each change to the list takes
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
  `<video>_video_pellets.csv` and `<video>_video_sessions.csv`, then deleted. Before any row is deleted the
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
  skipped (`N filed videos skipped: a newer run is in review`). Writing them would put the pellet answers under review back into the exports
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
- on the **MouseReach Video Processing Progress** tab, among the problems at the top, with that
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
| measurements and other data files, plus `registry.json` (the provenance) | `<mousedb_root>/exports/Tissue/<analysis>/...` (or under the folder name the lab settings give that analysis; see "Renaming an output folder" in INSTALL.md) |
| figures | `<mousedb_root>/figures/<analysis>/...` |
| the analysis's registration log | `<mousedb_root>/logs/<analysis>.log` |
| one summary of every analysis | `<mousedb_root>/exports/ANALYSES_MANIFEST.json` |

Modification times are kept, and so is each file's path inside its analysis:
a path recorded in `registry.json` as
`exports/<analysis>/<sample>/measurements.csv` is
`<mousedb_root>/exports/Tissue/<analysis>/<sample>/measurements.csv` here
(the tissue analyses sit together in `Tissue/`, apart from the behavior
exports in `Behavior/`); a figure or log path resolves under `<mousedb_root>`
exactly as it does in MouseBrain's registry. Only new
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
| the folder `exports/<analysis>` is there but has no `registry.json`, and the mirror holds nothing of that analysis (neither the ledger nor the mirror's own folders) | nothing to do, and not an error: a line `[note] <analysis>: exports/<analysis> has no registry.json and nothing of it is in the mirror ...`. MouseBrain makes this folder as soon as anything opens that analysis's registry, before anything is registered in it -- a new analysis, or a mistyped name given to `mousebrain-registry --name ...`. Once MouseBrain registers something in it, the next run mirrors it. A folder made by a mistyped name can be left alone; it costs nothing here. |
| the folder `exports/<name>` is there but has no `registry.json`, and files ARE kept under that name on the mousedb side, but nothing marks them as the mirror of an analysis (see "How the mirror tells an analysis from another folder" below) | nothing to do, and not an error: a line `[note] <name>: exports/<name> has no registry.json, and the N file(s) kept under that name on the mousedb side are not copies of anything in it ...; they are left as they are`. This is what happens when MouseBrain's registry was opened with a name that mousedb already uses for a folder of its own. Those files are not touched, and the run does not suggest `--confirm-withdrawn` for them (that option refuses the name too). |
| the folder `exports/<analysis>` is there but has no `registry.json`, and the mirror already holds files of that analysis | archives nothing of it, copies nothing of it: `[FAIL] <analysis>: exports/<analysis> has no registry.json ...`, and the MouseReach Video Processing Progress tab shows the analysis's line ending with that reason. The run looks in the mirror's own folders as well as the ledger, so this holds after `--set-aside-ledger` or on a machine whose ledger is new too ("holds files of that analysis" is judged by the three marks listed under "How the mirror tells an analysis from another folder" below). If the mirror's folders of it cannot be opened, the line says so instead and nothing is archived. Usually MouseBrain is part-way through rebuilding the registry, or the share answered short; run again later. If MouseBrain set a damaged `registry.json` aside (MouseBrain's `logs` folder then holds `<analysis>.registry.json.unreadable-<time>`) and no new one has been written yet, this job keeps failing every night until the next registration in that analysis writes a new `registry.json` or MouseBrain's registry is repaired (see MouseBrain's registry guide). If the analysis really was withdrawn, see "When MouseBrain really withdrew an analysis" below. |
| a folder of the analysis cannot be opened (a network error, no permission) | archives nothing of it: `[FAIL] <analysis>: its files could not be listed ...`. The other analyses are mirrored as usual. |
| a file is to be archived, but the mirror's own copy of it cannot be looked at (a network error, no permission) | leaves that copy where it is and keeps it in the ledger: `[FAIL] archive <path>: ...`. The next run that can see the copy archives it. |
| an analysis folder is missing from the listing, but a second look cannot confirm it is gone (the share answered short, or the second listing failed) | archives nothing of it: `[FAIL] 1 analysis folder(s) missing from the listing of ... could not be confirmed gone ...`, naming the analyses. The MouseReach Video Processing Progress tab shows that analysis's line ending `[FAIL] <analysis>: its folder was missing from the listing of exports/ but could not be confirmed gone ...`. Check that the share is reachable and run again; if the analysis really was withdrawn, the next run that can confirm it archives its mirror. |
| the ledger holds a line that names no analysis | keeps it, archives nothing for it: `[FAIL] 1 ledger entry names no analysis ...`. Such a line does not belong in the mirror's ledger; run `mousedb import-analyses --set-aside-ledger` (see "The mirror's ledger" below). |
| no analysis at all is listed, yet the mirror holds files | archives nothing: `[FAIL] no analysis registry was listed ...`. The MouseReach Video Processing Progress tab still shows one line per analysis the mirror holds, ending `[FAIL] <analysis>: not listed this run (no analysis registry was listed at all) ...`, and a `[!] analysis mirror: no analysis registry was listed ...` line with the whole message. Run `mousedb config --show` and check that `mousebrain_registry_root` points at MouseBrain's registry folder (when it is not set, the mirror uses the `Registry` folder inside `mousebrain_pipeline_root`, so check that one instead) and that the share is reachable. If every analysis really was withdrawn, see "When MouseBrain really withdrew an analysis" below. |

`ANALYSES_MANIFEST.json` has one row per analysis: how many samples are
registered, how many are **current**, how many are **stale vs approved**
(current, but produced with a method other than the one the analysis now
approves -- re-run them before using them), how many were **invalidated**,
and when the mirror was taken. The MouseReach Video Processing Progress tab shows the same
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
`mousedb import-analyses` (or wait for the nightly run). The recorded date
and `--refresh-approved` need a MouseBrain whose analysis registry keeps
`approved_method_recorded_at`; with an older MouseBrain the option does not
exist and the date shows as unknown (below) -- update MouseBrain first. Other things you may
see in that place:

- `approved method as recorded at an unknown date -- registry written
  before the date was kept` -- the registry was last written by an older MouseBrain
  that did not keep the date. Treat the count with the same caution;
  refreshing it as above adds the date.
- `no approved method` in place of the stale count -- the analysis is one no
  method approval judges (MouseBrain records it with no approved method, for
  example an orientation check). There is nothing for its outputs to be stale
  against, so none is counted as stale, and no date is shown.
- `[!] N damaged manifest(s) set aside by the tool ...` at the end of the
  counts -- MouseBrain found this analysis's record damaged, set the damaged
  copy aside for a person and started a fresh one. Until a person repairs it,
  the samples recorded only in the damaged copy are not counted, so the
  numbers are lower than they should be. Repair it on the MouseBrain side (its
  registry documentation says how); the note goes away at the next import
  after that.
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
- `[!] analysis mirror: ...` (with the other `[!]` lines at the bottom of
  the tab) -- a problem of the whole run rather than of one analysis, such
  as no analysis registry listed at all. It is the same text as the job's
  `[FAIL]` line, and it goes away on the first run that does not have it.
- `[!] analysis mirror: the last run stopped before mirroring anything
  (<date time>): <reason> -- the analysis lines shown are from the last run
  that finished` -- the last run stopped before it copied or archived
  anything, so it wrote no new `ANALYSES_MANIFEST.json`, and every analysis
  line on the tab is still the one from the last run that finished. The
  reason is the job's own `[FAIL]` line: a ledger it cannot read (see "The
  mirror's ledger" below), another run that held the lock for too long (see
  "Only one mirror runs at a time" below), or a registry folder it could not
  reach (check `mousedb config --show` and the share). The line stays until
  a run finishes -- the next nightly run, the **Update the database now**
  button, or `mousedb import-analyses` in a terminal -- and then goes by
  itself. If it says instead `the last run stopped part-way`, the run had
  started copying when it stopped (a write that failed, for example): some
  files may have been copied or archived, but the ledger and the manifest
  may not have been rewritten; run it again. The stop is kept in
  `<mousedb_root>/logs/analysis_import_last_failure.json`; do not edit or
  delete it by hand (only this job writes it, and a run that finishes
  removes it). A `--dry-run` that stops records nothing. Only the run that
  holds the mirror's lock writes or removes it (a run that gave up waiting
  for the lock writes it too), so a run that finishes can never wipe out
  the stop of a run that started after it.
- `[!] analysis mirror: the record of its last stopped run cannot be read:
  <error>` -- the tab could not look at that file (the share did not answer,
  or the file is damaged), so it cannot tell you whether the last run
  stopped. It never treats that as "no run stopped". Press **Refresh**; if
  the line stays, check the share, then run `mousedb import-analyses` (or
  press **Update the database now**): a run that finishes removes the file.

`mousedb import-analyses --dry-run` says what would be copied or archived
without writing anything (no files, no ledger, no manifest).

**When MouseBrain really withdrew an analysis.** The mirror holds files back
whenever it cannot be sure they were withdrawn (the table above). When you
ARE sure -- the analysis was withdrawn in MouseBrain on purpose -- tell the
mirror so; never move or delete files in the mirror's folders by hand (only
this job writes there, and the archive keeps the record of what was moved
and when). In a terminal:

    conda activate <your MouseDB environment>
    mousedb import-analyses --dry-run --confirm-withdrawn <analysis>
    mousedb import-analyses --confirm-withdrawn <analysis>

The first command only counts (look at "to archive" in its output); the
second does it. Give `--confirm-withdrawn <analysis>` once for each analysis,
for example `--confirm-withdrawn <analysis_1> --confirm-withdrawn
<analysis_2>`. It archives every mirrored file of that analysis that
MouseBrain no longer has -- moved to
`<mousedb_root>/_archived/analyses/<date_time>/`, never deleted -- including
files the ledger no longer knows of (after `--set-aside-ledger`, below).
Which files that is depends on what MouseBrain still has of the analysis:

- the analysis is still registered (its `registry.json` is there): only the
  files MouseBrain has dropped are archived; everything it still has stays
  in the mirror;
- the folder `exports/<analysis>` is there but has no `registry.json`:
  EVERYTHING the mirror holds of that analysis is archived, even files still
  in MouseBrain's folders. Without its `registry.json` the folder is no
  analysis the mirror can copy, and keeping part of it would make every
  later run fail on it again. Nothing in MouseBrain's folders is touched; if
  MouseBrain registers the analysis again, the next run mirrors it afresh;
- the whole analysis folder is gone: everything of it is archived.

**How the mirror tells an analysis from another folder.** The three places
the mirror writes to -- `<mousedb_root>/exports/Tissue/<name>/`,
`<mousedb_root>/figures/<name>/` and `<mousedb_root>/logs/<name>.log` -- can
also hold things that are NOT mirrored analyses: a folder someone keeps beside
the analyses, and the log files of mousedb's other jobs. So a name is treated as an analysis only when
at least one of these is true:

1. the ledger has lines for it (this job copied files of it and recorded
   them);
2. MouseBrain's registry has a folder `exports/<name>` right now, and either
   its `registry.json` is there, or the mirror holds a copy of at least one
   file of it (same place, same size, same modification time -- which is how
   this job's own copies look);
3. the mirror holds `exports/Tissue/<name>/registry.json` -- the copy of MouseBrain's
   own record, which only a mirrored analysis has.

`--confirm-withdrawn <name>` archives nothing for a name that meets none of
the three, however many files sit under it, and the nightly run never tells
you to use the option for such a name.

What it does NOT do:

- change anything in MouseBrain's folders: it only moves the mirror's
  copies into the archive;
- archive when the source could not be read: a folder that could not be
  listed, or a folder missing from the listing that a second look cannot
  confirm is gone, still keeps its mirror and fails the run -- check the
  share and run again;
- archive anything of the analysis when the MIRROR's own folders of it
  cannot be listed: `[FAIL] --confirm-withdrawn <analysis>: the mirror's
  folders of it could not be listed ...; nothing of it was archived`, and the
  analysis's line on the MouseReach Video Processing Progress tab ends with that reason. Nothing
  of that analysis is moved -- not even files the run would archive without
  the option -- because the run cannot tell what the mirror holds of it.
  Check the share and run the same command again;
- accept a name that is not an analysis: `[FAIL] --confirm-withdrawn
  <name>: the mirror holds no analysis by that name ... -- check the name`,
  the command ends with exit code 1, and nothing is archived or moved. That
  covers a mistyped name (no file under the mirror's `exports/Tissue/<name>/` or
  `figures/<name>/`, no `logs/<name>.log`, no line in the ledger) AND a name
  that has files under it but meets none of the three marks above -- for
  example a folder kept beside the analyses, or the log of another job.
  The message then also says how many files sit under the name and that
  they were left where they are. A path (anything with `/`, `\` or `:`) is
  refused the same way as a wrong name.
  Every analysis a run reports as held with "run `mousedb import-analyses
  --confirm-withdrawn <analysis>`" meets one of the three marks, so it can
  be settled with exactly that command -- also when the mirror's own copy of
  its `registry.json` is gone and the ledger is new, as long as MouseBrain's
  folder of it still holds a file the mirror has a copy of.
  One case stays refused on purpose: the ledger is new (or was set aside),
  the mirror has no `registry.json` copy of the analysis, AND MouseBrain's
  folder `exports/<analysis>` is gone altogether or holds no file the mirror
  has a copy of. Nothing then tells those mirrored files from files kept for
  another purpose, so the command leaves them where they are (the nightly
  run does not report them either). They do no harm there; do not move them
  by hand;
- protect a folder of mousedb's own that carries the SAME name as a real
  analysis in MouseBrain's registry: the mirror would copy that analysis
  into the folder, the ledger would then list the name, and the name would
  count as an analysis. Do not give an analysis the name of a folder that
  already exists under `<mousedb_root>/exports/` or `figures/`.

The option is for a run a person starts on purpose; the nightly task never
uses it.

**Only one mirror runs at a time.** The nightly task and the **Update the database now**
button on the MouseReach Video Processing Progress tab (`mousedb update`) both start
`mousedb import-analyses`. When one is already
running, the second waits for it (its output says what it is waiting for),
then runs and normally finds nothing left to copy. If it waits longer than
the lock's usual limit (the same rules as the database jobs' lock: it keeps
waiting while the first run is still getting somewhere; 40 minutes for the
nightly task and a terminal, 25 for the button, `MOUSEDB_LOCK_WAIT_MINUTES`
overrides both), it stops with `[FAIL] import-analyses not run: ...`,
changes nothing, and the job fails; run it again later. The Where Is My
Data tab then shows `[!] analysis mirror: the last run stopped before
mirroring anything ...` with that message until a run finishes (see above).
The run that held the lock is not affected and finishes normally; when it
finishes before the waiting one gives up, there is nothing to show. The button also stops
each of its steps after one hour: if `MOUSEDB_LOCK_WAIT_MINUTES` is set above
60, a step still waiting then is stopped by the button instead, and the
button shows `step exceeded its limit of 3600 s and was stopped` in place of
the `[FAIL]` line. Either way nothing was changed; press the button again
later. The lock is the file
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
  the job fails (exit code 1). The MouseReach Video Processing Progress tab shows the same
  message as `[!] analysis mirror: the last run stopped before mirroring
  anything ...` until a run finishes, so the stop is seen without opening
  the task log; every nightly run fails the same way until the ledger is
  dealt with. If the network share was only briefly
  unavailable, run it again. Otherwise run (in the MouseDB environment, as
  above) `mousedb import-analyses --set-aside-ledger`: it keeps the damaged
  file beside the new one as `analysis_imports.json.set-aside-<date_time>`,
  starts a new ledger, re-checks every file, and copies nothing that is
  already there. Do not rename or delete the file by hand. A ledger that
  reads correctly is left alone by this option (the output says so). The
  same option clears a ledger holding lines that name no analysis.
  The new ledger does not know of files MouseBrain withdrew BEFORE it was
  started, so the nightly run leaves those in the mirror. To archive them,
  run `mousedb import-analyses --confirm-withdrawn <analysis>` for each
  analysis concerned (see "When MouseBrain really withdrew an analysis"
  above): it also looks at what the mirror itself holds, not only the ledger.
- The ledger and `ANALYSES_MANIFEST.json` are written whole or not at all (a
  temporary file swapped in), so a run that is stopped part-way -- by the
  scheduler's time limit, or by End in Task Scheduler -- leaves the previous
  version of each, never half a file.

## How brain counts get here

MouseBrain's last step writes one counts file per brain into
`3D_Cleared/2_Data_Summary` of its pipeline folder (`<brain>_counts.csv`: the
cells in every atlas region, in total and per side), plus one file for all
brains with the 25 functional groups (`elife_counts.csv`) and its record of
every calibration run (`calibration_runs.csv`). `mousedb import-brains --all`
(scheduled daily where set up, and step 4 of the Update button) reads that
folder. Nothing in it is changed.

**The database holds what each brain's file says now.** For every counts
file the import compares the file with what the database holds for that
brain:

| what it finds | what it does | what the line for that brain says |
|---|---|---|
| a brain the database has no counts for | adds them | `region counts added`, `eLife groups added` |
| the same counts as before | nothing | `unchanged` |
| different counts (the brain was recounted, for example after its registration was corrected) | **replaces** that brain's counts -- region counts and groups together, in one step, so nobody ever sees half an old count and half a new one | `REPLACED (the file changed)` |

(For the groups only the numbers are compared. The list of atlas regions in
each group is filled when the groups are computed with MouseBrain's own
mapping and empty when they are read from `elife_counts.csv`; the same
numbers from the other source are `unchanged`, and the stored list is kept.)

Rules that keep a replacement safe:

- A counts file that is gone does not remove anything: the brain keeps the
  counts it has. It is REPORTED instead: see "Brains whose counts file has
  gone" below.
- A file changed in the last two minutes may still be being written. That
  brain is left alone (its stored counts kept) and the run ends with a
  `[FAIL]` line naming it; the next run takes it. The same happens when the
  file cannot be read or holds no region rows.
  The two-minute rule also covers `elife_counts.csv`, which every brain
  shares: if THAT file changed in the last two minutes, every brain waits,
  and the run ends with a `[FAIL]` line naming all of them. Nothing is lost:
  the next scheduled run, or running the command again a few minutes later,
  imports them all. (Task Scheduler's restart setting does not re-run a job
  that exited 1.)
- A counts file that looks cut off is refused the same way (stored counts
  kept, `[FAIL]` naming the brain, the words `looks cut off` in its line):
  a file whose last line has no line break at its end (its last row was cut
  off, perhaps in the middle of a number), or a row that names a region but
  has no readable number in `cell_count`, `left_count` or `right_count`
  (MouseBrain writes all three for every region, zeros included). Replacing
  from such a file would keep only the rows that happened to be complete.
  To fix it, run MouseBrain's step 6 (count regions) for that brain again;
  the next import takes the new file.
- A counts file cut off exactly between two rows ends with a line break and
  every row it still has is complete, so the checks above cannot see it.
  For that the import compares the file with MouseBrain's own
  `region_counts.csv` (one row per brain, which step 6 writes right AFTER
  the counts file, from the same count). When `region_counts.csv` is newer
  than the counts file and gives the brain a different total of cells, or
  other regions, the file lost rows: it is refused the same way (stored
  counts kept, `[FAIL]` naming the brain and the regions missing, `looks
  cut off` in its line). Fix: run step 6 for that brain again.
  When `region_counts.csv` is OLDER than the counts file -- step 6 stopped
  after writing the counts file and before writing `region_counts.csv` -- it
  holds the previous count and proves nothing, so a counts file cut off
  between two rows in that same moment is NOT caught. If step 6 ever stops
  part-way for a brain, run it again for that brain.
  The other way round, a `region_counts.csv` rewritten later for ANOTHER
  brain, while this brain's row is still from its earlier count, holds back
  a good recount of this brain; running step 6 again for the brain clears
  that too.
  This check only refuses a file that would CHANGE what the database holds
  (a new brain, or a recount). When the database already holds exactly the
  file's counts, nothing would be written, so the brain's line says
  `unchanged [OK]` and the disagreement is printed under it as a `[!]` line
  instead -- an unchanged brain never fails the nightly job. If you see that
  `[!]` line, run step 6 for the brain again so both files agree.
- **Unknown acronym.** A counts file names each region by its atlas
  acronym (`RN`, ...). mousedb turns each acronym into the atlas's region
  number with a lookup: the BrainGlobe atlas where `brainglobe-atlasapi` is
  installed in the MouseDB environment, otherwise a copy of the atlas's
  region list built into mousedb. If the file has a region the lookup does
  not know, that region cannot be stored, and replacing the brain's counts
  would DELETE its cells from the database (while the eLife groups and
  MouseBrain's `region_counts.csv` still count them). So such a file is
  refused: the brain keeps the counts it has, and its line says `[FAIL]`,
  names the acronyms and which lookup did not know them, and the run ends
  with a `[FAIL]` line. It means the file was made with another atlas, or
  another version of it, than the lookup mousedb has now (for example the
  brain was counted on a machine with a newer atlas). Nothing in the file is
  wrong; a person who looks after the database has to make mousedb's lookup
  know the acronyms (install the same atlas in the MouseDB environment, or
  update mousedb's built-in list), then run the import again. As with the
  check above, when the database already holds exactly the file's other
  rows, nothing would change: the brain's line says `unchanged [OK]` and the
  unknown acronyms are printed under it as a `[!]` line.
  (`mousedb import-brains --csv` behaves the same. Only the older add-only
  path, `BrainGlobeImporter.import_region_counts`, which never deletes,
  still stores the other regions and prints `Unknown acronym: X`.)
- Two counts files that name the same brain (for example an old
  `<brain>_counts.csv` left beside step 6's `<mouse>_<brain>_counts.csv`):
  neither is imported, the brain keeps the counts it has, and the run ends
  with a `[FAIL]` line naming every one of the files. Only a person can say
  which one is the brain's current count: move the other aside (out of
  `2_Data_Summary`) and run the import again. (Importing both would let
  whichever file sorts last replace the counts on every run, however old.)
- When a brain's groups cannot be had, it is reported with a `[FAIL]` line
  naming the brain. That happens when its row is missing from
  `elife_counts.csv`; when `elife_counts.csv` looks cut off (MouseBrain
  rewrites it in place: it does not end with a line break, or the brain's
  row has fewer or more cells than the file has columns); and when
  `elife_counts.csv` was last written BEFORE
  the brain's counts file: MouseBrain writes the counts file first and the
  groups a few seconds after, so a row in an older `elife_counts.csv` is
  from an earlier count (MouseBrain's step 6 stopped between the two). The
  fix is the same: run step 6 for that brain again.
  If the brain's region counts were replaced in that run, its OLD groups are
  removed -- they were computed from the old count and would disagree with
  the new one. They come back on the first run that can read the new ones.
  If the region counts did not change, stored groups are left as they are.
  (When mousebrain is installed where the import runs, the groups are
  computed from the counts file itself and this check is not needed.)
- What the import cannot see: an `elife_counts.csv` that still holds a
  brain's row from an earlier count but was rewritten AFTER the brain's
  counts file (because step 6 stopped for this brain and then counted
  another brain before the import ran). Nothing in the file tells how old
  one brain's row is. If step 6 ever stops part-way for a brain, run it
  again for that brain before counting others.
- A brain that is refused (its cohort is not in the database yet, its file
  cannot be read ...) writes nothing at all, not even its animal or its
  brain row.
- Warnings are printed under the brain's line, as lines starting `[!]` (at
  most ten per brain, then a count of the rest). An unknown acronym printed
  there belongs to a brain whose stored counts did not change (see
  "Unknown acronym" above): region X is not among them. `group ... is not a known eLife group name` means a group in
  `elife_counts.csv` was stored under the name it has there. Neither fails
  the run.

**Brains whose counts file has gone are reported, never removed.** The
import reads only the counts files that are in MouseBrain's `2_Data_Summary`
folder. When a brain's file leaves that folder -- the brain was renamed in
MouseBrain (its cohort or subject corrected in the folder name), or its
counts were withdrawn -- the counts already in the database would otherwise
stay there, marked final, under the old name, and in every brain export; a
renamed brain would be there twice, once under each name. So every
`import-brains --all` that finds at least one counts file compares the
brains with final counts in the database with the files it found, and lists
every brain that has counts but no file:

- in its output, before its summary:
  `[!] N brain(s) have final counts in the database but no counts file in <folder> -- nothing was taken out; a person should look (<first 20 names>)`;
- in `<mousedb_root>/logs/brain_counts_without_file.json` (every name, the
  folder it looked in, and when it checked, `checked_at`). A `--dry-run`
  prints the line but does not write the file, and a run that found no
  counts file at all does not check (an empty folder is treated as a broken
  listing, never as "every brain is gone"), so the list can be older than
  the last import; its date tells you how old;
- on the **MouseReach Video Processing Progress** tab, among the problems at the top, with that
  date (`found by the brain import of <checked_at>`).

Nothing is deleted, and the import's result does not change: a listing that
missed a file must never cost counts. What to do: find out where the file
went. If it was moved or renamed by mistake, put it back -- the next import
clears the line. If the brain really was renamed or withdrawn, the old
brain's rows have to be taken out of the database by whoever looks after it;
there is deliberately no button or command for that here. A brain imported
once with `import-brains --csv` from a file kept somewhere else is listed
too, because its file is not in the summary folder.

**Calibration runs follow the tracker too.** A run the tracker names by its
own id (`exp_id`) that is already in the database is brought in line with
the tracker on every run: its status (a run first seen as `started` becomes
`completed` or `failed`), cells found, duration, whether it is marked best,
its detection settings and its date. The `Calibration runs` line counts new
and updated runs separately. A row with no `exp_id` gets a made-up id when it
is first imported and is never changed afterwards, because the same made-up
id need not name the same run the next time.

Only what the file actually has is brought in line. A field whose column is
not in `calibration_runs.csv` at all (an older tracker wrote the file, or
someone edited it by hand and removed a column) keeps the value the
database holds; an EMPTY cell in a column that is there does count (the
tracker empties `marked_best` when another run of the brain becomes best).
And a `calibration_runs.csv` that has no `status` column, no `marked_best`
(or older `is_best`) column, or no `brain` (or `brain_id`) column is refused
whole: the `Calibration runs` block says `[FAIL]` and names the missing
column, no run is added or changed, and the job ends with a `[FAIL]` line
(the region counts are still imported). Without that, one such file would
mark every stored run "not best" and "unknown" in a single night. To fix
it, let the current MouseBrain write the file again (any new calibration
run does), or put the column back; never run an older copy of MouseBrain
against the live file.

The first run after this change updates nearly every stored calibration run
once: older imports read column names the tracker does not write, so stored
runs are missing their date, duration and detection settings, and a run
marked best in the tracker is not best here. Run `mousedb import-brains --all
--dry-run` first to see how many: the `Calibration runs` block prints
`calibration_runs_updated: <n>` (and `calibration_runs: <n>` for runs it
would add). The values written are the tracker's own, and a second run
changes nothing. These rows are only ever written by this import, so no
entry a person made is overwritten.

`--dry-run` shows the same lines (`REPLACED` included) without writing
anything. `--update` replaces every brain's counts even when its file has
not changed (only needed if rows were changed in the database by hand).

The first run of this version replaces the counts of every brain whose file
differs from what the database holds -- every brain recounted since it was
first imported. Run `mousedb import-brains --all --dry-run` first: it lists
every brain that would be replaced, so the change can be checked brain by
brain before it happens.

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

## A video whose results match another tray (a label mismatch)

Sometimes a video's pellet results match the hand scores of ANOTHER tray of the
same animal on the same day almost exactly, and its own tray's scores much less
(for example: the P3 video matches the sheet's P4 row on 19 of 19 pellets and its
own P3 row on 7 of 19). Then either the recording was named with the wrong tray,
or the sheet has those two trays' rows the other way round. The reaching pipeline
holds such a video. It is NOT sent to a review queue: nobody answering pellets
there could settle which tray it is.

**Where you see it.** In red on the MouseReach Video Processing Progress tab, and as a line in the
cohort's "!! <cohort> - FIX THESE.xlsx" file beside its sheet. The line names the
video, both trays, how well the results matched each, and -- when the other tray's
video is analyzed too -- whether that one matches back (which is what a swap
looks like). mousedb writes this line itself from the pipeline's list
(`<pipeline>/attention/`), so it reads the same whichever version of the pipeline
wrote the list; mousedb never imports the pipeline.

**What to do.** Decide whether the recording's NAME or the SHEET is wrong.

- The sheet is wrong: a sheet error is kept as it is and recorded. The sheet is
  never edited -- how often the bench gets a tray wrong is itself something the
  lab measures. Record it on the processing server with
  `mousereach-label-mismatch --sheet-error <video> --by <your name>`; the video is
  filed with the disagreement and your decision kept with it.
- The name is wrong: record it with
  `mousereach-label-mismatch --name-wrong <video> --as <right name> --by <your
  name>`, and ask whoever maintains the pipeline to rename the recording the
  documented way (the original and a record are kept in
  `Archive/manual_corrections`). The command only records the decision; the
  video checks itself again once renamed.
- Neither is wrong (another tray's row just happened to fit):
  `mousereach-label-mismatch --labels-right <video> --by <your name>`; the video
  is then compared with its own row as usual.

Every decision is kept in `review_records/label_decisions.jsonl` on the storage
drive (who, when, which video, which rows), so the error rate can be counted. The
line disappears from the tab and the FIX THESE file once the pipeline no longer
holds the video.

**Sessions whose comparison is suspended.** The lab can list sessions whose
hand scores cannot be trusted to belong to their video (while nobody yet knows
which way round a swap is) in the pipeline's lab settings
(`"suspended_sessions"`). Those are not compared with their hand scores at all and
are never held as a label mismatch; `mousereach-watch-status` lists them.

## Sessions scored by hand that have no video

Every tray scored at the bench is written in the tracking sheet the same day,
so a hand-scored session proves a recording was made. If that recording never
reaches the pipeline, nothing else would notice: the pipeline only knows the
videos it is given. So mousedb checks, for every hand-scored session (one
animal, one day, one tray), that its video is somewhere the pipeline knows
about -- a recording waiting to be cut, a video being posed or analyzed, one in
a review queue or quarantine, or a filed one in Analyzed.

**What you see.** A red line per cohort at the top of the MouseReach Video Processing Progress tab:

    PROJA_03: 256 hand-scored session(s) in 24 recording(s) (<first day> to <last day>) have no video anywhere in the reaching pipeline. ...

and, in that cohort's `!! <cohort> - FIX THESE.xlsx` beside its sheet, one
line per missing recording (day + tray), naming its animals and saying what
to do: look in the recording PC's OBS folder for that day's recording (named
like `<YYYYMMDD>_<animal>,<animal>,..._<tray>.mkv` once a person has named it,
or by its date and time if not yet); if it is there, name it and send it to
the pipeline's intake folder (`<MouseReach pipeline root>\Unanalyzed\Multi-Animal`)
the way the day's recordings are sent. When the pipeline holds the same animal
and day under ANOTHER tray, the line says so: the tray in the sheet or in the
video's name is then wrong, and the recording is probably there under the other
name -- correct whichever is wrong.

The full list, one row per session, is
`<mousedb_root>\exports\Behavior\Sessions_Without_Video\missing_session_videos.csv` (columns:
cohort, animal, day, tray, session name, `missing`, `recording_failed` or
`acknowledged`, days since the session, other trays the pipeline holds for
that animal and day, the recording name to look for, what to do, who
acknowledged it, when and why, and -- for a recording that failed -- the
reason, the recording and whether it was scored; see "A recording that
failed" below). mousedb rewrites it after every sheet import, after the nightly
pipeline scan, and when someone runs `mousedb missing-videos --write`;
nothing else writes it.

**What is checked, and against what.**

- The hand scores are mousedb's own hand-scores file
  (`exports\Behavior\Hand_Scores\hand_pellet_scores.csv`, rewritten after every sheet
  import). What the pipeline knows is the pipeline census the tab already uses
  (`logs\pipeline_census.json`, retaken every night by the snapshot job, or by
  **Refresh pipeline view**). Nothing new walks the folders, and the live
  database is not opened.
- Only cohorts with a live tracking sheet in the configured sheets folder.
  Frozen workbooks of finished projects (imported another way) are not
  checked: their recordings were collected before this pipeline existed and
  nobody will record those animals again.
- A session counts as missing only when it is more than **3 days** old at the
  time the census was taken. Three days covers a weekend between recording and
  sending. Change it with `mousedb config --set missing_video_grace_days <days>`.
  It is counted to the census, not to today, because a census taken last night
  cannot hold a recording sent this morning.
- If the census or the hand-scores file is missing or unreadable, the red box
  says "Could not check for hand-scored sessions without a video" and why. It
  never reads as "nothing missing".

**Old gaps: acknowledge them once.** Long-finished cohorts are checked too,
because a recording that never left its PC can still be on that PC or on a
backup. When a person has looked and the recording is gone (or was never
made), they acknowledge it:

    mousedb missing-videos --acknowledge <cohort>:<YYYY-MM-DD> --by "<name>" --reason "<why>"

(the selector can also be a whole cohort, or one session such as
`<YYYYMMDD>_<animal>_<tray>`). The sessions it names at that moment are
recorded one by one, with who, when and why, in
`<mousedb_root>\logs\missing_videos_acknowledged.json`. They leave the red line
and the FIX THESE file, and stay in the CSV marked `acknowledged`. A gap that
opens later in the same cohort is still listed -- acknowledging old gaps never
silences new ones.

**From a terminal** (MouseDB environment): `mousedb missing-videos` prints the
count per cohort and every missing recording, and changes nothing;
`--write` also rewrites the CSV; `--cohort <id>` limits it to one cohort;
`--grace-days <n>` overrides the setting for that run.
`mousedb missing-videos --help` lists every option. (If you meet an "unrecognized arguments" message, the
same command works as `python -m mousedb.missing_videos <options>` or
`python -m mousedb.session_states <options>` in the MouseDB environment.)

### Acknowledging: what can be, how, and how to undo it

**What can be acknowledged.** Only what `mousedb missing-videos` lists as
open right now:

- a session listed as `missing` (overdue: older than the grace period when
  the pipeline view was taken, in a cohort with a live sheet); and
- a session of a failed recording (`recording_failed`; see "A recording that
  failed" below).

Nothing else can be: not a recent session still inside the grace period (it
is not missing yet), not a session of a frozen workbook's cohort (never
checked), not one already acknowledged, and none of the other lines on the
tab or in a FIX THESE file (a video held for review, a label mismatch, a
hand-score hold, a sheet typo, a failed import, a video with no sheet row):
those are fixed where they come from.

**How.** There is no button for it in the MouseDB window; it is one command
in a terminal:

    conda activate <the MouseDB environment>
    mousedb missing-videos --acknowledge <selector> [<selector> ...] --by "<your name>" --reason "<why>"

A selector is one of:

- a cohort, e.g. `PROJA_03` -- every session of it open now, failed
  recordings included;
- a cohort and a day, `PROJA_03:2025-03-14` -- every tray of that day open
  now, failed recordings included;
- one session, written as in the CSV's `video_key` column
  (`<YYYYMMDD>_<animal>_<tray>`);
- a failed recording's file name, exactly as in the CSV's `recording` column
  (put it in quotes).

Selectors must match exactly, capital letters included. `--cohort <cohort>`
on the same command limits what the selectors can name to that cohort, and
`--grace-days <n>` changes which sessions count as missing for that run, the
acknowledging included.

**What it prints.** `[OK] <N> session(s) acknowledged`, then the usual
listing with the new counts. If anything is wrong it prints one line, exits
with code 1 and records **nothing** (every selector is checked before
anything is written):

| message | what to do |
|---|---|
| `[FAIL] say who is acknowledging (--by) and why (--reason)` | give both, each not empty |
| `[FAIL] '<selector>' names no session that is missing or whose recording failed now` | check the spelling and capitals against `mousedb missing-videos` or the CSV; it may already be acknowledged, still recent, or in a cohort that is not checked |
| `[FAIL] the acknowledgements file <file> could not be read (...); fix or delete it` | see "Undoing" below |
| `[FAIL] the hand-scores file ... does not exist yet ...`, `[FAIL] no pipeline view has been taken yet ...` (or another `[FAIL]` about those two files) | the check cannot run at all; import the sheets, or press **Refresh pipeline view**, then try again |

**When it stops showing.** The acknowledgement is written at once to
`<mousedb_root>/logs/missing_videos_acknowledged.json`. Then:

- **MouseReach Video Processing Progress tab**: the next time the tab reads its status (press
  **Refresh**; it also reads it when the window opens and after **Update the
  database now** or **Refresh pipeline view**). Its red line goes; a missing session
  is then counted as "acknowledged as lost" (still not done), and a failed
  recording's sessions stay counted as recording failed.
- **`!! <cohort> - FIX THESE.xlsx`**: at the next sheet import that writes
  (the nightly one, **Import all sheets** / **Import selected**, or
  **Update the database now**); the file is removed when nothing else is in it.
- **`missing_session_videos.csv`**: at its next rewrite (a sheet import, the
  nightly snapshot job, or `mousedb missing-videos --write`), where the
  session stays, marked `acknowledged`.
- **The exports**: nothing changes. A missing session has no rows there
  anyway, and a failed recording stays in `ODC_days_*.csv` with its
  `Recording_Status` -- what happened to it is a fact about the data.

**Undoing an acknowledgement.** There is no command or button for it. Open
`<mousedb_root>/logs/missing_videos_acknowledged.json` in a text editor
(save a copy beside it first). Each acknowledged session is one entry, named
by its `video_key`, holding `by`, `at`, `reason` and `cohort_id`; delete the
entries you want back (mind the commas between entries) and save. Keep the
file valid: a damaged file stops the check everywhere until it is put right
or deleted (the tab and this command say it "could not be read", and the FIX
THESE files lose their missing-video and failed-recording lines meanwhile) --
and deleting the whole file un-acknowledges everything.
The session reappears in red at the same moments as above.

## A recording that failed

Sometimes a recording fails: the recording program stopped after a fraction
of a second, or the file is empty or cannot be opened. The reaching pipeline
checks every multi-animal recording before cutting it, and a recording that
fails the check is a **recording issue**: it is set aside (in the pipeline's
`Processing\Review\_Problematic` folder, with a reason file), never cut and
never reviewed. (The pipeline side, including how to set one aside by hand, is explained in the MouseReach guide on recording issues.)

**What it means for your data.** The sessions on that recording (each animal,
that day, that tray) will never have reaches or kinematics. Their hand pellet
scores, if the trays were scored at the bench, are real and are kept: they
stay in `manual_scores.csv` and in the session files like any other.

**What you see.**

- On the MouseReach Video Processing Progress tab, in red, and in the cohort's
  `!! <cohort> - FIX THESE.xlsx`: one line per failed recording,

      recording failed: <recording file name>, <reason>. Its sessions (...) will never have a video to analyze; ...

  Nothing needs fixing; the line is there so you know.
- In the list of hand-scored sessions without a video
  (`exports\Behavior\Sessions_Without_Video\missing_session_videos.csv`): each session of the
  recording with status `recording_failed` -- not `missing` -- and the
  columns `recording_issue_reason`, `recording` and `hand_scored` (whether the
  trays were scored). Nobody is told to go looking for these on the recording
  PC: there is nothing to find. They are listed at once (no 3-day wait: the
  failure is known), scored or not, for every cohort with a live sheet.
- In the pipeline counts: "Recording failed (set aside, never analyzed): N",
  and those sessions are not counted as still to do.
- In the database: one row per session in the table
  `session_recording_status` (the session, `recording_failed`, the reason,
  the recording's name, who or what flagged it and when).
- In the exports: every `ODC_days_<cohort>.csv` row (one animal, one day)
  has two columns, `Recording_Status` (`recording_failed`, or
  `no_issue_recorded`) and `Recording_Issue_Reason` (which tray, why, and the
  recording's name). The per-reach files (`ODC_reaches`, `reaches_*_analysis`,
  `sessions_*_analysis`, `reach_data.csv`) have NO rows for a failed recording,
  because no reach was ever measured in it: the session row is where the
  reason is written. The data dictionary says the same. Every row of `sessions_*_analysis` also carries `day_recording_status` and
  `day_recording_issue_reason` for its animal-day, read from the same table
  (the snapshot copies it), so a tray missing from a day can be explained
  without opening `ODC_days`.

**Acknowledging it.** Once a person has seen it, acknowledge it once with the
same command as a missing video (see "Acknowledging: what can be, how, and how
to undo it" above); the recording's file name is a selector:

    mousedb missing-videos --acknowledge "<recording file name>" --by "<name>" --reason "<why>"

It then leaves the red line (at the tab's next **Refresh**) and the FIX THESE
file (at the next sheet import). It stays in the CSV (marked
`acknowledged`), in the database and in the exports, because what happened to
the recording is a fact about the data. (The pipeline's own dashboard lists set
-aside recordings without an alarm; the acknowledgement lives here.)

**When it shows up, and why it can lag a day.** mousedb learns of a failed
recording only from the pipeline census (`logs\pipeline_census.json`): the
census lists each recording issue with its reason and sessions. mousedb never
reads the pipeline's code and never writes into its folders.

- The tab and the FIX THESE file read the census directly, so they show a
  failure as soon as the census has been retaken (every night, or **Refresh
  pipeline view**) and the next sheet import has rewritten the FIX THESE file.
- The database and the export files are brought up to date by the nightly
  snapshot job right after it retakes the census (it prints "Failed recordings
  imported: N added, M updated, K removed", or `[FAIL]` and why). That census
  is taken AFTER the night's exports are written, so a recording flagged today
  reaches the export files after the NEXT night's refresh.
- By hand (MouseDB environment): `mousedb recording-issues` lists the failed
  recordings in the last census and changes nothing; `mousedb recording-issues
  --import` also writes the database record (it waits for the database-task
  lock like every other write).
- If a person releases a recording (puts it back to be cut), the next import
  removes its rows.

## The "Update the database now" button

On the MouseReach Video Processing Progress tab, the green button **Update the database now**
pulls everything that is new into the database and rewrites what you see,
in this fixed order:

1. Tracking sheets -> database (the hand-entered data; the sheet is
   authoritative but late). **The sheet wins:** a value corrected in the
   sheet is corrected in the database, and a row removed from the sheet is
   removed from the database at this step -- except rows typed in by hand in
   mousedb, which an import never touches
2. MouseReach results -> reach_data (every new or changed video)
3. MouseBrain analysis registry -> exports
4. Brain region counts -> database (a recounted brain's counts replace the
   old ones; see "How brain counts get here")
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
  short ones try for about three quarters of an hour (45 minutes), the
  snapshot refresh for about twenty-five minutes (`--retry-minutes`, default
  25), because a retry starts it over from the beginning and
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
to finish -- the snapshot refresh for up to 165 minutes (`--lock-wait-minutes`),
the others for up to forty minutes (`MOUSEDB_LOCK_WAIT_MINUTES` changes either)
-- and writes a line every ten minutes while it waits. If it
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

- Only finished videos, and only those analyzed by the outcome detector
  MouseReach currently declares (read from MouseReach's
  `pipeline_versions.json` each night). A video waiting to be re-run is left
  until it has been, so nobody is asked to judge output that is about to change.
- Every pellet with a hand score -- including pellets where the pipeline found
  no reach at all. "The sheet says the pellet moved, and the
  pipeline saw no reach" is one of the most useful things to show a person.
- Never a pellet a person has already answered. The scan skips pellets the
  database records as decided by a person; on a `--route` run by hand,
  MouseReach's route command also checks the review itself before marking
  anything, so a pellet already answered is never sent back.

The list it made is written to `<mousedb_root>/logs/never_reviewed_worklist.json`;
the job's printed output (how many videos and pellets, and each video's
result) goes wherever your scheduler sends the command's output (mousedb
writes no log file of its own for it).

## When something looks wrong

- **A cohort's videos are in the pipeline but "Videos in DB" is low**: check
  *In review* first -- held videos are not final yet. Then run
  `mousedb import-reaches --dry-run` to see whether files are waiting to be
  imported.
- **"N video(s) have reaches in the database but no results file"** at the top
  of the tab: see "Reaches whose results file has gone are reported, never
  removed" above.
- **"N brain(s) have final counts in the database but no counts file"** at
  the top of the tab: see "Brains whose counts file has gone are reported,
  never removed" above.
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
mousedb-current-exports          # rewrite reach_data.csv and manual_scores.csv, and the per-reach files of every cohort that changed (+ the combined files of their projects and the dictionaries): what "Refresh exports now" does
mousedb-current-exports --full   # the same, but every cohort's files rebuilt from nothing
mousedb export-incremental-check --snapshot-dir <copy> --work <empty folder> --previous <copy of exports/Behavior/All_Cohorts>
                                 # prove on real data that the incremental export equals a full rebuild (INSTALL.md)
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
attention box for a person to settle. Animals themselves are never removed
by an import; an animal that the sheet no longer names and that nothing is
recorded for (an id typed into the sheet and later taken out) is listed in
the same red box, for a person to add to the sheet or have deleted from the
database by whoever looks after it (no button or command removes an animal).

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
