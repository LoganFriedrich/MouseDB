"""The CURRENT exports: what mousedb knows, as CSVs a person can open now.

WHY THIS EXISTS
---------------
The end of the lab's workflow is a person asking "where is my data?" and
expecting a current CSV of everything the database holds, formatted to
ODC-SCI, with a data dictionary beside it. Until 2026-08-28 the answer was:
a 200 MB dump the sync overwrote, an ODC Excel export run by hand for one
cohort in January, and no dictionary anywhere. This module writes, and the
nightly refresh keeps current, one folder:

    Databases/exports/current/
        reach_data.csv                    one row per detected reach
        reach_data_DATA_DICTIONARY.csv
        manual_scores.csv                 one row per hand-scored pellet
        manual_scores_DATA_DICTIONARY.csv
        ODC_sessions_DATA_DICTIONARY.csv  (and one dictionary per other kind)
        MANIFEST.json                     when, from what, how many rows, problems
        README.txt                        what each file is, in plain words
        [<GRANT>/]<PROJECT>/<COHORT>/     each cohort's files (exporters.layout; a
                                          project in a grant sits in its folder)
        [<GRANT>/]<PROJECT>/*_all_cohorts*  each project's cohorts combined

It reads the analysis SNAPSHOT (parquet), never connectome.db, for the two
big tables -- so it can run anywhere, any time, without touching the live
database. The per-cohort ODC session files need computed statistics that
live behind the ORM, so they are only refreshed when the caller says the
database may be read (the watcher's own loop, or a paused watcher).

Every dataset is checked against its data dictionary; columns with no
definition are listed in MANIFEST.json under "undocumented_columns" and
the manifest's "complete" flag is False. An export that would fail ODC
upload must say so.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from . import data_dictionary as dd
from . import layout, missing, trays
from .. import DEFAULT_EXPORT_PATH

from ..config import at_import as _at_import, require, snapshot_dir as _snapshot_dir

CURRENT_DIR = (Path(DEFAULT_EXPORT_PATH) / "current") if DEFAULT_EXPORT_PATH else None
DEFAULT_SNAPSHOT_DIR = _at_import(_snapshot_dir)  # None until configured

# Column order of reach_data.csv: identity first, then the reach, then
# provenance. extended_features (a JSON blob per row) goes LAST so a person
# opening the file in Excel sees the numbers before the wall of text.
REACH_DATA_ORDER_FIRST = [
    "subject_id", "video_name", "session_date", "tray_type", "run_number",
    "segment_num", "reach_id", "reach_num",
]
REACH_DATA_ORDER_LAST = ["extended_features"]

README = """CURRENT EXPORTS -- everything mousedb knows, as of the time in MANIFEST.json.
These files are regenerated automatically (nightly on the processing server)
and by the "Where Is My Data" tab in mousedb. There is one data dictionary
per kind of file, shared by every cohort's file of that kind. Do not edit them; edit the
source (tracking sheets, review tools) and they will be regenerated.

HOW THE FOLDER IS ARRANGED
  This folder (the top)   files covering every project at once (reach_data.csv,
                          manual_scores.csv), every data dictionary, and
                          analysis_EXCLUSIONS.csv.
  CNT\, ASPA\, ...        one folder per project. A project the lab lists under
                          a grant ("mousedb grants") sits inside the grant's
                          folder: GRANT\PROJECT\.
  CNT\CNT_05\             one folder per cohort, holding that cohort's files.
                          The cohort is in every file name too, so a file copied
                          out of its folder still says which cohort it is.
  CNT\*_CNT_all_cohorts*  beside the cohort folders: one file per kind with
                          every cohort of the project, one after the other, in
                          cohort order. Built from the cohort files after they
                          are all written, so it is exactly those files put
                          together. A column only some cohorts have is filled
                          for the others with "Not recorded" (NA in the
                          analysis files); MANIFEST.json says which. The entry
                          index is counted again over the combined rows.
  In a script             do not write a file's path out; ask mousedb where it
                          is (locate in mousedb.exporters.layout -- see the
                          Where Is My Data guide). A written-out path breaks if
                          the folder is ever rearranged again; asking does not.

reach_data.csv            One row per reach the pipeline detected, with the
                          reach's kinematics, the pellet outcome of its segment,
                          and where that outcome came from (algorithm, human
                          review, or ground truth). Column definitions:
                          reach_data_DATA_DICTIONARY.csv.
reach_data_extended.parquet
                          The 174-value extended feature block per reach (per
                          paw-point kinematics) as JSON, keyed by subject_id,
                          video_name, segment_num, reach_id. Kept out of the CSV
                          because it made the file 3 GB. Read with pandas /
                          pyarrow and join on those four columns.
manual_scores.csv         One row per pellet scored by hand from the tray
                          (0 untouched / 1 displaced / 2 retrieved), with the
                          session's derived phase. Definitions:
                          manual_scores_DATA_DICTIONARY.csv.
ODC_sessions_<cohort>.csv One row per animal per session in the ODC-SCI
                          2_ODC_Animal_Tracking shape (per-tray and daily
                          counts and percentages, weight, injury). Definitions:
                          ODC_sessions_DATA_DICTIONARY.csv.
ODC_reaches_<cohort>.csv  One row per reach, with that animal's details
                          (strain, sex, surgery columns copied from the tracking
                          sheet) and that session's details (date, tray, phase)
                          repeated on every row, plus video and hand-score
                          totals for the tray -- the complete record, including
                          how each result was produced. One dictionary for every
                          cohort: ODC_reaches_DATA_DICTIONARY.csv (a surgery
                          column only some cohorts' sheets record is described
                          once; MANIFEST.json lists which cohorts have it).
reaches_<cohort>_summary.csv
                          The same reaches, process columns removed, everything
                          named in plain words: the file to share. Definitions:
                          reaches_summary_DATA_DICTIONARY.csv.
reaches_<cohort>_analysis.csv, sessions_<cohort>_analysis.csv
                          The same reaches, and their per-session totals, in the
                          shape a statistics package wants (numbers, NA for
                          absent). An entry with an identified problem is left
                          out; analysis_EXCLUSIONS.csv says which and why.
                          Definitions: reaches_analysis_DATA_DICTIONARY.csv,
                          sessions_analysis_DATA_DICTIONARY.csv.
ASPA_NN_X in a file name  A frozen ASPA cohort. X is the cohort letter the lab
                          uses (its workbook and folder, e.g. D); NN is the same
                          cohort encoded for the pipeline (the letter's position in
                          the alphabet: D -> 04). MANIFEST.json names the workbook.
MANIFEST.json             When these were written, from which snapshot, row
                          counts, and any problems (e.g. columns missing a
                          dictionary entry, which would fail an ODC upload).

ODC-SCI submission = a dataset CSV + its DATA_DICTIONARY CSV, together.
See Databases/docs/ODC-SCI_submission_standard.md.
"""


def _staging_dir(snapshot_dir: Path) -> Path:
    """A scratch folder on the same (local) disk as the snapshot.

    WHY: the exports folder is on a network share, and pandas writes a CSV
    onto a share a few bytes at a time -- measured at a fraction of a megabyte
    per second against tens of MB/s for copying a finished file. Writing every
    file here first and copying it over turned a 2-3 hour nightly export into
    minutes (2026-10-16). refresh_current() empties this folder at the start
    of each run so nothing stale is ever copied across."""
    d = Path(snapshot_dir) / "_export_staging"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _place(local: Path, final: Path) -> None:
    """Copy a finished local file onto its final name atomically: a reader
    sees the previous file or the new one, never a partial write."""
    import shutil
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(final.name + ".tmp")
    shutil.copyfile(local, tmp)
    tmp.replace(final)


def _reach_data_csv(snapshot_dir: Path, out_dir: Path, manifest: dict) -> None:
    src = snapshot_dir / "reach_data.parquet"
    staging = _staging_dir(snapshot_dir)
    df = pd.read_parquet(src)
    sort_cols = [c for c in ("subject_id", "session_date", "video_name", "segment_num", "reach_num")
                 if c in df.columns]
    df = df.sort_values(sort_cols)

    # The extended block (174 per-reach values from extractor 2.6.0, stored as one JSON string
    # per row) is NOT in the CSV: with it the file was 2.98 GB, which nothing
    # opens. It goes to a parquet sidecar keyed by the same identity columns;
    # the CSV stays the ~200 MB a person can actually load.
    cols = [c for c in REACH_DATA_ORDER_FIRST if c in df.columns]
    cols += [c for c in df.columns if c not in cols and c not in REACH_DATA_ORDER_LAST and c != "id"]
    # Easy and flat trays: the algorithm's pellet and reach outcome columns read
    # "Not applicable" (trays.ALGO_PELLET_COLUMNS says which, and why).
    trays.blank_where(df, trays.outcome_free(df), trays.ALGO_PELLET_COLUMNS,
                      missing.NOT_APPLICABLE)
    out = out_dir / "reach_data.csv"
    df[cols].to_csv(staging / out.name, index=False)
    _place(staging / out.name, out)
    dd.write_dictionary("reach_data", out_dir / "reach_data_DATA_DICTIONARY.csv")
    manifest["files"]["reach_data.csv"] = {
        "rows": int(len(df)), "columns": int(len(cols)),
        "source": str(src), "source_mtime": _mtime(src),
        "undocumented_columns": dd.undocumented_columns("reach_data", cols),
    }
    if "extended_features" in df.columns:
        key = [c for c in ("subject_id", "video_name", "segment_num", "reach_id") if c in df.columns]
        side = out_dir / "reach_data_extended.parquet"
        df[key + ["extended_features"]].to_parquet(staging / side.name, index=False)
        _place(staging / side.name, side)
        manifest["files"][side.name] = {
            "rows": int(len(df)), "columns": len(key) + 1,
            "note": "extended_features JSON per reach; join to reach_data.csv on " + ", ".join(key),
        }


def _manual_scores_csv(snapshot_dir: Path, out_dir: Path, manifest: dict) -> None:
    src = snapshot_dir / "pellet_scores.parquet"
    df = pd.read_parquet(src)
    cols = [c for c in ("subject_id", "session_date", "test_phase", "phase_group", "tray_type",
                        "tray_number", "pellet_number", "score", "contact_group",
                        "entered_by", "entered_at", "id") if c in df.columns]
    cols += [c for c in df.columns if c not in cols]
    df = df[cols].sort_values([c for c in ("subject_id", "session_date", "tray_number", "pellet_number") if c in cols])
    out = out_dir / "manual_scores.csv"
    staging = _staging_dir(snapshot_dir)
    df.to_csv(staging / out.name, index=False)
    _place(staging / out.name, out)
    dd.write_dictionary("manual_scores", out_dir / "manual_scores_DATA_DICTIONARY.csv")
    manifest["files"]["manual_scores.csv"] = {
        "rows": int(len(df)), "columns": int(len(cols)),
        "source": str(src), "source_mtime": _mtime(src),
        "undocumented_columns": dd.undocumented_columns("manual_scores", cols),
    }


# The snapshot tables the per-reach export reads: odc_reaches.load_snapshot
# (reaches, animals, hand scores, sheet records, per-pellet table, weights) and
# the exclusion checks (export_exclusions.compute). A table read by either and
# missing here is a change a skip would hide.
ODC_INPUT_TABLES = ("reach_data", "subjects", "pellet_scores", "animal_records",
                    "video_pellets", "weights")


def _package_root() -> Path:
    """The mousedb package folder whose code writes these files."""
    return Path(__file__).resolve().parent.parent


def exporter_code_hash(root: Optional[Path] = None) -> str:
    """SHA-256 of every .py file of the mousedb package, in sorted path order.

    WHY the whole package and not a list of exporter modules: the files are
    shaped by code all over it (phase labels, statistics, the sheet readers,
    the dictionary), and a list kept by hand goes stale exactly like the
    hand-bumped number this replaced. The price is one full rebuild after
    every install of new code, which the nightly run has time for.

    Line endings are normalised so the same code checked out on two computers
    hashes the same. Read from disk at the time of the call: the Where Is My
    Data button runs the exporter as a fresh program (gui.data_status_tab) so
    the code that runs is the code on disk, never an older copy a window has
    held in memory since it was opened."""
    import hashlib
    root = Path(root) if root else _package_root()
    h = hashlib.sha256()
    for f in sorted(root.rglob("*.py"), key=lambda q: q.relative_to(root).as_posix()):
        if "__pycache__" in f.parts:
            continue
        h.update(f.relative_to(root).as_posix().encode("utf-8") + b"\0")
        h.update(f.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()


def odc_reaches_signature(snapshot_dir: Path) -> dict:
    """Everything the per-reach ODC/analysis files and analysis_EXCLUSIONS.csv
    are built from, reduced to values that change whenever an input changes.

    WHY each part:
      * snapshot tables -- by CONTENT (SHA-256, recorded by the snapshot refresh
        in its generation stamp, so this costs no extra read), not by size and
        row count: a corrected phase label or score changes neither.
      * study facts, lab field definitions, grants, lab settings, lab name --
        configuration that decides cell values, dictionary entries and folders.
      * the exclusion checks' own inputs outside the snapshot (collage names
        from the pipeline, the name-audit record, which cohorts have a tracking
        workbook) -- export_exclusions.input_signature.
      * the ASPA workbook names -- the manifest names each letter cohort's
        workbook.
      * output_format -- the exporter's own code: a hash of the package's
        source (exporter_code_hash), so any change to it rebuilds the files.
    Anything that cannot be read is recorded as such ("absent", "unreadable"),
    so the day it can be read the signature changes and the files are rebuilt.
    """
    from .. import snapshot_generation, study_facts
    from ..config import get as _cfg, lab_name, mousereach_pipeline_root
    from ..grants import grants as _grants
    from . import export_exclusions, field_definitions

    def text_of(path) -> str:
        try:
            return Path(path).read_text(encoding="utf-8") if path and Path(path).is_file() else ""
        except OSError as e:
            return "unreadable: %s" % type(e).__name__

    sig: dict = {}
    for name in ODC_INPUT_TABLES:
        sig[name] = snapshot_generation.table_hash(snapshot_dir, name)
    sig["study_facts"] = text_of(study_facts.facts_path())
    sig["field_definitions"] = text_of(field_definitions.definitions_path())
    # The signature covers the INPUTS; this part covers the exporter ITSELF.
    # A change to the code that writes these files (a new column, a different
    # word in a cell, a new dictionary entry) must rebuild them even when no
    # data changed. Until this was derived from the code it was a number bumped
    # by hand, and a change made without bumping it was silently skipped: the
    # run reused the previous run's files and its record of what it wrote.
    sig["output_format"] = exporter_code_hash()
    # Which grant holds which project decides where the files go, so a change
    # to it must re-export even when no data changed.
    sig["grants"] = _grants()
    try:
        sig["lab_name"] = lab_name()
    except Exception as e:
        sig["lab_name"] = "unreadable: %s" % type(e).__name__
    try:
        pipeline_root = mousereach_pipeline_root()
    except Exception:
        pipeline_root = None
    sig["exclusion_inputs"] = export_exclusions.input_signature(
        pipeline_root, _cfg("cohort_sheets_dir"))
    try:
        from ..cohort_sheets import available_aspa_cohorts, find_aspa_sheet
        sig["aspa_workbooks"] = sorted(
            (L, getattr(find_aspa_sheet(L), "name", None)) for L in available_aspa_cohorts())
    except Exception as e:
        sig["aspa_workbooks"] = "unreadable: %s" % type(e).__name__
    return json.loads(json.dumps(sig, default=str))     # as it will read back


def _odc_reaches_csvs(snapshot_dir: Path, out_dir: Path, manifest: dict) -> None:
    """Per-cohort per-reach ODC files (see exporters.odc_reaches).

    Skipped when nothing they are built from has changed since the last write
    (``odc_reaches_signature``): they are large, and every refresh would
    otherwise rewrite hundreds of MB on the share for no change. The manifest
    then carries the previous run's entries, problems and notes.

    The signature is saved only after a run whose every problem follows from
    the signed inputs alone (``from_inputs``: a check that could not run, a
    table not yet in the snapshot). A problem that does not -- a cohort's file
    that failed to write -- leaves no signature, so the next run rebuilds."""
    from . import odc_reaches
    sig = odc_reaches_signature(snapshot_dir)
    sig_file = out_dir / ".odc_reaches_signature.json"
    try:
        old = json.loads(sig_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    if old.get("signature") == sig and old.get("files") and all(
            (out_dir / n).exists() for n in old["files"] if old["files"][n].get("rows")):
        manifest["files"].update(old["files"])
        manifest["problems"].extend(old.get("problems", []))
        manifest.setdefault("notes", []).extend(old.get("notes", []))
        manifest["odc_reaches_skipped"] = (
            "nothing the per-reach files are built from has changed since %s; the files "
            "written then were kept" % old.get("saved_at", "the last full export"))
        return
    part = {"files": {}, "problems": [], "notes": []}
    odc_reaches.export(snapshot_dir, out_dir, manifest=part, staging_dir=_staging_dir(snapshot_dir))
    for k in ("archived_flat_layout_files", "archived_per_cohort_dictionaries",
              "archived_cohorts_without_reaches", "archived_combined_files"):
        if k in part:
            manifest[k] = part[k]
    manifest["files"].update(part["files"])
    manifest["problems"].extend(part["problems"])
    manifest.setdefault("notes", []).extend(part.get("notes", []))
    # Notes about something this run DID (files it moved) are shown once and
    # not saved with the signature below: saved notes are repeated on every
    # skipped night, which would report a move that did not happen that night.
    manifest["notes"].extend(part.get("run_notes", []))
    manifest.setdefault("failures", []).extend(part.get("failures", []))
    from_inputs = set(part.get("from_inputs", []))
    if not part.get("failures") and all(p in from_inputs for p in part["problems"]):
        sig_file.write_text(json.dumps({
            "signature": sig, "files": part["files"], "problems": part["problems"],
            "notes": part.get("notes", []),
            "saved_at": datetime.now().isoformat(timespec="seconds")}, indent=1),
            encoding="utf-8")
    else:
        # A previous signature must not survive a run that changed the files.
        # A delete that is refused is said, never swallowed: a signature left
        # behind can make a later night skip the rebuild and repeat an old
        # run's file list and problems -- and a delete refused night after
        # night (another program holding the file open) went unseen for weeks.
        try:
            sig_file.unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            manifest["problems"].append(
                "the per-reach skip record %s could not be removed (%s: %s) -- another "
                "program may have it open; close it, or delete the file by hand once no "
                "refresh is running" % (sig_file.name, type(e).__name__, e))


def _odc_sessions_csvs(out_dir: Path, manifest: dict) -> None:
    """Per-cohort ODC session tables. Needs the ORM (database read)."""
    from ..database import get_db
    from .cohort_reports import export_odc_format
    from ..schema import Cohort

    db = get_db()
    with db.session() as s:
        cohorts = [c.cohort_id for c in s.query(Cohort).order_by(Cohort.cohort_id).all()]
    from .odc_reaches import cohort_label, cohort_source, archive_old_names
    dd.write_dictionary("ODC_sessions", out_dir / "ODC_sessions_DATA_DICTIONARY.csv")
    for cid in cohorts:
        xlsx = out_dir / ("_tmp_%s_ODC.xlsx" % cid)
        label = cohort_label(cid)  # letter cohorts carry their letter: ASPA_04 -> ASPA_04_D
        try:
            export_odc_format(db, cid, xlsx)
            if not xlsx.exists():
                manifest["files"][layout.key(out_dir, layout.cohort_file(
                    out_dir, "ODC_sessions", cid, label))] = {"rows": 0, "note": "no data"}
                continue
            df = pd.read_excel(xlsx)
            out = layout.cohort_file(out_dir, "ODC_sessions", cid, label)
            out.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(out, index=False)
            name = layout.key(out_dir, out)
            manifest["files"][name] = {
                "rows": int(len(df)), "columns": int(len(df.columns)),
                "undocumented_columns": dd.undocumented_columns("ODC_sessions", df.columns),
            }
            if label != cid:
                manifest["files"][name]["cohort"] = cohort_source(cid) or label
                archive_old_names(out_dir, ["ODC_sessions_%s.csv" % cid], label)
        except Exception as e:
            msg = "ODC_sessions_%s: %s: %s" % (cid, type(e).__name__, e)
            manifest["problems"].append(msg)
            manifest.setdefault("failures", []).append(msg)
        finally:
            if xlsx.exists():
                xlsx.unlink()
    # Each project's combined session file, from the cohort files just written
    # (exporters.layout); then any left at the top by the old flat layout.
    layout.combine_all(out_dir, layout.SESSION_KINDS, {layout.project_of(c) for c in cohorts}, manifest)
    layout.archive_flat_layout(out_dir)
    layout.archive_moved_project_folders(out_dir)


def _mtime(p: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return None


# ONE WRITER AT A TIME. Three things rewrite this folder: the nightly snapshot
# job, the "Refresh exports now" button and the command line (and the per-reach
# exporter's own command, ``mousedb export-odc-reaches``). They share one
# staging folder beside the snapshot, which each run empties when it starts,
# and fixed ".tmp" names for the files they copy into place. Two runs at once
# therefore delete each other's half-written files, or publish each other's
# half-written files under the final name -- and nothing says so. A lockfile
# with this name, in the same folder as every other mousedb task lock
# (task_mutex.default_lock_dir), lets only one run write at a time.
EXPORTS_LOCK = "current-exports"
EXPORTS_LOCK_LABEL = "the current-exports lock"

BUSY_MESSAGE = ("the current exports are already being written by another refresh "
                "(the nightly job, the Where Is My Data button or a command) -- "
                "try again when it finishes; nothing was changed")


class ExportsBusy(RuntimeError):
    """Another refresh holds the exports lock and this caller asked not to wait.
    Nothing was changed."""


def _lock_dir() -> Path:
    """Where the exports lock lives: the folder every mousedb task lock uses, so
    every process on every computer pointed at this database sees it."""
    from ..task_mutex import default_lock_dir
    return default_lock_dir()


def hold_exports_lock(lock_wait_seconds: Optional[float] = None,
                      lock_dir: Optional[Path] = None):
    """Take the exports lock and return the held TaskMutex (release it when done).

    ``lock_wait_seconds`` None: wait as long as this process's wait budget
    allows (task_mutex.default_wait_seconds) -- right for the nightly job and
    the command line, where waiting turns an overlap into a short delay.
    0: do not wait; raise ExportsBusy at once -- right for a button, where a
    person is better told now than kept waiting behind an hour-long job.
    A wait that runs out raises task_mutex.LockWaitTimeout (holder named).
    A lock folder this login cannot write raises task_mutex.LockFolderNotWritable
    at once, waiting or not: it is not "busy", and waiting would not help."""
    from ..task_mutex import PENDING_DELETE_GRACE_SECONDS, TaskMutex
    # Labelled at construction, so a dead holder's lock broken while waiting
    # (task_mutex._record_break) is logged under this lock's name too.
    mutex = TaskMutex(EXPORTS_LOCK, base_dir=Path(lock_dir) if lock_dir else _lock_dir(),
                      label=EXPORTS_LOCK_LABEL)
    if lock_wait_seconds is not None and lock_wait_seconds <= 0:
        started = mutex._clock()
        while not mutex.try_acquire(heartbeat=True):
            # "Busy" only when there is a holder to name. Try again, briefly,
            # in the two cases where the lock was released a moment ago:
            #   * no lockfile at all;
            #   * creating the lockfile was just refused (try_acquire recorded
            #     the refusal in _refused_since) and the file left at the path
            #     cannot be read, so its holder is "unknown". On Windows that
            #     is a lockfile whose delete is still pending: it refuses both
            #     creation and reading for a moment after the previous refresh
            #     released it. Reading it as a holder told a person "already
            #     being written" just after the other refresh had finished.
            # A folder that refuses writes is raised by try_acquire
            # (LockFolderNotWritable) within the grace period, and a holder
            # that is really there is named at once -- so "already being
            # written" is said only while something holds the lock.
            state, who = mutex.holder()
            released_moments_ago = (
                state == "absent"
                or (state == "unknown" and mutex.path in mutex._refused_since))
            if (not released_moments_ago
                    or mutex._clock() - started > PENDING_DELETE_GRACE_SECONDS + 5):
                raise ExportsBusy("%s (holder: %s)" % (BUSY_MESSAGE, who))
            mutex._sleep(0.2)
        return mutex
    return mutex.acquire(wait_seconds=lock_wait_seconds,
                         waiting_for="another export refresh")


def refresh_current(snapshot_dir: Path = None, out_dir: Path = None,
                    db_ok: bool = False, lock_wait_seconds: Optional[float] = None,
                    lock_dir: Optional[Path] = None) -> dict:
    """Rewrite the current-exports folder. Returns the manifest.

    ``db_ok``: the caller vouches that reading connectome.db is safe right
    now (the watcher's own loop, or no watcher running). Without it the
    per-cohort ODC session files are left as they were and the manifest
    says so.

    Only one refresh writes at a time (EXPORTS_LOCK): ``lock_wait_seconds``
    says how long to wait for another one to finish (see hold_exports_lock;
    0 raises ExportsBusy at once), ``lock_dir`` where the lock is (tests)."""
    snapshot_dir = snapshot_dir or require("snapshot_dir")
    out_dir = out_dir or CURRENT_DIR or (require("mousedb_root") / "exports" / "current")
    snapshot_dir, out_dir = Path(snapshot_dir), Path(out_dir)
    # Taken BEFORE anything is read or emptied: the snapshot generation is then
    # judged after any run we waited for has finished, and the staging folder
    # is emptied only when no other run can be writing into it.
    mutex = hold_exports_lock(lock_wait_seconds, lock_dir)
    try:
        return _refresh_current_locked(snapshot_dir, out_dir, db_ok)
    finally:
        mutex.release()


def _refresh_current_locked(snapshot_dir: Path, out_dir: Path, db_ok: bool) -> dict:
    """refresh_current's work, run only while the exports lock is held."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # Every export is built from ONE snapshot generation (see
    # mousedb.snapshot_generation). A snapshot holding tables from two
    # refreshes is not exported at all -- the files already there stay, and
    # the caller is told -- because files built from it would join one night's
    # reaches with another night's animals and pellets.
    from .. import snapshot_generation
    gen_state, gen_stamp, gen_why = snapshot_generation.wait_while_swapping(snapshot_dir)
    if gen_state in ("broken", "mixed"):
        raise snapshot_generation.SnapshotNotConsistent(
            "current exports not rewritten: %s. The files in %s are still those of "
            "the last good refresh." % (gen_why, out_dir))
    generation = (gen_stamp or {}).get("generation") if gen_state == "complete" else None
    # Start from an empty staging folder: every file copied onto the share
    # this run was written this run (see _staging_dir).
    import shutil
    shutil.rmtree(_staging_dir(snapshot_dir), ignore_errors=True)
    _staging_dir(snapshot_dir)
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "snapshot_dir": str(snapshot_dir),
        "files": {}, "problems": [], "notes": [], "complete": True,
        "odc_sessions_refreshed": bool(db_ok),
        # Which snapshot refresh every file here was built from (None: a
        # snapshot written before refreshes were numbered).
        "snapshot_generation": generation,
    }
    # A part that FAILED makes the whole refresh incomplete: its files are
    # the previous run's (or missing), and "complete" is what the nightly job
    # and the Where Is My Data tab judge the exports by.
    for fn in (_reach_data_csv, _manual_scores_csv, _odc_reaches_csvs):
        try:
            fn(snapshot_dir, out_dir, manifest)
        except Exception as e:
            manifest["complete"] = False
            manifest["problems"].append("%s: %s: %s" % (fn.__name__, type(e).__name__, e))
    if db_ok:
        try:
            _odc_sessions_csvs(out_dir, manifest)
        except Exception as e:
            manifest["complete"] = False
            manifest["problems"].append("ODC sessions: %s: %s" % (type(e).__name__, e))
    else:
        manifest["problems"].append(
            "ODC_sessions_*.csv not refreshed this run (database not readable now); "
            "the files present are from the previous refresh")

    if manifest.get("failures"):
        manifest["complete"] = False

    # Did a new generation land while the files were being written? Then they
    # mix two refreshes: say so, loudly, rather than present them as current.
    after = snapshot_generation.state(snapshot_dir)
    if generation is not None and (after[0] != "complete"
                                   or (after[1] or {}).get("generation") != generation):
        manifest["complete"] = False
        manifest["problems"].append(
            "the snapshot was replaced while these exports were being written, so some "
            "files are from one refresh and some from the next -- write them again")

    for name, info in manifest["files"].items():
        if info.get("undocumented_columns"):
            manifest["complete"] = False
            manifest["problems"].append(
                "%s has %d column(s) with no data-dictionary entry: %s -- an ODC upload "
                "would fail" % (name, len(info["undocumented_columns"]),
                                ", ".join(info["undocumented_columns"][:12])))
    (out_dir / "README.txt").write_text(README, encoding="utf-8")
    (out_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, default=str),
                                          encoding="utf-8")
    return manifest


# The exit code of ``--no-wait`` when another refresh is writing: distinct from
# 1 (an export that ran and is incomplete) so a caller can say "busy" instead.
EXIT_BUSY = 3
# The exit code when this run WAITED for another refresh and the wait ran out
# (its budget used up, or the other refresh showed no progress for
# task_mutex.STALL_MINUTES). Nothing was changed; distinct from 1 and 3.
EXIT_LOCK_TIMEOUT = 4
# The exit code when the lock folder cannot be written by this login
# (task_mutex.LockFolderNotWritable): nobody is writing the exports, and running
# again will fail the same way until the folder's permissions are fixed.
EXIT_LOCK_FOLDER = 5


def lock_folder_message(e: Exception) -> str:
    """The plain line a person sees when the lock folder refuses writes."""
    return ("[!] the lock folder cannot be written by this login, so the exports were "
            "not rewritten. Nobody else is writing them; waiting or pressing again will "
            "not help. Nothing was changed. Details: %s" % e)


def lock_timeout_message(e: Exception) -> str:
    """The plain line a terminal user sees when the wait for the exports lock
    ran out (task_mutex.LockWaitTimeout), instead of a Python traceback."""
    return ("[!] another export refresh is still writing the current exports, and this "
            "run stopped waiting for it. Nothing was changed: the files are those of "
            "the last refresh that finished. Run it again once the other one is done. "
            "Details: %s" % e)


def main(argv=None) -> int:
    import argparse
    from ..task_mutex import LockFolderNotWritable, LockWaitTimeout
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    ap.add_argument("--out-dir", type=Path, default=CURRENT_DIR)
    ap.add_argument("--db-ok", action="store_true",
                    help="Also refresh the per-cohort ODC session files (reads "
                         "connectome.db -- only when no watcher is writing).")
    ap.add_argument("--no-wait", action="store_true",
                    help="If another refresh is writing the exports right now, stop at "
                         "once (exit code %d) instead of waiting for it to finish."
                         % EXIT_BUSY)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        m = refresh_current(args.snapshot_dir, args.out_dir, db_ok=args.db_ok,
                            lock_wait_seconds=0 if args.no_wait else None)
    except ExportsBusy as e:
        print("[!] %s" % e, flush=True)
        return EXIT_BUSY
    except LockWaitTimeout as e:
        print(lock_timeout_message(e), flush=True)
        return EXIT_LOCK_TIMEOUT
    except LockFolderNotWritable as e:
        print(lock_folder_message(e), flush=True)
        return EXIT_LOCK_FOLDER
    if args.json:
        print(json.dumps(m, indent=1, default=str))
    else:
        print("Current exports written to %s at %s" % (args.out_dir, m["generated_at"]))
        for name, info in m["files"].items():
            print("  %-36s %8s rows" % (name, info.get("rows", "-")))
        for p in m["problems"]:
            print("  [!] %s" % p)
        print("complete for ODC upload:", m["complete"])
    return 0 if m["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
