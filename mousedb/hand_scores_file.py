"""Every hand pellet score, written as one file the reaching pipeline reads.

WHY
---
A video is checked against its session's hand scores before the reaching
pipeline files it: filing means done, so a pellet the pipeline and the bench
disagree about goes to a person first, and a video whose hand scores cannot
be found waits. The pipeline is a separate tool that never imports mousedb,
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
hand_pellet_scores.json  when it was written, how many rows, and for each
    cohort the end of its last successful sheet import -- so the pipeline
    can tell "the sheet has not been imported since this video was ready"
    (wait) from "it was imported and the scores are still not there" (a
    defect a person must see).

The file is rewritten after every sheet import (any route: the nightly job,
the GUI, mousedb-sheets) and by `mousedb hand-scores`. It is written to a
temporary name and renamed, so a reader never sees half a file.
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
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


def write(db_file: Optional[Path] = None, dest: Optional[Path] = None, log=print) -> dict:
    """Write the scores file and its manifest. Reads the database under the
    cross-task mutex (a short read). Returns the manifest."""
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
        con = sqlite3.connect("file:%s?mode=ro" % Path(db_file).as_posix(), uri=True, timeout=600)
        try:
            rows = con.execute(q).fetchall()
        finally:
            con.close()
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
    os.replace(tmp, dest / FILE_NAME)
    manifest = {
        "written_at": datetime.now().isoformat(timespec="seconds"),
        "rows": len(out),
        "videos": len({r[0] for r in out}),
        "last_successful_import": _last_successful_imports(),
    }
    tmp = dest / (MANIFEST_NAME + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    os.replace(tmp, dest / MANIFEST_NAME)
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
