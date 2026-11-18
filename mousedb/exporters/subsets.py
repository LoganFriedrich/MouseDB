"""Limited exports: a saved, filtered slice of the current exports.

WHY THIS EXISTS
---------------
A person often needs only part of what the current exports hold: some cohorts,
the last few sessions before the injury, one kind of tray, a handful of
columns, and a yes/no column saying which animals go into one analysis. Until
this existed every such slice was a one-off script, written again each time,
with nothing to say what it was cut from or how.

The rule this module keeps: **a limited export is a filtered copy of the
exports that already exist. It never recomputes a value.** Every cell in a
slice is a cell of exports/current, byte for byte. So a slice can never
disagree with the full export it was cut from, and every fix that reaches the
full export reaches the slice the next time it is made.

WHAT IT WRITES
--------------
``<mousedb_root>/exports/subsets/<name>/``::

    ODC_reaches_<name>.csv, reaches_<name>_summary.csv, reaches_<name>_analysis.csv,
    sessions_<name>_analysis.csv, ODC_sessions_<name>.csv
                                   the chosen kinds of file, every chosen cohort in one
                                   file per kind (cohort order, then the source order)
    <kind>_DATA_DICTIONARY.csv     the full export's dictionary of that kind, cut down to
                                   the columns kept (plus the yes/no column, if any)
    analysis_EXCLUSIONS.csv        the full export's exclusions, filtered the same way
    SUBSET_MANIFEST.json           the filter, the source snapshot generation, when it was
                                   written, row counts, which sessions were chosen
    README.txt                     the same, in plain words

A previous slice of the same name is MOVED to the archive when the new one is
put in place, never deleted: ``<mousedb_root>/_archived/exports_subsets/<name>/
<time>/``. A remake that cannot put every new file in place puts the previous
slice back (see _place), so the folder holds one complete set or the other. ``exports/subsets/SUBSETS_STATUS.json`` records every attempt to make
each slice -- including one that failed, and why -- so the Where Is My Data tab
can show it.

WHERE A SLICE IS DEFINED
------------------------
A definition ("profile") is a fact about the lab, not about the tool, so it lives
in the lab settings file (mousedb.sheet_projects.config_path, beside the
database: one file for every machine and every login), never in this code::

    "export_subsets": {
      "<name>": {
        "cohorts":    ["PROJA_01", "PROJA_02"],      # or "project": "PROJA" for all of it
        "tray_types": ["P"],                         # P pillar, E easy, F flat; [] = all
        "window":     {"type": "last_before_injury", "n": 3, "tray_type": "P"},
                      # or {"type": "dates", "from": "2026-01-01", "to": "2026-02-01"}
                      # or {"type": "all"}
        "kinds":      ["ODC_reaches", "sessions_analysis"],   # default: every kind
        "columns":    {"ODC_reaches": ["subject_id", "video_name", "..."]},
                      # a kind not named keeps every column
        "flag":       {"column": "<yes/no column name>", "rule": "<rule name or null>"},
        "nightly":    true                           # remake after the nightly export
      }
    },
    "export_subset_flag_rules": {
      "<rule name>": {"type": "listed", "animals": ["PROJA_01_03"]},
      "<rule name>": {"type": "share_at_least", "kind": "sessions_analysis",
                      "numerator": "<column>", "denominator": "<column>", "percent": 30}
    },
    "export_subsets_replaced": {
      "<name>": [{"until": "<time>", "why": "replaced" | "forgotten", "definition": {...}}]
    }

A saved definition is replaced only when that is asked for (save_profile's
``replace``), and a replaced or forgotten one is kept under
export_subsets_replaced: a definition is a person's work, never overwritten
or dropped without a trace.

THE SESSION WINDOW ("last N before the injury")
-----------------------------------------------
The sheet decides when things happened. For each animal: the dates on which the
cohort's tracking sheet records hand scores for a tray of the window's tray type
(the snapshot's pellet_scores table, imported from the sheet), strictly before
the cohort's FIRST injury (the earliest contusion surgery date of any animal of
that cohort, in the snapshot's surgeries table); the last N of those dates. An
animal with fewer than N such dates keeps the ones it has (the manifest lists
each animal's dates). A cohort with no injury date in the sheet has no window,
and its animals contribute no rows; the manifest says so.

The window is read from the SAME snapshot generation the current exports were
built from. If the snapshot has been refreshed since (the nightly job between
its two steps), the slice is refused rather than cut with one night's sessions
from another night's files.

A full export whose MANIFEST.json does not say it is complete (a part of it
failed, so some of its files are an earlier run's or missing) is never cut, by
any path: the nightly remake, the button, --run or a dry run.

WHICH ROWS A FILTER KEEPS
-------------------------
Each row's animal, date and tray type come from the row itself: the animal from
its video name (the pipeline's naming, so letter cohorts need no decoding) or,
in the ODC session file, its Animal column; the date and tray type from the
file's own columns. An ODC session row is one animal-day holding up to four
trays: it is kept when its day is in the window and ANY of its trays is of a
chosen type. An exclusion with no video (one that covers a whole cohort) is kept
for every chosen cohort.

COLUMNS
-------
A requested column that NONE of the chosen cohorts' files of that kind has is
refused before anything is written, with its name: it is never silently
dropped. A column that some cohorts' files lack is filled for them with the
kind's word for absent ("Not recorded", or NA in the analysis files), exactly as
the full export's combined files do, and the manifest says which.

The yes/no column holds, per animal, "yes" or "no" from the named rule. With no
rule it is left blank -- written as the kind's word for absent, because an ODC
file may not hold an empty cell. Rules only READ the slice; they never change a
value in it.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

from . import layout

PROFILES_KEY = "export_subsets"
RULES_KEY = "export_subset_flag_rules"
# Definitions that were replaced or forgotten, newest last per name: a saved
# definition is a person's work and is never simply overwritten (see
# save_profile). Kept in the same lab settings file, so nothing new is made.
REPLACED_KEY = "export_subsets_replaced"
MANIFEST_NAME = "SUBSET_MANIFEST.json"
STATUS_NAME = "SUBSETS_STATUS.json"
EXCLUSIONS_NAME = "analysis_EXCLUSIONS.csv"
SUBSETS_DIR = "subsets"

ALL_KINDS = tuple(layout.KINDS)
WINDOW_TYPES = ("all", "last_before_injury", "dates")
RULE_TYPES = ("listed", "share_at_least")
# The tray types the pipeline names (exporters.trays): P pillar, E easy, F flat.
TRAY_WORDS = {"PILLAR": "P", "EASY": "E", "FLAT": "F"}
# The surgery type the schema calls the injury (schema.Surgery's check).
INJURY_SURGERY = "contusion"

# Where each kind keeps the row's date, tray type, video and animal. The animal
# is read from the video name when there is one (import_reaches.parse_subject_id),
# because letter cohorts show their animals decoded (a letter and a number) in
# some kinds; the video name always carries the pipeline's own id.
ROW_KEYS = {
    "ODC_reaches": {"date": "session_date", "tray": "tray_type", "video": "video_name",
                    "animal": "subject_id"},
    "reaches_summary": {"date": "session_date", "tray": "tray_type", "video": "session_video",
                        "animal": "subject"},
    "reaches_analysis": {"date": "test_date", "tray": "tray_type", "video": "video_name",
                         "animal": "subject_id"},
    "sessions_analysis": {"date": "test_date", "tray": "tray_type", "video": "video_name",
                          "animal": "subject_id"},
    "ODC_sessions": {"date": "Date", "tray": None, "video": None, "animal": "Animal"},
}
ODC_SESSION_TRAY_COLUMNS = ("Tray1_Type", "Tray2_Type", "Tray3_Type", "Tray4_Type")

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_COHORT_RE = re.compile(r"[A-Z]+_\d{2}")
_COLUMN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

# Exit codes of the program (main): distinct so the window can say what happened.
EXIT_OK, EXIT_FAILED, EXIT_BUSY, EXIT_REFUSED = 0, 1, 3, 6


class SubsetRefused(ValueError):
    """The definition, or the state of the exports, does not allow this slice.
    Nothing was written; the message says what to change."""


class SubsetNotPlaced(RuntimeError):
    """The new files of a slice could not be put in place. The message says what
    state the slice's folder was left in (see _place)."""


class DefinitionExists(SubsetRefused):
    """A different definition is already saved under this name, and replacing
    it was not asked for. Nothing was changed."""


# --------------------------------------------------------------- definitions

def _settings_path() -> Path:
    from .. import sheet_projects
    p = sheet_projects.config_path()
    if p is None:
        raise SubsetRefused("mousedb_root is not set, so there is no lab settings file to keep "
                            "limited-export definitions in; run 'mousedb config --set "
                            "mousedb_root <folder>' first")
    return p


def _read_settings() -> dict:
    p = _settings_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SubsetRefused("the lab settings file %s cannot be read (%s: %s); nothing was "
                            "changed" % (p, type(e).__name__, e)) from None
    if not isinstance(data, dict):
        raise SubsetRefused("the lab settings file %s does not hold named settings" % p)
    return data


def _update_settings(change) -> Path:
    """Change the lab settings file in one locked read-change-write
    (sheet_projects.update_settings, which says why), keeping every other
    setting in it (sheet projects, grants). ``change(data)`` runs under the
    lock and may raise SubsetRefused: nothing is written then. Any other
    failure to read or save the file is a SubsetRefused too, saying that
    nothing was changed."""
    from .. import sheet_projects
    p = _settings_path()
    try:
        return sheet_projects.update_settings(change)
    except SubsetRefused:
        raise
    except sheet_projects.SettingsBusy as e:
        raise SubsetRefused(str(e)) from None
    except (OSError, ValueError) as e:
        raise SubsetRefused("the lab settings file %s could not be saved (%s: %s); nothing "
                            "was changed" % (p, type(e).__name__, e)) from None


def profiles() -> Dict[str, dict]:
    """Every saved definition, by name."""
    raw = _read_settings().get(PROFILES_KEY) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def flag_rules() -> Dict[str, dict]:
    raw = _read_settings().get(RULES_KEY) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def tray_code(t: str) -> str:
    """"Pillar" or "P" -> "P". Anything else is refused by validate()."""
    t = str(t).strip().upper()
    return TRAY_WORDS.get(t, t)


def validate(name: str, profile: dict, rules: Optional[Dict[str, dict]] = None) -> dict:
    """The definition in its stored form, or SubsetRefused naming what is wrong.

    Checks only what can be checked without the exports (names, shapes, known
    words); the columns are checked against the files when the slice is made."""
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise SubsetRefused("'%s' cannot be a limited export's name: letters, digits, - and _ "
                            "only, starting with a letter or digit (it becomes a folder name)"
                            % name)
    if not isinstance(profile, dict):
        raise SubsetRefused("limited export %s: its definition must be a set of named values"
                            % name)
    p = dict(profile)
    unknown = set(p) - {"project", "cohorts", "tray_types", "window", "kinds", "columns",
                        "flag", "nightly", "description"}
    if unknown:
        raise SubsetRefused("limited export %s: unknown setting(s) %s" % (name, ", ".join(sorted(unknown))))
    cohorts = [str(c).strip().upper() for c in (p.get("cohorts") or [])]
    bad = [c for c in cohorts if not _COHORT_RE.fullmatch(c)]
    if bad:
        raise SubsetRefused("limited export %s: %s is not a cohort id (letters, underscore, two "
                            "digits, e.g. PROJA_01)" % (name, ", ".join(bad)))
    project = str(p.get("project") or "").strip().upper()
    if project and not re.fullmatch(r"[A-Z]+", project):
        raise SubsetRefused("limited export %s: '%s' is not a project code (capital letters)"
                            % (name, project))
    if not cohorts and not project:
        raise SubsetRefused("limited export %s: name its cohorts, or a project for all of its "
                            "cohorts" % name)
    if project and cohorts and any(layout.project_of(c) != project for c in cohorts):
        raise SubsetRefused("limited export %s: cohorts %s are not all of project %s"
                            % (name, ", ".join(cohorts), project))
    trays = [tray_code(t) for t in (p.get("tray_types") or [])]
    bad = [t for t in trays if t not in TRAY_WORDS.values()]
    if bad:
        raise SubsetRefused("limited export %s: unknown tray type(s) %s (Pillar/P, Easy/E, Flat/F)"
                            % (name, ", ".join(bad)))
    window = dict(p.get("window") or {"type": "all"})
    wtype = window.get("type", "all")
    if wtype not in WINDOW_TYPES:
        raise SubsetRefused("limited export %s: window type '%s' is not one of %s"
                            % (name, wtype, ", ".join(WINDOW_TYPES)))
    if wtype == "last_before_injury":
        try:
            n = int(window.get("n"))
        except (TypeError, ValueError):
            n = 0
        if n < 1:
            raise SubsetRefused("limited export %s: say how many sessions before the injury "
                                "(a whole number, 1 or more)" % name)
        wt = tray_code(window.get("tray_type") or "")
        if wt not in TRAY_WORDS.values():
            raise SubsetRefused("limited export %s: say which tray type's sessions count "
                                "(Pillar/P, Easy/E or Flat/F)" % name)
        window = {"type": wtype, "n": n, "tray_type": wt}
    elif wtype == "dates":
        out = {"type": wtype}
        for k in ("from", "to"):
            v = window.get(k)
            if v:
                try:
                    out[k] = pd.Timestamp(str(v)).strftime("%Y-%m-%d")
                except ValueError:
                    raise SubsetRefused("limited export %s: '%s' is not a date (YYYY-MM-DD)"
                                        % (name, v)) from None
        if len(out) == 1:
            raise SubsetRefused("limited export %s: a date window needs a first date, a last "
                                "date, or both" % name)
        window = out
    else:
        window = {"type": "all"}
    kinds = list(p.get("kinds") or ALL_KINDS)
    bad = [k for k in kinds if k not in layout.KINDS]
    if bad:
        raise SubsetRefused("limited export %s: unknown kind(s) of file %s (known: %s)"
                            % (name, ", ".join(bad), ", ".join(ALL_KINDS)))
    columns = p.get("columns") or {}
    if not isinstance(columns, dict):
        raise SubsetRefused("limited export %s: columns must be given per kind of file, "
                            "{kind: [column, ...]}" % name)
    cols_out = {}
    for kind, cols in columns.items():
        if kind not in kinds:
            raise SubsetRefused("limited export %s: columns are given for %s, which is not one "
                                "of its kinds of file (%s)" % (name, kind, ", ".join(kinds)))
        cols = [str(c).strip() for c in (cols or []) if str(c).strip()]
        if not cols:
            continue
        dup = sorted({c for c in cols if cols.count(c) > 1})
        if dup:
            raise SubsetRefused("limited export %s: column(s) %s named twice for %s"
                                % (name, ", ".join(dup), kind))
        cols_out[kind] = cols
    flag = p.get("flag") or None
    if flag:
        col = str(flag.get("column") or "").strip()
        if not _COLUMN_RE.fullmatch(col):
            raise SubsetRefused("limited export %s: '%s' cannot be a column name (letters, "
                                "digits and _, starting with a letter)" % (name, col))
        rule = flag.get("rule") or None
        if rule is not None:
            known = flag_rules() if rules is None else rules
            if rule not in known:
                raise SubsetRefused("limited export %s: there is no yes/no rule named '%s' (rules "
                                    "in the lab settings: %s)" % (name, rule,
                                                                  ", ".join(sorted(known)) or "none"))
            validate_rule(rule, known[rule])
        flag = {"column": col, "rule": rule}
    out = {"cohorts": cohorts} if cohorts else {"project": project}
    if cohorts and project:
        out["project"] = project
    out.update({"tray_types": trays, "window": window, "kinds": kinds})
    if cols_out:
        out["columns"] = cols_out
    if flag:
        out["flag"] = flag
    out["nightly"] = bool(p.get("nightly", True))
    if p.get("description"):
        out["description"] = str(p["description"])
    return out


def validate_rule(name: str, rule: dict) -> dict:
    if not isinstance(rule, dict) or rule.get("type") not in RULE_TYPES:
        raise SubsetRefused("yes/no rule %s: its type must be one of %s" % (name, ", ".join(RULE_TYPES)))
    if rule["type"] == "listed":
        animals = rule.get("animals")
        if not isinstance(animals, list):
            raise SubsetRefused("yes/no rule %s: 'animals' must be a list of animal ids" % name)
        return {"type": "listed", "animals": [str(a).strip().upper() for a in animals]}
    kind = rule.get("kind")
    if kind not in layout.KINDS:
        raise SubsetRefused("yes/no rule %s: 'kind' must be one of %s" % (name, ", ".join(ALL_KINDS)))
    for k in ("numerator", "denominator"):
        if not rule.get(k):
            raise SubsetRefused("yes/no rule %s: name the %s column" % (name, k))
    try:
        pct = float(rule.get("percent"))
    except (TypeError, ValueError):
        raise SubsetRefused("yes/no rule %s: 'percent' must be a number" % name) from None
    return {"type": "share_at_least", "kind": kind, "numerator": str(rule["numerator"]),
            "denominator": str(rule["denominator"]), "percent": pct}


def _section(data: dict, key: str) -> dict:
    """``data[key]`` as a dict, made if absent; refused if it holds something else
    (written back as it was, it would be lost)."""
    sec = data.setdefault(key, {})
    if not isinstance(sec, dict):
        raise SubsetRefused("the lab settings file's '%s' is not a set of named entries; "
                            "nothing was changed" % key)
    return sec


def _keep_old(data: dict, name: str, old: dict, why: str) -> None:
    """Keep a definition that is being replaced or forgotten, with when and why,
    under REPLACED_KEY in the lab settings file."""
    entry = {"until": datetime.now().isoformat(timespec="seconds"), "why": why,
             "definition": old}
    kept = _section(data, REPLACED_KEY).setdefault(name, [])
    if not isinstance(kept, list):
        raise SubsetRefused("the lab settings file's '%s' entry for %s is not a list; nothing "
                            "was changed" % (REPLACED_KEY, name))
    kept.append(entry)


def save_profile(name: str, profile: dict, replace: bool = False) -> Path:
    """Validate and store one definition. Every other setting in the lab
    settings file is kept.

    A DIFFERENT definition already saved under the name is replaced only when
    ``replace`` is True (the window asks the person first; the command needs
    --replace), and the one replaced is kept under REPLACED_KEY with the time.
    WHY: a saved definition is a person's work, remade every night; typing a
    name that is already taken used to overwrite it with no question and no
    copy. Saving exactly what is already saved changes nothing and asks
    nothing."""
    def change(data: dict) -> None:
        # Validated under the lock, against the rules as they are now.
        rules = data.get(RULES_KEY) if isinstance(data.get(RULES_KEY), dict) else {}
        clean = validate(name, profile, rules)
        saved = _section(data, PROFILES_KEY)
        old = saved.get(name)
        if old is not None and old != clean:
            if not replace:
                raise DefinitionExists(
                    "a limited export named '%s' is already saved, with a different "
                    "definition; nothing was changed. To replace it, say so (answer Yes in "
                    "the window, or add --replace to the command); the one it replaces is "
                    "kept in the lab settings file under '%s'. Or pick another name."
                    % (name, REPLACED_KEY))
            _keep_old(data, name, old, "replaced")
        saved[name] = clean
    return _update_settings(change)


def save_rule(name: str, rule: dict) -> Path:
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise SubsetRefused("'%s' cannot be a rule's name: letters, digits, - and _ only" % name)
    clean = validate_rule(name, rule)

    def change(data: dict) -> None:
        _section(data, RULES_KEY)[name] = clean
    return _update_settings(change)


def remove_profile(name: str) -> Path:
    """Forget a definition. Its slices already written stay where they are, and
    the definition itself is kept under REPLACED_KEY (archive, never delete)."""
    def change(data: dict) -> None:
        saved = _section(data, PROFILES_KEY)
        if name not in saved:
            raise SubsetRefused("there is no limited export named '%s'" % name)
        _keep_old(data, name, saved.pop(name), "forgotten")
    return _update_settings(change)


# ----------------------------------------------------------------- locations

def subsets_root() -> Path:
    """<mousedb_root>/exports/subsets."""
    from ..config import export_path, require
    require("mousedb_root")
    return export_path() / SUBSETS_DIR


def archive_root(out_root: Path) -> Path:
    """Where replaced slices go: <mousedb_root>/_archived/exports_subsets when the
    slices are in the usual <mousedb_root>/exports/subsets (the same place the full
    export's replaced files go, beside it), else <out_root>/_archived."""
    out_root = Path(out_root)
    if out_root.name == SUBSETS_DIR and out_root.parent.name == "exports":
        return out_root.parent.parent / "_archived" / "exports_subsets"
    return out_root / "_archived"


# ------------------------------------------------------------- the selection

def _read_manifest(current: Path) -> dict:
    try:
        return json.loads((Path(current) / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SubsetRefused("the current exports have no readable MANIFEST.json in %s (%s); "
                            "make the full export first" % (current, type(e).__name__)) from None


def _require_complete(name: str, manifest: dict, current: Path) -> None:
    """Refuse a slice of a full export that does not say it is complete.

    WHY: when a part of the full export fails, its files are the previous run's
    (or missing) while the manifest already carries the new snapshot
    generation. A slice cut from it would pair one night's files with another
    night's session window -- exactly what the generation check exists to
    prevent -- and nothing in the slice would show it. The nightly remake only
    runs after a complete export; the button and ``--run`` must hold to the
    same rule, so the check sits here, where every way of making a slice
    passes (a dry run included)."""
    if manifest.get("complete") is True:
        return
    problems = [str(x) for x in (manifest.get("problems") or [])]
    said = ("; ".join(problems[:5]) + (" (and %d more)" % (len(problems) - 5)
                                       if len(problems) > 5 else "")) if problems else         "it names no problem"
    raise SubsetRefused(
        "limited export %s: the full export in %s is not complete (its MANIFEST.json "
        "says complete=%s: %s). Some of its files may be from an earlier run or missing, "
        "so a slice of it could mix two runs. Nothing was written. Rewrite the full "
        "export ('Refresh exports now' on the Where Is My Data tab, or wait for the "
        "nightly job) and make this again once it is complete."
        % (name, current, manifest.get("complete"), said))


def _cohorts_of(profile: dict, current: Path) -> List[str]:
    """The cohort ids the slice covers, in cohort order."""
    if profile.get("cohorts"):
        return sorted(profile["cohorts"])
    project = profile["project"]
    pdir = layout.project_dir(current, project)
    if not pdir.is_dir():
        raise SubsetRefused("the current exports have no folder for project %s (%s)"
                            % (project, pdir))
    found = set()
    for d in pdir.iterdir():
        if d.is_dir() and not d.name.startswith("_"):
            cid = re.sub(r"_[A-Z]$", "", d.name)
            if layout.project_of(cid) == project and _COHORT_RE.fullmatch(cid):
                found.add(cid)
    return sorted(found)


def _norm_date(v) -> str:
    return str(v)[:10]


def session_window(profile: dict, cohorts: List[str], snapshot_dir: Optional[Path],
                   expect_generation: Optional[str]) -> Tuple[Optional[set], dict]:
    """The (animal, date) pairs the window keeps -- None when every session is
    kept -- and what the manifest should say about how they were chosen."""
    window = profile["window"]
    if window["type"] == "all":
        return None, {"type": "all"}
    if window["type"] == "dates":
        return None, dict(window)
    from .. import snapshot_generation
    if not snapshot_dir:
        raise SubsetRefused("the session window comes from the analysis snapshot, and "
                            "snapshot_dir is not set (mousedb config --set snapshot_dir <folder>)")
    snapshot_dir = Path(snapshot_dir)

    def read():
        ps = pd.read_parquet(snapshot_dir / "pellet_scores.parquet",
                             columns=["subject_id", "session_date", "tray_type"])
        su = pd.read_parquet(snapshot_dir / "surgeries.parquet",
                             columns=["subject_id", "surgery_type", "surgery_date"])
        return ps, su

    try:
        (ps, su), generation = snapshot_generation.read_consistent(snapshot_dir, read)
    except snapshot_generation.SnapshotNotConsistent as e:
        raise SubsetRefused("the session window could not be read: %s" % e) from None
    except (OSError, ValueError, KeyError) as e:
        raise SubsetRefused("the session window could not be read from the snapshot in %s "
                            "(%s: %s)" % (snapshot_dir, type(e).__name__, e)) from None
    # Compared as they are, None included. WHY: a full export written from a
    # snapshot made before refreshes were numbered records no generation
    # (exporters.current writes None). Skipping the check then would cut the
    # window from whatever the snapshot holds now, against files of another
    # night. Only when BOTH are unnumbered (a snapshot still unnumbered today)
    # can nothing be compared; that case is as before.
    if generation != expect_generation:
        if expect_generation is None:
            raise SubsetRefused(
                "the current exports do not record which snapshot they were made from "
                "(they were written before snapshot refreshes were numbered), and the "
                "snapshot is now %s: the sessions cannot be shown to come from the same "
                "night's records as the files. Rewrite the full export ('Refresh exports "
                "now' on the Where Is My Data tab) and try again." % generation)
        raise SubsetRefused(
            "the current exports were made from snapshot %s, but the snapshot is now %s: the "
            "sessions would be chosen from a different night's records than the files. This "
            "happens while the nightly job is between its two steps; try again once the full "
            "export has been rewritten (or press 'Refresh exports now')."
            % (expect_generation, generation or "an unnumbered one"))
    n, wt = window["n"], window["tray_type"]
    cohort_of = lambda s: str(s).rsplit("_", 1)[0]
    ps = ps[ps["subject_id"].map(cohort_of).isin(cohorts)].copy()
    ps["d"] = ps["session_date"].map(_norm_date)
    ps = ps[ps["tray_type"].fillna("").astype(str).str.upper() == wt]
    su = su[(su["surgery_type"] == INJURY_SURGERY) & su["surgery_date"].notna()].copy()
    su["cohort"] = su["subject_id"].map(cohort_of)
    su["d"] = su["surgery_date"].map(_norm_date)
    first = su[su["cohort"].isin(cohorts)].groupby("cohort")["d"].min().to_dict()
    keep, chosen, short = set(), {}, {}
    for subject, g in ps.groupby("subject_id"):
        injury = first.get(cohort_of(subject))
        if not injury:
            continue
        dates = sorted(d for d in g["d"].unique() if d < injury)[-n:]
        if dates:
            chosen[subject] = dates
            keep.update((subject, d) for d in dates)
            if len(dates) < n:
                short[subject] = len(dates)
    info = {"type": window["type"], "n": n, "tray_type": wt,
            "rule": ("each animal's last %d dates with %s-tray hand scores in the tracking sheet, "
                     "strictly before its cohort's first %s date" % (n, wt, INJURY_SURGERY)),
            "snapshot_generation": generation,
            "first_injury_date": {c: first.get(c) for c in cohorts},
            "cohorts_without_injury_date": [c for c in cohorts if not first.get(c)],
            "sessions_chosen": chosen,
            "animals_with_fewer_sessions": short}
    return keep, info


# ------------------------------------------------------------------- filters

def _row_animal(frame: pd.DataFrame, kind: str) -> pd.Series:
    from ..import_reaches import parse_subject_id
    keys = ROW_KEYS[kind]
    if keys["video"] and keys["video"] in frame.columns:
        videos = frame[keys["video"]]
        ids = {v: parse_subject_id(str(v)) or "" for v in videos.unique()}
        return videos.map(ids)
    return frame.get(keys["animal"], pd.Series("", index=frame.index)).astype(str).str.upper()


def _row_mask(frame: pd.DataFrame, kind: str, profile: dict, keep: Optional[set]) -> pd.Series:
    """True on the rows the filter keeps. ``frame`` holds text cells."""
    keys = ROW_KEYS[kind]
    mask = pd.Series(True, index=frame.index)
    trays = profile.get("tray_types") or []
    if trays:
        if kind == "ODC_sessions":
            any_tray = pd.Series(False, index=frame.index)
            for c in ODC_SESSION_TRAY_COLUMNS:
                if c in frame.columns:
                    any_tray |= frame[c].str.strip().str.upper().isin(trays)
            mask &= any_tray
        else:
            mask &= frame[keys["tray"]].str.strip().str.upper().isin(trays)
    window = profile["window"]
    if window["type"] == "dates":
        d = frame[keys["date"]].map(_norm_date)
        if window.get("from"):
            mask &= d >= window["from"]
        if window.get("to"):
            mask &= d <= window["to"]
    elif keep is not None:
        animal = _row_animal(frame, kind)
        d = frame[keys["date"]].map(_norm_date)
        mask &= pd.Series([(a, x) in keep for a, x in zip(animal, d)], index=frame.index)
    return mask


def _exclusion_mask(frame: pd.DataFrame, profile: dict, cohorts: List[str],
                    keep: Optional[set]) -> pd.Series:
    """The exclusions the slice keeps: those of its cohorts whose video passes the
    same filter; one with no video covers its whole cohort and is kept."""
    from ..import_reaches import parse_subject_id, parse_video_metadata
    out = []
    trays = profile.get("tray_types") or []
    window = profile["window"]
    for cohort, video, date in zip(frame.get("cohort", ""), frame.get("video_name", ""),
                                   frame.get("date", "")):
        if str(cohort).strip().upper() not in cohorts:
            out.append(False)
            continue
        video = str(video).strip()
        if not video:
            out.append(True)
            continue
        meta = parse_video_metadata(video)
        d = _norm_date(date) if str(date).strip() else (meta.get("session_date") or "")
        ok = not trays or (meta.get("tray_type") or "") in trays
        if window["type"] == "dates":
            ok = ok and (not window.get("from") or d >= window["from"]) \
                and (not window.get("to") or d <= window["to"])
        elif keep is not None:
            ok = ok and (parse_subject_id(video), d) in keep
        out.append(ok)
    return pd.Series(out, index=frame.index, dtype=bool)


# --------------------------------------------------------------------- rules

def _flag_values(profile: dict, rules: Dict[str, dict], sources: Dict[str, List[Tuple[str, Path]]],
                 cohorts: List[str], keep: Optional[set]) -> Tuple[Optional[Dict[str, str]], str]:
    """{animal: "yes"/"no"} from the profile's rule, and the sentence the
    dictionary uses to say how. None: no rule, the column is left blank."""
    flag = profile.get("flag")
    if not flag or not flag.get("rule"):
        return None, ("Left blank on purpose: no rule has been set for it. It is the column "
                      "to fill once the rule for it is decided.")
    rule = validate_rule(flag["rule"], rules[flag["rule"]])
    if rule["type"] == "listed":
        listed = set(rule["animals"])
        return ({"__listed__": listed},
                "yes for the animals listed in the lab's rule '%s', no for every other animal."
                % flag["rule"])
    kind = rule["kind"]
    paths = sources.get(kind)
    if paths is None:
        paths = [(c, p) for c, p in _find_sources(kind, cohorts, sources.get("__root__"))]
    num, den = rule["numerator"], rule["denominator"]
    tot: Dict[str, List[float]] = {}
    for _, path in paths:
        head = layout._header(path)
        for c in (num, den):
            if c not in head:
                raise SubsetRefused("yes/no rule %s reads column %s, which %s does not have"
                                    % (flag["rule"], c, path.name))
        for chunk in pd.read_csv(path, dtype=str, keep_default_na=False, chunksize=layout.CHUNK_ROWS):
            chunk = chunk[_row_mask(chunk, kind, profile, keep)]
            animal = _row_animal(chunk, kind)
            n = pd.to_numeric(chunk[num], errors="coerce")
            d = pd.to_numeric(chunk[den], errors="coerce")
            for a, x, y in zip(animal, n, d):
                if pd.notna(x) and pd.notna(y):
                    t = tot.setdefault(a, [0.0, 0.0])
                    t[0] += x
                    t[1] += y
    values = {a: ("yes" if t[1] > 0 and 100.0 * t[0] / t[1] >= rule["percent"] else "no")
              for a, t in tot.items() if t[1] > 0}
    how = ("yes when the animal's total %s divided by its total %s, over this file's rows of the "
           "%s file, is at least %g percent; no when below; blank when the animal has no such "
           "rows (the lab's rule '%s')." % (num, den, kind, rule["percent"], flag["rule"]))
    return values, how


# ------------------------------------------------------------------ building

def _find_sources(kind: str, cohorts: List[str], current: Path) -> List[Tuple[str, Path]]:
    found = []
    for c in cohorts:
        try:
            found.append((c, layout.locate(kind, c, current)))
        except FileNotFoundError:
            pass
    return found


def plan(name: str, profile: dict, current: Path, snapshot_dir: Optional[Path],
         rules: Optional[Dict[str, dict]] = None) -> dict:
    """Everything a slice needs, checked, before anything is written: the cohorts,
    each kind's source files, the columns, the session window. Raises
    SubsetRefused for anything that would make a wrong or partial slice."""
    rules = flag_rules() if rules is None else rules
    profile = validate(name, profile, rules)
    current = Path(current)
    manifest = _read_manifest(current)
    _require_complete(name, manifest, current)
    cohorts = _cohorts_of(profile, current)
    if not cohorts:
        raise SubsetRefused("limited export %s: none of its cohorts are in the current exports"
                            % name)
    sources: Dict[str, List[Tuple[str, Path]]] = {}
    columns: Dict[str, List[str]] = {}
    filled: Dict[str, Dict[str, List[str]]] = {}
    missing_files: Dict[str, List[str]] = {}
    flag_col = (profile.get("flag") or {}).get("column")
    for kind in profile["kinds"]:
        found = _find_sources(kind, cohorts, current)
        missing_files[kind] = [c for c in cohorts if c not in {x for x, _ in found}]
        sources[kind] = found
        headers = {c: layout._header(p) for c, p in found}
        union: List[str] = []
        for c in headers.values():
            union += [x for x in c if x not in union]
        wanted = profile.get("columns", {}).get(kind) or union
        absent = [c for c in wanted if c not in union]
        if absent:
            raise SubsetRefused(
                "limited export %s: the full export's %s files of %s have no column %s. Nothing "
                "was written. Check the spelling against %s_DATA_DICTIONARY.csv, or remove it "
                "from the definition." % (name, kind, ", ".join(cohorts), ", ".join(absent), kind))
        if flag_col and flag_col in union:
            raise SubsetRefused("limited export %s: its yes/no column '%s' is already a column of "
                                "the %s files; give it another name" % (name, flag_col, kind))
        need = set(k for k in ROW_KEYS[kind].values() if k)
        if kind == "ODC_sessions":
            need = {"Date", "Animal"}
        lacking_keys = [k for k in need if k not in union]
        if found and lacking_keys:
            raise SubsetRefused("limited export %s: the %s files lack %s, which the filter reads"
                                % (name, kind, ", ".join(lacking_keys)))
        columns[kind] = list(wanted)
        for c, h in headers.items():
            lack = [x for x in wanted if x not in h]
            if lack:
                filled.setdefault(kind, {})[c] = lack
    if all(not v for v in sources.values()):
        raise SubsetRefused("limited export %s: the current exports hold no file of the chosen "
                            "kinds for %s" % (name, ", ".join(cohorts)))
    keep, window_info = session_window(profile, cohorts, snapshot_dir,
                                       manifest.get("snapshot_generation"))
    return {"name": name, "profile": profile, "current": current, "manifest": manifest,
            "cohorts": cohorts, "sources": sources, "columns": columns, "filled": filled,
            "missing_files": missing_files, "keep": keep, "window": window_info, "rules": rules}


def _cut_dictionary(current: Path, kind: str, columns: List[str], flag_col: Optional[str],
                    flag_how: str) -> Tuple[pd.DataFrame, List[str]]:
    src = current / ("%s_DATA_DICTIONARY.csv" % kind)
    d = pd.read_csv(src, dtype=str, keep_default_na=False)
    d = d[d["VariableName"].isin(columns)]
    order = {c: i for i, c in enumerate(columns)}
    d = d.assign(_o=d["VariableName"].map(order)).sort_values("_o").drop(columns="_o")
    undocumented = [c for c in columns if c not in set(d["VariableName"]) and c != flag_col]
    if flag_col:
        row = {c: "" for c in d.columns}
        row.update({"VariableName": flag_col, "Title": flag_col.replace("_", " "),
                    "Description": "Per animal, the same on every row of the animal: " + flag_how,
                    "DataType": "text", "PermittedValues": "yes|no",
                    "Comments": "Added by the limited export; not a column of the full export."})
        d = pd.concat([d, pd.DataFrame([row])], ignore_index=True)
    return d, undocumented


def build(p: dict, staging: Path) -> dict:
    """Write every file of the slice into ``staging``. Returns the manifest."""
    name, profile, current = p["name"], p["profile"], p["current"]
    flag_col = (profile.get("flag") or {}).get("column")
    srcs = dict(p["sources"])
    srcs["__root__"] = current
    flag_values, flag_how = _flag_values(profile, p["rules"], srcs, p["cohorts"], p["keep"])
    listed = (flag_values or {}).get("__listed__")
    files, problems = {}, []
    for kind in profile["kinds"]:
        absent = layout.KINDS[kind][1]
        counted = layout.KINDS[kind][2]
        cols = p["columns"][kind] + ([flag_col] if flag_col else [])
        out = staging / layout.file_name(kind, name)
        rows, first = 0, True
        per_cohort = {}
        seen = set()      # (animal, date) pairs that have rows in this file
        with open(out, "w", encoding="utf-8", newline="") as fh:
            for cohort, path in p["sources"][kind]:
                kept = 0
                for chunk in pd.read_csv(path, dtype=str, keep_default_na=False,
                                         chunksize=layout.CHUNK_ROWS):
                    chunk = chunk[_row_mask(chunk, kind, profile, p["keep"])]
                    if p["keep"] is not None:
                        seen.update(zip(_row_animal(chunk, kind),
                                        chunk[ROW_KEYS[kind]["date"]].map(_norm_date)))
                    if flag_col:
                        animal = _row_animal(chunk, kind)
                        if listed is not None:
                            chunk[flag_col] = ["yes" if a in listed else "no" for a in animal]
                        elif flag_values is not None:
                            chunk[flag_col] = [flag_values.get(a, absent) for a in animal]
                        else:
                            chunk[flag_col] = absent
                    chunk = chunk.reindex(columns=cols, fill_value=absent)
                    if counted and layout.ENTRY_INDEX in chunk.columns:
                        chunk[layout.ENTRY_INDEX] = [str(i) for i in
                                                     range(rows + 1, rows + len(chunk) + 1)]
                    if len(chunk) or first:
                        chunk.to_csv(fh, index=False, header=first)
                        first = False
                    rows += len(chunk)
                    kept += len(chunk)
                per_cohort[cohort] = kept
            if first:
                pd.DataFrame(columns=cols).to_csv(fh, index=False)
        d, undocumented = _cut_dictionary(current, kind, p["columns"][kind], flag_col, flag_how)
        d.to_csv(staging / ("%s_DATA_DICTIONARY.csv" % kind), index=False)
        entry = {"rows": rows, "columns": len(cols), "rows_per_cohort": per_cohort,
                 "source_files": [layout.key(current, f) for _, f in p["sources"][kind]],
                 "dictionary": "%s_DATA_DICTIONARY.csv" % kind}
        if p["filled"].get(kind):
            entry["columns_filled_for_cohorts_without_them"] = {
                c: {"columns": v, "filled_with": absent} for c, v in p["filled"][kind].items()}
        if p["missing_files"].get(kind):
            entry["cohorts_with_no_file_of_this_kind"] = p["missing_files"][kind]
        if p["keep"] is not None:
            # A chosen session with no row here: the sheet has it, the full export
            # does not (no recording found, still in review, or no reaches). Said,
            # so a slice short of sessions is not mistaken for a complete one.
            empty = sorted(p["keep"] - seen)
            entry["chosen_sessions_with_no_rows"] = ["%s %s" % x for x in empty]
        if undocumented:
            entry["undocumented_columns"] = undocumented
            problems.append("%s: %d column(s) have no entry in the full export's dictionary: %s"
                            % (out.name, len(undocumented), ", ".join(undocumented[:12])))
        files[out.name] = entry
    ex_src = current / EXCLUSIONS_NAME
    if ex_src.is_file():
        ex = pd.read_csv(ex_src, dtype=str, keep_default_na=False)
        ex = ex[_exclusion_mask(ex, profile, p["cohorts"], p["keep"])]
        ex.to_csv(staging / EXCLUSIONS_NAME, index=False)
        files[EXCLUSIONS_NAME] = {"rows": int(len(ex)), "source_files": [EXCLUSIONS_NAME]}
    else:
        problems.append("the full export has no %s, so none is written here" % EXCLUSIONS_NAME)
    src_m = p["manifest"]
    manifest = {
        "name": name,
        "written_at": datetime.now().isoformat(timespec="seconds"),
        "definition": profile,
        "cohorts": p["cohorts"],
        "source_exports": str(current),
        "source_exports_written_at": src_m.get("generated_at"),
        "source_snapshot_generation": src_m.get("snapshot_generation"),
        "source_exports_complete": src_m.get("complete"),
        "session_window": p["window"],
        "files": files,
        "problems": problems,
        "signature": p.get("signature"),
        # The content of every file but this manifest and the README (which carry
        # the time they were written): a remake whose files come out the same is
        # not put in place (make, if_changed).
        "file_hashes": {f.name: _sha256(f) for f in sorted(staging.iterdir())
                        if f.is_file() and f.name not in (MANIFEST_NAME, "README.txt")},
    }
    (staging / MANIFEST_NAME).write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")
    (staging / "README.txt").write_text(readme(manifest), encoding="utf-8")
    return manifest


def readme(m: dict) -> str:
    d = m["definition"]
    w = m["session_window"]
    if w["type"] == "all":
        wtext = "every session"
    elif w["type"] == "dates":
        wtext = "sessions dated %s to %s" % (w.get("from", "the first"), w.get("to", "the last"))
    else:
        wtext = w["rule"]
    lines = [
        "LIMITED EXPORT %s -- a filtered copy of the current exports, not a new analysis." % m["name"],
        "Written %s from the current exports of %s (snapshot %s)."
        % (m["written_at"], m["source_exports_written_at"], m["source_snapshot_generation"]),
        "Every value here is copied unchanged from the full export; only rows and columns",
        "were left out. Do not edit these files: change the definition and make it again.",
        "",
        "WHAT WAS KEPT",
        "  Cohorts:    %s" % ", ".join(m["cohorts"]),
        "  Tray types: %s" % (", ".join(d.get("tray_types") or []) or "all"),
        "  Sessions:   %s" % wtext,
    ]
    if w.get("cohorts_without_injury_date"):
        lines.append("  No injury date in the sheet, so no sessions: %s"
                     % ", ".join(w["cohorts_without_injury_date"]))
    if d.get("flag"):
        lines.append("  Yes/no column: %s (rule: %s)" % (d["flag"]["column"],
                                                          d["flag"].get("rule") or "none -- left blank"))
    lines += ["", "FILES"]
    for f, e in m["files"].items():
        lines.append("  %-44s %8s rows" % (f, e.get("rows")))
        if e.get("chosen_sessions_with_no_rows"):
            lines.append("      %d chosen animal-day(s) have no rows in this file (listed in "
                         "SUBSET_MANIFEST.json, chosen_sessions_with_no_rows)"
                         % len(e["chosen_sessions_with_no_rows"]))
    lines += ["",
              "Each kind of file has its data dictionary beside it (<kind>_DATA_DICTIONARY.csv),",
              "cut down to the columns kept. analysis_EXCLUSIONS.csv lists the entries the full",
              "export leaves out for a known problem, for these cohorts and sessions only.",
              "SUBSET_MANIFEST.json holds the full definition, which sessions were chosen for",
              "each animal, and the row counts. A previous version of this folder is in the",
              "archive (_archived/exports_subsets/<name>/<time>), never deleted."]
    if m["problems"]:
        lines += ["", "PROBLEMS"] + ["  [!] %s" % x for x in m["problems"]]
    return "\n".join(lines) + "\n"


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def signature(p: dict) -> dict:
    """Everything the slice is cut from, reduced to values that change whenever an
    input changes: the definition, its rule, each source file's size and time,
    the dictionaries and exclusions, the snapshot's sessions and surgeries tables
    by content, and the code. NOT the snapshot generation id, which changes every
    night whether or not anything this slice reads did."""
    from .. import snapshot_generation
    from .current import exporter_code_hash
    current = p["current"]

    def facts(path: Path):
        try:
            st = Path(path).stat()
            return [st.st_size, st.st_mtime_ns]
        except OSError:
            return "absent"

    files = {}
    for kind, found in p["sources"].items():
        for _, f in found:
            files[layout.key(current, f)] = facts(f)
        files["%s_DATA_DICTIONARY.csv" % kind] = facts(current / ("%s_DATA_DICTIONARY.csv" % kind))
    files[EXCLUSIONS_NAME] = facts(current / EXCLUSIONS_NAME)
    sig = {"definition": p["profile"], "files": files, "code": exporter_code_hash()}
    flag = p["profile"].get("flag") or {}
    if flag.get("rule"):
        sig["rule"] = p["rules"].get(flag["rule"])
    if p["profile"]["window"]["type"] == "last_before_injury":
        snap = p.get("snapshot_dir")
        sig["tables"] = {t: snapshot_generation.table_hash(snap, t) if snap else None
                         for t in ("pellet_scores", "surgeries")}
    return json.loads(json.dumps(sig, default=str))


def _previous_signature(folder: Path) -> Optional[dict]:
    try:
        return json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8")).get("signature")
    except (OSError, ValueError):
        return None


# The name a new file is copied under inside the slice's folder before it is
# put in place. Starts with "_" so it is never taken for a file of the
# previous slice (those are what gets archived).
INCOMING_PREFIX = "_incoming_"
# Left in a slice's folder only when a remake failed half way AND the previous
# files could not be put back; the next remake archives it with the rest.
NOT_COMPLETE_NAME = "NOT_COMPLETE.txt"
# The manifest and README go in last: a folder that has them has every file.
_LAST = (MANIFEST_NAME, "README.txt")


def _move(src: Path, dst: Path) -> None:
    """One rename (a function of its own so a test can make it fail)."""
    Path(src).replace(dst)


def _place(staging: Path, folder: Path, out_root: Path, name: str) -> List[str]:
    """Put a new slice in place of the previous one. Returns what was archived.

    WHY THREE STEPS: a remake used to move the previous slice into the archive
    first and then copy the new files in. A copy that failed part way (a full
    share; a file held open by a spreadsheet program) left the folder with
    part of a new set, no manifest and no README, while the window said
    nothing was changed. Now:

    1. every new file is copied into the folder under a temporary name
       (``_incoming_<file>.tmp``). A failure here removes those copies:
       nothing was changed;
    2. the previous files are moved to the archive. A failure here (a file
       held open cannot be moved) moves the ones already moved back:
       nothing was changed;
    3. the new files are renamed into place, the manifest and README last.
       A failure here moves the new files back out and the previous files
       back in: nothing was changed.

    Only if putting things back ALSO fails is the folder left part-way; then a
    NOT_COMPLETE.txt there says so and where the previous files are, and the
    error says the same. Either way SubsetNotPlaced is raised, saying which."""
    out_root = Path(out_root)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    made_folder = not folder.is_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for left in folder.glob(INCOMING_PREFIX + "*.tmp"):
        # A copy an earlier, interrupted attempt left behind: the files it
        # copied are still in that attempt's staging or the archive.
        try:
            left.unlink()
        except OSError:
            pass
    new = sorted((f for f in staging.iterdir() if f.is_file()),
                 key=lambda f: (f.name in _LAST, _LAST.index(f.name) if f.name in _LAST else 0,
                                f.name))
    incoming: List[Tuple[Path, Path]] = []      # (temporary copy, final name)

    def drop_incoming() -> None:
        for tmp, _ in incoming:
            try:
                tmp.unlink()
            except OSError:
                pass
        if made_folder:
            try:
                folder.rmdir()
            except OSError:
                pass

    # 1. copy in under temporary names
    try:
        for f in new:
            tmp = folder / (INCOMING_PREFIX + f.name + ".tmp")
            incoming.append((tmp, folder / f.name))
            shutil.copyfile(f, tmp)
    except OSError as e:
        drop_incoming()
        raise SubsetNotPlaced(
            "limited export %s: the new files could not be copied into %s (%s: %s). Nothing "
            "was changed: the previous files are still there." % (name, folder,
                                                                 type(e).__name__, e)) from None
    # 2. move the previous files to the archive
    old = [f for f in folder.iterdir() if not f.name.startswith("_") and f.is_file()]
    dest = archive_root(out_root) / name / stamp
    moved: List[Tuple[Path, Path]] = []         # (where it was, where it is now)
    which = "folder"
    try:
        if old:
            dest.mkdir(parents=True, exist_ok=True)
        for f in old:
            which = "file " + f.name
            _move(f, dest / f.name)
            moved.append((f, dest / f.name))
    except OSError as e:
        restored = _undo(moved)
        drop_incoming()
        _drop_empty(dest)
        if restored:
            raise SubsetNotPlaced(
                "limited export %s: the previous %s could not be moved to the archive "
                "(%s: %s; is it open in another program?). Nothing was changed: the previous "
                "files are still there. Close it and make the limited export again."
                % (name, which, type(e).__name__, e)) from None
        _not_complete(folder, name, dest, e)
    # 3. rename the new files into place
    placed: List[Tuple[Path, Path]] = []        # (temporary copy, final name)
    try:
        for tmp, final in incoming:
            _move(tmp, final)
            placed.append((tmp, final))
    except OSError as e:
        back = _undo([(tmp, final) for tmp, final in placed])
        restored = back and _undo(moved)
        if restored:
            drop_incoming()
            _drop_empty(dest)
            raise SubsetNotPlaced(
                "limited export %s: a new file could not be put in place in %s (%s: %s). "
                "Nothing was changed: the previous files are back where they were."
                % (name, folder, type(e).__name__, e)) from None
        _not_complete(folder, name, dest, e)
    return [str(now) for _, now in moved]


def _undo(moves: List[Tuple[Path, Path]]) -> bool:
    """Move each (was, now) pair back, last first. True when all went back."""
    ok = True
    for was, now in reversed(moves):
        try:
            _move(now, was)
        except OSError:
            ok = False
    return ok


def _drop_empty(folder: Path) -> None:
    """Remove the archive folder made for this attempt if nothing is in it."""
    try:
        folder.rmdir()
        folder.parent.rmdir()       # the name's folder, if this was its first
    except OSError:
        pass


def _not_complete(folder: Path, name: str, archive: Path, error: Exception) -> None:
    """The last resort: the folder holds part of a set and the previous files
    could not all be put back. Say so in the folder and in the error."""
    text = ("THIS FOLDER IS NOT COMPLETE. Making limited export %s again failed part way "
            "(%s: %s) and the previous files could not all be put back. Do not use these "
            "files. The previous ones are in %s. Make the limited export again; that "
            "archives this folder's files, this note included, and writes a complete set.\n"
            % (name, type(error).__name__, error, archive))
    try:
        (folder / NOT_COMPLETE_NAME).write_text(text, encoding="utf-8")
        said = "NOT_COMPLETE.txt in it says so"
    except OSError:
        said = "and a note saying so could not be written there either"
    raise SubsetNotPlaced(
        "limited export %s: the new files could not be put in place in %s (%s: %s), and the "
        "previous files could not all be put back: the folder is NOT complete (%s). The "
        "previous files are in %s. Make it again." % (name, folder, type(error).__name__,
                                                     error, said, archive))


def _record_status(out_root: Path, name: str, entry: dict) -> None:
    """One line per slice in SUBSETS_STATUS.json: the last attempt and how it went,
    so a failure at night is seen on the Where Is My Data tab."""
    path = Path(out_root) / STATUS_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[name] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


def read_status(out_root: Optional[Path] = None) -> dict:
    try:
        root = Path(out_root) if out_root else subsets_root()
        data = json.loads((root / STATUS_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def make(name: str, profile: Optional[dict] = None, *, current: Optional[Path] = None,
         out_root: Optional[Path] = None, snapshot_dir: Optional[Path] = None,
         dry_run: bool = False, if_changed: bool = False,
         lock_wait_seconds: Optional[float] = None, lock_dir: Optional[Path] = None,
         rules: Optional[Dict[str, dict]] = None) -> dict:
    """Make (or remake) one limited export. Returns {"status": "written" |
    "unchanged" | "dry run", "manifest": ..., "archived": [...], "folder": ...}.

    ``profile`` None: the saved definition of that name. ``dry_run``: check and
    count, and write nothing into the exports or the lock folder -- not even a
    lock file -- then read the full export's manifest again afterwards to be
    sure it did not change underneath. A dry run given ``out_root`` leaves the
    files it built in ``out_root/<name>`` for inspection (nothing archived, no
    status recorded); without one they are built in a temporary folder and
    removed.
    ``if_changed``: keep the slice as it is when nothing it is cut from changed
    since it was made (the nightly run). Otherwise the exports lock is held while
    the slice is read, so the nightly export cannot rewrite the files halfway
    through (``lock_wait_seconds`` 0: do not wait, raise current.ExportsBusy).
    """
    from ..config import snapshot_dir as _snap
    if profile is None:
        saved = profiles()
        if name not in saved:
            raise SubsetRefused("there is no saved limited export named '%s' (saved: %s)"
                                % (name, ", ".join(sorted(saved)) or "none"))
        profile = saved[name]
    current = Path(current) if current else layout.current_root()
    snapshot_dir = Path(snapshot_dir) if snapshot_dir else _snap()
    if dry_run:
        before = _read_manifest(current).get("generated_at")
        p = plan(name, profile, current, snapshot_dir, rules)
        if out_root:
            keep_in = Path(out_root) / name
            if keep_in.exists() and any(keep_in.iterdir()):
                raise SubsetRefused("a dry run leaves its files only in an empty folder, and "
                                    "%s is not empty" % keep_in)
            keep_in.mkdir(parents=True, exist_ok=True)
            m = build(p, keep_in)
        else:
            tmp = Path(tempfile.mkdtemp(prefix="mousedb_subset_dry_"))
            try:
                m = build(p, tmp)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        if _read_manifest(current).get("generated_at") != before:
            raise SubsetRefused("the full export was rewritten while the dry run read it; "
                                "run it again")
        return {"status": "dry run", "manifest": m, "archived": [],
                "folder": str(Path(out_root) / name) if out_root else None}
    from .current import hold_exports_lock
    out_root = Path(out_root) if out_root else subsets_root()
    folder = out_root / name
    mutex = hold_exports_lock(lock_wait_seconds, lock_dir)
    try:
        try:
            p = plan(name, profile, current, snapshot_dir, rules)
            p["snapshot_dir"] = snapshot_dir
            p["signature"] = signature(p)
            previous = _read_manifest_safe(folder)
            # Two ways to know nothing changed. Fast: every input is as it was
            # (the signature). Certain: the files built now are byte for byte
            # those already there -- needed because the full export rewrites
            # many of its files every night with the same content, which
            # changes their times but not what the slice holds.
            last_sig = ((read_status(out_root).get(name) or {}).get("signature")
                        or previous.get("signature"))
            if if_changed and previous and last_sig == p["signature"]:
                return _unchanged(out_root, name, folder, previous, p["signature"],
                                  "nothing it is cut from changed since it was made")
            staging_parent = Path(snapshot_dir) if snapshot_dir and Path(snapshot_dir).is_dir() else None
            staging = Path(tempfile.mkdtemp(prefix="_subset_staging_", dir=staging_parent))
            try:
                m = build(p, staging)
                if (if_changed and previous.get("file_hashes") == m["file_hashes"]
                        and all((folder / f).is_file() for f in m["file_hashes"])):
                    return _unchanged(out_root, name, folder, previous, p["signature"],
                                      "made again, and every file came out the same as before")
                archived = _place(staging, folder, out_root, name)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        except Exception as e:
            _record_status(out_root, name, {
                "last_attempt": datetime.now().isoformat(timespec="seconds"), "ok": False,
                "result": "%s: %s" % (type(e).__name__, e)})
            raise
        _record_status(out_root, name, {
            "last_attempt": m["written_at"], "ok": True, "result": "written",
            "written_at": m["written_at"],
            "rows": {f: e.get("rows") for f, e in m["files"].items()},
            "problems": m["problems"], "signature": p["signature"]})
        return {"status": "written", "manifest": m, "archived": archived, "folder": str(folder)}
    finally:
        mutex.release()


def _unchanged(out_root: Path, name: str, folder: Path, previous: dict, sig: dict,
               why: str) -> dict:
    """Keep the slice as it is (nothing archived, nothing rewritten); remember the
    inputs it was checked against, so the next check can take the fast path."""
    _record_status(out_root, name, {
        "last_attempt": datetime.now().isoformat(timespec="seconds"), "ok": True,
        "result": "unchanged: " + why, "written_at": previous.get("written_at"),
        "signature": sig})
    return {"status": "unchanged", "manifest": previous, "archived": [], "folder": str(folder)}


def _read_manifest_safe(folder: Path) -> dict:
    try:
        return json.loads((Path(folder) / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def make_saved(nightly_only: bool = True, if_changed: bool = True, log=print, **kw) -> int:
    """Remake every saved limited export (those with "nightly" true, when
    ``nightly_only``). Returns the number that FAILED; each failure is printed
    with [FAIL] and recorded in SUBSETS_STATUS.json."""
    failed = 0
    try:
        saved = profiles()
    except SubsetRefused as e:
        log("  [FAIL] limited exports: %s" % e)
        return 1
    for name in sorted(saved):
        if nightly_only and not saved[name].get("nightly", True):
            continue
        try:
            r = make(name, saved[name], if_changed=if_changed, **kw)
            log("  limited export %s: %s" % (name, r["status"]))
        except Exception as e:
            failed += 1
            log("  [FAIL] limited export %s not made: %s: %s" % (name, type(e).__name__, e))
    return failed


# ----------------------------------------------------------------- the program

def _parse_columns(items: Iterable[str]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for item in items or []:
        if "=" not in item:
            raise SubsetRefused("--columns takes KIND=column,column,... (got '%s')" % item)
        kind, cols = item.split("=", 1)
        out.setdefault(kind.strip(), []).extend(c.strip() for c in cols.split(",") if c.strip())
    return out


def profile_from_args(a) -> dict:
    p: dict = {}
    if a.cohorts:
        p["cohorts"] = [c for x in a.cohorts for c in x.split(",") if c]
    if a.project:
        p["project"] = a.project
    if a.tray_types:
        p["tray_types"] = [t for x in a.tray_types for t in x.split(",") if t]
    if a.last_sessions:
        p["window"] = {"type": "last_before_injury", "n": a.last_sessions,
                       "tray_type": a.session_tray or (p.get("tray_types") or [""])[0]}
    elif a.date_from or a.date_to:
        p["window"] = {"type": "dates", "from": a.date_from, "to": a.date_to}
    if a.kinds:
        p["kinds"] = [k for x in a.kinds for k in x.split(",") if k]
    if a.columns:
        p["columns"] = _parse_columns(a.columns)
    if a.flag_column:
        p["flag"] = {"column": a.flag_column, "rule": a.flag_rule}
    if a.not_nightly:
        p["nightly"] = False
    return p


def build_parser(ap=None):
    import argparse
    ap = ap or argparse.ArgumentParser(
        prog="mousedb export-subset", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", nargs="?", help="the limited export's name (its folder name)")
    ap.add_argument("--list", action="store_true", help="list the saved limited exports")
    ap.add_argument("--save", action="store_true",
                    help="save the definition given by the options below under NAME; "
                         "with --run, save then make it. A different definition already "
                         "saved under NAME is refused unless --replace is given")
    ap.add_argument("--replace", action="store_true",
                    help="with --save: replace a definition already saved under NAME (the "
                         "replaced one is kept in the lab settings file, under %s)"
                         % REPLACED_KEY)
    ap.add_argument("--run", action="store_true", help="make NAME now from the current exports")
    ap.add_argument("--remove", action="store_true",
                    help="forget the saved definition NAME (slices already made stay)")
    ap.add_argument("--all-saved", action="store_true",
                    help="make every saved limited export (as the nightly job does)")
    ap.add_argument("--if-changed", action="store_true",
                    help="keep a slice as it is when nothing it is cut from has changed")
    ap.add_argument("--dry-run", action="store_true",
                    help="check the definition and count the rows; write nothing")
    ap.add_argument("--no-wait", action="store_true",
                    help="if the exports are being rewritten right now, stop at once "
                         "(exit code %d) instead of waiting" % EXIT_BUSY)
    ap.add_argument("--current-dir", type=Path, help="the full export to cut from "
                    "(default: <mousedb_root>/exports/current)")
    ap.add_argument("--out-dir", type=Path, help="where slices go "
                    "(default: <mousedb_root>/exports/subsets)")
    ap.add_argument("--snapshot-dir", type=Path, help="the analysis snapshot (default: configured)")
    g = ap.add_argument_group("the definition (with --save, or with --run/--dry-run for a one-off)")
    g.add_argument("--project", help="every cohort of this project (e.g. PROJA)")
    g.add_argument("--cohorts", nargs="+", help="cohort ids, e.g. PROJA_01 PROJA_02")
    g.add_argument("--tray-types", nargs="+", help="Pillar/P, Easy/E, Flat/F (default: all)")
    g.add_argument("--last-sessions", type=int,
                   help="keep each animal's last N sessions before its cohort's first injury")
    g.add_argument("--session-tray", help="which tray type's sessions count for --last-sessions "
                   "(default: the first --tray-types)")
    g.add_argument("--from", dest="date_from", help="keep sessions on or after this date")
    g.add_argument("--to", dest="date_to", help="keep sessions on or before this date")
    g.add_argument("--kinds", nargs="+", help="kinds of file (default all): %s" % ", ".join(ALL_KINDS))
    g.add_argument("--columns", nargs="+", metavar="KIND=COL,COL",
                   help="keep only these columns of that kind of file")
    g.add_argument("--flag-column", help="add a per-animal yes/no column of this name")
    g.add_argument("--flag-rule", help="the named rule that fills it (default: left blank)")
    g.add_argument("--definition-file", type=Path,
                   help="read the definition from this JSON file instead of the options "
                        "(how the window checks a definition without saving it)")
    g.add_argument("--not-nightly", action="store_true",
                   help="do not remake this one after the nightly export")
    return ap


def main(argv=None) -> int:
    return run_args(build_parser().parse_args(argv))


def run_args(a) -> int:
    """The program's work, from parsed arguments (``mousedb export-subset`` parses
    them with the same build_parser)."""
    from .current import ExportsBusy
    from ..task_mutex import LockFolderNotWritable, LockWaitTimeout
    try:
        if a.list:
            saved = profiles()
            status = read_status(a.out_dir)
            if not saved:
                print("No limited exports are saved.")
            for n in sorted(saved):
                s = status.get(n) or {}
                print("%s: %s" % (n, json.dumps(saved[n])))
                if s:
                    print("    last attempt %s: %s" % (s.get("last_attempt"),
                                                      s.get("result") if s.get("ok") else
                                                      "[FAIL] " + str(s.get("result"))))
            return EXIT_OK
        if a.all_saved:
            failed = make_saved(nightly_only=False, if_changed=a.if_changed,
                                current=a.current_dir, out_root=a.out_dir,
                                snapshot_dir=a.snapshot_dir,
                                lock_wait_seconds=0 if a.no_wait else None)
            return EXIT_FAILED if failed else EXIT_OK
        if not a.name:
            print("[!] give the limited export's name (or --list / --all-saved)")
            return EXIT_REFUSED
        if a.remove:
            print("Forgot %s (in %s; the definition is kept there under %s); its files stay "
                  "where they are." % (a.name, remove_profile(a.name), REPLACED_KEY))
            return EXIT_OK
        given = profile_from_args(a)
        if a.definition_file:
            try:
                given = json.loads(Path(a.definition_file).read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise SubsetRefused("the definition file %s cannot be read (%s)"
                                    % (a.definition_file, e)) from None
        if a.save:
            path = save_profile(a.name, given, replace=a.replace)
            print("Saved limited export %s in %s" % (a.name, path))
            if not (a.run or a.dry_run):
                return EXIT_OK
            given = None
        elif not given:
            given = None
        if not (a.run or a.dry_run):
            print("[!] say what to do: --save, --run, --dry-run, --remove or --list")
            return EXIT_REFUSED
        r = make(a.name, given, current=a.current_dir, out_root=a.out_dir,
                 snapshot_dir=a.snapshot_dir, dry_run=a.dry_run, if_changed=a.if_changed,
                 lock_wait_seconds=0 if a.no_wait else None)
    except SubsetRefused as e:
        print("[!] %s" % e, flush=True)
        return EXIT_REFUSED
    except ExportsBusy as e:
        print("[!] %s" % e, flush=True)
        return EXIT_BUSY
    except (LockWaitTimeout, LockFolderNotWritable) as e:
        print("[!] the exports lock could not be taken: %s. Nothing was written." % e, flush=True)
        return EXIT_FAILED
    except SubsetNotPlaced as e:
        print("[FAIL] %s" % e, flush=True)
        return EXIT_FAILED
    m = r["manifest"]
    print("Limited export %s: %s%s" % (a.name, r["status"],
                                       (" -> %s" % r["folder"]) if r["folder"] else ""))
    if m:
        print("  from the full export of %s (snapshot %s)"
              % (m.get("source_exports_written_at"), m.get("source_snapshot_generation")))
        for f, e in (m.get("files") or {}).items():
            print("  %-44s %8s rows" % (f, e.get("rows")))
        for x in m.get("problems") or []:
            print("  [!] %s" % x)
    if r["archived"]:
        print("  the previous version was moved to %s" % Path(r["archived"][0]).parent)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
