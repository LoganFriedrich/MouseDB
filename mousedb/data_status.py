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

It reads the analysis SNAPSHOT (parquet), two queue folders and two manifest
files -- never connectome.db -- so it is safe to call from a GUI at any time.

    mousedb-data-status [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List

from . import DEFAULT_EXPORT_PATH

from .config import require, snapshot_dir as _snapshot_dir
from .import_analyses import MANIFEST_NAME as ANALYSES_MANIFEST_NAME

SNAPSHOT_DIR = _snapshot_dir()  # None until configured (mousedb config --set snapshot_dir ...)


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


def _queue_videos(name: str) -> List[str]:
    d = _pipeline_root() / "Processing" / "Review" / name
    try:
        return [p.name for p in d.iterdir() if p.is_dir()]
    except OSError:
        return []


def _cohort_of_video(video_id: str) -> str:
    """20250624_CNT0115_P2 -> CNT_01 ; 20220811_ASPA1011_P3 -> ASPA_10"""
    try:
        tok = video_id.split("_")[1]
        letters = "".join(ch for ch in tok if ch.isalpha())
        digits = "".join(ch for ch in tok if ch.isdigit())
        return "%s_%s" % (letters, digits[:2])
    except Exception:
        return "?"


def status(snapshot_dir: Path = SNAPSHOT_DIR) -> dict:
    snapshot_dir = snapshot_dir or require("snapshot_dir")
    import pandas as pd

    out: Dict = {"snapshot_dir": str(snapshot_dir), "cohorts": [], "problems": [],
                 "exports": None, "analyses": []}
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
    try:
        import datetime as _dt
        snap_time = _dt.datetime.fromtimestamp(
            (snapshot_dir / "reach_data.parquet").stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        pass
    out["snapshot_time"] = snap_time

    rd["cohort"] = rd["subject_id"].str.rsplit("_", n=1).str[0]
    ps["cohort"] = ps["subject_id"].str.rsplit("_", n=1).str[0]
    triage = _queue_videos("triage")
    deep = _queue_videos("flagged_for_review")
    q_by_cohort: Dict[str, Dict[str, int]] = {}
    for name, vids in (("triage", triage), ("deep_review", deep)):
        for v in vids:
            q_by_cohort.setdefault(_cohort_of_video(v), {"triage": 0, "deep_review": 0})[name] += 1

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
    return out


def _print(st: dict) -> None:
    print("Snapshot: %s  (%s)" % (st.get("snapshot_dir"), st.get("snapshot_time")))
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
