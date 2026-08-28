"""Tracking Sheets tab -- import the lab's tracking workbooks from a button.

WHY THIS TAB EXISTS
-------------------
The tracking workbooks are the hand-kept record of every animal (weights,
ramps, injury, surgery, manual tray scores). They are filled in "eventually",
and the database only knows what was imported from them. Until 2026-08-28
importing was a terminal command nobody in the lab knew, it ran hourly in the
background with no visible result, and a failure rolled back in silence for
weeks. This tab is the visible, clickable version: per cohort it shows WHICH
file is the sheet, WHEN it was edited, WHEN it was last imported and whether
that worked -- and lets you import, pick the right file when several match,
set the folder, and create a new blank sheet.

It lives in mousedb's own GUI (mousedb-entry) because importing sheets is
this tool's job; it used to be a tab inside MouseReach that shelled out to
this environment, which made MouseReach depend on a tool it should not know.
All work runs off the GUI thread; nothing here ever edits a workbook.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog,
    QInputDialog, QMessageBox, QTextEdit,
)

STATE_TEXT = {
    "up_to_date": ("Up to date", "green"),
    "sheet_newer": ("Sheet edited since last import -- import", "yellow"),
    "never_imported": ("Never imported -- import", "yellow"),
    "last_import_failed": ("LAST IMPORT FAILED -- see message", "red"),
    "no_sheet": ("No sheet found", "grey"),
    "error": ("Status error", "red"),
}

QUICK_GUIDE = """\
The tracking workbooks (one per cohort, e.g. Connectome_05_Animal_Tracking.xlsx)
are the lab's hand-kept record: animals, weights, ramps, manual tray scores, injury
and surgery details. The database only knows what has been IMPORTED from them.

WHAT THE TABLE SHOWS, per cohort: which file is being read as the sheet; when that
file was last edited; when it was last imported and whether that worked; and a
plain verdict -- Up to date / Sheet edited since last import / Never imported /
LAST IMPORT FAILED.

WHAT TO DO:
* Press "Import all sheets" (or select rows and "Import selected"). Progress and
  the outcome per cohort appear in the log box. A failure shows its reason.
* "Sheet edited since last import" means someone changed the workbook after the
  last import -- import again. (The hourly background job also imports; this tab
  shows you whether it succeeded.)
* "N files match -- choose": more than one workbook claims to be that cohort's
  sheet (a copy, a draft, a "(2)"). Select the row, press "This is the sheet",
  pick the real one. The choice is remembered and shown as [pinned].
* "Set sheets folder" points this machine at the folder holding the workbooks
  (once per machine, and again if the folder ever moves). "Open folder" opens it.
* "New cohort sheet" creates a correctly formatted, empty workbook in that folder.

NOTHING HERE EDITS A WORKBOOK. Importing only reads them.
Terminal equivalents: mousedb-sheets status | import | pin | set-dir
"""


class _Worker(QThread):
    """Run one Python callable off the GUI thread and hand back its dict."""
    done = pyqtSignal(dict)

    def __init__(self, fn: Callable[[], dict]):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            r = self._fn()
            self.done.emit(r if isinstance(r, dict) else {"result": r})
        except Exception as e:  # never let a worker exception kill the GUI
            self.done.emit({"problem": "%s: %s" % (type(e).__name__, e)})


class TrackingSheetsTab(QWidget):
    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.db = db
        self._status: dict = {}
        self._worker: Optional[_Worker] = None
        self._build_ui()
        self.refresh()

    # ----------------------------------------------------------------- ui
    def _build_ui(self):
        root = QVBoxLayout(self)
        head = QHBoxLayout()
        title = QLabel("<b>Tracking Sheets</b>")
        title.setStyleSheet("font-size: 14px;")
        head.addWidget(title, 1)
        helpb = QPushButton("?")
        helpb.setFixedWidth(28)
        helpb.setToolTip("Quick guide to this tab")
        helpb.clicked.connect(lambda: QMessageBox.information(self, "Tracking Sheets", QUICK_GUIDE))
        head.addWidget(helpb)
        root.addLayout(head)

        self.folder_label = QLabel("")
        self.folder_label.setWordWrap(True)
        self.folder_label.setStyleSheet("color: #888;")
        root.addWidget(self.folder_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Cohort", "Status", "Sheet file", "Sheet edited", "Last import"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._show_selected_detail)
        root.addWidget(self.table, 2)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        root.addWidget(self.detail)

        row = QHBoxLayout()
        for text, slot, tip, style in (
            ("Refresh", self.refresh, "Re-read the folder and the import history.", ""),
            ("Import all sheets", self.import_all,
             "Read every cohort's sheet into the database (reads only; never edits a sheet).",
             "background:#16405a; color:white; font-weight:bold;"),
            ("Import selected", self.import_selected,
             "Import only the cohort(s) selected in the table.", ""),
            ("This is the sheet", self.pin_selected,
             "When several files match a cohort, mark the selected cohort's chosen file "
             "as THE sheet (you will be asked which).", ""),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            if style:
                b.setStyleSheet(style)
            b.clicked.connect(slot)
            row.addWidget(b)
        root.addLayout(row)

        row2 = QHBoxLayout()
        for text, slot, tip in (
            ("Set sheets folder...", self.set_folder,
             "Point this machine at the folder holding the tracking workbooks. "
             "Needed once per machine, and again if the folder moves."),
            ("Open folder", self.open_folder, "Open the sheets folder in Explorer."),
            ("New cohort sheet...", self.new_sheet,
             "Create a correctly formatted, empty tracking workbook for a new cohort."),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row2.addWidget(b)
        row2.addStretch()
        root.addLayout(row2)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(160)
        self.log.setPlaceholderText("Import results appear here.")
        root.addWidget(self.log)

    # ------------------------------------------------------------- actions
    def _run(self, fn: Callable[[], dict], on_done):
        if self._worker is not None and self._worker.isRunning():
            self._log("(busy -- wait for the current command to finish)")
            return
        self._worker = _Worker(fn)
        self._worker.done.connect(on_done)
        self._worker.start()

    def refresh(self):
        self._log("Reading sheet status...")
        from ..sheet_sync import status
        self._run(status, self._on_status)

    def _on_status(self, st: dict):
        self._status = st
        if st.get("problem"):
            self.folder_label.setText("[!] " + str(st["problem"]))
            self.table.setRowCount(0)
            self._log(str(st["problem"]))
            return
        self.folder_label.setText("Sheets folder: %s    (import history: %s)"
                                  % (st.get("cnt_sheets_dir"), st.get("ledger")))
        cohorts = st.get("cohorts", [])
        self.table.setRowCount(len(cohorts))
        for i, c in enumerate(cohorts):
            label, color = STATE_TEXT.get(c.get("state"), (c.get("state"), "grey"))
            if c.get("ambiguous"):
                label = "%d files match -- choose (currently: newest)" % len(c["candidates"])
                color = "yellow"
            li = c.get("last_import") or {}
            vals = [c["cohort_id"], label,
                    (c.get("sheet") or "-") + ("  [pinned]" if c.get("pinned") else ""),
                    c.get("sheet_edited") or "-", li.get("finished") or "never"]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j == 1 and color in ("green", "red", "yellow"):
                    it.setForeground(Qt.white)
                    it.setBackground({"green": Qt.darkGreen, "red": Qt.darkRed,
                                      "yellow": Qt.darkYellow}[color])
                self.table.setItem(i, j, it)
        n_bad = sum(1 for c in cohorts if c.get("state") in ("last_import_failed", "error"))
        n_stale = sum(1 for c in cohorts if c.get("state") in ("sheet_newer", "never_imported"))
        self._log("%d cohort(s): %d need importing, %d failed last time."
                  % (len(cohorts), n_stale, n_bad))

    def _selected_cohorts(self) -> list:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        cohorts = self._status.get("cohorts", [])
        return [cohorts[r] for r in rows if r < len(cohorts)]

    def _show_selected_detail(self):
        sel = self._selected_cohorts()
        if not sel:
            self.detail.setText("")
            return
        c = sel[0]
        lines = ["%s: %s" % (c["cohort_id"], c.get("why", ""))]
        if len(c.get("candidates", [])) > 1:
            lines.append("Files matching this cohort: " + "; ".join(
                "%s (edited %s)" % (x["name"], x["edited"]) for x in c["candidates"]))
        li = c.get("last_import") or {}
        if li.get("error"):
            lines.append("Last error: " + str(li["error"]))
        self.detail.setText("\n".join(lines))

    def import_all(self):
        self._log("Importing all sheets... (this reads every workbook; a minute or two)")
        from ..sheet_sync import import_cohorts
        self._run(lambda: import_cohorts(None, triggered_by="gui"), self._on_import)

    def import_selected(self):
        sel = self._selected_cohorts()
        if not sel:
            self._log("Select one or more cohort rows first.")
            return
        ids = [c["cohort_id"] for c in sel]
        self._log("Importing %s..." % ", ".join(ids))
        from ..sheet_sync import import_cohorts
        self._run(lambda: import_cohorts(ids, triggered_by="gui"), self._on_import)

    def _on_import(self, r: dict):
        if r.get("problem"):
            self._log("[!] " + str(r["problem"]))
        for c in r.get("cohorts", []):
            if c.get("success"):
                self._log("OK   %s <- %s  %s" % (c["cohort_id"], c.get("sheet_name"), c.get("imported")))
            else:
                self._log("FAIL %s <- %s  %s" % (c["cohort_id"], c.get("sheet_name"), c.get("error")))
        self.refresh()

    def pin_selected(self):
        sel = self._selected_cohorts()
        if len(sel) != 1:
            self._log("Select exactly one cohort row.")
            return
        c = sel[0]
        names = [x["name"] for x in c.get("candidates", [])]
        if not names:
            self._log("%s has no matching files." % c["cohort_id"])
            return
        choice, ok = QInputDialog.getItem(
            self, "Which file is the sheet?",
            "Files matching %s (newest first):" % c["cohort_id"], names, 0, False)
        if not ok:
            return
        from ..cohort_sheets import pin_cohort_sheet
        self._log("Pinning %s -> %s" % (c["cohort_id"], choice))
        pin_cohort_sheet(c["cohort_id"], choice)
        self.refresh()

    def set_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Folder holding the tracking workbooks")
        if not d:
            return
        if not any(n.lower().endswith(".xlsx") and "tracking" in n.lower() for n in os.listdir(d)):
            QMessageBox.warning(self, "Not the sheets folder",
                                "That folder holds no *_Animal_Tracking.xlsx workbook. "
                                "Pick the folder that contains the tracking workbooks.")
            return
        from ..cohort_sheets import set_cohort_sheets_dir
        self._log("Sheets folder recorded in %s" % set_cohort_sheets_dir(d))
        self.refresh()

    def open_folder(self):
        d = self._status.get("cnt_sheets_dir")
        if d and Path(d).is_dir():
            os.startfile(d)
        else:
            self._log("No sheets folder configured.")

    def new_sheet(self):
        d = self._status.get("cnt_sheets_dir")
        if not d:
            self._log("Set the sheets folder first.")
            return
        cohort, ok = QInputDialog.getText(self, "New cohort sheet", "Cohort name (e.g. CNT_06):")
        if not ok or not cohort.strip():
            return
        start, ok = QInputDialog.getText(self, "New cohort sheet",
                                         "Food-deprivation start date (YYYY-MM-DD):")
        if not ok or not start.strip():
            return
        self._log("Creating sheet for %s in %s ..." % (cohort.strip(), d))
        # Same interpreter, own process: the sheet builder is argparse-driven and
        # writes files; keeping it out of the GUI process keeps the GUI responsive.
        try:
            r = subprocess.run(
                [sys.executable, "-m", "mousedb.cohort_tools.make_sheets", "--new",
                 "--cohort", cohort.strip(), "--start-date", start.strip(), "--output-dir", d],
                capture_output=True, text=True, timeout=600)
            self._log((r.stdout or "").strip()[-1500:] or "(no output)")
            if r.returncode != 0:
                self._log("FAILED: " + (r.stderr or "")[-1500:])
        except Exception as e:
            self._log("FAILED: %s" % e)
        self.refresh()

    def _log(self, msg: str):
        self.log.append(msg)
