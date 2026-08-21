"""Which videos are finished, and therefore allowed into an analysis.

THE RULE
--------
A video may be read only if it is done. Done means two things at once:

  1. Its results sit in the Analyzed folder - the pipeline's final output
     location. A video still moving through Processing has results that are
     provisional. Some of its stages may not have run yet, and the numbers
     sitting in the database for it are a snapshot of an unfinished job.

  2. Every stage that produced it ran at the version currently declared as the
     one to use - the pose model, segmentation, reach detection, outcome
     detection, reach assignment, and kinematic extraction. A video that is
     current on four stages and a version behind on the fifth is not finished
     work, it is work that is waiting to be redone.

Reading anything else mixes finished measurements with provisional ones, and
there is no way to tell afterwards which is which.

WHY IT READS THE INDEX
----------------------
MouseReach records what version each stage ran at in a per-video manifest, and
pushes those into a small database at pipeline_records/version_index.db so that
readers can ask "is this video current?" with one query instead of re-reading
thousands of files over the network. This module reads that index and the
declared versions, and never re-implements the pipeline's own bookkeeping.

The list of stages is taken from the declared-versions file itself rather than
written out here. That matters: the two times a stage escaped version tracking,
it was because a second hand-maintained list existed somewhere and drifted from
the first. Whatever is declared must match; a stage added later is covered
without touching this file.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

PIPELINE_ROOT = Path(r"Y:\LAB_ROOT\Behavior\MouseReach_Pipeline")
DECLARED_VERSIONS = PIPELINE_ROOT / "pipeline_versions.json"
VERSION_INDEX = PIPELINE_ROOT / "pipeline_records" / "version_index.db"
ANALYZED = PIPELINE_ROOT / "Analyzed"

# Declared keys that are not per-stage version checks.
_NOT_A_STAGE = {"mousereach"}
_DLC_KEY = "dlc_scorer"

# What a version field says when the stage did not record one. A video carrying
# any of these has a hole in its provenance and cannot be called finished.
_NOT_A_VERSION = {"", "not_run", "unknown", "error_reading", "None", None}


def declared_versions(path: Path = DECLARED_VERSIONS) -> Dict[str, str]:
    """The versions the pipeline currently says every stage should be at."""
    return json.loads(Path(path).read_text()).get("versions", {})


def _index_rows(db_path: Path = VERSION_INDEX) -> Dict[str, dict]:
    """Per-video recorded versions, from MouseReach's version index."""
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=60)
    try:
        rows = {}
        for stem, pv, dlc in con.execute(
                "SELECT stem, pipeline_versions, dlc_scorer FROM versions"):
            if not stem:
                continue
            try:
                versions = json.loads(pv) if pv else {}
            except ValueError:
                versions = {}
            rows[stem] = {"pipeline_versions": versions, "dlc_scorer": dlc}
        return rows
    finally:
        con.close()


def finished_videos(declared: Optional[Dict[str, str]] = None,
                    require_analyzed: bool = True) -> Set[str]:
    """Videos that are done: in Analyzed, and current at every declared stage.

    Args:
        declared: override the declared versions (defaults to the file)
        require_analyzed: also require the video's results to be in the Analyzed
            folder. Leave this on unless you are deliberately auditing.

    Returns:
        Set of video stems safe to read.
    """
    ok, _ = finished_videos_with_reasons(declared, require_analyzed)
    return ok


def finished_videos_with_reasons(
    declared: Optional[Dict[str, str]] = None,
    require_analyzed: bool = True,
) -> Tuple[Set[str], Dict[str, str]]:
    """As finished_videos, plus why each rejected video was rejected."""
    declared = declared or declared_versions()
    stages = [k for k in declared if k not in _NOT_A_STAGE and k != _DLC_KEY]
    want_dlc = declared.get(_DLC_KEY)

    in_analyzed = None
    if require_analyzed:
        in_analyzed = {p.name[: -len("_processing_manifest.json")]
                       for p in ANALYZED.rglob("*_processing_manifest.json")}

    ok: Set[str] = set()
    rejected: Dict[str, str] = {}

    for stem, rec in _index_rows().items():
        if in_analyzed is not None and stem not in in_analyzed:
            rejected[stem] = "not in the Analyzed folder - still in progress"
            continue

        pv = rec.get("pipeline_versions") or {}
        dlc = rec.get("dlc_scorer")

        problems = []
        if want_dlc:
            if dlc in _NOT_A_VERSION:
                problems.append("pose model not recorded")
            elif dlc != want_dlc:
                problems.append("pose model is %s, current is %s" % (dlc, want_dlc))

        for stage in stages:
            got = pv.get(stage)
            if got in _NOT_A_VERSION:
                problems.append("%s never recorded a version" % stage)
            elif str(got) != str(declared[stage]):
                problems.append("%s is %s, current is %s" % (stage, got, declared[stage]))

        if problems:
            rejected[stem] = "; ".join(problems)
        else:
            ok.add(stem)

    return ok, rejected


def summarize() -> None:
    """Print what is and is not analyzable, and why."""
    from collections import Counter

    declared = declared_versions()
    print("declared current versions:")
    for k, v in declared.items():
        print("   %-20s %s" % (k, v))

    ok, rejected = finished_videos_with_reasons(declared)
    print("\n%6d  videos finished and current at every step (analyzable)" % len(ok))
    print("%6d  videos excluded" % len(rejected))

    buckets = Counter()
    for reason in rejected.values():
        first = reason.split(";")[0].strip()
        buckets[first] += 1
    print("\nwhy videos are excluded (leading reason):")
    for reason, n in buckets.most_common(10):
        print("   %5d  %s" % (n, reason))


if __name__ == "__main__":
    summarize()
