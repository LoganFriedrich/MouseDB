"""Tracking Sheets tab -- import the lab's tracking workbooks from a button.

WHY THIS TAB EXISTS
-------------------
The tracking workbooks are the hand-kept record of every animal (weights,
ramps, injury, surgery, manual tray scores). They are filled in "eventually",
and the database only knows what was imported from them. Importing used to be
a terminal command, or a scheduled background job with no visible result, so
an import that failed and rolled back could go unnoticed for a long time.
This tab is the visible, clickable version: per cohort it shows WHICH
file is the sheet, WHEN it was edited, WHEN it was last imported and whether
that worked -- and lets you import, pick the right file when several match,
forget a pin, set the folder, and create a new blank sheet.

WHAT IT SAYS OUT LOUD
---------------------
Which file is read for a cohort is a decision, and two such decisions used to
be invisible here: a PIN whose file has since been renamed, moved or deleted
(the cohort then quietly reads its newest file instead), and a file that LOOKS
like a sheet but is refused as one ("Copy of ...", an export a tool wrote from
a sheet). Both are listed in a red box above the table, one plain sentence
each, saying what happened and what to do. (cohort_sheets' rule: no status
display may leave a broken pin unsaid. A cohort whose pinned file has gone and
which has no other file has no row at all, so the box is the only place it can
be said.)

NEVER TAKES THE WINDOW DOWN
---------------------------
With no sys.excepthook installed, PyQt5 aborts the WHOLE application when an
exception escapes a slot -- every tab, any unsaved entry. So every button and
every background result here runs through _guarded: a failure becomes a
message box and a log line, and the window stays up. Expected refusals (a pin
on a file renamed since the last refresh, a new sheet for a cohort that has
one) are caught where they happen and explained in plain words.

THE ONE WRITE
-------------
It lives in mousedb's own GUI (mousedb-entry) because importing sheets is
this tool's job; it used to be a tab inside MouseReach that shelled out to
this environment, which made MouseReach depend on a tool it should not know.
All work runs off the GUI thread. Importing only READS the workbooks. The one
thing here that writes into the sheets folder is "New cohort sheet...", and it
only ever creates a NEW file: the name must be exactly <PROJECT>_NN, and it
refuses -- writing nothing -- when that cohort already has a workbook there,
under its exact name or a variant (cohort_tools.make_sheets, "A NEW SHEET
NEVER REPLACES A FILE"). It never closes Excel, and it refuses when the sheets
folder is no longer there, because the builder would recreate the old path.
"""
from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog,
    QInputDialog, QMessageBox, QPlainTextEdit,
)

STATE_TEXT = {
    "up_to_date": ("Up to date", "green"),
    "sheet_newer": ("Sheet edited since last import -- import", "yellow"),
    "never_imported": ("Never imported -- import", "yellow"),
    "last_import_failed": ("LAST IMPORT FAILED -- see message", "red"),
    "no_sheet": ("No sheet found", "grey"),
    "error": ("Status error", "red"),
}

# How long the sheet builder may run before the tab stops waiting for it.
NEW_SHEET_TIMEOUT_S = 600

QUICK_GUIDE = """\
The tracking workbooks (one per cohort, named as the lab settings say for each
project -- "mousedb sheet-projects --show" lists them)
are the lab's hand-kept record: animals, weights, ramps, manual tray scores, injury
and surgery details. The database only knows what has been IMPORTED from them.

WHAT THE TABLE SHOWS, per cohort: which file is being read as the sheet; when that
file was last edited; when it was last imported and whether that worked; and a
plain verdict -- Up to date / Sheet edited since last import / Never imported /
LAST IMPORT FAILED.

THE RED BOX above the table appears only when something about WHICH file is read
needs you:
* PIN BROKEN -- a file you once marked as a cohort's sheet ("This is the sheet")
  has been renamed, moved or deleted, so that cohort now reads its newest file
  instead. Fix: select the cohort, press "This is the sheet" and pick the right
  file -- or press "Forget a pin..." to let the newest file be read. If the cohort
  has no file left at all, put its workbook back in the folder, or forget the pin.
* NOT READ AS A SHEET -- a file in the folder looks like a sheet but is never
  imported (a "Copy of ...", an export a tool made from a sheet, an Excel lock file).
  If it really is a cohort's sheet, rename it so its name STARTS with its
  project's sheet name. Otherwise nothing to do.
* If the folder set for this machine holds NO readable sheet at all (say every
  sheet there was renamed "Copy of ..."), the table stays empty and the line at
  the top says so -- and the red box still lists what is in that folder.

WHAT TO DO:
* Press "Import all sheets" (or select rows and "Import selected"). Progress and
  the outcome per cohort appear in the log box. A failure shows its reason.
* "Sheet edited since last import" means someone changed the workbook after the
  last import -- import again. (The scheduled background job also imports; this
  tab shows you whether it succeeded.)
* "N files match -- choose": more than one workbook claims to be that cohort's
  sheet (a copy, a draft, a "(2)"). Select the row, press "This is the sheet",
  pick the real one. The choice is remembered and shown as [pinned]. If the file
  was renamed since you last pressed Refresh, you are told so and nothing is
  pinned -- the table is re-read; choose again.
* "Set sheets folder" points this machine at the folder holding the workbooks
  (once per machine, and again if the folder ever moves). A folder holding no
  tracking sheet, or an archive/backup copy, is refused and the setting is kept.
  "Open folder" opens it.
* "New cohort sheet" creates an EMPTY workbook in that folder for a NEW cohort,
  made from the project's TEMPLATE: same tabs, columns, formulas and laid-out
  schedule as the cohort sheet the template was made from, with this cohort's
  animals. Type the cohort exactly as PROJECT_NN (e.g. PROJA_07), then the
  subject numbers (01-16, or 3,4,16,17,19 -- animals keep the numbers they were
  given). It refuses -- and writes nothing -- if the name is in any other form,
  if the project has no template yet (it names the command that makes one), or
  if that cohort already has a workbook in the folder (under its exact name or a
  variant such as "...Tracking1.xlsx"). It never replaces a file and never closes
  Excel. It also refuses if the sheets folder has gone (moved, renamed, drive
  offline) since the last Refresh: it never recreates a missing folder.

IMPORTING NEVER EDITS A WORKBOOK. Only "New cohort sheet" writes, and only a new file.
Terminal equivalents: mousedb-sheets status | import | pin | unpin | set-dir,
and mousedb new-sheet PROJECT_NN --animals 01-16 --output-dir <folder>
"""


class _Worker(QThread):
    """Run one Python callable off the GUI thread and hand back its dict,
    tagged with the job number the tab gave it."""
    done = pyqtSignal(int, dict)

    def __init__(self, job: int, fn: Callable[[], dict]):
        super().__init__()
        self._job = job
        self._fn = fn

    def run(self):
        # BaseException, not only Exception. WHY: something deep in a command
        # calling sys.exit() would otherwise leave run() with no result sent --
        # the tab would stay "busy" until restarted -- and an exception leaving
        # run() reaches PyQt, which aborts the whole application.
        try:
            r = self._fn()
            r = r if isinstance(r, dict) else {"result": r}
        except BaseException as e:  # every kind, on purpose -- see above
            r = {"problem": "%s: %s" % (type(e).__name__, e)}
        self.done.emit(self._job, r)


def _set_but_not_accepted_folder() -> Optional[Path]:
    """The sheets folder this machine is SET to (environment variable first,
    then local config -- cohort_sheets_dir's order), when it exists but
    status() does not accept it; else None."""
    from .. import cohort_sheets
    raws = [os.environ.get(getattr(cohort_sheets, "ENV_VAR", "MOUSEDB_COHORT_SHEETS"))]
    reader = getattr(cohort_sheets, "_read_config", None)
    if reader is not None:
        raws.append((reader() or {}).get("cohort_sheets_dir"))
    for raw in raws:
        if raw and Path(raw).is_dir():
            return Path(raw)
    return None


def _status_with_broken_pins() -> dict:
    """sheet_sync.status(), plus EVERY broken pin cohort_sheets knows of.

    WHY the extra list: status() reports a broken pin on its cohort's row, but
    a cohort whose pinned file has gone and that has no other file has no row
    at all -- its pin would go unsaid. cohort_sheets.broken_pins() lists them
    all; read through getattr so the tab still opens if that function moves.

    And WHY the folder status() turned down is looked at too: status() accepts
    a folder only while it holds at least one tracking sheet, and otherwise
    lists nothing in it. A folder whose sheets were ALL renamed to names that
    are refused ("Copy of ...") is exactly when a person needs to see those
    files and the pins they broke, not only "no folder is configured"."""
    from .. import cohort_sheets, sheet_sync
    st = sheet_sync.status()
    lister = getattr(cohort_sheets, "broken_pins", None)
    folder = st.get("cnt_sheets_dir")
    if not folder:
        try:
            raw = _set_but_not_accepted_folder()
            refused = getattr(sheet_sync, "_refused_files", None)
            if raw is not None:
                folder = str(raw)
                st["set_folder_not_accepted"] = folder
                if refused is not None and not st.get("refused_files"):
                    st["refused_files"] = refused(raw)
        except Exception as e:  # a notice must never stop the status listing
            st["broken_pins_problem"] = "the configured folder could not be checked: %s" % e
    if lister is not None and folder:
        try:
            st["all_broken_pins"] = lister(folder)
        except Exception as e:  # a notice must never stop the status listing
            st["broken_pins_problem"] = "the pins could not be checked: %s" % e
    return st


def notice_lines(st: dict) -> List[str]:
    """What the red box says: one line per broken pin and one per file that
    looks like a sheet but is not read as one. Empty when there is nothing.

    A broken pin's sentence comes from the cohort's row ('broken_pin') or, for
    a cohort without a row, from cohort_sheets.broken_pins; each cohort is
    named once."""
    lines: List[str] = []
    said = set()
    for c in st.get("cohorts") or []:
        if c.get("broken_pin") and c.get("cohort_id") not in said:
            lines.append("PIN BROKEN: %s" % c["broken_pin"])
            said.add(c.get("cohort_id"))
    for b in st.get("all_broken_pins") or []:
        if b.get("cohort_id") not in said:
            lines.append("PIN BROKEN: %s" % (b.get("why") or (
                "%s is pinned to %s, which is no longer one of its files"
                % (b.get("cohort_id"), b.get("pinned")))))
            said.add(b.get("cohort_id"))
    if st.get("broken_pins_problem"):
        lines.append("[!] %s" % st["broken_pins_problem"])
    for f in st.get("refused_files") or []:
        lines.append("NOT READ AS A SHEET: %s -- %s" % (f.get("file"), f.get("reason")))
    return lines


class TrackingSheetsTab(QWidget):
    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.db = db
        self._status: dict = {}
        self._busy = False
        self._next_job = 0
        self._jobs: Dict[int, Callable[[dict], None]] = {}
        self._threads: List[_Worker] = []
        self._build_ui()
        self._guarded(self.refresh)

    # ----------------------------------------------------------------- ui
    def _button(self, text: str, slot: Callable, tip: str, style: str = "") -> QPushButton:
        b = QPushButton(text)
        b.setToolTip(tip)
        if style:
            b.setStyleSheet(style)
        # clicked() passes a "checked" flag; it is dropped, and the slot runs
        # guarded (see the module docstring, NEVER TAKES THE WINDOW DOWN).
        b.clicked.connect(lambda _checked=False, f=slot: self._guarded(f))
        return b

    def _build_ui(self):
        root = QVBoxLayout(self)
        head = QHBoxLayout()
        title = QLabel("<b>Tracking Sheets</b>")
        title.setStyleSheet("font-size: 14px;")
        head.addWidget(title, 1)
        helpb = self._button("?", lambda: QMessageBox.information(self, "Tracking Sheets", QUICK_GUIDE),
                             "Quick guide to this tab")
        helpb.setFixedWidth(28)
        head.addWidget(helpb)
        root.addLayout(head)

        self.folder_label = QLabel("")
        self.folder_label.setWordWrap(True)
        self.folder_label.setTextFormat(Qt.PlainText)
        self.folder_label.setStyleSheet("color: #888;")
        root.addWidget(self.folder_label)

        # The red box: broken pins and files refused as sheets, in full words.
        # Plain text (a file name may hold '<'), selectable so a name can be
        # copied, hidden while there is nothing to say.
        self.notices = QLabel("")
        self.notices.setWordWrap(True)
        self.notices.setTextFormat(Qt.PlainText)
        self.notices.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.notices.setStyleSheet(
            "background:#5a1616; color:white; padding:6px; border-radius:3px;")
        self.notices.setVisible(False)
        root.addWidget(self.notices)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Cohort", "Status", "Sheet file", "Sheet edited", "Last import"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemSelectionChanged.connect(lambda: self._guarded(self._show_selected_detail))
        root.addWidget(self.table, 2)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.PlainText)
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
            ("Forget a pin...", self.forget_pin,
             "Stop treating a chosen file as a cohort's sheet; the cohort's newest file is "
             "read again. Use it for a PIN BROKEN line in the red box.", ""),
        ):
            row.addWidget(self._button(text, slot, tip, style))
        root.addLayout(row)

        row2 = QHBoxLayout()
        for text, slot, tip in (
            ("Set sheets folder...", self.set_folder,
             "Point this machine at the folder holding the tracking workbooks. "
             "Needed once per machine, and again if the folder moves."),
            ("Open folder", self.open_folder, "Open the sheets folder in Explorer."),
            ("New cohort sheet...", self.new_sheet,
             "Create a correctly formatted, empty tracking workbook for a NEW cohort. "
             "Refuses (writes nothing) if that cohort already has a workbook."),
        ):
            row2.addWidget(self._button(text, slot, tip))
        row2.addStretch()
        root.addLayout(row2)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(160)
        self.log.setPlaceholderText("Import results appear here.")
        root.addWidget(self.log)

    # --------------------------------------------------------- plumbing
    def _guarded(self, fn: Callable, *args):
        """Run ``fn(*args)`` so that no exception reaches Qt (see the module
        docstring, NEVER TAKES THE WINDOW DOWN). An unexpected failure is
        logged with its traceback and shown in a message box."""
        try:
            return fn(*args)
        except Exception as e:
            what = getattr(fn, "__name__", "that action")
            try:
                self._log("[!] %s stopped: %s: %s" % (what, type(e).__name__, e))
                self._log(traceback.format_exc()[-1500:])
                QMessageBox.warning(
                    self, "Tracking Sheets",
                    "That did not work:\n\n%s: %s\n\nThe window is still running; the "
                    "details are in the log box at the bottom of this tab."
                    % (type(e).__name__, e))
            except Exception:
                pass
            return None

    def _run(self, fn: Callable[[], dict], on_done: Callable[[dict], None]) -> bool:
        """Start ``fn`` off the GUI thread; ``on_done(result)`` runs on the GUI
        thread when it finishes. One command at a time: returns False (and
        says so in the log) while another is still running."""
        if self._busy:
            self._log("(busy -- wait for the current command to finish)")
            return False
        self._next_job += 1
        job = self._next_job
        self._jobs[job] = on_done
        worker = _Worker(job, fn)
        worker.done.connect(self._job_done)
        # Every started thread stays referenced until it has finished. WHY: a
        # QThread object garbage-collected while its thread still runs aborts
        # the application, and the next command (the refresh after an import)
        # starts before the previous thread has fully exited.
        self._threads = [t for t in self._threads if not t.isFinished()]
        self._threads.append(worker)
        self._busy = True
        worker.start()
        return True

    def _job_done(self, job: int, r: dict):
        # Not busy any more BEFORE the callback, so a callback that refreshes
        # the table is not turned away as "busy".
        self._busy = False
        on_done = self._jobs.pop(job, None)
        if on_done is not None:
            self._guarded(on_done, r)

    def _folder_projects(self) -> List[str]:
        """The projects whose sheets the sheets folder holds: those with a sheet
        name in the lab settings (sheet_projects). The only cohorts a new sheet
        may be made for here, since the folder reads only those names."""
        from .. import sheet_projects
        try:
            return sorted(sheet_projects.definitions())
        except ValueError:
            return []

    # ------------------------------------------------------------- status
    def refresh(self):
        self._log("Reading sheet status...")
        self._run(_status_with_broken_pins, self._on_status)

    def _show_notices(self, lines: List[str]):
        if not lines:
            self.notices.setText("")
            self.notices.setVisible(False)
            return
        text = "Which files are read -- needs your attention:\n" + "\n".join(
            "* " + ln for ln in lines)
        if any(ln.startswith("PIN BROKEN") for ln in lines):
            text += ("\n(Here: select the cohort and press 'This is the sheet', or press "
                     "'Forget a pin...' -- the same as 'mousedb-sheets unpin'.)")
        self.notices.setText(text)
        self.notices.setVisible(True)

    def _on_status(self, st: dict):
        self._status = st if isinstance(st, dict) else {}
        st = self._status
        notices = notice_lines(st)
        self._show_notices(notices)
        if st.get("problem"):
            self.folder_label.setText("[!] " + str(st["problem"]) + (
                "  (Set to: %s -- anything there that looks like a sheet but is not "
                "read is listed in the red box.)" % st["set_folder_not_accepted"]
                if st.get("set_folder_not_accepted") else ""))
            self.table.setRowCount(0)
            self._log(str(st["problem"]))
            return
        self.folder_label.setText("Sheets folder: %s    (import history: %s)"
                                  % (st.get("cnt_sheets_dir"), st.get("ledger")))
        cohorts = st.get("cohorts") or []
        self.table.setRowCount(len(cohorts))
        for i, c in enumerate(cohorts):
            label, color = STATE_TEXT.get(c.get("state"), (c.get("state") or "?", "grey"))
            if c.get("ambiguous"):
                label = "%d files match -- choose (currently: newest)" % len(c.get("candidates") or [])
                color = "yellow"
            sheet_text = c.get("sheet") or "-"
            if c.get("pinned"):
                sheet_text += "  [pinned]"
            if c.get("broken_pin"):
                sheet_text += "  [PIN BROKEN -- see the red box]"
            li = c.get("last_import") or {}
            vals = [c.get("cohort_id", "?"), label, sheet_text,
                    c.get("sheet_edited") or "-", li.get("finished") or "never"]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j == 1 and color in ("green", "red", "yellow"):
                    it.setForeground(Qt.white)
                    it.setBackground({"green": Qt.darkGreen, "red": Qt.darkRed,
                                      "yellow": Qt.darkYellow}[color])
                if j == 2 and c.get("broken_pin"):
                    it.setForeground(Qt.white)
                    it.setBackground(Qt.darkRed)
                    it.setToolTip(str(c["broken_pin"]))
                self.table.setItem(i, j, it)
        n_bad = sum(1 for c in cohorts if c.get("state") in ("last_import_failed", "error"))
        n_stale = sum(1 for c in cohorts if c.get("state") in ("sheet_newer", "never_imported"))
        n_pins = sum(1 for ln in notices if ln.startswith("PIN BROKEN"))
        n_refused = len(st.get("refused_files") or [])
        msg = "%d cohort(s): %d need importing, %d failed last time." % (
            len(cohorts), n_stale, n_bad)
        if n_pins or n_refused:
            msg += " %d broken pin(s), %d file(s) not read as a sheet -- see the red box." % (
                n_pins, n_refused)
        self._log(msg)

    def _selected_cohorts(self) -> list:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        cohorts = self._status.get("cohorts") or []
        return [cohorts[r] for r in rows if r < len(cohorts)]

    def _show_selected_detail(self):
        sel = self._selected_cohorts()
        if not sel:
            self.detail.setText("")
            return
        c = sel[0]
        lines = ["%s: %s" % (c.get("cohort_id"), c.get("why", ""))]
        if c.get("broken_pin"):
            lines.append("PIN BROKEN: %s" % c["broken_pin"])
        if len(c.get("candidates") or []) > 1:
            lines.append("Files matching this cohort: " + "; ".join(
                "%s (edited %s)" % (x.get("name"), x.get("edited")) for x in c["candidates"]))
        li = c.get("last_import") or {}
        if li.get("error"):
            lines.append("Last error: " + str(li["error"]))
        self.detail.setText("\n".join(lines))

    # ------------------------------------------------------------- import
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
        # The same per-cohort lines as 'mousedb-sheets import' (OK/FAIL, what
        # landed or why not, warnings, files not read, one closing verdict),
        # so the tab and the terminal never tell two stories.
        from .. import sheet_sync
        lines: List[str] = []

        def log(msg):
            lines.append(str(msg))
            self._log(msg)

        printer = getattr(sheet_sync, "print_import_result", None)
        if printer is not None:
            ok = printer(r, log=log)
        else:
            if r.get("problem"):
                log("[!] " + str(r["problem"]))
            for c in r.get("cohorts", []):
                log("%s %s <- %s  %s" % ("OK  " if c.get("success") else "FAIL", c.get("cohort_id"),
                                         c.get("sheet_name"),
                                         c.get("imported") if c.get("success") else c.get("error")))
            ok = not r.get("problem") and all(c.get("success") for c in r.get("cohorts", []))
        if not ok:
            # Said where a person sees it without reading the log (the closing
            # line is the verdict); the log box above has the per-cohort reasons.
            QMessageBox.warning(
                self, "Import -- not everything was imported",
                "%s\n\nThe log box at the bottom of this tab says which cohort failed "
                "and why." % (lines[-1] if lines else "The import did not finish."))
        self.refresh()

    # --------------------------------------------------------------- pins
    def pin_selected(self):
        sel = self._selected_cohorts()
        if len(sel) != 1:
            self._log("Select exactly one cohort row.")
            return
        c = sel[0]
        if c.get("project") is not None and c.get("project") not in self._folder_projects():
            QMessageBox.information(
                self, "Nothing to choose",
                "%s keeps one workbook per cohort in its own folder; there is no "
                "choice of file to make." % c.get("cohort_id"))
            return
        names = [x["name"] for x in c.get("candidates") or []]
        if not names:
            self._log("%s has no matching files." % c.get("cohort_id"))
            return
        choice, ok = QInputDialog.getItem(
            self, "Which file is the sheet?",
            "Files matching %s (newest first):" % c["cohort_id"], names, 0, False)
        if not ok:
            return
        from ..cohort_sheets import pin_cohort_sheet
        self._log("Pinning %s -> %s" % (c["cohort_id"], choice))
        try:
            pin_cohort_sheet(c["cohort_id"], choice)
        except ValueError as e:
            # pin_cohort_sheet refuses a file that cannot be the cohort's sheet
            # -- most often one renamed or deleted since the table was last read.
            self._log("[FAIL] %s -- nothing was pinned" % e)
            QMessageBox.warning(
                self, "Not pinned",
                "%s\n\nNothing was pinned. The table is being re-read now; select "
                "the cohort and choose again from the files that are there." % e)
            self.refresh()
            return
        except OSError as e:
            self._log("[FAIL] could not save the pin: %s" % e)
            QMessageBox.warning(self, "Not pinned",
                                "The choice could not be saved: %s\n\nNothing was pinned." % e)
            return
        self.refresh()

    def forget_pin(self):
        """Forget a cohort's pin -- the button form of 'mousedb-sheets unpin'.
        WHY a button: a PIN BROKEN line's own advice includes unpinning, and a
        cohort with no file left has no row to re-pin from; routine lab work
        never needs a terminal."""
        pins: Dict[str, str] = {}
        for c in self._status.get("cohorts") or []:
            if c.get("pinned"):
                pins[c["cohort_id"]] = "%s  (pinned to %s)" % (c["cohort_id"], c["pinned"])
            elif c.get("broken_pin"):
                pins[c["cohort_id"]] = "%s  (pin BROKEN)" % c["cohort_id"]
        for b in self._status.get("all_broken_pins") or []:
            if b.get("cohort_id"):
                pins[b["cohort_id"]] = "%s  (pinned to %s -- BROKEN)" % (b["cohort_id"], b.get("pinned"))
        if not pins:
            QMessageBox.information(self, "Forget a pin",
                                    "No cohort is pinned, so there is no pin to forget.")
            return
        ids = sorted(pins)
        items = [pins[k] for k in ids]
        choice, ok = QInputDialog.getItem(
            self, "Forget a pin",
            "Forget which cohort's pin? That cohort then reads its newest file.",
            items, 0, False)
        if not ok:
            return
        cohort_id = ids[items.index(choice)]
        from ..cohort_sheets import pin_cohort_sheet, pinned_sheet
        was = pinned_sheet(cohort_id)
        try:
            pin_cohort_sheet(cohort_id, None)
        except (ValueError, OSError) as e:
            self._log("[FAIL] %s -- nothing was unpinned" % e)
            QMessageBox.warning(self, "Not unpinned", "%s\n\nNothing was unpinned." % e)
            self.refresh()
            return
        self._log("Unpinned %s%s; its newest file is read from now on."
                  % (cohort_id, " (was %s)" % was if was else ""))
        self.refresh()

    # -------------------------------------------------------------- folder
    def _folder_refusal(self, folder: Path) -> Optional[str]:
        """Why ``folder`` cannot be the sheets folder, or None -- the tests of
        'mousedb-sheets set-dir' (sheet_sync._not_the_sheets_folder)."""
        from .. import sheet_sync
        check = getattr(sheet_sync, "_not_the_sheets_folder", None)
        if check is not None:
            return check(folder)
        from ..cohort_sheets import available_cohorts
        if not folder.is_dir():
            return "%s is not a folder" % folder
        if not available_cohorts(folder):
            return "%s holds no tracking sheet (named as 'mousedb sheet-projects --show' lists)" % folder
        return None

    def set_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Folder holding the tracking workbooks")
        if not d:
            return
        # Judged and saved as an ABSOLUTE path, by the same tests as
        # 'mousedb-sheets set-dir'. WHY: this button used to accept any folder
        # with "tracking" in a workbook's name -- a folder of refused copies or
        # an archive snapshot was saved, and every status afterwards said "not
        # configured" with no hint that the setting itself was the problem.
        folder = Path(os.path.abspath(d))
        why = self._folder_refusal(folder)
        if why:
            from .. import sheet_sync
            lister = getattr(sheet_sync, "_refused_files", None)
            refused = lister(folder) if (lister is not None and folder.is_dir()) else []
            extra = "".join("\n  - %s: %s" % (f.get("file"), f.get("reason")) for f in refused)
            self._log("[FAIL] %s; the sheets folder setting was not changed" % why)
            QMessageBox.warning(
                self, "Not the sheets folder",
                "%s.\n\nThe sheets folder setting was not changed. Pick the folder that "
                "holds the live tracking workbooks.%s"
                % (why, ("\n\nFiles there that look like sheets but are not read:" + extra)
                        if extra else ""))
            return
        from .. import cohort_sheets
        self._log("Sheets folder %s recorded in %s"
                  % (folder, cohort_sheets.set_cohort_sheets_dir(folder)))
        # WHY this check: an environment variable naming a usable folder takes
        # precedence over the recorded setting (cohort_sheets_dir), so the
        # choice just made would silently change nothing.
        in_force = cohort_sheets.cohort_sheets_dir()
        if in_force is not None and (os.path.normcase(os.path.abspath(str(in_force)))
                                     != os.path.normcase(str(folder))):
            var = getattr(cohort_sheets, "ENV_VAR", "MOUSEDB_COHORT_SHEETS")
            self._log("[!] but the sheets are still read from %s (%s)" % (in_force, var))
            QMessageBox.warning(
                self, "Recorded -- but another folder is in use",
                "%s was recorded, but the sheets are still read from:\n\n%s\n\nbecause "
                "the %s environment variable on this machine names that folder, and it "
                "comes first. Clear or change that variable for this setting to take "
                "effect." % (folder, in_force, var))
        self.refresh()

    def open_folder(self):
        d = self._status.get("cnt_sheets_dir")
        if not (d and Path(d).is_dir()):
            self._log("No sheets folder configured.")
            return
        try:
            os.startfile(d)
        except (OSError, AttributeError) as e:  # AttributeError: not Windows
            QMessageBox.information(self, "Open folder",
                                    "Could not open the folder (%s). It is:\n\n%s" % (e, d))

    # ----------------------------------------------------------- new sheet
    def _refuse_new_sheet(self, reason: str):
        self._log("[REFUSED] new cohort sheet: %s -- nothing was written" % reason)
        QMessageBox.warning(self, "New cohort sheet -- not created",
                            "%s\n\nNothing was written." % reason)

    def new_sheet(self):
        """Create a NEW cohort's empty workbook in the sheets folder -- the one
        write this tab makes. Everything that can be refused is refused HERE,
        before anything runs, in a message box (and the builder refuses the
        same things again, whoever calls it): see the module docstring."""
        d = self._status.get("cnt_sheets_dir")
        if not d:
            QMessageBox.information(
                self, "New cohort sheet",
                "No sheets folder is set yet. Press 'Set sheets folder...' first; the "
                "new workbook is created in that folder.")
            return
        if not Path(d).is_dir():
            # WHY: the builder creates a missing output folder. Had the sheets
            # folder been moved, renamed or gone offline since the last
            # Refresh, it would recreate the OLD path holding one empty sheet,
            # the setting would then point there, and every real sheet would
            # drop out of view (and on a synced drive the old folder would be
            # recreated for everyone).
            self._refuse_new_sheet(
                "The sheets folder %s is not there any more (moved, renamed, or its "
                "drive is offline). Press Refresh; if it moved, use 'Set sheets "
                "folder...' first." % d)
            self.refresh()
            return
        from ..cohort_tools import make_sheets as ms
        from .. import sheet_templates as st
        projects = self._folder_projects()
        example = (projects or ["PROJ"])[0]
        text, ok = QInputDialog.getText(
            self, "New cohort sheet",
            "Name of the NEW cohort, exactly as PROJECT_NN (for example %s_07).\n"
            "Projects with sheets here: %s" % (example, ", ".join(projects) or "none set"))
        if not ok:
            return
        try:
            # WHY all of this before anything runs: a project with no template,
            # or a cohort that already has a workbook, is said at once in a box
            # rather than after a minute of building. The builder checks again.
            project, cohort_id = st.cohort_of(text)
            st.refuse_if_cohort_has_a_workbook(d, cohort_id)
            st.template_path(project)
        except ms.NewSheetRefused as e:
            self._refuse_new_sheet(str(e))
            return
        nums, ok = QInputDialog.getText(
            self, "New cohort sheet",
            "%s: the subject numbers, as a range or a list\n"
            "(for example 01-16, or 3,4,16,17,19):" % cohort_id)
        if not ok:
            return
        try:
            animals = st.parse_animals(nums, cohort_id)
        except ms.NewSheetRefused as e:
            self._refuse_new_sheet(str(e))
            return
        filename = project.file_name(cohort_id.rsplit("_", 1)[1])
        target = Path(d) / filename
        answer = QMessageBox.question(
            self, "New cohort sheet",
            "Create a new, EMPTY tracking workbook for %s?\n\n"
            "File:     %s\nFolder:   %s\nAnimals:  %d (%s ... %s)\nTemplate: %s\n\n"
            "An existing workbook is never replaced: if one for %s turns up in the "
            "folder first, nothing is written." % (
                cohort_id, filename, d, len(animals), animals[0], animals[-1],
                project.template.name, cohort_id),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return

        cmd = [sys.executable, "-m", "mousedb.cli", "new-sheet", cohort_id,
               "--animals", nums, "--output-dir", str(d)]
        refused_exit = ms.REFUSED_EXIT

        def job() -> dict:
            # Same interpreter, own process: the builder is a command-line tool
            # that prints as it goes, and keeping it out of the GUI process
            # keeps a builder failure out of the window. stdin is closed so a
            # question it might ask can never hang it; output is read as UTF-8
            # (PYTHONIOENCODING) so a folder name outside the console's code
            # page cannot crash it mid-way.
            if not Path(d).is_dir():
                # Checked again here: the confirmation box may have stood open
                # for a while (see new_sheet on why a missing folder refuses).
                return {"returncode": refused_exit, "stdout": "", "target": str(target),
                        "refused_exit": refused_exit,
                        "stderr": "REFUSED: the sheets folder %s is not there any more "
                                  "(moved, renamed, or its drive is offline)." % d}
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=NEW_SHEET_TIMEOUT_S, env=env,
                               stdin=subprocess.DEVNULL,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return {"returncode": r.returncode, "stdout": r.stdout or "",
                    "stderr": r.stderr or "", "target": str(target),
                    "refused_exit": refused_exit}

        self._log("Creating %s in %s ... (up to a minute)" % (filename, d))
        if not self._run(job, self._on_new_sheet):
            QMessageBox.information(
                self, "New cohort sheet",
                "Another command is still running in this tab. Wait for it to finish "
                "(see the log box), then press 'New cohort sheet...' again.")

    def _on_new_sheet(self, r: dict):
        target = Path(r.get("target") or ".")
        if r.get("problem"):
            self._log("FAILED: %s" % r["problem"])
            QMessageBox.critical(
                self, "New cohort sheet",
                "The sheet builder did not finish: %s\n\nNo existing workbook was touched "
                "(the builder never replaces one). The table is being re-read; check "
                "whether %s is there." % (r["problem"], target.name))
            self.refresh()
            return
        out = (r.get("stdout") or "").strip()
        err = (r.get("stderr") or "").strip()
        if out:
            self._log(out[-1500:])
        rc = r.get("returncode")
        if rc == r.get("refused_exit"):
            reason = "\n".join(ln[len("REFUSED:"):].strip() for ln in err.splitlines()
                               if ln.startswith("REFUSED:"))
            self._refuse_new_sheet(reason or err[-800:] or "(the builder gave no reason)")
        elif rc == 0 and target.is_file():
            self._log("Created %s" % target)
            QMessageBox.information(
                self, "New cohort sheet",
                "Created %s in the sheets folder.\n\nFill it in as the cohort runs; it is "
                "imported like every other sheet." % target.name)
        else:
            if err:
                self._log("FAILED: " + err[-1500:])
            QMessageBox.critical(
                self, "New cohort sheet",
                "Creating the sheet failed (exit code %s%s).\n\nNo existing workbook was "
                "touched: the builder never replaces one.\n\n%s" % (
                    rc, "" if rc else "; the file is not there", err[-800:]))
        self.refresh()

    def _log(self, msg: str):
        self.log.appendPlainText(str(msg))
