"""Where Is My Data tab -- the answer to the question at the end of every cohort.

WHY THIS TAB EXISTS
-------------------
The lab's workflow ends with someone asking where the clean data is, finding
a review queue nobody mentioned, and concluding everything failed. This tab
answers, per cohort, in one table: animals, hand-scored sessions, videos in
the database, videos waiting in review, how many outcomes are still
algorithm-only vs human-reviewed, the sheet's import status -- and the folder
of current CSVs (with data dictionaries) a person can open now.

The numbers come from mousedb.data_status, computed from the analysis
snapshot and the review-queue folders -- never the live database -- so
refreshing is always safe. It lives in mousedb's own GUI because these are
this tool's numbers; it used to be a tab inside MouseReach that shelled out
to this environment.
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
* Videos in DB: videos whose reaches are in the database.
* In review: videos waiting for a person in MouseReach's queues -- triage
  (per-segment questions) and deep review (whole-video problems). Open
  MouseReach's Review Queues tab to work them. Until a video is reviewed and
  released, its data is NOT final.
* Outcomes algo / human: how many pellet outcomes rest on the algorithm
  alone vs were confirmed or corrected by a person.

THE FILES: the bottom panel names the current export folder. It holds
reach_data.csv (one row per reach), manual_scores.csv (one row per pellet
scored from the tray), ODC_sessions_<cohort>.csv (one row per animal per
session, ODC-SCI shape), each with a DATA_DICTIONARY.csv beside it, plus
MANIFEST.json saying when they were written and whether they are complete
for an ODC-SCI upload. "Open exports folder" opens it in Explorer.

The folder is rewritten by the hourly job. "Refresh exports now" rewrites
reach_data and manual_scores immediately from the latest snapshot.
Terminal equivalents: mousedb-data-status, mousedb-current-exports

TISSUE ANALYSES: below the export files, one line per MouseBrain analysis
says how many samples are current, how many were produced with a method
other than the one now approved (stale -- re-run before use), how many were
invalidated, and when the mirror was last taken. The measurements, figures
and provenance (registry.json) are mirrored hourly by mousedb import-analyses
into exports/<analysis> and figures/<analysis> beside the export folder.
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

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["Cohort", "Animals", "Sheet", "Sessions scored", "Videos in DB",
             "In review (triage / deep)", "Outcomes algo / human", "Reaches"])
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
            ("Refresh", self.refresh, "Re-read the snapshot, the queues and the export manifest.", ""),
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
        from ..data_status import status
        self._run(status, self._on_status)

    def _on_status(self, st: dict):
        self._status = st
        self.snapshot_label.setText("Numbers as of the analysis snapshot taken %s"
                                    % (st.get("snapshot_time") or "?"))
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
            vals = [c["cohort_id"], animals, sh.get("state") or "-",
                    c.get("sessions_scored", 0), c.get("videos_in_db", 0),
                    "%d / %d" % (q.get("triage", 0), q.get("deep_review", 0)),
                    "%d / %d" % (src.get("algo", 0), human), c.get("reaches_in_db", 0)]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j == 5 and (q.get("triage", 0) or q.get("deep_review", 0)):
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
        for p in list(st.get("problems", [])) + list(ex.get("problems", [])):
            lines.append("  [!] %s" % p)
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
