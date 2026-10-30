"""Tracking-sheet status and import, with a memory of what was imported when.

WHY THIS EXISTS
---------------
The lab fills the tracking spreadsheets "eventually" and the database is
only as current as the last import. Until 2026-08-28 nothing recorded when a
sheet was last imported, nothing compared that to when the sheet was last
edited, and an import that failed rolled back in silence (one cohort's, on
every scheduled run, for weeks). This module is the one place that knows,
per cohort:

  * which file IS the sheet (all candidates listed; a person can pin one),
  * when it was last edited,
  * when it was last imported, by what, with what result,
  * therefore whether it NEEDS importing, and whether the last try failed.

Every import goes through ``import_cohorts`` (or, for one named workbook,
``import_sheet_file``) and is appended to the ledger
(``Databases/logs/sheet_imports.jsonl``) -- success or failure, with the
error text and the warnings -- so the GUI and the hourly job read the same
history. These routes land here: this module's own command, the Tracking
Sheets tab, the hourly snapshot refresh, ``mousedb import --all`` /
``--file`` and the "Update the database" button. Until 2026-10 the last
three went straight to the importer, so their errors and warnings were
thrown away, nothing was recorded, they did not wait for the cross-task
database lock, and the command reported success whatever happened.
One route does NOT come through here: the cohort-setup tab's Import /
Replace button (mousedb.gui.app) calls the importer itself, but it takes the
same lock and writes its own line to this ledger, in this shape, with
triggered_by "gui_cohort_setup". It does not refresh animal_records.

One cohort's import is two steps on one workbook: the importer (the sheet's
tables: animals, weights, scores, surgeries ...), then animal_records (each
animal's details copied column for column, REPLACING the cohort's previous
copy). The second runs only when the first SAVED. The importer saves a
workbook whole or not at all -- an import that records an error undoes the
whole workbook -- and until 2026-10 the animal details were replaced anyway,
so after a failed import they described a workbook the rest of the database
did not hold. Now that cohort's ledger line carries
"animal_records_left_unchanged" (why they were left as they were), and the
console prints it under the cohort's FAIL line. A dry run says what a real
run would do.

A dry run reads everything and writes nothing -- not the database and not
the ledger (the ledger is the history of what reached the database).

CLI (the GUI in MouseReach calls these with --json):
    mousedb-sheets status [--json]
    mousedb-sheets import [--cohort CNT_05 ...] [--dry-run] [--json]
        prints one line per cohort (OK/FAIL, what landed, how many warnings,
        the first few of them) and a closing summary line; exits 1 if any
        cohort failed or nothing could be imported at all
    mousedb-sheets pin PROJA_05 "<that cohort's workbook file name>"
        a file cohort_sheets will not accept as that cohort's sheet (another
        cohort's, a "Copy of ...", a name not in the folder), or another
        project's cohort id, is refused in one line, exit 1, nothing recorded
    mousedb-sheets unpin CNT_05
    mousedb-sheets set-dir "<folder>"      # where the tracking sheets live
        refused (exit 1, setting unchanged) for a folder that does not
        exist, holds no tracking sheet, or is a snapshot/archive folder;
        a folder typed relative to where you stand is recorded in full

A workbook named directly (``mousedb import --file``, import_sheet_file) is
refused, not imported, when the folder route would never read it: a copy
inside a snapshot or archive folder, or a name the naming rule refuses (see
refuse_sheet_file for why).

ASCII-only console output (Windows cp1252 consoles cannot print Unicode).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import DEFAULT_LOG_PATH
from . import cohort_sheets as _cs
from .cohort_sheets import (
    CONFIG_PATH, available_cohorts, cohort_sheet_candidates, cohort_sheets_dir,
    find_cohort_sheet, is_stale_source, pin_cohort_sheet, pinned_sheet,
    set_cohort_sheets_dir,
)

# Override hook (tests point it at a temp file); None = the configured logs folder.
LEDGER = None

# Whether an import that wrote rewrites the hand-scores file afterwards
# (hand_scores_file). The test suite turns it off (tests/conftest.py): the
# file's location comes from this machine's configuration, which a unit test
# must never write into.
WRITE_HAND_SCORES = True

# Whether an import that wrote rewrites each cohort's FIX THESE file beside its
# sheet (attention.write_all_fix_these). Off in the test suite for the same
# reason: the sheets folder is this machine's configuration.
WRITE_FIX_THESE = True

# Which projects' workbooks the tracking-sheet folder holds, and what each is
# called, is the lab's configuration (mousedb.sheet_projects). Every cohort
# found there has the id <PROJECT>_NN, read from its workbook's name. A cohort a
# person ASKS for is matched against those ids and never re-labelled into one:
# until 2026-10 every request was rewritten as one project's cohort from its
# last number, so asking for another project's cohort 01 -- or for an animal id
# such as PROJA_05_01 -- silently imported that project's cohort-01 sheet.

# A requested cohort: letters, an optional underscore, one or two digits
# (CNT_05, cnt05). Anything longer -- an animal id, a typo -- is refused by
# name rather than having its trailing number reinterpreted as a cohort.
_REQUESTED_COHORT_RE = re.compile(r"^([A-Za-z]+)_?(\d{1,2})$")

# Most warnings kept per cohort in the LEDGER (the returned result keeps them
# all). WHY a cap: the ledger gains a line per cohort on every run, forever,
# and one workbook can raise hundreds of warnings. The full count is stored
# beside the list (warning_count) and the cut is announced inside it, so a
# shortened list never reads as "that was all of them".
LEDGER_WARNINGS_KEPT = 50


def _ledger() -> Path:
    """The import ledger lives in the configured logs folder."""
    if LEDGER is not None:
        return Path(LEDGER)
    from .config import require
    return (DEFAULT_LOG_PATH or (require("mousedb_root") / "logs")) / "sheet_imports.jsonl"


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------

def _read_ledger() -> List[dict]:
    if not _ledger().is_file():
        return []
    out = []
    for line in _ledger().read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _append_ledger(entry: dict) -> None:
    _ledger().parent.mkdir(parents=True, exist_ok=True)
    with _ledger().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _for_ledger(entry: dict) -> dict:
    """The copy of a cohort's outcome that goes in the ledger: warnings cut to
    LEDGER_WARNINGS_KEPT, closed by a line saying how many more there were and
    how to see them all (see LEDGER_WARNINGS_KEPT for why there is a cut)."""
    warnings = list(entry.get("warnings") or [])
    if len(warnings) <= LEDGER_WARNINGS_KEPT:
        return entry
    out = dict(entry)
    out["warnings"] = warnings[:LEDGER_WARNINGS_KEPT] + [
        "... and %d more warnings, not kept in this ledger (all of them: "
        "mousedb-sheets import --cohort %s --dry-run --json)"
        % (len(warnings) - LEDGER_WARNINGS_KEPT, entry.get("cohort_id") or "<cohort>")]
    return out


def last_import(cohort_id: str) -> Optional[dict]:
    """The most recent ledger entry for this cohort (success or failure)."""
    entries = [e for e in _read_ledger() if e.get("cohort_id") == cohort_id]
    return entries[-1] if entries else None


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _refused_files(sheets_dir) -> List[dict]:
    """Files in the sheets folder that look like a tracking sheet but are not
    read as one, each with the reason, from cohort_sheets. WHY shown by every
    status and import: a refused file is a decision, and if a cohort's real
    sheet were ever saved under a refused name the cohort would simply stop
    being imported -- this is what lets a person see why. Empty when
    cohort_sheets offers no such list."""
    lister = getattr(_cs, "refused_sheet_files", None)
    if lister is None or sheets_dir is None:
        return []
    try:
        return [{"file": name, "reason": why} for name, why in lister(sheets_dir)]
    except Exception as e:  # a listing problem must never stop an import
        return [{"file": "(could not list the sheets folder)", "reason": str(e)}]


def _broken_pin_notice(cohort_id: str, stored: str) -> str:
    """One sentence for a person about a pin that names none of the cohort's
    files: cohort_sheets.broken_pins' own wording when it offers it (it knows
    why the pin no longer resolves), else a plain fallback. Shown ONCE, under
    the cohort's row in the status listing -- not again in import output."""
    lister = getattr(_cs, "broken_pins", None)
    if lister is not None:
        try:
            for b in lister():
                if b.get("cohort_id") == cohort_id and b.get("why"):
                    return b["why"]
        except Exception:      # a notice must never stop a status listing
            pass
    return ("%s is pinned to %s, which is no longer one of its files, so its newest "
            "file is read instead; choose again with 'mousedb-sheets pin %s <file>' "
            "(or 'mousedb-sheets unpin %s')" % (cohort_id, stored, cohort_id, cohort_id))


def cohort_status(cohort: str) -> dict:
    # A cohort id ('PROJA_05'); a bare number is read the way every sheet lookup
    # reads one (cohort_sheets._cohort_key: only while one project is set).
    cohort_id = _cs._cohort_key(cohort) or str(cohort)
    cands = cohort_sheet_candidates(cohort_id)
    chosen = find_cohort_sheet(cohort_id)
    # A pin is IN FORCE only while it names one of the cohort's files; once
    # that file is renamed, moved or deleted, find_cohort_sheet reads the
    # newest file instead. WHY decided here and not taken from pinned_sheet:
    # this listing used to call such a cohort "pinned" and "not ambiguous"
    # while a file nobody chose was the one imported.
    stored = pinned_sheet(cohort_id)
    pin = stored if stored and any(c.name == stored for c in cands) else None
    broken = _broken_pin_notice(cohort_id, stored) if stored and not pin else None
    last = last_import(cohort_id)
    edited = _iso(chosen.stat().st_mtime) if chosen else None

    if chosen is None:
        state, why = "no_sheet", "no tracking sheet found for this cohort"
    elif last is None:
        state, why = "never_imported", "this sheet has never been imported"
    elif not last.get("success"):
        state, why = "last_import_failed", (last.get("error") or "unknown error")[:300]
    elif last.get("sheet_mtime") and edited and edited > last["sheet_mtime"]:
        state, why = "sheet_newer", "the sheet was edited after the last import"
    elif last.get("sheet_name") and chosen.name != last.get("sheet_name"):
        state, why = "sheet_newer", "a different file is now the sheet (%s -> %s)" % (
            last.get("sheet_name"), chosen.name)
    else:
        state, why = "up_to_date", "database matches the sheet's last edit"

    return {
        "cohort_id": cohort_id,
        "sheet": chosen.name if chosen else None,
        "sheet_path": str(chosen) if chosen else None,
        "sheet_edited": edited,
        "candidates": [{"name": c.name, "edited": _iso(c.stat().st_mtime),
                        "size": c.stat().st_size} for c in cands],
        "ambiguous": len(cands) > 1 and not pin,
        "pinned": pin,
        "broken_pin": broken,
        "last_import": last,
        "state": state,
        "why": why,
    }


def aspa_cohort_status(letter: str) -> dict:
    """ASPA is its own project with its own (frozen) workbook per cohort
    letter, in its own folder. Same verdicts as CNT; 'sheet_newer' can only
    happen if someone edits a frozen sheet, which is itself worth seeing."""
    from .cohort_sheets import aspa_cohort_number, find_aspa_sheet
    cohort_id = "ASPA_%s" % aspa_cohort_number(letter)
    sheet = find_aspa_sheet(letter)
    last = last_import(cohort_id)
    edited = _iso(sheet.stat().st_mtime) if sheet else None
    if sheet is None:
        state, why = "no_sheet", "no ASPA workbook for cohort %s" % letter
    elif last is None:
        state, why = "never_imported", "manual scores never imported (mousedb-import-aspa-scores)"
    elif not last.get("success"):
        state, why = "last_import_failed", (last.get("error") or "unknown error")[:300]
    elif last.get("sheet_mtime") and edited and edited > last["sheet_mtime"]:
        state, why = "sheet_newer", "the workbook was edited after the last import"
    else:
        state, why = "up_to_date", "database matches the workbook's last edit"
    return {"cohort_id": cohort_id, "project": "ASPA", "letter": letter,
            "sheet": sheet.name if sheet else None, "sheet_path": str(sheet) if sheet else None,
            "sheet_edited": edited, "candidates": [{"name": sheet.name, "edited": edited,
                                                    "size": sheet.stat().st_size}] if sheet else [],
            "ambiguous": False, "pinned": None, "last_import": last, "state": state, "why": why}


def status() -> dict:
    """Everything the Tracking Sheets tab shows, as one JSON-able dict.

    Two folders: the tracking sheets (every project named in the lab
    settings, sheet_projects) and ASPA's frozen workbooks; both are listed."""
    from .cohort_sheets import aspa_data_dir, available_aspa_cohorts
    d = cohort_sheets_dir()
    a = aspa_data_dir()
    out = {
        "config_path": str(CONFIG_PATH),
        "cnt_sheets_dir": str(d) if d else None,
        "aspa_sheets_dir": str(a) if a else None,
        "configured": d is not None,
        "ledger": str(_ledger()),
        "cohorts": [],
        "refused_files": _refused_files(d),
        "problem": None,
    }
    if d is None:
        from . import sheet_projects
        out["problem"] = ("No tracking-sheet folder is configured, or the configured "
                          "folder holds no tracking sheet (sheet names: %s). "
                          "Use 'Set sheets folder' / mousedb-sheets set-dir."
                          % sheet_projects.describe())
    else:
        for cohort_id in available_cohorts(d):
            project = cohort_id.rsplit("_", 1)[0]
            try:
                c = cohort_status(cohort_id)
                c["project"] = project
                out["cohorts"].append(c)
            except Exception as e:
                out["cohorts"].append({"cohort_id": cohort_id, "project": project,
                                       "state": "error", "why": "status failed: %s" % e})
    if a is not None:
        for L in available_aspa_cohorts(a):
            try:
                out["cohorts"].append(aspa_cohort_status(L))
            except Exception as e:
                out["cohorts"].append({"cohort_id": "ASPA_%s" % L, "project": "ASPA",
                                       "state": "error", "why": "status failed: %s" % e})
    return out


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def _animal_records(imp, cohort_id, sheet, kind, dry_run):
    """Copy the sheet's animal-level tabs into animal_records (see mousedb.animal_records).
    None when the importer has no database (test doubles). Called only once
    the importer has saved the same workbook -- see _not_saved_reason."""
    db = getattr(imp, "db", None)
    if db is None:
        return None
    from .animal_records import sync_cohort
    return sync_cohort(db, cohort_id, sheet, kind, dry_run=dry_run)


# The key, in a cohort's outcome and its ledger line, that says this import
# left the cohort's animal details as they were, and why (a sentence for a
# person). Absent when they were refreshed.
LEFT_UNCHANGED = "animal_records_left_unchanged"

_STOPPED_BEFORE_RECORDS = ("animal details left as they were: the import stopped on the "
                           "error before it reached them")


def _not_saved_reason(r: dict, dry_run: bool) -> Optional[str]:
    """Why the cohort's animal details must be left as they are after the
    importer returned ``r``, or None when it saved the workbook (or, in a dry
    run, would).

    WHY: animal_records.sync_cohort REPLACES a cohort's animal details with the
    workbook's. The importer saves a workbook whole or not at all: an import
    that records an error undoes everything it read (its result then holds
    the errors, success False and 'not_saved'). The animal details used to be
    replaced even then, so for that run they described the new workbook while
    every other table still held the old one -- two parts of the database
    disagreeing about the same animals, with nothing saying which was right.
    Now both land, or neither. A dry run applies the same test to the
    importer's dry run, so its preview shows what a real run would do."""
    if r.get("success") and not r.get("errors") and "not_saved" not in r:
        return None
    if dry_run:
        return ("animal details would be left as they are: a real import of this workbook "
                "would save nothing (see the error), and replacing the animal details "
                "alone would make them disagree with the rest of the database")
    return ("animal details left as they were: nothing from this workbook was saved (see "
            "the error), and replacing the animal details alone would make them disagree "
            "with the rest of the database; the next import of this workbook that saves "
            "refreshes them")


def _new_entry(cohort_id, sheet, triggered_by: str, dry_run: bool) -> dict:
    """The start of one cohort's outcome record (the ledger's line shape)."""
    return {"cohort_id": cohort_id, "sheet_name": sheet.name if sheet else None,
            "sheet_path": str(sheet) if sheet else None,
            "sheet_mtime": _iso(sheet.stat().st_mtime) if sheet else None,
            "started": datetime.now().isoformat(timespec="seconds"),
            "triggered_by": triggered_by, "dry_run": dry_run}


def _finish(entry: dict, results: dict, dry_run: bool, record: bool = True) -> None:
    """Close one cohort's outcome: stamp and count it, add it to the result,
    and -- unless this is a dry run, or there is no cohort to file it under --
    append it to the ledger."""
    entry["finished"] = datetime.now().isoformat(timespec="seconds")
    # Counted before the ledger copy is cut, so the number is the real one.
    entry["warning_count"] = len(entry.get("warnings") or [])
    results["cohorts"].append(entry)
    if record and not dry_run:
        _append_ledger(_for_ledger(entry))


def _import_one(cohort_id: str, sheet, dry_run: bool, triggered_by: str, imp=None) -> dict:
    """Import ONE workbook as ONE cohort -- the same steps for every route
    (the folder's cohorts, a named file): the sheet's tables, then its
    animal-level tabs, under the cross-task mutex when anything is written.
    The animal-level tabs are copied only when the importer saved the
    workbook (see _not_saved_reason); otherwise the outcome says, under
    LEFT_UNCHANGED, that they were left as they were and why.
    Never raises: a crash becomes this cohort's failure, with the reason."""
    from .importers import ExcelImporter
    entry = _new_entry(cohort_id, sheet, triggered_by, dry_run)
    if sheet is None:
        entry.update(success=False, error="no sheet found")
        return entry
    # kept: why the animal details are left alone (None = copy them).
    # copying: set the moment animal_records is asked to replace anything, so a
    # crash before that moment is recorded as having left them alone too.
    kept, copying = None, False
    try:
        imp = imp or ExcelImporter()
        if dry_run:
            r = imp.import_cohort_file(sheet, dry_run=True)
            kept = _not_saved_reason(r, True)
            copying = not kept
            rec = _animal_records(imp, cohort_id, sheet, "cohort", True) if copying else None
        else:
            # Under the cross-task mutex, one cohort at a time. WHY:
            # this import commits to connectome.db like the reach and
            # brain imports, but did not take the mutex they share, so
            # on 2026-09-15 it overlapped a long import and four of six
            # cohorts failed "database is locked". Per cohort, not
            # around the whole loop: reading the workbooks is slow and
            # must not keep the hourly imports waiting.
            from .task_mutex import hold
            with hold(waiting_for="another database task"):
                r = imp.import_cohort_file(sheet, dry_run=False)
                # The animal details follow the importer's save; they never
                # replace the cohort's copy from a workbook it did not save.
                kept = _not_saved_reason(r, False)
                copying = not kept
                rec = _animal_records(imp, cohort_id, sheet, "cohort", False) if copying else None
        errors = list(r.get("errors", []))
        warnings = list(r.get("warnings", []))
        imported = dict(r.get("imported") or {})
        if rec is not None:
            imported["animal_records"] = rec["records"]
            warnings += rec["warnings"]
            if rec["error"]:
                errors.append("animal records not imported: %s" % rec["error"])
        # Every warning is kept in the RESULT (the console count and --json
        # show them all); only the ledger copy is cut -- see _for_ledger.
        entry.update(success=bool(r.get("success")) and not (rec and rec["error"]),
                     imported=imported,
                     warnings=warnings,
                     error="; ".join(errors) or None)
        if kept:
            entry[LEFT_UNCHANGED] = kept
    except Exception as e:
        entry.update(success=False,
                     error="%s: %s" % (type(e).__name__, e),
                     traceback=traceback.format_exc()[-2000:])
        if not copying:
            entry[LEFT_UNCHANGED] = kept or _STOPPED_BEFORE_RECORDS
    return entry


def requested_cohort_id(text: str) -> Tuple[Optional[str], Optional[str]]:
    """The folder cohort a person typed, or why what they typed is not one --
    before asking whether the folder has a sheet for it.

    Returns ``(cohort_id, None)``, or ``(cohort_id or None, reason)`` with the
    reason worded for a person; the caller adds what was (not) done about it.
    'PROJA_05' and 'proja05' name PROJA's cohort 05. A bare number ('05', '5')
    names a cohort only while ONE project is configured; with more, it could be
    any of them and is refused with the choices. A project with no sheet name
    in the lab settings (sheet_projects) has no sheet here, and its cohorts are
    never given another project's. Anything else -- an animal id, a typo -- is
    not a cohort id. WHY one function for every command that takes a cohort
    (import, pin, unpin, ``mousedb cohort-sheets --cohort``): each used to keep
    only the trailing number, so 'pin PROJAB_01' pinned PROJA_01's sheet and
    'import --cohort PROJA_05_01' imported PROJA_01's."""
    from . import sheet_projects
    t = str(text).strip()
    projects = sorted(sheet_projects.definitions())
    if re.fullmatch(r"\d{1,2}", t):
        if len(projects) == 1:
            return "%s_%02d" % (projects[0], int(t)), None
        return None, ("'%s' names no project, and the sheets folder holds more than one "
                      "project's sheets (%s): give the cohort id, e.g. %s_%02d"
                      % (t, ", ".join(projects) or "none set", (projects or ["PROJ"])[0], int(t)))
    m = _REQUESTED_COHORT_RE.match(t)
    example = (projects or ["PROJ"])[0]
    if not m:
        return None, "'%s' is not a cohort id (expected e.g. %s_05)" % (t, example)
    project = m.group(1).upper()
    cohort_id = "%s_%02d" % (project, int(m.group(2)))
    if project not in projects:
        return cohort_id, (
            "no tracking sheet for %s: no sheet name is set for project %s in the lab "
            "settings (projects with one: %s), and a cohort is never given another "
            "project's sheet. To add it: %s"
            % (cohort_id, project, ", ".join(projects) or "none", sheet_projects.SET_COMMAND))
    return cohort_id, None


def _resolve_requested(text: str, folder_ids) -> Tuple[Optional[str], Optional[str]]:
    """Which of the sheets folder's cohorts a person asked to IMPORT, or why
    none: requested_cohort_id, then whether the folder has a sheet for it.
    Returns ``(cohort_id, None)`` or ``(cohort_id or None, reason)``."""
    cohort_id, why = requested_cohort_id(text)
    if why is None and cohort_id not in folder_ids:
        why = ("no tracking sheet for %s in the sheets folder (cohorts with a sheet: %s)"
               % (cohort_id, ", ".join(sorted(folder_ids)) or "none"))
    if why:
        return cohort_id, why + "; nothing was imported for it"
    return cohort_id, None


def _import_aspa_one(text: str, dry_run: bool, triggered_by: str) -> dict:
    """One requested ASPA cohort, through its own importer (frozen wide-layout
    workbooks), under the same mutex as everything else that writes."""
    from .cohort_sheets import aspa_cohort_number, aspa_letter
    from .cohort_tools.import_aspa_scores import import_cohort as import_aspa

    letter = aspa_letter(text)
    if letter is None:
        # 'ASPA_J': the cohort named by its LETTER, which is how the lab names
        # these cohorts and how status() labels a letter cohort whose status
        # failed. aspa_letter does not read that form, so it is read here --
        # only a single trailing letter, never a digit (see below).
        m = re.fullmatch(r"ASPA_?([A-Z])", text.strip().upper())
        letter = m.group(1) if m else None
    if letter is None:
        # Refused rather than guessed. This used to fall back to the id's last
        # character, which the sheet lookup reads as a cohort NUMBER but the id
        # does not: 'ASPA_99' imported cohort I's workbook (9 -> I) under the
        # cohort id 'ASPA_None'.
        e = _new_entry(text, None, triggered_by, dry_run)
        e.update(success=False, warnings=[], warning_count=0,
                 error="'%s' is not an ASPA cohort id (expected e.g. ASPA_10, or "
                       "ASPA_J by its letter); nothing was imported for it" % text,
                 finished=datetime.now().isoformat(timespec="seconds"))
        return e
    try:
        if dry_run:
            e = import_aspa(letter, apply=False)
        else:
            # Under the cross-task mutex like the other cohorts: it commits
            # pellet scores and animal records to the same database, and
            # until 2026-10 it was the one sheet import that did not wait.
            from .task_mutex import hold
            with hold(waiting_for="another database task"):
                e = import_aspa(letter, apply=True)
    except Exception as ex:
        # The ASPA importer records its own failures in the ledger; this is
        # what it never got to record (the mutex never freeing, or a crash
        # before its own guard) -- recorded here so it is not lost.
        e = _new_entry("ASPA_%s" % aspa_cohort_number(letter), None, triggered_by, dry_run)
        e.update(success=False, warnings=[], error="%s: %s" % (type(ex).__name__, ex),
                 traceback=traceback.format_exc()[-2000:],
                 finished=datetime.now().isoformat(timespec="seconds"))
        if not dry_run:
            _append_ledger(e)
    e["triggered_by"] = triggered_by
    e["warning_count"] = len(e.get("warnings") or [])
    return e


def import_cohorts(cohorts: Optional[List[str]] = None, dry_run: bool = False,
                   triggered_by: str = "manual", sheets_dir=None) -> dict:
    """Import the chosen sheet of each cohort (all cohorts if None).

    One cohort failing never stops the others, and NOTHING is swallowed:
    each cohort's outcome -- counts, warnings, or the full error -- is
    returned AND (unless dry_run) appended to the ledger. A requested
    cohort with no sheet is returned as a failure naming why (it used to be
    skipped without a word); it is not written to the ledger, because no
    sheet was read and the ledger is the history of sheets reaching the
    database.

    ``sheets_dir`` reads a folder other than the configured one
    (``mousedb import --all --directory``); a snapshot or archive folder is
    refused whichever way it was named (see cohort_sheets.is_stale_source)."""
    d = Path(sheets_dir) if sheets_dir else cohort_sheets_dir()
    results = {"triggered_by": triggered_by, "dry_run": dry_run,
               "sheets_dir": str(d) if d else None, "cohorts": []}
    if d is None:
        results["problem"] = ("no tracking-sheet folder configured "
                              "(mousedb-sheets set-dir <folder>)")
        return results
    if not d.is_dir():
        results["problem"] = "the tracking-sheet folder does not exist: %s" % d
        return results
    if snapshot_folder_of(d):
        # WHY here and not only in one command: every route reads through this
        # function, and a snapshot folder's copies are out of date -- an import
        # from one writes stale rows that add-only importing never corrects.
        results["problem"] = ("REFUSED: %s is a snapshot or archive folder, not the live "
                              "sheets; its copies are out of date. Point the sheets folder "
                              "at the working copy (mousedb-sheets set-dir <folder>)" % d)
        return results
    results["refused_files"] = _refused_files(d)

    # The folder's cohorts, each id taken from its workbook's name.
    folder_ids = set(available_cohorts(d))
    if cohorts is None and not folder_ids:
        from . import sheet_projects
        results["problem"] = "no tracking sheet in %s (sheet names: %s)" % (
            d, sheet_projects.describe())
        return results

    # None = every cohort. A SET, possibly empty, = only these. WHY the
    # distinction matters: a request whose every cohort was refused leaves an
    # empty set, and an empty set read as "no filter" would import everything.
    wanted = None
    if cohorts:
        wanted = set()
        for c in cohorts:
            text = str(c).strip()
            if text.upper().startswith("ASPA"):
                results["cohorts"].append(_import_aspa_one(text, dry_run, triggered_by))
                continue
            cohort_id, why = _resolve_requested(text, folder_ids)
            if why:
                e = _new_entry(cohort_id or text, None, triggered_by, dry_run)
                e.update(success=False, error=why, warnings=[])
                _finish(e, results, dry_run, record=False)
            else:
                wanted.add(cohort_id)

    for cohort_id in available_cohorts(d):
        if wanted is not None and cohort_id not in wanted:
            continue
        sheet = find_cohort_sheet(cohort_id, sheets_dir=d)
        _finish(_import_one(cohort_id, sheet, dry_run, triggered_by), results, dry_run)

    # A full import also refreshes each frozen letter cohort's ODC tab into
    # animal_records. WHY: that tab is where those animals' surgery, strain and
    # group are written, and the per-reach ODC export reads them from the
    # database; the scores themselves stay on their own importer. Not written to
    # the ledger (the ledger tracks score imports of those workbooks).
    if cohorts is None:
        results["aspa_animal_records"] = _aspa_animal_records(dry_run)
    if not dry_run and WRITE_HAND_SCORES:
        # The reaching pipeline compares each video with its hand scores before
        # it files it, reading them from this file (hand_scores_file), so the
        # file is rewritten after every import that may have changed them.
        try:
            from .hand_scores_file import write as write_hand_scores
            results["hand_scores"] = write_hand_scores(
                log=lambda *_: None, full_run=cohorts is None, sheets_seen=sorted(folder_ids))
        except Exception as e:
            results["hand_scores_error"] = "%s: %s" % (type(e).__name__, e)
    if not dry_run and WRITE_FIX_THESE and folder_ids:
        # What a person must fix in each cohort's sheet, in a file beside it
        # (attention.py). After the ledger entries above, which it reads.
        try:
            from .attention import write_all_fix_these
            results["fix_these"] = write_all_fix_these(d, sorted(folder_ids))
        except Exception as e:
            results["fix_these"] = {"all cohorts": "FAILED: %s: %s" % (type(e).__name__, e)}
    return results


def snapshot_folder_of(path) -> Optional[str]:
    """The name of the snapshot or archive folder ``path`` sits in (or is),
    else None -- cohort_sheets.is_stale_source, judged on the ABSOLUTE path.

    WHY absolute: a path is judged by the folder names in it, and a bare file
    name typed while standing inside an archive folder carries none, so it
    used to pass. WHY normalised (os.path.abspath, which folds '..'): a path
    that climbs OUT of such a folder ('archive/../live') is not inside it.
    Symlinks and mapped drives are left as typed, so the names judged are the
    ones the person sees."""
    p = Path(os.path.abspath(str(path)))
    if not is_stale_source(p):
        return None
    # The first folder, from the top, that makes the path a snapshot path --
    # named in the refusal so a person knows which folder to move out of.
    for i in range(1, len(p.parts) + 1):
        if is_stale_source(Path(*p.parts[:i])):
            return p.parts[i - 1]
    return p.name


def refuse_sheet_file(path) -> Optional[Tuple[str, str]]:
    """``(why, what to do instead)`` if this workbook must not be imported by
    naming it (``mousedb import --file``), else None. Both worded for a person.

    WHY: importing one named workbook REPLACES that cohort's animal records
    with the workbook's contents, and adds its rows to the database for good
    (the importer only adds). So a file the folder route would never read must
    not get in by being named:
      * a copy inside a snapshot or archive folder -- out of date, refused by
        the folder route for that reason (cohort_sheets.is_stale_source). Until
        2026-10 naming one only printed a warning and imported it anyway, so an
        old copy overwrote good records until the next scheduled import;
      * a file the naming rule refuses -- an Excel lock file, a "Copy of ...",
        an export a mousedb tool wrote FROM the sheet. An export has none of
        the sheet's animal tabs, so importing one would empty that cohort's
        animal records. The NAME is judged by cohort_sheets' own verdict (the
        one rule every sheet lookup uses), not by listing the file's folder:
        the folder could be anywhere, and that listing also carries notices
        about pins, which say nothing about this file.
    A name the rule neither accepts nor refuses (it does not look like a
    tracking sheet at all) is left to the importer, which refuses a name that
    says no cohort -- see import_sheet_file.
    """
    path = Path(path)
    folder = snapshot_folder_of(path)
    if folder:
        why = ("it sits inside a folder named '%s', which is where snapshot and archive "
               "copies are kept, not the live tracking sheets. A copy there is out of "
               "date, and importing it would replace that cohort's animal records with "
               "the old copy's contents" % folder)
    else:
        refused = _cs._sheet_verdict(path.name)[1]
        if not refused:
            return None
        why = "it is not read as a tracking sheet: %s" % refused
    return why, _import_instead(path)


def _import_instead(path: Path) -> str:
    """What to run in place of importing ``path``: the recorded import of that
    cohort's LIVE sheet, named when the sheets folder has one."""
    n = _cs._sheet_cohort(path.name)
    live = find_cohort_sheet(n) if n else None
    if live is not None and not snapshot_folder_of(live):
        return ("import %s's live sheet, %s, from the sheets folder: "
                "mousedb-sheets import --cohort %s" % (n, live.name, n))
    return ("'mousedb-sheets status' lists each cohort's live sheet; import one with "
            "'mousedb-sheets import --cohort <cohort>', or all of them with 'mousedb "
            "import --all'. If this file really is the newest record of its cohort, "
            "move it into the live sheets folder under the sheet's own name first")


def _importer_cohort_id(imp, path) -> Optional[str]:
    """The cohort the importer will file this workbook's rows under, asked of
    the importer itself (it reads it from the file name). WHY not worked out
    here: the ledger must name the cohort the rows actually land in, and a
    second copy of that rule could drift from the one that files them."""
    pick = getattr(imp, "_extract_cohort_from_filename", None)
    return pick(Path(path).name) if callable(pick) else None


def import_sheet_file(path, dry_run: bool = False, triggered_by: str = "manual") -> dict:
    """Import ONE named workbook (``mousedb import --file``) exactly as a cohort
    of the sheets folder is imported: same mutex, same animal records, same
    ledger line, same result shape as import_cohorts. WHY: this route used to
    call the importer directly, so nothing recorded it, it did not wait for
    the database lock, and a failure still exited 0. A file the folder route
    would never read is refused, not imported -- see refuse_sheet_file."""
    from .importers import ExcelImporter
    # Absolute, so the ledger line says where the workbook was: a name typed
    # relative to where the command ran would be recorded as "." otherwise.
    path = Path(os.path.abspath(str(path)))
    results = {"triggered_by": triggered_by, "dry_run": dry_run,
               "sheets_dir": str(path.parent), "cohorts": []}
    if not path.is_file():
        results["problem"] = "file not found: %s" % path
        return results
    refusal = refuse_sheet_file(path)
    if refusal:
        # Refused before the importer is built (it opens the database) and not
        # written to the ledger: no sheet was read, so no cohort's history
        # changed. The command that asked prints the reason.
        results["problem"] = "REFUSED: %s was not imported: %s. Instead, %s" % (
            path.name, refusal[0], refusal[1])
        return results
    try:
        imp = ExcelImporter()
        cohort_id = _importer_cohort_id(imp, path)
    except Exception as e:
        entry = _new_entry(path.name, path, triggered_by, dry_run)
        entry.update(success=False, warnings=[], error="%s: %s" % (type(e).__name__, e),
                     traceback=traceback.format_exc()[-2000:])
        # No cohort is known, so there is no cohort's history to file it under.
        _finish(entry, results, dry_run, record=False)
        return results
    if cohort_id is None:
        entry = _new_entry(path.name, path, triggered_by, dry_run)
        entry.update(success=False, warnings=[],
                     error="the file name %s does not say which cohort it is (expected "
                           "a project's sheet name -- %s); nothing was imported"
                           % (path.name, __import__("mousedb.sheet_projects",
                                                    fromlist=["describe"]).describe()))
        _finish(entry, results, dry_run, record=False)
        return results
    _finish(_import_one(cohort_id, path, dry_run, triggered_by, imp=imp), results, dry_run)
    return results


def _aspa_animal_records(dry_run: bool) -> List[dict]:
    from .cohort_sheets import available_aspa_cohorts, find_aspa_sheet, aspa_cohort_number
    from .animal_records import sync_cohort
    out = []
    letters = available_aspa_cohorts()
    if not letters:
        return out
    from .database import get_db
    from .task_mutex import hold
    try:
        db = get_db()
    except Exception as e:
        # Every letter cohort fails for the same reason; say so for each, so
        # the count of what did not land is right.
        return [{"cohort_id": "ASPA_%s" % aspa_cohort_number(L), "records": 0, "warnings": [],
                 "error": "database not opened: %s: %s" % (type(e).__name__, e)}
                for L in letters]
    for L in letters:
        cohort_id = "ASPA_%s" % aspa_cohort_number(L)
        sheet = find_aspa_sheet(L)
        if sheet is None:
            out.append({"cohort_id": cohort_id, "records": 0, "warnings": [],
                        "error": "no single workbook for this letter"})
            continue
        # One letter cohort failing (or the mutex never freeing) must not
        # stop the others, nor throw away the CNT results already in hand.
        try:
            if dry_run:
                r = sync_cohort(db, cohort_id, sheet, "odc", dry_run=True)
            else:
                with hold(waiting_for="another database task"):
                    r = sync_cohort(db, cohort_id, sheet, "odc", dry_run=False)
        except Exception as e:
            r = {"records": 0, "warnings": [], "error": "%s: %s" % (type(e).__name__, e)}
        out.append(dict(r, cohort_id=cohort_id, sheet_name=sheet.name))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _ascii(text) -> str:
    """Plain-ASCII form of any text for the console. WHY: a sheet's cells and
    an importer's messages can carry characters a Windows console or the
    scheduled jobs' redirected log cannot encode, and printing one would crash
    the command AFTER the import landed, reporting a failure that was not one.
    Non-ASCII becomes a visible escape, never silently dropped."""
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def import_succeeded(r: dict) -> bool:
    """True only when everything landed: no cohort failed, no letter cohort's
    animal records failed, and nothing stopped the import as a whole. WHY the
    last: an unconfigured or refused folder imports nothing, and "nothing
    failed" over zero cohorts used to exit 0 -- the scheduled job and the
    update button reported success while no sheet was read."""
    if r.get("problem"):
        return False
    if not all(c.get("success") for c in r.get("cohorts", [])):
        return False
    if r.get("hand_scores_error"):
        # The reaching pipeline would go on reading old scores: videos whose
        # scores just landed would wait for nothing.
        return False
    return not any(a.get("error") for a in r.get("aspa_animal_records") or [])


def print_import_result(r: dict, show_warnings: int = 3, log=print) -> bool:
    """Print an import result for a person and return import_succeeded(r).

    One line per cohort: OK/FAIL, the cohort, its sheet, what landed (or, on
    FAIL, why not), and how many warnings it raised -- followed by the first
    ``show_warnings`` of them. Then any file refused as a sheet, any problem,
    and ONE closing summary line. WHY the closing line: the "Update the
    database" button shows each step's last line as what that step did, so
    the last line has to be the verdict, not whichever cohort came last."""
    cohorts = r.get("cohorts", [])
    for c in cohorts:
        n_warn = c.get("warning_count", len(c.get("warnings") or []))
        # On FAIL the reason, never the counts: a failed cohort's counts are
        # what it WOULD have written, and printed alone they read as success.
        detail = c.get("imported") if c.get("success") else (c.get("error") or "failed (no reason recorded)")
        log(_ascii("%s %s %s %s  [%d warning%s]" % (
            "OK  " if c.get("success") else "FAIL", c.get("cohort_id"),
            c.get("sheet_name") or "-", detail, n_warn, "" if n_warn == 1 else "s")))
        if c.get(LEFT_UNCHANGED):
            # Right under the FAIL line: what this failure did NOT change.
            log(_ascii("       [!] %s" % c[LEFT_UNCHANGED]))
        shown = list(c.get("warnings") or [])[:max(show_warnings, 0)]
        for w in shown:
            log(_ascii("       - %s" % w))
        if n_warn > len(shown):
            log(_ascii("       ... and %d more (all of them: mousedb-sheets import --cohort %s "
                       "--dry-run --json)" % (n_warn - len(shown), c.get("cohort_id"))))
    records = r.get("aspa_animal_records") or []
    for a in records:
        log(_ascii("%s %s %s animal_records %s" % (
            "FAIL" if a.get("error") else "OK  ", a.get("cohort_id"),
            a.get("sheet_name") or "-", a.get("error") or a.get("records"))))
    for f in r.get("refused_files") or []:
        log(_ascii("[!] not read as a sheet: %s -- %s" % (f.get("file"), f.get("reason"))))
    for cid, what in sorted((r.get("fix_these") or {}).items()):
        if what.startswith("FAILED"):
            log(_ascii("[!] %s's FIX THESE file not updated: %s" % (cid, what)))
        elif what != "none":
            log(_ascii("[!] %s: things to fix in the sheet -- see '!! %s - FIX THESE.xlsx' "
                       "beside it (%s)" % (cid, cid, what)))
    if r.get("hand_scores_error"):
        log(_ascii("FAIL hand scores for the reaching pipeline NOT rewritten: %s"
                   % r["hand_scores_error"]))
    elif r.get("hand_scores"):
        log(_ascii("OK   hand scores for the reaching pipeline: %d pellets of %d sessions"
                   % (r["hand_scores"]["rows"], r["hand_scores"]["videos"])))

    ok = import_succeeded(r)
    if r.get("problem"):
        # The problem is said ONCE, in the closing line. WHY: it used to be
        # printed on a "[!]" line and then again, word for word, in this line,
        # and the closing line is the one that must carry it (it is the verdict
        # the update dialog shows).
        log(_ascii("Sheets: NOTHING IMPORTED -- %s" % r["problem"]))
        return ok
    failed = [str(c.get("cohort_id")) for c in cohorts if not c.get("success")]
    total_warn = sum(c.get("warning_count", len(c.get("warnings") or [])) for c in cohorts)
    line = "Sheets%s: %d of %d cohort%s %s, %s, %d warning%s" % (
        " (dry run, nothing written)" if r.get("dry_run") else "",
        len(cohorts) - len(failed), len(cohorts), "" if len(cohorts) == 1 else "s",
        "checked" if r.get("dry_run") else "imported",
        ("%d FAILED (%s)" % (len(failed), ", ".join(failed))) if failed else "0 failed",
        total_warn, "" if total_warn == 1 else "s")
    if records:
        bad = [str(a.get("cohort_id")) for a in records if a.get("error")]
        line += "; letter-cohort animal records: %d of %d refreshed%s" % (
            len(records) - len(bad), len(records),
            (", %d FAILED (%s)" % (len(bad), ", ".join(bad))) if bad else "")
    log(_ascii(line))
    return ok


def _print_status(st: dict) -> None:
    # Every line through _ascii, the folder line included: the folder's path is
    # whatever the synced drive calls it, and one non-ASCII letter in it would
    # crash the listing on a Windows console before a single row was shown.
    print(_ascii("Tracking sheets folder: %s" % (st["cnt_sheets_dir"] or "(NOT CONFIGURED)")))
    if st.get("problem"):
        print(_ascii("  [!] %s" % st["problem"]))
        return
    print("%-8s %-14s %-19s %-19s %s" % ("cohort", "state", "sheet edited", "last import", "sheet"))
    for c in st["cohorts"]:
        li = c.get("last_import") or {}
        print(_ascii("%-8s %-14s %-19s %-19s %s%s" % (
            c["cohort_id"], c["state"], c.get("sheet_edited") or "-",
            li.get("finished") or "never", c.get("sheet") or "-",
            "   [%d files match -- pin one]" % len(c["candidates"]) if c.get("ambiguous") else "")))
        if c.get("broken_pin"):
            print(_ascii("         [!] %s" % c["broken_pin"]))
        if c["state"] in ("last_import_failed", "error"):
            print(_ascii("         %s" % c["why"]))
    for f in st.get("refused_files") or []:
        print(_ascii("[!] not read as a sheet: %s -- %s" % (f.get("file"), f.get("reason"))))


def _pin_command(cohort: str, filename: Optional[str]) -> int:
    """``mousedb-sheets pin`` (filename given) and ``unpin`` (None).

    Every refusal is ONE plain line and exit 1, never a traceback. WHY: the
    pin guard in cohort_sheets.pin_cohort_sheet raises ValueError for a file
    that cannot be the cohort's sheet (another cohort's, a "Copy of ...", a
    name not in the folder), and that used to reach the person as a Python
    traceback. Which files may be pinned is decided there, once; this only
    reports it. The cohort is read by requested_cohort_id first, because the
    guard keeps only a request's trailing number: 'pin CNTS_01 ...' would
    otherwise pin CNT_01's sheet."""
    cohort_id, why = requested_cohort_id(cohort)
    try:
        if why:
            raise ValueError(why)
        if filename is None:
            # Says what holds now, never "nothing changed": unpinning also
            # clears a pin whose file has gone, which is a change worth making.
            was = pinned_sheet(cohort_id)
            pin_cohort_sheet(cohort_id, None)
            print(_ascii("unpinned %s%s; the newest of its files is read from now on"
                         % (cohort_id, " (was %s)" % was if was else "")))
            return 0
        cfg = pin_cohort_sheet(cohort_id, filename)
    except ValueError as e:
        print(_ascii("[FAIL] %s -- nothing was %s" % (e, "pinned" if filename else "unpinned")))
        return 1
    # The name as recorded (the guard stores a file's real spelling when only
    # the letter case was typed differently), so what is printed is what holds.
    print(_ascii("pinned %s -> %s (in %s)" % (cohort_id, pinned_sheet(cohort_id) or
                                             Path(filename).name, cfg)))
    return 0


def _not_the_sheets_folder(folder) -> Optional[str]:
    """Why ``folder`` cannot be recorded as the sheets folder, or None -- the
    same tests ``mousedb cohort-sheets --set`` applies. WHY: set-dir used to
    record any text at all; a folder that does not exist, holds no tracking
    sheet, or is a snapshot was saved, and every later status and import then
    said "no tracking-sheet folder is configured" with no hint that the
    setting itself was the problem."""
    d = Path(folder)
    if not d.is_dir():
        return "%s is not a folder" % d
    snap = snapshot_folder_of(d)
    if snap:
        return ("%s is inside the snapshot or archive folder '%s', not the live sheets; "
                "its copies are out of date" % (d, snap))
    if not available_cohorts(d):
        from . import sheet_projects
        return ("%s holds no tracking sheet (sheet names: %s)"
                % (d, sheet_projects.describe()))
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mousedb-sheets", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="Per-cohort: which file, edited when, imported when, needs import?")
    s.add_argument("--json", action="store_true")
    i = sub.add_parser("import", help="Import the sheets (all cohorts, or --cohort ...)")
    i.add_argument("--cohort", action="append", help="e.g. CNT_05; repeatable")
    i.add_argument("--dry-run", action="store_true")
    i.add_argument("--json", action="store_true")
    i.add_argument("--triggered-by", default="manual")
    p = sub.add_parser("pin", help="Say which file IS a cohort's sheet")
    p.add_argument("cohort")
    p.add_argument("filename")
    u = sub.add_parser("unpin", help="Forget a pin (newest file wins again)")
    u.add_argument("cohort")
    sd = sub.add_parser("set-dir", help="Where the CNT tracking sheets live")
    sd.add_argument("folder")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        st = status()
        print(json.dumps(st, indent=1, default=str)) if args.json else _print_status(st)
        return 0
    if args.cmd == "import":
        r = import_cohorts(args.cohort, dry_run=args.dry_run, triggered_by=args.triggered_by)
        if args.json:
            print(json.dumps(r, indent=1, default=str))
        else:
            print_import_result(r)
        return 0 if import_succeeded(r) else 1
    if args.cmd in ("pin", "unpin"):
        return _pin_command(args.cohort, args.filename if args.cmd == "pin" else None)
    if args.cmd == "set-dir":
        # Judged and recorded as an ABSOLUTE path. WHY: a folder typed relative
        # to where the command was run passed the tests below and was saved as
        # typed, and the scheduled job and the GUI -- run from elsewhere -- then
        # found no such folder and said "not configured", the very failure these
        # tests exist to stop. os.path.abspath, not resolve(): a mapped drive
        # letter is kept as the person sees it.
        folder = Path(os.path.abspath(args.folder))
        why = _not_the_sheets_folder(folder)
        if why:
            print(_ascii("[FAIL] %s; the sheets folder setting was not changed" % why))
            for f in _refused_files(folder) if folder.is_dir() else []:
                print(_ascii("[!] not read as a sheet: %s -- %s" % (f.get("file"), f.get("reason"))))
            return 1
        print(_ascii("sheets folder %s recorded in %s" % (folder, set_cohort_sheets_dir(folder))))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
