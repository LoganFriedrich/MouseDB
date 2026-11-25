"""Where Is My Data tab -- the answer to the question at the end of every cohort.

WHY THIS TAB EXISTS
-------------------
The lab's workflow ends with someone asking where the clean data is, finding
a review queue nobody mentioned, and concluding everything failed. This tab
answers, per cohort, in one table -- and since 2026-09 it answers the four
questions the census exists for:

1. how far along the videos are, counted against the TRACKING SHEETS (since
   2026-11): every session scored by hand in a sheet is filed, in
   processing, held for review, recording failed, or NOT ON THE SERVER YET
   (mousedb.session_states). WHY the sheets: the pipeline's own list cannot
   see a recording nobody sent to it, and such recordings once sat unseen
   on a recording PC for over a year;
2. the pipeline's own workload and how many of it are NOT done yet,
3. WHERE every unfinished one sits (categories that name real folders), and
4. percentages and pace-based completion estimates, always labeled as
   estimates with their basis.

The per-cohort numbers come from mousedb.data_status (analysis snapshot +
queue folders -- never the live database, so refreshing is always safe). The
pipeline columns come from the cached MouseReach census
(mousedb.pipeline_census), joined with the snapshot's video list so
"Analyzed" means finished AND in the database AND on disk. The census cache
is rewritten by the "Refresh pipeline view" button (a 5-25 minute, network-load-dependent folder scan
through the MouseReach environment).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import Qt, QThread
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QTextEdit, QMessageBox,
)

def sheet_cells(d: Optional[dict]):
    """The table's sheet-progress cells for one cohort (session_states
    by_cohort entry): (done, not on server, processing / held / failed,
    no sheet row, whether the not-on-server cell is red). "-" when there is
    no sheet progress for the cohort."""
    if not d:
        return "-", "-", "-", "-", False
    from ..session_states import pct_text
    done = ("%d / %d (%s)" % (d["filed"], d["sheet_scored"], pct_text(d["done_pct"]))
            if d["sheet_scored"] else "-")
    notsrv = str(d["not_on_server"])
    if d["not_on_server_red"]:
        notsrv += " (%d overdue)" % d["not_on_server_red"]
    others = "%d / %d / %d" % (d["in_processing"], d["held_for_review"], d["recording_failed"])
    return done, notsrv, others, str(d["videos_without_sheet_row"]), bool(d["not_on_server_red"])


QUICK_GUIDE = """\
NEEDS YOUR ATTENTION (red, at the top; hidden when there is nothing): things
only a person can fix, gathered from every tool --
* a video MouseReach cannot file because its check against the hand scores
  cannot run (a setting or file to fix);
* a date the sheet import had to read as a typo ("REPAIRED ..."): correct it
  in the sheet;
* a sheet import that failed;
* a recording that FAILED and was set aside by the pipeline (one line per
  recording: "recording failed: <recording>, <reason>"; acknowledged once with
  the same command as a missing video -- docs/WHERE_IS_MY_DATA.md, "A
  recording that failed");
* sessions scored by hand whose video is nowhere in the pipeline (one line
  per cohort; each recording, with where to look for it, is in the FIX THESE
  file and in exports/missing_videos/). A gap nobody can close is
  acknowledged once: mousedb missing-videos --acknowledge ... (see
  docs/WHERE_IS_MY_DATA.md, "Sessions scored by hand that have no video").
Each cohort's list is also written beside its sheet, in
"!! <cohort> - FIX THESE.xlsx" (rewritten after every import, removed when
nothing is left).

PROGRESS AGAINST THE TRACKING SHEETS (the line above the table): every
session scored by hand in a tracking sheet (one animal, one tray, one day)
is in exactly one of five states --
  filed                  finished and in the Analyzed folder;
  in processing          on the server and moving (waiting to be cut, posed,
                         analysed, or waiting for the sheet's next import);
  held for review        waiting for a person (triage, deep review,
                         quarantine, or a label mismatch / hand-score hold);
  recording failed       the recording was empty or unreadable; never a video;
  NOT ON THE SERVER YET  scored in the sheet, no video anywhere. Red when it
                         is more than a few days old: look on the recording
                         PC. Recent ones and ones a person acknowledged as
                         lost are counted but not red.
Done = filed / scored in the sheet. Videos the pipeline holds with no sheet
row are counted separately ("No sheet row") and never in done.
Terminal: mousedb sheet-progress (--list names every missing recording).

WHERE IS MY DATA -- one row per cohort:

* Animals: how many animals the database knows for the cohort (a number in
  brackets = created from a video before the sheet named them).
* Sheet: the tracking sheet's import status (see the Tracking Sheets tab).
* Sessions scored: hand-scored animal-days in the database.
* Done (filed / sheet-scored): the cohort's sessions scored in its sheet that
  are filed, out of all of them, with the percentage.
* Not on server yet: sessions scored in the sheet with no video anywhere in
  the pipeline (red when any is overdue; the number in brackets is how many
  are overdue).
* Processing / held / failed: the other three states, as counts.
* No sheet row: videos the pipeline holds that no sheet row matches (listed
  by `mousedb sheet-progress --json`; never dropped, never in done).
* Pipeline: where the unfinished ones are, as four numbers --
    not started    only the parent collage exists (Unanalyzed/Multi-Animal)
    crop+DLC       being cropped or pose-estimated (DLC_Queue, Unanalyzed/Single_Animal)
    algorithms     MouseReach's analysis (Processing/Posed, Processing), including
                   finished results not yet imported into the database
    quarantined    held out as unprocessable (Processing/Quarantine)
* In review: videos waiting for a person in MouseReach's queues -- triage
  (per-segment questions) and deep review (whole-video problems). Open
  MouseReach's Review Queues tab to work them. Until a video is reviewed and
  released, its data is NOT final.
* Analyzed: fully done -- analysis finished AND in the database AND in the
  Analyzed folder. (+N E/F = outcome-free-tray sessions, which finish without
  database rows by design.)
* Videos in DB / Outcomes / Reaches: what the database itself holds.

THE ALARM LINE above the table: sessions that FINISHED analysis but never
landed in the database must number ZERO. A video that finished minutes ago
lands at the next run of the scheduled reach import -- the line only
matters if a video stays there across imports. (How often the import runs
is set when the jobs are scheduled; INSTALL.md, "Scheduled jobs".)

ESTIMATES: pace is measured from output-file timestamps over the trailing
14 days and projected over the backlog. It is an estimate from recent pace,
never a promise.

"Refresh pipeline view" re-walks the pipeline folders (5-25 minutes
depending on network load; reads only). The other numbers refresh from the
snapshot instantly.

THE FILES: the bottom panel names the current export folder. At its top:
reach_data.csv (one row per reach), manual_scores.csv (one row per pellet
scored from the tray), ONE DATA_DICTIONARY.csv per kind of file (shared by
every cohort), and MANIFEST.json saying when they were written and whether
they are complete for an ODC-SCI upload. Then one folder per project (CNT,
ASPA, ...) -- inside its grant's folder when the lab lists the project under
a grant ("mousedb grants") -- and inside it one folder per cohort (CNT_05) holding that
cohort's files: ODC_days (one row per animal per day, ODC-SCI
shape), ODC_reaches (one row per reach with the animal's surgery details and
the session repeated on every row), the shareable summary and the analysis
tables. Beside the cohort folders, the *_all_cohorts* files hold every
cohort of the project in one file per kind. "Open exports folder" opens it
in Explorer.

The folder is rewritten each time the scheduled snapshot job runs (the
panel says when that last was). "Refresh exports now" rewrites reach_data,
manual_scores, every cohort's reach files and each project's combined reach
files immediately from the latest snapshot; the ODC_days files (and
their combined file) wait for the scheduled job, because they read the
database itself. Only one refresh writes the exports at a time: if another
is already writing (often the nightly job), the button changes nothing and
says "already being written -- try again when it finishes". If a refresh
ran but a file could not be rewritten, a box says "The exports were not
fully rewritten" and names what to fix; those files keep their previous
version. While any job of this tab is running, the buttons say "still
running" instead of starting another.
Terminal equivalents: mousedb-data-status, mousereach-census

MAKE A LIMITED EXPORT: a copy of PART of the current exports -- some cohorts,
some sessions (e.g. each animal's last 3 pillar sessions before the injury),
some tray types, some columns, and an optional yes/no column per animal.
Nothing is calculated again: every value is copied from the full export. The
window saves the definition by name (for everyone) and makes the files in
exports\\subsets\\<name>\\, with their data dictionaries, the exclusions for
those sessions and a SUBSET_MANIFEST.json saying what was chosen. Making it
again archives the previous files, never deletes them; saved ones are remade
after the nightly export when anything they hold changed. Each saved one has
a line below the export files: when it was last made, or "[FAIL]" and why.
Terminal: mousedb export-subset --list

BROWSE THE DATA: opens the data browser -- every table the database holds,
in your web browser, read-only, no code. A small window appears while it
makes your own copy of the snapshot (under a minute, once a day), then your
web browser opens. Click a column to sort, a value to filter, CSV to save
what you see. Close the small window when you are done. It shows the
snapshot (as of the time it names), never the live database, so it cannot
get in the way of the imports. Its window can put a shortcut on your
desktop. Guide: docs/DATA_BROWSER.md. Terminal: mousedb-data-browser

TISSUE ANALYSES: below the export files, one line per MouseBrain analysis
says how many samples are current, how many were produced with a method
other than the one now approved (stale -- re-run before use), how many were
invalidated, and when the mirror was last taken. A line ending "[FAIL] ..."
is an analysis the last mirror run kept as it was, with the reason; an
"[!] analysis mirror: ..." line at the bottom is a problem of the whole run
(for example, no analysis listed at all). "[!] analysis mirror: the last run
stopped before mirroring anything" means the last run changed nothing (a
damaged ledger, or another run held the lock too long): the analysis lines
are still those of the last run that finished, and the line goes when a run
finishes. "[!] analysis mirror: the record of its last stopped run cannot be
read" means the tab could not look at that record, so it cannot say whether
the last run stopped: press Refresh, and check the share if it stays. Guide: docs/WHERE_IS_MY_DATA.md,
"How tissue analysis outputs get here".
"""


def run_exports_program(timeout_seconds: Optional[float] = None) -> dict:
    """Rewrite the current exports by starting the exporter as a program of its
    own (``python -m mousedb.exporters.current --no-wait``) and waiting for it.

    WHY a fresh program and not a call inside this window: the exports record
    which version of the code wrote them (a hash of the code on disk, see
    exporters.current.exporter_code_hash). A window opened before new code was
    installed still holds the OLD code in memory; called in here, it would
    write the old format under the new code's name -- or the new code's
    files would be rebuilt in the old format. A fresh program runs exactly the
    code that is installed.

    --no-wait: if another refresh (usually the nightly job) is writing the
    exports, the program stops at once with exit code EXIT_BUSY instead of
    keeping the button busy for an hour; ``busy`` is then True. If the lock
    folder cannot be written by this login, it stops with EXIT_LOCK_FOLDER and
    ``lock_folder`` is True -- a different message: nobody is writing, and
    pressing again will not help until the folder's permissions are fixed.
    Returns {"returncode", "busy", "lock_folder", "output"}."""
    import subprocess
    import sys
    from ..exporters.current import EXIT_BUSY, EXIT_LOCK_FOLDER
    exe = Path(sys.executable)
    # The window runs under pythonw (no console); its python.exe sits beside it.
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").is_file():
        exe = exe.parent / "python.exe"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # cwd: the Python environment's own folder, never the window's. WHY: with
    # -m, Python puts the working directory FIRST on the import path, so a
    # window started from a folder holding a ``mousedb`` source tree would make
    # the program import that tree instead of the installed package -- the
    # very mismatch this fresh program exists to avoid. The environment's
    # folder holds the interpreter and its library folders, never a package
    # named mousedb at its top.
    proc = subprocess.run([str(exe), "-m", "mousedb.exporters.current", "--no-wait"],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL, creationflags=flags,
                          cwd=str(exe.parent), timeout=timeout_seconds)
    out = proc.stdout.decode("utf-8", errors="replace")
    return {"returncode": proc.returncode, "busy": proc.returncode == EXIT_BUSY,
            "lock_folder": proc.returncode == EXIT_LOCK_FOLDER, "output": out}


def limited_export_lines() -> list:
    """One line per saved limited export: when it was last made, or [FAIL] and
    why (exports/subsets/SUBSETS_STATUS.json). WHY on this tab: the nightly job
    remakes them while nobody watches, and a failure must be seen without
    opening a log."""
    try:
        from ..exporters import subsets
        saved = subsets.profiles()
        status = subsets.read_status()
    except Exception as e:
        return ["  [!] limited exports: the saved definitions could not be read: %s" % e]
    if not saved:
        return []
    lines = ["LIMITED EXPORTS (exports\\subsets):"]
    for name in sorted(saved):
        s = status.get(name) or {}
        if not s:
            lines.append("  %-38s not made yet" % name)
        elif s.get("ok"):
            lines.append("  %-38s made %s%s" % (name, s.get("written_at") or "?",
                                                "" if s.get("result") == "written"
                                                else " (last check: unchanged)"))
        else:
            lines.append("  [FAIL] %s: last attempt %s: %s"
                         % (name, s.get("last_attempt"), s.get("result")))
    return lines


class _Worker(QThread):
    """Runs one job off the window's thread and keeps its result in ``result``.

    WHY the result is read on QThread.finished and not sent from inside run():
    a signal sent from run() reaches the window while the thread is still
    running, so a handler that starts the next job (Update, then Refresh) found
    the worker busy and was silently turned away. ``finished`` arrives once the
    thread has stopped."""

    def __init__(self, fn: Callable[[], dict]):
        super().__init__()
        self._fn = fn
        self.result: dict = {}

    def run(self):
        try:
            r = self._fn()
            self.result = r if isinstance(r, dict) else {"result": r}
        except Exception as e:
            self.result = {"problems": ["%s: %s" % (type(e).__name__, e)]}


class DataStatusTab(QWidget):
    COLUMNS = ["Cohort", "Animals", "Sheet", "Sessions scored",
               "Done (filed / sheet-scored)", "Not on server yet",
               "Processing / held / failed", "No sheet row",
               "Not started / crop+DLC / algorithms / quarantined",
               "In review (triage / deep)", "Analyzed",
               "Videos in DB", "Outcomes algo / human", "Reaches"]

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.db = db
        self._status: dict = {}
        self._worker: Optional[_Worker] = None
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        head = QHBoxLayout()
        title = QLabel("<b>Where Is My Data</b>")
        title.setStyleSheet("font-size: 14px;")
        head.addWidget(title, 1)
        helpb = QPushButton("?")
        helpb.setFixedWidth(28)
        helpb.setToolTip("Quick guide to this tab")
        helpb.clicked.connect(lambda: QMessageBox.information(self, "Where Is My Data", QUICK_GUIDE))
        head.addWidget(helpb)
        root.addLayout(head)

        # What needs a person (attention.py): red, at the top, hidden when
        # nothing does. WHY here: this is the tab people open to find their
        # data, and what is listed there holds that data back.
        self.attention_label = QLabel("")
        self.attention_label.setWordWrap(True)
        self.attention_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.attention_label.setStyleSheet("background:#8b1a1a; color:white; padding:6px;")
        self.attention_label.hide()
        root.addWidget(self.attention_label)

        self.snapshot_label = QLabel("")
        self.snapshot_label.setStyleSheet("color: #888;")
        self.snapshot_label.setWordWrap(True)
        root.addWidget(self.snapshot_label)

        self.update_label = QLabel("")
        self.update_label.setStyleSheet("color: #888;")
        self.update_label.setWordWrap(True)
        root.addWidget(self.update_label)

        # The workload summary (census questions 1, 2 and 4) and the alarm
        # line (the finished-but-not-in-database invariant, which must be 0).
        # Progress against the tracking sheets (session_states): the one
        # "how far along" figure, with the sheet as the denominator.
        self.sheet_label = QLabel("")
        self.sheet_label.setWordWrap(True)
        self.sheet_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.sheet_label)
        self.workload_label = QLabel("")
        self.workload_label.setWordWrap(True)
        root.addWidget(self.workload_label)
        self.invariant_label = QLabel("")
        self.invariant_label.setWordWrap(True)
        root.addWidget(self.invariant_label)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        root.addWidget(self.table, 2)

        self.exports = QTextEdit()
        self.exports.setReadOnly(True)
        self.exports.setMaximumHeight(170)
        root.addWidget(self.exports)

        row = QHBoxLayout()
        for text, slot, tip, style in (
            ("Update the database now", self.update_database,
             "Pull everything that is new -- tracking sheets, MouseReach results, MouseBrain "
             "analyses, brain counts -- into the database, in order, then rewrite the snapshot "
             "and the current exports. Can take an hour or more on a large database (the "
             "snapshot step is the long one); the tab refreshes itself when done.",
             "background:#2e7d32; color:white; font-weight:bold;"),
            ("Refresh", self.refresh, "Re-read the snapshot, the queues and the export manifest.", ""),
            ("Refresh pipeline view (~5-25 min)", self.refresh_pipeline,
             "Walk the MouseReach pipeline folders (collages, queues, the Analyzed tree) "
             "through the MouseReach environment and rewrite the cached census that the "
             "'Should exist' / pipeline / 'Analyzed' columns read. Reads folders only -- "
             "nothing is moved or modified. Takes 5-25 minutes depending on network "
             "load; the table refreshes itself when done. The nightly snapshot job "
             "retakes it too, so press this only for counts newer than last night; "
             "the line above the table says how old the census is, in red once it is "
             "over a day and a half.", ""),
            ("Open exports folder", self.open_exports,
             "Open the folder of current CSVs (+ data dictionaries) in Explorer.",
             "background:#16405a; color:white; font-weight:bold;"),
            ("Browse the data", self.browse_data,
             "Open every table the database holds in your web browser -- read-only, "
             "point and click, save any view as CSV. Shows your own copy of the "
             "snapshot, never the live database, so leaving it open is harmless. "
             "A small window appears first; closing it closes the browser.",
             "background:#5a3d7a; color:white; font-weight:bold;"),
            ("Make a limited export...", self.limited_export,
             "Make a copy of PART of the current exports -- chosen cohorts, sessions (for "
             "example the last 3 pillar sessions before the injury), tray types and "
             "columns -- saved by name so it can be made again. Nothing is calculated "
             "again; every value is copied from the full export. Files go to "
             "exports\\subsets\\<name>.", ""),
            ("Refresh exports now", self.refresh_exports,
             "Rewrite reach_data.csv, manual_scores.csv, every cohort's reach files and "
             "each project's combined reach files from the latest snapshot right now "
             "(the ODC session files wait for the scheduled snapshot job, which reads "
             "the database itself).", ""),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            if style:
                b.setStyleSheet(style)
            b.clicked.connect(slot)
            row.addWidget(b)
        root.addLayout(row)
        note = QLabel("Videos 'in review' are worked in MouseReach (its Review Queues tab); "
                      "their data is not final until released.")
        note.setStyleSheet("color: #888;")
        note.setWordWrap(True)
        root.addWidget(note)

    BUSY_TEXT = "A refresh is still running -- try again in a moment."

    def _busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _run(self, fn: Callable[[], dict], on_done) -> bool:
        """Start ``fn`` off the window's thread; ``on_done(result)`` when it ends.

        Returns False, and starts nothing, while another job of this tab is
        still running. Every button checks that BEFORE it writes anything on
        screen: a button that printed "Rewriting..." and then did nothing was
        a press that looked accepted and was silently dropped."""
        if self._busy():
            return False
        worker = _Worker(fn)
        self._worker = worker
        worker.finished.connect(lambda: on_done(worker.result))
        worker.start()
        return True

    def refresh(self):
        if self._busy():
            QMessageBox.information(self, "Refresh", self.BUSY_TEXT)
            return
        self.snapshot_label.setText("Reading...")
        self._show_last_update()
        from ..data_status import status

        def status_and_attention():
            st = status()
            from ..attention import items
            st["attention"] = items()
            return st
        self._run(status_and_attention, self._on_status)

    # --- the "Update the database now" button --------------------------------
    def _show_last_update(self):
        from ..update import last_update
        last = last_update()
        if not last:
            self.update_label.setText("Last database update: none recorded yet -- press "
                                      "'Update the database now' to pull everything that is new.")
            return
        verdict = "everything landed" if last.get("ok") else ("FAILED -- " + (last.get("message") or ""))
        self.update_label.setText("Last database update: %s (started by %s) -- %s"
                                  % (last.get("finished") or last.get("started") or "?",
                                     last.get("triggered_by") or "?", verdict))

    def update_database(self):
        if self._busy():
            QMessageBox.information(self, "Update the database", self.BUSY_TEXT)
            return
        from ..update import run_update
        self.update_label.setText("Updating the database: tracking sheets, MouseReach results, "
                                  "MouseBrain analyses, brain counts, then the snapshot. This can take "
                                  "an hour or more on a large database -- the snapshot step is the "
                                  "long one. The tab refreshes itself when done.")
        self._run(lambda: {"update": run_update(triggered_by="gui", log=lambda s: None)},
                  self._on_update_done)

    def _on_update_done(self, r: dict):
        from ..update import format_summary
        res = r.get("update")
        if res is None:
            QMessageBox.critical(self, "Update the database",
                                 "The update could not run:\n" + "\n".join(r.get("problems", ["unknown failure"])))
        else:
            text = format_summary(res)
            box = QMessageBox.information if res.ok else QMessageBox.warning
            box(self, "Update the database", text)
        self._show_last_update()
        self.refresh()

    # --- the "Refresh pipeline view" button ----------------------------------
    def refresh_pipeline(self):
        if self._busy():
            QMessageBox.information(self, "Refresh pipeline view", self.BUSY_TEXT)
            return
        self.update_label.setText("Scanning the pipeline folders through the MouseReach "
                                  "environment (5-25 minutes depending on network load; reads "
                                  "only). The table refreshes itself when done.")
        from ..pipeline_census import refresh as census_refresh
        self._run(lambda: {"census": census_refresh()}, self._on_pipeline_done)

    def _on_pipeline_done(self, r: dict):
        if r.get("census") is None:
            QMessageBox.critical(
                self, "Refresh pipeline view",
                "The pipeline scan could not run:\n"
                + "\n".join(r.get("problems", ["unknown failure"])))
            self._show_last_update()
            return
        self.refresh()

    # --- rendering ------------------------------------------------------------
    def _render_sheet_progress(self, sp: Optional[dict], why: Optional[str]):
        if not sp:
            self.sheet_label.setText(
                "PROGRESS AGAINST THE TRACKING SHEETS: could not be counted (%s). No done "
                "percentage is shown rather than one counted against the pipeline alone."
                % (why or "no sheet progress"))
            self.sheet_label.setStyleSheet("background:#e65100; color:white; padding:3px;")
            return
        import html as _html
        from ..session_states import headline
        t = sp["totals"]
        text = _html.escape("PROGRESS AGAINST THE TRACKING SHEETS (pipeline census of %s): %s"
                            % (sp["view_taken"], headline(sp)))
        if t["not_on_server_red"]:
            text = text.replace(
                "NOT ON THE SERVER YET",
                "<span style='background:#b71c1c; color:white; font-weight:bold;'>"
                "NOT ON THE SERVER YET</span>", 1)
        for c in sp.get("caveats") or []:
            text += "<br>[!] " + _html.escape(c)
        self.sheet_label.setText(text)
        self.sheet_label.setStyleSheet("padding:3px; font-weight:bold;")

    def _render_pipeline_summary(self, pc: Optional[dict]):
        if not pc:
            self.workload_label.setText(
                "PIPELINE: no census taken yet -- press 'Refresh pipeline view' to "
                "count the total workload and locate every unfinished video.")
            self.workload_label.setStyleSheet("color: #888;")
            self.invariant_label.setText("")
            self.invariant_label.setStyleSheet("")
            return

        t = pc.get("totals") or {}
        be = pc.get("by_element") or {}
        exp = t.get("expected") or 0
        fin = t.get("finished_files") or 0
        analyzed = t.get("analyzed")
        an_txt = "unavailable" if analyzed is None else str(analyzed)
        if t.get("session_only"):
            an_txt += "  (+%d E/F-only)" % t["session_only"]
        # Say how OLD, as for the snapshot below: a bare timestamp let an
        # old census pass for current.
        stale = pc.get("stale", False)
        lines = []
        if stale:
            lines.append(
                "[!] These pipeline counts are %s old. The nightly job retakes the census "
                "after the snapshot; it has not succeeded since %s. Press 'Refresh "
                "pipeline view' to retake it now."
                % (pc.get("age") or "an unknown time", pc.get("generated_at") or "?"))
        # No percentage here: the census's own list is not the denominator
        # (the sheets are -- the line above). It cannot see a recording that
        # never reached the pipeline.
        lines += [
            "PIPELINE (census of %s -- %s old, scan %ss): it holds %d sessions | "
            "%d finished on disk | fully analyzed (done + in DB + on disk): %s"
            % (pc.get("generated_at") or "?", pc.get("age") or "unknown",
               pc.get("scan_seconds"), exp, fin, an_txt),
            "Remaining: %d not started, %d in crop/pose, %d in algorithms, "
            "%d + %d waiting on a person (triage + deep review), %d quarantined. "
            "Recording failed (set aside, never analyzed): %d"
            % (be.get("unanalyzed", 0), be.get("crop_dlc", 0), be.get("mousereach", 0),
               be.get("triage", 0), be.get("deep_review", 0), be.get("quarantined", 0),
               be.get("recording_issue", 0)),
        ]
        eta = pc.get("eta") or {}
        pace = []
        if eta.get("finished_per_day"):
            pace.append("machine backlog %d at ~%.1f/day -> ~%.1f days (about %s)"
                        % (eta.get("machine_backlog", 0), eta["finished_per_day"],
                           eta.get("machine_days", 0), eta.get("machine_date")))
        else:
            pace.append("machine backlog %d -- no finishes in the window, no pace measurable"
                        % eta.get("machine_backlog", 0))
        if eta.get("reviews_per_day"):
            pace.append("review backlog %d at ~%.1f/day -> ~%.1f days (about %s)"
                        % (eta.get("human_backlog", 0), eta["reviews_per_day"],
                           eta.get("human_days", 0), eta.get("human_date")))
        else:
            pace.append("review backlog %d -- no saved reviews in the window, no pace measurable"
                        % eta.get("human_backlog", 0))
        lines.append("Estimates (%s): %s" % (eta.get("basis") or "no basis", "; ".join(pace)))
        self.workload_label.setText("\n".join(lines))
        self.workload_label.setStyleSheet(
            "background:#b71c1c; color:white; padding:3px;" if stale else "")

        inv = pc.get("invariant")
        if inv is None:
            self.invariant_label.setText(
                "Cannot check finished-vs-database: the snapshot was unreadable, so no "
                "verdict exists for this run (this is a refusal, not a zero).")
            self.invariant_label.setStyleSheet(
                "background:#e65100; color:white; padding:3px; font-weight:bold;")
        elif inv.get("count"):
            examples = ", ".join(list(inv["sessions"])[:3])
            more = inv["count"] - min(3, inv["count"])
            self.invariant_label.setText(
                "%d finished videos have NOT landed in the database (this number must "
                "be 0). New results land at the next scheduled reach import -- investigate only "
                "if a video stays here across imports. E.g.: %s%s"
                % (inv["count"], examples, (" (+%d more)" % more) if more > 0 else ""))
            self.invariant_label.setStyleSheet(
                "background:#b71c1c; color:white; padding:3px; font-weight:bold;")
        else:
            self.invariant_label.setText(
                "Nothing is stuck between analysis and the database (must be 0; it is 0).")
            self.invariant_label.setStyleSheet(
                "background:#2e7d32; color:white; padding:3px;")

    # Lines shown in the red panel before "... and N more".
    ATTENTION_SHOWN = 10

    def _show_attention(self, found):
        # A cohort's missing videos are one line here, not one per recording
        # (those are in its FIX THESE file): a finished cohort's gaps would
        # otherwise push every other item out of the panel.
        from ..missing_videos import fold_for_tab
        found = fold_for_tab(found)
        if not found:
            self.attention_label.hide()
            return
        lines = ["<b>Needs your attention (%d)</b>" % len(found)]
        lines += ["&bull; %s" % i.get("says", "") for i in found[:self.ATTENTION_SHOWN]]
        if len(found) > self.ATTENTION_SHOWN:
            lines.append("... and %d more -- each cohort's are listed in the "
                         "'!! &lt;cohort&gt; - FIX THESE.xlsx' file beside its sheet"
                         % (len(found) - self.ATTENTION_SHOWN))
        self.attention_label.setText("<br>".join(lines))
        self.attention_label.show()

    def _on_status(self, st: dict):
        self._status = st
        self._show_attention(st.get("attention") or [])
        # Say how OLD, not just when. Everything on this tab is as of the
        # snapshot, and the snapshot is rewritten by a scheduled job that can
        # fail -- usually because another program had the database locked. When
        # it does, these numbers quietly stop moving. A bare timestamp asks the
        # reader to know how often the job runs and subtract; in the case this
        # was written for, three days went by with nobody noticing.
        age_hours = st.get("snapshot_age_hours")
        from ..data_status import STALE_SNAPSHOT_HOURS
        stale = age_hours is not None and age_hours > STALE_SNAPSHOT_HOURS
        self.snapshot_label.setText(
            "Numbers as of the analysis snapshot taken %s -- %s old.%s"
            % (st.get("snapshot_time") or "?", st.get("snapshot_age") or "unknown",
               "  The job that rewrites it has not succeeded since, so newly "
               "analysed work is missing from everything below." if stale else ""))
        self.snapshot_label.setStyleSheet(
            "background:#b71c1c; color:white; padding:3px;" if stale else "color: #888;")
        self._render_sheet_progress(st.get("sheet_progress"), st.get("sheet_progress_error"))
        self._render_pipeline_summary(st.get("pipeline"))
        cohorts = st.get("cohorts", [])
        self.table.setRowCount(len(cohorts))
        for i, c in enumerate(cohorts):
            q = c.get("videos_in_review") or {}
            src = c.get("segments_by_outcome_source") or {}
            human = sum(v for k, v in src.items() if k != "algo")
            animals = "?" if c.get("animals") is None else str(c["animals"])
            if c.get("animals_created_from_video_only"):
                animals += "  [%d from video only]" % c["animals_created_from_video_only"]
            sh = c.get("sheet") or {}
            p = c.get("pipeline")
            done, notsrv, others, nosheet, red = sheet_cells(c.get("sheet_progress"))
            if p:
                stages = "%d / %d / %d / %d" % (
                    p.get("unanalyzed", 0), p.get("crop_dlc", 0),
                    p.get("mousereach", 0), p.get("quarantined", 0))
                an = str(p.get("analyzed", 0) or 0)
                if p.get("session_only"):
                    an += " (+%d E/F)" % p["session_only"]
            else:
                stages = an = "-"
            vals = [c["cohort_id"], animals, sh.get("state") or "-",
                    c.get("sessions_scored", 0),
                    done, notsrv, others, nosheet, stages,
                    "%d / %d" % (q.get("triage", 0), q.get("deep_review", 0)),
                    an,
                    c.get("videos_in_db", 0),
                    "%d / %d" % (src.get("algo", 0), human), c.get("reaches_in_db", 0)]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j == 5 and red:
                    it.setForeground(Qt.white)
                    it.setBackground(Qt.darkRed)
                if j == 9 and (q.get("triage", 0) or q.get("deep_review", 0)):
                    it.setForeground(Qt.white)
                    it.setBackground(Qt.darkYellow)
                if j == 2 and sh.get("state") in ("last_import_failed", "never_imported", "sheet_newer",
                                                  "not_synced"):
                    it.setForeground(Qt.white)
                    it.setBackground(Qt.darkRed if sh.get("state") in ("last_import_failed", "not_synced")
                                     else Qt.darkYellow)
                self.table.setItem(i, j, it)
        ex = st.get("exports") or {}
        lines = ["CURRENT EXPORTS: %s" % ex.get("folder"),
                 "written: %s    complete for ODC-SCI upload: %s"
                 % (ex.get("generated_at") or "never", ex.get("complete"))]
        for name, rows in (ex.get("files") or {}).items():
            lines.append("  %-38s %s rows" % (name, rows))
        # Tissue analyses mirrored from MouseBrain's registry -- same lines as the terminal
        from ..data_status import analysis_lines, old_storage_lines, session_list_lines
        lines.extend(analysis_lines(st.get("analyses") or []))
        # How far each study's list of sessions has got -- same lines as the terminal
        lines.extend(session_list_lines(st.get("session_lists") or []))
        # Recordings still only on an old storage root -- same lines as the terminal
        lines.extend(old_storage_lines(st.get("old_storage") or []))
        pc = st.get("pipeline") or {}
        for cv in pc.get("caveats") or []:
            lines.append("  [!] %s" % cv)
        d = pc.get("diagnostics") or {}
        if d.get("missing_roots"):
            lines.append("  [!] pipeline census scanned MISSING roots as empty: %s"
                         % ", ".join(d["missing_roots"]))
        if d.get("collages_that_parsed_to_nothing"):
            lines.append("  [!] %d collages could not be parsed into sessions (their "
                         "videos are invisible to the workload count until renamed)"
                         % len(d["collages_that_parsed_to_nothing"]))
        lines.extend(limited_export_lines())
        if ex.get("skipped"):
            lines.append("  per-reach files not rewritten: %s" % ex["skipped"])
        elif ex.get("incremental"):
            lines.append("  %s" % ex["incremental"])
        for note in ex.get("notes") or []:
            lines.append("  [note] %s" % note)
        for prob in list(st.get("problems", [])) + list(ex.get("problems", [])):
            lines.append("  [!] %s" % prob)
        self.exports.setPlainText("\n".join(lines))

    def limited_export(self):
        """Open the limited-export window (gui.limited_export_dialog); refresh
        this tab when it closes, so the lines about limited exports are current."""
        from .limited_export_dialog import LimitedExportDialog
        LimitedExportDialog(self).exec_()
        if not self._busy():
            self.refresh()

    def open_exports(self):
        d = (self._status.get("exports") or {}).get("folder")
        if d and Path(d).is_dir():
            os.startfile(d)
        else:
            self.exports.append("No exports folder yet -- press 'Refresh exports now'.")

    def browse_data(self):
        """Start the data browser as its own process (mousedb.data_browser).

        WHY a separate process, not a window of this one: the browser serves a
        web page for as long as someone keeps looking at it, and this GUI must
        stay free -- and closing this GUI must not close someone's browsing.
        It never opens the live database (see mousedb/data_browser)."""
        from ..data_browser.launch import datasette_missing_message, start_detached
        missing = datasette_missing_message()
        if missing:
            QMessageBox.warning(self, "Browse the data", missing)
            return
        try:
            start_detached()
        except Exception as e:
            QMessageBox.critical(
                self, "Browse the data",
                "The data browser could not be started:\n%s: %s\n\nTry again; if it "
                "keeps happening, tell whoever looks after mousedb." % (type(e).__name__, e))
            return
        self.exports.append("Opening the data browser: a small window appears, and your "
                            "web browser opens when your copy is ready (under a minute).")

    def refresh_exports(self):
        if self._busy():
            QMessageBox.information(self, "Refresh exports now", self.BUSY_TEXT)
            return
        self.exports.append("Rewriting current exports from the latest snapshot...")
        self._run(run_exports_program, self._on_exports_done)

    def _on_exports_done(self, r: dict):
        if r.get("busy"):
            QMessageBox.information(
                self, "Refresh exports now",
                "The exports are already being written -- try again when it finishes. "
                "Nothing was changed.")
        elif r.get("lock_folder"):
            QMessageBox.warning(
                self, "Refresh exports now",
                "The exports were not rewritten: your login cannot write to the folder "
                "that holds mousedb's lock files. Nobody else is writing the exports, "
                "and pressing again will not help. Nothing was changed. Ask whoever "
                "manages the computer to give your login write access to that folder.\n\n"
                + r.get("output", "")[-1500:])
        elif r.get("returncode") not in (0, None) or r.get("problems"):
            QMessageBox.warning(
                self, "Refresh exports now",
                "The exports were not fully rewritten:\n"
                + "\n".join(r.get("problems") or [r.get("output", "")[-2000:]]))
        self.refresh()
