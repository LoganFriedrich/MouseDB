"""Sessions whose recording FAILED, as the reaching pipeline reports them.

WHAT A RECORDING ISSUE IS
-------------------------
The reaching pipeline checks every multi-animal recording before cutting it
into single-animal videos. A recording that is empty, a fraction of a second
long, unreadable or broken at its start or end is a "recording issue": it is
set aside (in the pipeline's Processing/Review/_Problematic folder, with a
reason file), never cut and never reviewed. Its sessions will never have
kinematics. Their hand pellet scores, if the operator scored the trays, are
still real and are kept.

HOW MOUSEDB LEARNS OF THEM
--------------------------
From the pipeline census only (``pipeline_census.json``, retaken every night
by the snapshot job and by the Where Is My Data tab's refresh button). The
census lists each recording issue -- the recording, the reason, who or what
flagged it and when, and the sessions it was meant to hold -- and gives each
of those sessions the element ``recording_issue``. mousedb never imports the
pipeline's code and never writes into its folders. A census taken by an older
pipeline that does not know recording issues simply lists none.

WHERE IT GOES
-------------
* The database: one row per session in ``session_recording_status`` (status
  ``recording_failed``, the reason, the recording's name, when it was
  flagged), written by ``import_from_census`` right after the nightly census.
  The exports read it: each ``ODC_sessions_<cohort>.csv`` row (one animal, one
  day) carries ``Recording_Status`` and ``Recording_Issue_Reason``. The census
  is taken after that night's exports are written, so a recording flagged
  today shows in the export files after the NEXT night's refresh.
* The Where Is My Data tab and the cohort's FIX THESE file: one line per
  failed recording, "recording failed: <recording>, <reason>"
  (missing_videos.items).
* The list of hand-scored sessions without a video (missing_videos.py): such
  a session is listed with status ``recording_failed`` -- not ``missing``.

ACKNOWLEDGING
-------------
The same command as a missing video, once per session:
    mousedb missing-videos --acknowledge <selector> --by "<name>" --reason "<why>"
After that the recording is no longer shown in red or in the FIX THESE file;
it stays in the list file, and in the database and the exports, because what
happened to the recording is a fact about the data.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Dict, List, Optional

STATUS = "recording_failed"
ELEMENT = "recording_issue"

_KEY = re.compile(r"^(\d{8})_([A-Za-z]+)(\d{2})(\d{2})_([A-Za-z]+)(\d+)$")


def parse_key(video_key: str) -> dict:
    """'20250101_PROJ0102_P2' -> subject PROJ_01_02, 2025-01-01, P, 2.
    Fields that cannot be read are None."""
    m = _KEY.match(str(video_key or ""))
    if not m:
        return {"subject_id": None, "session_date": None, "tray_type": None,
                "tray_number": None}
    d, letters, coh, sub, tt, tn = m.groups()
    try:
        day = date(int(d[:4]), int(d[4:6]), int(d[6:]))
    except ValueError:
        day = None
    return {"subject_id": "%s_%s_%s" % (letters.upper(), coh, sub), "session_date": day,
            "tray_type": tt.upper(), "tray_number": int(tn)}


def from_census(census: Optional[dict]) -> Dict[str, dict]:
    """{session key: {collage, reason, flagged_by, flagged_at}} for every
    session of every recording issue the census lists. {} for no census, or
    for one taken by a pipeline that does not report recording issues."""
    out: Dict[str, dict] = {}
    if not isinstance(census, dict):
        return out
    for r in census.get("recording_issues") or []:
        if not isinstance(r, dict):
            continue
        info = {"collage": r.get("collage") or "", "reason": r.get("reason") or "",
                "flagged_by": r.get("flagged_by") or "", "flagged_at": r.get("flagged_at") or ""}
        for s in r.get("sessions") or []:
            out[str(s)] = info
    # A session the census put under the element without the list (an older
    # list format) still counts, with what the session entry carries.
    for sid, s in (census.get("sessions") or {}).items():
        if isinstance(s, dict) and s.get("element") == ELEMENT and sid not in out:
            ri = s.get("recording_issue") or {}
            out[sid] = {"collage": ri.get("collage") or "", "reason": ri.get("reason") or "",
                        "flagged_by": "", "flagged_at": ri.get("flagged_at") or ""}
    return out


def recordings(census: Optional[dict]) -> List[dict]:
    """The census's recording issues, one per recording."""
    if not isinstance(census, dict):
        return []
    return [r for r in census.get("recording_issues") or [] if isinstance(r, dict)]


def load_census() -> Optional[dict]:
    from .pipeline_census import load_cached
    return load_cached()


def import_from_census(census: Optional[dict] = None, db=None, hold_lock: bool = True) -> dict:
    """Bring ``session_recording_status`` in step with the census: add or
    update a row for every session of every recording issue, and remove the
    rows of sessions no longer listed. Returns {added, updated, removed}.

    A census that does not carry ``recording_issues`` at all (taken by an
    older pipeline) changes nothing: absence of the list is not "none". The
    write holds the database-task lock (task_mutex) like every other write."""
    import contextlib
    if census is None:
        census = load_census()
    if not isinstance(census, dict) or "recording_issues" not in census:
        return {"added": 0, "updated": 0, "removed": 0, "skipped": "census has no recording issues list"}
    wanted = from_census(census)
    from .database import get_db
    from .schema import SessionRecordingStatus
    db = db or get_db()
    SessionRecordingStatus.__table__.create(db.engine, checkfirst=True)
    from .task_mutex import hold
    counts = {"added": 0, "updated": 0, "removed": 0}
    with (hold(waiting_for="another database task") if hold_lock else contextlib.nullcontext()):
        with db.session() as s:
            have = {r.video_key: r for r in s.query(SessionRecordingStatus).all()}
            for key, info in wanted.items():
                parsed = parse_key(key)
                values = dict(parsed, status=STATUS, reason=info["reason"],
                              collage_name=info["collage"], flagged_by=info["flagged_by"],
                              detected_at=info["flagged_at"])
                row = have.get(key)
                if row is None:
                    s.add(SessionRecordingStatus(video_key=key, imported_at=datetime.now(),
                                                 **values))
                    counts["added"] += 1
                elif any(getattr(row, k) != v for k, v in values.items()):
                    for k, v in values.items():
                        setattr(row, k, v)
                    row.imported_at = datetime.now()
                    counts["updated"] += 1
            for key, row in have.items():
                if key not in wanted:
                    s.delete(row)
                    counts["removed"] += 1
    return counts


def import_quietly() -> str:
    """import_from_census for the scheduled job: never raises; returns what
    happened ('imported: N added, M updated, K removed' or 'FAILED: ...')."""
    try:
        c = import_from_census()
        if c.get("skipped"):
            return "nothing imported (%s)" % c["skipped"]
        return "imported: %d added, %d updated, %d removed" % (c["added"], c["updated"],
                                                               c["removed"])
    except Exception as e:
        return "FAILED: %s: %s" % (type(e).__name__, e)


def by_subject_day(session, subject_id: str) -> Dict[date, List[object]]:
    """{session date: [rows]} for one animal, from the database. {} when the
    table does not exist yet (a database no import has run on)."""
    from sqlalchemy import inspect
    from .schema import SessionRecordingStatus
    try:
        if not inspect(session.get_bind()).has_table(SessionRecordingStatus.__tablename__):
            return {}
    except Exception:
        return {}
    out: Dict[date, List[object]] = {}
    for r in (session.query(SessionRecordingStatus)
              .filter_by(subject_id=subject_id).all()):
        if r.session_date is not None:
            out.setdefault(r.session_date, []).append(r)
    return out


def session_columns(rows: List[object]) -> dict:
    """The two session-level export columns for one animal on one day:
    Recording_Status ('recording_failed' when a recording of that day failed,
    else 'no_issue_recorded') and Recording_Issue_Reason (which tray and why,
    empty when none)."""
    if not rows:
        return {"Recording_Status": "no_issue_recorded", "Recording_Issue_Reason": ""}
    parts = []
    for r in sorted(rows, key=lambda r: (r.tray_type or "", r.tray_number or 0)):
        parts.append("tray %s%s: %s (recording %s)" % (r.tray_type or "?", r.tray_number or "?",
                                                      r.reason or "recording failed",
                                                      r.collage_name or "?"))
    return {"Recording_Status": STATUS, "Recording_Issue_Reason": "; ".join(parts)}
