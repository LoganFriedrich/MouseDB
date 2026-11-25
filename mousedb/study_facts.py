"""Study-wide facts (strain, supplier, study leader, injury device ...) -- per project, local only.

WHY THIS MODULE EXISTS
----------------------
An ODC-SCI record carries facts that are the same for every animal in a study:
the species and strain, where the animals came from, who led the study, which
injury device was used. They are properties of ONE lab's study, not of the tool.
Typed into the sheet generators and the ODC row builders, they would put a
person's name in this public repository, and a typed-in strain would be wrong
for any study that used a different line.

So they are configuration, like the lab name: kept in a file on the machine,
never in source, and there is NO built-in default. A fact nobody has set is
exported as an empty cell (and reported as unset), never guessed.

Where values come from
----------------------
``~/.mousedb/study_facts.json`` (or the file named by MOUSEDB_STUDY_FACTS)::

    {
      "default":   {"SpeciesTyp": "..."},
      "PROJECT_A": {"SpeciesStrainTyp": "...", "StudyLeader": "..."}
    }

A project's own value wins; "default" fills anything a project leaves out.
The project is the letters before the first underscore of a cohort or subject
id (``PROJECT_A_02_07`` -> ``PROJECT_A``).

Set and inspect with::

    mousedb study-facts --show
    mousedb study-facts --set PROJECT_A SpeciesStrainTyp "..."
    mousedb study-facts --unset PROJECT_A StudyLeader

Any ODC column name may be stored; FIELDS lists the ones the tools fill today,
with a plain description of each for the command and the GUI.

A damaged file is refused, never read as empty
----------------------------------------------
A file that exists but is not valid JSON (a half-written save, a hand edit with
a stray comma) raises StudyFactsFileDamaged from every reader and every writer,
naming the file. WHY: it used to read as "nothing set", so every export quietly
wrote its study-fact columns blank as if nobody had ever set them, and the next
Set (tab or command) wrote back a file holding only that one fact -- every other
project's facts and exception rules gone. Writes go through
config.update_settings_file, the one writer every mousedb settings file uses: it
refuses a damaged file (keeping a copy beside it, <name>.damaged-<time>) and
swaps a finished temporary file in, so a crash mid-save leaves the old file
whole. A missing or empty file is the normal "nothing set yet".
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from .config import CONFIG_PATH, SettingsFileDamaged, read_settings_file, update_settings_file

ENV_VAR = "MOUSEDB_STUDY_FACTS"
DEFAULT_SECTION = "default"

# Reserved key inside a project section holding per-animal EXCEPTIONS to its facts.
# WHY this exists: a study fact is only nearly constant. A colony can be one strain
# except for the animals on a transgenic line, and which line, what it is called, and
# how it is recognisable in that lab's sheets are all facts about ONE lab -- exactly
# the kind of thing that must never be typed into this repository. So the tool knows
# only the SHAPE of an exception ("when this text appears in an animal's records, that
# field takes this value") and the lab supplies the text and the value:
#
#     "PROJECT_A": {
#       "SpeciesStrainTyp": "<the usual strain>",
#       "_rules": [
#         {"field": "SpeciesStrainTyp",
#          "value": "<the strain for these animals>",
#          "when_any_value_contains": "<text that marks them in the sheets>"}
#       ]
#     }
#
# Optional "in_fields" restricts the search to named record columns; without it every
# recorded value for that animal is searched. Matching is case-insensitive substring,
# because the sheets spell these by hand and inconsistently.
RULES_KEY = "_rules"

# ODC column name -> what a person should type there. Order is display order.
FIELDS: Dict[str, str] = {
    "SpeciesTyp": "Species of the animals (e.g. mouse).",
    "SpeciesStrainTyp": "Strain or line, as the supplier names it.",
    "AnimalSourceNam": "Where the animals came from (supplier or in-house colony).",
    "StudyLeader": "The person who led the study, as it should appear in shared records.",
    "Injury_device": "Device used to make the injury (model and version).",
}

# Surgery-protocol values pre-filled into a NEW blank tracking sheet (sheet column
# name -> description). Only starting text: each animal's real values are whatever
# is typed into its sheet row.
PROTOCOL_FIELDS: Dict[str, str] = {
    "Contusion_Location": "Spinal level of the contusion.",
    "Intended_kd": "Intended impact force (kdyn), a number.",
    "Anesthetic": "Anesthetic used for surgery.",
    "Anesthetic_Dose": "Anesthetic dose.",
    "Analgesic": "Analgesic given after surgery.",
    "Analgesic_Dose": "Analgesic dose.",
    "Injection_Location": "Spinal cord injection target.",
    "Depths (D/V)": "Injection depths (dorsal/ventral).",
    "Coordinates (M/L)": "Injection coordinates (medial/lateral).",
}


class StudyFactsFileDamaged(SettingsFileDamaged):
    """The study-facts file exists but cannot be read as study facts. A
    SettingsFileDamaged (so a ConfigError: the mousedb command prints it as one
    [FAIL] line), worded for this file."""

    def __init__(self, path, why: str, backup=None):
        super().__init__(path, why, backup)
        msg = ("the study-facts file %s is damaged and was not read (%s).\n"
               "  Until it is put right no export carries study facts and nothing can "
               "be saved to it. Open it in a text editor and correct it (it must be "
               "JSON: {\"<PROJECT>\": {\"<column>\": \"<value>\"}, ...}); then "
               "'mousedb study-facts --show' lists what it holds." % (path, why))
        if backup is not None:
            msg += "\n  Nothing was saved: a copy of the damaged file is at %s" % backup
        self.args = (msg,)


def facts_path() -> Path:
    """The study-facts file for this machine (env override, else beside config.json)."""
    env = os.environ.get(ENV_VAR)
    return Path(env) if env else CONFIG_PATH.parent / "study_facts.json"


def _read_file() -> dict:
    """The file as it is on disk; {} when missing or empty. Raises
    StudyFactsFileDamaged when it exists but is not a JSON object."""
    try:
        return read_settings_file(facts_path())
    except SettingsFileDamaged as e:
        raise StudyFactsFileDamaged(e.path, e.why) from None


def read_all() -> Dict[str, Dict[str, str]]:
    """Every section in the file; {} when the file is missing or empty.
    Raises StudyFactsFileDamaged when it exists but cannot be read (see the
    module docstring: a damaged file never reads as "nothing set").

    Values are coerced to text so a number typed into the file cannot reach an export
    as an int -- except RULES_KEY, which is a list and is passed through untouched.
    """
    out = {}
    for k, sec in _read_file().items():
        if not isinstance(sec, dict):
            continue
        section = {}
        for f, v in sec.items():
            if str(f) == RULES_KEY:
                section[RULES_KEY] = v if isinstance(v, list) else []
            else:
                section[str(f)] = str(v)
        out[str(k)] = section
    return out


def _update(change) -> Path:
    """Apply ``change(dict)`` to the file through config.update_settings_file
    (refuses a damaged file and keeps a copy of it; writes a temporary file and
    swaps it in whole). Returns the file's path."""
    try:
        return update_settings_file(facts_path(), change)
    except SettingsFileDamaged as e:
        if isinstance(e, StudyFactsFileDamaged):
            raise
        raise StudyFactsFileDamaged(e.path, e.why, e.backup) from None


def _own_section(data: dict, project: str) -> dict:
    """The project's section of the raw file, created when missing. A section
    that is there but is not a set of named values is refused, not replaced:
    replacing it would throw away whatever it holds."""
    section = data.setdefault(project, {})
    if not isinstance(section, dict):
        raise StudyFactsFileDamaged(
            facts_path(), "the section '%s' holds a %s, not named values; nothing "
                          "was saved" % (project, type(section).__name__))
    return section


def rules(project: Optional[str]) -> list:
    """Exception rules for a project: the default section's, then the project's own."""
    sections = read_all()
    out = list(sections.get(DEFAULT_SECTION, {}).get(RULES_KEY) or [])
    out += list(sections.get(project_of(project), {}).get(RULES_KEY) or [])
    return [r for r in out if isinstance(r, dict)]


def _rule_matches(rule: dict, records) -> bool:
    needle = str(rule.get("when_any_value_contains") or "").strip().lower()
    if not needle:
        return False
    only = rule.get("in_fields")
    only = {str(f).lower() for f in only} if isinstance(only, list) else None
    for field, value in (records or {}).items():
        if only is not None and str(field).lower() not in only:
            continue
        if value is not None and needle in str(value).lower():
            return True
    return False


def apply_rules(project: Optional[str], base: Dict[str, str], records) -> Dict[str, str]:
    """``base`` with any matching exception rules applied for ONE animal.

    ``records`` is that animal's recorded values (its tracking-sheet row, flattened).
    Rules are applied in order, so a later rule wins over an earlier one -- which lets
    a lab write a broad rule and then a narrower exception to it.
    """
    out = dict(base or {})
    for rule in rules(project):
        field = str(rule.get("field") or "").strip()
        if not field:
            continue
        if _rule_matches(rule, records):
            out[field] = str(rule.get("value", ""))
    return out


def project_of(identifier: Optional[str]) -> str:
    """Project letters of a cohort or subject id: the part before the first underscore."""
    text = str(identifier or "").strip()
    return text.split("_", 1)[0] if text else ""


def facts(project: Optional[str]) -> Dict[str, str]:
    """Every set fact for a project: "default" overlaid by the project's own values.
    Blank values are left out, so a caller can tell unset from set."""
    sections = read_all()
    merged = {k: v for k, v in sections.get(DEFAULT_SECTION, {}).items()
              if v and k != RULES_KEY}
    merged.update({k: v for k, v in sections.get(project_of(project), {}).items()
                   if v and k != RULES_KEY})
    return merged


def get(project: Optional[str], field: str) -> str:
    """One fact for a project, or "" when nobody has set it."""
    return facts(project).get(field, "")


def unset_fields(project: Optional[str]) -> list:
    """The FIELDS a project has no value for -- what an export should report as blank."""
    have = facts(project)
    return [f for f in FIELDS if not have.get(f)]


def set_fact(project: str, field: str, value: Optional[str]) -> Path:
    """Write one fact (None or "" removes it). Returns the file path."""
    project = project.strip()
    field = field.strip()
    if not project or not field:
        raise ValueError("both a project (or 'default') and a field name are needed")

    def change(data):
        section = _own_section(data, project)
        if value is None or str(value) == "":
            section.pop(field, None)
            if not section:
                data.pop(project, None)
        else:
            section[field] = str(value)
    return _update(change)


def set_rules(project: str, rule_list: list) -> Path:
    """Replace a project's exception rules. An empty list removes them.

    Whole-list rather than one-at-a-time because the rules are ORDERED -- a later one
    wins over an earlier one -- so adding, removing and reordering are all the same
    operation on the list, and an editor that could only append would make the order
    impossible to correct.
    """
    project = str(project).strip()
    if not project:
        raise ValueError("a project (or 'default') is needed")
    cleaned = []
    for rule in rule_list or []:
        if not isinstance(rule, dict):
            continue
        field = str(rule.get("field") or "").strip()
        needle = str(rule.get("when_any_value_contains") or "").strip()
        if not field or not needle:
            # A rule with no column or no text matches nothing; storing it would only
            # look like a setting that does something.
            continue
        entry = {"field": field, "value": str(rule.get("value", "")),
                 "when_any_value_contains": needle}
        only = rule.get("in_fields")
        if isinstance(only, list):
            only = [str(f).strip() for f in only if str(f).strip()]
            if only:
                entry["in_fields"] = only
        cleaned.append(entry)

    def change(data):
        section = _own_section(data, project)
        if cleaned:
            section[RULES_KEY] = cleaned
        else:
            section.pop(RULES_KEY, None)
        if not section:
            data.pop(project, None)
    return _update(change)


def describe(project: Optional[str] = None) -> str:
    """Human-readable table: each project's value for every known field, and any extras."""
    data = read_all()
    lines = ["study facts (%s)" % facts_path(), ""]
    projects = [project_of(project)] if project else sorted(k for k in data if k != DEFAULT_SECTION)
    if DEFAULT_SECTION in data and not project:
        projects = [DEFAULT_SECTION] + projects
    if not projects:
        lines.append("  nothing set yet. Example:")
        lines.append('    mousedb study-facts --set <PROJECT> SpeciesStrainTyp "<strain>"')
        return "\n".join(lines)
    for name in projects:
        own = data.get(name, {})
        shown = own if name == DEFAULT_SECTION else facts(name)
        lines.append("[%s]" % name)
        known = list(FIELDS) + list(PROTOCOL_FIELDS)
        for field in known + sorted(k for k in shown if k not in known and k != RULES_KEY):
            value = shown.get(field)
            if value:
                src = "" if name == DEFAULT_SECTION or field in own else "  (from default)"
                lines.append("  %-18s %s%s" % (field, value, src))
            else:
                lines.append("  %-18s NOT SET" % field)
        for rule in (own.get(RULES_KEY) or []):
            if isinstance(rule, dict):
                lines.append("  exception: %s = %r when any value contains %r%s"
                             % (rule.get("field"), rule.get("value"),
                                rule.get("when_any_value_contains"),
                                (" in %s" % rule["in_fields"]) if rule.get("in_fields") else ""))
        lines.append("")
    return "\n".join(lines)
