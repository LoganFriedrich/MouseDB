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
     Nothing is ever deleted: the lab archives, it does not delete.
  4. Writes ``<mousedb_root>/exports/ANALYSES_MANIFEST.json``: one row per
     analysis with its entry counts (current, invalidated, stale against the
     approved method) and what this run copied, skipped and archived.

It writes NO database rows -- only files under <mousedb_root> -- so, unlike
``import-reaches``, it needs no watcher guard and may run at any time.

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
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import require

logger = logging.getLogger(__name__)

REGISTRY_DIRNAME = "Registry"          # MouseBrain's AnalysisRegistry root, inside its pipeline folder
REGISTRY_FILE = "registry.json"        # one per analysis: exports/<analysis>/registry.json
LEDGER_NAME = "analysis_imports.json"  # <mousedb_root>/logs/analysis_imports.json
MANIFEST_NAME = "ANALYSES_MANIFEST.json"  # <mousedb_root>/exports/ANALYSES_MANIFEST.json
ARCHIVE_SUBDIR = ("_archived", "analyses")  # <mousedb_root>/_archived/analyses/<stamp>/...

MANIFEST_FIELDS = ("analysis_name", "entries", "current", "invalidated", "last_updated",
                   "approved_method_hash", "stale_vs_approved", "files_copied", "files_skipped",
                   "files_archived", "imported_at", "source_root")


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


def find_registries(root: Path) -> List[Path]:
    """Every exports/<analysis>/registry.json under the registry root, sorted."""
    exports = root / "exports"
    if not exports.is_dir():
        return []
    return sorted(p for p in exports.glob("*/" + REGISTRY_FILE) if p.is_file())


def analysis_files(root: Path, analysis: str) -> List[Path]:
    """All files that belong to one analysis: its exports tree (registry.json
    included -- it IS the provenance), its figures tree and its log."""
    found: List[Path] = []
    for d in (root / "exports" / analysis, root / "figures" / analysis):
        if d.is_dir():
            found.extend(p for p in d.rglob("*") if p.is_file())
    log_file = root / "logs" / ("%s.log" % analysis)
    if log_file.is_file():
        found.append(log_file)
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
    p = ledger_path()
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("ledger %s unreadable; starting a new one", p)
    return {}


def save_ledger(ledger: Dict[str, dict]) -> None:
    p = ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, indent=1, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# registry.json -> manifest row
# ---------------------------------------------------------------------------

def read_registry(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_registry(reg: dict) -> dict:
    """The provenance counts one manifest row carries. ``stale_vs_approved``
    is the number of CURRENT entries whose method hash is not the approved
    one -- outputs that exist but were produced with a method the analysis no
    longer approves, and so need re-running before they are used."""
    entries = reg.get("entries") or {}
    approved = reg.get("approved_method_hash")
    current = [e for e in entries.values() if isinstance(e, dict) and e.get("is_current")]
    return {
        "analysis_name": reg.get("analysis_name"),
        "entries": len(entries),
        "current": len(current),
        "invalidated": len(entries) - len(current),
        "last_updated": reg.get("last_updated"),
        "approved_method_hash": approved,
        "stale_vs_approved": sum(1 for e in current if e.get("method_hash") != approved),
    }


def _new_row(analysis: str, imported_at: str, root: Path) -> dict:
    row = {k: None for k in MANIFEST_FIELDS}
    row.update({"analysis_name": analysis, "files_copied": 0, "files_skipped": 0,
                "files_archived": 0, "imported_at": imported_at, "source_root": str(root),
                "problems": []})
    return row


def manifest_path() -> Path:
    return require("mousedb_root") / "exports" / MANIFEST_NAME


def write_manifest(rows: List[dict], imported_at: str, root: Path) -> Path:
    p = manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"imported_at": imported_at, "source_root": str(root),
                             "analyses": rows}, indent=1), encoding="utf-8")
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


def run(all_files: bool = False, dry_run: bool = False, limit: Optional[int] = None,
        log=print) -> ImportResult:
    res = ImportResult()
    root = registry_root()
    dest = require("mousedb_root")
    if not (root / "exports").is_dir():
        raise FileNotFoundError(
            "no analysis registry at %s (MouseBrain has not registered any analysis yet, "
            "or mousebrain_pipeline_root points at the wrong folder)" % root)
    registries = find_registries(root)
    res.analyses = len(registries)
    ledger = load_ledger()
    now = datetime.now()
    imported_at = now.isoformat(timespec="seconds")
    stamp_dir = now.strftime("%Y%m%d_%H%M%S")
    rows: Dict[str, dict] = {}
    present = set()
    todo: List[Tuple[Path, str, str, Optional[str]]] = []  # (src, key, stamp, hash-if-known)

    for reg_path in registries:
        analysis = reg_path.parent.name
        row = _new_row(analysis, imported_at, root)
        try:
            row.update(summarize_registry(read_registry(reg_path)))
        except Exception as e:
            res.errors += 1
            msg = "%s: %s unreadable: %s" % (analysis, REGISTRY_FILE, e)
            row["problems"].append(msg)
            res.messages.append(msg)
            log("  [FAIL] %s" % msg)
        rows[analysis] = row
        for src in analysis_files(root, analysis):
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
    # Mirrored before, gone from the source now: to be archived (never deleted).
    gone = sorted(k for k in ledger if k not in present)
    log("import-analyses: %d analyses under %s; %d files: %d to copy, %d unchanged, %d to archive%s" %
        (res.analyses, root, res.scanned, len(todo), res.skipped, len(gone),
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
            log("  %s: %d entries, %d current, %d stale vs approved, %d invalidated; "
                "%d copied, %d unchanged, %d archived" % (
                    row["analysis_name"], row["entries"], row["current"], row["stale_vs_approved"],
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
    try:
        res = run(all_files=args.all, dry_run=args.dry_run, limit=args.limit,
                  log=(lambda *_: None) if args.json else print)
    except Exception as e:
        print("[FAIL] %s" % e)
        return 1
    if args.json:
        print(json.dumps(res.as_dict(), indent=1))
    return 1 if res.errors else 0


if __name__ == "__main__":
    sys.exit(main())
