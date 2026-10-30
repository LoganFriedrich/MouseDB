"""mousedb import-reaches -- PULL MouseReach's per-video results into reach_data.

WHY THIS LIVES HERE AND NOT IN MOUSEREACH
-----------------------------------------
MouseReach produces complete, usable output on its own: one
``{video}_features.json`` per video (every reach, every kinematic feature,
the outcome it was joined to, and the segment context), filed under its
Analyzed tree. Putting those rows into a central database is an INTEGRATION
-- this tool's job, and this tool's schema (``reach_data`` in
``mousedb.schema``). Until 2026-08-28 the pipeline PUSHED rows into the
database from inside the watcher, which tied MouseReach to a database it did
not own and could not run without. Now mousedb pulls: it scans the configured
MouseReach pipeline folder, imports what is new or changed, and keeps its own
ledger. The flattening (one row per reach) is the same as the old pusher's, so
existing rows and existing analyses are unaffected.

WHAT IT DOES
  1. Refuses to write while a MouseReach watcher is running on this machine
     (the database is a plain SQLite file; concurrent writers over a network
     share corrupt it). ``--force`` overrides for a deliberate run.
  2. Lists every ``*_features.json`` under ``<mousereach_pipeline_root>/Analyzed``
     (and ``Processing`` with ``--include-processing``), never descending into
     ``Archive``, ``DLC Model*``, ``Multi-Animal``, ``Folder Template``,
     ``UNKNOWN`` or a folder starting ``.`` or ``_``: those hold files with the
     live names that are not live results (``mousedb.pipeline_tree``).
  3. Skips files whose content hash is already in the ledger
     (``<mousedb_root>/logs/reach_imports.json``); ``--all`` re-imports everything.
     A file that only MOVED folder keeps its ledger entry (``_moved_entries``),
     so reorganising the Analyzed folders re-imports nothing.
  4. For each file: the animal is CREATED from the video name if the tracking
     sheet has not named it yet (machine data never waits on hand data; the
     sheet import enriches the record later), old rows for that video are
     deleted, new rows inserted -- one transaction per video.
     The same transaction replaces the video's rows in ``video_pellets``: one
     per pellet the pipeline cut the video into, reached for or not, with its
     outcome. That is where "how many pellets were presented" is MEASURED (20
     unless something went wrong); reach_data cannot say, because a pellet
     nobody reached for has no reach row.
  5. Re-derives test_phase / phase_group for every cohort touched
     (``mousedb.backfill.backfill_phases``).
  6. Takes out the rows of every video that has gone BACK TO REVIEW: its
     results file sits in a review queue (``Processing/Review``) and no longer
     in Analyzed, or a NEWER run of it waits there while the old one is still
     filed. See ``videos_back_in_review`` and ``withdraw_videos_back_in_review``.

Usage:
    mousedb import-reaches                 # what is new or changed
    mousedb import-reaches --dry-run       # count only
    mousedb import-reaches --all           # ignore the ledger, re-import everything
    mousedb import-reaches --include-processing
    mousedb import-reaches --pellets-only  # (re)fill video_pellets from every file
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import require
from .pipeline_tree import iter_files

logger = logging.getLogger(__name__)

FEATURES_SUFFIX = "_features.json"
LEDGER_NAME = "reach_imports.json"
AUTO_CREATED_MARK = "auto-created from video"

# Columns read straight from each reach dict. segment_num is NOT here on
# purpose: the extractor leaves it 0 on every reach; it is taken from the
# enclosing segment, the only place it is correct.
REACH_JSON_COLUMNS = [
    'reach_id', 'reach_num',
    'outcome', 'causal_reach', 'interaction_frame', 'distance_to_interaction',
    'is_first_reach', 'is_last_reach', 'n_reaches_in_segment',
    'start_frame', 'apex_frame', 'end_frame', 'duration_frames',
    'max_extent_pixels', 'max_extent_ruler', 'max_extent_mm',
    'velocity_at_apex_px_per_frame', 'velocity_at_apex_mm_per_sec',
    'peak_velocity_px_per_frame', 'mean_velocity_px_per_frame',
    'trajectory_straightness', 'trajectory_smoothness',
    'hand_angle_at_apex_deg', 'hand_rotation_total_deg',
    'grasp_aperture_max_mm', 'grasp_aperture_at_contact_mm',
    'head_width_at_apex_mm', 'nose_to_slit_at_apex_mm',
    'head_angle_at_apex_deg', 'head_angle_change_deg',
    'apex_distance_to_pellet_mm', 'lateral_deviation_mm',
    'mean_likelihood', 'frames_low_confidence', 'tracking_quality_score',
    'flagged_for_review', 'flag_reason',
    'outcome_source', 'reviewed_by', 'algo_outcome', 'algo_causal_reach_id',
]
BOOL_COLUMNS = {'causal_reach', 'is_first_reach', 'is_last_reach', 'flagged_for_review'}
SEGMENT_COLUMNS = ['segment_outcome', 'segment_outcome_confidence', 'segment_outcome_flagged',
                   'attention_score', 'pellet_position_idealness']
PROVENANCE_COLUMNS = ['processed_by', 'mousereach_version', 'dlc_scorer', 'segmenter_version',
                      'reach_detector_version', 'outcome_detector_version']
ALL_COLUMNS = (['subject_id', 'video_name', 'session_date', 'tray_type', 'run_number', 'segment_num']
               + REACH_JSON_COLUMNS + SEGMENT_COLUMNS
               + ['source_file', 'extractor_version', 'imported_at', 'extended_features']
               + PROVENANCE_COLUMNS)
# Columns older databases may lack (added with ALTER TABLE, never destructive)
MIGRATION_COLUMNS = [(c, 'TEXT') for c in PROVENANCE_COLUMNS + ['outcome_source', 'reviewed_by',
                     'algo_outcome', 'extended_features', 'test_phase', 'phase_group']] + \
                    [('algo_causal_reach_id', 'INTEGER')]

# Every pellet the pipeline cut a video into, one row each -- INCLUDING the
# pellets nobody reached for, which reach_data cannot hold (it has one row per
# reach, so a pellet with no reach leaves no trace there).
#
# WHY THIS TABLE EXISTS (2026-10-16): the exports counted "pellets presented"
# and the per-video outcome totals from reach rows, so an ignored pellet was
# invisible: the count came out below 20 in over half the sessions, and a
# displaced or retrieved pellet whose reach went undetected was never counted.
# A tray holds 20 pellets; how many the pipeline actually found in the video is
# a measurement, and a number other than 20 is an error worth seeing. This
# table is where that measurement lives. Created on first use, never altered.
VIDEO_PELLETS_DDL = """
CREATE TABLE IF NOT EXISTS video_pellets (
    video_name         TEXT    NOT NULL,
    segment_num        INTEGER NOT NULL,
    subject_id         TEXT    NOT NULL,
    session_date       TEXT,
    tray_type          TEXT,
    run_number         INTEGER,
    outcome            TEXT,
    outcome_confidence REAL,
    outcome_flagged    INTEGER,
    n_reaches          INTEGER,
    source_file        TEXT,
    imported_at        TEXT,
    PRIMARY KEY (video_name, segment_num)
)"""
VIDEO_PELLET_COLUMNS = ['video_name', 'segment_num', 'subject_id', 'session_date', 'tray_type',
                        'run_number', 'outcome', 'outcome_confidence', 'outcome_flagged',
                        'n_reaches', 'source_file', 'imported_at']


@dataclass
class ImportResult:
    scanned: int = 0
    imported: int = 0
    skipped: int = 0
    errors: int = 0
    reaches: int = 0
    subjects_created: int = 0
    withdrawn: int = 0            # videos taken out because they went back to review
    moved: int = 0                # results files found under a new folder, not re-imported
    withdrawn_reaches: int = 0
    cohorts_touched: set = field(default_factory=set)
    messages: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["cohorts_touched"] = sorted(self.cohorts_touched)
        return d


# ---------------------------------------------------------------------------
# video-name parsing (a property of the pipeline's file naming, not of a lab)
# ---------------------------------------------------------------------------

_ANIMAL_TOKEN = re.compile(r"^([A-Za-z]+)(\d{2})(\d{2})$")


def parse_subject_id(video_name: str) -> Optional[str]:
    """``20250624_CNT0115_P2`` -> ``CNT_01_15``; ``20220811_ASPA1011_P3`` -> ``ASPA_10_11``.
    The animal token is ``{project letters}{cohort 2d}{subject 2d}``; every
    project is handled the same way (hardcoding one prefix once silently
    dropped a whole project's results)."""
    name = video_name
    for suffix in ('_features', '_reaches', '_pellet_outcomes', '_segments'):
        name = name.replace(suffix, '')
    m = re.search(r'([A-Za-z]+_\d{2}_\d{2})', name)
    if m:
        return m.group(1)
    for token in name.split('_'):
        m = _ANIMAL_TOKEN.match(token)
        if m:
            return "%s_%s_%s" % (m.group(1).upper(), m.group(2), m.group(3))
    return None


def parse_video_metadata(video_name: str) -> Dict[str, Any]:
    """session_date (YYYY-MM-DD), tray_type (letters), run_number (int)."""
    out = {'session_date': None, 'tray_type': None, 'run_number': None}
    name = video_name.replace('_features', '')
    m = re.match(r'(\d{8})_', name)
    if m:
        d = m.group(1)
        out['session_date'] = "%s-%s-%s" % (d[:4], d[4:6], d[6:8])
    m = re.search(r'[A-Za-z]+\d{4}_([A-Za-z]+)(\d+)$', name)
    if m:
        out['tray_type'] = m.group(1).upper()
        try:
            out['run_number'] = int(m.group(2))
        except ValueError:
            pass
    return out


def _moved_entries(ledger: dict, root: Path, files: List[Path]) -> Dict[str, str]:
    """{new key: old key} for results files that MOVED since the last import.

    The ledger is keyed by each file's path under the pipeline folder. When the
    finished-work folders are reorganised (2026-10-30: project folders grouped
    under their grant), every file gets a new path, and without this each one
    would be hashed and imported again -- every reach of every moved cohort
    rewritten over the share, the multi-hour import that held the database
    lock on 2026-10-11. A file counts as moved when its new path is not in the
    ledger, exactly one ledger entry has the same file name, and that entry's
    old path no longer exists. Its content is still checked as for any file:
    the moved entry carries its stamp and hash, so an unchanged file is skipped
    and a changed one is imported."""
    listed = {str(p.relative_to(root)).replace("\\", "/") for p in files}
    by_name: Dict[str, List[str]] = {}
    for key in ledger:
        if key not in listed:
            by_name.setdefault(key.rsplit("/", 1)[-1], []).append(key)
    out = {}
    for key in listed:
        if key in ledger:
            continue
        olds = by_name.get(key.rsplit("/", 1)[-1], [])
        if len(olds) == 1 and not (root / olds[0]).exists():
            out[key] = olds[0]
    return out


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 16), b''):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# discovery + ledger
# ---------------------------------------------------------------------------

def find_features_files(root: Path, include_processing: bool = False) -> List[Path]:
    # A pruned walk, never rglob. Superseded generations (Analyzed/Archive),
    # pose-only DLC Model folders and scratch copies keep the live file names;
    # importing one would DELETE the video's live rows and insert superseded
    # kinematics in their place (mousedb.pipeline_tree says which folders).
    dirs = [root / "Analyzed"] + ([root / "Processing"] if include_processing else [])
    found: List[Path] = []
    for d in dirs:
        found.extend(iter_files(d, "*" + FEATURES_SUFFIX))
    return sorted(set(found))


def ledger_path() -> Path:
    return require("mousedb_root") / "logs" / LEDGER_NAME


def load_ledger() -> Dict[str, str]:
    p = ledger_path()
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("ledger %s unreadable; starting a new one", p)
    return {}


def save_ledger(ledger: Dict[str, str]) -> None:
    p = ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, indent=1, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# database side
# ---------------------------------------------------------------------------

def _load_provenance(features_path: Path, video_name: str) -> dict:
    out = {c: None for c in PROVENANCE_COLUMNS}
    mp = features_path.parent / ("%s_processing_manifest.json" % video_name)
    if not mp.is_file():
        return out
    try:
        m = json.loads(mp.read_text(encoding="utf-8"))
        pv = m.get('pipeline_versions', {}) or {}
        out.update({
            'processed_by': m.get('processed_by'),
            'mousereach_version': pv.get('mousereach'),
            'dlc_scorer': (m.get('dlc_model', {}) or {}).get('dlc_scorer'),
            'segmenter_version': pv.get('segmenter'),
            'reach_detector_version': pv.get('reach_detector'),
            'outcome_detector_version': pv.get('outcome_detector'),
        })
    except Exception:
        pass
    return out


# Easy and Flat: pellets presented but not tracked (pellet_rows; the exports
# leave their pellet and outcome counts blank, exporters/odc_reaches).
OUTCOME_FREE_TRAYS = ("E", "F")


def pellet_rows(data: dict, video_name: str, subject_id: str, now: str,
                source_file: str) -> List[dict]:
    """One row per pellet (segment) of the video, whether or not it was reached for.

    None for an Easy or Flat tray video. WHY (lab decision, 2026-10-23): on
    those trays pellets are presented but not tracked -- the video is one
    whole-session span, not cut per pellet, and no outcome is computed -- so a
    pellet row would claim "1 pellet presented, 0 retrieved" for a session
    whose pellets nobody counted. Their reaches and attention still import.
    """
    meta = parse_video_metadata(video_name)
    if str(meta.get('tray_type') or '').upper() in OUTCOME_FREE_TRAYS:
        return []
    rows = []
    for seg in data.get('segments', []):
        if seg.get('segment_num') is None:
            continue
        reaches = seg.get('reaches')
        rows.append({
            'video_name': video_name, 'segment_num': int(seg['segment_num']),
            'subject_id': subject_id, 'session_date': meta['session_date'] or '',
            'tray_type': meta['tray_type'], 'run_number': meta['run_number'],
            'outcome': seg.get('outcome'), 'outcome_confidence': seg.get('outcome_confidence'),
            'outcome_flagged': 1 if seg.get('outcome_flagged') else 0,
            'n_reaches': len(reaches) if isinstance(reaches, list) else seg.get('n_reaches'),
            'source_file': source_file, 'imported_at': now,
        })
    return rows


def flatten(features_path: Path, subject_id: str, now: str,
            data: Optional[dict] = None) -> Tuple[str, List[dict]]:
    """(video_name, rows) -- one row per reach, exactly the old pusher's layout.
    ``data`` is the already-parsed file, when the caller has it."""
    if data is None:
        data = json.loads(features_path.read_text(encoding="utf-8"))
    video_name = data.get('video_name', features_path.stem.replace('_features', ''))
    meta = parse_video_metadata(video_name)
    prov = _load_provenance(features_path, video_name)
    extractor_version = data.get('extractor_version', 'unknown')
    rows = []
    for seg in data.get('segments', []):
        seg_ctx = {
            'segment_num': seg.get('segment_num'),
            'segment_outcome': seg.get('outcome'),
            'segment_outcome_confidence': seg.get('outcome_confidence'),
            'segment_outcome_flagged': 1 if seg.get('outcome_flagged') else 0,
            'attention_score': seg.get('attention_score'),
            'pellet_position_idealness': seg.get('pellet_position_idealness'),
        }
        for reach in seg.get('reaches', []):
            row = {'subject_id': subject_id, 'video_name': video_name,
                   'session_date': meta['session_date'] or '',
                   'tray_type': meta['tray_type'], 'run_number': meta['run_number']}
            for c in REACH_JSON_COLUMNS:
                v = reach.get(c)
                if c in BOOL_COLUMNS and v is not None:
                    v = 1 if v else 0
                row[c] = v
            row['extended_features'] = json.dumps(reach.get('extended') or {})
            row.update(seg_ctx)
            row['source_file'] = features_path.name
            row['extractor_version'] = extractor_version
            row['imported_at'] = now
            row.update(prov)
            rows.append(row)
    return video_name, rows


REACH_SUBJECT_INDEX_DDL = ("CREATE INDEX IF NOT EXISTS ix_reach_data_subject_session "
                           "ON reach_data (subject_id, session_date)")


def ensure_columns(conn: sqlite3.Connection) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(reach_data)")}
    if not have:
        raise RuntimeError("reach_data table does not exist -- run `mousedb init` first")
    for col, typ in MIGRATION_COLUMNS:
        if col not in have:
            conn.execute("ALTER TABLE reach_data ADD COLUMN %s %s" % (col, typ))
    conn.execute(VIDEO_PELLETS_DDL)
    # The phase re-labelling after every import selects one cohort's rows by
    # subject_id; with no index on it SQLite read the WHOLE table once per
    # touched cohort (hours over the share, 2026-10-15/16). Created here, under
    # the database lock this import already holds, so an existing database gets
    # it on the next nightly run; a new database gets it from schema.py.
    conn.execute(REACH_SUBJECT_INDEX_DDL)
    # The contact columns' current definitions (schema.py says why), applied
    # the same way: instant, and a no-op once current.
    from .migrations import bring_contact_columns_current
    bring_contact_columns_current(conn)


def write_pellets(conn: sqlite3.Connection, video_name: str, rows: List[dict]) -> None:
    """Replace one video's pellet rows. Call inside the video's transaction."""
    conn.execute("DELETE FROM video_pellets WHERE video_name = ?", (video_name,))
    if rows:
        conn.executemany("INSERT INTO video_pellets (%s) VALUES (%s)" % (
            ', '.join(VIDEO_PELLET_COLUMNS), ', '.join(':' + c for c in VIDEO_PELLET_COLUMNS)), rows)


def ensure_subject(conn: sqlite3.Connection, subject_id: str, video_name: str) -> bool:
    """Create project/cohort/subject from the video name if absent. Returns
    True if the subject row was created now. WHY: the sheet is authoritative
    but LATE; the video exists today. Rows created here carry a note starting
    with AUTO_CREATED_MARK so the sheet import may overwrite the placeholders."""
    parts = subject_id.split('_')
    if len(parts) != 3:
        raise ValueError("cannot derive project/cohort from %r" % subject_id)
    project, cohort = parts[0], "%s_%s" % (parts[0], parts[1])
    start = parse_video_metadata(video_name).get('session_date') or datetime.now().date().isoformat()
    note = "%s %s by mousedb import-reaches; tracking-sheet import fills in the rest" % (AUTO_CREATED_MARK, video_name)
    now = datetime.now().isoformat(sep=' ')
    conn.execute("INSERT OR IGNORE INTO projects (project_code, project_name, created_at) VALUES (?, ?, ?)",
                 (project, project, now))
    conn.execute("INSERT OR IGNORE INTO cohorts (cohort_id, project_code, start_date, notes, is_archived, created_at) "
                 "VALUES (?, ?, ?, ?, 0, ?)", (cohort, project, start, note, now))
    cur = conn.execute("INSERT OR IGNORE INTO subjects (subject_id, cohort_id, notes, is_active, created_at) "
                       "VALUES (?, ?, ?, 1, ?)", (subject_id, cohort, note, now))
    return cur.rowcount > 0


def import_file(conn: sqlite3.Connection, path: Path, dry_run: bool = False) -> Tuple[str, int, bool]:
    """Import one features file inside one transaction. Returns
    (subject_id, n_rows, subject_created)."""
    subject_id = parse_subject_id(path.stem)
    if subject_id is None:
        raise ValueError("no animal id in file name")
    now = datetime.now().isoformat()
    data = json.loads(path.read_text(encoding="utf-8"))
    video_name, rows = flatten(path, subject_id, now, data=data)
    pellets = pellet_rows(data, video_name, subject_id, now, path.name)
    if dry_run:
        return subject_id, len(rows), False
    cols = ', '.join(ALL_COLUMNS)
    params = ', '.join(':' + c for c in ALL_COLUMNS)
    with conn:  # one transaction per video: its reaches and its pellets, all or none
        created = ensure_subject(conn, subject_id, video_name)
        conn.execute("DELETE FROM reach_data WHERE video_name = ?", (video_name,))
        if rows:
            conn.executemany("INSERT INTO reach_data (%s) VALUES (%s)" % (cols, params), rows)
        write_pellets(conn, video_name, pellets)
    return subject_id, len(rows), created


def backfill_video_pellets(root: Optional[Path] = None, include_processing: bool = False,
                           log=print) -> Dict[str, Any]:
    """Fill video_pellets for every live features file, touching nothing else.

    For videos imported before the table existed: the ledger skips an unchanged
    file, so the nightly import would never revisit them. Reads every features
    file (outside the database lock -- it is all network reads), then writes the
    pellet rows in one transaction under the lock. reach_data, subjects and the
    ledger are not touched. Safe to re-run: each video's rows are replaced.
    """
    root = Path(root) if root else require("mousereach_pipeline_root")
    files = find_features_files(root, include_processing)
    now = datetime.now().isoformat()
    by_video: Dict[str, List[dict]] = {}
    unreadable: List[str] = []
    for p in files:
        subject_id = parse_subject_id(p.stem)
        if subject_id is None:
            unreadable.append(p.name)
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable.append(p.name)
            continue
        video_name = data.get('video_name', p.stem.replace('_features', ''))
        by_video[video_name] = pellet_rows(data, video_name, subject_id, now, p.name)
    log("backfill: %d features files read, %d videos, %d unreadable" % (
        len(files), len(by_video), len(unreadable)))
    from .task_mutex import hold
    with hold(log=log, waiting_for="another database job"):
        conn = sqlite3.connect(str(require("db_path")), timeout=600)
        try:
            with conn:
                conn.execute(VIDEO_PELLETS_DDL)
                for video_name, rows in by_video.items():
                    write_pellets(conn, video_name, rows)
        finally:
            conn.close()
    counts: Dict[int, int] = {}
    for rows in by_video.values():
        counts[len(rows)] = counts.get(len(rows), 0) + 1
    log("backfill: pellets per video -> %s" % ", ".join(
        "%d pellets: %d videos" % kv for kv in sorted(counts.items())))
    return {"videos": len(by_video), "pellets_per_video": counts, "unreadable": unreadable}


# ---------------------------------------------------------------------------
# videos that went back to review
# ---------------------------------------------------------------------------

WITHDRAWALS_DIR = "reach_withdrawals"


def videos_back_in_review(root: Path, filed: Dict[str, Path]) -> Dict[str, Path]:
    """{video name: its results file in a review queue} for every video whose
    ``*_features.json`` sits under ``<pipeline>/Processing/Review`` and that is
    either not filed, or filed with an OLDER run than the one in review.
    ``filed`` maps each name found in Analyzed to its results file there.

    A newer run in review: the pipeline re-runs a filed video without touching
    the filed copy (it is kept until the new run is filed, then archived), so a
    video can sit in both places. While the newer run waits for a person the
    video is not done -- on 2026-10-26 the old filed copies of all 23 such
    videos had pellets nobody decided or cuts a reviewer had since sent to deep
    review -- so it leaves the database like any other video sent back. A copy
    in a queue that is OLDER than the filed one is a leftover and changes
    nothing. Which run is newer comes from each run's own record, the
    processing manifest's created_at (``_run_time``), not from file times: a
    later backfill rewrote filed results files and made a March run look newer
    than an August one.

    Only positive evidence counts. A video merely missing from Analyzed -- on
    its way back after review, being renamed, or a listing that failed -- is
    never taken out: a failed listing cannot put files into a review queue."""
    out: Dict[str, Path] = {}
    for p in iter_files(root / "Processing" / "Review", "*" + FEATURES_SUFFIX):
        name = p.name[:-len(FEATURES_SUFFIX)]
        here = filed.get(name)
        if here is None:
            out.setdefault(name, p)
            continue
        queued_at, filed_at = _run_time(p), _run_time(here)
        if queued_at is None or filed_at is None:
            continue                    # moved mid-run: the next import decides
        if queued_at >= filed_at:
            out.setdefault(name, p)
    return out


def _run_time(features: Path) -> Optional[float]:
    """When the run that wrote ``features`` was made: its processing manifest's
    created_at, else (no manifest, or one that cannot be read) the results
    file's modification time. None when neither can be read."""
    manifest = features.with_name(features.name[:-len(FEATURES_SUFFIX)] + "_processing_manifest.json")
    try:
        made = json.loads(manifest.read_text(encoding="utf-8")).get("created_at")
        return datetime.fromisoformat(str(made)).timestamp()
    except Exception:
        pass
    try:
        return features.stat().st_mtime
    except OSError:
        return None


def _copy_rows(conn: sqlite3.Connection, table: str, video_name: str, dest: Path) -> int:
    """Write one video's rows of ``table`` to ``dest`` (CSV); the row count."""
    import csv
    cur = conn.execute("SELECT * FROM %s WHERE video_name = ?" % table, (video_name,))
    rows = cur.fetchall()
    if rows:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([d[0] for d in cur.description])
            w.writerows(rows)
    return len(rows)


def withdraw_videos_back_in_review(conn: sqlite3.Connection, root: Path, filed: Dict[str, Path],
                                   ledger: Dict[str, Any], backup_root: Path,
                                   dry_run: bool = False, log=print) -> Tuple[int, int, List[str]]:
    """Take out the rows of every video that went back to review.

    WHY: filing means done. The database holds a video's reaches because the
    video was filed, and it is sent back to a review queue only when something
    in it is not done after all (a pellet nobody answered, a disagreement with
    the hand scores, an "are you sure?"). Until 2026-10-26 its rows stayed --
    with the answers it had before it went back -- and went out in every export
    as if finished: 286 videos and 44,178 reach rows that day. Now they leave
    the database until the video is filed again.

    HOW: each such video's rows in reach_data and video_pellets are first
    copied to ``<backup_root>/<video>_reach_data.csv`` and
    ``_video_pellets.csv``, then deleted, one transaction per video. Its ledger
    entries are dropped too, so when it is filed again its file is imported
    even if nothing in it changed. Returns (videos, reach rows, messages)."""
    videos = withdrawn = 0
    messages: List[str] = []
    for name, where in sorted(videos_back_in_review(root, filed).items()):
        n = conn.execute("SELECT COUNT(*) FROM reach_data WHERE video_name = ?",
                         (name,)).fetchone()[0]
        p = conn.execute("SELECT COUNT(*) FROM video_pellets WHERE video_name = ?",
                         (name,)).fetchone()[0]
        if not n and not p:
            continue
        queue = where.relative_to(root / "Processing" / "Review").parts[0]
        if not dry_run:
            with conn:
                _copy_rows(conn, "reach_data", name, backup_root / (name + "_reach_data.csv"))
                _copy_rows(conn, "video_pellets", name, backup_root / (name + "_video_pellets.csv"))
                conn.execute("DELETE FROM reach_data WHERE video_name = ?", (name,))
                conn.execute("DELETE FROM video_pellets WHERE video_name = ?", (name,))
            for key in [k for k in ledger if k.rsplit("/", 1)[-1] == name + FEATURES_SUFFIX]:
                del ledger[key]
        videos += 1
        withdrawn += n
        line = ("%s: %s the %s queue, so not done -- %d reach rows taken out until it "
                "is filed again%s" % (name, "a newer run is in" if name in filed else "back in",
                                      queue, n, " [dry run]" if dry_run else ""))
        messages.append(line)
        log("  [withdrawn] " + line)
    return videos, withdrawn, messages


def watcher_running() -> bool:
    """Kept for compatibility; the run() guard uses watcher_blocks_db (a watcher
    that cannot write the central database must not stall imports)."""
    try:
        from .bench_scan import watcher_running as _wr
        return bool(_wr())
    except Exception:
        return False


def watcher_blocks_db() -> bool:
    try:
        from .bench_scan import watcher_blocks_db as _wb
        return bool(_wb())
    except Exception:
        return False


def run(all_files: bool = False, dry_run: bool = False, include_processing: bool = False,
        limit: Optional[int] = None, force: bool = False, log=print) -> ImportResult:
    res = ImportResult()
    root = require("mousereach_pipeline_root")
    db = require("db_path")
    if not db.is_file():
        raise FileNotFoundError("database not found at %s (run `mousedb init`)" % db)
    if not dry_run and not force and watcher_blocks_db():
        raise RuntimeError("a MouseReach watcher that can WRITE the central database is running; it must not be "
                           "written concurrently over the share. Stop it, or pass --force for a deliberate run.")
    files = find_features_files(root, include_processing)
    res.scanned = len(files)
    ledger = load_ledger()
    moved_from = _moved_entries(ledger, root, files) if not all_files else {}
    res.moved = len(moved_from)
    todo = []
    for p in files:
        key = str(p.relative_to(root)).replace("\\", "/")
        st = p.stat()
        stamp = "%d:%d" % (st.st_size, int(st.st_mtime))
        if key in moved_from:
            # Same file, new folder: its entry moves with it (see _moved_entries).
            ledger[key] = ledger.pop(moved_from[key])
        prev = ledger.get(key)
        # Fast path: same size and modification time as last import -> unchanged,
        # without reading the file (these live on a network share; hashing
        # thousands of them every run took minutes). A changed stamp is
        # confirmed by content hash so a touch without a change is not re-imported.
        if not all_files and isinstance(prev, dict) and prev.get("stamp") == stamp:
            res.skipped += 1
            continue
        h = file_hash(p)
        if not all_files and isinstance(prev, dict) and prev.get("hash") == h:
            ledger[key] = {"hash": h, "stamp": stamp}
            res.skipped += 1
            continue
        todo.append((p, key, {"hash": h, "stamp": stamp}))
    if limit:
        todo = todo[:limit]
    log("import-reaches: %d features files under %s; %d to import, %d unchanged%s" %
        (len(files), root, len(todo), res.skipped,
         "" if not res.moved else " (%d of them moved folders since the last import)" % res.moved))
    # The db phase runs under the cross-task mutex (see task_mutex.py): this
    # import overlapping the snapshot's long reads put the db in PENDING
    # starvation for 22 minutes on 2026-09-02 -- neither task was slow at the
    # db, the OVERLAP was the outage. Enumeration above stays outside the
    # lock (it is pure filesystem and can take many minutes over the share).
    from .task_mutex import hold
    with hold(log=log, waiting_for="the snapshot or another import"):
        conn = sqlite3.connect(str(db), timeout=600)
        try:
            if not dry_run:
                with conn:
                    ensure_columns(conn)
            for p, key, h in todo:
                try:
                    subject_id, n, created = import_file(conn, p, dry_run=dry_run)
                    res.imported += 1
                    res.reaches += n
                    res.subjects_created += int(created)
                    res.cohorts_touched.add("_".join(subject_id.split("_")[:2]))
                    if not dry_run:
                        ledger[key] = h
                except FileNotFoundError:
                    # The file existed when we listed the tree and is gone now:
                    # the disagreement router (or review-return) moved its bundle
                    # into a queue mid-run. That is normal traffic, not an import
                    # failure -- the file will be found again wherever it lands.
                    # (First seen 2026-09-01 13:20; it failed the whole run.)
                    res.skipped += 1
                    res.messages.append("%s: vanished mid-run (moved to a review queue?) -- skipped" % p.name)
                    log("  [skip] %s vanished mid-run (moved to a review queue?)" % p.name)
                except Exception as e:
                    res.errors += 1
                    res.messages.append("%s: %s" % (p.name, e))
                    log("  [FAIL] %s: %s" % (p.name, e))
            # Videos that went back to review leave the database until filed
            # again. Never on a listing that found nothing filed at all: that is
            # a broken listing, not an empty pipeline.
            if files:
                backup = (require("mousedb_root") / "_archived" / WITHDRAWALS_DIR
                          / datetime.now().strftime("%Y-%m-%d_%H%M%S"))
                filed = {f.name[:-len(FEATURES_SUFFIX)]: f for f in files}
                v, r, msgs = withdraw_videos_back_in_review(
                    conn, root, filed, ledger, backup, dry_run=dry_run, log=log)
                res.withdrawn, res.withdrawn_reaches = v, r
                res.messages.extend(msgs)
            else:
                res.messages.append("no filed results found; nothing taken out")
        finally:
            conn.close()
        if not dry_run:
            save_ledger(ledger)  # also persists refreshed stamps for unchanged files
            if res.cohorts_touched:
                try:
                    from .backfill import backfill_phases
                    from .prefetch import warmed
                    # Re-labelling reads the whole reach table once per cohort.
                    # Read the file sequentially first and hold it open for all
                    # the cohorts, so those scans run from memory: 3 hours cold
                    # against seconds warm on a network share (2026-10-16).
                    with warmed(db, log=log):
                        for cohort in sorted(res.cohorts_touched):
                            backfill_phases(cohort_id=cohort)
                except Exception as e:
                    res.messages.append("phase assignment: %s" % e)
                    log("  [!] phase assignment failed: %s" % e)
                    # Rows landed without test_phase/phase_group. That is a
                    # failed import for anyone reading the data, so the job must
                    # exit nonzero -- it was exit 0, and the scheduler showed
                    # green through nine straight hourly failures (2026-09-01).
                    res.errors += 1
    log("import-reaches: %d videos imported (%d reaches), %d subjects created, %d taken out "
        "(back in review: %d reaches), %d errors%s" % (
            res.imported, res.reaches, res.subjects_created, res.withdrawn,
            res.withdrawn_reaches, res.errors, " [dry run]" if dry_run else ""))
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="Ignore the ledger; re-import every file")
    ap.add_argument("--dry-run", action="store_true", help="Count what would be imported; write nothing")
    ap.add_argument("--include-processing", action="store_true",
                    help="Also scan the pipeline's Processing folder (videos not yet archived)")
    ap.add_argument("--limit", type=int, help="Import at most this many files (testing)")
    ap.add_argument("--force", action="store_true", help="Write even if a MouseReach watcher is running")
    ap.add_argument("--json", action="store_true", help="Machine-readable summary")
    ap.add_argument("--pellets-only", action="store_true",
                    help="Only (re)fill the per-pellet table (video_pellets) from every features "
                         "file; reaches, animals and the ledger are not touched")
    args = ap.parse_args(argv)
    if args.pellets_only:
        try:
            if not args.force and watcher_blocks_db():
                raise RuntimeError("a MouseReach watcher that can WRITE the central database is running")
            backfill_video_pellets(include_processing=args.include_processing)
        except Exception as e:
            print("[FAIL] %s" % e)
            return 1
        return 0
    try:
        res = run(all_files=args.all, dry_run=args.dry_run, include_processing=args.include_processing,
                  limit=args.limit, force=args.force, log=(lambda *_: None) if args.json else print)
    except Exception as e:
        print("[FAIL] %s" % e)
        return 1
    if args.json:
        print(json.dumps(res.as_dict(), indent=1))
    return 1 if res.errors else 0


if __name__ == "__main__":
    sys.exit(main())
