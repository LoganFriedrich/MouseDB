"""One cohort's analysis documents, built in a few seconds, for looking at.

WHY THIS EXISTS
---------------
The full export is thirteen cohorts and about two gigabytes onto a network
share, and it takes well over half an hour. That is the right thing to produce
once the format is settled and the wrong thing to wait for while deciding
whether a column is correct, which is a loop of: look at the file, say what is
wrong, change it, look again.

So this builds ONE cohort, from a sample of its sessions, into its own folder.
Same code as the real export -- the same build functions, the same dictionary,
the same write rules -- so what is wrong here is wrong there.

WHAT MAKES IT FAST
------------------
Three things, in the order they mattered:

* It reads the parquet snapshot on local disk, not the finished CSV on the share.
* It asks the snapshot for one cohort's subjects instead of reading every cohort.
* It writes a sample of sessions, not all 61,520 rows. An example does not need
  every row; it needs every COLUMN to hold real values, which a few thousand
  rows do.

And then the part that matters for a loop: the cohort's slice of the snapshot is
CACHED on local disk, keyed to the snapshot's own modification time. The first
build pays for reading it; every build after that does not, which is most of the
time the loop was spending.

THE SAMPLE IS WHOLE SESSIONS, SPREAD OUT
----------------------------------------
Never the first N rows. The file is ordered, so the first rows are one animal
early in training, and every column that depends on the phase of the experiment
would hold a single value -- which is exactly the kind of thing being checked.
Sessions are taken evenly through the cohort in date order, so the sample covers
training, baseline, post-injury and rehabilitation.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional

import pandas as pd

from .. import study_facts
from ..config import lab_name, require, snapshot_dir
from . import analysis_reaches as ar
from . import analysis_sessions as asess
from . import odc_reaches

FOLDER_NAME = "Example"

# How an absent value is written in these files.
MISSING_WORD = "NA"
DEFAULT_ROWS = 5000
CACHE_NAME = "example_slice_%s_%s.parquet"


def _cache_dir() -> Path:
    """Beside the snapshot, which is already local, fast and disposable."""
    path = Path(snapshot_dir()) / "_example_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _subjects_of(snapshot: Path, cohort: str) -> List[str]:
    """Which subjects belong to the cohort -- one small column, not the table."""
    ids = pd.read_parquet(snapshot / "reach_data.parquet", columns=["subject_id"])
    return sorted({s for s in ids["subject_id"].dropna().unique()
                   if str(s).upper().startswith(cohort + "_")})


def cohort_slice(cohort: str, target_rows: int = DEFAULT_ROWS,
                 use_cache: bool = True) -> pd.DataFrame:
    """The sampled reach rows for one cohort, from cache when it is still valid.

    The cache is keyed to the snapshot file's modification time, so a fresh
    snapshot invalidates it without anyone having to remember to clear it.
    """
    snapshot = Path(snapshot_dir())
    source = snapshot / "reach_data.parquet"
    stamp = int(source.stat().st_mtime)
    cache = _cache_dir() / (CACHE_NAME % (cohort, "%s_%s" % (stamp, target_rows)))
    if use_cache and cache.exists():
        return pd.read_parquet(cache)

    subjects = _subjects_of(snapshot, cohort)
    if not subjects:
        return pd.DataFrame()

    import pyarrow.parquet as pq
    names = pq.read_schema(source).names
    # extended_features_json is the single largest column and the build expands
    # it per reach; it is dropped only when the schema says it exists.
    columns = ([c for c in names if c != "extended_features_json"]
               if "extended_features_json" in names else None)
    reach = pd.read_parquet(source, columns=columns,
                            filters=[("subject_id", "in", subjects)])

    reach = _sample_sessions(reach, target_rows)
    # Stale caches for older snapshots are removed rather than left to pile up.
    for old in _cache_dir().glob(CACHE_NAME % (cohort, "*")):
        if old != cache:
            old.unlink(missing_ok=True)
    reach.to_parquet(cache, index=False)
    return reach


def _sample_sessions(reach: pd.DataFrame, target_rows: int) -> pd.DataFrame:
    """Whole sessions, evenly through the cohort in date order (see module docstring)."""
    if reach.empty or "video_name" not in reach.columns:
        return reach
    sessions = sorted(reach["video_name"].dropna().unique())
    if not sessions:
        return reach
    per_session = max(1, len(reach) // len(sessions))
    want = max(1, min(len(sessions), target_rows // max(1, per_session)))
    step = max(1, len(sessions) // want)
    chosen = set(sessions[::step][:want])
    return reach[reach["video_name"].isin(chosen)]


def build(cohort: str = "CNT_01", target_rows: int = DEFAULT_ROWS,
          out_dir: Optional[Path] = None, use_cache: bool = True) -> dict:
    """Write the example folder. Returns what was written and how long it took."""
    started = time.time()
    cohort = cohort.upper()
    out_dir = Path(out_dir) if out_dir else (
        Path(require("mousedb_root")) / "exports" / "current" / FOLDER_NAME)

    reach = cohort_slice(cohort, target_rows, use_cache)
    # The same weight join the real export does when it loads the snapshot. This
    # path reads the parquet files directly for speed, which means every join
    # done in load_snapshot has to be done here too -- or the example quietly
    # lacks a column the real export has, which defeats the point of it.
    reach = odc_reaches.attach_weights(
        reach, odc_reaches.session_weights(snapshot_dir()))
    if reach.empty:
        return {"cohort": cohort, "files": [], "reason": "no reaches for this cohort"}
    loaded = time.time()

    snapshot = Path(snapshot_dir())
    others = {}
    for name in ("subjects", "animal_records", "pellet_scores"):
        path = snapshot / ("%s.parquet" % name)
        others[name] = pd.read_parquet(path) if path.exists() else None

    full, _rows = odc_reaches.build_cohort(
        cohort, reach, others["subjects"], others["animal_records"],
        others["pellet_scores"], study_facts.facts, lab_name())
    if full.empty:
        return {"cohort": cohort, "files": [], "reason": "the cohort built no rows"}

    reaches, reach_rows = ar.build(full)
    sessions, session_rows = asess.build(reaches, reach_rows)
    built = time.time()

    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for frame, rows, stem, dictionary in (
            (reaches, reach_rows, "reaches_%s_analysis" % cohort, "reaches_analysis"),
            (sessions, session_rows, "sessions_%s_analysis" % cohort, "sessions_analysis")):
        if not len(frame):
            continue
        # The dictionary carries no cohort in its name: these columns are the
        # same for every cohort, so thirteen identical copies would be thirteen
        # things to keep in step.
        for target, data in ((stem + ".csv", frame),
                             (dictionary + "_DATA_DICTIONARY.csv", pd.DataFrame(rows))):
            try:
                data.to_csv(out_dir / target, index=False)
            except PermissionError:
                # Almost always the file open in a spreadsheet, which is the
                # whole point of this folder. Say so plainly instead of a
                # traceback, and do not pretend the file was rewritten.
                written.append({"file": target, "left_alone": "open in another program"})
                continue
            written.append({"file": target, "rows": int(len(data)),
                            "columns": int(len(data.columns))})

    return {"cohort": cohort, "out_dir": str(out_dir), "files": written,
            "sessions_sampled": int(reach["video_name"].nunique()),
            "seconds": {"load": round(loaded - started, 1),
                        "build": round(built - loaded, 1),
                        "write": round(time.time() - built, 1),
                        "total": round(time.time() - started, 1)}}


def sample(cohort: str = "CNT_01", rows: int = 3, out_dir: Optional[Path] = None,
           seed: Optional[int] = None) -> str:
    """A few random rows of the example reaches file, laid out to be read.

    One column per line with the rows side by side. The file has nearly 200
    columns, so a raw CSV line is unreadable on anything but a wide screen, and
    a wrong value is easiest to see next to its own column name.
    """
    out_dir = Path(out_dir) if out_dir else (
        Path(require("mousedb_root")) / "exports" / "current" / FOLDER_NAME)
    path = out_dir / ("reaches_%s_analysis.csv" % cohort.upper())
    if not path.exists():
        return "%s does not exist yet" % path

    # keep_default_na=False: absent values are written as the WORD "NA" on
    # purpose, and pandas would otherwise turn it into a blank -- hiding the
    # very thing being checked.
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, low_memory=False)

    # NEVER sample a row whose session has a problem nobody can fix now. The
    # three "Unscheduled" dates in CNT_01 are days 6-8 after injury: filmed, but
    # never hand-scored, because they are not protocol timepoints. Their rows are
    # correct and they stay in the export -- but showing one as an EXAMPLE wastes
    # the reader's attention on a known dead end, because every hand-scored
    # column reads NA and none of that is about the format being checked.
    clean = frame
    if "test_type" in frame.columns:
        clean = clean[clean["test_type"] != "Unscheduled"]
    if "manual_retrieved" in clean.columns:
        clean = clean[clean["manual_retrieved"] != MISSING_WORD]
    if clean.empty:
        clean = frame
    picked = clean.sample(min(rows, len(clean)), random_state=seed)

    width = max(len(c) for c in frame.columns)
    lines = ["%s" % path,
             "%d rows x %d columns" % (len(frame), len(frame.columns)), ""]
    for n, column in enumerate(frame.columns, 1):
        values = [str(v)[:34] for v in picked[column].tolist()]
        lines.append(("%3d  %-*s  %s" % (n, width, column,
                                         " | ".join("%-34s" % v for v in values))).rstrip())
    return "\n".join(lines)
