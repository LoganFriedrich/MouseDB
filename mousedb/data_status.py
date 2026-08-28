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

It reads the analysis SNAPSHOT (parquet) and two queue folders -- never
connectome.db -- so it is safe to call from a GUI at any time.

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

SNAPSHOT_DIR = Path("C:/LAB_ROOT/_analysis_snapshot")


def _pipeline_root() -> Path:
    return Path(os.environ.get("CONNECTOME_ROOT", "Y:/LAB_ROOT")) / "Behavior" / "MouseReach_Pipeline"


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
    import pandas as pd

    out: Dict = {"snapshot_dir": str(snapshot_dir), "cohorts": [], "problems": [],
                 "exports": None}
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

    all_cohorts = set(rd["cohort"]) | set(ps["cohort"]) | set(q_by_cohort) | set(sheets)
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
        })

    manifest = Path(DEFAULT_EXPORT_PATH) / "current" / "MANIFEST.json"
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
    ex = st.get("exports") or {}
    print("Exports: %s  written %s  ODC-complete=%s" % (ex.get("folder"), ex.get("generated_at"), ex.get("complete")))
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
