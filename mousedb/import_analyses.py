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
     no registry.json, keeps its mirror as it is and is reported as an error;
     a run that lists no registry at all while the ledger holds files
     archives nothing (see ``archivable_keys``).
  4. Writes ``<mousedb_root>/exports/ANALYSES_MANIFEST.json``: one row per
     analysis with its entry counts (current, invalidated, stale against the
     approved method) and what this run copied, skipped and archived.

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
        "otherwise rename that file (keep it) and run again -- the run then "
        "starts a new ledger, re-checks every file, and cannot archive files "
        "that MouseBrain withdrew before the rename", "nothing was copied or archived")
    if data is None:
        logger.warning("no analysis mirror ledger at %s yet: every file will be checked",
                       ledger_path())
        return {}
    return data


def save_ledger(ledger: Dict[str, dict]) -> None:
    # Atomic (temporary file + os.replace): a run killed mid-write leaves the
    # old ledger, never half of one that the next run would refuse to read.
    _write_json_atomically(ledger_path(), ledger)


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
    --refresh-approved`` rewrites it on demand). Shown bare, "0 stale" after a
    change of the approved method looked like "all current" when it meant
    "judged against the old method". The date lets a person see which.
    A registry written before MouseBrain recorded the date says so.

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
            else "at an unknown date (registry written before the date was kept)")
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


def write_manifest(rows: List[dict], imported_at: str, root: Path) -> Path:
    # Atomic for the same reason as the ledger: the Where Is My Data tab reads
    # this file, and a half-written one showed there as "manifest unreadable".
    p = manifest_path()
    _write_json_atomically(p, {"imported_at": imported_at, "source_root": str(root),
                               "analyses": rows})
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


def run(all_files: bool = False, dry_run: bool = False, limit: Optional[int] = None,
        log=print) -> ImportResult:
    res = ImportResult()
    root = registry_root()
    dest = require("mousedb_root")
    if not (root / "exports").is_dir():
        raise FileNotFoundError(
            "no analysis registry at %s (MouseBrain has not registered any analysis yet, "
            "or mousebrain_registry_root / mousebrain_pipeline_root points at the wrong "
            "folder: see mousedb config --show)" % root)
    folders = list_analysis_folders(root)   # raises when exports/ cannot be listed
    ledger = load_ledger()
    now = datetime.now()
    imported_at = now.isoformat(timespec="seconds")
    stamp_dir = now.strftime("%Y%m%d_%H%M%S")
    rows: Dict[str, dict] = {}
    present = set()
    todo: List[Tuple[Path, str, str, Optional[str]]] = []  # (src, key, stamp, hash-if-known)
    # Analyses whose files were listed completely this run (registry.json
    # among them). Only these -- and analyses whose folder is confirmed gone
    # -- may have mirrored files archived; see archivable_keys.
    listed: set = set()

    def problem(row: dict, msg: str) -> None:
        res.errors += 1
        row["problems"].append(msg)
        res.messages.append(msg)
        log("  [FAIL] %s" % msg)

    for analysis in folders:
        row = _new_row(analysis, imported_at, root)
        rows[analysis] = row
        try:
            files = analysis_files(root, analysis)
        except OSError as e:
            problem(row, "%s: its files could not be listed (%s); nothing of it was copied "
                         "or archived -- the mirror is kept as it was" % (analysis, e))
            continue
        reg_path = root / "exports" / analysis / REGISTRY_FILE
        if reg_path not in files:
            # A folder without its registry.json is not something MouseBrain
            # withdrew (that removes the folder): it is a registry being
            # rebuilt, or a share that answered short. Archiving on it moved
            # every mirrored file of the analysis away.
            problem(row, "%s: exports/%s has no %s; nothing of it was copied or archived "
                         "-- the mirror is kept as it was" % (analysis, analysis, REGISTRY_FILE))
            continue
        res.analyses += 1
        listed.add(analysis)
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
    gone, kept = archivable_keys(ledger, present, listed, folders, root)
    if gone and not listed:
        # Not one registry was listed, yet the ledger holds mirrored files:
        # far more likely an empty or wrong listing (a share that answered
        # with nothing, a pipeline root pointing at a new, empty folder) than
        # every analysis withdrawn at once. Archive nothing.
        kept, gone = sorted(kept + gone), []
        msg = ("no analysis registry was listed under %s, but the ledger holds %d mirrored "
               "files; nothing was archived. If every analysis really was withdrawn, move "
               "the mirrored files to _archived by hand and rename the ledger (keep it)"
               % (root / "exports", len(kept)))
        res.errors += 1
        res.messages.append(msg)
        log("  [FAIL] %s" % msg)
    log("import-analyses: %d analyses under %s; %d files: %d to copy, %d unchanged, %d to archive, "
        "%d kept (not listed this run)%s" %
        (res.analyses, root, res.scanned, len(todo), res.skipped, len(gone), len(kept),
         " [dry run]" if dry_run else ""))

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
            if old.is_file():
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
        write_manifest(res.manifest, imported_at, root)
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
    args = ap.parse_args(argv)
    quiet = (lambda *_: None) if args.json else print
    from .task_mutex import LockWaitTimeout, TaskMutex
    try:
        if args.dry_run:
            res = run(all_files=args.all, dry_run=True, limit=args.limit, log=quiet)
        else:
            # One mirror at a time. WHY: the nightly task and the "Update
            # the database now" button on the Where Is My Data tab
            # (mousedb update) both run this, and nothing
            # kept them apart. Each run reads the ledger at the start and
            # rewrites it at the end, so two at once dropped the other's
            # entries (files then copied and hashed again), and both could
            # move the same withdrawn file. A second run waits for the first
            # (task_mutex's usual wait: as long as the holder is working) and
            # then finds nothing left to do. Its own lock, not the database
            # lock: this import writes files only, and must not queue behind
            # the hours-long database jobs. It sits beside the ledger it
            # protects (<mousedb_root>/logs), named explicitly so it never
            # depends on which settings point the database lock elsewhere.
            lock = TaskMutex(LOCK_NAME, base_dir=ledger_path().parent,
                             label="the analysis-import lock")
            with lock.acquire(log=(quiet if args.json else print),
                              waiting_for="another analysis import"):
                res = run(all_files=args.all, dry_run=False, limit=args.limit, log=quiet)
    except LockWaitTimeout as e:
        print("[FAIL] import-analyses not run: %s" % e)
        return 1
    except StateFileUnreadable as e:
        print("[FAIL] import-analyses not run: %s" % e)
        return 1
    except Exception as e:
        print("[FAIL] %s" % e)
        return 1
    if args.json:
        print(json.dumps(res.as_dict(), indent=1))
    return 1 if res.errors else 0


if __name__ == "__main__":
    sys.exit(main())
