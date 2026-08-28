"""Bench-vs-pipeline disagreement scan -- core, no figures.

Finds finished, never-reviewed pellets where the bench (manual tray score)
and the pipeline disagree, and emits them as a worklist a review tool can
route on.

WHY THIS IS A CORE MODULE, NOT A RECIPE
    A tool's repo holds what produces its outputs and operates it. This scan
    produces a work queue -- it is not a figure -- and mousedb is the
    integrator that may call the analysis tools. It was lifted out of the
    manual_scoring_accuracy figure recipes so that removing those accessories
    does not take the scan with them. The logic is unchanged; only the path
    defaults now come from mousedb's own configuration instead of being
    hardcoded, so this module carries no site-specific location.

    Read the snapshot, not the live database. connectome.db sits on a network
    share in rollback-journal mode, so while the watcher is writing a reader
    is blocked outright -- and a read that dies partway through yields
    inconsistent output. The snapshot is a pair of parquet files that can be
    read at any time.

Usage:
    python -m mousedb.bench_scan --out worklist.json
    mousedb-bench-scan --out worklist.json
    mousedb-bench-scan --route            # also send the worklist to MouseReach's queue

ROUTING
    --route hands the worklist to MouseReach's own CLI
    (``mousereach-route-to-queue``), which files each listed segment into the
    triage queue. mousedb is the integrator and may call a tool's CLI; the
    tool never depends on mousedb.

    That command lives in the MouseReach environment, whose location is site
    configuration, so it is NOT hardcoded here. Set ONE of these in
    ``~/.mousedb/config.json``:

        "mousereach_route_cmd": "<full path to mousereach-route-to-queue>"
        "mousereach_env":       "<the env's Scripts/bin directory>"

    With neither set, --route prints the worklist and says routing is not
    configured, and still exits 0 -- an unconfigured integration is not a
    failure of the scan.
"""
from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from mousedb import DEFAULT_DB_PATH
from mousedb.data_status import SNAPSHOT_DIR

DEFAULT_DB = DEFAULT_DB_PATH
DEFAULT_CACHE = SNAPSHOT_DIR
DEFAULT_OUT = Path("never_reviewed_worklist.json")

CURRENT_VERSION = "6.1.0"

HUMAN_SCORE = {0: "missed", 1: "displaced", 2: "retrieved"}
ALGO_CATEGORY = {
    "untouched": "missed",
    "displaced_sa": "displaced",
    "displaced_outside": "displaced",
    "retrieved": "retrieved",
}
NON_COMMITTAL = ("triaged", "uncertain")

# A reviewer resolved this AND it isn't a normal committed outcome -- something
# abnormal happened (classically tail-knockover) that the algorithm can't
# reasonably be expected to detect. Unlike NON_COMMITTAL, the reviewer HAS
# decided; what's missing is a reliable missed-vs-displaced signal. Two cases
# with identical all_miss=True can resolve oppositely -- one genuinely missed
# (pellet never moved), one displaced by the abnormal event itself with no
# causal reach -- and the schema does not currently distinguish them without a
# free-text note. Excluded from the comparison rather than guessed at.
NON_EVALUABLE = ("abnormal_exception",)

JOIN_HUMAN = ["subject_id", "session_date", "tray_type", "tray_number", "pellet_number"]
JOIN_ALGO = ["subject_id", "session_date", "tray_type", "run_number", "segment_num"]


def watcher_running():
    """Is a MouseReach watcher running on this machine?

    Fails SAFE: if this cannot be determined it answers True, because wrongly
    saying "no" costs a half-finished analysis and inconsistent output, while
    wrongly saying "yes" only means reading a snapshot instead.
    """
    try:
        import psutil
    except ImportError:
        return True
    try:
        for proc in psutil.process_iter(["cmdline"]):
            cmd = " ".join(proc.info.get("cmdline") or [])
            if "mousereach-watch" in cmd or "mousereach.watcher" in cmd:
                return True
        return False
    except Exception:
        return True


def load_paired_pellets(db_path=None, segmap_path=None, cache_dir=None):
    """Load reach rows and manual scores, repairing segment_num if asked.

    Args:
        db_path: connectome.db (defaults to mousedb's configured database)
        segmap_path: optional pickle of {(video_name, reach_id): segment_num}, used to
            repair rows written with segment_num = 0 before the 2026-08-20 fix.
        cache_dir: if it holds reach_data.parquet / pellet_scores.parquet, read those
            instead of the database. connectome.db is rollback-journal over SMB, so a
            live watcher blocks readers outright.

    Returns:
        (reach_rows, manual_scores)
    """
    if db_path is None:
        from mousedb.config import require
        db_path = DEFAULT_DB or require("db_path")
    # Prefer the snapshot without being asked. Only fall through to the live
    # database when there is no snapshot AND no watcher is running.
    if cache_dir is None and DEFAULT_CACHE and (DEFAULT_CACHE / "reach_data.parquet").exists():
        cache_dir = DEFAULT_CACHE

    if cache_dir and (Path(cache_dir) / "reach_data.parquet").exists():
        cache = Path(cache_dir)
        rd = pd.read_parquet(cache / "reach_data.parquet")
        ps = pd.read_parquet(cache / "pellet_scores.parquet")
        stamp = datetime.fromtimestamp(
            (cache / "reach_data.parquet").stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print("  reading the snapshot taken %s" % stamp)
        load_paired_pellets.snapshot_taken = stamp
        # The snapshot predates the fix that put a segment number on every reach
        # row, so it ships with the map that repairs them. The two belong
        # together: reading the snapshot without it silently drops most of the
        # current detector's rows, because they are filtered out for having no
        # segment. Callers can still pass their own.
        if segmap_path is None and (cache / "segmap.pkl").exists():
            segmap_path = cache / "segmap.pkl"
    else:
        if watcher_running():
            raise RuntimeError(
                "Refusing to read connectome.db: the MouseReach watcher is running. "
                "The database is on a network share in rollback-journal mode, so a "
                "writer blocks readers outright and a read that dies partway through "
                "leaves inconsistent output behind. Read the snapshot instead "
                "(--cache), or stop the watcher first.")
        load_paired_pellets.snapshot_taken = None
        con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=180)
        try:
            rd = pd.read_sql(
                "SELECT subject_id, session_date, tray_type, run_number, segment_num, "
                "segment_outcome, outcome_detector_version, video_name, reach_id, "
                "outcome_source FROM reach_data", con)
            ps = pd.read_sql(
                "SELECT subject_id, session_date, tray_type, tray_number, "
                "pellet_number, score FROM pellet_scores", con)
        finally:
            con.close()

    if segmap_path:
        with open(segmap_path, "rb") as fh:
            segmap = pickle.load(fh)
        broken = rd.segment_num.eq(0)
        if broken.any():
            rd.loc[broken, "segment_num"] = [
                segmap.get((v, r), 0)
                for v, r in zip(rd.loc[broken, "video_name"], rd.loc[broken, "reach_id"])
            ]

    rd = rd[rd.segment_num > 0].copy()
    ps = ps.copy()
    for frame in (rd, ps):
        frame["session_date"] = frame["session_date"].astype(str).str[:10]
    return rd, ps


def pair_current(rd, ps, version=CURRENT_VERSION):
    """Pair each manual score with the current detector's segment outcome,
    keeping video_name and outcome_source so the result can be split by
    review coverage."""
    seg = (rd[rd.outcome_detector_version == version]
           .groupby(JOIN_ALGO, as_index=False)
           .agg(segment_outcome=("segment_outcome", "first"),
                outcome_source=("outcome_source", "first"),
                video_name=("video_name", "first")))
    paired = ps.merge(seg, left_on=JOIN_HUMAN, right_on=JOIN_ALGO)
    paired["human"] = paired.score.map(HUMAN_SCORE)
    paired["algo"] = paired.segment_outcome.map(ALGO_CATEGORY)
    return paired


def build_worklist() -> list:
    """[{"video_id": str, "segment_nums": [int, ...]}, ...] for every finished,
    never-reviewed pellet where bench and algo disagree."""
    from mousedb.analyzable import finished_videos

    rd, ps = load_paired_pellets()
    finished = finished_videos()
    rd = rd[rd.video_name.isin(finished)]

    paired = pair_current(rd, ps)
    never_reviewed = paired[paired.outcome_source == "algo"]
    committed = never_reviewed[
        ~never_reviewed.segment_outcome.isin(NON_COMMITTAL + NON_EVALUABLE)
        & never_reviewed.algo.notna() & never_reviewed.human.notna()
    ]
    disagreements = committed[committed.human != committed.algo]

    out = []
    for video_id, group in disagreements.groupby("video_name"):
        out.append({
            "video_id": video_id,
            "segment_nums": sorted(int(s) for s in group["segment_num"].unique()),
        })
    return out


ROUTE_REASON = "bench disagreement (never reviewed)"


def route_command():
    """The mousereach-route-to-queue command, or None if unconfigured.

    Site configuration, never a literal in source: a hardcoded environment
    path would be wrong on every other machine and would not survive the
    public repo's own content rules.
    """
    from mousedb.cohort_sheets import _read_config

    cfg = _read_config()
    cmd = cfg.get("mousereach_route_cmd")
    if cmd:
        return str(cmd)
    env_dir = cfg.get("mousereach_env")
    if env_dir:
        exe = Path(env_dir) / "mousereach-route-to-queue"
        return str(exe.with_suffix(".exe") if exe.with_suffix(".exe").exists() else exe)
    return None


def route(worklist_path, queue="triage", reason=ROUTE_REASON):
    """Hand the worklist to MouseReach. Returns True if routing ran.

    Not being configured is not an error: the scan's own job -- finding the
    disagreements -- already succeeded by the time this is called.
    """
    import subprocess

    cmd = route_command()
    if not cmd:
        print("  routing not configured -- set mousereach_route_cmd or "
              "mousereach_env in ~/.mousedb/config.json to file these "
              "automatically")
        return False
    subprocess.run([cmd, "--worklist", str(worklist_path),
                    "--queue", queue, "--reason", reason], check=True)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--route", action="store_true",
                    help="also file the worklist into MouseReach's triage queue")
    ap.add_argument("--queue", default="triage", choices=["triage", "deep_review"],
                    help="queue to route into (default: triage)")
    args = ap.parse_args()

    worklist = build_worklist()
    args.out.write_text(json.dumps(worklist, indent=2))
    n_segments = sum(len(item["segment_nums"]) for item in worklist)
    print("videos: %d  segments: %d  wrote: %s" % (len(worklist), n_segments, args.out))

    if args.route:
        if not worklist:
            print("  nothing to route")
        else:
            route(args.out, queue=args.queue)


if __name__ == "__main__":
    main()
