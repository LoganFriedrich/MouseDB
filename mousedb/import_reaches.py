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
     (and ``Processing`` with ``--include-processing``).
  3. Skips files whose content hash is already in the ledger
     (``<mousedb_root>/logs/reach_imports.json``); ``--all`` re-imports everything.
  4. For each file: the animal is CREATED from the video name if the tracking
     sheet has not named it yet (machine data never waits on hand data; the
     sheet import enriches the record later), old rows for that video are
     deleted, new rows inserted -- one transaction per video.
  5. Re-derives test_phase / phase_group for every cohort touched
     (``mousedb.backfill.backfill_phases``).

Usage:
    mousedb import-reaches                 # what is new or changed
    mousedb import-reaches --dry-run       # count only
    mousedb import-reaches --all           # ignore the ledger, re-import everything
    mousedb import-reaches --include-processing
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


@dataclass
class ImportResult:
    scanned: int = 0
    imported: int = 0
    skipped: int = 0
    errors: int = 0
    reaches: int = 0
    subjects_created: int = 0
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
    dirs = [root / "Analyzed"] + ([root / "Processing"] if include_processing else [])
    found: List[Path] = []
    for d in dirs:
        if d.is_dir():
            found.extend(p for p in d.rglob("*" + FEATURES_SUFFIX) if p.is_file())
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


def flatten(features_path: Path, subject_id: str, now: str) -> Tuple[str, List[dict]]:
    """(video_name, rows) -- one row per reach, exactly the old pusher's layout."""
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


def ensure_columns(conn: sqlite3.Connection) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(reach_data)")}
    if not have:
        raise RuntimeError("reach_data table does not exist -- run `mousedb init` first")
    for col, typ in MIGRATION_COLUMNS:
        if col not in have:
            conn.execute("ALTER TABLE reach_data ADD COLUMN %s %s" % (col, typ))


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
    video_name, rows = flatten(path, subject_id, now)
    if dry_run:
        return subject_id, len(rows), False
    cols = ', '.join(ALL_COLUMNS)
    params = ', '.join(':' + c for c in ALL_COLUMNS)
    with conn:  # one transaction per video: all rows or none
        created = ensure_subject(conn, subject_id, video_name)
        conn.execute("DELETE FROM reach_data WHERE video_name = ?", (video_name,))
        if rows:
            conn.executemany("INSERT INTO reach_data (%s) VALUES (%s)" % (cols, params), rows)
    return subject_id, len(rows), created


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
    todo = []
    for p in files:
        key = str(p.relative_to(root)).replace("\\", "/")
        st = p.stat()
        stamp = "%d:%d" % (st.st_size, int(st.st_mtime))
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
    log("import-reaches: %d features files under %s; %d to import, %d unchanged" %
        (len(files), root, len(todo), res.skipped))
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
    finally:
        conn.close()
    if not dry_run:
        save_ledger(ledger)  # also persists refreshed stamps for unchanged files
        if res.cohorts_touched:
            try:
                from .backfill import backfill_phases
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
    log("import-reaches: %d videos imported (%d reaches), %d subjects created, %d errors%s" % (
        res.imported, res.reaches, res.subjects_created, res.errors, " [dry run]" if dry_run else ""))
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
    args = ap.parse_args(argv)
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
