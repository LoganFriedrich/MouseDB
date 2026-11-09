"""Where is my data? -- written to a file on a schedule, for a person to open.

WHY THIS EXISTS
---------------
The Where Is My Data tab answers the question when someone opens the GUI and
asks. A lab meeting needs the answer ready BEFORE anyone asks: a page that is
already written, dated, and can be copied into meeting notes. This module
writes that page from what mousedb already has -- it measures nothing new:

    * the pipeline census (<mousedb_root>/logs/pipeline_census.json, retaken
      every night by the snapshot refresh job): every session that should
      exist, and which stage each one is at, counted per project and per
      cohort;
    * the review-time estimate the census already makes;
    * each processing machine's own status report
      (<mousereach pipeline>/Processing/.node_status/*.json), read only;
    * data_status.status(): the per-cohort database table and every problem
      it knows about (stale snapshot, failing overnight jobs, ...).

A section whose source cannot be read says so in the page; it never shows
zero instead (a missing number must not look like "nothing there").

WHAT IS WRITTEN
---------------
In the folder given (the scheduled job uses
<mousedb_root>/reports/Where is my data/):

    Where is my data YYYY-MM-DD.html   one page per run, kept
    Where is my data - LATEST.html     the same page, overwritten each run

HTML because it opens in any browser by double-clicking and its tables paste
into Word or a Google Doc as tables. Both files are written to a temporary
name first and renamed, so nobody opening the folder sees half a page.

    mousedb-weekly-report [--out <folder>]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# The stages a session can be at, in the order a reader thinks of them, with
# the words used in the page. Keys are the census's own stage names.
STAGES = [
    ("done", "Done (filed)"),
    ("triage", "Waiting for triage"),
    ("deep_review", "Waiting for deep review"),
    ("crop_dlc", "Being posed"),
    ("not_started_pillar", "Not started -- pillar trays"),
    ("not_started_easy_flat", "Not started -- Easy/Flat trays"),
    ("quarantined", "Set aside (quarantined)"),
]

FOLDER_NAME = "Where is my data"


def default_out() -> Path:
    from .config import require
    return Path(require("mousedb_root")) / "reports" / FOLDER_NAME


def _stage(info: dict) -> str:
    """The page's stage for one census session. WHY "done" is the census's
    finished-and-not-held state: a finished video still sitting in a review
    queue (sent back after filing) is counted where it is now, in the queue."""
    el = info.get("element") or "unanalyzed"
    if el == "mousereach" and info.get("finished"):
        return "done"
    if el in ("unanalyzed", "mousereach"):
        return "not_started_easy_flat" if info.get("tray") in ("E", "F") else "not_started_pillar"
    return el


def _cohort(sid: str, project: str) -> str:
    from .data_status import _cohort_of_video
    c = _cohort_of_video(sid)
    return c if c != "?" else "%s (name not readable)" % project


def census_tables(census: dict):
    """({project: Counter(stage)}, {cohort: Counter(stage)}) from the census."""
    by_project: Dict[str, Counter] = defaultdict(Counter)
    by_cohort: Dict[str, Counter] = defaultdict(Counter)
    for sid, info in (census.get("sessions") or {}).items():
        project = info.get("project") or "?"
        st = _stage(info)
        by_project[project][st] += 1
        if project != "ASPA":      # ASPA is listed as one project, not per cohort
            by_cohort[_cohort(sid, project)][st] += 1
    return by_project, by_cohort


# A recording file in the intake folder is named
#   <date>_<animal>,<animal>,..._<tray>.<ext>
# and holds one session per animal: <date>_<animal>_<tray>.
_COLLAGE_NAME = re.compile(r"^(\d{8})_(.+)_([A-Za-z]+\d+)\.[A-Za-z0-9]+$")


def _queued_sessions(intake: Path) -> set:
    """Every session whose recording is sitting in the intake folder, i.e.
    queued for a cutting machine to split into one video per animal."""
    out = set()
    for name in os.listdir(intake):
        m = _COLLAGE_NAME.match(name)
        if m:
            for animal in m.group(2).split(","):
                out.add("%s_%s_%s" % (m.group(1), animal.strip(), m.group(3)))
    return out


def _priority_lists():
    """({project: sessions on that project's priority list}, the work order's
    own note), from the work order MouseReach's cutting machines follow
    (priority_order.json in the MouseReach pipeline folder). ({}, "") when
    there is no such file. The note is the lab's own words for its order, so
    the page quotes it instead of this code describing any lab's order."""
    from .data_status import _pipeline_root
    root = Path(_pipeline_root())
    try:
        order = json.loads((root / "priority_order.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, ""
    out: Dict[str, set] = {}
    for project, files in (order.get("session_lists") or {}).items():
        names = set()
        for f in files:
            try:
                names |= {ln.strip() for ln in (root / f).read_text(encoding="utf-8").splitlines()
                          if ln.strip() and not ln.startswith("#")}
            except OSError:
                pass
        out[project] = names
    return out, str(order.get("notes") or "")


def why_not_started(census: dict, problems: List[str]) -> str:
    """Why pillar-tray sessions have not started, as an HTML section.

    WHY ONLY PILLAR TRAYS: Easy/Flat trays are cut and posed only when a
    machine has nothing else to do, so their being behind is the plan, not a
    question. WHY THESE GROUPS: a session that has not started is either
    queued (its recording is in the intake folder, waiting its turn at a
    cutting machine), or it is not queued -- and then the page has to say
    why, because nothing will ever pick it up on its own."""
    sessions = {sid: info for sid, info in (census.get("sessions") or {}).items()
                if _stage(info) == "not_started_pillar"}
    if not sessions:
        return "<h2>Why videos have not started</h2><p>Every pillar-tray session has started.</p>"
    intake = Path(((census.get("roots") or {}).get("collages_intake") or {}).get("path") or "")
    try:
        queued = _queued_sessions(intake)
    except OSError as e:
        problems.append("intake folder unreadable (%s): %s" % (intake, e))
        return ("<h2>Why videos have not started</h2><p><b>The intake folder could not be read</b>, "
                "so the reasons are unknown this week.</p>")
    lists, order_note = _priority_lists()
    groups: Dict[str, List[str]] = defaultdict(list)
    for sid, info in sorted(sessions.items()):
        project = info.get("project") or "?"
        if sid in queued:
            if project in lists:
                key = ("Queued for cutting (%s, on the priority list)" if sid in lists[project]
                       else "Queued for cutting (%s, after the priority list)") % project
            else:
                key = "Queued for cutting (%s)" % project
        elif len(project) <= 1 or project in ("other", "?"):
            key = ("Not queued: the recording's name does not read as animal IDs, "
                   "so nothing will pick it up")
        else:
            key = "Not queued: no recording for it in the intake folder"
        groups[key].append(sid)
    rows = []
    for key in sorted(groups, key=lambda k: (not k.startswith("Queued"), k)):
        names = groups[key]
        ex = "" if key.startswith("Queued") else "<br><span class=sub>%s%s</span>" % (
            html.escape(", ".join(names[:12])), " ..." if len(names) > 12 else "")
        rows.append("<tr><td>%s%s</td><td>%s</td></tr>" % (html.escape(key), ex, format(len(names), ",")))
    return ("<h2>Why videos have not started (pillar trays)</h2>"
            "<p class=sub>Easy/Flat trays are left until machines are idle, on purpose, "
            "so they are not listed here.%s</p>"
            "<table><tr><th>Reason</th><th>Sessions</th></tr>%s</table>"
            % (" Queued recordings are cut in the work order: %s" % html.escape(order_note)
               if order_note else "", "".join(rows)))


def _table(title_col: str, rows: Dict[str, Counter], order: Optional[List[str]] = None) -> str:
    keys = order or sorted(rows)
    head = "".join("<th>%s</th>" % html.escape(k) for k in keys)
    out = ["<table><tr><th>%s</th>%s<th>Total</th></tr>" % (html.escape(title_col), head)]
    totals = Counter()
    for stage, words in STAGES:
        cells = []
        for k in keys:
            n = rows[k][stage]
            totals[k] += n
            cells.append("<td>%s</td>" % (format(n, ",") if n else ""))
        n_all = sum(rows[k][stage] for k in keys)
        bold = stage == "done"
        label = "<b>%s</b>" % words if bold else words
        out.append("<tr><td>%s</td>%s<td>%s</td></tr>" % (label, "".join(cells), format(n_all, ",")))
    out.append("<tr class=tot><td>All sessions</td>%s<td>%s</td></tr>" % (
        "".join("<td>%s</td>" % format(totals[k], ",") for k in keys),
        format(sum(totals.values()), ",")))
    out.append("</table>")
    return "\n".join(out)


def _project_order(by_project: Dict[str, Counter]) -> List[str]:
    """Named projects first, largest first; the short unreadable-name groups last."""
    named = [p for p in by_project if len(p) > 1 and p not in ("other", "?")]
    rest = [p for p in by_project if p not in named]
    return sorted(named, key=lambda p: -sum(by_project[p].values())) + sorted(rest)


def _machines(problems: List[str]) -> str:
    from .data_status import _pipeline_root
    d = _pipeline_root() / "Processing" / ".node_status"
    try:
        files = sorted(d.glob("*.json"))
    except OSError as e:
        problems.append("machine status folder not readable (%s): %s" % (d, e))
        return "<p>Machine status could not be read.</p>"
    rows = []
    for f in files:
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            rows.append("<tr><td>%s</td><td colspan=4>unreadable: %s</td></tr>"
                        % (html.escape(f.stem), html.escape(str(e))))
            continue
        doing = s.get("doing") or {}
        last = s.get("last_done") or {}
        rows.append("<tr><td>%s</td><td>%s%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            html.escape(str(s.get("hostname", f.stem))),
            html.escape(str(s.get("state", "?"))),
            (" -- " + html.escape(s["reason"])) if s.get("reason") else "",
            html.escape("%s %s" % (doing.get("type", ""), doing.get("id", ""))) if doing else "",
            s.get("done_last_hour", ""),
            html.escape(str(s.get("at", "")))))
    return ("<table><tr><th>Machine</th><th>State</th><th>Doing</th>"
            "<th>Finished in the last hour</th><th>Reported at</th></tr>%s</table>"
            % "".join(rows))


def _cohort_db_table(st: dict) -> str:
    rows = []
    for c in st.get("cohorts") or []:
        q = c.get("videos_in_review") or {}
        rows.append("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
            html.escape(str(c.get("cohort_id"))),
            c.get("animals") if c.get("animals") is not None else "?",
            format(c.get("sessions_scored") or 0, ","), format(c.get("videos_in_db") or 0, ","),
            format(c.get("reaches_in_db") or 0, ","),
            html.escape(str((c.get("sheet") or {}).get("state") or "-"))))
    return ("<table><tr><th>Cohort</th><th>Animals</th><th>Hand-scored sessions</th>"
            "<th>Videos in database</th><th>Reaches in database</th><th>Tracking sheet</th></tr>"
            "%s</table>" % "".join(rows))


CSS = """body{font-family:Segoe UI,Arial,sans-serif;margin:24px;max-width:1100px;color:#222}
h1{margin-bottom:0}.sub{color:#666;margin-top:4px}
table{border-collapse:collapse;margin:8px 0 20px}td,th{border:1px solid #bbb;padding:4px 10px;text-align:right}
td:first-child,th:first-child{text-align:left}th{background:#eee}tr.tot td{font-weight:bold;border-top:2px solid #444}
li{margin:4px 0}"""


def build(now: Optional[datetime] = None) -> str:
    """The page, as HTML text. Every section reads its own source and says so
    when it cannot; one unreadable source never stops the page."""
    from .pipeline_census import load_cached as read_cache
    now = now or datetime.now()
    problems: List[str] = []
    parts = ["<!doctype html><meta charset=utf-8><title>Where is my data %s</title><style>%s</style>"
             % (now.strftime("%Y-%m-%d"), CSS),
             "<h1>Where is my data</h1><p class=sub>Written %s by mousedb's weekly report.</p>"
             % now.strftime("%A %d %B %Y, %H:%M")]

    try:
        census = read_cache()
    except Exception as e:
        census = None
        problems.append("pipeline census unreadable: %s: %s" % (type(e).__name__, e))
    if census:
        by_project, by_cohort = census_tables(census)
        t = census.get("totals") or {}
        done = sum(c["done"] for c in by_project.values())
        expected = t.get("expected") or sum(sum(c.values()) for c in by_project.values())
        parts.append("<h2>Videos: %s of %s sessions done (%d%%)</h2>"
                     % (format(done, ","), format(expected, ","), round(100 * done / expected) if expected else 0))
        parts.append("<p class=sub>From the pipeline census taken %s. A session is one animal on one "
                     "tray on one day.</p>" % html.escape(str(census.get("generated_at"))))
        parts.append(_table("By project", by_project, _project_order(by_project)))
        parts.append("<h3>Per cohort (ASPA above as one project)</h3>")
        parts.append(_table("Cohort", by_cohort, sorted(k for k in by_cohort if "(" not in k)))
        try:
            parts.append(why_not_started(census, problems))
        except Exception as e:
            problems.append("why-not-started section: %s: %s" % (type(e).__name__, e))
        held = census.get("held_for_hand_scores")
        if held is not None:
            parts.append("<p>Analysed and waiting for their hand scores from the sheets: <b>%s</b></p>" % held)
        eta = census.get("eta") or {}
        if eta:
            parts.append("<p>At the pace of the last %s days: machines finish their backlog of %s "
                         "around <b>%s</b> (%s a day); people finish the review backlog of %s around "
                         "<b>%s</b> (%s reviews a day). An estimate from recent pace, not a promise.</p>"
                         % (eta.get("window_days"), format(eta.get("machine_backlog") or 0, ","),
                            eta.get("machine_date"), eta.get("finished_per_day"),
                            format(eta.get("human_backlog") or 0, ","), eta.get("human_date"),
                            eta.get("reviews_per_day")))
    else:
        parts.append("<h2>Videos</h2><p><b>The pipeline census could not be read</b>, so the video "
                     "counts are unknown this week (not zero).</p>")

    parts.append("<h2>Machines right now</h2>")
    try:
        parts.append(_machines(problems))
    except Exception as e:
        problems.append("machine status: %s: %s" % (type(e).__name__, e))

    try:
        from .data_status import status
        st = status()
        parts.append("<h2>In the database</h2><p class=sub>Snapshot taken %s (%s old).</p>"
                     % (html.escape(str(st.get("snapshot_time"))), html.escape(str(st.get("snapshot_age")))))
        parts.append(_cohort_db_table(st))
        ex = st.get("exports") or {}
        parts.append("<p>Current exports: %s, written %s.</p>" % (
            html.escape(str(ex.get("folder"))), html.escape(str(ex.get("generated_at")))))
        problems += list(st.get("problems") or []) + list(ex.get("problems") or [])
    except Exception as e:
        problems.append("database view unreadable: %s: %s" % (type(e).__name__, e))

    parts.append("<h2>Problems mousedb knows about</h2>")
    parts.append("<ul>%s</ul>" % "".join("<li>%s</li>" % html.escape(p) for p in problems)
                 if problems else "<p>None.</p>")
    return "\n".join(parts)


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write(out: Optional[Path] = None, now: Optional[datetime] = None) -> Path:
    now = now or datetime.now()
    out = Path(out) if out else default_out()
    out.mkdir(parents=True, exist_ok=True)
    page = build(now)
    dated = out / ("%s %s.html" % (FOLDER_NAME, now.strftime("%Y-%m-%d")))
    _write(dated, page)
    _write(out / ("%s - LATEST.html" % FOLDER_NAME), page)
    return dated


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=None,
                    help="folder to write into (default <mousedb_root>/reports/Where is my data)")
    args = ap.parse_args(argv)
    try:
        p = write(args.out)
    except Exception as e:
        print("[FAIL] weekly report not written: %s: %s" % (type(e).__name__, e))
        return 1
    print("[OK] weekly report written: %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
