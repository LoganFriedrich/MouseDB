"""Where the cohort tracking spreadsheets live, and how to get a fresh one.

Each cohort has one Excel tracking sheet -- ``Connectome_{NN}_Animal_Tracking.xlsx``
-- and it is the source of record for that cohort's animals: subject list, weights,
manual tray and ramp scoring, injury and injection details. ``mousedb import`` reads
them to populate the database.

The location is NOT in this file, and must not be. Two reasons:

  1. It is lab-specific. Another lab running this code keeps its sheets
     somewhere else entirely, and should not have to edit source to say so.
  2. This repository is public. A synced-folder path carries a username, a
     drive letter, and an organisation's internal folder names.

So the path lives in local configuration that is never committed, and this module
only knows the FILENAME pattern, which is a property of the project's data rather
than of any one machine.

Resolution order, first hit wins:

  1. ``MOUSEDB_COHORT_SHEETS`` environment variable.
  2. ``cohort_sheets_dir`` in ``~/.mousedb/config.json``.
  3. Nothing -- and callers say so loudly, with instructions.

There is deliberately no built-in fallback path. The previous arrangement was a
hardcoded default on the ``import`` command pointing at a directory that had since
been moved. The command found no files and reported that as though there were
none to import, and anyone going looking found the snapshots under
``Databases/_archive/`` instead -- which are stale. The cohort 05 copy there is a
92 KB stub with an empty subject table; the live sheet is 359 KB, was last edited
2026-08-21, and carries 2,234 rows of tray scoring, 273 weights, and the injury
and injection details. A wrong default that silently resolves is worse than no
default, because it produces a confident wrong answer.

READ-ONLY. The configured folder is a synced share and this code must never
write into it -- not the sheets, not a sibling file, not a lock file. Everything
here reads; fetch_cohort_sheet exists precisely so that a tool which might
open a workbook for writing gets a copy in its own working directory instead.
Writing tools in cohort_tools already emit to a separate output directory
under a distinct name; keep it that way.

Setup:
    mousedb cohort-sheets --discover        # find it and save it
    mousedb cohort-sheets --set "<path>"    # or say where it is
    mousedb cohort-sheets                   # show what is configured

Usage:
    from mousedb.cohort_sheets import cohort_sheets_dir, find_cohort_sheet

    d = cohort_sheets_dir()                          # None if unconfigured
    p = find_cohort_sheet("CNT_05")                  # the live sheet
    fresh = fetch_cohort_sheet("CNT_05", work_dir)   # a working copy to read
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import List, Optional

CONFIG_PATH = Path.home() / ".mousedb" / "config.json"
ENV_VAR = "MOUSEDB_COHORT_SHEETS"

# A property of the project's data, not of any lab's filesystem.
_SHEET_RE = re.compile(r"Connectome_(\d{1,2})_Animal_Tracking.*\.xlsx$", re.I)

# Never read a tracking sheet from a folder like these. A snapshot that looks
# like a source is worse than no copy at all -- see the module docstring.
_NOT_A_SOURCE = ("_archive", "old_cohort_scripts", "generated", "archive", "backup")

# How deep to look under a search root when discovering. The sheets sit a few
# folders down in a synced drive; this is not a whole-disk crawl.
_DISCOVER_MAX_DEPTH = 6


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

def _read_config() -> dict:
    try:
        if CONFIG_PATH.is_file():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def set_cohort_sheets_dir(path) -> Path:
    """Record where this machine's cohort sheets are. Returns the config path."""
    cfg = _read_config()
    cfg["cohort_sheets_dir"] = str(Path(path))
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return CONFIG_PATH


def cohort_sheets_dir() -> Optional[Path]:
    """The configured directory of live cohort tracking sheets, or None.

    Returns a directory only if it exists AND actually contains a tracking
    sheet: an empty directory of the right name is not the right directory.
    """
    for raw in (os.environ.get(ENV_VAR), _read_config().get("cohort_sheets_dir")):
        if not raw:
            continue
        d = Path(raw)
        try:
            if d.is_dir() and any(_SHEET_RE.search(f.name) for f in d.iterdir()):
                return d
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# discovery -- convenience for first run, no lab-specific paths
# ---------------------------------------------------------------------------

def _search_roots() -> List[Path]:
    """Generic places a synced lab folder tends to live on this machine."""
    roots = []
    for var in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer", "USERPROFILE", "HOME"):
        v = os.environ.get(var)
        if v:
            roots.append(Path(v))
    seen, out = set(), []
    for r in roots:
        if r.exists() and str(r).lower() not in seen:
            seen.add(str(r).lower())
            out.append(r)
    return out


def discover(extra_roots=()) -> List[Path]:
    """Directories on this machine that hold cohort tracking sheets.

    Bounded-depth walk of the search roots. Snapshot folders are excluded.
    """
    found: List[Path] = []
    roots = [Path(r) for r in extra_roots] + _search_roots()
    for root in roots:
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            if len(d.parts) - base_depth >= _DISCOVER_MAX_DEPTH:
                dirnames[:] = []
                continue
            # do not descend into snapshot folders, or hidden ones
            dirnames[:] = [n for n in dirnames
                           if not n.startswith(".")
                           and n.lower() not in _NOT_A_SOURCE]
            if any(_SHEET_RE.search(n) for n in filenames):
                if d not in found:
                    found.append(d)
    return found


# ---------------------------------------------------------------------------
# lookups
# ---------------------------------------------------------------------------

def is_stale_source(path) -> bool:
    """True if this path is a snapshot folder rather than the live sheets."""
    parts = {p.lower() for p in Path(path).parts}
    return any(marker in parts for marker in _NOT_A_SOURCE)


def _cohort_number(cohort: str) -> Optional[str]:
    """'CNT_05' / 'CNT05' / '5' / '05' -> '05'."""
    m = re.search(r"(\d{1,2})\s*$", str(cohort).strip())
    return "%02d" % int(m.group(1)) if m else None


def find_cohort_sheet(cohort: str, sheets_dir=None) -> Optional[Path]:
    """The live tracking sheet for a cohort, or None.

    Where a cohort has more than one file (``..._Animal_Tracking1.xlsx``, dated
    variants), the most recently modified wins -- that is the one being kept up.
    """
    d = Path(sheets_dir) if sheets_dir else cohort_sheets_dir()
    if d is None:
        return None
    n = _cohort_number(cohort)
    if n is None:
        return None
    hits = []
    try:
        for f in d.iterdir():
            m = _SHEET_RE.search(f.name)
            if m and "%02d" % int(m.group(1)) == n and not f.name.startswith("~"):
                hits.append(f)
    except OSError:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def fetch_cohort_sheet(cohort: str, dest_dir, sheets_dir=None) -> Optional[Path]:
    """Copy a cohort's live sheet into ``dest_dir`` and return the copy.

    A copy, because these live in a synced folder: reading in place competes
    with the sync, and opening one for writing can block a colleague. Returns
    None if the sheet cannot be found.
    """
    src = find_cohort_sheet(cohort, sheets_dir=sheets_dir)
    if src is None:
        return None
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.copy2(src, dest)
    return dest


def available_cohorts(sheets_dir=None) -> List[str]:
    """Cohort numbers that have a live tracking sheet, e.g. ['00','01', ...]."""
    d = Path(sheets_dir) if sheets_dir else cohort_sheets_dir()
    if d is None:
        return []
    found = set()
    try:
        for f in d.iterdir():
            m = _SHEET_RE.search(f.name)
            if m and not f.name.startswith("~"):
                found.add("%02d" % int(m.group(1)))
    except OSError:
        return []
    return sorted(found)


def describe() -> str:
    """What is configured, or how to configure it. For --help and for saying so
    out loud when an import finds nothing."""
    d = cohort_sheets_dir()
    if d is not None:
        cohorts = available_cohorts(d)
        return ("Cohort sheets: %s\n  cohorts with a sheet: %s"
                % (d, ", ".join(cohorts) if cohorts else "(none)"))
    return "\n".join([
        "No cohort tracking sheets are configured on this machine.",
        "",
        "They are the Excel files named Connectome_NN_Animal_Tracking.xlsx.",
        "Tell mousedb where they are, once:",
        "",
        "    mousedb cohort-sheets --discover      # look for them",
        '    mousedb cohort-sheets --set "<path>"  # or say where they are',
        "",
        "Or set the %s environment variable." % ENV_VAR,
        "Saved to %s, which is local to this machine and never committed."
        % CONFIG_PATH,
    ])
