"""Where is my data? -- one answer per cohort, as JSON for the GUI.

WHY THIS EXISTS
---------------
The end of the lab's workflow is someone sitting down and asking where the
clean data is, discovering a review queue nobody mentioned, and concluding
the whole thing failed. Nothing answered the question. This does, per
cohort, in one table:

    animals          in the database (from the sheet, or created from a video)
    sheet            which file, edited when, imported when, verdict
    sessions_scored  hand-scored sessions (distinct animal-days in manual scores)
    videos_in_db     videos with reach rows in the database
    videos_in_review triage / deep-review bundles waiting for a person
    outcomes         how many segments' outcomes are algo-only vs human-reviewed
    exports          the current CSVs, when written, and whether ODC-complete
    analyses         one line per tissue analysis mirrored from MouseBrain's
                     registry (exports/ANALYSES_MANIFEST.json, written by
                     mousedb import-analyses): current / stale / invalidated
    session_lists    one line per list of sessions a study needs: how many
                     are analysed, in review, waiting to be posed or cut,
                     not imported (MouseReach's coverage report, below)

It reads the analysis SNAPSHOT (parquet), two queue folders and two manifest
files -- never connectome.db -- so it is safe to call from a GUI at any time.

    mousedb-data-status [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

from . import DEFAULT_EXPORT_PATH

from .config import require, snapshot_dir as _snapshot_dir
from .import_analyses import MANIFEST_NAME as ANALYSES_MANIFEST_NAME

SNAPSHOT_DIR = _snapshot_dir()  # None until configured (mousedb config --set snapshot_dir ...)

# When the snapshot counts as out of date. The job that rewrites it is normally
# scheduled daily, so a day and a half allows for one late or slow run without
# crying wolf, while still catching a job that has stopped working. Raise it if
# the refresh is scheduled less often than daily.
STALE_SNAPSHOT_HOURS = 36


def describe_age(hours: Optional[float]) -> str:
    """How old, in words -- "4 hours", "3 days" -- for a person, not a machine.

    A timestamp alone makes the reader do arithmetic before they can tell
    whether what they are looking at is current, and they mostly will not.
    """
    if hours is None:
        return "unknown"
    if hours < 1:
        return "under an hour"
    if hours < 48:
        n = int(round(hours))
        return "%d hour%s" % (n, "" if n == 1 else "s")
    n = int(round(hours / 24))
    return "%d day%s" % (n, "" if n == 1 else "s")


def read_analyses_manifest(path: Path) -> List[dict]:
    """The per-analysis rows of exports/ANALYSES_MANIFEST.json; [] when no
    import has run yet. Raises when the file is there but unreadable."""
    if not path.is_file():
        return []
    m = json.loads(path.read_text(encoding="utf-8"))
    rows = m.get("analyses", []) if isinstance(m, dict) else m
    return [r for r in rows if isinstance(r, dict)]


def analysis_lines(rows: List[dict]) -> List[str]:
    """One line per analysis, identical in the terminal and the GUI."""
    def _n(v):
        return "?" if v is None else v
    return ["Analysis %s: %s current, %s stale vs approved, %s invalidated, imported %s" % (
                r.get("analysis_name"), _n(r.get("current")), _n(r.get("stale_vs_approved")),
                _n(r.get("invalidated")), r.get("imported_at") or "?")
            for r in rows]


def _pipeline_root() -> Path:
    return require("mousereach_pipeline_root")


# Lists of sessions a study needs, and how far each has got. MouseReach's
# coverage report (mousereach-session-coverage --sessions-file <list>) writes
# <list>.coverage.json beside each list; the lab keeps its lists in this
# folder under the pipeline root. WHY: a study asks for particular sessions,
# spread over many cohorts and recordings, and the per-cohort table cannot say
# how far those have got -- "where is my data" for a study was unanswerable.
SESSION_LISTS_FOLDER = "priority_lists"
COVERAGE_SUFFIX = ".coverage.json"


def read_session_coverage(folder: Path) -> List[dict]:
    """One row per ``*.coverage.json`` in ``folder``: list name, when written,
    session count and counts by stage. [] when the folder is absent. A file
    that cannot be read is a row with an ``error``, never silently dropped."""
    rows: List[dict] = []
    if not folder.is_dir():
        return rows
    for f in sorted(folder.glob("*" + COVERAGE_SUFFIX)):
        name = f.name[: -len(COVERAGE_SUFFIX)]
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            rows.append({"list": name, "written_at": d.get("written_at"),
                         "sessions": d.get("sessions"),
                         "by_stage": d.get("by_stage") or {}})
        except Exception as e:
            rows.append({"list": name, "error": str(e)})
    return rows


def session_list_lines(rows: List[dict]) -> List[str]:
    """One line per session list, identical in the terminal and the GUI."""
    out = []
    for r in rows:
        if r.get("error"):
            out.append("Sessions list %s: report unreadable (%s)" % (r["list"], r["error"]))
            continue
        stages = ", ".join("%s %s" % (n, s) for s, n in (r.get("by_stage") or {}).items())
        out.append("Sessions list %s: %s sessions -- %s (report written %s)"
                   % (r["list"], r.get("sessions"), stages or "none", r.get("written_at") or "?"))
    return out


# MouseReach's review-queue folders under <pipeline root>/Processing/Review/.
# mousedb reads them directly (it does not import mousereach), so the names are
# restated here and must follow MouseReach's layout. The deep-review queue was
# renamed from "flagged_for_review" to "deep_review" when the pipeline moved to
# "the folder is the state" (2026-09-14); reading the old name after that
# showed every cohort's deep-review count as a silent 0.
TRIAGE_QUEUE = "triage"
DEEP_REVIEW_QUEUE = "deep_review"

# A real bundle is named by its video, which starts with an 8-digit date.
# Queues also hold scratch and retired folders (_Problematic, .return_claims,
# ...) that are not videos waiting for a person.
_BUNDLE_NAME = re.compile(r"^\d{8}_")


def _queue_videos(name: str) -> Optional[List[str]]:
    """Video stems held in one review queue, or None when the queue folder
    cannot be read. None is not zero: a missing or renamed folder must show
    up as a problem, never as an empty queue."""
    d = _pipeline_root() / "Processing" / "Review" / name
    try:
        return [p.name for p in d.iterdir() if p.is_dir() and _BUNDLE_NAME.match(p.name)]
    except OSError:
        return None


def review_queue_counts(problems: List[str]) -> Dict[str, Dict[str, int]]:
    """{cohort: {"triage": n, "deep_review": n}} from the two queue folders.
    An unreadable queue folder is appended to ``problems`` by path, so the
    person sees "count unknown" instead of a reassuring zero."""
    by_cohort: Dict[str, Dict[str, int]] = {}
    for key, folder in (("triage", TRIAGE_QUEUE), ("deep_review", DEEP_REVIEW_QUEUE)):
        vids = _queue_videos(folder)
        if vids is None:
            problems.append("review queue folder not readable: %s -- its count is unknown, not zero"
                            % (_pipeline_root() / "Processing" / "Review" / folder))
            continue
        for v in vids:
            by_cohort.setdefault(_cohort_of_video(v), {"triage": 0, "deep_review": 0})[key] += 1
    return by_cohort


_ANIMAL_IN_NAME = re.compile(r"([A-Za-z]+)(\d+)")


def _cohort_of_video(video_id: str) -> str:
    """Which cohort a video belongs to, from its name.

    20250624_PROJ0115_P2        -> PROJ_01   (one animal)
    20220811_PROJ0501PROJ0502_P1 -> PROJ_05  (a collage of several)
    anything unreadable          -> "?"

    WHY the collage case needs saying: a multi-animal recording names every
    animal in it, one after another. The old version pulled ALL the letters out
    of the name and ALL the digits out of the name and glued them together, so
    a collage of seven animals from one cohort reported itself as a cohort
    called "PROJPROJPROJPROJPROJPROJPROJ_05". That is not only ugly in a
    listing: those recordings were then counted under a cohort that does not
    exist instead of under the real one, so every per-cohort workload figure
    was short by however many of its collages had not been split up yet.

    Each animal is matched separately and they must AGREE. A recording whose
    animals come from different cohorts has no single cohort, and this returns
    "?" rather than picking the first one -- the same choice the rest of this
    census makes, for the same reason: refusing is recoverable, and a plausible
    wrong number is not.
    """
    parts = video_id.split("_")
    if len(parts) < 2:
        return "?"
    found = {"%s_%s" % (m.group(1), m.group(2)[:2])
             for m in _ANIMAL_IN_NAME.finditer(parts[1])}
    if len(found) != 1:
        return "?"
    return found.pop()


# How long a health report may go unwritten before its silence is the finding.
# The check runs daily, so a report older than two days means the check itself
# stopped.
STALE_HEALTH_HOURS = 48


def health_file() -> Path:
    """Where the nightly health check leaves its report."""
    # require() stops with the exact command to run when the root is unset,
    # which is how every other path in this module already behaves.
    return Path(require("mousedb_root")) / "logs" / "health.json"


def _failing_jobs_sentence(report: dict) -> str:
    """One sentence naming each failing job ONCE, with how many runs failed.

    The health report lists one line per failing run, so three bad nights of
    the same job are three lines. A person needs "import brains failed its last
    3 runs", not the job's log filename repeated three times.
    """
    # A failing job shows up twice in the report: once per bad run in its own
    # log ("snapshot_refresh.log"), and once as its task's exit code ("(task)
    # MouseDB analysis snapshot refresh"). Runs are counted from the logs, and
    # a task line is kept only when no log already accounts for that job -- the
    # task name contains the log's name ("...snapshot refresh"), so containment
    # is the match.
    #
    # The health check's own entries are dropped outright: it is the observer,
    # and an observer that reports itself as failing never stops doing so.
    counts: Dict[str, int] = {}
    task_names: List[str] = []
    for line in report.get("detail") or []:
        line = str(line).strip()
        if "|" not in line or line.startswith("[FAIL]"):
            continue
        name = line.split("|")[0].strip()
        if "health check" in name.replace("_", " ").lower():
            continue
        if name.startswith("(task)"):
            task_names.append(name[len("(task)"):].strip())
            continue
        if name.endswith(".log"):
            name = name[:-4]
        name = name.replace("_", " ")
        counts[name] = counts.get(name, 0) + 1
    for task in task_names:
        if not any(known.lower() in task.lower() for known in counts):
            counts[task] = counts.get(task, 0) + 1
    if not counts:
        return ("an overnight job is failing (checked %s); see "
                "Databases\\logs\\tasks\\health_check.log" % report.get("checked_age"))
    parts = ["%s (%d run%s)" % (n, c, "" if c == 1 else "s")
             for n, c in sorted(counts.items())]
    return ("overnight jobs failing, checked %s: %s. Every figure below stops "
            "moving until they succeed again."
            % (report.get("checked_age"), "; ".join(parts)))


def scheduled_job_health(problems: List[str]) -> Optional[dict]:
    """What the nightly health check found, and a problem line when it found one.

    Separate from the snapshot's age on purpose -- see the module note at the
    call site. The age says whether the numbers are current; this says whether
    the jobs that produce them are working, which a hand-run refresh cannot
    fake.
    """
    import datetime as _dt
    import json

    path = health_file()
    if not path.exists():
        problems.append(
            "nothing has checked whether the overnight jobs succeeded: no health "
            "report at %s. The 'MouseDB nightly health check' task writes it." % path)
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        problems.append("the overnight job health report could not be read: %s" % e)
        return None

    checked = report.get("checked_at")
    age_hours = None
    if checked:
        try:
            age_hours = (_dt.datetime.now()
                         - _dt.datetime.fromisoformat(checked)).total_seconds() / 3600.0
        except ValueError:
            age_hours = None
    report["checked_age"] = describe_age(age_hours) if age_hours is not None else "unknown"

    if age_hours is not None and age_hours > STALE_HEALTH_HOURS:
        problems.append(
            "the check that watches the overnight jobs has not run for %s, so "
            "nothing is currently watching them" % report["checked_age"])

    if report.get("failing"):
        problems.append(_failing_jobs_sentence(report))

    # Any task whose own exit result says it was stopped at its time limit.
    # The pre-deploy check whitelists that result as "stopped on purpose", which
    # is how a job being killed every night stayed invisible for weeks.
    jobs = report.get("jobs") or []
    if isinstance(jobs, dict):
        # PowerShell writes a one-element list as a bare object.
        jobs = [jobs]
    killed = sorted(j.get("task", "?") for j in jobs
                    if j.get("last_result") == 267014
                    and "health check" not in str(j.get("task", "")).lower())
    if killed:
        problems.append(
            "stopped at its time limit before finishing, on its last run: %s"
            % ", ".join(killed))
    return report


def status(snapshot_dir: Path = SNAPSHOT_DIR) -> dict:
    snapshot_dir = snapshot_dir or require("snapshot_dir")
    import pandas as pd

    out: Dict = {"snapshot_dir": str(snapshot_dir), "cohorts": [], "problems": [],
                 "exports": None, "analyses": [], "session_lists": []}
    try:
        rd = pd.read_parquet(snapshot_dir / "reach_data.parquet",
                             columns=["subject_id", "video_name", "outcome_source", "segment_num"])
        ps = pd.read_parquet(snapshot_dir / "pellet_scores.parquet",
                             columns=["subject_id", "session_date"])
    except Exception as e:
        out["problems"].append("snapshot unreadable: %s" % e)
        return out
    try:
        subjects = pd.read_parquet(snapshot_dir / "subjects.parquet")
        cohorts = pd.read_parquet(snapshot_dir / "cohorts.parquet")
    except Exception:
        subjects = cohorts = None
        out["problems"].append("subjects/cohorts not in snapshot yet (refresh once more)")

    snap_time = None
    snap_age_hours = None
    try:
        import datetime as _dt
        taken = _dt.datetime.fromtimestamp(
            (snapshot_dir / "reach_data.parquet").stat().st_mtime)
        snap_time = taken.isoformat(timespec="seconds")
        snap_age_hours = (_dt.datetime.now() - taken).total_seconds() / 3600.0
    except OSError:
        pass
    out["snapshot_time"] = snap_time
    out["snapshot_age_hours"] = snap_age_hours
    out["snapshot_age"] = describe_age(snap_age_hours)
    # JUDGE the age, do not just report it. Every number below this line is as
    # of the snapshot, and the snapshot is rewritten by a scheduled job. When
    # that job fails -- most often because another program had the database
    # locked -- the numbers simply stop moving, and a timestamp on screen does
    # not tell anybody that: it asks them to know today's date, know how often
    # the job is meant to run, and do the subtraction. In the one case this was
    # written for, three days passed with nobody noticing.
    if snap_age_hours is None:
        out["problems"].append(
            "the snapshot's age is unknown, so nothing below can be dated")
    elif snap_age_hours > STALE_SNAPSHOT_HOURS:
        out["problems"].append(
            "every figure below is %s old: the snapshot was taken %s and the job "
            "that rewrites it has not succeeded since. Newly analysed work will "
            "not appear until it does." % (out["snapshot_age"], snap_time))

    out["jobs"] = scheduled_job_health(out["problems"])

    rd["cohort"] = rd["subject_id"].str.rsplit("_", n=1).str[0]
    ps["cohort"] = ps["subject_id"].str.rsplit("_", n=1).str[0]
    q_by_cohort = review_queue_counts(out["problems"])

    try:
        from .sheet_sync import status as sheet_status
        sheets = {c["cohort_id"]: c for c in sheet_status().get("cohorts", [])}
    except Exception as e:
        sheets = {}
        out["problems"].append("sheet status unavailable: %s" % e)

    # The pipeline census (MouseReach's workload view), cached by the tab's
    # "Refresh pipeline view" button, joined here with the snapshot's video
    # list so "analyzed" means finished AND in the database AND on disk.
    pc = None
    pc_by_cohort: Dict[str, dict] = {}
    try:
        from .pipeline_census import load_cached, join_with_db
        census = load_cached()
        if census:
            in_db = set(rd["video_name"].dropna().unique())
            # Union with the import ledger: a zero-reach video (post-injury,
            # the animal never reached) imports with no reach rows and must
            # still count as landed -- rows alone are blind to it.
            from .pipeline_census import _ledger_video_names
            in_db |= _ledger_video_names(
                require("mousedb_root") / "logs" / "reach_imports.json")
            pc = join_with_db(census, in_db)
            pc_by_cohort = pc.get("by_cohort") or {}
    except Exception as e:
        out["problems"].append("pipeline census unreadable: %s" % e)
    out["pipeline"] = pc

    all_cohorts = (set(rd["cohort"]) | set(ps["cohort"]) | set(q_by_cohort)
                   | set(sheets) | set(pc_by_cohort))
    if cohorts is not None:
        all_cohorts |= set(cohorts["cohort_id"])
    for cid in sorted(c for c in all_cohorts if c and c != "?"):
        r = rd[rd["cohort"] == cid]
        p = ps[ps["cohort"] == cid]
        seg = r.drop_duplicates(["video_name", "segment_num"])
        src = seg["outcome_source"].fillna("algo").value_counts().to_dict()
        n_subjects = None
        if subjects is not None:
            n_subjects = int((subjects["cohort_id"] == cid).sum())
        auto = None
        if subjects is not None and "notes" in subjects.columns:
            auto = int(((subjects["cohort_id"] == cid)
                        & subjects["notes"].fillna("").str.startswith("auto-created")).sum())
        sh = sheets.get(cid) or {}
        out["cohorts"].append({
            "cohort_id": cid,
            "animals": n_subjects,
            "animals_created_from_video_only": auto,
            "sheet": {"file": sh.get("sheet"), "state": sh.get("state"),
                      "why": sh.get("why"), "edited": sh.get("sheet_edited"),
                      "last_import": (sh.get("last_import") or {}).get("finished")},
            "sessions_scored": int(p.drop_duplicates(["subject_id", "session_date"]).shape[0]),
            "pellets_scored": int(len(p)),
            "videos_in_db": int(r["video_name"].nunique()),
            "reaches_in_db": int(len(r)),
            "videos_in_review": q_by_cohort.get(cid, {"triage": 0, "deep_review": 0}),
            "segments_by_outcome_source": {k: int(v) for k, v in src.items()},
            "pipeline": pc_by_cohort.get(cid),
        })

    manifest = require("mousedb_root") / "exports" / "current" / "MANIFEST.json"
    if manifest.is_file():
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
            out["exports"] = {"folder": str(manifest.parent), "generated_at": m.get("generated_at"),
                              "complete": m.get("complete"), "problems": m.get("problems", []),
                              "files": {k: v.get("rows") for k, v in m.get("files", {}).items()}}
        except Exception as e:
            out["problems"].append("exports manifest unreadable: %s" % e)
    else:
        out["exports"] = {"folder": str(manifest.parent), "generated_at": None,
                          "complete": False, "problems": ["no current exports yet"], "files": {}}

    # Tissue analyses mirrored from MouseBrain's registry (mousedb import-analyses)
    try:
        out["analyses"] = read_analyses_manifest(
            require("mousedb_root") / "exports" / ANALYSES_MANIFEST_NAME)
    except Exception as e:
        out["problems"].append("analyses manifest unreadable: %s" % e)

    # How far each study's list of sessions has got (MouseReach coverage report)
    try:
        out["session_lists"] = read_session_coverage(_pipeline_root() / SESSION_LISTS_FOLDER)
    except Exception as e:
        out["problems"].append("session lists unreadable: %s" % e)
    return out


def _print(st: dict) -> None:
    print("Snapshot: %s  (taken %s -- %s old)" % (
        st.get("snapshot_dir"), st.get("snapshot_time"), st.get("snapshot_age")))
    print("%-9s %7s %8s %8s %9s %7s %6s  %s" % (
        "cohort", "animals", "sessions", "videos", "reaches", "triage", "deep", "sheet"))
    for c in st["cohorts"]:
        q = c["videos_in_review"]
        print("%-9s %7s %8d %8d %9d %7d %6d  %s" % (
            c["cohort_id"], c["animals"] if c["animals"] is not None else "?",
            c["sessions_scored"], c["videos_in_db"], c["reaches_in_db"],
            q["triage"], q["deep_review"], c["sheet"].get("state") or "-"))
    pc = st.get("pipeline")
    if pc:
        t = pc.get("totals") or {}
        print("Pipeline census (as of %s): %s sessions should exist; "
              "%s finished on disk; analyzed (done+DB+files): %s"
              % (pc.get("generated_at"), t.get("expected"),
                 t.get("finished_files"),
                 "?" if t.get("analyzed") is None else t.get("analyzed")))
        inv = pc.get("invariant")
        if inv is not None:
            print("  finished-but-not-in-database (MUST be 0): %d" % inv["count"])
            for sid in list(inv["sessions"])[:5]:
                print("    %s -- %s" % (sid, inv["sessions"][sid]))
        for cv in pc.get("caveats") or []:
            print("  [!] %s" % cv)
    else:
        print("Pipeline census: none taken yet -- press 'Refresh pipeline view' "
              "in the GUI, or run mousereach-census")
    ex = st.get("exports") or {}
    print("Exports: %s  written %s  ODC-complete=%s" % (ex.get("folder"), ex.get("generated_at"), ex.get("complete")))
    for line in analysis_lines(st.get("analyses") or []):
        print(line)
    for line in session_list_lines(st.get("session_lists") or []):
        print(line)
    for p in st.get("problems", []) + list(ex.get("problems", [])):
        print("  [!] %s" % p)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--snapshot-dir", type=Path, default=SNAPSHOT_DIR)
    args = ap.parse_args(argv)
    st = status(args.snapshot_dir)
    print(json.dumps(st, indent=1, default=str)) if args.json else _print(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
