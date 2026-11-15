"""
Read-only bridge to MouseReach watcher.db for video pipeline status rollup.

This module reads the watcher's SQLite database to provide cross-database views:
"For animal CNT_01_15, how many videos are processed vs pending vs failed?"

SCOPE: the watcher database holds what THIS computer's watcher recorded; it
is not the pipeline's state (other computers' work is not in it). Every view
built on this module says so (watcher_scope_lines); the pipeline's state is
the pipeline census, read from the folders (pipeline_census.py).

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
# A filed video whose results are older than the declared tool versions; the
# watcher re-runs it (it re-enters the pipeline).
RERUN_STATES = ['outdated']
# A filed video locked for a publication: done, and stays as it is.
LOCKED_STATES = ['crystallized']
# Held for a person: the review queues. WHY their own bucket: until mousedb
# knew these states they fell into 'in pipeline' / 'in progress', so a video
# waiting for a reviewer looked like one the pipeline was working on.
REVIEW_STATES = ['triage', 'deep_review']
# This computer has no file for the video (another computer holds it). Not a
# failure and not a hold -- and not work this computer is doing.
NODE_ABSENT_STATES = ['unresolvable']
ALL_STATES = (PIPELINE_STATES + RERUN_STATES + LOCKED_STATES + REVIEW_STATES
              + NODE_ABSENT_STATES + ERROR_STATES)

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
    'outdated':     {'label': 'Outdated',      'color': '#FF9800'},
    'crystallized': {'label': 'Crystallized',  'color': '#2196F3'},
    'triage':       {'label': 'In Review (triage)',      'color': '#AB47BC'},
    'deep_review':  {'label': 'In Review (deep review)', 'color': '#7B1FA2'},
    'unresolvable': {'label': 'Not on this computer',    'color': '#BDBDBD'},
}

# 'crystallized' is a filed video locked for a publication: its analysis is
# finished, so it counts as done (it used to count as 'in progress').
DONE_STATES = frozenset(['processed', 'archiving', 'archived', 'crystallized'])

# Work the pipeline still has to do on this computer ('In Progress').
ACTIVE_STATES = frozenset([
    'discovered', 'validated', 'dlc_queued', 'dlc_running', 'dlc_complete',
    'processing', 'outdated',
])

# Archive-relevant state groupings
ARCHIVE_STATES = frozenset(['archived', 'outdated', 'crystallized'])
IN_PIPELINE_STATES = frozenset([
    'discovered', 'validated', 'dlc_queued', 'dlc_running', 'dlc_complete',
    'processing', 'processed', 'archiving',
])

# Color scheme for archive status view
ARCHIVE_STATE_DISPLAY = {
    'current':       {'label': 'Current',       'color': '#4CAF50'},
    'outdated':      {'label': 'Outdated',       'color': '#FF9800'},
    'crystallized':  {'label': 'Crystallized',   'color': '#2196F3'},
    'in_pipeline':   {'label': 'In Pipeline',    'color': '#9E9E9E'},
    'in_review':     {'label': 'In Review',      'color': '#AB47BC'},
    'not_on_this_node': {'label': 'Not on this computer', 'color': '#BDBDBD'},
    'failed':        {'label': 'Failed',         'color': '#F44336'},
    'other':         {'label': 'Other',          'color': '#795548'},
}


def pipeline_bucket(state: str) -> str:
    """Which count of the Video Pipeline view a watcher state belongs to:
    'done', 'in_progress', 'in_review', 'not_on_this_node', 'failed',
    'quarantined', or 'other' for a state mousedb does not know (shown under
    its own name, never folded into a bucket that means something else)."""
    if state in DONE_STATES:
        return 'done'
    if state in ACTIVE_STATES:
        return 'in_progress'
    if state in REVIEW_STATES:
        return 'in_review'
    if state in NODE_ABSENT_STATES:
        return 'not_on_this_node'
    if state == 'failed':
        return 'failed'
    if state == 'quarantined':
        return 'quarantined'
    return 'other'


def archive_bucket(state: str) -> str:
    """Which count of the Archive Status view a watcher state belongs to:
    'archived', 'outdated', 'crystallized', 'in_pipeline', 'in_review',
    'not_on_this_node', 'failed' (failed or quarantined), or 'other'."""
    if state in ('archived', 'outdated', 'crystallized'):
        return state
    if state in IN_PIPELINE_STATES:
        return 'in_pipeline'
    if state in REVIEW_STATES:
        return 'in_review'
    if state in NODE_ABSENT_STATES:
        return 'not_on_this_node'
    if state in ERROR_STATES:
        return 'failed'
    return 'other'


def _add_archive_count(summary, state: str, count: int) -> None:
    """Add ``count`` videos in ``state`` to an archive summary (overall,
    cohort or animal): one rule for all three."""
    bucket = archive_bucket(state)
    if bucket == 'other':
        summary.other[state] = summary.other.get(state, 0) + count
    else:
        setattr(summary, bucket, getattr(summary, bucket) + count)


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
    in_progress_count: int = 0
    in_review_count: int = 0
    not_on_this_node_count: int = 0
    other_states: Dict[str, int] = field(default_factory=dict)


@dataclass
class AnimalVideoSummary:
    """Video status rollup for one animal."""
    subject_id: str
    cohort_id: str
    total_videos: int = 0
    by_state: Dict[str, int] = field(default_factory=dict)
    failed_videos: int = 0
    # Held until a person deals with the recording (e.g. renames it). Its
    # own count so the per-animal columns add up to total_videos.
    quarantined: int = 0
    latest_activity: Optional[str] = None
    in_progress: int = 0
    in_review: int = 0
    not_on_this_node: int = 0
    other: Dict[str, int] = field(default_factory=dict)


@dataclass
class ArchiveSummary:
    """Overall archive processing summary."""
    total_videos: int = 0
    archived: int = 0
    outdated: int = 0
    crystallized: int = 0
    in_pipeline: int = 0
    failed: int = 0
    in_review: int = 0
    not_on_this_node: int = 0
    other: Dict[str, int] = field(default_factory=dict)
    versions_path: Optional[Path] = None
    versions_info: Optional[dict] = None


@dataclass
class ArchiveCohortSummary:
    """Per-cohort archive processing summary."""
    cohort_id: str = ''
    total_videos: int = 0
    archived: int = 0
    outdated: int = 0
    crystallized: int = 0
    in_pipeline: int = 0
    failed: int = 0
    in_review: int = 0
    not_on_this_node: int = 0
    other: Dict[str, int] = field(default_factory=dict)
    crystallized_label: Optional[str] = None

    @property
    def complete_count(self) -> int:
        return self.archived + self.crystallized

    @property
    def completion_pct(self) -> float:
        if self.total_videos == 0:
            return 0.0
        return self.complete_count / self.total_videos * 100


@dataclass
class ArchiveAnimalSummary:
    """Per-animal archive processing summary."""
    subject_id: str = ''
    cohort_id: str = ''
    total_videos: int = 0
    archived: int = 0
    outdated: int = 0
    crystallized: int = 0
    in_pipeline: int = 0
    failed: int = 0
    in_review: int = 0
    not_on_this_node: int = 0
    other: Dict[str, int] = field(default_factory=dict)
    crystallized_label: Optional[str] = None


def find_watcher_db() -> WatcherStatus:
    """
    Locate the watcher database this computer's watcher WRITES.

    Order (the same rule MouseReach's own commands use; mousedb reads the
    config file directly and never imports mousereach):
    1. MOUSEDB_WATCHER_DB environment variable
    2. ~/.mousereach/config.json -> watcher.db_path (the configured database)
    3. ~/.mousereach/config.json -> processing_root/watcher.db (the default)
    4. mousedb config: mousereach_pipeline_root/watcher.db

    WHY step 2 comes before step 3: a node may point its watcher at another
    file (``db_path`` in the ``watcher`` section, set because SQLite over a
    network share loses writes). The watcher then writes ONLY that file, and
    the default ``<processing_root>/watcher.db`` may still exist beside it
    holding old rows. Reading the default showed that stale copy as the
    pipeline's state. When the configured file is missing, nothing is shown:
    falling back to the default would show the stale copy again.
    """
    # 1. Environment variable
    env_path = os.environ.get('MOUSEDB_WATCHER_DB')
    if env_path:
        p = Path(env_path)
        if p.exists():
            return WatcherStatus(True, p, f"Found via MOUSEDB_WATCHER_DB: {p}")
        return WatcherStatus(False, None,
                             f"MOUSEDB_WATCHER_DB set but file not found: {p}")

    # 2./3. Read MouseReach config.json (no mousereach import!)
    config_file = Path.home() / ".mousereach" / "config.json"
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
            watcher_cfg = config.get('watcher') or {}
            configured = (watcher_cfg.get('db_path')
                          if isinstance(watcher_cfg, dict) else None)
            if configured:
                p = Path(configured)
                if p.exists():
                    return WatcherStatus(
                        True, p, f"Found via mousereach config watcher.db_path: {p}")
                return WatcherStatus(
                    False, None,
                    f"The watcher is configured to write {p} (watcher.db_path in "
                    f"~/.mousereach/config.json), but that file does not exist. "
                    f"The watcher may not have been started yet."
                )
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

    # 3. The configured MouseReach pipeline root (mousedb config)
    try:
        from .config import mousereach_pipeline_root
        root = mousereach_pipeline_root()
        if root and (root / "watcher.db").exists():
            return WatcherStatus(True, root / "watcher.db", f"Found at configured pipeline root: {root}")
    except Exception:
        pass

    return WatcherStatus(
        False, None,
        "watcher.db not found. Set MOUSEDB_WATCHER_DB or run mousereach-setup."
    )


# Machine-readable name of what the watcher views cover (in --json output).
WATCHER_SCOPE = "this_computer_watcher_records_only"


def watcher_db_last_written(status: WatcherStatus) -> Optional[str]:
    """When the watcher database was last written ('YYYY-MM-DD HH:MM', local
    time), or None. The watcher keeps its database in rollback-journal mode,
    so every committed change rewrites the file and its modified time is the
    time of the last recorded change."""
    if not status or not status.db_path:
        return None
    try:
        import time
        return time.strftime("%Y-%m-%d %H:%M",
                             time.localtime(os.path.getmtime(status.db_path)))
    except OSError:
        return None


def watcher_scope_lines(status: WatcherStatus) -> List[str]:
    """The lines every watcher view (two GUI tabs, two commands, the web
    dashboard) shows above its numbers. ASCII only: the commands print them.

    WHY: the watcher database records what THIS computer's watcher did. Work
    done by other computers, and anything moved by hand, is not in it, so its
    counts are not the pipeline's state. The pipeline's state is its folders,
    which the pipeline census reads (Where Is My Data tab). A person reading
    these views must be told which of the two they are looking at."""
    when = watcher_db_last_written(status) or "unknown"
    return [
        f"Records of this computer's watcher only: {status.db_path} "
        f"(last written {when}).",
        "Work done on other computers is not in these records. The whole "
        "pipeline's state, read from its folders, is on the Where Is My Data tab.",
    ]


def find_versions_json() -> Optional[Path]:
    """
    Locate pipeline_versions.json WITHOUT importing mousereach.

    Discovery chain:
    1. MOUSEDB_VERSIONS_JSON environment variable
    2. ~/.mousereach/config.json -> nas_root/pipeline_versions.json
    3. ~/.mousereach/config.json -> processing_root/pipeline_versions.json
    4. mousedb config: mousereach_pipeline_root/pipeline_versions.json
    """
    env_path = os.environ.get('MOUSEDB_VERSIONS_JSON')
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
        return None

    config_file = Path.home() / ".mousereach" / "config.json"
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)

            nas_root = config.get('nas_root')
            if nas_root:
                p = Path(nas_root) / "pipeline_versions.json"
                if p.exists():
                    return p

            processing_root = config.get('processing_root')
            if processing_root:
                p = Path(processing_root) / "pipeline_versions.json"
                if p.exists():
                    return p
        except Exception:
            pass

    try:
        from .config import mousereach_pipeline_root
        root = mousereach_pipeline_root()
        if root and (root / "pipeline_versions.json").exists():
            return root / "pipeline_versions.json"
    except Exception:
        pass

    return None


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

            for st, cnt in summary.by_state.items():
                bucket = pipeline_bucket(st)
                if bucket == 'in_progress':
                    summary.in_progress_count += cnt
                elif bucket == 'in_review':
                    summary.in_review_count += cnt
                elif bucket == 'not_on_this_node':
                    summary.not_on_this_node_count += cnt
                elif bucket == 'other':
                    summary.other_states[st] = cnt

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
                elif row['state'] == 'quarantined':
                    summary.quarantined += row['cnt']
                bucket = pipeline_bucket(row['state'])
                if bucket == 'in_progress':
                    summary.in_progress += row['cnt']
                elif bucket == 'in_review':
                    summary.in_review += row['cnt']
                elif bucket == 'not_on_this_node':
                    summary.not_on_this_node += row['cnt']
                elif bucket == 'other':
                    summary.other[row['state']] = (
                        summary.other.get(row['state'], 0) + row['cnt'])

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
                    'quarantined': 0,
                    'fully_processed': 0,
                    'in_progress': 0,
                    'in_review': 0,
                    'not_on_this_node': 0,
                    'other': {},
                }
            c = cohorts[cid]
            c['animals'] += 1
            c['total_videos'] += animal.total_videos
            c['failed'] += animal.failed_videos
            c['quarantined'] += animal.quarantined
            c['in_progress'] += animal.in_progress
            c['in_review'] += animal.in_review
            c['not_on_this_node'] += animal.not_on_this_node
            for st, n in animal.other.items():
                c['other'][st] = c['other'].get(st, 0) + n
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

    def get_archive_summary(self) -> ArchiveSummary:
        """Get overall archive processing and version compliance summary."""
        summary = ArchiveSummary()
        if not self.is_available:
            return summary

        versions_path = find_versions_json()
        if versions_path:
            summary.versions_path = versions_path
            try:
                with open(versions_path, 'r') as f:
                    summary.versions_info = json.load(f)
            except Exception:
                pass

        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT state, COUNT(*) as cnt FROM videos GROUP BY state"
            ).fetchall()

            for row in rows:
                state = row['state']
                count = row['cnt']
                summary.total_videos += count
                _add_archive_count(summary, state, count)

            return summary
        finally:
            conn.close()

    def get_archive_cohort_rollup(self) -> List['ArchiveCohortSummary']:
        """Get per-cohort archive processing summary."""
        if not self.is_available:
            return []

        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT animal_id, state, COUNT(*) as cnt,
                       GROUP_CONCAT(DISTINCT crystallized_label) as labels
                FROM videos
                WHERE animal_id IS NOT NULL
                GROUP BY animal_id, state
            """).fetchall()

            cohorts: Dict[str, ArchiveCohortSummary] = {}
            for row in rows:
                subject_id = _animal_id_to_subject_id(row['animal_id'])
                if not subject_id:
                    continue

                cohort_id = '_'.join(subject_id.split('_')[:2])
                if cohort_id not in cohorts:
                    cohorts[cohort_id] = ArchiveCohortSummary(cohort_id=cohort_id)

                c = cohorts[cohort_id]
                state = row['state']
                count = row['cnt']
                c.total_videos += count
                _add_archive_count(c, state, count)
                if state == 'crystallized' and row['labels']:
                    c.crystallized_label = row['labels']

            return sorted(cohorts.values(), key=lambda c: c.cohort_id)
        finally:
            conn.close()

    def get_archive_animal_rollup(
        self, cohort: Optional[str] = None
    ) -> List['ArchiveAnimalSummary']:
        """Get per-animal archive processing summary.

        Args:
            cohort: Optional cohort filter (e.g. 'ENCR_01'). If None, all.
        """
        if not self.is_available:
            return []

        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT animal_id, state, COUNT(*) as cnt,
                       GROUP_CONCAT(DISTINCT crystallized_label) as labels
                FROM videos
                WHERE animal_id IS NOT NULL
                GROUP BY animal_id, state
            """).fetchall()

            animals: Dict[str, ArchiveAnimalSummary] = {}
            for row in rows:
                subject_id = _animal_id_to_subject_id(row['animal_id'])
                if not subject_id:
                    continue

                cohort_id = '_'.join(subject_id.split('_')[:2])
                if cohort and cohort_id != cohort.upper():
                    continue

                if subject_id not in animals:
                    animals[subject_id] = ArchiveAnimalSummary(
                        subject_id=subject_id,
                        cohort_id=cohort_id,
                    )

                a = animals[subject_id]
                state = row['state']
                count = row['cnt']
                a.total_videos += count
                _add_archive_count(a, state, count)
                if state == 'crystallized' and row['labels']:
                    a.crystallized_label = row['labels']

            return sorted(animals.values(), key=lambda a: a.subject_id)
        finally:
            conn.close()
