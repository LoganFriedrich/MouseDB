"""Where Is My Data tab -- the answer to the question at the end of every cohort.

WHY THIS TAB EXISTS
-------------------
The lab's workflow ends with someone asking where the clean data is, finding
a review queue nobody mentioned, and concluding everything failed. This tab
answers, per cohort, in one table -- and since 2026-09 it answers the four
questions the census exists for:

1. the TOTAL workload (how many single-animal sessions should exist, from
   every collage anywhere in the pipeline),
2. how many are NOT done yet,
3. WHERE every unfinished one sits (categories that name real folders), and
4. percentages and pace-based completion estimates, always labelled as
   estimates with their basis.

The per-cohort numbers come from mousedb.data_status (analysis snapshot +
queue folders -- never the live database, so refreshing is always safe). The
pipeline columns come from the cached MouseReach census
(mousedb.pipeline_census), joined with the snapshot's video list so
"Analyzed" means finished AND in the database AND on disk. The census cache
is rewritten by the "Refresh pipeline view" button (a 2-5 minute folder scan
through the MouseReach environment).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QTextEdit, QMessageBox,
)

QUICK_GUIDE = """\
WHERE IS MY DATA -- one row per cohort:

* Animals: how many animals the database knows for the cohort (a number in
  brackets = created from a video before the sheet named them).
* Sheet: the tracking sheet's import status (see the Tracking Sheets tab).
* Sessions scored: hand-scored animal-days in the database.
* Should exist: the cohort's TOTAL workload -- every single-animal video that
  ultimately needs analysis, counted from every multi-animal collage found
  anywhere in the pipeline (intake, archives, retired) plus every video that
  already left a file on disk.
* Left to do: Should exist minus everything fully done.
* Pipeline: where the unfinished ones are, as four numbers --
    not started    only the parent collage exists (Unanalyzed/Multi-Animal)
    crop+DLC       being cropped or pose-estimated (DLC_Queue, Single_Animal)
    algorithms     MouseReach's analysis (DLC_Complete, Processing), including
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
lands on the next hourly import -- the line only matters if a video stays
there across imports.

ESTIMATES: pace is measured from output-file timestamps over the trailing
14 days and projected over the backlog. It is an estimate from recent pace,
never a promise.

"Refresh pipeline view" re-walks the pipeline folders (2-5 minutes, reads
only). The other numbers refresh from the snapshot instantly.

THE FILES: the bottom panel names the current export folder. It holds
reach_data.csv (one row per reach), manual_scores.csv (one row per pellet
scored from the tray), ODC_sessions_<cohort>.csv (one row per animal per
session, ODC-SCI shape), each with a DATA_DICTIONARY.csv beside it, plus
MANIFEST.json saying when they were written and whether they are complete
for an ODC-SCI upload. "Open exports folder" opens it in Explorer.

The folder is rewritten by the hourly job. "Refresh exports now" rewrites
reach_data and manual_scores immediately from the latest snapshot.
Terminal equivalents: mousedb-data-status, mousereach-census

TISSUE ANALYSES: below the export files, one line per MouseBrain analysis
says how many samples are current, how many were produced with a method
other than the one now approved (stale -- re-run before use), how many were
invalidated, and when the mirror was last taken.
"""


class _Worker(QThread):
    done = pyqtSignal(dict)

    def __init__(self, fn: Callable[[], dict]):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            r = self._fn()
            self.done.emit(r if isinstance(r, dict) else {"result": r})
        except Exception as e:
            self.done.emit({"problems": ["%s: %s" % (type(e).__name__, e)]})


class DataStatusTab(QWidget):
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
        self.workload_label = QLabel("")
        self.workload_label.setWordWrap(True)
        root.addWidget(self.workload_label)
        self.invariant_label = QLabel("")
        self.invariant_label.setWordWrap(True)
        root.addWidget(self.invariant_label)

        self.table = QTableWidget(0, 12)
        self.table.setHorizontalHeaderLabels(
            ["Cohort", "Animals", "Sheet", "Sessions scored",
             "Should exist", "Left to do",
             "Not started / crop+DLC / algorithms / quarantined",
             "In review (triage / deep)", "Analyzed",
             "Videos in DB", "Outcomes algo / human", "Reaches"])
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
             "and the current exports. Takes a few minutes; the tab refreshes itself when done.",
             "background:#2e7d32; color:white; font-weight:bold;"),
            ("Refresh", self.refresh, "Re-read the snapshot, the queues and the export manifest.", ""),
            ("Refresh pipeline view (~2-5 min)", self.refresh_pipeline,
             "Walk the MouseReach pipeline folders (collages, queues, the Analyzed tree) "
             "through the MouseReach environment and rewrite the cached census that the "
             "'Should exist' / pipeline / 'Analyzed' columns read. Reads folders only -- "
             "nothing is moved or modified. The table refreshes itself when done.", ""),
            ("Open exports folder", self.open_exports,
             "Open the folder of current CSVs (+ data dictionaries) in Explorer.",
             "background:#16405a; color:white; font-weight:bold;"),
            ("Refresh exports now", self.refresh_exports,
             "Rewrite reach_data.csv and manual_scores.csv from the latest snapshot "
             "right now (ODC session files refresh on the hourly run).", ""),
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

    def _run(self, fn: Callable[[], dict], on_done):
        if self._worker is not None and self._worker.isRunning():
            return
        self._worker = _Worker(fn)
        self._worker.done.connect(on_done)
        self._worker.start()

    def refresh(self):
        self.snapshot_label.setText("Reading...")
        self._show_last_update()
        from ..data_status import status
        self._run(status, self._on_status)

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
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Update the database",
                                    "A refresh is still running -- try again in a moment.")
            return
        from ..update import run_update
        self.update_label.setText("Updating the database: tracking sheets, MouseReach results, "
                                  "MouseBrain analyses, brain counts, then the snapshot. This takes "
                                  "a few minutes; the tab refreshes itself when done.")
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
        if self._worker is not None and self._worker.isRunning():
            QMessageBox.information(self, "Refresh pipeline view",
                                    "A refresh is still running -- try again in a moment.")
            return
        self.update_label.setText("Scanning the pipeline folders through the MouseReach "
                                  "environment (2-5 minutes over the network share; reads only). "
                                  "The table refreshes itself when done.")
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
        pct = (100.0 * fin / exp) if exp else 0.0
        analyzed = t.get("analyzed")
        an_txt = "unavailable" if analyzed is None else str(analyzed)
        if t.get("session_only"):
            an_txt += "  (+%d E/F-only)" % t["session_only"]
        lines = [
            "PIPELINE (census of %s, scan %ss): %d sessions should exist | "
            "%d finished on disk (%.1f%%) | fully analyzed (done + in DB + on disk): %s"
            % (pc.get("generated_at") or "?", pc.get("scan_seconds"), exp, fin, pct, an_txt),
            "Remaining: %d not started, %d in crop/pose, %d in algorithms, "
            "%d + %d waiting on a person (triage + deep review), %d quarantined"
            % (be.get("unanalyzed", 0), be.get("crop_dlc", 0), be.get("mousereach", 0),
               be.get("triage", 0), be.get("deep_review", 0), be.get("quarantined", 0)),
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
        self.workload_label.setStyleSheet("")

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
                "be 0). New results land on the next hourly import -- investigate only "
                "if a video stays here across imports. E.g.: %s%s"
                % (inv["count"], examples, (" (+%d more)" % more) if more > 0 else ""))
            self.invariant_label.setStyleSheet(
                "background:#b71c1c; color:white; padding:3px; font-weight:bold;")
        else:
            self.invariant_label.setText(
                "Nothing is stuck between analysis and the database (must be 0; it is 0).")
            self.invariant_label.setStyleSheet(
                "background:#2e7d32; color:white; padding:3px;")

    def _on_status(self, st: dict):
        self._status = st
        self.snapshot_label.setText("Numbers as of the analysis snapshot taken %s"
                                    % (st.get("snapshot_time") or "?"))
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
            if p:
                exp = p.get("expected", 0)
                done = (p.get("analyzed", 0) or 0) + p.get("session_only", 0)
                left = exp - done
                stages = "%d / %d / %d / %d" % (
                    p.get("unanalyzed", 0), p.get("crop_dlc", 0),
                    p.get("mousereach", 0), p.get("quarantined", 0))
                an = str(p.get("analyzed", 0) or 0)
                if p.get("session_only"):
                    an += " (+%d E/F)" % p["session_only"]
            else:
                exp = left = stages = an = "-"
            vals = [c["cohort_id"], animals, sh.get("state") or "-",
                    c.get("sessions_scored", 0),
                    exp, left, stages,
                    "%d / %d" % (q.get("triage", 0), q.get("deep_review", 0)),
                    an,
                    c.get("videos_in_db", 0),
                    "%d / %d" % (src.get("algo", 0), human), c.get("reaches_in_db", 0)]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j == 7 and (q.get("triage", 0) or q.get("deep_review", 0)):
                    it.setForeground(Qt.white)
                    it.setBackground(Qt.darkYellow)
                if j == 2 and sh.get("state") in ("last_import_failed", "never_imported", "sheet_newer"):
                    it.setForeground(Qt.white)
                    it.setBackground(Qt.darkRed if sh.get("state") == "last_import_failed" else Qt.darkYellow)
                self.table.setItem(i, j, it)
        ex = st.get("exports") or {}
        lines = ["CURRENT EXPORTS: %s" % ex.get("folder"),
                 "written: %s    complete for ODC-SCI upload: %s"
                 % (ex.get("generated_at") or "never", ex.get("complete"))]
        for name, rows in (ex.get("files") or {}).items():
            lines.append("  %-38s %s rows" % (name, rows))
        # Tissue analyses mirrored from MouseBrain's registry -- same lines as the terminal
        from ..data_status import analysis_lines
        lines.extend(analysis_lines(st.get("analyses") or []))
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
        for prob in list(st.get("problems", [])) + list(ex.get("problems", [])):
            lines.append("  [!] %s" % prob)
        self.exports.setPlainText("\n".join(lines))

    def open_exports(self):
        d = (self._status.get("exports") or {}).get("folder")
        if d and Path(d).is_dir():
            os.startfile(d)
        else:
            self.exports.append("No exports folder yet -- press 'Refresh exports now'.")

    def refresh_exports(self):
        self.exports.append("Rewriting current exports from the latest snapshot...")
        from ..exporters.current import refresh_current
        self._run(lambda: refresh_current(), lambda _m: self.refresh())
