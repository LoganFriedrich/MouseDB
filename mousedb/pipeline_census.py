"""The pipeline census, joined with the database view.

WHY THIS EXISTS
---------------
MouseReach's ``mousereach-census`` answers, from the pipeline's folders alone:
how many single-animal sessions SHOULD exist (every collage anywhere,
expanded, unioned with found artifacts), how many are finished on disk, and
where every unfinished one sits. What it deliberately CANNOT answer is the
third condition of "analyzed" -- present in the database -- because the
pipeline side has no database view (see mousereach.census.runner).

This module is that missing half. It:

* runs the census through the configured MouseReach environment (the same
  public-CLI pattern bench_scan uses -- mousedb calls tools' entry points,
  never imports their code),
* caches the JSON under ``<mousedb_root>/logs/`` (mousedb's own ledger area),
* joins it with the snapshot's video list to promote finished-and-landed
  sessions to ``analyzed`` and to evaluate THE INVARIANT: nothing may sit
  finished-on-disk but absent from the database. That number must be zero;
  when it is not, the sessions and a reason are named.

TRANSIENTS ARE REASONS, NOT STATES: a video that finished five minutes ago
lands on the next hourly import -- the invariant message says so, and the
violation list is recomputed live on every call, never cached, so a healed
pipeline never shows a stale alarm.

Outcome-free trays (E/F) are promoted to ``session_only`` WITHOUT the
database condition and are excluded from the invariant: their sessions are
deliberately not database material (no per-pellet outcomes exist to import),
so "absent from the database" is their correct permanent state, not a gap.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional, Set

from .config import get, require

CACHE_NAME = "pipeline_census.json"

# Elements in pipeline order, as the census emits them.
ELEMENT_ORDER = ["unanalyzed", "crop_dlc", "mousereach", "triage",
                 "deep_review", "quarantined", "analyzed", "session_only"]

OUTCOME_FREE_TRAYS = ("E", "F")


def cache_path() -> Path:
    return require("mousedb_root") / "logs" / CACHE_NAME


def mousereach_python() -> Optional[Path]:
    """The configured MouseReach environment's python.exe, or None.

    ``mousereach_env`` points at the env's Scripts/bin directory (the same
    key bench_scan uses); accept the env root too, so a slightly-off value
    still resolves rather than failing on a technicality."""
    env_dir = get("mousereach_env")
    if not env_dir:
        return None
    p = Path(env_dir)
    for cand in (p.parent / "python.exe", p / "python.exe",
                 p.parent / "python", p / "python"):
        if cand.exists():
            return cand
    return None


def refresh(days: int = 14, timeout: int = 1800,
            cache: Optional[Path] = None) -> dict:
    """Run the census now (a 2-5 minute scan over the NAS), cache and return it.

    Invoked as ``python -m mousereach.census`` rather than the console shim,
    so it works even when an environment's Scripts entry points are stale or
    missing.
    """
    py = mousereach_python()
    if py is None:
        raise RuntimeError(
            "MouseReach environment not configured -- set it with:\n"
            "  mousedb config --set mousereach_env <MouseReach env Scripts dir>")
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: no console flash
    r = subprocess.run(
        [str(py), "-m", "mousereach.census", "--json", "--days", str(days)],
        capture_output=True, text=True, timeout=timeout, **kwargs)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-8:]
        raise RuntimeError("the pipeline census failed (exit %d):\n%s"
                           % (r.returncode, "\n".join(tail)))
    census = json.loads(r.stdout)
    save_cache(census, cache)
    return census


def save_cache(census: dict, cache: Optional[Path] = None) -> Path:
    cache = cache or cache_path()
    cache.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(cache.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(census, fh, indent=0)
        os.replace(tmp, cache)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return cache


def load_cached(cache: Optional[Path] = None) -> Optional[dict]:
    """The last census taken, or None when none exists yet. Unreadable is
    reported by raising -- a corrupt cache must not read as 'no census'."""
    cache = cache or cache_path()
    if not cache.is_file():
        return None
    return json.loads(cache.read_text(encoding="utf-8"))


def join_with_db(census: dict, in_db_names: Optional[Set[str]]) -> dict:
    """Promote finished+landed sessions to ``analyzed`` and evaluate the
    invariant. ``in_db_names`` is the set of video names the database holds
    (from the snapshot); None means NO database view, in which case the
    analyzed count and the invariant are UNAVAILABLE (None) -- never zero,
    never guessed (see mousereach.census.locate_sessions for why refusing
    beats a plausible wrong answer).
    """
    from .data_status import _cohort_of_video  # lazy: avoids an import cycle

    sessions: Dict[str, dict] = census.get("sessions") or {}
    db_view = in_db_names is not None

    by_element: Dict[str, int] = {}
    by_cohort: Dict[str, Dict[str, int]] = {}
    violations: Dict[str, str] = {}
    n_analyzed = 0
    n_session_only = 0

    for sid, info in sessions.items():
        el = info.get("element") or "unanalyzed"
        finished = bool(info.get("finished"))
        tray = info.get("tray")
        outcome_free = tray in OUTCOME_FREE_TRAYS

        if finished and el == "mousereach":
            # Finished and NOT held for a person: either landed, or the gap.
            if outcome_free:
                # Deliberately not database material -- terminal without the
                # database condition, excluded from the invariant.
                el = "session_only"
                n_session_only += 1
            elif not db_view:
                pass  # cannot promote and cannot accuse; counted below as caveat
            elif sid in in_db_names:
                el = "analyzed"
                n_analyzed += 1
            else:
                violations[sid] = (
                    "finished on disk but not in the database -- new results "
                    "land on the next hourly import; investigate only if a "
                    "video stays here across imports")

        by_element[el] = by_element.get(el, 0) + 1
        coh = _cohort_of_video(sid)
        row = by_cohort.setdefault(coh, {"expected": 0})
        row["expected"] += 1
        row[el] = row.get(el, 0) + 1

    totals = dict(census.get("totals") or {})
    totals["analyzed"] = n_analyzed if db_view else None
    totals["session_only"] = n_session_only

    caveats = []
    if not db_view:
        caveats.append(
            "analyzed: UNAVAILABLE (snapshot unreadable, so database "
            "membership could not be checked). Finished sessions stay "
            "counted under 'mousereach'. Do not read this as 'nothing is "
            "analyzed', and no invariant verdict exists for this run.")

    return {
        "database_view": db_view,
        "generated_at": census.get("generated_at"),
        "scan_seconds": census.get("scan_seconds"),
        "totals": totals,
        "by_element": {k: by_element.get(k, 0)
                       for k in ELEMENT_ORDER if by_element.get(k)},
        "by_cohort": by_cohort,
        "invariant": ({"count": len(violations), "sessions": violations}
                      if db_view else None),
        "eta": census.get("eta"),
        "review": census.get("review"),
        "diagnostics": census.get("diagnostics"),
        "roots": census.get("roots"),
        "caveats": caveats,
    }
