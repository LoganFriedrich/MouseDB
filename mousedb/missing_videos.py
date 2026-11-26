"""Sessions scored by hand in a tracking sheet whose video the reaching
pipeline has never seen.

WHY
---
The operator scores every tray at the bench, in the sheet, the same day. So a
hand-scored session is proof that a recording was made. If that recording
never reaches the pipeline, nothing else notices: the pipeline only knows the
videos it was given, and the database only knows what the pipeline produced.
One cohort's two days of pillar sessions were scored by hand, but their
recordings never left the recording PC, and that went unseen for fifteen
months. This check is the other side of the hand-score comparison: that one
asks "does this video agree with its hand scores?", this one asks "does this
hand score have a video at all?"

WHAT IS COMPARED
----------------
* The hand scores: mousedb's own hand-scores file
  (<mousedb_root>/exports/Behavior/Hand_Scores/hand_pellet_scores.csv, written from
  the database after every sheet import; see hand_scores_file). One session
  there = one animal, one day, one tray, named the way the pipeline names its
  single-animal video (<YYYYMMDD>_<animal>_<tray>).
* Everything the pipeline knows: the pipeline census
  (<mousedb_root>/logs/pipeline_census.json; see pipeline_census). It lists
  every single-animal session the pipeline's folders account for -- every
  multi-animal recording waiting to be cut (expanded into its animals), every
  video being posed or analyzed, every one in a review queue or quarantine,
  every filed one in the Analyzed tree. It is retaken every night by the
  snapshot job (a read-only folder scan) and by the tab's "Refresh pipeline
  view" button. WHY the census and not a new scan: it already walks every
  place a video can be, and a second walker would be a second answer that
  could disagree with the first.
A hand-scored session the census does not hold is MISSING.

WHICH SESSIONS ARE JUDGED
-------------------------
* Only cohorts with a live tracking sheet (the cohorts the hand-scores file
  records as seen in the configured sheets folder). Frozen workbooks of
  finished projects are imported another way and are not judged: nobody is
  going to record those animals again, and their videos were collected long
  before this pipeline existed.
* Only sessions more than the grace period old (missing_video_grace_days,
  default 3 days) AT THE TIME THE CENSUS WAS TAKEN. WHY: a recording is
  named and sent some time after it is made; three days covers a weekend.
  WHY measured from the census and not from today: a census taken last night
  cannot hold a recording sent this morning, so judging by today would
  accuse every recording sent since.
* Long-finished cohorts ARE judged. WHY: the gap this check exists for
  belonged to a cohort that had long finished; a recording may still sit on
  a recording PC or a backup drive. A gap a person has looked into and
  cannot close (the recording is gone, or was never made) is ACKNOWLEDGED
  once (see below) and from then on is no longer shown in red or written into
  the cohort's FIX THESE file. It stays in the list file, marked
  acknowledged, so nothing is hidden.

WHAT HAPPENS TO A MISSING SESSION
---------------------------------
* The Where Is My Data tab shows one red line per cohort.
* The cohort's "!! <cohort> - FIX THESE.xlsx" file (attention.py) gets one
  line per missing recording (day + tray), naming its animals and saying
  what to do.
* <mousedb_root>/exports/Behavior/Sessions_Without_Video/missing_session_videos.csv lists
  every missing session, one row each, acknowledged ones included. Written
  only by mousedb: after every sheet import, after the nightly census is
  retaken, and by `mousedb missing-videos --write`.
* When the census holds the same animal and day under ANOTHER tray, that is
  said too: one of the two names (the sheet's or the video's) is wrong, and
  the recording is probably there under the other name.

A RECORDING THAT FAILED IS NOT A MISSING VIDEO
----------------------------------------------
When the reaching pipeline set a session's recording aside as a RECORDING
ISSUE (the recording is empty, a fraction of a second long, or unreadable --
see recording_issues.py), the census says so. Such a session is listed with
status "recording_failed", never "missing": there is no video to look for.
It is judged at once (no grace period: the failure is known), whether or not
the trays were scored by hand, and only for cohorts with a live sheet. The
Where Is My Data tab and the cohort's FIX THESE file get one line per failed
recording: "recording failed: <recording>, <reason>". Nothing can be fixed;
the line is there so a person knows, and it is acknowledged once like a gap
(a recording's file name is a selector too).

ACKNOWLEDGING A GAP
-------------------
    mousedb missing-videos --acknowledge <cohort>:<YYYY-MM-DD> --by "<name>" --reason "<why>"
A selector is a cohort (every session of it missing now), a cohort and day
(<cohort>:<YYYY-MM-DD>), or one session (<YYYYMMDD>_<animal>_<tray>). The
sessions it names NOW are written, one by one, with who, when and why, to
<mousedb_root>/logs/missing_videos_acknowledged.json. WHY session by session
and not the selector itself: acknowledging a cohort's old gaps must not
silence a gap that opens in it next week.
"""
from __future__ import annotations

import csv
import json
import os
import tempfile
from collections import OrderedDict, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

GRACE_DAYS_DEFAULT = 3
GRACE_KEY = "missing_video_grace_days"
SOURCE = "missing videos"
KIND = "missing_video"
ACK_NAME = "missing_videos_acknowledged.json"
CSV_NAME = "missing_session_videos.csv"
KIND_FAILED = "recording_failed"
CSV_COLUMNS = ["cohort_id", "subject_id", "session_date", "tray", "video_key",
               "status", "days_after_session", "pipeline_has_instead",
               "expected_recording", "what_to_do", "acknowledged_by",
               "acknowledged_at", "acknowledged_reason", "pipeline_view_of",
               "recording_issue_reason", "recording", "hand_scored"]


class CannotCheck(RuntimeError):
    """An input the check needs is missing or unreadable. Said out loud, never
    read as "nothing is missing"."""


# --- inputs -----------------------------------------------------------------

def grace_days() -> int:
    """The configured grace period in days (missing_video_grace_days), else
    GRACE_DAYS_DEFAULT. A value that is not a whole number of days, or is
    negative, stops the check (CannotCheck) instead of being guessed at."""
    from .config import get
    raw = get(GRACE_KEY)
    if raw is None or str(raw).strip() == "":
        return GRACE_DAYS_DEFAULT
    try:
        n = int(str(raw).strip())
    except ValueError:
        n = -1
    if n < 0:
        raise CannotCheck("the setting %s is %r, which is not a whole number of days "
                          "(0 or more). Set it with: mousedb config --set %s 3"
                          % (GRACE_KEY, raw, GRACE_KEY))
    return n


def _root() -> Path:
    from .config import require
    return require("mousedb_root")


def scores_path(mousedb_root: Optional[Path] = None) -> Path:
    from .hand_scores_file import FILE_NAME
    from .output_folders import hand_scores_dir
    return hand_scores_dir(mousedb_root or _root()) / FILE_NAME


def ack_path(mousedb_root: Optional[Path] = None) -> Path:
    return Path(mousedb_root or _root()) / "logs" / ACK_NAME


def csv_path(mousedb_root: Optional[Path] = None) -> Path:
    from .output_folders import sessions_without_video_dir
    return sessions_without_video_dir(mousedb_root or _root()) / CSV_NAME


def csv_shown() -> str:
    """The list file as messages name it: its path inside <mousedb_root>."""
    from .output_folders import sessions_without_video_dir, shown
    return shown(sessions_without_video_dir, CSV_NAME)


def read_hand_sessions(path: Path) -> Dict[str, dict]:
    """{video key: {cohort_id, subject_id, session_date, tray}} -- one entry per
    hand-scored session in the hand-scores file."""
    path = Path(path)
    if not path.is_file():
        raise CannotCheck("the hand-scores file %s does not exist yet (it is written "
                          "after every sheet import, or by: mousedb hand-scores)" % path)
    out: Dict[str, dict] = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                k = r.get("video_key")
                if k and k not in out:
                    out[k] = {"cohort_id": r.get("cohort_id") or "",
                              "subject_id": r.get("subject_id") or "",
                              "session_date": (r.get("session_date") or "")[:10],
                              "tray": r.get("tray") or ""}
    except (OSError, ValueError, csv.Error) as e:
        raise CannotCheck("the hand-scores file %s could not be read (%s)" % (path, e))
    return out


def live_sheet_cohorts(scores_file: Path) -> Optional[Set[str]]:
    """The cohorts with a live tracking sheet: the hand-scores file's manifest
    "sheets_seen" (every cohort ever seen in the configured sheets folder; none
    is ever taken off -- see hand_scores_file). None when the manifest has no
    such list."""
    from .hand_scores_file import MANIFEST_NAME
    m = Path(scores_file).parent / MANIFEST_NAME
    try:
        seen = json.loads(m.read_text(encoding="utf-8")).get("sheets_seen")
    except (OSError, ValueError, AttributeError):
        return None
    return set(seen) if isinstance(seen, list) else None


def read_census(census: Optional[dict] = None):
    """(set of session names the pipeline knows, when that view was taken)."""
    known, taken, _census = read_census_full(census)
    return known, taken


def read_census_full(census: Optional[dict] = None):
    """(set of session names the pipeline knows, when that view was taken,
    the census itself)."""
    if census is None:
        from .pipeline_census import cache_path, load_cached
        try:
            census = load_cached()
        except (OSError, ValueError) as e:
            raise CannotCheck("the pipeline view (%s) could not be read (%s)"
                              % (cache_path(), e))
        if census is None:
            raise CannotCheck("no pipeline view has been taken yet (press 'Refresh "
                              "pipeline view' on the Where Is My Data tab, or wait for "
                              "the nightly snapshot job)")
    try:
        taken = datetime.fromisoformat(str(census.get("generated_at")))
    except (TypeError, ValueError):
        raise CannotCheck("the pipeline view does not say when it was taken")
    sessions = census.get("sessions")
    if not isinstance(sessions, dict):
        raise CannotCheck("the pipeline view holds no session list")
    return set(sessions), taken, census


def read_acks(path: Path) -> Dict[str, dict]:
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        # Unreadable is never read as "nothing acknowledged": that would put
        # every acknowledged gap back in red with no word why. The check
        # stops and says which file to fix.
        raise CannotCheck("the acknowledgements file %s could not be read (%s); fix "
                          "or delete it" % (path, e))
    return data if isinstance(data, dict) else {}


# --- the comparison ---------------------------------------------------------

def _compact(day: str) -> str:
    return str(day)[:10].replace("-", "")


def find_gaps(hand: Dict[str, dict], known: Set[str], view_taken: datetime,
              cohorts: Optional[Set[str]], grace: int,
              acks: Optional[Dict[str, dict]] = None,
              failed: Optional[Dict[str, dict]] = None) -> List[dict]:
    """Every judged hand-scored session the pipeline does not know, and every
    session whose recording failed (``failed``: recording_issues.from_census),
    sorted by cohort, day, tray, animal. Each: the session's fields plus kind
    ('missing' or 'recording_failed'), status ('missing', 'recording_failed'
    or 'acknowledged'), days_after_session (counted to the view),
    pipeline_has_instead (other trays of that animal and day the pipeline
    does hold), the recording issue's reason and recording, and the
    acknowledgement, if any."""
    acks = acks or {}
    failed = failed or {}
    trays_by_animal_day = defaultdict(set)
    for name in known:
        parts = name.split("_")
        if len(parts) == 3:
            trays_by_animal_day[(parts[0], parts[1])].add(parts[2])
    out = []
    for key, info in failed.items():
        s = hand.get(key)
        if s is None:
            from .recording_issues import parse_key
            pk = parse_key(key)
            if not pk["subject_id"] or pk["session_date"] is None:
                continue
            s = {"cohort_id": pk["subject_id"].rsplit("_", 1)[0],
                 "subject_id": pk["subject_id"],
                 "session_date": pk["session_date"].isoformat(),
                 "tray": "%s%s" % (pk["tray_type"], pk["tray_number"])}
        if cohorts is not None and s["cohort_id"] not in cohorts:
            continue
        try:
            day = date.fromisoformat(s["session_date"])
            after = (view_taken.date() - day).days
        except ValueError:
            after = ""
        a = acks.get(key) or {}
        out.append(dict(s, video_key=key, kind=KIND_FAILED,
                        status="acknowledged" if a else KIND_FAILED,
                        days_after_session=after, pipeline_has_instead="",
                        recording_issue_reason=info.get("reason", ""),
                        recording=info.get("collage", ""),
                        hand_scored="yes" if key in hand else "no",
                        acknowledged_by=a.get("by", ""),
                        acknowledged_at=a.get("at", ""),
                        acknowledged_reason=a.get("reason", ""),
                        pipeline_view_of=view_taken.isoformat(timespec="minutes")))
    for key, s in hand.items():
        if cohorts is not None and s["cohort_id"] not in cohorts:
            continue
        if key in known or key in failed:
            continue
        try:
            day = date.fromisoformat(s["session_date"])
        except ValueError:
            continue
        # More than ``grace`` days old when the view was taken.
        if (view_taken.date() - day).days <= grace:
            continue
        parts = key.split("_")
        instead = sorted(trays_by_animal_day.get((parts[0], parts[1]), set())
                         - {parts[-1]}) if len(parts) == 3 else []
        a = acks.get(key) or {}
        out.append(dict(s, video_key=key, kind="missing",
                        status="acknowledged" if a else "missing",
                        recording_issue_reason="", recording="", hand_scored="yes",
                        days_after_session=(view_taken.date() - day).days,
                        pipeline_has_instead=",".join(instead),
                        acknowledged_by=a.get("by", ""),
                        acknowledged_at=a.get("at", ""),
                        acknowledged_reason=a.get("reason", ""),
                        pipeline_view_of=view_taken.isoformat(timespec="minutes")))
    out.sort(key=lambda g: (g["cohort_id"], g["session_date"], g["tray"], g["subject_id"]))
    return out


def recordings(gaps: Iterable[dict], include_acknowledged: bool = False) -> List[dict]:
    """The gaps grouped into the recordings they would have come from: one per
    cohort, day and tray, with its missing animals."""
    groups: "OrderedDict[tuple, dict]" = OrderedDict()
    for g in gaps:
        if g.get("kind", "missing") != "missing":
            continue
        if g["status"] != "missing" and not include_acknowledged:
            continue
        k = (g["cohort_id"], g["session_date"], g["tray"])
        r = groups.setdefault(k, {"cohort_id": k[0], "session_date": k[1], "tray": k[2],
                                  "animals": [], "video_keys": [], "instead": set()})
        r["animals"].append(g["video_key"].split("_")[1])
        r["video_keys"].append(g["video_key"])
        if g.get("pipeline_has_instead"):
            r["instead"].update(g["pipeline_has_instead"].split(","))
    return list(groups.values())


def expected_name(session_date: str, animals: List[str], tray: str) -> str:
    """The name the multi-animal recording carries once a person has named it:
    <YYYYMMDD>_<animal>,<animal>,..._<tray>.mkv. Only the missing animals are
    known here; the recording may hold others too."""
    return "%s_%s_%s.mkv" % (_compact(session_date), ",".join(animals), tray)


def what_to_do(rec: dict, intake: Optional[str] = None) -> str:
    """What a person does about one missing recording, in plain words."""
    day, tray = rec["session_date"], rec["tray"]
    shown = rec["animals"] if len(rec["animals"]) <= 8 else rec["animals"][:8] + ["..."]
    text = ("Check the recording PC's OBS folder for the %s tray %s recording "
            "(animals %s; once named it reads like %s -- raw, it is named by the date "
            "and time it was made, %s ...). If it is there, name it if it is not named "
            "yet and send it to the pipeline's intake folder%s as the day's recordings "
            "are sent; the pipeline takes it from there. If it is nowhere (not on the "
            "recording PC, not on a backup), acknowledge it so it stops being listed: "
            "mousedb missing-videos --acknowledge %s:%s --by <your name> --reason <why>"
            % (day, tray, ", ".join(shown), expected_name(day, rec["animals"][:2] + (
                ["..."] if len(rec["animals"]) > 2 else []), tray), day,
               (" (%s)" % intake) if intake else "", rec["cohort_id"], day))
    if rec.get("instead"):
        text += (". NOTE: the pipeline does hold these animals on %s under tray %s -- "
                 "if that is this session, the tray in the sheet or in the video's name "
                 "is wrong: correct whichever is wrong" % (day, "/".join(sorted(rec["instead"]))))
    return text


def says(rec: dict, intake: Optional[str] = None) -> str:
    n = len(rec["animals"])
    return ("%s %s tray %s: scored by hand for %d animal(s), but no video of %s is "
            "anywhere in the reaching pipeline. %s" % (
                rec["cohort_id"], rec["session_date"], rec["tray"], n,
                "it" if n == 1 else "them", what_to_do(rec, intake)))


def failed_recordings(gaps: Iterable[dict], include_acknowledged: bool = False) -> List[dict]:
    """The failed-recording rows grouped into recordings: one per cohort and
    recording, with its sessions, animals and reason. A recording is listed
    while any of its sessions is not acknowledged (or always, with
    ``include_acknowledged``)."""
    groups: "OrderedDict[tuple, dict]" = OrderedDict()
    for g in gaps:
        if g.get("kind") != KIND_FAILED:
            continue
        k = (g["cohort_id"], g.get("recording") or g["video_key"])
        r = groups.setdefault(k, {"cohort_id": k[0], "recording": g.get("recording") or "",
                                  "reason": g.get("recording_issue_reason") or "",
                                  "session_date": g["session_date"], "tray": g["tray"],
                                  "animals": [], "video_keys": [], "open": 0})
        r["animals"].append(g["video_key"].split("_")[1])
        r["video_keys"].append(g["video_key"])
        if g["status"] == KIND_FAILED:
            r["open"] += 1
    return [r for r in groups.values() if include_acknowledged or r["open"]]


def says_failed(rec: dict) -> str:
    """One FIX THESE / tab line for a failed recording."""
    shown = rec["animals"] if len(rec["animals"]) <= 8 else rec["animals"][:8] + ["..."]
    return ("recording failed: %s, %s. Its sessions (%s %s tray %s; animals %s) will never "
            "have a video to analyze; their hand scores, where the trays were scored, are "
            "kept. Nothing needs fixing. To stop this line: mousedb missing-videos "
            "--acknowledge \"%s\" --by <your name> --reason <why>"
            % (rec["recording"] or "?", rec["reason"] or "no reason recorded",
               rec["cohort_id"], rec["session_date"], rec["tray"], ", ".join(shown),
               rec["recording"] or rec["video_keys"][0]))


def _intake() -> Optional[str]:
    try:
        from .config import mousereach_pipeline_root
        root = mousereach_pipeline_root()
    except Exception:
        return None
    return str(Path(root) / "Unanalyzed" / "Multi-Animal") if root else None


def check(cohort_ids: Optional[Iterable[str]] = None, mousedb_root: Optional[Path] = None,
          census: Optional[dict] = None, grace: Optional[int] = None) -> dict:
    """Run the comparison from the configured files. Returns {gaps, view_taken,
    grace_days, cohorts}. Raises CannotCheck when an input is missing."""
    root = Path(mousedb_root) if mousedb_root else _root()
    sp = scores_path(root)
    hand = read_hand_sessions(sp)
    known, taken, census = read_census_full(census)
    from .recording_issues import from_census
    failed = from_census(census)
    grace = grace_days() if grace is None else grace
    live = live_sheet_cohorts(sp)
    if live is None:
        from .cohort_sheets import available_cohorts
        live = set(available_cohorts())
    cohorts = live if cohort_ids is None else live & set(cohort_ids)
    gaps = find_gaps(hand, known, taken, cohorts, grace, read_acks(ack_path(root)), failed)
    return {"gaps": gaps, "view_taken": taken, "grace_days": grace,
            "cohorts": sorted(cohorts)}


# --- where it is shown --------------------------------------------------------

def items(cohort_ids: Optional[List[str]] = None) -> List[dict]:
    """One attention item per missing recording (unacknowledged), for the
    FIX THESE files and the tab (attention.items). An input that cannot be
    read is one item without a cohort, never silence."""
    try:
        res = check(cohort_ids)
    except CannotCheck as e:
        return [{"cohort_id": None, "source": SOURCE, "kind": KIND,
                 "says": "Could not check for hand-scored sessions without a video: %s." % e}]
    intake = _intake()
    return ([{"cohort_id": r["cohort_id"], "source": SOURCE, "kind": KIND,
              "sessions": len(r["animals"]), "session_date": r["session_date"],
              "says": says(r, intake)} for r in recordings(res["gaps"])]
            + [{"cohort_id": r["cohort_id"], "source": SOURCE, "kind": KIND_FAILED,
                "sessions": len(r["animals"]), "session_date": r["session_date"],
                "says": says_failed(r)} for r in failed_recordings(res["gaps"])])


def fold_for_tab(found: List[dict]) -> List[dict]:
    """The tab's list: every missing-video item of a cohort folded into ONE red
    line for that cohort (the per-recording lines are in its FIX THESE file and
    the list file); every other item as it is."""
    per: "OrderedDict[str, list]" = OrderedDict()
    out = []
    for i in found:
        if i.get("kind") == KIND and i.get("cohort_id"):
            per.setdefault(i["cohort_id"], []).append(i)
        else:
            out.append(i)
    folded = []
    for cid, its in per.items():
        n = sum(int(i.get("sessions") or 0) for i in its)
        days = sorted({i.get("session_date") for i in its if i.get("session_date")})
        span = days[0] if len(days) == 1 else "%s to %s" % (days[0], days[-1])
        folded.append({"cohort_id": cid, "source": SOURCE, "kind": KIND, "says": (
            "%s: %d hand-scored session(s) in %d recording(s) (%s) have no video anywhere "
            "in the reaching pipeline. Each recording, and what to do, is listed in "
            "'!! %s - FIX THESE.xlsx' beside the sheet and in %s"
            % (cid, n, len(its), span, cid, csv_shown()))})
    return folded + out


def write_csv(result: Optional[dict] = None, dest: Optional[Path] = None) -> Path:
    """Write the list file (every judged missing session, acknowledged ones
    included), replacing the previous one in one rename. Returns its path."""
    result = result if result is not None else check()
    dest = Path(dest) if dest else csv_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    intake = _intake()
    todo = {}
    for r in recordings(result["gaps"], include_acknowledged=True):
        for k in r["video_keys"]:
            todo[k] = (expected_name(r["session_date"], r["animals"], r["tray"]),
                       what_to_do(r, intake))
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            w.writeheader()
            for g in result["gaps"]:
                name, do = todo.get(g["video_key"], ("", ""))
                if g.get("kind") == KIND_FAILED:
                    name, do = g.get("recording", ""), ""
                w.writerow(dict(g, expected_recording=name,
                                what_to_do=do if g["status"] == "missing" else ""))
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return dest


def write_list_quietly() -> str:
    """write_csv for the scheduled jobs: never raises; returns what happened
    ('written: <path> (N missing, M acknowledged)' or 'FAILED: ...')."""
    try:
        res = check()
        p = write_csv(res)
        n = sum(1 for g in res["gaps"] if g["status"] == "missing")
        f = sum(1 for g in res["gaps"] if g["status"] == KIND_FAILED)
        return "written: %s (%d missing, %d recording failed, %d acknowledged)" % (
            p, n, f, len(res["gaps"]) - n - f)
    except Exception as e:
        return "FAILED: %s: %s" % (type(e).__name__, e)


# --- acknowledging ------------------------------------------------------------

def select(gaps: List[dict], selector: str) -> List[dict]:
    """The currently open (unacknowledged: missing, or recording failed) gaps a
    selector names: a cohort, <cohort>:<YYYY-MM-DD>, one session's video key,
    or a failed recording's file name."""
    sel = selector.strip()
    missing = [g for g in gaps if g["status"] in ("missing", KIND_FAILED)]
    by_recording = [g for g in missing if g.get("recording") and g["recording"] == sel]
    if by_recording:
        return by_recording
    if ":" in sel:
        cid, day = sel.split(":", 1)
        return [g for g in missing if g["cohort_id"] == cid and g["session_date"] == day]
    by_cohort = [g for g in missing if g["cohort_id"] == sel]
    return by_cohort or [g for g in missing if g["video_key"] == sel]


def acknowledge(selectors: List[str], by: str, reason: str,
                mousedb_root: Optional[Path] = None, result: Optional[dict] = None) -> List[str]:
    """Record the sessions the selectors name now as acknowledged, with who,
    when and why. Returns the session keys added. Refuses an empty name or
    reason, and a selector that names nothing missing."""
    if not (by or "").strip() or not (reason or "").strip():
        raise ValueError("say who is acknowledging (--by) and why (--reason)")
    root = Path(mousedb_root) if mousedb_root else _root()
    result = result if result is not None else check(mousedb_root=root)
    chosen: List[dict] = []
    for s in selectors:
        hit = select(result["gaps"], s)
        if not hit:
            raise ValueError("%r names no session that is missing or whose recording "
                             "failed now" % s)
        chosen += hit
    path = ack_path(root)
    acks = read_acks(path)
    now = datetime.now().isoformat(timespec="seconds")
    added = []
    for g in chosen:
        if g["video_key"] not in acks:
            acks[g["video_key"]] = {"by": by.strip(), "at": now, "reason": reason.strip(),
                                    "cohort_id": g["cohort_id"]}
            added.append(g["video_key"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(acks, f, indent=1, sort_keys=True)
    os.replace(tmp, path)
    return added


# --- command line ---------------------------------------------------------------

DESCRIPTION = ("List hand-scored sessions with no video anywhere in the reaching "
               "pipeline (read-only unless --write or --acknowledge is given).")


def build_parser(ap=None):
    """The options of ``mousedb missing-videos``. ``mousedb``'s own command line
    adds them to its subcommand through this function, and ``python -m
    mousedb.missing_videos`` builds its parser here too. WHY one function: the
    subcommand would otherwise hand everything after its name on as a block
    (argparse.REMAINDER), which refuses a block that starts with an option, so
    every option -- --help, --cohort, --write, and the --acknowledge line the
    FIX THESE files print -- failed with "unrecognized arguments"."""
    import argparse
    ap = ap or argparse.ArgumentParser(prog="mousedb missing-videos", description=DESCRIPTION)
    ap.add_argument("--cohort", action="append", help="Only this cohort (repeatable)")
    ap.add_argument("--grace-days", type=int, default=None,
                    help="Days after a session before its video counts as missing "
                         "(default: the %s setting, else %d)" % (GRACE_KEY, GRACE_DAYS_DEFAULT))
    ap.add_argument("--write", action="store_true",
                    help="Also rewrite %s" % csv_shown())
    ap.add_argument("--acknowledge", nargs="+", metavar="SELECTOR",
                    help="Stop listing these gaps: a cohort, <cohort>:<YYYY-MM-DD>, or a "
                         "session (<YYYYMMDD>_<animal>_<tray>)")
    ap.add_argument("--by", help="Who acknowledges (with --acknowledge)")
    ap.add_argument("--reason", help="Why (with --acknowledge)")
    return ap


def main(argv=None) -> int:
    return run_args(build_parser().parse_args(argv))


def run_args(a) -> int:
    """The command's work, from parsed options (see build_parser)."""
    try:
        res = check(a.cohort, grace=a.grace_days)
        if a.acknowledge:
            added = acknowledge(a.acknowledge, a.by or "", a.reason or "", result=res)
            print("[OK] %d session(s) acknowledged" % len(added))
            res = check(a.cohort, grace=a.grace_days)
    except (CannotCheck, ValueError) as e:
        print("[FAIL] %s" % e)
        return 1
    gaps = res["gaps"]
    print("Hand-scored sessions with no video (pipeline view of %s, grace %d days):"
          % (res["view_taken"].isoformat(timespec="minutes"), res["grace_days"]))
    counts = defaultdict(lambda: [0, 0, 0])
    for g in gaps:
        counts[g["cohort_id"]][{"missing": 0, KIND_FAILED: 2}.get(g["status"], 1)] += 1
    for cid in res["cohorts"]:
        m, k, f = counts.get(cid, [0, 0, 0])
        print("  %-10s %4d missing, %4d recording failed, %4d acknowledged" % (cid, m, f, k))
    for r in recordings(gaps):
        print("  [!] %s %s %s: %s" % (r["cohort_id"], r["session_date"], r["tray"],
                                      ", ".join(r["animals"])))
    for r in failed_recordings(gaps):
        print("  [!] recording failed: %s, %s" % (r["recording"], r["reason"]))
    if a.write:
        print("Written: %s" % write_csv(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
