"""
Read-only bridge to MouseReach watcher.db for video pipeline status rollup.

This module reads the watcher's SQLite database to provide cross-database views:
"For animal CNT_01_15, how many videos are processed vs pending vs failed?"

RULES:
- NEVER writes to watcher.db (read-only)
- NEVER imports mousereach (reads config.json directly)
- Gracefully returns empty results if watcher.db not found
"""

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


# Pipeline states ordered by progress (for progress bars and sorting)
PIPELINE_STATES = [
    'discovered', 'validated', 'dlc_queued', 'dlc_running', 'dlc_complete',
    'processing', 'processed', 'archiving', 'archived',
]
ERROR_STATES = ['quarantined', 'failed']
ALL_STATES = PIPELINE_STATES + ERROR_STATES

# State display names and colors for GUI
STATE_DISPLAY = {
    'discovered':   {'label': 'Discovered',   'color': '#9E9E9E'},
    'validated':    {'label': 'Validated',     'color': '#2196F3'},
    'dlc_queued':   {'label': 'DLC Queued',    'color': '#FF9800'},
    'dlc_running':  {'label': 'DLC Running',   'color': '#FFC107'},
    'dlc_complete': {'label': 'DLC Done',      'color': '#8BC34A'},
    'processing':   {'label': 'Processing',    'color': '#009688'},
    'processed':    {'label': 'Processed',     'color': '#4CAF50'},
    'archiving':    {'label': 'Archiving',     'color': '#00BCD4'},
    'archived':     {'label': 'Archived',      'color': '#607D8B'},
    'quarantined':  {'label': 'Quarantined',   'color': '#FF5722'},
    'failed':       {'label': 'Failed',        'color': '#F44336'},
}

DONE_STATES = frozenset(['processed', 'archiving', 'archived'])


@dataclass
class WatcherStatus:
    """Watcher connection status."""
    available: bool
    db_path: Optional[Path]
    message: str


@dataclass
class PipelineSummary:
    """Overall pipeline summary."""
    total_videos: int = 0
    total_collages: int = 0
    by_state: Dict[str, int] = field(default_factory=dict)
    failed_count: int = 0
    quarantined_count: int = 0
    archived_count: int = 0
    fully_processed_pct: float = 0.0


@dataclass
class AnimalVideoSummary:
    """Video status rollup for one animal."""
    subject_id: str
    cohort_id: str
    total_videos: int = 0
    by_state: Dict[str, int] = field(default_factory=dict)
    failed_videos: int = 0
    latest_activity: Optional[str] = None


def find_watcher_db() -> WatcherStatus:
    """
    Locate watcher.db using discovery chain.

    Order:
    1. MOUSEDB_WATCHER_DB environment variable
    2. ~/.mousereach/config.json -> processing_root/watcher.db
    3. Hardcoded fallback path
    """
    # 1. Environment variable
    env_path = os.environ.get('MOUSEDB_WATCHER_DB')
    if env_path:
        p = Path(env_path)
        if p.exists():
            return WatcherStatus(True, p, f"Found via MOUSEDB_WATCHER_DB: {p}")
        return WatcherStatus(False, None,
                             f"MOUSEDB_WATCHER_DB set but file not found: {p}")

    # 2. Read MouseReach config.json (no mousereach import!)
    config_file = Path.home() / ".mousereach" / "config.json"
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
            processing_root = config.get('processing_root')
            if processing_root:
                p = Path(processing_root) / "watcher.db"
                if p.exists():
                    return WatcherStatus(True, p,
                                         f"Found via mousereach config: {p}")
                return WatcherStatus(
                    False, None,
                    f"MouseReach configured but watcher.db not found at {p}. "
                    f"The watcher may not have been started yet."
                )
        except Exception:
            pass

    # 3. Fallback
    fallback = Path("Y:/2_Connectome/Behavior/MouseReach_Pipeline/watcher.db")
    if fallback.exists():
        return WatcherStatus(True, fallback, f"Found at fallback path: {fallback}")

    return WatcherStatus(
        False, None,
        "watcher.db not found. Set MOUSEDB_WATCHER_DB or run mousereach-setup."
    )


def _animal_id_to_subject_id(animal_id: str) -> Optional[str]:
    """
    Convert watcher's animal_id format (CNT0115) to database format (CNT_01_15).

    Mirrors mousereach.sync.database.parse_subject_id without importing mousereach.
    """
    if not animal_id:
        return None

    # Already in database format?
    if re.match(r'^[A-Z]+_\d{2}_\d{2}$', animal_id):
        return animal_id

    # Compact format: CNT0115 -> CNT_01_15
    match = re.match(r'^([A-Za-z]+)(\d{2})(\d{2})$', animal_id)
    if match:
        experiment = match.group(1).upper()
        cohort = match.group(2)
        subject = match.group(3)
        return f"{experiment}_{cohort}_{subject}"

    return None


class WatcherBridge:
    """
    Read-only bridge to watcher.db.

    Usage:
        bridge = WatcherBridge()
        if bridge.is_available:
            summary = bridge.get_pipeline_summary()
            animal_data = bridge.get_animal_rollup()
    """

    def __init__(self, db_path: Optional[Path] = None):
        if db_path:
            self._status = WatcherStatus(
                db_path.exists(), db_path,
                f"Using explicit path: {db_path}"
            )
        else:
            self._status = find_watcher_db()

    @property
    def is_available(self) -> bool:
        return self._status.available

    @property
    def status(self) -> WatcherStatus:
        return self._status

    def _connect(self) -> sqlite3.Connection:
        """Open read-only connection to watcher.db."""
        if not self.is_available:
            raise RuntimeError("watcher.db not available")
        uri = f"file:{self._status.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def get_pipeline_summary(self) -> PipelineSummary:
        """Get overall pipeline summary (counts by state)."""
        summary = PipelineSummary()
        if not self.is_available:
            return summary

        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT state, COUNT(*) as cnt FROM videos GROUP BY state"
            ).fetchall()
            for row in rows:
                summary.by_state[row['state']] = row['cnt']
                summary.total_videos += row['cnt']

            summary.failed_count = summary.by_state.get('failed', 0)
            summary.quarantined_count = summary.by_state.get('quarantined', 0)
            summary.archived_count = summary.by_state.get('archived', 0)

            done = sum(summary.by_state.get(s, 0) for s in DONE_STATES)
            if summary.total_videos > 0:
                summary.fully_processed_pct = done / summary.total_videos * 100

            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM collages"
            ).fetchone()
            summary.total_collages = row['cnt'] if row else 0

            return summary
        finally:
            conn.close()

    def get_animal_rollup(self) -> List[AnimalVideoSummary]:
        """Get per-animal video status rollup."""
        if not self.is_available:
            return []

        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT animal_id, state, COUNT(*) as cnt,
                       MAX(updated_at) as latest
                FROM videos
                WHERE animal_id IS NOT NULL
                GROUP BY animal_id, state
            """).fetchall()

            animals: Dict[str, AnimalVideoSummary] = {}
            for row in rows:
                animal_id = row['animal_id']
                subject_id = _animal_id_to_subject_id(animal_id)
                if not subject_id:
                    continue

                if subject_id not in animals:
                    cohort_id = '_'.join(subject_id.split('_')[:2])
                    animals[subject_id] = AnimalVideoSummary(
                        subject_id=subject_id,
                        cohort_id=cohort_id,
                    )

                summary = animals[subject_id]
                summary.by_state[row['state']] = row['cnt']
                summary.total_videos += row['cnt']
                if row['state'] == 'failed':
                    summary.failed_videos += row['cnt']

                if row['latest']:
                    if (summary.latest_activity is None
                            or row['latest'] > summary.latest_activity):
                        summary.latest_activity = row['latest']

            return sorted(animals.values(), key=lambda a: a.subject_id)
        finally:
            conn.close()

    def get_cohort_rollup(self) -> Dict[str, dict]:
        """Get per-cohort aggregated status."""
        animal_rollup = self.get_animal_rollup()

        cohorts: Dict[str, dict] = {}
        for animal in animal_rollup:
            cid = animal.cohort_id
            if cid not in cohorts:
                cohorts[cid] = {
                    'cohort_id': cid,
                    'total_videos': 0,
                    'animals': 0,
                    'by_state': {},
                    'failed': 0,
                    'fully_processed': 0,
                }
            c = cohorts[cid]
            c['animals'] += 1
            c['total_videos'] += animal.total_videos
            c['failed'] += animal.failed_videos
            for state, count in animal.by_state.items():
                c['by_state'][state] = c['by_state'].get(state, 0) + count
                if state in DONE_STATES:
                    c['fully_processed'] += count

        return cohorts

    def get_videos_for_animal(self, animal_id: str) -> List[dict]:
        """Get all video records for a specific animal."""
        if not self.is_available:
            return []

        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT video_id, source_path, state, date, tray_type,
                       tray_position, error_message, error_count,
                       discovered_at, validated_at, dlc_started_at,
                       dlc_completed_at, processing_started_at,
                       processing_completed_at, archived_at, updated_at
                FROM videos
                WHERE animal_id = ?
                ORDER BY date DESC, video_id
            """, (animal_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_failed_videos(self) -> List[dict]:
        """Get all failed/quarantined videos with error info."""
        if not self.is_available:
            return []

        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT video_id, animal_id, state, error_message,
                       error_count, last_error_at, updated_at
                FROM videos
                WHERE state IN ('failed', 'quarantined')
                ORDER BY updated_at DESC
            """).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_recent_activity(self, limit: int = 50) -> List[dict]:
        """Get recent processing log entries."""
        if not self.is_available:
            return []

        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT video_id, step, status, message,
                       duration_seconds, machine, created_at
                FROM processing_log
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
