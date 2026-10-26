"""Where the lab's source-of-record spreadsheets live, and how to read them.

Two folders, both on a synced share, both READ-ONLY, and neither path in source:

  CNT cohort tracking sheets -- Connectome_NN_Animal_Tracking.xlsx, one per
      cohort, kept current.
  ASPA animal sheets -- one workbook per cohort letter ("J.xlsx", or the
      letter followed by a description, "K - <description>.xlsx").

Reading either to keep the database current is expected. The rules are that it is
read-only, and that the PATH is configuration rather than code.


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
none to import, and anyone going looking found old snapshot copies of the sheets
in an archive folder instead -- which are stale. A snapshot copy can be a
fraction of the live sheet's size, with an empty subject table and none of the
scoring, weights or surgery details recorded since it was taken; read as the
source, it says a cohort has no data when it has plenty. A wrong default that
silently resolves is worse than no default, because it produces a confident
wrong answer.

WHICH FILES COUNT AS A SHEET. A file is a cohort's sheet only if its name
STARTS with ``Connectome_NN_Animal_Tracking`` and ends ``.xlsx``; anything may
sit between ("...Tracking1.xlsx", "...Tracking (2).xlsx"), and when a cohort has
more than one such file a person pins the right one (pin_cohort_sheet). Not
sheets, however named: Excel lock files (``~$...``), anything that merely
CONTAINS the name ("Copy of ...", a note or another program's file beside the
sheets), and the derived files mousedb's own tools write named after a sheet
(``_ODC``, ``_summary_matrix``, ``_updated``, ``_fixed`` anywhere after the
sheet's name). Refused files are not hidden: refused_sheet_files lists them with
the reason, ``mousedb cohort-sheets`` prints that list, and a refused name cannot
be pinned.

PINS. When a cohort has more than one candidate file, a person says which one
IS the sheet (pin_cohort_sheet; the Tracking Sheets tab's "This is the sheet"
button). A pin must name one of that cohort's files when it is made. If the
pinned file later goes (renamed, moved, deleted), the pin is BROKEN: the cohort
falls back to its newest file exactly as if it were unpinned. That fallback
must never be silent -- it used to be, and the status said "pinned" while a
file nobody had chosen was imported. broken_pins names each broken pin and the
file being read instead; ``mousedb cohort-sheets`` prints it, and any status
display must neither call a broken pin "pinned" nor leave it unsaid.
pinned_sheet returns the RECORD as stored, broken or not, so a display can
tell the two apart.

READ-ONLY. The configured folder is a synced share holding the records people
edit. Nothing in this module writes into it -- not the sheets, not a sibling
file, not a lock file. Everything here reads; fetch_cohort_sheet exists
precisely so that a tool which might open a workbook for writing gets a copy in
its own working directory instead.

Two things in mousedb are BUILT to write into the folder. The first is the
FIX THESE file: after every import that writes, each cohort with something a
person must fix in its sheet (attention.py: a date read as a typo, a failed
import, a video the reaching pipeline cannot check against the hand scores) gets
"!! <cohort> - FIX THESE.xlsx" beside its workbook, and a cohort with nothing
left has it removed. WHY there: that folder is where the sheet is opened and
fixed. It never touches the sheet, and its name can never be read as one
(_SHEET_RE is anchored at "Connectome_"). The second is the Tracking
Sheets tab's "New cohort sheet..." button (gui.sheets_tab.new_sheet). When a
person clicks it and types a new cohort's name and start date, it runs
``cohort_tools.make_sheets --new --output-dir <the configured folder>``, which
creates that cohort's empty workbook there -- the folder is where the new sheet
will be kept, so that is where it is made. Nothing else may write there. Know
what that one write does not yet guard against: make_sheets does not check
whether a workbook of that name already exists (a cohort that already has a
sheet would get an empty one in its place); it takes the cohort number from
whatever follows the LAST underscore of the typed name, so another project's
id or a typo can name an existing cohort's workbook; and if the save is
blocked by an open workbook it force-closes every Excel window on that
machine, losing any unsaved work in them, and retries.

Other tools write wherever they are pointed or run from, so pointed at the
live folder, or run from inside it, they would write there too -- which is
why they must never be. odc_builder and summary_matrix write NEXT TO their
``--source``; ``make_sheets --fix --output-dir`` writes under the input's own
name (into the live folder, that replaces the sheet), and without
``--output-dir`` make_sheets writes into a "generated" folder under the
current directory; update_sheets writes into a "processed" folder under the
current directory (or ``--output-dir``); ``mousedb-reorganize --dir`` writes its CSV
next to the workbooks it read (default: the current directory). Run them on a
fetched copy, from a working directory of their own, never in the sheets
folder. (A derived workbook that lands in the folder anyway is refused as a
sheet -- see above.)

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
from typing import List, Optional, Tuple

CONFIG_PATH = Path.home() / ".mousedb" / "config.json"
ENV_VAR = "MOUSEDB_COHORT_SHEETS"

# A property of the project's data, not of any lab's filesystem.
#
# Anchored at the START of the name (the ``^`` here, and ``.match`` wherever it
# is used). WHY: this used to be an unanchored search, so ANY file whose name
# merely CONTAINED the pattern became a candidate -- "Copy of
# Connectome_05_Animal_Tracking.xlsx", an Excel lock file "~$Connectome_05...",
# the "._Connectome_05..." shadow file a Mac leaves on a network share, a note
# another tool drops beside the sheets -- and with no pin the NEWEST candidate is
# the one imported, so the newest stray file silently became the cohort's sheet.
#
# The tail after "Animal_Tracking" stays open on purpose: real, in-use sheets
# carry suffixes ("..._Animal_Tracking1.xlsx", "..._Animal_Tracking(<a note of
# where or when it was edited>).xlsx", a "(2)" copy), and those must stay visible
# as candidates so a person can pin the right one rather than lose one unseen.
_SHEET_RE = re.compile(r"^Connectome_(\d{1,2})_Animal_Tracking(.*)\.xlsx$", re.I)

# The same words ANYWHERE in a name. Used only to find files that LOOK like a
# sheet but are refused, so a refusal can be shown to a person (see
# refused_sheet_files) instead of the file just vanishing from every listing.
_LOOKS_LIKE_SHEET_RE = re.compile(r"Connectome_\d{1,2}_Animal_Tracking.*\.xlsx$", re.I)

# Files this package's OWN tools write next to a sheet, named after it:
# cohort_tools.odc_builder "<sheet>_ODC.xlsx", cohort_tools.summary_matrix
# "<sheet>_summary_matrix.xlsx", cohort_tools.update_sheets "<sheet>_updated.xlsx",
# and older cohort_tools.make_sheets "<sheet>_fixed.xlsx". WHY refuse them: they
# start with the sheet's own name, so anchoring alone does not exclude them, and
# being written AFTER the sheet they are the newest candidate -- exactly the one
# an unpinned import would read. They are derived outputs, never the record.
#
# Looked for ANYWHERE in the tail, not only at its start. WHY: each tool appends
# its marker to the source's full stem, so for a sheet that already carries a
# suffix ("..._Animal_Tracking1.xlsx") the output is "..._Animal_Tracking1_ODC.xlsx"
# -- the marker sits after the "1" -- and a download or sync copy can add more
# after the marker ("_ODC(1)"). Checking only the start let those through.
_DERIVED_TAILS = ("_odc", "_summary_matrix", "_updated", "_fixed")


def _sheet_verdict(name: str) -> Tuple[Optional[str], Optional[str]]:
    """Decide what a file name is, in one place for every caller.

    Returns ``(cohort_number, None)`` for a tracking sheet ("05"),
    ``(None, reason)`` for a file that looks like one but is refused (the
    reason is worded for a person), and ``(None, None)`` for anything else.
    """
    m = _SHEET_RE.match(name)
    if m:
        tail = m.group(2).lower()
        if any(mark in tail for mark in _DERIVED_TAILS):
            return None, ("written by a mousedb tool FROM the sheet (an export or a "
                          "fixed/updated copy), not the sheet itself")
        return "%02d" % int(m.group(1)), None
    if not _LOOKS_LIKE_SHEET_RE.search(name):
        return None, None
    if name.startswith("~"):
        # Excel creates "~$<name>" while a workbook is open and deletes it on
        # close. It is a few bytes of owner name, never a workbook.
        return None, "Excel lock file -- someone has the workbook open; not a workbook"
    return None, ("the name does not START with Connectome_NN_Animal_Tracking "
                  "(a copy, a note, or another program's file); if it really is "
                  "the cohort's sheet, rename it so it does")


def _sheet_cohort(name: str) -> Optional[str]:
    """The two-digit cohort number if ``name`` is a tracking sheet, else None."""
    return _sheet_verdict(name)[0]


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
            if d.is_dir() and any(_sheet_cohort(f.name) for f in d.iterdir()):
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
            if any(_sheet_cohort(n) for n in filenames):
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


def cohort_sheet_candidates(cohort: str, sheets_dir=None) -> List[Path]:
    """EVERY file in the folder whose name STARTS with this cohort's sheet name
    (a ``(2).xlsx``, a dated copy, a draft...), newest first. Excel lock files
    (``~$...``), copies named "Copy of ...", and files mousedb's own tools write
    beside a sheet are not candidates -- refused_sheet_files lists them.
    More than one candidate is a question for a person -- see pin_cohort_sheet."""
    d = Path(sheets_dir) if sheets_dir else cohort_sheets_dir()
    if d is None:
        return []
    n = _cohort_number(cohort)
    if n is None:
        return []
    hits = []
    try:
        for f in d.iterdir():
            if _sheet_cohort(f.name) == n:
                hits.append(f)
    except OSError:
        return []
    return sorted(hits, key=lambda p: p.stat().st_mtime, reverse=True)


def _ascii(text) -> str:
    """``text`` with anything outside ASCII escaped (an en dash becomes
    ``\\u2013``). WHY: file names come from disk and may hold any character,
    and messages built from them are printed to Windows consoles that crash on
    non-ASCII -- escaped, a name is still recognisable and never a crash."""
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def _stored_pin(n: str) -> Optional[str]:
    """The file name recorded for cohort ``n`` ("05") in local config."""
    return (_read_config().get("pinned_sheets") or {}).get("CNT_%s" % n)


def pinned_sheet(cohort: str) -> Optional[str]:
    """The filename a person pinned for this cohort ("it's this one"), or None.

    This is the RECORD, whether or not that file is still there -- a status
    display compares it with the cohort's files to tell a pin in force from a
    broken one (see broken_pins)."""
    n = _cohort_number(cohort)
    return _stored_pin(n) if n else None


def pin_cohort_sheet(cohort: str, filename: Optional[str], sheets_dir=None) -> Path:
    """Record which of several matching files IS the cohort's sheet.

    WHY: when a cohort matches more than one file, silently taking the newest
    was a guess nobody could see. The person who knows which file is real
    says so once; it is remembered in local config (never in source, never on
    the share) and shown in every status listing. ``filename=None`` unpins.

    A name that is not this cohort's sheet name (a typo, another cohort's
    sheet, a file refused by the naming rule such as "Copy of ...") raises
    ValueError and nothing is recorded. So does a correctly shaped name that
    is not in the sheets folder (a typo of a real variant, a file since
    renamed), whenever the folder can be read; the message lists the files
    the cohort does have. WHY: find_cohort_sheet only honours a pin that names
    one of the cohort's candidates, so such a pin would be broken from the
    moment it was made -- stored, then ignored, with the newest file imported.
    A name that differs from a real file only in letter case is stored as the
    file's real name, so it matches exactly afterwards.
    Returns the config path."""
    n = _cohort_number(cohort)
    if n is None:
        raise ValueError("not a cohort: %r" % cohort)
    name = Path(filename).name if filename else None
    if name and _sheet_cohort(name) != n:
        why = _sheet_verdict(name)[1] or (
            "it is not named as cohort %s's sheet (Connectome_%s_Animal_Tracking...xlsx)"
            % (n, n))
        raise ValueError(_ascii("cannot pin %r as the sheet for cohort %s: %s" % (name, n, why)))
    if name:
        d = Path(sheets_dir) if sheets_dir else cohort_sheets_dir()
        if d is not None:
            have = [c.name for c in cohort_sheet_candidates(n, sheets_dir=d)]
            if name not in have:
                same = [h for h in have if h.lower() == name.lower()]
                if len(same) != 1:
                    raise ValueError(_ascii(
                        "cannot pin %r as the sheet for cohort %s: no file of that name "
                        "is in the sheets folder. The files for cohort %s are: %s"
                        % (name, n, n, ", ".join(have) if have else "(none)")))
                name = same[0]
    cfg = _read_config()
    pins = cfg.setdefault("pinned_sheets", {})
    key = "CNT_%s" % n
    if name:
        pins[key] = name
    else:
        pins.pop(key, None)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return CONFIG_PATH


def find_cohort_sheet(cohort: str, sheets_dir=None) -> Optional[Path]:
    """The live tracking sheet for a cohort, or None.

    A pinned file wins if it is still one of the cohort's files. Otherwise --
    no pin, or a BROKEN pin (the pinned file renamed, moved or deleted) --
    where a cohort has more than one file (``..._Animal_Tracking1.xlsx``,
    dated variants), the most recently modified is used. Neither case may
    pass silently: callers that show status list every candidate
    (``cohort_sheet_candidates``) and say when a pin is broken
    (``broken_pins`` names the pinned file and the file read instead).
    """
    hits = cohort_sheet_candidates(cohort, sheets_dir=sheets_dir)
    if not hits:
        return None
    pin = pinned_sheet(cohort)
    if pin:
        for h in hits:
            if h.name == pin:
                return h
        # The pin names no candidate any more: fall back to the newest, as if
        # unpinned. broken_pins reports exactly this case, so it is not silent.
    return hits[0]


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
            n = _sheet_cohort(f.name)
            if n:
                found.add(n)
    except OSError:
        return []
    return sorted(found)


def refused_sheet_files(sheets_dir=None) -> List[Tuple[str, str]]:
    """Files in the sheets folder that LOOK like a tracking sheet but are not
    read, each as ``(file name, reason)``, sorted by name.

    WHY this exists: refusing a file is a decision, and a decision nobody can
    see is a silent drop. If a cohort's real sheet were ever saved under a name
    that is refused (say "Copy of ..."), the cohort would simply stop being
    imported; this list is what lets a status display say why.

    Only files the NAMING RULE refuses belong here: callers treat a name in
    this list as "never import this file". A broken pin is a different fact
    about a different thing (a pin, not a file) and is reported by
    broken_pins.
    """
    d = Path(sheets_dir) if sheets_dir else cohort_sheets_dir()
    if d is None:
        return []
    out = []
    try:
        for f in d.iterdir():
            reason = _sheet_verdict(f.name)[1]
            if reason:
                out.append((f.name, reason))
    except OSError:
        return []
    return sorted(out)


def broken_pins(sheets_dir=None) -> List[dict]:
    """Every BROKEN pin: a file a person pinned as a cohort's sheet that is no
    longer one of that cohort's files in the sheets folder. Sorted by cohort.

    Each is ``{"cohort_id", "pinned", "reading", "why"}``: the pinned file
    name; the file the cohort is reading instead (its newest file, exactly as
    if nothing were pinned -- or None when it has no file at all); and one
    sentence for a person saying what happened and what to do.

    WHY this exists: find_cohort_sheet honours a pin only while it names one
    of the cohort's files, and when the pinned file was renamed, moved or
    deleted it fell back to the newest file WITHOUT notice -- the status went
    on saying the cohort was pinned while a file nobody had chosen was the one
    imported. This is the notice. A pin recorded before the naming rule
    refused its file, or on another cohort's sheet, is broken the same way.
    """
    d = Path(sheets_dir) if sheets_dir else cohort_sheets_dir()
    if d is None:
        return []
    try:
        present = {f.name for f in d.iterdir()}
    except OSError:
        return []
    out = []
    pins = _read_config().get("pinned_sheets") or {}
    for key in sorted(pins):
        pin, n = pins.get(key), _cohort_number(key)
        if not pin or n is None:
            continue
        cands = cohort_sheet_candidates(n, sheets_dir=d)
        if any(c.name == pin for c in cands):
            continue          # in force: nothing to say
        cid = "CNT_%s" % n
        refusal = _sheet_verdict(pin)[1]
        if pin not in present:
            cause = "is no longer in the sheets folder (renamed, moved or deleted)"
        elif refusal:
            cause = "is in the folder but is not read as a sheet: %s" % refusal
        else:
            cause = "is not named as cohort %s's sheet" % n
        reading = cands[0].name if cands else None
        if reading:
            now = ("so %s is reading %s instead (its newest file, as if nothing "
                   "were pinned)" % (cid, reading))
            fix = ("in the Tracking Sheets tab select %s and press 'This is the "
                   "sheet', or run 'mousedb-sheets pin %s <file>' (or "
                   "'mousedb-sheets unpin %s')" % (cid, cid, cid))
        else:
            # WHY a different instruction: a cohort with no file has no row in
            # the Tracking Sheets tab to select, and a pin can only name a file
            # that is there -- so the only moves are to put the workbook back
            # or to drop the pin.
            now = "and %s has no other file, so it has no sheet to read" % cid
            fix = ("put the workbook back in the sheets folder (under the pinned "
                   "name the pin holds again; under another, pin that file), or, "
                   "if the cohort is gone for good, run 'mousedb-sheets unpin %s'"
                   % cid)
        out.append({
            "cohort_id": cid, "pinned": pin, "reading": reading,
            "why": "%s is pinned to %s, which %s; %s. To fix: %s." % (
                cid, pin, cause, now, fix),
        })
    return out


# ---------------------------------------------------------------------------
# ASPA animal sheets
# ---------------------------------------------------------------------------

ASPA_ENV_VAR = "MOUSEDB_ASPA_DATA"

# "J.xlsx", "K - <description>.xlsx", and an older form with a prefix,
# "OptD - <description> - <more>.xlsx". A cohort letter, optionally behind an
# "Opt" prefix, optionally followed by a description.
#
# The "Opt" prefix is not part of the cohort letter: some workbooks kept it
# from an earlier naming scheme. Matching only a bare letter silently lost every
# cohort whose workbook carries it.
_ASPA_RE = re.compile(r"^(?:Opt)?([A-Z])(?:\s*-\s*.+)?\.xlsx?$", re.I)


def _aspa_file_letter(name: str):
    m = _ASPA_RE.match(name)
    return m.group(1).upper() if m else None


def aspa_data_dir() -> Optional[Path]:
    """The configured folder of ASPA animal sheets, or None."""
    for raw in (os.environ.get(ASPA_ENV_VAR), _read_config().get("aspa_data_dir")):
        if not raw:
            continue
        d = Path(raw)
        try:
            if d.is_dir() and any(_aspa_file_letter(f.name) for f in d.iterdir()):
                return d
        except OSError:
            continue
    return None


def set_aspa_data_dir(path) -> Path:
    """Record where this machine's ASPA animal sheets are."""
    cfg = _read_config()
    cfg["aspa_data_dir"] = str(Path(path))
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return CONFIG_PATH


def aspa_letter(cohort) -> Optional[str]:
    """The ASPA cohort letter for anything that names a cohort.

    ASPA animals are named ``{letter}{subject:2d}`` (J11). The pipeline needs
    ``{letters}{cohort:2d}{subject:2d}``, so ids are encoded with
    **cohort number = the letter's alphabet position**: J11 -> ASPA1011. A rule
    rather than a lookup, so it reconstructs itself if any table is lost.

    Accepts 'J', 10, '10', 'ASPA_10', 'ASPA_10_11', 'ASPA1011'.
    """
    t = str(cohort).strip().upper()
    m = re.fullmatch(r"[A-Z]", t)
    if m:
        return t
    m = re.search(r"ASPA_?(\d{2})", t) or re.fullmatch(r"(\d{1,2})", t)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 26:
            return chr(ord("A") + n - 1)
    return None


def aspa_cohort_number(letter: str) -> Optional[str]:
    """'J' -> '10'. The inverse of :func:`aspa_letter`."""
    t = str(letter).strip().upper()
    return "%02d" % (ord(t) - ord("A") + 1) if re.fullmatch(r"[A-Z]", t) else None


def find_aspa_sheet(cohort, data_dir=None) -> Optional[Path]:
    """The ASPA animal workbook for a cohort, or None.

    ``cohort`` may be the letter, the encoded number, or a full animal id.
    """
    d = Path(data_dir) if data_dir else aspa_data_dir()
    if d is None:
        return None
    letter = aspa_letter(cohort)
    if letter is None:
        return None
    hits = []
    try:
        for f in d.iterdir():
            if _aspa_file_letter(f.name) == letter and not f.name.startswith("~"):
                hits.append(f)
    except OSError:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def available_aspa_cohorts(data_dir=None) -> List[str]:
    """Cohort letters that have an ASPA animal sheet, e.g. ['G','H','I','J']."""
    d = Path(data_dir) if data_dir else aspa_data_dir()
    if d is None:
        return []
    found = set()
    try:
        for f in d.iterdir():
            L = _aspa_file_letter(f.name)
            if L and not f.name.startswith("~"):
                found.add(L)
    except OSError:
        return []
    return sorted(found)


def fetch_aspa_sheet(cohort, dest_dir, data_dir=None) -> Optional[Path]:
    """Copy an ASPA cohort's workbook into ``dest_dir``. Read-only at the source."""
    src = find_aspa_sheet(cohort, data_dir=data_dir)
    if src is None:
        return None
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.copy2(src, dest)
    return dest


def describe() -> str:
    """What is configured, or how to configure it. For --help and for saying so
    out loud when an import finds nothing."""
    lines = []
    d = cohort_sheets_dir()
    if d is not None:
        cohorts = available_cohorts(d)
        lines.append("CNT cohort sheets: %s" % d)
        lines.append("  cohorts with a sheet: %s"
                     % (", ".join(cohorts) if cohorts else "(none)"))
        # Said out loud so that a sheet saved under a refused name shows up
        # here, rather than its cohort quietly dropping out of the import --
        # and a pin whose file has gone, rather than its cohort quietly
        # reading a file nobody chose. Each said once, here. (Escaped to
        # ASCII with everything else on the way out -- see the return.)
        for name, reason in refused_sheet_files(d):
            lines.append("  NOT read: %s -- %s" % (name, reason))
        for b in broken_pins(d):
            lines.append("  PIN BROKEN: %s" % b["why"])
    a = aspa_data_dir()
    if a is not None:
        letters = available_aspa_cohorts(a)
        lines.append("ASPA animal sheets: %s" % a)
        lines.append("  cohorts with a sheet: %s"
                     % (", ".join(letters) if letters else "(none)"))
    # Everything returned is escaped to ASCII, not only the file names. WHY:
    # this text is printed to Windows consoles and task logs that crash on
    # non-ASCII, and the folder paths and the config path (which holds the
    # user's home folder name) come from the machine and may hold any
    # character just as file names may. Escaped, a path is still
    # recognisable; unescaped, one accented letter is a crash.
    if lines:
        if d is None:
            lines.append("")
            lines.append("No CNT cohort sheets configured "
                         "(mousedb cohort-sheets --set <path>).")
        if a is None:
            lines.append("")
            lines.append("No ASPA animal sheets configured "
                         "(mousedb cohort-sheets --set-aspa <path>).")
        return _ascii("\n".join(lines))
    return _ascii("\n".join([
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
    ]))
