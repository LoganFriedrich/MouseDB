"""Study-wide facts (strain, supplier, study leader, injury device ...) -- per project, local only.

WHY THIS MODULE EXISTS
----------------------
An ODC-SCI record carries facts that are the same for every animal in a study:
the species and strain, where the animals came from, who led the study, which
injury device was used. They are properties of ONE lab's study, not of the tool.
Until 2026-09-15 they were typed straight into the sheet generators and the ODC
row builders -- including a person's name -- in this public repository, and the
typed-in strain was also wrong for any study that used a different line.

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
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

from .config import CONFIG_PATH

ENV_VAR = "MOUSEDB_STUDY_FACTS"
DEFAULT_SECTION = "default"

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


def facts_path() -> Path:
    """The study-facts file for this machine (env override, else beside config.json)."""
    env = os.environ.get(ENV_VAR)
    return Path(env) if env else CONFIG_PATH.parent / "study_facts.json"


def read_all() -> Dict[str, Dict[str, str]]:
    """Every section in the file; {} when the file is missing or unreadable."""
    path = facts_path()
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): {str(f): str(v) for f, v in (sec or {}).items()}
                        for k, sec in data.items() if isinstance(sec, dict)}
    except Exception:
        pass
    return {}


def project_of(identifier: Optional[str]) -> str:
    """Project letters of a cohort or subject id: the part before the first underscore."""
    text = str(identifier or "").strip()
    return text.split("_", 1)[0] if text else ""


def facts(project: Optional[str]) -> Dict[str, str]:
    """Every set fact for a project: "default" overlaid by the project's own values.
    Blank values are left out, so a caller can tell unset from set."""
    sections = read_all()
    merged = {k: v for k, v in sections.get(DEFAULT_SECTION, {}).items() if v}
    merged.update({k: v for k, v in sections.get(project_of(project), {}).items() if v})
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
    data = read_all()
    section = data.setdefault(project, {})
    if value is None or str(value) == "":
        section.pop(field, None)
        if not section:
            data.pop(project, None)
    else:
        section[field] = str(value)
    path = facts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


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
        for field in known + sorted(k for k in shown if k not in known):
            value = shown.get(field)
            if value:
                src = "" if name == DEFAULT_SECTION or field in own else "  (from default)"
                lines.append("  %-18s %s%s" % (field, value, src))
            else:
                lines.append("  %-18s NOT SET" % field)
        lines.append("")
    return "\n".join(lines)
