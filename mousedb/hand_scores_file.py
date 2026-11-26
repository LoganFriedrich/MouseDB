"""Every hand pellet score, written as one file the reaching pipeline reads.

WHY
---
A video is checked against its session's hand scores before the reaching
pipeline files it: filing means done, so a pellet the pipeline and the bench
disagree about goes to a person first, and a video whose hand scores are not
there yet waits for the sheet's next import (a session never scored by hand is
then filed without the comparison). The pipeline is a separate tool that never
imports mousedb,
so mousedb hands it the scores as a plain file at a path the pipeline is
configured to read.

WHAT IS WRITTEN (in <mousedb_root>/exports/Behavior/Hand_Scores/)
--------------------------------------------------------
hand_pellet_scores.csv   one row per hand-scored pellet:
    video_key      the session as the pipeline names its video:
                   <YYYYMMDD>_<animal>_<tray type><tray number>, the animal
                   written without underscores (PROJA_02_07 -> PROJA0207)
    pellet         pellet number, 1-20
    score          0 = not touched, 1 = displaced, 2 = retrieved (eaten)
    subject_id, cohort_id, project, session_date, tray
hand_pellet_scores.json  beside it:
    written_at              when this file was written
    rows, videos            how many pellets, how many sessions
    last_successful_import  {cohort id: when its last sheet import that SAVED
                            the cohort's scores ended -- even if a later step of
                            that import, such as copying the animal details,
                            failed} -- so the pipeline can tell "the sheet has
                            not been imported since this video was ready"
                            (wait) from "it was, and has no scores for it"
                            (never scored by hand: filed without comparing)
    sheets_seen             every cohort the sheets folder has EVER been seen
                            to hold a sheet for (each import that reads the
                            configured folder adds the cohorts it found; none is
                            ever taken off). WHY never taken off: a cohort that
                            is on this list waits for its first import, and one
                            that is not is settled by full_import_at as having
                            no sheet -- so its videos are filed without the
                            hand-score comparison. A sheet that drops out of one
                            folder listing (the sync program mid-update, the
                            file open elsewhere, renamed for a moment) would otherwise
                            take its cohort off the list, and a cohort never
                            imported yet would then have its videos filed uncompared.
                            A cohort whose sheet is really gone and was never
                            imported keeps its videos waiting, listed, until a
                            person sees to it.
    full_import_at          when the last import of EVERY sheet in the folder
                            ended. With sheets_seen it answers the one case
                            last_successful_import cannot: a cohort with no
                            import at all. If it has a sheet, its first import
                            is still to come (wait); if it has none, an import
                            of every sheet after the video was ready settles
                            that no score exists. WHY not written_at: a hand
                            import of one cohort rewrites the file too, and
                            says nothing about the others.

    scores_sha256           the SHA-256 of hand_pellet_scores.csv as written with
                            this manifest. WHY: the two files are two renames,
                            not one, so a reader can catch the new scores file
                            beside the old manifest (or the reverse). The hash
                            is what lets a reader tell: it CAN compare it with
                            the scores file it read and, when they differ, read
                            both again instead of keeping a mismatched pair.
                            Writing the hash does not by itself make any reader
                            do that -- a reader that does not look at this key
                            still cannot detect a mismatched pair. A manifest
                            written before this key existed has none, so a
                            reader that checks must accept a missing hash.

The file is rewritten after every sheet import (any route: the nightly job,
the GUI, mousedb-sheets) and by `mousedb hand-scores`. Each file is written to
a temporary name and renamed, so a reader never sees half a file; the scores
file is renamed first and the manifest, carrying its hash, second. A rename
that Windows refuses because a reader has the file open at that instant is
retried for up to ten seconds before the write counts as failed.

ONE WRITER AT A TIME. The whole write -- reading the previous manifest,
reading the database, both temporary files and both renames -- happens while
holding a lock file beside the scores file (WRITE_LOCK_NAME, the same kind of
lock as the database-task lock: a crashed holder's lock is broken, a running
holder's never). A second writer (a button import finishing while the nightly
folder import does) waits for it. WHY: several routes write these files. Two
writes that overlapped could each start from the same previous manifest, and
the one that finished last dropped the cohorts the other had just added to
sheets_seen -- the failure sheets_seen exists to prevent; or both wrote the
same temporary file, leaving a scores file that does not match the hash in the
manifest beside it, so the reaching pipeline held videos until the next write.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

FILE_NAME = "hand_pellet_scores.csv"
MANIFEST_NAME = "hand_pellet_scores.json"
# The lock one write holds from start to finish (see ONE WRITER AT A TIME);
# task_mutex names the file ".<name>.task.lock", in the output folder.
WRITE_LOCK_NAME = "hand_pellet_scores_write"
COLUMNS = ["video_key", "pellet", "score", "subject_id", "cohort_id", "project",
           "session_date", "tray"]


def out_dir() -> Path:
    from .output_folders import hand_scores_dir
    return hand_scores_dir()


def video_key(subject_id: str, session_date, tray_type: str, tray_number) -> str:
    """'PROJA_02_07', 2025-07-09, 'P', 1 -> '20250709_PROJA0207_P1'."""
    day = str(session_date)[:10].replace("-", "")
    return "%s_%s_%s%d" % (day, subject_id.replace("_", ""), tray_type, int(tray_number))


# How long a rename refused by Windows (PermissionError: a reader -- the
# reaching pipeline, a person's spreadsheet program -- has the target open) is
# retried before the write counts as failed: REPLACE_TRIES tries,
# REPLACE_WAIT_SECONDS apart. The same pattern as the reach import's ledger
# write (import_reaches._write_json_atomically).
REPLACE_TRIES = 20
REPLACE_WAIT_SECONDS = 0.5


def _replace(tmp: Path, target: Path) -> None:
    """os.replace, retried while Windows refuses it because the target is open.
    WHY: a reader holds the file only for a moment; failing the whole write on
    that moment would otherwise leave the scores file new and its manifest old."""
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_WAIT_SECONDS)


class ManifestUnreadable(RuntimeError):
    """The existing manifest is there but cannot be read or understood."""


def _previous_manifest(path: Path) -> dict:
    """The manifest the last write left, or {} when there is none yet.

    WHY it refuses instead of starting from nothing: sheets_seen only grows,
    and is carried from the previous manifest. Taking an unreadable manifest
    (a moment's read failure on the share, a damaged file) as empty would
    shrink the list to this one import's listing -- and a never-imported
    cohort whose sheet is missing from that listing would drop off it, so
    its videos would be filed without the hand-score comparison. A read
    failure is retried like a refused rename; one that persists, or a file
    that is not a JSON object, raises ManifestUnreadable, which fails the
    write (the import reports "FAIL hand scores") and leaves both files as
    they were. Only a manifest that does not EXIST counts as a first write."""
    for attempt in range(REPLACE_TRIES):
        try:
            text = path.read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return {}
        except OSError as e:
            if attempt == REPLACE_TRIES - 1:
                raise ManifestUnreadable(
                    "the existing %s could not be read (%s); not rewritten, so its "
                    "list of cohorts with a sheet is not lost" % (path.name, e)) from e
            time.sleep(REPLACE_WAIT_SECONDS)
    try:
        data = json.loads(text)
    except ValueError as e:
        data = e
    if not isinstance(data, dict):
        raise ManifestUnreadable(
            "the existing %s is damaged (not a JSON object); not rewritten, so its "
            "list of cohorts with a sheet is not lost. Restore it from a backup, or "
            "delete it to start that list again from the next import" % path.name)
    return data


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _scores_landed(entry: dict) -> bool:
    """Whether one ledger line is an import that SAVED its cohort's scores.

    The line's scores_saved when it has one (sheet_sync.SCORES_SAVED: true even
    when a later step of the same import, such as copying the animal details,
    failed); a line written before that key existed counts by "success", as
    before. WHY scores_saved and not success: a video waits for its session's
    hand scores until an import after it was ready has brought the cohort's
    scores in. Counting by success let a failed accessory step hold videos
    whose scores had in fact been saved."""
    if entry.get("dry_run"):
        return False
    if "scores_saved" in entry:
        return bool(entry["scores_saved"])
    return bool(entry.get("success"))


def _last_successful_imports() -> Dict[str, str]:
    """{cohort_id: when its last (not dry-run) sheet import that saved the
    cohort's scores finished} -- see _scores_landed."""
    from .sheet_sync import _read_ledger
    out = {}
    for e in _read_ledger():
        if _scores_landed(e) and e.get("cohort_id"):
            when = e.get("finished") or e.get("started")
            if when and when > out.get(e["cohort_id"], ""):
                out[e["cohort_id"]] = when
    return out


def _read_rows(db_file, q: str) -> list:
    """Run one read of the database and return its rows. Opened through
    db_read.connect_for_reading, so a stopped writer's unfinished save does not
    make the read fail (see that module for why)."""
    from .db_read import connect_for_reading
    con = connect_for_reading(db_file)
    try:
        return con.execute(q).fetchall()
    finally:
        con.close()


def _sheets_seen(previous, now) -> Optional[list]:
    """The previous list with this import's cohorts added (see sheets_seen).
    None while no import has ever named any, as before."""
    if now is None:
        return previous
    return sorted(set(previous or []) | set(now))


def write_lock(dest: Path):
    """The held lock one write keeps from start to finish (see ONE WRITER AT A
    TIME), as a context manager. It lives in ``dest`` -- beside the files it
    guards, so every writer of those files finds the same lock whatever its
    configuration says, and a test writing into a temporary folder locks only
    that folder."""
    from .task_mutex import TaskMutex
    return TaskMutex(WRITE_LOCK_NAME, base_dir=dest,
                     label="the hand-scores file lock").acquire(
        waiting_for="another write of the hand-scores file")


def write(db_file: Optional[Path] = None, dest: Optional[Path] = None, log=print,
          full_run: bool = False, sheets_seen: Optional[list] = None) -> dict:
    """Write the scores file and its manifest, as the only writer for the
    whole write (write_lock). Reads the database under the cross-task mutex
    (a short read). ``full_run``: this follows an import of every sheet in
    the folder (sets full_import_at; otherwise the previous value is kept).
    ``sheets_seen``: the cohorts this import found in the configured sheets
    folder; they are ADDED to the previous list, never replace it (see
    sheets_seen in the module docstring). None adds nothing. Returns the
    manifest."""
    from .config import require
    db_file = Path(db_file) if db_file else require("db_path")
    dest = Path(dest) if dest else out_dir()
    dest.mkdir(parents=True, exist_ok=True)
    with write_lock(dest):
        return _write_locked(db_file, dest, log, full_run, sheets_seen)


def _write_locked(db_file: Path, dest: Path, log, full_run: bool,
                  sheets_seen: Optional[list]) -> dict:
    """write(), with the write lock held: the previous manifest is read under
    it, so no other write can land between that read and this rename."""
    from .task_mutex import hold
    # Read first: a manifest that cannot be read stops the write before
    # either file is replaced (see _previous_manifest).
    previous = _previous_manifest(dest / MANIFEST_NAME)
    q = """SELECT ps.subject_id, ps.session_date, ps.tray_type, ps.tray_number,
                  ps.pellet_number, ps.score, s.cohort_id, c.project_code
           FROM pellet_scores ps
           JOIN subjects s ON s.subject_id = ps.subject_id
           LEFT JOIN cohorts c ON c.cohort_id = s.cohort_id"""
    with hold(waiting_for="another database task"):
        rows = _read_rows(db_file, q)
    out = []
    for sid, day, tt, tn, pellet, score, cohort, project in rows:
        out.append((video_key(sid, day, tt, tn), int(pellet), int(score), sid, cohort,
                    project or (cohort or "").split("_")[0], str(day)[:10], "%s%d" % (tt, int(tn))))
    out.sort()
    tmp = dest / (FILE_NAME + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(out)
    # Hashed from the temporary file, which is exactly what the rename puts in
    # place; the manifest below carries the hash (see scores_sha256 above).
    scores_hash = _sha256(tmp)
    _replace(tmp, dest / FILE_NAME)
    now = datetime.now().isoformat(timespec="seconds")
    manifest = {
        "written_at": now,
        "rows": len(out),
        "videos": len({r[0] for r in out}),
        "last_successful_import": _last_successful_imports(),
        "sheets_seen": _sheets_seen(previous.get("sheets_seen"), sheets_seen),
        "full_import_at": now if full_run else previous.get("full_import_at"),
        "scores_sha256": scores_hash,
    }
    tmp = dest / (MANIFEST_NAME + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    _replace(tmp, dest / MANIFEST_NAME)
    log("Hand scores written: %d pellets of %d sessions -> %s" % (
        manifest["rows"], manifest["videos"], dest / FILE_NAME))
    return manifest


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.parse_args(argv)
    try:
        write()
    except Exception as e:
        print("[FAIL] hand scores not written: %s: %s" % (type(e).__name__, e))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
