"""Setup tab -- where this machine keeps things, set with a folder chooser.

WHY THIS TAB EXISTS
-------------------
None of these locations can live in the code: they are properties of one lab's
machines, and this is a public tool. So they are configuration -- and until this tab
existed, configuration meant a terminal command. That made the terminal a hard
requirement for *installing the tool at all*, which is exactly backwards: the person
who knows where the lab keeps its data is not usually the person who is comfortable
in a command line.

What the tab adds beyond the command:

* It CHECKS each location. A mistyped or moved path is the classic failure here, and
  it normally surfaces hours later as a confusing error inside some other job. Here
  it is a red row saying the folder is not there.
* It says what each location is FOR, in place, so a person can answer without
  knowing the codebase.
* It lists everything wrong in one panel, rather than delivering one error at a time
  as each separate job happens to hit it.

Nothing here touches the database or any lab file. It writes one small settings file,
whose path is shown at the top.
"""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
    QWidget,
)

from .. import config as cfg

QUICK_GUIDE = """\
WHAT THIS TAB IS FOR
Telling this computer where things live. None of it can be built into the tool,
because every one of these is specific to your lab's machines -- which is also why
another lab can install the same tool and point it somewhere completely different.

WHAT YOU MUST SET
* Database folder -- where this tool keeps the database, the exports it writes and
  its logs. It will create what it needs inside.
* Snapshot folder -- where the fast read-only copy of the database lives. Analyses
  read that copy, so they keep working while the pipeline is writing to the real
  database. Put it on this computer's own disk, not on the network share.

WHAT YOU PROBABLY WANT TO SET
* The reaching pipeline folder and the tracking-sheet folder, so this tool can read
  results and the hand-kept records. Both are read-only to this tool.
* Laboratory name, which is written into generated sheets and shared exports.

WHAT THE COLOURS MEAN
* Red -- either something required is missing, or a path points at something that is
  not there (usually a typo, a renamed folder, or a network drive that is not
  connected right now).
* Grey -- not set, and not required.

"From the environment" on a row means something outside this tool is overriding it
for this session. Changing it here will not take effect until that is removed.
"""


class SetupTab(QWidget):
    """Every machine-specific location, shown, checked and editable."""

    def __init__(self, db=None):
        super().__init__()
        self.db = db
        self._build()
        self.reload()

    def _build(self):
        layout = QVBoxLayout(self)

        self.path_label = QLabel()
        self.path_label.setStyleSheet("color: #555;")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        self.problems = QLabel()
        self.problems.setWordWrap(True)
        layout.addWidget(self.problems)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Setting", "Where it points", "Comes from", "What it is for"])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setWordWrap(True)
        layout.addWidget(self.table)

        row = QHBoxLayout()
        for text, slot, tip in (
                ("Choose...", self._choose, "Pick the folder or file for the selected row"),
                ("Type it in...", self._type_in, "Enter the value by hand"),
                ("Clear", self._clear, "Unset the selected row"),
                ("Re-check", self.reload, "Look again -- use after connecting a drive")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        guide = QPushButton("?")
        guide.setFixedWidth(30)
        guide.clicked.connect(self._show_guide)
        row.addWidget(guide)
        layout.addLayout(row)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(80)
        layout.addWidget(self.log)

    # ------------------------------------------------------------------ data
    def reload(self):
        self.path_label.setText("Stored in: %s   (this file stays on this computer; "
                                "it is never part of the tool)" % cfg.CONFIG_PATH)
        entries = cfg.status()
        self._entries = entries
        self.table.setRowCount(len(entries))
        for i, e in enumerate(entries):
            name = QTableWidgetItem(e["key"] + ("  (required)" if e["required"] else ""))
            value = QTableWidgetItem(e["value"] or "not set")
            source = QTableWidgetItem(
                "from the environment" if e["source"].startswith("environment")
                else e["source"])
            help_item = QTableWidgetItem(e["help"])

            bad = (e["required"] and not e["value"]) or e["exists"] is False
            if bad:
                for item in (name, value, source):
                    item.setForeground(Qt.red)
                if e["exists"] is False:
                    value.setText("%s   <-- not there" % e["value"])
            elif not e["value"]:
                for item in (name, value, source):
                    item.setForeground(Qt.gray)

            for col, item in enumerate((name, value, source, help_item)):
                self.table.setItem(i, col, item)
        self.table.resizeColumnsToContents()
        self.table.resizeRowsToContents()

        trouble = cfg.problems()
        if trouble:
            self.problems.setText(
                "<b style='color:#b00'>This machine is not ready:</b><br>"
                + "<br>".join("&bull; %s" % p for p in trouble))
        else:
            self.problems.setText(
                "<b style='color:#070'>Everything needed is set and present.</b>")

    def _selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._entries):
            QMessageBox.information(self, "Pick a setting",
                                    "Select a row in the table first.")
            return None
        return self._entries[row]

    # --------------------------------------------------------------- actions
    def _choose(self):
        e = self._selected()
        if not e:
            return
        if e["kind"] == "text":
            self._type_in()
            return
        start = e["value"] or str(Path.home())
        if e["kind"] == "dir":
            chosen = QFileDialog.getExistingDirectory(self, "Choose the folder for %s"
                                                      % e["key"], start)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, "Choose the file for %s"
                                                    % e["key"], start)
        if chosen:
            self._write(e["key"], chosen)

    def _type_in(self):
        e = self._selected()
        if not e:
            return
        value, ok = QInputDialog.getText(self, e["key"], "%s\n\nValue:" % e["help"],
                                         text=e["value"] or "")
        if ok:
            self._write(e["key"], value.strip())

    def _clear(self):
        e = self._selected()
        if not e:
            return
        if e["required"] and QMessageBox.question(
                self, "Clear a required setting",
                "%s is required -- clearing it will stop this tool working until it "
                "is set again.\n\nClear it anyway?" % e["key"],
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self._write(e["key"], "")

    def _write(self, key: str, value: str):
        try:
            cfg.set_value(key, value or None)
        except Exception as exc:
            QMessageBox.critical(self, "Could not save", str(exc))
            return
        self.log.append("%s = %s" % (key, value or "(cleared)"))
        # Warn immediately rather than at the next job: a path that is not there is
        # almost always a typo or a drive that is not connected, and the person who
        # just typed it is the one who can fix it.
        if value:
            entry = next((e for e in cfg.status() if e["key"] == key), None)
            if entry and entry["exists"] is False:
                QMessageBox.warning(
                    self, "Saved, but nothing is there",
                    "%s was saved, but there is no %s at:\n\n%s\n\nIf that is a "
                    "network drive, connect it and press Re-check."
                    % (key, "folder" if entry["kind"] == "dir" else "file", value))
        self.reload()

    def _show_guide(self):
        box = QMessageBox(self)
        box.setWindowTitle("Setup -- quick guide")
        box.setText(QUICK_GUIDE)
        box.exec_()
