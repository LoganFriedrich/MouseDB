"""mousedb import-analyses -- MIRROR MouseBrain's analysis registry into the mousedb folders.

WHY THIS LIVES HERE AND NOT IN MOUSEBRAIN
-----------------------------------------
MouseBrain registers every analysis output it produces (per-sample
measurements, figures, the log of registrations) in ITS OWN pipeline folder,
``<mousebrain_pipeline_root>/Registry/``, through its AnalysisRegistry. That
registry is complete on its own: ``exports/<analysis>/registry.json`` records,
per sample, the method and parameters, the method hash, the source files, the
output files and whether the entry is still current. Putting those outputs
next to the behavioural exports, so a person finds every result in ONE place,
is an INTEGRATION -- this tool's job. Until 2026-08-28 MouseBrain wrote its
outputs straight into this tool's folder, which tied it to a folder it did not
own and could not run without. Now mousedb pulls: it mirrors the registry
tree, keeps its own ledger, and summarises the provenance in one manifest.

WHAT IT DOES
  1. Finds every ``exports/<analysis>/registry.json`` under the registry root
     (``<mousebrain_pipeline_root>/Registry``).
  2. Mirrors ``exports/<analysis>/**``, ``figures/<analysis>/**`` and
     ``logs/<analysis>.log`` into ``<mousedb_root>/exports/<analysis>/``,
     ``figures/<analysis>/`` and ``logs/<analysis>.log`` -- same relative
     paths, same modification times (shutil.copy2). A file is skipped when its
     destination already exists with the same size and modification time (so
     the first run over a tree that was mirrored before copies nothing), or
     when the ledger (``<mousedb_root>/logs/analysis_imports.json``) says it
     is unchanged; ``--all`` ignores the ledger and re-checks every file.
  3. Files mirrored on an earlier run that no longer exist at the source are
     MOVED to ``<mousedb_root>/_archived/analyses/<YYYYmmdd_HHMMSS>/<relative path>``.
     Nothing is ever deleted: the lab archives, it does not delete. A file is
     archived only when the run is SURE it is gone: its analysis was listed
     completely this run (registry.json included), or its analysis folder is
     confirmed gone. An analysis that could not be listed, or whose folder has
     no registry.json while the mirror holds files of it, keeps its mirror as
     it is and is reported as an error; a run that lists no registry at all
     while the ledger holds files archives nothing (see ``archivable_keys``).
     Every file kept back this way is an error of the run with its reason,
     and shows on the Where Is My Data tab (see ``held_problems``). A folder
     with no registry.json and nothing of it in the mirror -- neither in the
     ledger nor in the mirror's own folders, which are looked at too because
     a new or set-aside ledger knows nothing of earlier copies -- is not an
     error (MouseBrain creates the folder before anything is registered in
     it): there is nothing to copy and nothing to protect. "Of it" means
     marked as that analysis, not merely kept under the same name (see
     ``held_as_analysis``): this tool keeps other folders in the same places.
  3a. When a person confirms that MouseBrain withdrew an analysis (or files of
     it) -- ``--confirm-withdrawn <analysis>`` -- the run archives every
     mirrored file of that analysis MouseBrain no longer has, through the same
     archive step, even where it would otherwise hold back (see
     ``confirmed_extra_keys``). For a folder left WITHOUT its registry.json
     that is everything the mirror holds of the analysis, even files still in
     the source folder: without registry.json it is no analysis this job can
     mirror, and keeping part of it would fail every later run. For an
     analysis still registered, only the files MouseBrain dropped are
     archived. WHY a supported command: nothing but this
     tool writes into the mirror, so the remedy for "everything really was
     withdrawn" cannot be "move the files by hand". The name must be one
     something identifies as an analysis -- the ledger, a folder at the
     source the mirror holds copies from, or the mirror's own registry.json
     copy (``held_as_analysis``); any other name is refused and nothing
     moves, because the same places hold this tool's non-mirror folders.
  4. Writes ``<mousedb_root>/exports/ANALYSES_MANIFEST.json``: one row per
     analysis with its entry counts (current, invalidated, stale against the
     approved method) and what this run copied, skipped and archived, plus
     ``problems``: the run's problems that belong to no single analysis.
  5. A run that stops before it finishes (an unreadable ledger, a lock that
     did not come free in time, a registry it cannot reach) writes no
     manifest; it records the stop in ``<mousedb_root>/logs/
     analysis_import_last_failure.json`` instead, which the Where Is My Data
     tab shows until a run finishes and removes it (see ``record_stop``).

It writes NO database rows -- only files under <mousedb_root> -- so, unlike
``import-reaches``, it needs no watcher guard and may run at any time. It
does take a lock of its own (``LOCK_NAME``; ``<mousedb_root>/logs/
.import-analyses.task.lock``) so that only ONE mirror runs at a time: the
nightly task and the "Update the database now" button on the Where Is My
Data tab (mousedb update) both start it, and two runs copying, archiving and rewriting the same ledger at once lose each other's
entries (see ``main``). A ``--dry-run`` writes nothing and takes no lock.

Usage:
    mousedb import-analyses                # mirror what is new or changed
    mousedb import-analyses --dry-run      # count only; write nothing (no ledger, no manifest)
    mousedb import-analyses --all          # ignore the ledger; re-check every file
    mousedb import-analyses --confirm-withdrawn <analysis>   # archive what MouseBrain withdrew of it
    mousedb import-analyses --set-aside-ledger   # keep an unreadable ledger aside, start a new one
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import stat
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import require
from .state_files import (StateFileUnreadable, _confirmed_absent, _read_state_file,
                          _write_json_atomically)

logger = logging.getLogger(__name__)

REGISTRY_DIRNAME = "Registry"          # MouseBrain's AnalysisRegistry root, inside its pipeline folder
REGISTRY_FILE = "registry.json"        # one per analysis: exports/<analysis>/registry.json
LEDGER_NAME = "analysis_imports.json"  # <mousedb_root>/logs/analysis_imports.json
MANIFEST_NAME = "ANALYSES_MANIFEST.json"  # <mousedb_root>/exports/ANALYSES_MANIFEST.json
ARCHIVE_SUBDIR = ("_archived", "analyses")  # <mousedb_root>/_archived/analyses/<stamp>/...
LOCK_NAME = "import-analyses"          # <mousedb_root>/logs/.import-analyses.task.lock (see main)
STOP_RECORD_NAME = "analysis_import_last_failure.json"  # <mousedb_root>/logs/... (see record_stop)

MANIFEST_FIELDS = ("analysis_name", "entries", "current", "invalidated", "last_updated",
                   "approved_method_hash", "approved_method_recorded_at", "stale_vs_approved",
                   "no_approved_method", "set_aside_manifests", "files_copied",
                   "files_skipped", "files_archived", "imported_at", "source_root")


@dataclass
class ImportResult:
    analyses: int = 0
    scanned: int = 0
    copied: int = 0
    skipped: int = 0
    archived: int = 0
    errors: int = 0
    manifest: List[dict] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# discovery + ledger
# ---------------------------------------------------------------------------

def registry_root() -> Path:
    """Where MouseBrain keeps its registry on this machine (configured, never
    built in): mousebrain_registry_root, else <mousebrain_pipeline_root>/Registry."""
    return require("mousebrain_registry_root")


# WHY LISTING ERRORS MUST RAISE HERE
# ---------------------------------
# A mirrored file is archived when the ledger holds it but this run's listing
# of the source does not. So a listing that QUIETLY comes back short -- a
# share that drops for a moment, a folder that cannot be opened -- reads as
# "MouseBrain withdrew all of this", and every mirrored file of the analysis
# was moved to the archive. Path.glob / rglob / is_dir / is_file all swallow
# errors (is_dir() is False for a folder that cannot be read), which is how the
# old listing worked. Everything below lists with os.scandir, so an error is an
# error, and "not there" is believed only from a folder listing that answers
# (state_files._confirmed_absent), never from a FileNotFoundError alone.

def _scan(folder: Path) -> Optional[List[os.DirEntry]]:
    """The entries of ``folder``; None when it is confirmed absent. Any other
    listing failure raises."""
    try:
        with os.scandir(folder) as it:
            return list(it)
    except FileNotFoundError:
        if _confirmed_absent(folder):
            return None
        raise


def _files_under(top: Path) -> List[Path]:
    """Every file below ``top`` ([] when ``top`` is confirmed absent). A folder
    that cannot be listed raises instead of being skipped."""
    first = _scan(top)
    if first is None:
        return []
    found: List[Path] = []
    stack = [(top, first)]
    while stack:
        folder, entries = stack.pop()
        for e in entries:
            path = folder / e.name
            if e.is_dir():
                sub = _scan(path)
                if sub is not None:      # removed between the two listings: nothing in it
                    stack.append((path, sub))
            elif e.is_file():
                found.append(path)
    return found


def list_analysis_folders(root: Path) -> List[str]:
    """The names of the folders under ``<root>/exports`` (one per analysis),
    sorted. Raises when the folder cannot be listed; [] when it is confirmed
    absent."""
    entries = _scan(root / "exports")
    return sorted(e.name for e in entries or () if e.is_dir())


def find_registries(root: Path) -> List[Path]:
    """Every exports/<analysis>/registry.json under the registry root, sorted.
    Raises when a folder cannot be listed (see above)."""
    found = []
    for analysis in list_analysis_folders(root):
        entries = _scan(root / "exports" / analysis) or ()
        if any(e.name == REGISTRY_FILE and e.is_file() for e in entries):
            found.append(root / "exports" / analysis / REGISTRY_FILE)
    return found


def analysis_files(root: Path, analysis: str) -> List[Path]:
    """All files that belong to one analysis: its exports tree (registry.json
    included -- it IS the provenance), its figures tree and its log. Raises
    when any of them cannot be listed (see above): a short list would archive
    the files it left out."""
    found: List[Path] = []
    for d in (root / "exports" / analysis, root / "figures" / analysis):
        found.extend(_files_under(d))
    log_file = root / "logs" / ("%s.log" % analysis)
    try:
        if stat.S_ISREG(os.stat(log_file).st_mode):
            found.append(log_file)
    except FileNotFoundError:
        if not _confirmed_absent(log_file):
            raise
    return sorted(set(found))


def rel_key(root: Path, path: Path) -> str:
    """Ledger key: the path relative to the registry root, forward slashes,
    so a ledger written on one operating system reads on another."""
    return path.relative_to(root).as_posix()


def analysis_of_key(key: str) -> Optional[str]:
    """exports/<a>/... | figures/<a>/... | logs/<a>.log  ->  <a>"""
    parts = key.split("/")
    if len(parts) >= 3 and parts[0] in ("exports", "figures"):
        return parts[1]
    if len(parts) == 2 and parts[0] == "logs" and parts[1].endswith(".log"):
        return parts[1][:-4]
    return None


def ledger_path() -> Path:
    return require("mousedb_root") / "logs" / LEDGER_NAME


def load_ledger() -> Dict[str, dict]:
    """The mirror's ledger; {} when there is none yet (a first run).

    Raises ``StateFileUnreadable`` when the ledger is there but cannot be
    read. WHY it refuses instead of starting a new ledger: the ledger is the
    only record of which files were mirrored. Read as empty, nothing mirrored
    before looks "gone" to the archive step (it archives ledger keys that the
    source no longer lists), so a file MouseBrain withdrew afterwards stayed
    in the mirror for good -- and every file was hashed again. "None yet" is
    believed only from a folder listing (see state_files._read_state_file),
    because a dropped network share also answers "file not found"."""
    data = _read_state_file(
        ledger_path(), "analysis mirror ledger", dict,
        "otherwise run `mousedb import-analyses --set-aside-ledger`: it keeps "
        "that file beside the new one, starts a new ledger and re-checks every "
        "file; files MouseBrain withdrew before that are then archived with "
        "`--confirm-withdrawn <analysis>`", "nothing was copied or archived")
    if data is None:
        logger.warning("no analysis mirror ledger at %s yet: every file will be checked",
                       ledger_path())
        return {}
    return data


def save_ledger(ledger: Dict[str, dict]) -> None:
    # Atomic (temporary file + os.replace): a run killed mid-write leaves the
    # old ledger, never half of one that the next run would refuse to read.
    _write_json_atomically(ledger_path(), ledger)


SET_ASIDE_SUFFIX = ".set-aside-"   # logs/analysis_imports.json.set-aside-<YYYYmmdd_HHMMSS>


def ledger_needs_setting_aside() -> Tuple[bool, str]:
    """(True, why) when the ledger is there but holds something no run can
    use: content that is not JSON or not a mapping, or entries that name no
    analysis. (False, why) otherwise -- also when the file cannot be READ,
    which is the share, not the file, and is not mended by setting it aside."""
    p = ledger_path()
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        if _confirmed_absent(p):
            return False, "there is no ledger yet"
        return False, "the ledger cannot be reached (the share did not answer); run again later"
    except OSError as e:
        return False, "the ledger cannot be reached (%s); run again later" % e
    try:
        data = json.loads(text)
    except ValueError:
        return True, "it is not valid JSON"
    if not isinstance(data, dict):
        return True, "it holds a %s, not a mapping" % type(data).__name__
    unnamed = [k for k in data if analysis_of_key(k) is None]
    if unnamed:
        return True, "%d entr%s name no analysis" % (len(unnamed),
                                                    "y" if len(unnamed) == 1 else "ies")
    return False, "the ledger reads correctly"


def set_aside_ledger(stamp_dir: str) -> Path:
    """Move the ledger to ``<ledger>.set-aside-<stamp>`` beside it (kept,
    never deleted) so this run starts a new one.

    WHY a command and not "rename it by hand": nothing but this tool writes
    into its folders, and a ledger renamed by hand to some other name left no
    trace of why. The set-aside copy keeps the old record for anyone who
    needs to look at it."""
    p = ledger_path()
    target = p.with_name(p.name + SET_ASIDE_SUFFIX + stamp_dir)
    os.replace(str(p), str(target))
    return target


def mirror_files_of(dest: Path, analysis: str) -> List[Path]:
    """Every file the MIRROR holds for one analysis: ``exports/<a>/**``,
    ``figures/<a>/**`` and ``logs/<a>.log`` under ``<mousedb_root>``.
    Raises when a folder cannot be listed (a short list would hide files)."""
    found: List[Path] = []
    for d in (dest / "exports" / analysis, dest / "figures" / analysis):
        found.extend(_files_under(d))
    log_file = dest / "logs" / ("%s.log" % analysis)
    try:
        if stat.S_ISREG(os.stat(log_file).st_mode):
            found.append(log_file)
    except FileNotFoundError:
        if not _confirmed_absent(log_file):
            raise
    return sorted(set(found))


# ---------------------------------------------------------------------------
# a run that stopped before it finished
# ---------------------------------------------------------------------------
#
# WHY A RECORD OF ITS OWN: some stops happen before the run writes anything
# -- a ledger it refuses to read, a lock it could not get in time, a registry
# folder it cannot reach. ANALYSES_MANIFEST.json is then left as the last
# good run wrote it, so the Where Is My Data tab went on showing normal
# analysis lines with no reason anywhere but the scheduler's task log, which
# nobody opens -- and a damaged ledger fails every night until a person acts.
# The stop is therefore written to a small file of this tool's own, which the
# tab shows as an "[!] analysis mirror: ..." line. Not into the manifest: when
# the lock wait runs out, the run holding the lock is the one writing the
# manifest. The next run that finishes removes the record.

def stop_record_path() -> Path:
    return require("mousedb_root") / "logs" / STOP_RECORD_NAME


def record_stop(reason: str, before_mirroring: bool) -> Optional[str]:
    """Write the record of a run that stopped (atomically, like the ledger).
    ``before_mirroring``: True when the run stopped before it copied or
    archived anything. Returns None, or why the record could not be written
    (the caller prints that -- the stop itself is already an error)."""
    try:
        _write_json_atomically(stop_record_path(), {
            "stopped_at": datetime.now().isoformat(timespec="seconds"),
            "before_mirroring": bool(before_mirroring),
            "reason": reason,
        })
    except Exception as e:
        return "%s: %s" % (type(e).__name__, e)
    return None


def clear_stop() -> Optional[str]:
    """Remove the record once a run has finished. Returns None, or why it
    could not be removed."""
    try:
        os.remove(str(stop_record_path()))
    except FileNotFoundError:
        pass
    except Exception as e:
        return "%s: %s" % (type(e).__name__, e)
    return None


def read_stop(path: Path) -> Optional[dict]:
    """The record of the last run that stopped before it finished; None when
    there is none (the last run finished). Raises when it cannot be read.

    WHY "none" is believed only from a folder listing that answers:
    Path.is_file() is False for a file that could not be looked at (a share
    that dropped for a moment), and the status tab then showed no stop line
    and no reason -- the one message this record exists to deliver, hidden
    by the kind of fault it reports."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if _confirmed_absent(path):
            return None
        raise
    data = json.loads(text)
    return data if isinstance(data, dict) else {"reason": repr(data)}


def stop_line(rec: dict) -> str:
    """The Where Is My Data line for a stopped run (shown with ``[!]``)."""
    when = str(rec.get("stopped_at") or "?").replace("T", " ")
    if rec.get("before_mirroring", True):
        head = "the last run stopped before mirroring anything (%s)" % when
    else:
        head = ("the last run stopped part-way (%s); files may have been copied or "
                "archived, but its ledger and ANALYSES_MANIFEST.json may not have been "
                "written" % when)
    return ("analysis mirror: %s: %s -- the analysis lines shown are from the last run "
            "that finished" % (head, rec.get("reason") or "no reason recorded"))


# ---------------------------------------------------------------------------
# registry.json -> manifest row
# ---------------------------------------------------------------------------

def read_registry(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_registry(reg: dict) -> dict:
    """The provenance counts one manifest row carries. ``stale_vs_approved``
    is the number of CURRENT entries whose method hash is not the approved
    one -- outputs that exist but were produced with a method the analysis no
    longer approves, and so need re-running before they are used.

    An analysis with NO approved method (the registry header's
    ``approved_method_hash`` is empty -- MouseBrain writes that for analyses
    whose outputs no method approval judges) has nothing to be stale against:
    ``stale_vs_approved`` is None and ``no_approved_method`` True. WHY: counting
    every current entry as stale there (what a comparison with an empty hash
    gives) told a person that perfectly current outputs needed re-running.

    ``set_aside_manifests`` is how many damaged manifests the tool has set
    aside for a person to repair (the header's ``set_aside_manifests`` list).
    WHY: while one is set aside the analysis shows FEWER entries, and without
    this a person sees only a smaller number and no reason.

    The approved method is the one RECORDED in registry.json's header, not
    the one MouseBrain would approve today: this tool never reads MouseBrain's
    approved-method settings (it only mirrors). So the count is only as fresh
    as that header, and the row carries ``approved_method_recorded_at`` (when
    MouseBrain last wrote it; None in a registry written before MouseBrain
    recorded it) so every place that shows the count shows that date beside
    it. WHY: the header used to be written once, when the registry was
    created, and a later change of the approved method showed "0 stale" with
    nothing to say the comparison was against an old method."""
    entries = reg.get("entries") or {}
    approved = reg.get("approved_method_hash") or None
    current = [e for e in entries.values() if isinstance(e, dict) and e.get("is_current")]
    set_aside = reg.get("set_aside_manifests") or []
    return {
        "analysis_name": reg.get("analysis_name"),
        "entries": len(entries),
        "current": len(current),
        "invalidated": len(entries) - len(current),
        "last_updated": reg.get("last_updated"),
        "approved_method_hash": approved,
        "approved_method_recorded_at": reg.get("approved_method_recorded_at"),
        "stale_vs_approved": (None if approved is None else
                              sum(1 for e in current if e.get("method_hash") != approved)),
        "no_approved_method": approved is None,
        "set_aside_manifests": len(set_aside) if isinstance(set_aside, list) else 1,
    }


def stale_phrase(row: dict) -> str:
    """'N stale vs approved (approved method as recorded <date>)' for one
    manifest row, or 'no approved method'.

    WHY the date: the count compares each entry with the approved method
    RECORDED in MouseBrain's registry.json, which is only as new as the last
    time MouseBrain wrote that file (``mousebrain-registry --name <analysis>
    --refresh-approved`` rewrites it on demand, in a MouseBrain whose
    registry records ``approved_method_recorded_at``; an older one has
    neither the option nor the date). Shown bare, "0 stale" after a
    change of the approved method looked like "all current" when it meant
    "judged against the old method". The date lets a person see which.
    A registry written before MouseBrain recorded the date says so, after a
    dash rather than in a second pair of brackets inside the first, which
    read as a stray bracket on every line of an older MouseBrain's registry.

    A row whose registry.json was NOT read this run (``entries`` is None: the
    folder had no registry.json, could not be listed, or its registry.json
    could not be read) says exactly that. WHY: its approved-method fields are
    empty because nothing was read, not because an old MouseBrain wrote the
    registry; falling through to the "unknown date" text blamed the wrong
    thing. The reason itself is in the row's ``problems`` and is shown by
    ``data_status.analysis_lines``."""
    stale = row.get("stale_vs_approved")
    if row.get("no_approved_method"):
        return "no approved method"
    if row.get("entries") is None:
        return "? stale vs approved (registry not read this run)"
    if stale is None and row.get("approved_method_hash") is None:
        return "no approved method"
    recorded = row.get("approved_method_recorded_at")
    when = (str(recorded)[:16].replace("T", " ") if recorded
            else "at an unknown date -- registry written before the date was kept")
    return "%s stale vs approved (approved method as recorded %s)" % (
        "?" if stale is None else stale, when)


def _new_row(analysis: str, imported_at: str, root: Path) -> dict:
    row = {k: None for k in MANIFEST_FIELDS}
    row.update({"analysis_name": analysis, "files_copied": 0, "files_skipped": 0,
                "files_archived": 0, "imported_at": imported_at, "source_root": str(root),
                "problems": []})
    return row


def manifest_path() -> Path:
    return require("mousedb_root") / "exports" / MANIFEST_NAME


def write_manifest(rows: List[dict], imported_at: str, root: Path,
                   problems: Optional[List[str]] = None) -> Path:
    """Write the manifest: one row per analysis, plus ``problems``, the
    run-level problems (ones that belong to no single analysis's row).

    WHY the run-level list: a run that held mirrored files back without
    archiving them (see ``held_problems``) can have no row to hang the reason
    on -- when the source listed no analysis at all, the rows were empty and
    the manifest said only ``{"analyses": []}``, so the Where Is My Data tab
    showed no analysis and no reason while the mirror still held every file.
    data_status shows these problems as ``[!]`` lines."""
    # Atomic for the same reason as the ledger: the Where Is My Data tab reads
    # this file, and a half-written one showed there as "manifest unreadable".
    p = manifest_path()
    _write_json_atomically(p, {"imported_at": imported_at, "source_root": str(root),
                               "problems": list(problems or []), "analyses": rows})
    return p


# ---------------------------------------------------------------------------
# mirroring
# ---------------------------------------------------------------------------

def same_file(src_stat: os.stat_result, dst: Path) -> bool:
    """Destination already holds this file: same size and same modification
    time (copy2 preserves it). Whole-second comparison, because network
    shares and different filesystems round mtimes differently."""
    try:
        d = dst.stat()
    except OSError:
        return False
    return d.st_size == src_stat.st_size and int(d.st_mtime) == int(src_stat.st_mtime)


def mirror_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dst))  # copy2: keeps the modification time, which same_file() relies on


def archive_dir(dest: Path, stamp_dir: str) -> Path:
    return dest.joinpath(*ARCHIVE_SUBDIR) / stamp_dir


def archivable_keys(ledger: Dict[str, dict], present: set, listed: set,
                    folders: List[str], root: Path) -> Tuple[List[str], List[str]]:
    """Split the ledger keys this run did not see into (archive, keep).

    A key is archived only when its analysis was listed COMPLETELY this run
    (its folders listed without error, its registry.json among them), or
    when the analysis's exports folder is confirmed gone -- missing from the
    listing of exports/ and confirmed absent by a listing of its own.
    Everything else (an analysis that could not be listed, a folder without
    its registry.json, a key that names no analysis) is kept: its mirror
    stays as it is and its ledger entry stays, so a later run can decide.

    WHY: archiving moves the mirror's copy away. Deciding "withdrawn" from a
    listing that failed or came back short moved every mirrored file of an
    analysis to the archive because its registry.json was not seen once."""
    gone: List[str] = []
    kept: List[str] = []
    folder_names = set(folders)
    absent: Dict[str, bool] = {}
    for key in sorted(k for k in ledger if k not in present):
        analysis = analysis_of_key(key)
        if analysis in listed:
            gone.append(key)
            continue
        if analysis is not None and analysis not in folder_names:
            if analysis not in absent:
                absent[analysis] = _confirmed_absent(root / "exports" / analysis)
            if absent[analysis]:
                gone.append(key)
                continue
        kept.append(key)
    return gone, kept


def held_as_analysis(analysis: str, mirrored: set, mirror_files: List[Path], dest: Path,
                     root: Path, source_files: Optional[List[Path]]) -> bool:
    """Is what the mirror holds under this name the mirror of an ANALYSIS?
    True when at least one of these marks it as one:

    (a) the ledger records files of it (``mirrored``: this tool copied them);
    (b) the source lists ``exports/<name>`` this run (``source_files``: the
        files of that analysis there, None when it lists no such folder) and
        the mirror has a copy of one of them -- same relative path, size and
        modification time, the mark this tool's own copies carry (see
        ``same_file``). A source folder that still has its registry.json
        never gets here: ``run`` takes it as an analysis outright;
    (c) the mirror holds ``exports/<name>/registry.json``, the copy of
        MouseBrain's own record that only a mirrored analysis has.

    WHY a name alone is not enough: ``<mousedb_root>/exports``, ``figures``
    and ``logs`` are not the mirror's alone. This tool keeps its own export
    folders and its other jobs' log files in the same places, under names
    that are no analysis. Judged by "is there any file under
    exports/<name>", ``--confirm-withdrawn <name>`` moved such a folder --
    live files other tools read -- into the archive, with exit code 0. So
    the confirmation, and the hold that points a person at it, act only on
    a name one of the marks above identifies.

    WHY (b) asks for a copied file and not just for the source folder:
    MouseBrain creates ``exports/<name>`` for any name its registry is
    opened with, a mistyped or unrelated one included, so an empty source
    folder says nothing about what the mirror keeps under that name."""
    if analysis in mirrored:
        return True
    if any(f == dest / "exports" / analysis / REGISTRY_FILE for f in mirror_files):
        return True
    if source_files and mirror_files:
        for src in source_files:
            try:
                if same_file(os.stat(src), dest / rel_key(root, src)):
                    return True
            except OSError:
                continue            # cannot be looked at: no mark from this file
    return False


def confirmed_extra_keys(confirmed: List[str], ledger: Dict[str, dict], present: set,
                         unlistable: set, recognized: set, dest: Path, run_problem,
                         mirror_listing: Dict[str, List[Path]]) -> Dict[str, dict]:
    """For each analysis a person confirmed withdrawn (``--confirm-withdrawn``),
    the mirror's files of it that neither the ledger nor this run's listing of
    the source holds -- as extra keys for ``archivable_keys``, which still
    decides, as for any ledger key, whether the run may archive them.
    ``mirror_listing``: the mirror's files of each confirmed analysis, listed
    once at the start of the run; an analysis missing from it is one whose
    mirror folders could not be listed -- held whole by ``run``, which reports
    it, so it gets no extra keys here.

    ``recognized``: the confirmed names that ``held_as_analysis`` identifies
    as an analysis (worked out by ``run``, which has the listings).

    WHY the mirror is listed too: the ledger is the usual record of what was
    mirrored, but a ledger set aside (``--set-aside-ledger``) forgot every
    file mirrored before it, so a file MouseBrain withdrew before that would
    otherwise stay in the mirror for good. Refused, as an error of the run:
    a name that is not a single folder name; a name nothing identifies as an
    analysis (see ``held_as_analysis`` -- a mistyped name, or the name of a
    folder or log this tool keeps for another purpose); an analysis whose
    source or mirror folders could not be listed this run.

    WHY a name is not accepted just because the mirror's folders hold files
    under it: those folders also hold this tool's own, non-mirror folders
    and logs, and accepting any name with files archived them whole. WHY it
    is not judged by the mirror's copy of registry.json alone either: a
    mirror can hold files of an analysis without that one file (the copy
    was archived or never made) while the ledger no longer knows the
    analysis; the nightly run holds such an analysis and names this command
    as the remedy, so the command has to accept it -- which it does through
    the copies the mirror shares with the source folder. Both sides use the
    one rule, so every hold that names this command can be settled by it.

    The corner that stays refused: the ledger knows nothing of the name, the
    mirror has no registry.json copy of it, and the source has no folder
    ``exports/<name>`` holding a file the mirror has a copy of (the source
    folder wholly gone, or emptied). Nothing then tells those files from
    files kept for another purpose, so they stay where they are."""
    extra: Dict[str, dict] = {}
    for a in confirmed:
        if not _plain_name(a):
            run_problem("--confirm-withdrawn %r: not an analysis name (one folder name, no "
                        "path); nothing was archived for it" % a)
            continue
        if a in unlistable:
            run_problem("--confirm-withdrawn %s: its folders at the source could not be "
                        "listed this run, so nothing of it was archived; run again" % a)
            continue
        if a not in mirror_listing:
            continue                    # its mirror could not be listed: held and reported by run
        if a not in recognized:
            n = len(mirror_listing[a])
            if n:
                run_problem("--confirm-withdrawn %s: the mirror holds no analysis by that name. "
                            "%d file(s) sit under exports/%s, figures/%s or logs/%s.log, but "
                            "nothing marks them as a mirrored analysis (nothing in the ledger, "
                            "no exports/%s/%s in the mirror, and no file of theirs is a copy "
                            "of one in a folder exports/%s at the source); they were left "
                            "where they are and nothing was archived -- check the name"
                            % (a, n, a, a, a, a, REGISTRY_FILE, a))
            else:
                run_problem("--confirm-withdrawn %s: the mirror holds no analysis by that name "
                            "(no file under exports/%s or figures/%s, no logs/%s.log, and "
                            "nothing in the ledger); nothing was archived -- check the name"
                            % (a, a, a, a))
            continue
        for f in mirror_listing[a]:
            k = rel_key(dest, f)
            if k not in ledger and k not in present:
                extra[k] = {}
    return extra


def held_problems(kept: List[str], rows: Dict[str, dict], imported_at: str, root: Path,
                  reason: Optional[str] = None) -> List[str]:
    """Make every kept ledger key visible. Returns the run-level problems
    to report (each one an error of the run); adds a row, with its reason,
    for every analysis that has kept keys but no row yet.

    Keys of an analysis that already has a row need nothing more: that row
    already carries the problem that kept them (no registry.json, a folder
    that could not be listed). The others were kept for a reason no row
    shows: the analysis was missing from the listing of exports/ but its
    absence could not be confirmed (the share answered short, or a second
    listing failed), the run listed no analysis at all (``reason`` is then
    that run-level message), or the key names no analysis.

    WHY: such keys used to be kept silently -- only a "N kept" count in the
    summary line, exit code 0, and nothing on the Where Is My Data tab --
    so a mirror frozen for a reason nobody could see looked current."""
    held: Dict[str, int] = {}
    unnamed: List[str] = []
    for key in kept:
        a = analysis_of_key(key)
        if a is None:
            unnamed.append(key)
        elif a not in rows:
            held[a] = held.get(a, 0) + 1
    out: List[str] = []
    for a in sorted(held):
        row = _new_row(a, imported_at, root)
        if reason is not None:
            row["problems"].append(
                "%s: not listed this run (no analysis registry was listed at all); its %d "
                "mirrored file(s) were kept, not archived" % (a, held[a]))
        else:
            row["problems"].append(
                "%s: its folder was missing from the listing of exports/ but could not be "
                "confirmed gone; its %d mirrored file(s) were kept, not archived -- the "
                "mirror is kept as it was" % (a, held[a]))
        rows[a] = row
    if held and reason is None:
        out.append("%d analysis folder(s) missing from the listing of %s could not be "
                   "confirmed gone, so their mirrored files were kept, not archived: %s. "
                   "Check that the share is reachable and run again; if the analysis was "
                   "withdrawn, the next run that can confirm it archives its mirror"
                   % (len(held), root / "exports", ", ".join(sorted(held))))
    if unnamed:
        out.append("%d ledger entr%s name%s no analysis (for example %s) and were kept, not "
                   "archived; they do not belong in the analysis mirror's ledger -- run "
                   "`mousedb import-analyses --set-aside-ledger`, which keeps logs/%s "
                   "aside and starts a new one"
                   % (len(unnamed), "y" if len(unnamed) == 1 else "ies",
                      "s" if len(unnamed) == 1 else "", unnamed[0], LEDGER_NAME))
    return out


def _plain_name(name: str) -> bool:
    """An analysis name as MouseBrain makes one: a single folder name, never
    a path, so ``--confirm-withdrawn`` cannot reach outside the three places
    the mirror writes to (``exports/<name>``, ``figures/<name>``,
    ``logs/<name>.log``). That alone does not make the name an analysis:
    other folders and logs of this tool sit in the same places, which is
    what ``held_as_analysis`` is for."""
    return bool(name) and name not in (".", "..") and not any(c in name for c in ("/", "\\", ":"))


def run(all_files: bool = False, dry_run: bool = False, limit: Optional[int] = None,
        log=print, confirm_withdrawn: Optional[List[str]] = None,
        set_aside: bool = False, progress: Optional[dict] = None) -> ImportResult:
    """One mirror run. ``confirm_withdrawn``: analyses a person confirms
    MouseBrain withdrew (wholly or in part) -- every mirrored file of them
    that MouseBrain no longer has is archived (all of it, for a folder left
    without registry.json), see ``--confirm-withdrawn`` in main.
    ``set_aside``: an unusable ledger is moved aside first and the run starts
    a new one, see ``set_aside_ledger``. ``progress``: when given, its
    ``"mirroring"`` is set True as the run starts copying and archiving, so
    a caller whose run stops can say whether anything had been changed."""
    res = ImportResult()
    root = registry_root()
    dest = require("mousedb_root")
    if not (root / "exports").is_dir():
        raise FileNotFoundError(
            "no analysis registry at %s (MouseBrain has not registered any analysis yet, "
            "or mousebrain_registry_root / mousebrain_pipeline_root points at the wrong "
            "folder: see mousedb config --show)" % root)
    folders = list_analysis_folders(root)   # raises when exports/ cannot be listed
    now = datetime.now()
    imported_at = now.isoformat(timespec="seconds")
    stamp_dir = now.strftime("%Y%m%d_%H%M%S")
    ledger: Optional[Dict[str, dict]] = None
    if set_aside:
        needed, why = ledger_needs_setting_aside()
        if not needed:
            log("  [note] --set-aside-ledger: the ledger was left as it is (%s)" % why)
        elif dry_run:
            log("  [note] --set-aside-ledger: would set %s aside (%s) and start a new one"
                % (ledger_path(), why))
            ledger = {}
        else:
            target = set_aside_ledger(stamp_dir)
            log("  [note] --set-aside-ledger: %s set aside as %s (%s); a new ledger is started"
                % (ledger_path(), target.name, why))
    if ledger is None:
        ledger = load_ledger()
    # Analyses of which the ledger records mirrored files.
    mirrored = {analysis_of_key(k) for k in ledger}
    confirmed = sorted(set(confirm_withdrawn or ()))
    rows: Dict[str, dict] = {}
    present = set()
    todo: List[Tuple[Path, str, str, Optional[str]]] = []  # (src, key, stamp, hash-if-known)
    # Analyses whose files were listed completely this run (registry.json
    # among them). Only these -- and analyses whose folder is confirmed gone
    # -- may have mirrored files archived; see archivable_keys.
    listed: set = set()
    # Analyses whose folder is there without registry.json and that a person
    # confirmed withdrawn (--confirm-withdrawn): archived like a listed one.
    withdrawn: set = set()
    unlistable: set = set()        # analyses whose source folders could not be listed
    # The mirror's files of each analysis a person confirmed withdrawn, listed
    # once, up front. One whose mirror folders cannot be listed is held WHOLE
    # this run -- not treated as withdrawn, and none of its files archived,
    # not even those the ledger knows of. WHY: the confirmation archives what
    # the mirror holds; with the mirror unlistable the run cannot know what
    # that is, and archiving the ledger's part of it while reporting "nothing
    # of it was archived" told the person the mirror was left alone when it
    # was not.
    mirror_listing: Dict[str, List[Path]] = {}
    mirror_unlistable: Dict[str, str] = {}
    # The confirmed names something identifies as an analysis (see
    # held_as_analysis); only these are archived on a person's word.
    recognized: set = set()
    for a in confirmed:
        if not _plain_name(a):
            continue
        try:
            mirror_listing[a] = mirror_files_of(dest, a)
        except OSError as e:
            mirror_unlistable[a] = "%s" % e

    run_problems: List[str] = []   # problems no single row carries (manifest "problems")

    def problem(row: dict, msg: str) -> None:
        res.errors += 1
        row["problems"].append(msg)
        res.messages.append(msg)
        log("  [FAIL] %s" % msg)

    def run_problem(msg: str) -> None:
        res.errors += 1
        run_problems.append(msg)
        res.messages.append(msg)
        log("  [FAIL] %s" % msg)

    for analysis in folders:
        row = _new_row(analysis, imported_at, root)
        rows[analysis] = row
        try:
            files = analysis_files(root, analysis)
        except OSError as e:
            unlistable.add(analysis)
            problem(row, "%s: its files could not be listed (%s); nothing of it was copied "
                         "or archived -- the mirror is kept as it was" % (analysis, e))
            continue
        reg_path = root / "exports" / analysis / REGISTRY_FILE
        if reg_path not in files:
            if analysis in mirror_unlistable:
                # Confirmed withdrawn, but the mirror's folders of it could not
                # be listed: held whole (see mirror_listing above); the
                # reason is added to its row below. Nothing of it is listed
                # this run, so none of its ledger keys can be archived.
                continue
            # Does the mirror hold this ANALYSIS? The ledger answers for
            # what this ledger copied, but the MIRROR itself is asked too.
            # WHY: a ledger set aside (--set-aside-ledger), lost, or new on a
            # machine whose mirror already exists knows nothing of earlier
            # copies; deciding from it alone read "nothing to keep safe"
            # while the mirror still held the analysis, and the analysis
            # dropped off the Where Is My Data tab with no reason given.
            # WHY not "any file under that name": see held_as_analysis.
            others: List[Path] = []     # files under the name that mark no analysis
            in_mirror = analysis in mirrored
            if not in_mirror:
                try:
                    others = (mirror_listing[analysis] if analysis in mirror_listing
                              else mirror_files_of(dest, analysis))
                except OSError as e:
                    problem(row, "%s: exports/%s has no %s, and the mirror's folders of it "
                                 "could not be listed (%s); nothing of it was copied or "
                                 "archived -- run again" % (analysis, analysis, REGISTRY_FILE, e))
                    continue
                in_mirror = held_as_analysis(analysis, mirrored, others, dest, root, files)
            if in_mirror and analysis in confirmed:
                # A person confirmed the withdrawal: archive its WHOLE mirror
                # (below) -- files still in the source folder included, since
                # none of them is added to ``present``. WHY whole: without its
                # registry.json the folder is no analysis this job can mirror;
                # keeping some of its files would leave the analysis held, and
                # failing, on every later run, so the confirmation would never
                # settle it. Its source files are not touched.
                del rows[analysis]
                withdrawn.add(analysis)
                recognized.add(analysis)
                log("  [note] %s: no %s at the source and confirmed withdrawn; its mirror "
                    "is archived" % (analysis, REGISTRY_FILE))
                continue
            if not in_mirror:
                # Nothing of it is in the mirror, so there is nothing to keep
                # safe and nothing to copy: no error. WHY: MouseBrain creates
                # exports/<analysis> as soon as anything opens that analysis's
                # registry -- before anything is registered in it (a new
                # analysis, a mistyped name given to its registry command).
                # Counted as an error, that empty folder failed this job every
                # night and no later run could clear it.
                del rows[analysis]
                if others:
                    # Files sit under this name on the mousedb side, but
                    # nothing marks them as the mirror of this folder: they
                    # are left alone, and NO remedy is offered. WHY no
                    # "--confirm-withdrawn": that would point a person at
                    # archiving files that are, as far as anything shows,
                    # kept there for another purpose (a source folder made
                    # by opening the registry with the name of one of this
                    # tool's own folders). --confirm-withdrawn refuses the
                    # name for the same reason.
                    log("  [note] %s: exports/%s has no %s, and the %d file(s) kept under "
                        "that name on the mousedb side are not copies of anything in it "
                        "(nothing in the ledger, no %s copy); they are left as they are -- "
                        "nothing to do" % (analysis, analysis, REGISTRY_FILE, len(others),
                                           REGISTRY_FILE))
                else:
                    log("  [note] %s: exports/%s has no %s and nothing of it is in the "
                        "mirror; nothing to do (an analysis with nothing registered yet)"
                        % (analysis, analysis, REGISTRY_FILE))
                continue
            # A folder without its registry.json, while the mirror holds files
            # of it, is not proof of a withdrawal (a person withdraws an
            # analysis by removing its whole folder; MouseBrain never removes
            # one): it is a registry being rebuilt or set aside as damaged, or
            # a share that answered short. Archiving on it moved every
            # mirrored file of the analysis away.
            problem(row, "%s: exports/%s has no %s; nothing of it was copied or archived "
                         "-- the mirror is kept as it was. If MouseBrain is rebuilding its "
                         "registry, run again later; if the analysis was withdrawn, run "
                         "`mousedb import-analyses --confirm-withdrawn %s`"
                    % (analysis, analysis, REGISTRY_FILE, analysis))
            continue
        res.analyses += 1
        listed.add(analysis)
        recognized.add(analysis)        # registered at the source: an analysis
        try:
            row.update(summarize_registry(read_registry(reg_path)))
        except Exception as e:
            problem(row, "%s: %s unreadable: %s" % (analysis, REGISTRY_FILE, e))
        for src in files:
            key = rel_key(root, src)
            present.add(key)
            res.scanned += 1
            st = src.stat()
            stamp = "%d:%d" % (st.st_size, int(st.st_mtime))
            dst = dest / key
            prev = ledger.get(key)
            # Fast path: same size and modification time as when it was last
            # mirrored, and the mirror is still there -> unchanged, without
            # reading the file (these live on a network share; hashing a
            # gigabyte of figures every hour is not acceptable). A changed
            # stamp is confirmed by content hash so a touch without a change
            # is not copied again.
            if not all_files and isinstance(prev, dict) and dst.exists():
                if prev.get("stamp") == stamp:
                    res.skipped += 1
                    row["files_skipped"] += 1
                    continue
                h = file_hash(src)
                if prev.get("hash") == h:
                    ledger[key] = {"hash": h, "stamp": stamp}
                    res.skipped += 1
                    row["files_skipped"] += 1
                    continue
                todo.append((src, key, stamp, h))
                continue
            # Destination already identical (size + mtime): the first run over
            # a tree that is already there, or a file copied by hand.
            if same_file(st, dst):
                if not dry_run:
                    ledger[key] = {"hash": file_hash(src), "stamp": stamp}
                res.skipped += 1
                row["files_skipped"] += 1
                continue
            todo.append((src, key, stamp, None))

    if limit:
        todo = todo[:limit]
    # Mirrored before, gone from the source now: to be archived (never deleted)
    # -- but only when the run can be sure it is gone (see archivable_keys).
    # Files of an analysis a person confirmed withdrawn that the mirror holds
    # but the ledger does not (a ledger set aside or started anew) join them.
    for a in mirror_listing:
        # A confirmed name with no folder at the source this run: an analysis
        # only by the ledger or by the mirror's own registry.json copy.
        if a not in recognized and held_as_analysis(a, mirrored, mirror_listing[a],
                                                    dest, root, None):
            recognized.add(a)
    extra = confirmed_extra_keys(confirmed, ledger, present, unlistable, recognized, dest,
                                 run_problem, mirror_listing)
    pool = dict(ledger)
    pool.update(extra)
    gone, kept = archivable_keys(pool, present, listed | withdrawn, folders, root)
    for a in sorted(mirror_unlistable):
        if a in unlistable:
            continue                    # its source could not be listed either: reported already
        # Held whole (see mirror_listing): even the files the ledger knows
        # MouseBrain dropped stay, so the message below is true as written.
        gone, held_now = ([k for k in gone if analysis_of_key(k) != a],
                          [k for k in gone if analysis_of_key(k) == a])
        kept = sorted(kept + held_now)
        if a not in rows:
            rows[a] = _new_row(a, imported_at, root)
        problem(rows[a], "--confirm-withdrawn %s: the mirror's folders of it could not be "
                         "listed (%s); nothing of it was archived -- the mirror is kept as it "
                         "was; run again" % (a, mirror_unlistable[a]))
    guard = None
    held_back = [k for k in gone if analysis_of_key(k) not in confirmed]
    if held_back and not listed:
        # Not one registry was listed, yet the ledger holds mirrored files:
        # far more likely an empty or wrong listing (a share that answered
        # with nothing, a pipeline root pointing at a new, empty folder) than
        # every analysis withdrawn at once. Archive nothing a person has not
        # confirmed withdrawn (--confirm-withdrawn).
        gone = [k for k in gone if analysis_of_key(k) in confirmed]
        kept = sorted(kept + held_back)
        msg = ("no analysis registry was listed under %s, but the ledger holds %d mirrored "
               "files; nothing was archived. Check that the registry folder is right "
               "(mousedb config --show) and the share is reachable, then run again. If "
               "these analyses really were withdrawn in MouseBrain, run `mousedb "
               "import-analyses --confirm-withdrawn <analysis>` once for each (%s): that "
               "archives their mirror"
               % (root / "exports", len(held_back),
                  ", ".join(sorted({analysis_of_key(k) or "?" for k in held_back}))))
        run_problem(msg)
        guard = msg
    for msg in held_problems(kept, rows, imported_at, root, reason=guard):
        run_problem(msg)
    for a in confirmed:
        if a not in unlistable and a in recognized and a in mirror_listing and not any(
                analysis_of_key(k) == a for k in gone + kept):
            log("  [note] --confirm-withdrawn %s: the mirror holds no file of it that "
                "MouseBrain no longer has; nothing to archive" % a)
    log("import-analyses: %d analyses under %s; %d files: %d to copy, %d unchanged, %d to archive, "
        "%d kept (not listed this run)%s" %
        (res.analyses, root, res.scanned, len(todo), res.skipped, len(gone), len(kept),
         " [dry run]" if dry_run else ""))

    if progress is not None and not dry_run:
        progress["mirroring"] = True
    for src, key, stamp, h in todo:
        row = rows.get(analysis_of_key(key) or "")
        try:
            if not dry_run:
                mirror_file(src, dest / key)
                ledger[key] = {"hash": h or file_hash(src), "stamp": stamp}
            res.copied += 1
            if row is not None:
                row["files_copied"] += 1
        except Exception as e:
            res.errors += 1
            res.messages.append("%s: %s" % (key, e))
            log("  [FAIL] %s: %s" % (key, e))

    for key in gone:
        row = rows.get(analysis_of_key(key) or "")
        old = dest / key
        try:
            # Is the mirror's copy still there? Asked with os.stat, and "gone"
            # believed only from a folder listing that answers. WHY:
            # Path.is_file() answers False for a copy that could not be
            # looked at (a share that dropped for a moment), and the ledger
            # entry was then dropped below without the copy being archived --
            # a withdrawn file left in the mirror for good, with no error.
            # Any other failure raises here: an error of the run, and the
            # ledger keeps the key for the next run.
            try:
                old_mode: Optional[int] = os.stat(old).st_mode
            except FileNotFoundError:
                if not _confirmed_absent(old):
                    raise
                old_mode = None          # already gone from the mirror: nothing to move
            if old_mode is not None and not stat.S_ISREG(old_mode):
                raise OSError("%s is not a file" % old)
            if old_mode is not None:
                # The lab archives, it never deletes: the mirrored copy is
                # MOVED under _archived/analyses/<stamp>/ so a result that was
                # withdrawn upstream is still there to be looked at.
                if not dry_run:
                    target = archive_dir(dest, stamp_dir) / key
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(old), str(target))
                res.archived += 1
                if row is not None:
                    row["files_archived"] += 1
            if not dry_run:
                ledger.pop(key, None)
        except Exception as e:
            res.errors += 1
            res.messages.append("archive %s: %s" % (key, e))
            log("  [FAIL] archive %s: %s" % (key, e))

    res.manifest = [rows[a] for a in sorted(rows)]
    if not dry_run:
        save_ledger(ledger)  # also persists refreshed stamps for unchanged files
        write_manifest(res.manifest, imported_at, root, problems=run_problems)
    for row in res.manifest:
        if row.get("entries") is not None:
            log("  %s: %d entries, %d current, %s, %d invalidated; "
                "%d copied, %d unchanged, %d archived" % (
                    row["analysis_name"], row["entries"], row["current"], stale_phrase(row),
                    row["invalidated"], row["files_copied"], row["files_skipped"], row["files_archived"]))
    log("import-analyses: %d files copied, %d unchanged, %d archived, %d errors%s" % (
        res.copied, res.skipped, res.archived, res.errors, " [dry run]" if dry_run else ""))
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="Ignore the ledger; re-check every file")
    ap.add_argument("--dry-run", action="store_true",
                    help="Count what would be copied or archived; write nothing (no ledger, no manifest)")
    ap.add_argument("--limit", type=int, help="Copy at most this many files (testing)")
    ap.add_argument("--json", action="store_true", help="Machine-readable summary")
    ap.add_argument("--confirm-withdrawn", action="append", metavar="ANALYSIS", default=[],
                    help="Confirm that MouseBrain withdrew this analysis (or files of it): "
                         "archive every mirrored file of it that MouseBrain no longer has, "
                         "even when the run would otherwise keep them back; for a folder "
                         "left without registry.json, everything the mirror holds of it, "
                         "even files still at the source (repeatable). Refused, with "
                         "nothing moved, for a name nothing identifies as an analysis: "
                         "not in the ledger, no registry.json copy in the mirror, and no "
                         "folder at the source the mirror holds copies from")
    ap.add_argument("--set-aside-ledger", action="store_true",
                    help="If the ledger cannot be used (not JSON, or entries that name no "
                         "analysis), keep it beside the new one as "
                         "analysis_imports.json.set-aside-<time> and start a new ledger")
    args = ap.parse_args(argv)
    quiet = (lambda *_: None) if args.json else print
    from .task_mutex import LockWaitTimeout, TaskMutex
    if args.dry_run:
        # Writes nothing, so it takes no lock and neither records nor clears
        # a stop (see _stopped).
        try:
            res = run(all_files=args.all, dry_run=True, limit=args.limit, log=quiet,
                      confirm_withdrawn=args.confirm_withdrawn, set_aside=args.set_aside_ledger)
        except StateFileUnreadable as e:
            return _stopped(args, "import-analyses not run: %s" % e, before_mirroring=True)
        except Exception as e:
            return _stopped(args, "%s" % e, before_mirroring=True)
        if args.json:
            print(json.dumps(res.as_dict(), indent=1))
        return 1 if res.errors else 0
    # One mirror at a time. WHY: the nightly task and the "Update the
    # database now" button on the Where Is My Data tab (mousedb update) both
    # run this, and nothing kept them apart. Each run reads the ledger at the
    # start and rewrites it at the end, so two at once dropped the other's
    # entries (files then copied and hashed again), and both could move the
    # same withdrawn file. A second run waits for the first (task_mutex's
    # usual wait: as long as the holder is working) and then finds nothing
    # left to do. Its own lock, not the database lock: this import writes
    # files only, and must not queue behind the hours-long database jobs. It
    # sits beside the ledger it protects (<mousedb_root>/logs), named
    # explicitly so it never depends on which settings point the database
    # lock elsewhere.
    #
    # The record of a stopped run (record_stop / clear_stop) is written and
    # removed WHILE THE LOCK IS HELD. WHY: done after the lock was released,
    # a run that had just finished could remove the record that the run
    # waiting behind it had already written (that run takes the lock, stops
    # at once on an unreadable ledger, records it -- and the first run's
    # late clear deletes it), so the tab showed nothing until the next
    # failure. Only the lock holder touches the record, with one exception:
    # a run that never got the lock (LockWaitTimeout) has to record that
    # itself, outside it.
    progress: dict = {}
    rc: Optional[int] = None
    res: Optional[ImportResult] = None
    try:
        lock = TaskMutex(LOCK_NAME, base_dir=ledger_path().parent,
                         label="the analysis-import lock")
        with lock.acquire(log=(quiet if args.json else print),
                          waiting_for="another analysis import"):
            try:
                res = run(all_files=args.all, dry_run=False, limit=args.limit, log=quiet,
                          confirm_withdrawn=args.confirm_withdrawn,
                          set_aside=args.set_aside_ledger, progress=progress)
            except StateFileUnreadable as e:
                rc = _stopped(args, "import-analyses not run: %s" % e, before_mirroring=True)
            except Exception as e:
                rc = _stopped(args, "%s" % e, before_mirroring=not progress.get("mirroring"))
            else:
                rc = 1 if res.errors else 0
                # The run finished: its manifest now says what holds, so the
                # record of an earlier stop goes (see record_stop).
                err = clear_stop()
                if err:
                    print("[!] the record of an earlier stopped run (%s) could not be "
                          "removed (%s); the Where Is My Data tab still shows it -- run again"
                          % (stop_record_path(), err))
                    rc = 1
    except LockWaitTimeout as e:
        return _stopped(args, "import-analyses not run: %s" % e, before_mirroring=True)
    except Exception as e:
        # The lock itself failed (its folder cannot be written, or it could
        # not be released). If the run inside had already ended, its result
        # stands and only the lock fault is reported; otherwise nothing ran.
        if rc is None:
            return _stopped(args, "import-analyses not run: %s" % e, before_mirroring=True)
        print("[!] the analysis-import lock could not be released cleanly (%s)" % e)
        rc = 1
    if res is None:
        return rc
    if args.json:
        print(json.dumps(res.as_dict(), indent=1))
    return rc


def _stopped(args, msg: str, before_mirroring: bool) -> int:
    """Report a run that stopped before it finished: the [FAIL] line, and --
    unless it was a dry run, which writes nothing -- the record the Where Is
    My Data tab shows (see record_stop)."""
    print("[FAIL] %s" % msg)
    if not args.dry_run:
        err = record_stop(msg, before_mirroring)
        if err:
            print("[!] this stop could not be recorded for the Where Is My Data tab (%s)" % err)
    return 1


if __name__ == "__main__":
    sys.exit(main())
