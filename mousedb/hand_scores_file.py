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

WHAT IS WRITTEN (in <mousedb_root>/exports/hand_scores/)
--------------------------------------------------------
hand_pellet_scores.csv   one row per hand-scored pellet:
    video_key      the session as the pipeline names its video:
                   <YYYYMMDD>_<animal>_<tray type><tray number>, the animal
                   written without underscores (CNT_02_07 -> CNT0207)
    pellet         pellet number, 1-20
    score          0 = not touched, 1 = displaced, 2 = retrieved (eaten)
    subject_id, cohort_id, project, session_date, tray
hand_pellet_scores.json  beside it:
    written_at              when this file was written
    rows, videos            how many pellets, how many sessions
    last_successful_import  {cohort id: when its last successful sheet import
                            ended} -- so the pipeline can tell "the sheet has
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
                            file open elsewhere, renamed for a moment) used to
                            take its cohort off the list, and a cohort never
                            imported yet then had its videos filed uncompared.
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
                            beside the old manifest (or the reverse). The reader
                            compares this hash with the scores file it read and,
                            when they differ, reads both again instead of
                            keeping a mismatched pair. A manifest written before
                            this key existed has none; a reader accepts it.

The file is rewritten after every sheet import (any route: the nightly job,
the GUI, mousedb-sheets) and by `mousedb hand-scores`. Each file is written to
a temporary name and renamed, so a reader never sees half a file; the scores
file is renamed first and the manifest, carrying its hash, second. A rename
that Windows refuses because a reader has the file open at that instant is
retried for up to ten seconds before the write counts as failed.
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
COLUMNS = ["video_key", "pellet", "score", "subject_id", "cohort_id", "project",
           "session_date", "tray"]


def out_dir() -> Path:
    from .config import require
    return require("mousedb_root") / "exports" / "hand_scores"


def video_key(subject_id: str, session_date, tray_type: str, tray_number) -> str:
    """'CNT_02_07', 2025-07-09, 'P', 1 -> '20250709_CNT0207_P1'."""
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
    that moment used to leave the scores file new and its manifest old."""
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_WAIT_SECONDS)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _last_successful_imports() -> Dict[str, str]:
    """{cohort_id: when its last successful (not dry-run) sheet import finished}."""
    from .sheet_sync import _read_ledger
    out = {}
    for e in _read_ledger():
        if e.get("success") and not e.get("dry_run") and e.get("cohort_id"):
            when = e.get("finished") or e.get("started")
            if when and when > out.get(e["cohort_id"], ""):
                out[e["cohort_id"]] = when
    return out


def _read_rows(db_file, q: str) -> list:
    """Run one read of the database and return its rows.

    The read is tried on a read-only connection first. WHY the second try:
    when a writer was stopped part-way through a save, it leaves its unfinished
    save beside the database, and the next connection has to undo it before
    anything can be read. A read-only connection is not allowed to do that and
    fails with "attempt to write a readonly database" -- which left the scores
    file a day old although the import itself had landed. So on exactly that
    refusal the read is repeated on an ordinary connection, which undoes the
    unfinished save (as any writer's next open would) and is then told to
    accept reads only (PRAGMA query_only), so this code still cannot change a
    row. Any other failure is raised as it is.
    """
    con = sqlite3.connect("file:%s?mode=ro" % Path(db_file).as_posix(), uri=True, timeout=600)
    try:
        return con.execute(q).fetchall()
    except sqlite3.OperationalError as e:
        if "readonly" not in str(e).lower():
            raise
    finally:
        con.close()
    con = sqlite3.connect(str(db_file), timeout=600)
    try:
        con.execute("PRAGMA query_only = ON")
        return con.execute(q).fetchall()
    finally:
        con.close()


def _sheets_seen(previous, now) -> Optional[list]:
    """The previous list with this import's cohorts added (see sheets_seen).
    None while no import has ever named any, as before."""
    if now is None:
        return previous
    return sorted(set(previous or []) | set(now))


def write(db_file: Optional[Path] = None, dest: Optional[Path] = None, log=print,
          full_run: bool = False, sheets_seen: Optional[list] = None) -> dict:
    """Write the scores file and its manifest. Reads the database under the
    cross-task mutex (a short read). ``full_run``: this follows an import of
    every sheet in the folder (sets full_import_at; otherwise the previous
    value is kept). ``sheets_seen``: the cohorts this import found in the
    configured sheets folder; they are ADDED to the previous list, never
    replace it (see sheets_seen in the module docstring). None adds nothing.
    Returns the manifest."""
    from .config import require
    from .task_mutex import hold
    db_file = Path(db_file) if db_file else require("db_path")
    dest = Path(dest) if dest else out_dir()
    dest.mkdir(parents=True, exist_ok=True)
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
    previous = {}
    try:
        previous = json.loads((dest / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
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
