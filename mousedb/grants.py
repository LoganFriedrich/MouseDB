"""Which grant each project belongs to, so a grant's projects sit together.

WHY: a lab's projects are grouped by grant -- one grant can fund two projects
(two wings of one study), each with its own cohorts. Shown flat, the second
project sat beside the grant's first as if unrelated (2026-10-30). The
exports put each project's folder inside its grant's folder:
``exports/current/<GRANT>/<PROJECT>/<COHORT>/``; a project in no grant keeps
``exports/current/<PROJECT>/<COHORT>/``.

The grants are a fact about the lab, so they live in the lab settings file
(``mousedb.sheet_projects.config_path``, key "grants"), never in the code:

    "grants": {"GrantA": ["PROJA", "PROJAB"]}

Inspect / change with ``mousedb grants``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from . import sheet_projects

SET_COMMAND = "mousedb grants --set <GRANT> <PROJECT> [<PROJECT> ...]"
_GRANT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_PROJECT_RE = re.compile(r"[A-Z]+")


def grants() -> Dict[str, List[str]]:
    """{grant: [projects]} from the lab settings; {} when none are set.
    Raises ValueError, naming the file, for an entry that cannot be used: a
    project under two grants, a name that cannot be a folder."""
    try:
        raw = sheet_projects._read().get("grants") or {}
    except (OSError, ValueError) as e:
        raise ValueError("cannot read the lab settings file %s: %s"
                         % (sheet_projects.config_path(), e)) from None
    seen: Dict[str, str] = {}
    out: Dict[str, List[str]] = {}
    for grant, projects in sorted(raw.items()):
        _check(grant, projects)
        for p in projects:
            if p in seen:
                raise ValueError("%s: project %s is under two grants (%s and %s)"
                                 % (sheet_projects.config_path(), p, seen[p], grant))
            seen[p] = grant
        out[grant] = list(projects)
    return out


def _check(grant, projects) -> None:
    if not isinstance(grant, str) or not _GRANT_RE.fullmatch(grant):
        raise ValueError("'%s' cannot be a grant's folder name: letters, digits, - and _ "
                         "only, starting with a letter" % grant)
    if (not isinstance(projects, list) or not projects
            or not all(isinstance(p, str) and _PROJECT_RE.fullmatch(p) for p in projects)):
        raise ValueError("grant %s: give its projects as project codes (capital letters, "
                         "as in the cohort ids), for example PROJA PROJAB" % grant)


def grant_of(project: str) -> Optional[str]:
    """The grant a project is listed under, or None."""
    project = str(project).upper()
    for grant, projects in grants().items():
        if project in projects:
            return grant
    return None


def set_grant(grant: str, projects: List[str]) -> Path:
    """Record (or replace) one grant's projects in the lab settings file;
    an empty list removes the grant. Other settings are kept."""
    projects = [str(p).strip().upper() for p in projects]
    p = sheet_projects.config_path()
    if p is None:
        raise ValueError("mousedb_root is not set, so there is no lab settings file to "
                         "write to; run 'mousedb config --set mousedb_root <folder>' first")
    data = sheet_projects._read()
    g = data.setdefault("grants", {})
    if projects:
        _check(grant, projects)
        for other, theirs in g.items():
            clash = set(theirs) & set(projects)
            if other != grant and clash:
                raise ValueError("%s already belongs to grant %s" % (", ".join(sorted(clash)), other))
        g[grant] = projects
    else:
        g.pop(grant, None)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p
