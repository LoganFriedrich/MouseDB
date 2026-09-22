"""Study Facts tab -- the facts no sheet records, and the exceptions to them.

WHY THIS TAB EXISTS
-------------------
Some facts are true of a whole study and appear in no tracking sheet: the species,
the strain, who the animals came from, who led the study, which device made the
injury. The exports need them on every row, so they are declared once, per project,
and stored outside the code (they are facts about ONE lab; a tool that carries them
cannot be given to another lab).

Until this tab existed the only way to set them was a terminal command, and the only
way to write an EXCEPTION to one was to hand-edit a JSON file -- which means that in
practice a column stayed blank, or wrong, because the person who knew the answer had
no way to enter it. That is the failure this corrects: every routine action has a
button, and the command line is for people who choose it, never the only door.

WHAT AN EXCEPTION IS
--------------------
A study fact is often only NEARLY constant -- a colony is one strain except for the
animals on a transgenic line. An exception says: when this text appears anywhere in
an animal's recorded values, this column takes this value instead. The text and the
value come from the person; the tool supplies only the shape.

Nothing here edits a tracking sheet, and nothing here writes the database. It writes
one configuration file, whose path is shown at the top of the tab.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from .. import study_facts

QUICK_GUIDE = """\
WHAT THIS TAB IS FOR
Facts that are true of a whole study and appear in no tracking sheet -- species,
strain, where the animals came from, who led the study, the injury device. The
exports need one on every row, so you declare it once here instead of typing it into
every sheet.

A value recorded in a tracking sheet ALWAYS wins over anything set here. These only
fill gaps. If an exported column reads "Not recorded" on every row, this is where to
fix it.

THE TWO TABLES
* Facts -- one value for a project. "default" applies to every project; a project's
  own value beats the default.
* Exceptions -- for when a fact is only nearly constant. An exception says: when this
  text appears anywhere in an animal's recorded values, this column takes this other
  value. Use it for a transgenic line inside an otherwise uniform colony, or to fill a
  column that FOLLOWS from another (the spinal level of an injury follows from the
  injury type).

THINGS WORTH KNOWING
* Matching ignores capitals, because sheets are typed by hand.
* "Only in these columns" restricts the search -- safer when the text could also turn
  up in a comment.
* Exceptions are applied in order, so a broad one can be followed by a narrower one.
* An exception only ever fires on values that were actually IMPORTED. If the text you
  are matching lives in a sheet tab the importer does not read, the rule is correct
  and will still do nothing.

AFTER A CHANGE
Re-export to see it: "Where Is My Data" -> update, or the nightly job. The exports are
rebuilt from the database plus these facts.
"""


class _RuleDialog(QDialog):
    """Add or edit one exception, with every field explained in plain words."""

    def __init__(self, parent=None, rule: Optional[dict] = None):
        super().__init__(parent)
        self.setWindowTitle("Exception to a study fact")
        self.setMinimumWidth(560)
        rule = rule or {}

        self.field = QLineEdit(str(rule.get("field", "")))
        self.field.setPlaceholderText("e.g. SpeciesStrainTyp")
        self.value = QLineEdit(str(rule.get("value", "")))
        self.value.setPlaceholderText("what the column should say for these animals")
        self.needle = QLineEdit(str(rule.get("when_any_value_contains", "")))
        self.needle.setPlaceholderText("text that marks these animals in the sheets")
        only = rule.get("in_fields") or []
        self.in_fields = QLineEdit(", ".join(str(f) for f in only))
        self.in_fields.setPlaceholderText("leave empty to search every recorded value")

        form = QFormLayout()
        form.addRow("Column to set:", self.field)
        form.addRow("Value to set it to:", self.value)
        form.addRow("When any value contains:", self.needle)
        form.addRow("Only in these columns:", self.in_fields)

        note = QLabel(
            "Matching ignores capitals. The exception applies to an animal when the "
            "text appears in that animal's own recorded values.\n"
            "It can only match values that were imported -- text in a sheet tab the "
            "importer does not read will never trigger it.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #555;")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def _accept(self):
        if not self.field.text().strip():
            QMessageBox.warning(self, "Which column?",
                                "Name the column this exception sets.")
            return
        if not self.needle.text().strip():
            QMessageBox.warning(
                self, "Which animals?",
                "Give the text that marks these animals. Without it the exception "
                "would match nothing, which is the same as not having it.")
            return
        self.accept()

    def rule(self) -> dict:
        out = {"field": self.field.text().strip(),
               "value": self.value.text().strip(),
               "when_any_value_contains": self.needle.text().strip()}
        only = [f.strip() for f in self.in_fields.text().split(",") if f.strip()]
        if only:
            out["in_fields"] = only
        return out


class StudyFactsTab(QWidget):
    """Facts and their exceptions, per project, editable without a terminal."""

    def __init__(self, db=None):
        super().__init__()
        self.db = db
        self._build()
        self.reload()

    # ---------------------------------------------------------------- layout
    def _build(self):
        layout = QVBoxLayout(self)

        self.path_label = QLabel()
        self.path_label.setStyleSheet("color: #555;")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        row = QHBoxLayout()
        row.addWidget(QLabel("Project:"))
        self.project = QComboBox()
        self.project.setMinimumWidth(180)
        self.project.currentIndexChanged.connect(self.reload_tables)
        row.addWidget(self.project)
        add_project = QPushButton("Add project...")
        add_project.clicked.connect(self._add_project)
        row.addWidget(add_project)
        row.addStretch()
        guide = QPushButton("?")
        guide.setFixedWidth(30)
        guide.setToolTip("What this tab is for")
        guide.clicked.connect(self._show_guide)
        row.addWidget(guide)
        layout.addLayout(row)

        layout.addWidget(QLabel("<b>Facts</b> -- one value for the whole project. "
                                "A value recorded in a tracking sheet always wins."))
        self.facts_table = QTableWidget(0, 3)
        self.facts_table.setHorizontalHeaderLabels(["Column", "Value", "What it is"])
        self.facts_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.facts_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.facts_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.facts_table)

        frow = QHBoxLayout()
        set_btn = QPushButton("Set value...")
        set_btn.clicked.connect(self._set_fact)
        frow.addWidget(set_btn)
        clear_btn = QPushButton("Clear value")
        clear_btn.clicked.connect(self._clear_fact)
        frow.addWidget(clear_btn)
        other_btn = QPushButton("Set another column...")
        other_btn.setToolTip("Set a column that is not in the standard list")
        other_btn.clicked.connect(self._set_other_fact)
        frow.addWidget(other_btn)
        frow.addStretch()
        layout.addLayout(frow)

        layout.addWidget(QLabel(
            "<b>Exceptions</b> -- for a fact that is only nearly constant. "
            "Applied in order, so a broad one can be followed by a narrower one."))
        self.rules_table = QTableWidget(0, 4)
        self.rules_table.setHorizontalHeaderLabels(
            ["Sets column", "To value", "When any value contains", "Only in these columns"])
        self.rules_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.rules_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.rules_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.rules_table)

        rrow = QHBoxLayout()
        for text, slot in (("Add exception...", self._add_rule),
                           ("Edit...", self._edit_rule),
                           ("Remove", self._remove_rule),
                           ("Move up", lambda: self._move_rule(-1)),
                           ("Move down", lambda: self._move_rule(1))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            rrow.addWidget(b)
        rrow.addStretch()
        layout.addLayout(rrow)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(90)
        layout.addWidget(self.log)

    # ----------------------------------------------------------------- data
    def _say(self, text: str):
        self.log.append(text)

    def reload(self):
        self.path_label.setText("Stored in: %s   (this file is never committed; it "
                                "holds this lab's own facts)" % study_facts.facts_path())
        sections = study_facts.read_all()
        names = [study_facts.DEFAULT_SECTION] + sorted(
            k for k in sections if k != study_facts.DEFAULT_SECTION)
        current = self.project.currentText()
        self.project.blockSignals(True)
        self.project.clear()
        self.project.addItems(names)
        if current in names:
            self.project.setCurrentText(current)
        self.project.blockSignals(False)
        self.reload_tables()

    def _current_project(self) -> str:
        return self.project.currentText() or study_facts.DEFAULT_SECTION

    def reload_tables(self):
        project = self._current_project()
        sections = study_facts.read_all()
        own = sections.get(project, {})
        # For a real project show the effective value (its own, else the default) so
        # the person sees what the export will actually write.
        effective = own if project == study_facts.DEFAULT_SECTION else study_facts.facts(project)

        known = dict(study_facts.FIELDS)
        known.update(study_facts.PROTOCOL_FIELDS)
        extra = sorted(k for k in effective
                       if k not in known and k != study_facts.RULES_KEY)
        rows = list(known) + extra
        self.facts_table.setRowCount(len(rows))
        for i, name in enumerate(rows):
            value = effective.get(name, "")
            shown = value if value else "NOT SET"
            if value and project != study_facts.DEFAULT_SECTION and name not in own:
                shown = "%s   (from default)" % value
            self.facts_table.setItem(i, 0, QTableWidgetItem(name))
            item = QTableWidgetItem(shown)
            if not value:
                item.setForeground(Qt.gray)
            self.facts_table.setItem(i, 1, item)
            self.facts_table.setItem(i, 2, QTableWidgetItem(known.get(name, "")))
        self.facts_table.resizeColumnsToContents()

        rules = list(own.get(study_facts.RULES_KEY) or [])
        self.rules_table.setRowCount(len(rules))
        for i, rule in enumerate(rules):
            if not isinstance(rule, dict):
                rule = {}
            self.rules_table.setItem(i, 0, QTableWidgetItem(str(rule.get("field", ""))))
            self.rules_table.setItem(i, 1, QTableWidgetItem(str(rule.get("value", ""))))
            self.rules_table.setItem(
                i, 2, QTableWidgetItem(str(rule.get("when_any_value_contains", ""))))
            only = rule.get("in_fields") or []
            self.rules_table.setItem(
                i, 3, QTableWidgetItem(", ".join(str(f) for f in only) or "(every value)"))

    # --------------------------------------------------------------- facts
    def _selected_field(self) -> Optional[str]:
        row = self.facts_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Pick a column",
                                    "Select a row in the Facts table first.")
            return None
        return self.facts_table.item(row, 0).text()

    def _set_fact(self):
        name = self._selected_field()
        if not name:
            return
        project = self._current_project()
        known = dict(study_facts.FIELDS)
        known.update(study_facts.PROTOCOL_FIELDS)
        current = study_facts.read_all().get(project, {}).get(name, "")
        value, ok = QInputDialog.getText(
            self, "Set %s" % name,
            "%s\n\nValue for project '%s':" % (known.get(name, ""), project),
            text=str(current))
        if not ok:
            return
        self._write_fact(project, name, value.strip())

    def _set_other_fact(self):
        project = self._current_project()
        name, ok = QInputDialog.getText(
            self, "Set another column",
            "Exact column name as it appears in the export:")
        if not ok or not name.strip():
            return
        value, ok = QInputDialog.getText(self, "Set %s" % name.strip(),
                                         "Value for project '%s':" % project)
        if not ok:
            return
        self._write_fact(project, name.strip(), value.strip())

    def _clear_fact(self):
        name = self._selected_field()
        if not name:
            return
        project = self._current_project()
        if QMessageBox.question(
                self, "Clear value",
                "Clear %s for '%s'?\n\nThe column will read 'Not recorded' in the "
                "exports unless a sheet records it." % (name, project),
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        self._write_fact(project, name, "")

    def _write_fact(self, project: str, name: str, value: str):
        try:
            study_facts.set_fact(project, name, value or None)
        except Exception as e:
            QMessageBox.critical(self, "Could not save", str(e))
            return
        self._say("%s: %s = %s" % (project, name, value or "(cleared)"))
        self.reload()

    def _add_project(self):
        name, ok = QInputDialog.getText(
            self, "Add project",
            "Project code, as it starts an animal id (the part before the first "
            "underscore):")
        if not ok or not name.strip():
            return
        name = name.strip()
        if self.project.findText(name) < 0:
            self.project.addItem(name)
        self.project.setCurrentText(name)
        self._say("Project '%s' added. It is saved once it has a value or an "
                  "exception." % name)

    # --------------------------------------------------------------- rules
    def _rules(self) -> List[dict]:
        own = study_facts.read_all().get(self._current_project(), {})
        return [r for r in (own.get(study_facts.RULES_KEY) or []) if isinstance(r, dict)]

    def _save_rules(self, rules: List[dict]):
        project = self._current_project()
        try:
            study_facts.set_rules(project, rules)
        except Exception as e:
            QMessageBox.critical(self, "Could not save", str(e))
            return
        self.reload_tables()

    def _add_rule(self):
        dlg = _RuleDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            rules = self._rules() + [dlg.rule()]
            self._save_rules(rules)
            self._say("Exception added.")

    def _edit_rule(self):
        row = self.rules_table.currentRow()
        rules = self._rules()
        if row < 0 or row >= len(rules):
            QMessageBox.information(self, "Pick an exception",
                                    "Select a row in the Exceptions table first.")
            return
        dlg = _RuleDialog(self, rules[row])
        if dlg.exec_() == QDialog.Accepted:
            rules[row] = dlg.rule()
            self._save_rules(rules)
            self._say("Exception updated.")

    def _remove_rule(self):
        row = self.rules_table.currentRow()
        rules = self._rules()
        if row < 0 or row >= len(rules):
            QMessageBox.information(self, "Pick an exception",
                                    "Select a row in the Exceptions table first.")
            return
        if QMessageBox.question(self, "Remove exception",
                                "Remove this exception?",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        del rules[row]
        self._save_rules(rules)
        self._say("Exception removed.")

    def _move_rule(self, delta: int):
        row = self.rules_table.currentRow()
        rules = self._rules()
        new = row + delta
        if row < 0 or new < 0 or new >= len(rules):
            return
        rules[row], rules[new] = rules[new], rules[row]
        self._save_rules(rules)
        self.rules_table.selectRow(new)
        self._say("Order changed -- later exceptions win over earlier ones.")

    def _show_guide(self):
        box = QMessageBox(self)
        box.setWindowTitle("Study Facts -- quick guide")
        box.setText(QUICK_GUIDE)
        box.exec_()
