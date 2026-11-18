"""The "Make a limited export..." window of the Where Is My Data tab.

WHY THIS EXISTS
---------------
A limited export (mousedb.exporters.subsets) is routine lab work -- "these
cohorts, only the sessions just before the injury, only these columns" -- and
routine lab work never needs a terminal. This window builds the same
definition the command ``mousedb export-subset`` takes, saves it by name in the
lab settings file, and makes the slice by starting the exporter as a program of
its own (the same reason as "Refresh exports now": the code that runs is the
code installed, never an older copy this window holds in memory).

Everything the window offers comes from the current exports themselves: the
cohorts are the cohort folders there, the columns are the entries of each
kind's data dictionary. A column that is not in the full export cannot be
picked, and one typed into a saved definition by hand is refused by name when
the slice is made.

A SAVED DEFINITION OPENED AND SAVED AGAIN COMES BACK AS IT WAS, except for what
the person changed. WHY: the window shows only what the current exports hold,
and a definition can say more than that -- a column the full export has since
renamed, a cohort no longer in the exports, "every cohort of a project, future
ones included", a description written by hand. Rebuilt from the boxes alone,
such a definition was silently rewritten on Save (the stale column dropped, or
the column list turned into "every column"; the project turned into a fixed
cohort list; the description lost) while the person was told "Saved". So a
column or cohort the exports no longer show is listed, ticked and marked "(not
in the current exports)" -- untick it to drop it, and the slice refuses it by
name until then -- a column choice whose ticks were not changed is kept exactly
(order included), a project definition stays a project definition while its
cohort ticks are left as they were, and the description is carried through.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, QThread
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QListWidget, QListWidgetItem, QCheckBox, QRadioButton, QSpinBox,
    QTextEdit, QMessageBox, QGroupBox, QButtonGroup,
)

from ..exporters import layout, subsets

KIND_WORDS = {
    "ODC_reaches": "Per reach, complete record (ODC_reaches)",
    "reaches_summary": "Per reach, plain words, to share (reaches_..._summary)",
    "reaches_analysis": "Per reach, for statistics (reaches_..._analysis)",
    "sessions_analysis": "Per tray session, for statistics (sessions_..._analysis)",
    "ODC_sessions": "Per animal-day, ODC shape (ODC_sessions)",
}
TRAYS = (("P", "Pillar"), ("E", "Easy"), ("F", "Flat"))
NEW = "(a new limited export)"
BLANK_RULE = "(none -- leave it blank)"
# Shown after a column or cohort a saved definition names that the current
# exports do not have (see the module notes).
NOT_THERE = "  (not in the current exports)"
NAME_ROLE = Qt.UserRole            # the real column or cohort name of a list item
NOT_THERE_ROLE = Qt.UserRole + 1   # True on such an item

QUICK_GUIDE = """\
A LIMITED EXPORT is a copy of part of the current exports: the cohorts, sessions,
tray types and columns you choose. Every value is copied unchanged from the full
export -- nothing is calculated again -- so it always agrees with the full export.

1. Pick a saved one from the list at the top, or start a new one and give it a
   name (letters, digits, - and _; it becomes the folder name).
2. Tick the cohorts, and the tray types (none ticked = all).
3. Sessions: all of them; the last N sessions of one tray type before the
   cohort's first injury (from the tracking sheet: each animal's last N dates
   with hand scores for that tray, before the earliest injury date of its
   cohort); or a range of dates.
4. Tick the kinds of file you want. For each kind you can untick columns
   ("Columns of" picks the kind); all ticked = every column. A column or
   cohort marked "(not in the current exports)" is one the saved definition
   names that the exports no longer have: untick it to take it out.
5. Optional: a yes/no column per animal. Give it a name; pick a rule, or leave
   it blank to fill in later.
6. "Save" keeps the definition (in the lab settings, for everyone). If another
   definition is already saved under that name, you are asked before it is
   replaced, and the replaced one is kept. "Save and make now" also makes the
   files. "Check only" counts the rows of what the
   window shows now and writes nothing -- it does not save the definition
   either.

The files go to exports\\subsets\\<name>\\: the data files, their data
dictionaries (only the kept columns), analysis_EXCLUSIONS.csv for these
sessions, SUBSET_MANIFEST.json (what was chosen, from which export, row counts)
and README.txt. Making it again moves the previous files to
_archived\\exports_subsets\\<name>\\<time>\\ -- never deleted. If making it
again fails part way (a file open in another program, a full share), the
previous files are put back and you are told; a NOT_COMPLETE.txt in the folder
means even that failed: make it again. Saved limited
exports with "remake nightly" ticked are made again after the nightly export
whenever anything they hold has changed.
Full guide: docs/WHERE_IS_MY_DATA.md, "Limited exports".
"""


def run_subset_program(name: str, dry_run: bool = False,
                       definition_file: Optional[Path] = None,
                       timeout_seconds: Optional[float] = None) -> dict:
    """Make (or check) the saved limited export ``name`` in a program of its own
    (``python -m mousedb.exporters.subsets <name> --run --no-wait``); with
    ``definition_file``, the definition in that file instead of the saved one.
    Returns {"returncode", "busy", "refused", "output"}. See data_status_tab.
    run_exports_program for why a fresh program and why this working folder."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").is_file():
        exe = exe.parent / "python.exe"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    cmd = [str(exe), "-m", "mousedb.exporters.subsets", name,
           "--dry-run" if dry_run else "--run", "--no-wait"]
    if definition_file:
        cmd += ["--definition-file", str(definition_file)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL, creationflags=flags,
                          cwd=str(exe.parent), timeout=timeout_seconds)
    out = proc.stdout.decode("utf-8", errors="replace")
    return {"returncode": proc.returncode, "busy": proc.returncode == subsets.EXIT_BUSY,
            "refused": proc.returncode == subsets.EXIT_REFUSED, "output": out}


def profile_from_form(form: dict) -> dict:
    """The definition the window's fields describe, in the shape
    subsets.save_profile takes. ``form`` holds plain values (see
    LimitedExportDialog._form), so this is testable without a window."""
    p: dict = {"cohorts": list(form["cohorts"]), "tray_types": list(form["tray_types"]),
               "kinds": list(form["kinds"]), "nightly": bool(form.get("nightly", True))}
    w = form.get("window") or "all"
    if w == "last_before_injury":
        p["window"] = {"type": w, "n": int(form["n"]), "tray_type": form["window_tray"]}
    elif w == "dates":
        p["window"] = {"type": w, "from": form.get("from") or None, "to": form.get("to") or None}
    else:
        p["window"] = {"type": "all"}
    cols = {k: list(v) for k, v in (form.get("columns") or {}).items() if v and k in p["kinds"]}
    if cols:
        p["columns"] = cols
    if (form.get("flag_column") or "").strip():
        p["flag"] = {"column": form["flag_column"].strip(), "rule": form.get("flag_rule") or None}
    # Carried from the definition the window opened (LimitedExportDialog._form):
    # "every cohort of this project, future ones included", and the description.
    if form.get("project"):
        p["project"] = form["project"]
    if form.get("description"):
        p["description"] = form["description"]
    return p


def _item(name: str, checked: bool, not_there: bool = False) -> QListWidgetItem:
    """A tickable list item carrying its real name; one the current exports do
    not have says so in its text and tooltip."""
    it = QListWidgetItem(name + (NOT_THERE if not_there else ""))
    it.setData(NAME_ROLE, name)
    it.setData(NOT_THERE_ROLE, bool(not_there))
    if not_there:
        it.setToolTip("The saved definition names this, but the current exports do not have "
                      "it. Untick it to take it out of the definition; while it is in, making "
                      "the limited export says so.")
    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
    it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
    return it


def _dictionary_columns(current: Path, kind: str) -> List[str]:
    """The kind's columns, in the full export's dictionary order."""
    import pandas as pd
    try:
        d = pd.read_csv(Path(current) / ("%s_DATA_DICTIONARY.csv" % kind), dtype=str,
                        keep_default_na=False, usecols=["VariableName"])
        return list(d["VariableName"])
    except Exception:
        return []


class _Job(QThread):
    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self.result: dict = {}

    def run(self):
        try:
            self.result = self._fn()
        except Exception as e:
            self.result = {"returncode": 1, "output": "%s: %s" % (type(e).__name__, e)}


class LimitedExportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Make a limited export")
        self.resize(820, 760)
        self._job: Optional[_Job] = None
        try:
            self.current = layout.current_root()
        except Exception:
            self.current = None
        self._columns: Dict[str, Optional[List[str]]] = {}   # kind -> kept (None: all)
        self._shown_kind: Optional[str] = None
        # The saved definition the window opened, and the cohorts it ticked: a
        # project or description is carried from it on Save (see module notes).
        self._loaded: dict = {}
        self._loaded_ticks: set = set()
        self._build()
        self._load_names()

    # ------------------------------------------------------------------ UI
    def _build(self):
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Limited export:"))
        self.saved = QComboBox()
        self.saved.currentTextChanged.connect(self._pick_saved)
        top.addWidget(self.saved, 1)
        top.addWidget(QLabel("Name:"))
        self.name = QLineEdit()
        top.addWidget(self.name, 1)
        helpb = QPushButton("?")
        helpb.setFixedWidth(28)
        helpb.clicked.connect(lambda: QMessageBox.information(self, "Limited exports", QUICK_GUIDE))
        top.addWidget(helpb)
        root.addLayout(top)

        grid = QGridLayout()
        cbox = QGroupBox("Cohorts")
        cl = QVBoxLayout(cbox)
        self.cohorts = QListWidget()
        for c in self._cohorts_on_disk():
            self.cohorts.addItem(_item(c, False))
        cl.addWidget(self.cohorts)
        grid.addWidget(cbox, 0, 0, 2, 1)

        tbox = QGroupBox("Tray types (none ticked = all)")
        tl = QHBoxLayout(tbox)
        self.trays = {}
        for code, word in TRAYS:
            cb = QCheckBox(word)
            self.trays[code] = cb
            tl.addWidget(cb)
        grid.addWidget(tbox, 0, 1)

        sbox = QGroupBox("Sessions")
        sl = QGridLayout(sbox)
        self.w_all = QRadioButton("All sessions")
        self.w_last = QRadioButton("The last")
        self.w_dates = QRadioButton("From")
        grp = QButtonGroup(self)
        for b in (self.w_all, self.w_last, self.w_dates):
            grp.addButton(b)
        self.w_all.setChecked(True)
        self.n = QSpinBox()
        self.n.setRange(1, 99)
        self.n.setValue(3)
        self.w_tray = QComboBox()
        for code, word in TRAYS:
            self.w_tray.addItem(word, code)
        self.d_from, self.d_to = QLineEdit(), QLineEdit()
        self.d_from.setPlaceholderText("YYYY-MM-DD")
        self.d_to.setPlaceholderText("YYYY-MM-DD")
        sl.addWidget(self.w_all, 0, 0, 1, 4)
        sl.addWidget(self.w_last, 1, 0)
        sl.addWidget(self.n, 1, 1)
        sl.addWidget(self.w_tray, 1, 2)
        sl.addWidget(QLabel("sessions before the cohort's first injury"), 1, 3)
        sl.addWidget(self.w_dates, 2, 0)
        sl.addWidget(self.d_from, 2, 1)
        sl.addWidget(QLabel("to"), 2, 2)
        sl.addWidget(self.d_to, 2, 3)
        grid.addWidget(sbox, 1, 1)
        root.addLayout(grid)

        kbox = QGroupBox("Kinds of file")
        kl = QVBoxLayout(kbox)
        self.kinds = {}
        for kind in subsets.ALL_KINDS:
            cb = QCheckBox(KIND_WORDS.get(kind, kind))
            cb.setChecked(True)
            self.kinds[kind] = cb
            kl.addWidget(cb)
        root.addWidget(kbox)

        colbox = QGroupBox("Columns (all ticked = every column)")
        col = QVBoxLayout(colbox)
        row = QHBoxLayout()
        row.addWidget(QLabel("Columns of:"))
        self.col_kind = QComboBox()
        for kind in subsets.ALL_KINDS:
            self.col_kind.addItem(KIND_WORDS.get(kind, kind), kind)
        self.col_kind.currentIndexChanged.connect(self._show_columns)
        row.addWidget(self.col_kind, 1)
        for text, state in (("Tick all", Qt.Checked), ("Untick all", Qt.Unchecked)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, s=state: self._tick_all(s))
            row.addWidget(b)
        col.addLayout(row)
        self.col_list = QListWidget()
        col.addWidget(self.col_list)
        root.addWidget(colbox, 1)

        fl = QHBoxLayout()
        fl.addWidget(QLabel("Yes/no column per animal (optional) -- name:"))
        self.flag = QLineEdit()
        fl.addWidget(self.flag)
        fl.addWidget(QLabel("filled by rule:"))
        self.rule = QComboBox()
        fl.addWidget(self.rule)
        self.nightly = QCheckBox("Remake nightly")
        self.nightly.setChecked(True)
        self.nightly.setToolTip("Make it again after the nightly export whenever anything it "
                                "holds has changed (the previous files are archived).")
        fl.addWidget(self.nightly)
        root.addLayout(fl)

        btns = QHBoxLayout()
        for text, slot, style in (
                ("Save", self.save, ""),
                ("Save and make now", self.save_and_make,
                 "background:#2e7d32; color:white; font-weight:bold;"),
                ("Check only (writes nothing)", self.check, ""),
                ("Open its folder", self.open_folder, ""),
                ("Forget this definition", self.forget, ""),
                ("Close", self.close, "")):
            b = QPushButton(text)
            if style:
                b.setStyleSheet(style)
            b.clicked.connect(slot)
            btns.addWidget(b)
        root.addLayout(btns)
        self.out = QTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumHeight(160)
        root.addWidget(self.out)
        self._show_columns()

    def _cohorts_on_disk(self) -> List[str]:
        if not self.current:
            return []
        try:
            return sorted(layout.cohort_files_on_disk(self.current, layout.KINDS))
        except Exception:
            return []

    def _load_names(self, select: Optional[str] = None):
        self.saved.blockSignals(True)
        self.saved.clear()
        self.saved.addItem(NEW)
        try:
            names = sorted(subsets.profiles())
            rules = sorted(subsets.flag_rules())
        except Exception as e:
            names, rules = [], []
            self.out.append("[!] the saved definitions could not be read: %s" % e)
        self.saved.addItems(names)
        self.rule.clear()
        self.rule.addItem(BLANK_RULE)
        self.rule.addItems(rules)
        self.saved.blockSignals(False)
        if select and select in names:
            self.saved.setCurrentText(select)
        self._pick_saved(self.saved.currentText())

    # ------------------------------------------------------------- columns
    def _store_shown_columns(self):
        """Read the ticks of the kind on show back into the choice.

        A choice whose ticks are as they were is kept exactly, order included;
        every column of the dictionary ticked (and no column the exports lack)
        means every column; otherwise the ticked columns, in the saved order
        first, then newly ticked ones. With nothing on show (the dictionary
        could not be read) the choice is left as it was: zero boxes ticked out
        of zero is not "all ticked"."""
        if self._shown_kind is None:
            return
        items = [self.col_list.item(i) for i in range(self.col_list.count())]
        if not items:
            return
        known = [it.data(NAME_ROLE) for it in items if not it.data(NOT_THERE_ROLE)]
        ticked = [it.data(NAME_ROLE) for it in items if it.checkState() == Qt.Checked]
        prev = self._columns.get(self._shown_kind)
        if prev is not None and set(ticked) == set(prev):
            return
        if known and set(ticked) == set(known):
            self._columns[self._shown_kind] = None
            return
        prev = prev or []
        self._columns[self._shown_kind] = ([c for c in prev if c in ticked]
                                           + [c for c in ticked if c not in prev])

    def _show_columns(self, *_):
        self._store_shown_columns()
        kind = self.col_kind.currentData()
        self._shown_kind = kind
        self.col_list.clear()
        kept = self._columns.get(kind)
        known = _dictionary_columns(self.current, kind) if self.current else []
        for c in known:
            self.col_list.addItem(_item(c, kept is None or c in kept))
        for c in kept or []:
            if c not in known:
                self.col_list.addItem(_item(c, True, not_there=True))

    def _tick_all(self, state):
        for i in range(self.col_list.count()):
            self.col_list.item(i).setCheckState(state)

    # --------------------------------------------------------------- forms
    def _pick_saved(self, name: str):
        p = {}
        if name and name != NEW:
            p = (subsets.profiles() or {}).get(name, {})
            self.name.setText(name)
        else:
            self.name.clear()
        # Cohorts a previously opened definition added (not in the exports) go.
        for i in reversed(range(self.cohorts.count())):
            if self.cohorts.item(i).data(NOT_THERE_ROLE):
                self.cohorts.takeItem(i)
        want = list(p.get("cohorts") or [])
        shown = {self.cohorts.item(i).data(NAME_ROLE) for i in range(self.cohorts.count())}
        for c in want:
            if c not in shown:
                self.cohorts.addItem(_item(c, True, not_there=True))
        # Named cohorts decide when there are any (the slice is made from them,
        # subsets._cohorts_of); otherwise every cohort of the project.
        for i in range(self.cohorts.count()):
            it = self.cohorts.item(i)
            c = it.data(NAME_ROLE)
            on = (c in want) if want else bool(
                p.get("project") and layout.project_of(c) == p["project"])
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self._loaded = dict(p)
        self._loaded_ticks = set(self._ticked_cohorts())
        if p.get("project") and not want:
            self.out.append("'%s' takes every cohort of project %s, including cohorts added "
                            "later. Saved with these ticks left as they are, it stays that way; "
                            "change the ticks and it becomes a fixed list of cohorts."
                            % (name, p["project"]))
        for code, cb in self.trays.items():
            cb.setChecked(code in (p.get("tray_types") or []))
        w = p.get("window") or {"type": "all"}
        {"all": self.w_all, "last_before_injury": self.w_last, "dates": self.w_dates}.get(
            w.get("type"), self.w_all).setChecked(True)
        if w.get("type") == "last_before_injury":
            self.n.setMaximum(max(self.n.maximum(), int(w.get("n", 3))))
            self.n.setValue(int(w.get("n", 3)))
            self.w_tray.setCurrentIndex(max(0, self.w_tray.findData(w.get("tray_type"))))
        self.d_from.setText(w.get("from") or "")
        self.d_to.setText(w.get("to") or "")
        kinds = p.get("kinds") or list(subsets.ALL_KINDS)
        for kind, cb in self.kinds.items():
            cb.setChecked(kind in kinds)
        self._columns = {k: list(v) for k, v in (p.get("columns") or {}).items()}
        self._shown_kind = None
        self._show_columns()
        flag = p.get("flag") or {}
        self.flag.setText(flag.get("column") or "")
        if flag.get("rule") and self.rule.findText(flag["rule"]) < 0:
            # A rule the lab settings no longer hold: shown, never swapped for
            # "blank"; saving or making it says the rule is missing.
            self.rule.addItem(flag["rule"])
        self.rule.setCurrentText(flag.get("rule") or BLANK_RULE)
        self.nightly.setChecked(bool(p.get("nightly", True)))

    def _ticked_cohorts(self) -> List[str]:
        return [self.cohorts.item(i).data(NAME_ROLE) for i in range(self.cohorts.count())
                if self.cohorts.item(i).checkState() == Qt.Checked]

    def _form(self) -> dict:
        self._store_shown_columns()
        window = ("last_before_injury" if self.w_last.isChecked()
                  else "dates" if self.w_dates.isChecked() else "all")
        rule = self.rule.currentText()
        cohorts = self._ticked_cohorts()
        loaded = self._loaded or {}
        carried: dict = {}
        project = loaded.get("project")
        if project and not loaded.get("cohorts") and set(cohorts) == self._loaded_ticks:
            # "Every cohort of the project" with its ticks untouched stays that.
            carried["project"], cohorts = project, []
        elif project and cohorts and all(layout.project_of(c) == project for c in cohorts):
            carried["project"] = project
        if loaded.get("description"):
            carried["description"] = loaded["description"]
        return dict(carried, **{
            "cohorts": cohorts,
            "tray_types": [c for c, cb in self.trays.items() if cb.isChecked()],
            "window": window, "n": self.n.value(), "window_tray": self.w_tray.currentData(),
            "from": self.d_from.text().strip(), "to": self.d_to.text().strip(),
            "kinds": [k for k, cb in self.kinds.items() if cb.isChecked()],
            "columns": {k: v for k, v in self._columns.items() if v is not None},
            "flag_column": self.flag.text(),
            "flag_rule": None if rule == BLANK_RULE else rule,
            "nightly": self.nightly.isChecked(),
        })

    # ------------------------------------------------------------- actions
    def save(self) -> bool:
        """Save what the window shows under the name typed. A different
        definition already saved under that name is replaced only after the
        person says Yes, and the replaced one is kept (subsets.save_profile)."""
        name = self.name.text().strip()
        profile = None
        try:
            profile = profile_from_form(self._form())
            path = subsets.save_profile(name, profile)
        except subsets.DefinitionExists:
            if QMessageBox.question(
                    self, "Replace?",
                    "A limited export named '%s' is already saved, with a different "
                    "definition.\n\nReplace it with what this window shows? The one it "
                    "replaces is kept in the lab settings file (under %s)."
                    % (name, subsets.REPLACED_KEY)) != QMessageBox.Yes:
                self.out.append("Not saved: '%s' is already saved and was left as it is."
                                % name)
                return False
            try:
                path = subsets.save_profile(name, profile, replace=True)
            except Exception as e:
                QMessageBox.warning(self, "Not saved", "The definition was not saved:\n%s" % e)
                return False
        except Exception as e:
            QMessageBox.warning(self, "Not saved", "The definition was not saved:\n%s" % e)
            return False
        self.out.append("Saved %s (in %s)." % (name, path))
        self._load_names(select=name)
        return True

    def _start(self, dry_run: bool, definition_file: Optional[Path] = None):
        if self._job is not None and self._job.isRunning():
            QMessageBox.information(self, "Limited export", "It is still being made -- wait for it.")
            return
        name = self.name.text().strip()
        self.out.append("%s %s ... (large cohorts take a few minutes)"
                        % ("Checking" if dry_run else "Making", name))
        self._check_file = definition_file
        job = _Job(lambda: run_subset_program(name, dry_run=dry_run,
                                              definition_file=definition_file))
        self._job = job
        job.finished.connect(lambda: self._done(job.result))
        job.start()

    def save_and_make(self):
        if self.save():
            self._start(dry_run=False)

    def check(self):
        """Count what the window shows now, writing nothing -- not even the
        definition: the check runs on a temporary copy of it. WHY not save first:
        a saved definition is remade by the nightly job, and a check is not a
        decision to keep one."""
        import json
        import tempfile
        name = self.name.text().strip()
        try:
            profile = subsets.validate(name, profile_from_form(self._form()))
        except Exception as e:
            QMessageBox.warning(self, "Not checked", "The definition cannot be used:\n%s" % e)
            return
        fd, path = tempfile.mkstemp(prefix="mousedb_subset_check_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(profile, fh)
        self._start(dry_run=True, definition_file=Path(path))

    def _done(self, r: dict):
        if getattr(self, "_check_file", None):
            try:
                Path(self._check_file).unlink()
            except OSError:
                pass
            self._check_file = None
        self.out.append(r.get("output", "").strip())
        if r.get("busy"):
            QMessageBox.information(self, "Limited export",
                                    "The full exports are being rewritten right now -- try again "
                                    "when that finishes. Nothing was changed.")
        elif r.get("refused"):
            QMessageBox.warning(self, "Limited export",
                                "It was not made. Nothing was changed. The reason:\n\n%s"
                                % r.get("output", "")[-1500:])
        elif r.get("returncode") not in (0, None):
            # Not a refusal: the program says what state the folder was left in
            # (almost always "nothing was changed"), so that is shown, not assumed.
            QMessageBox.warning(self, "Limited export",
                                "It was not made. What happened, and the state of its "
                                "folder:\n\n%s" % r.get("output", "")[-1500:])

    def open_folder(self):
        try:
            d = subsets.subsets_root() / self.name.text().strip()
        except Exception as e:
            self.out.append("[!] %s" % e)
            return
        if d.is_dir():
            os.startfile(str(d))
        else:
            self.out.append("It has not been made yet -- press 'Save and make now'.")

    def forget(self):
        name = self.name.text().strip()
        if QMessageBox.question(self, "Forget", "Forget the saved definition '%s'? Files "
                                "already made stay where they are, and the definition is "
                                "kept in the lab settings file (under %s)."
                                % (name, subsets.REPLACED_KEY)) != QMessageBox.Yes:
            return
        try:
            subsets.remove_profile(name)
        except Exception as e:
            QMessageBox.warning(self, "Forget", str(e))
            return
        self.out.append("Forgot %s." % name)
        self._load_names()
