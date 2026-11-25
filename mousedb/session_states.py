"""How far along the videos are, counted against the TRACKING SHEETS.

WHY THE SHEETS ARE THE DENOMINATOR
----------------------------------
"How close are we to done?" would otherwise be answered from the pipeline alone:
the pipeline census counts every session the pipeline's folders account for
and how many of them are finished. That answer cannot see a recording that
never reached the pipeline. A cohort's sessions were scored by hand in its
sheet, their recordings stayed on the recording PC, and for fifteen months
every progress figure read as if those sessions did not exist -- because, to
the pipeline, they did not.

The operator scores every tray at the bench, in the sheet, the same day. So
the sheet is the one list of every session that was really recorded, and it
is the denominator here: done % = sessions FILED / sessions SCORED IN THE
SHEET. A recording nobody sent to the server is then work still to do, in
red, instead of nothing.

THE STATES
----------
Every hand-scored session (one animal, one day, one tray; a row of the
hand-scores file, see hand_scores_file) is in exactly ONE of these, decided in
this order:

  recording_failed  the recording itself failed (empty, a fraction of a
                    second long, unreadable) and was set aside; there will
                    never be a video (recording_issues.py).
  held_for_review   waiting for a person: in the triage or deep-review queue
                    (a quality check that could not run holds a video in
                    deep review, so a quality hold is counted here), set
                    aside as unprocessable (quarantined), or held by the
                    pipeline outside the queues for something only a person
                    can fix -- a LABEL MISMATCH (its results match another
                    tray's hand scores, so the sheet or the name has the
                    trays swapped) or hand scores the pipeline could not
                    find or compare. The holds outside the queues come from
                    the pipeline's own attention lists
                    (<pipeline>/attention/*.json, read only).
  filed             finished and in the final output folder (Analyzed).
  in_processing     on the server and moving: waiting to be cut, being
                    posed, in the analysis steps, or analyzed and waiting
                    for the sheet's next import to be compared with its
                    hand scores (that hold releases itself).
  not_on_server     scored in the sheet, but no video of it is anywhere in
                    the reaching pipeline.

WHY THAT ORDER: a session can be visible in two places at once (a filed
video sent back to a queue keeps its old results in Analyzed until the new
run replaces them). It is counted where a person has to act: a failed
recording is final, and a review queue outranks "filed". A hold outside the
queues (label mismatch, hand scores) counts only for a video that is not
filed: it holds a run, and a filed video's results stay filed.

"Not on the server yet" is split three ways, and only the first is red:
  * red: more than the grace period old (missing_video_grace_days, default
    3 days, counted to when the census was taken -- the same rule as
    `mousedb missing-videos`), in a cohort with a live tracking sheet, and
    not acknowledged. These are exactly the sessions `mousedb
    missing-videos` lists as missing.
  * recent: inside the grace period. A recording is named and sent some
    days after it is made; this is normal.
  * acknowledged: a person looked and the recording is gone for good
    (`mousedb missing-videos --acknowledge`). It stays in the denominator --
    the session was recorded and will never be filed -- so done % for such
    a cohort stays below 100 and says why.
A session the pipeline holds under ANOTHER tray of the same animal and day,
one the sheet does not have, is still "not on the server" (its own name is
nowhere), and is counted as "maybe under another name": one of the two
names is probably wrong.

THE REVERSE: videos the pipeline holds with NO sheet row are listed
separately (unmatched_videos), per cohort, with their pipeline stage. They
are never dropped and never counted in done %. A name the cohort cannot be
read from is listed under "?".

WHAT IS READ (all read only): the hand-scores file and its manifest, the
pipeline census (<mousedb_root>/logs/pipeline_census.json), the
acknowledgements file, and the reaching pipeline's attention lists. Nothing
is written.

    mousedb sheet-progress [--cohort <cohort>] [--list] [--json]
"""
from __future__ import annotations

import json
from collections import Counter, OrderedDict, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

# The five states, in the order a reader thinks of them.
STATES = ("filed", "in_processing", "held_for_review", "recording_failed", "not_on_server")
STATE_WORDS = OrderedDict([
    ("filed", "filed"),
    ("in_processing", "in processing"),
    ("held_for_review", "held for review"),
    ("recording_failed", "recording failed"),
    ("not_on_server", "NOT ON THE SERVER YET"),
])

# Census elements, mapped to a state. A census element not listed here is
# counted as in_processing and named in the session's detail.
HELD_ELEMENTS = ("triage", "deep_review", "quarantined")
PROCESSING_ELEMENTS = ("unanalyzed", "crop_dlc", "mousereach")
FILED_ELEMENTS = ("analyzed", "session_only")
FAILED_ELEMENT = "recording_issue"

# The reaching pipeline's attention-list kinds that hold a video outside the
# review queues until a PERSON acts. "hand_scores_waiting" is not one: it
# waits for the sheet's next import and releases itself.
HOLD_KINDS = {"label_mismatch": "label_mismatch",
              "hand_scores_missing": "hand_scores_not_found",
              "hand_score_check_failed": "hand_score_check_failed"}

# Pillar trays: the trays whose sessions carry pellet outcomes. Easy and flat
# trays are scored too but are processed last on purpose, so the pillar
# figure is given on its own as well.
PILLAR = "P"


def _compact(day: str) -> str:
    return str(day)[:10].replace("-", "")


def _pct(n: int, d: int) -> Optional[float]:
    return round(100.0 * n / d, 1) if d else None


def cohort_of_video(key: str) -> str:
    from .data_status import _cohort_of_video
    return _cohort_of_video(key)


# --- the pure core ------------------------------------------------------------

def classify(hand: Dict[str, dict], census_sessions: Dict[str, dict], view_taken: datetime,
             live_cohorts: Optional[Set[str]] = None, grace: int = 3,
             acks: Optional[Dict[str, dict]] = None,
             failed: Optional[Dict[str, dict]] = None,
             holds: Optional[Dict[str, str]] = None,
             recording_issues_reported: bool = True,
             caveats: Optional[List[str]] = None) -> dict:
    """The per-session state table and its per-cohort counts.

    ``hand``            {video key: {cohort_id, subject_id, session_date, tray}}
                        (missing_videos.read_hand_sessions).
    ``census_sessions`` the census's {session: {element, finished, ...}}.
    ``view_taken``      when the census was taken (the grace period counts to it).
    ``live_cohorts``    cohorts with a live tracking sheet; None = all.
    ``acks``            acknowledged gaps (missing_videos.read_acks).
    ``failed``          {session: {collage, reason}} (recording_issues.from_census).
    ``holds``           {session: hold kind} from the pipeline's attention lists.
    No file is read here."""
    acks = acks or {}
    failed = failed or {}
    holds = holds or {}
    caveats = list(caveats or [])
    if not recording_issues_reported:
        caveats.append(
            "the pipeline census does not report failed recordings (the pipeline that "
            "took it predates that report), so no session can be counted as 'recording "
            "failed' from it: a failed recording's sessions are counted where the census "
            "puts them, or as not on the server")

    # Trays of each animal and day the pipeline holds, for the "maybe under
    # another name" note.
    trays_held = defaultdict(set)
    for name in census_sessions:
        parts = name.split("_")
        if len(parts) == 3:
            trays_held[(parts[0], parts[1])].add(parts[2])
    trays_scored = defaultdict(set)
    for key in hand:
        parts = key.split("_")
        if len(parts) == 3:
            trays_scored[(parts[0], parts[1])].add(parts[2])

    rows: List[dict] = []
    for key, s in hand.items():
        info = census_sessions.get(key)
        el = (info or {}).get("element") if isinstance(info, dict) else None
        finished = bool((info or {}).get("finished")) if isinstance(info, dict) else False
        row = dict(s, video_key=key, element=el or "", detail="", red=False,
                   recent=False, acknowledged=False, other_name="")
        if key in failed or el == FAILED_ELEMENT:
            row["state"] = "recording_failed"
            row["detail"] = (failed.get(key) or {}).get("reason") or "recording failed"
        elif el in HELD_ELEMENTS:
            row["state"] = "held_for_review"
            row["detail"] = el
        elif el in FILED_ELEMENTS or (el == "mousereach" and finished):
            row["state"] = "filed"
        elif info is not None and key in holds:
            row["state"] = "held_for_review"
            row["detail"] = holds[key]
        elif info is not None:
            row["state"] = "in_processing"
            row["detail"] = el if el in PROCESSING_ELEMENTS else ("unknown stage: %s" % el)
        else:
            row["state"] = "not_on_server"
            parts = key.split("_")
            if len(parts) == 3:
                other = sorted(trays_held.get((parts[0], parts[1]), set())
                               - trays_scored.get((parts[0], parts[1]), set()))
                row["other_name"] = ",".join(other)
            try:
                age = (view_taken.date() - date.fromisoformat(str(s.get("session_date"))[:10])).days
            except ValueError:
                age = None
            row["recent"] = age is not None and age <= grace
            row["acknowledged"] = key in acks
            live = live_cohorts is None or s.get("cohort_id") in live_cohorts
            # An unreadable date is never red: `mousedb missing-videos` skips
            # it too, and the two must agree session for session.
            row["red"] = (live and age is not None and not row["recent"]
                          and not row["acknowledged"])
        rows.append(row)
    rows.sort(key=lambda r: (r.get("cohort_id") or "", r.get("session_date") or "",
                             r.get("tray") or "", r.get("subject_id") or ""))

    unmatched = []
    for name, info in census_sessions.items():
        if name in hand:
            continue
        info = info if isinstance(info, dict) else {}
        unmatched.append({"video_key": name, "cohort_id": cohort_of_video(name),
                          "element": info.get("element") or "",
                          "finished": bool(info.get("finished")),
                          "tray": info.get("tray") or ""})
    unmatched.sort(key=lambda u: (u["cohort_id"], u["video_key"]))

    return {"view_taken": view_taken.isoformat(timespec="minutes"), "grace_days": grace,
            "sessions": rows, "unmatched_videos": unmatched,
            "by_cohort": summarise(rows, unmatched, live_cohorts),
            "totals": totals(rows, unmatched), "caveats": caveats}


def _blank() -> dict:
    d = {"sheet_scored": 0, "pillar_scored": 0, "pillar_filed": 0,
         "not_on_server_red": 0, "not_on_server_recent": 0,
         "not_on_server_acknowledged": 0, "not_on_server_other_name": 0,
         "held": {}, "videos_without_sheet_row": 0}
    for s in STATES:
        d[s] = 0
    return d


def _finish(d: dict) -> dict:
    d["done_pct"] = _pct(d["filed"], d["sheet_scored"])
    d["pillar_done_pct"] = _pct(d["pillar_filed"], d["pillar_scored"])
    return d


def summarise(rows: Iterable[dict], unmatched: Iterable[dict] = (),
              live_cohorts: Optional[Set[str]] = None) -> Dict[str, dict]:
    """{cohort: counts} -- one count per state, done % (filed / sheet-scored,
    None for a cohort with no hand-scored session), the pillar-tray figures,
    the three kinds of "not on the server yet", the held sessions by reason,
    and the number of videos with no sheet row."""
    out: Dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(r.get("cohort_id") or "?", _blank())
        _add(d, r)
    for u in unmatched:
        out.setdefault(u["cohort_id"], _blank())["videos_without_sheet_row"] += 1
    for cid, d in out.items():
        d["live_sheet"] = live_cohorts is None or cid in live_cohorts
        _finish(d)
    return OrderedDict(sorted(out.items()))


def _add(d: dict, r: dict) -> None:
    d["sheet_scored"] += 1
    d[r["state"]] += 1
    if str(r.get("tray") or "").startswith(PILLAR):
        d["pillar_scored"] += 1
        if r["state"] == "filed":
            d["pillar_filed"] += 1
    if r["state"] == "held_for_review":
        d["held"][r["detail"]] = d["held"].get(r["detail"], 0) + 1
    if r["state"] == "not_on_server":
        d["not_on_server_red"] += int(bool(r["red"]))
        d["not_on_server_recent"] += int(bool(r["recent"]))
        d["not_on_server_acknowledged"] += int(bool(r["acknowledged"]))
        d["not_on_server_other_name"] += int(bool(r["other_name"]))


def totals(rows: Iterable[dict], unmatched: Iterable[dict] = ()) -> dict:
    d = _blank()
    for r in rows:
        _add(d, r)
    d["videos_without_sheet_row"] = len(list(unmatched))
    return _finish(d)


def not_on_server_recordings(result: dict, only_red: bool = True) -> List[dict]:
    """The not-on-the-server sessions grouped into the recordings they would
    have come from: one per cohort, day and tray, with its animals."""
    groups: "OrderedDict[tuple, dict]" = OrderedDict()
    for r in result["sessions"]:
        if r["state"] != "not_on_server" or (only_red and not r["red"]):
            continue
        k = (r.get("cohort_id"), r.get("session_date"), r.get("tray"))
        g = groups.setdefault(k, {"cohort_id": k[0], "session_date": k[1], "tray": k[2],
                                  "animals": [], "recent": r["recent"],
                                  "acknowledged": r["acknowledged"]})
        g["animals"].append(r["video_key"].split("_")[1] if "_" in r["video_key"]
                            else r["video_key"])
    return list(groups.values())


# --- words, shared by the tab, the terminal and the weekly page -------------

def headline(result: dict) -> str:
    """One line: filed of sheet-scored, and every other state as a count."""
    t = result["totals"]
    return ("%s of %s hand-scored sessions filed (%s) | %s in processing | %s held for "
            "review | %s recording failed | %s NOT ON THE SERVER YET (%s overdue, %s recent, "
            "%s acknowledged as lost)" % (
                format(t["filed"], ","), format(t["sheet_scored"], ","), pct_text(t["done_pct"]),
                format(t["in_processing"], ","), format(t["held_for_review"], ","),
                format(t["recording_failed"], ","), format(t["not_on_server"], ","),
                format(t["not_on_server_red"], ","), format(t["not_on_server_recent"], ","),
                format(t["not_on_server_acknowledged"], ",")))


def pct_text(p: Optional[float]) -> str:
    return "-" if p is None else "%.1f%%" % p


def cohort_lines(result: dict, cohorts: Optional[Iterable[str]] = None) -> List[str]:
    """A plain-text table, one line per cohort (ASCII only)."""
    want = set(cohorts) if cohorts else None
    lines = ["%-10s %7s %7s %7s %6s %6s %6s %6s %7s %7s" % (
        "cohort", "scored", "filed", "done%", "proc", "held", "failed", "NOTSRV",
        "overdue", "nosheet")]
    for cid, d in result["by_cohort"].items():
        if want is not None and cid not in want:
            continue
        lines.append("%-10s %7d %7d %7s %6d %6d %6d %6d %7d %7d" % (
            cid, d["sheet_scored"], d["filed"], pct_text(d["done_pct"]), d["in_processing"],
            d["held_for_review"], d["recording_failed"], d["not_on_server"],
            d["not_on_server_red"], d["videos_without_sheet_row"]))
    return lines


# --- reading the inputs ---------------------------------------------------------

def read_holds(pipeline_root: Optional[Path]) -> (Dict[str, str], List[str]):
    """({session: hold kind}, problems) from the reaching pipeline's attention
    lists. An unreadable list is a problem said out loud, never "no holds"."""
    holds: Dict[str, str] = {}
    problems: List[str] = []
    if not pipeline_root:
        return holds, ["the reaching pipeline's folder is not configured, so its holds "
                       "outside the review queues (label mismatch, hand scores not found) "
                       "could not be read; such sessions are counted as in processing"]
    d = Path(pipeline_root) / "attention"
    if not d.is_dir():
        return holds, problems
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            problems.append("the pipeline's attention list %s could not be read (%s); "
                            "its holds are counted as in processing" % (f.name, e))
            continue
        for i in (data.get("items") or []) if isinstance(data, dict) else []:
            if isinstance(i, dict) and i.get("video_id") and i.get("kind") in HOLD_KINDS:
                holds[str(i["video_id"])] = HOLD_KINDS[i["kind"]]
    return holds, problems


def from_files(mousedb_root: Optional[Path] = None, census: Optional[dict] = None,
               pipeline_root: Optional[Path] = None, grace: Optional[int] = None,
               read_pipeline_holds: bool = True) -> dict:
    """classify() from the configured files (all read only). Raises
    missing_videos.CannotCheck when the hand-scores file or the census is
    missing or unreadable."""
    from . import missing_videos as mv
    from .recording_issues import from_census
    root = Path(mousedb_root) if mousedb_root else mv._root()
    sp = mv.scores_path(root)
    hand = mv.read_hand_sessions(sp)
    _known, taken, census = mv.read_census_full(census)
    live = mv.live_sheet_cohorts(sp)
    if live is None:
        from .cohort_sheets import available_cohorts
        live = set(available_cohorts())
    grace = mv.grace_days() if grace is None else grace
    caveats: List[str] = []
    holds: Dict[str, str] = {}
    if read_pipeline_holds:
        if pipeline_root is None:
            from .config import mousereach_pipeline_root
            pipeline_root = mousereach_pipeline_root()
        holds, caveats = read_holds(pipeline_root)
    return classify(hand, census.get("sessions") or {}, taken, live_cohorts=live, grace=grace,
                    acks=mv.read_acks(mv.ack_path(root)), failed=from_census(census),
                    holds=holds,
                    recording_issues_reported="recording_issues" in census,
                    caveats=caveats)


# --- command line ---------------------------------------------------------------

DESCRIPTION = ("How far along the videos are, counted against the tracking sheets: "
               "every hand-scored session is filed, in processing, held for review, "
               "recording failed, or not on the server yet. Read only.")


def build_parser(ap=None):
    """The options of ``mousedb sheet-progress``; ``mousedb``'s command line adds
    them to its subcommand through this function (the same fix, and the same
    reason, as missing_videos.build_parser)."""
    import argparse
    ap = ap or argparse.ArgumentParser(prog="mousedb sheet-progress", description=DESCRIPTION)
    ap.add_argument("--cohort", action="append", help="Only this cohort (repeatable)")
    ap.add_argument("--list", action="store_true",
                    help="Also list every overdue not-on-the-server recording, by cohort")
    ap.add_argument("--json", action="store_true", help="Print the whole result as JSON")
    return ap


def main(argv=None) -> int:
    return run_args(build_parser().parse_args(argv))


def run_args(a) -> int:
    """The command's work, from parsed options (see build_parser)."""
    from .missing_videos import CannotCheck
    try:
        res = from_files()
    except CannotCheck as e:
        print("[FAIL] %s" % e)
        return 1
    if a.json:
        print(json.dumps(res, indent=1, default=str))
        return 0
    print("Progress against the tracking sheets (pipeline census of %s, grace %d days):"
          % (res["view_taken"], res["grace_days"]))
    print("  " + headline(res))
    for line in cohort_lines(res, a.cohort):
        print("  " + line)
    print("  (NOTSRV = not on the server yet; overdue = the red part of it; nosheet = "
          "videos in the pipeline with no sheet row)")
    if a.list:
        for g in not_on_server_recordings(res):
            if a.cohort and g["cohort_id"] not in a.cohort:
                continue
            print("  [!] %s %s tray %s: %s" % (g["cohort_id"], g["session_date"], g["tray"],
                                              ", ".join(g["animals"])))
    for c in res["caveats"]:
        print("  [!] %s" % c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
