"""Which projects keep tracking sheets, and what their sheets are called.

WHY THIS MODULE EXISTS (2026-10-29)
-----------------------------------
Until now one project's sheet name was written into the code, and every part
of mousedb that finds, imports, pins or creates a tracking sheet assumed that
one project. A lab that starts a second project (a new wing of a study, a
pilot) had nowhere to say what that project's sheets are called: its sheets
were never recognised, never imported, and their hand scores never reached the
reaching pipeline -- while the second project's videos went on being analysed.

So the projects are configuration. Each project names the pattern its
workbooks follow; everything that looks for a sheet asks here. The tool knows
the MECHANISM; the lab's own names live in the lab's settings file, never in
this public code.

WHERE THE SETTINGS LIVE
-----------------------
One file for the whole lab, beside the database on the shared storage, so
every machine and every login reads the same thing and nothing is set per
person:

    <mousedb_root>/config/mousedb_lab_config.json

(or the file named by the MOUSEDB_LAB_CONFIG environment variable). Its
"sheet_projects" block:

    {
      "sheet_projects": {
        "PROJA":  {"sheet_name": "ProjectA_{NN}_Animal_Tracking.xlsx"},
        "PROJAB": {"sheet_name": "ProjectAB_{NN}_Animal_Tracking.xlsx",
                   "template": "sheet_templates/PROJAB.xlsx"}
      }
    }

``{NN}`` is the cohort's number: written with two digits, read with one or two.
``template`` (optional) is the blank workbook a new cohort's sheet is made from
(cohort_tools.sheet_templates), relative to the settings file's folder.

Inspect and set with::

    mousedb sheet-projects --show
    mousedb sheet-projects --set PROJA "ProjectA_{NN}_Animal_Tracking.xlsx"

THERE IS NO BUILT-IN DEFAULT. With no project set, no file is recognised as a
sheet, and every place that lists sheets says so and names the command above
(the "no built-in defaults" rule every lab setting follows: a guessed name is a
wrong import waiting to happen).

HOW A NAME IS READ
------------------
A file is a project's sheet when its name STARTS with the pattern up to
``.xlsx`` -- with any tail after that (``..._Tracking1.xlsx``, a ``(2)`` copy),
because real, in-use sheets carry such tails and must stay visible as
candidates for a person to choose between (cohort_sheets.pin_cohort_sheet).
A name two projects' patterns both match is refused rather than given to
either: guessing would file one project's animals under the other.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, NamedTuple, Optional, Tuple

ENV_VAR = "MOUSEDB_LAB_CONFIG"
SET_COMMAND = 'mousedb sheet-projects --set <PROJECT> "<file name with {NN}>"'

_NUMBER = "{NN}"
_PROJECT_RE = re.compile(r"[A-Z]+")


class SheetProject(NamedTuple):
    project: str          # "PROJA": the letters of its cohort ids (PROJA_05)
    sheet_name: str       # "ProjectA_{NN}_Animal_Tracking.xlsx"
    template: Optional[Path]   # the blank workbook new sheets are made from, if set

    @property
    def head(self) -> str:
        return self.sheet_name.split(_NUMBER, 1)[0]

    @property
    def rest(self) -> str:
        """What follows the number, without the .xlsx ending."""
        return re.sub(r"\.xlsx$", "", self.sheet_name.split(_NUMBER, 1)[1], flags=re.I)

    def file_name(self, number) -> str:
        return self.sheet_name.replace(_NUMBER, "%02d" % int(number))

    def matcher(self) -> "re.Pattern":
        """Anchored: the name must START with the pattern (see the module notes)."""
        return re.compile(r"^%s(\d{1,2})%s(.*)\.xlsx$" % (re.escape(self.head), re.escape(self.rest)),
                          re.I)

    def resembler(self) -> "re.Pattern":
        """The pattern ANYWHERE in a name: finds files that look like a sheet but
        are refused ("Copy of ...", a lock file), so the refusal can be shown."""
        return re.compile(r"%s\d{1,2}%s.*\.xlsx$" % (re.escape(self.head), re.escape(self.rest)), re.I)


def config_path() -> Optional[Path]:
    """The lab's settings file, or None when mousedb_root itself is not set."""
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env)
    try:
        from .config import mousedb_root
        root = mousedb_root()
    except Exception:
        return None
    return Path(root) / "config" / "mousedb_lab_config.json" if root else None


def _read() -> dict:
    p = config_path()
    if p is None or not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _validate(project: str, sheet_name: str) -> None:
    if not _PROJECT_RE.fullmatch(project or ""):
        raise ValueError("'%s' is not a project code: capital letters only, as in the "
                         "cohort ids (PROJA for PROJA_05)" % project)
    if (sheet_name or "").count(_NUMBER) != 1 or not str(sheet_name).lower().endswith(".xlsx"):
        raise ValueError("'%s' is not a sheet name pattern: it must hold {NN} exactly once "
                         "(where the cohort number goes) and end in .xlsx" % sheet_name)
    if not sheet_name.split(_NUMBER, 1)[0]:
        raise ValueError("'%s' must have text before {NN}: a name starting with the number "
                         "would match any workbook whose name starts with digits" % sheet_name)


def definitions() -> Dict[str, SheetProject]:
    """Every configured project, by its code. Empty when nothing is set.
    Raises ValueError, naming the file, when the settings file is unreadable or
    holds an invalid entry: an import that silently ignored a project's sheets
    would be the very failure this module exists to prevent."""
    try:
        raw = _read().get("sheet_projects") or {}
    except (OSError, ValueError) as e:
        raise ValueError("cannot read the lab settings file %s: %s" % (config_path(), e)) from None
    base = config_path().parent if config_path() else None
    out = {}
    for project, entry in sorted(raw.items()):
        sheet_name = (entry or {}).get("sheet_name")
        try:
            _validate(project, sheet_name)
        except ValueError as e:
            raise ValueError("%s: project %s: %s" % (config_path(), project, e)) from None
        template = (entry or {}).get("template")
        out[project] = SheetProject(project, sheet_name,
                                    (base / template) if (template and base) else None)
    return out


def match(name: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Read a file name: ``(cohort_id, tail, None)`` for a project's sheet
    ("PROJA_05", the text after the pattern), ``(None, None, reason)`` when two
    projects' patterns both match it, and ``(None, None, None)`` otherwise."""
    hits = []
    for p in definitions().values():
        m = p.matcher().match(name)
        if m:
            hits.append(("%s_%02d" % (p.project, int(m.group(1))), m.group(2)))
    if len(hits) > 1:
        return None, None, ("the name fits the sheet names of more than one project (%s); "
                            "rename it so it fits exactly one"
                            % ", ".join(sorted(h[0].rsplit("_", 1)[0] for h in hits)))
    return (hits[0][0], hits[0][1], None) if hits else (None, None, None)


def resembles(name: str) -> bool:
    """True if a project's sheet name appears anywhere in ``name``."""
    return any(p.resembler().search(name) for p in definitions().values())


def project_of(cohort_id: str) -> Optional[SheetProject]:
    """The configured project of a cohort id ("PROJA_05" -> PROJA), or None."""
    return definitions().get(str(cohort_id).rsplit("_", 1)[0].upper())


def describe() -> str:
    """Every configured name, for messages: 'PROJA: ProjectA_{NN}_...xlsx; ...'."""
    d = definitions()
    if not d:
        return "no project's sheet name is set (%s)" % SET_COMMAND
    return "; ".join("%s: %s" % (p.project, p.sheet_name) for p in d.values())


def set_project(project: str, sheet_name: str, template: Optional[str] = None) -> Path:
    """Record a project's sheet name (and template) in the lab settings file.
    Returns the file's path. Other settings in the file are kept."""
    project = (project or "").strip().upper()
    _validate(project, sheet_name)
    p = config_path()
    if p is None:
        raise ValueError("mousedb_root is not set, so there is no lab settings file to "
                         "write to; run 'mousedb config --set mousedb_root <folder>' first")
    data = _read()
    entry = (data.setdefault("sheet_projects", {})).setdefault(project, {})
    entry["sheet_name"] = sheet_name
    if template is not None:
        if template:
            entry["template"] = template
        else:
            entry.pop("template", None)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p
