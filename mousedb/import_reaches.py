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
     The ledger is SAVED WHILE THE RUN GOES (every ``LEDGER_SAVE_SECONDS``),
     each video only after its rows are committed, and always whole (written
     beside and swapped in). So a run that is killed -- the scheduled task's
     time limit -- and restarted carries on where it stopped instead of
     importing every file again. A ledger that exists but cannot be read
     STOPS the run ([FAIL], exit 1) instead of being taken as empty: taken
     as empty it meant "import every file again", silently (``load_ledger``).
  4. Leaves out every filed video whose NEWER run waits in a review queue
     (the test of step 6): not imported, not entered in the ledger, its
     cohort not re-labelled. Importing it would only have its rows taken out
     again by step 6, every night. ``--verbose`` lists them.
  5. For each file: the animal is CREATED from the video name if the tracking
     sheet has not named it yet (machine data never waits on hand data; the
     sheet import enriches the record later), old rows for that video are
     deleted, new rows inserted -- one transaction per video.
     The same transaction replaces the video's rows in ``video_pellets``: one
     per pellet the pipeline cut the video into, reached for or not, with its
     outcome. That is where "how many pellets were presented" is MEASURED (20
     unless something went wrong); reach_data cannot say, because a pellet
     nobody reached for has no reach row.
  6. Re-derives test_phase / phase_group for every cohort touched
     (``mousedb.backfill.backfill_phases``). The cohorts still to do are kept
     in ``<mousedb_root>/logs/reach_imports_phases_pending.json`` until each
     is done, so a run killed before or during this step has the restart do
     them even though it imports nothing new for them.
  7. Takes out the rows of every video that has gone BACK TO REVIEW: its
     results file sits in a review queue (``Processing/Review``) and no longer
     in Analyzed, or a NEWER run of it waits there while the old one is still
     filed. See ``videos_back_in_review`` and ``withdraw_videos_back_in_review``.
  8. REPORTS (never removes) every video that has rows in the database but no
     results file anywhere it looked and is not in a review queue: in the run's
     output and in ``<mousedb_root>/logs/reach_rows_without_file.json``, which
     the Where Is My Data tab shows. See ``rows_without_a_filed_file``.

Usage:
    mousedb import-reaches                 # what is new or changed
    mousedb import-reaches --dry-run       # count only
    mousedb import-reaches --all           # ignore the ledger, re-import everything
    mousedb import-reaches --include-processing
    mousedb import-reaches --verbose       # also name the filed videos held back by review
    mousedb import-reaches --pellets-only  # (re)fill video_pellets from every filed file
                                           # (not a video whose newer run is in review)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .config import require
from .pipeline_tree import iter_files
from .task_mutex import LockWaitTimeout

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
# A person's Flag Session note on the video (MouseReach's top-level
# session_flag: null, or {session_key, reason, flagged_by, flagged_at}). A
# MARKER ONLY (owner decision D5-10, 2026-11-19): recorded so it can be found
# later, on every reach row of the session; nothing excludes or treats a
# flagged row differently. session_flag is 1 when flagged, NULL otherwise.
SESSION_FLAG_COLUMNS = ['session_flag', 'session_flag_reason', 'session_flag_by',
                        'session_flag_at']
ALL_COLUMNS = (['subject_id', 'video_name', 'session_date', 'tray_type', 'run_number', 'segment_num']
               + REACH_JSON_COLUMNS + SEGMENT_COLUMNS
               + ['source_file', 'extractor_version', 'imported_at', 'extended_features']
               + PROVENANCE_COLUMNS + SESSION_FLAG_COLUMNS)
# Columns older databases may lack (added with ALTER TABLE, never destructive)
MIGRATION_COLUMNS = [(c, 'TEXT') for c in PROVENANCE_COLUMNS + ['outcome_source', 'reviewed_by',
                     'algo_outcome', 'extended_features', 'test_phase', 'phase_group']] + \
                    [('algo_causal_reach_id', 'INTEGER'), ('session_flag', 'INTEGER'),
                     ('session_flag_reason', 'TEXT'), ('session_flag_by', 'TEXT'),
                     ('session_flag_at', 'TEXT')]

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
    held: int = 0                 # filed results not imported: a newer run waits in review
    without_file: List[str] = field(default_factory=list)  # rows in the db, no results file filed
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


# The state-file reader and the atomic writer moved to state_files.py so the
# analysis mirror can use them too; imported here under their old names.
from .state_files import (  # noqa: E402,F401
    STATE_READ_ATTEMPTS, STATE_READ_RETRY_SECONDS, StateFileUnreadable,
    _confirmed_absent, _read_state_file, _write_json_atomically)


# What a stop on an unreadable state file means, for the [FAIL] line.
BEFORE_IMPORTING = "nothing was imported"
PART_WAY = ("the run stopped part-way; videos imported since its last save "
            "are imported again by the next run")


def read_ledger(consequence: str = BEFORE_IMPORTING) -> Optional[Dict[str, str]]:
    """The import ledger; None when there is none yet (confirmed from a folder
    listing, see ``_confirmed_absent``). Raises ``StateFileUnreadable`` when
    one exists but cannot be read (see ``_read_state_file``: an unreadable
    ledger used to mean "import every file again", silently).

    None and {} are kept apart so that the run says "no ledger yet" only when
    there truly is none: a ledger that exists and is empty (left by a run
    that found no results files) is not news, and calling it missing would
    send a person looking for a file that is there."""
    return _read_state_file(
        ledger_path(), "reach import ledger", dict,
        "otherwise rename that file (keep it) and run again -- the run then "
        "imports every results file again, which takes hours", consequence)


def load_ledger(consequence: str = BEFORE_IMPORTING) -> Dict[str, str]:
    """The import ledger; {} when there is none yet. See ``read_ledger``."""
    data = read_ledger(consequence)
    if data is None:
        logger.warning("no reach import ledger at %s yet: every results file will be imported",
                       ledger_path())
        return {}
    return data


def save_ledger(ledger: Dict[str, str]) -> None:
    _write_json_atomically(ledger_path(), ledger)


# How often the import saves its ledger while it runs. WHY: the scheduled run
# has a time limit and is restarted when it is killed. With the ledger saved
# only at the end, a run killed just before the limit had recorded nothing, so
# the restart hashed and imported every file again -- and could be killed at
# the same point again. Saving every few minutes means a restart redoes at most
# that much. Time-based, not every video: the ledger is one JSON file on a
# network share and rewriting it takes seconds.
LEDGER_SAVE_SECONDS = 300

# Cohorts whose test-phase labels still have to be re-derived. WHY a separate
# file: the labels are re-derived after the import loop, for every cohort a
# newly imported video belongs to. Once the ledger is saved during the run, a
# run killed before that step leaves videos the restart will SKIP (the ledger
# says they are done), so the restart would not know their cohorts need it and
# their rows would keep no test phase. Every cohort is written here before the
# ledger that marks its videos done, and removed only once its labels are
# re-derived. Kept out of the ledger itself because other tools read the
# ledger's keys as file paths.
PENDING_PHASES_NAME = "reach_imports_phases_pending.json"


def pending_phases_path() -> Path:
    return require("mousedb_root") / "logs" / PENDING_PHASES_NAME


def load_pending_entries(consequence: str = BEFORE_IMPORTING) -> Dict[str, int]:
    """The list of cohorts still to re-label, as {cohort: times queued}; {}
    when the file does not exist. Raises ``StateFileUnreadable`` when it
    exists but cannot be read: treated as empty, the cohorts it named would
    keep wrong test-phase labels and nothing would ever say so.

    THE FILE'S FORMAT: a JSON object {cohort: count}. The count goes up by one
    every time a save QUEUES the cohort (``queue_pending_phases``: its phase
    refresh failed). WHY: the reach import re-labels a cohort and then takes
    it off the list. A cohort queued again WHILE it was being re-labelled may
    have been re-labelled from dates read before that save, and taking it off
    then lost it, with stale labels and nothing reporting it. The count tells
    the two apart: the import takes a cohort off only if its count is still
    the one it read before it began re-labelling it (see ``run``). Older
    versions wrote a plain JSON list of cohort names; that is still read, as
    count 0 for each. (An older version reading the new file takes its keys,
    which are the cohort names, so it keeps working too.)"""
    data = _read_state_file(
        pending_phases_path(), "list of cohorts still to re-label", (list, dict),
        "otherwise rename that file (keep it), run 'mousedb backfill-phases' "
        "to re-label every cohort, and run again", consequence)
    if data is None:
        return {}
    entries = {c: 0 for c in data} if isinstance(data, list) else data
    if not all(isinstance(c, str) and isinstance(n, int) and not isinstance(n, bool)
               for c, n in entries.items()):
        raise StateFileUnreadable(
            "the list of cohorts still to re-label %s holds something other than "
            "cohort names and counts; %s. Otherwise rename that file (keep it), run "
            "'mousedb backfill-phases' to re-label every cohort, and run again"
            % (pending_phases_path(), consequence))
    return dict(entries)


def load_pending_phases(consequence: str = BEFORE_IMPORTING) -> set:
    """Cohorts still to re-label (the names only); see ``load_pending_entries``."""
    return set(load_pending_entries(consequence))


def save_pending_phases(cohorts: Any) -> None:
    """Write the list whole. ``cohorts`` is {cohort: count} or, for a plain
    set of names, each name at count 0. Code that CHANGES the list goes
    through ``update_pending_phases`` / ``queue_pending_phases``, which hold
    its lock."""
    entries = cohorts if isinstance(cohorts, dict) else {c: 0 for c in cohorts}
    _write_json_atomically(pending_phases_path(), dict(sorted(entries.items())))


# How long a writer of the list of cohorts still to re-label waits for another
# writer to finish with it. Each holds it for one read and one write of a small
# file (well under a second, a few seconds over a slow share), so a minute
# means something is wrong, not that the other writer is busy.
PENDING_PHASES_LOCK_WAIT_SECONDS = 60


def update_pending_phases(change: Callable[[set], set],
                          consequence: str = BEFORE_IMPORTING) -> set:
    """Read the list of cohorts still to re-label, apply ``change`` to it, and
    write the result -- as ONE step no other writer can interleave with; the
    new list.

    WHY: two programs write this list. The reach import adds the cohorts it
    imported into and takes off those it re-labelled; a save in the MouseDB
    window whose own phase refresh failed adds that cohort
    (``Database._queue_for_reach_import``). The commonest reason that refresh
    fails is a database busy with the running reach import -- so the two write
    the list at the very moments they are most likely to overlap. Each used to
    read the file, change it in memory and write it back: a window write that
    landed between the import's read and its write was overwritten, and that
    cohort kept labels from its old dates with nothing reporting it.

    HOW: the read-change-write runs under its own lock (a ``TaskMutex`` named
    ``reach-phases-list`` in the same folder, so the import's hold of the
    database lock never blocks it, and a writer that crashed while holding it
    is recognised and its lock broken, as for the database lock). A writer
    that cannot get the lock within ``PENDING_PHASES_LOCK_WAIT_SECONDS``
    raises ``LockWaitTimeout``, having changed nothing.

    ``change`` works on the cohort names; a cohort it keeps keeps its queue
    count, a cohort it adds starts at 1 (see ``load_pending_entries``). A save
    that queues a cohort uses ``queue_pending_phases`` instead."""
    def on_entries(entries: Dict[str, int]) -> Dict[str, int]:
        return {c: entries.get(c, 1) for c in change(set(entries))}
    return set(_change_pending_entries(on_entries, consequence))


def queue_pending_phases(cohorts: Iterable[str],
                         consequence: str = BEFORE_IMPORTING) -> set:
    """Put ``cohorts`` on the list of cohorts still to re-label, raising each
    one's queue count by one (one locked read-and-write); the new list.

    WHY the count goes up even for a cohort already on the list: the running
    reach import may be re-labelling that very cohort from dates it read
    before the save that queued it. The import takes a cohort off the list
    only when its count is still the one it read before it began, so this
    cohort stays on for the next run instead of being lost with stale labels."""
    cohorts = list(cohorts)

    def bump(entries: Dict[str, int]) -> Dict[str, int]:
        out = dict(entries)
        for c in cohorts:
            out[c] = out.get(c, 0) + 1
        return out
    return set(_change_pending_entries(bump, consequence))


def _change_pending_entries(change: Callable[[Dict[str, int]], Dict[str, int]],
                            consequence: str) -> Dict[str, int]:
    """Apply ``change`` to {cohort: count} under the list's lock and write the
    result; see ``update_pending_phases`` for why the lock."""
    from .task_mutex import TaskMutex
    lock = TaskMutex("reach-phases-list", base_dir=pending_phases_path().parent)
    with lock.acquire(wait_seconds=PENDING_PHASES_LOCK_WAIT_SECONDS,
                      log=lambda *_: None,
                      waiting_for="another writer of the list of cohorts to re-label"):
        new = dict(change(load_pending_entries(consequence)))
        save_pending_phases(new)
    return new


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


def session_flag_fields(flag) -> dict:
    """The four session_flag columns from a features file's ``session_flag``.

    Anything that is not a note (null, absent, an empty object) is "not
    flagged": all four NULL. A note is recorded as written; only its
    session_key is not copied, because the row's own video_name is that key.
    """
    out = {c: None for c in SESSION_FLAG_COLUMNS}
    if isinstance(flag, dict) and flag:
        out['session_flag'] = 1
        for col, key in (('session_flag_reason', 'reason'), ('session_flag_by', 'flagged_by'),
                         ('session_flag_at', 'flagged_at')):
            v = flag.get(key)
            out[col] = None if v is None else str(v)
    return out


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
    flag = session_flag_fields(data.get('session_flag'))
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
            row.update(flag)
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

    Filed videos whose NEWER run waits in a review queue are skipped, by the
    same test the import itself applies (``videos_back_in_review``). WHY: such
    a video is not done -- the import leaves its filed copy out and takes its
    rows out of the database -- so writing its filed copy's pellets here put
    back, for every pellet, the answers the newer run is being reviewed to
    replace, and they stayed in the exports until the next import took them
    out again. Skipped only: rows such a video already has are left for the
    import's withdrawal step, which copies them aside before taking them out.
    """
    root = Path(root) if root else require("mousereach_pipeline_root")
    files = find_features_files(root, include_processing)
    held = (set(videos_back_in_review(root, {f.name[:-len(FEATURES_SUFFIX)]: f for f in files}))
            if files else set())
    now = datetime.now().isoformat()
    by_video: Dict[str, List[dict]] = {}
    unreadable: List[str] = []
    skipped_held: List[str] = []
    for p in files:
        if p.name[:-len(FEATURES_SUFFIX)] in held:
            skipped_held.append(p.name[:-len(FEATURES_SUFFIX)])
            continue
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
        len(files) - len(skipped_held), len(by_video), len(unreadable)))
    if skipped_held:
        log("backfill: %d filed videos skipped: a newer run is in review" % len(skipped_held))
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
    return {"videos": len(by_video), "pellets_per_video": counts, "unreadable": unreadable,
            "held": sorted(skipped_held)}


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
                                   dry_run: bool = False, log=print,
                                   checkpoint=None) -> Tuple[int, int, List[str]]:
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
    even if nothing in it changed. Returns (videos, reach rows, messages).

    ORDER (``checkpoint`` given: a callable that saves the ledger): the ledger
    entries of every video to be taken out are dropped and the ledger SAVED
    before any row is deleted. WHY:
    a run killed between deleting a video's rows and saving the ledger left a
    ledger still saying the video was imported; the restart found no rows to
    take out, kept the entry, and when the video was filed again unchanged it
    was skipped as already imported -- gone from the database for good. Saved
    first, a kill in between leaves rows and no entry, which the next run
    puts right on its own (it re-imports the filed copy, or takes the rows out
    again). For the same reason a video in review loses a leftover ledger
    entry even when it has no rows left."""
    videos = withdrawn = 0
    messages: List[str] = []
    out: List[Tuple[str, Path, int]] = []
    drop: List[str] = []
    for name, where in sorted(videos_back_in_review(root, filed).items()):
        keys = [k for k in ledger if k.rsplit("/", 1)[-1] == name + FEATURES_SUFFIX]
        n = conn.execute("SELECT COUNT(*) FROM reach_data WHERE video_name = ?",
                         (name,)).fetchone()[0]
        p = conn.execute("SELECT COUNT(*) FROM video_pellets WHERE video_name = ?",
                         (name,)).fetchone()[0]
        # Every video in review loses its entries, rows or not: one with no
        # rows left may still have an entry (a stopped run, or an older
        # importer), and since the import loop no longer imports a filed
        # video whose newer run is in review, nothing else would clear it.
        drop.extend(keys)
        if not n and not p:
            continue
        out.append((name, where, n))
    if not dry_run:
        for key in drop:
            ledger.pop(key, None)
        if drop and checkpoint is not None:
            checkpoint()
    for name, where, n in out:
        queue = where.relative_to(root / "Processing" / "Review").parts[0]
        if not dry_run:
            with conn:
                _copy_rows(conn, "reach_data", name, backup_root / (name + "_reach_data.csv"))
                _copy_rows(conn, "video_pellets", name, backup_root / (name + "_video_pellets.csv"))
                conn.execute("DELETE FROM reach_data WHERE video_name = ?", (name,))
                conn.execute("DELETE FROM video_pellets WHERE video_name = ?", (name,))
        videos += 1
        withdrawn += n
        line = ("%s: %s the %s queue, so not done -- %d reach rows taken out until it "
                "is filed again%s" % (name, "a newer run is in" if name in filed else "back in",
                                      queue, n, " [dry run]" if dry_run else ""))
        messages.append(line)
        log("  [withdrawn] " + line)
    return videos, withdrawn, messages


ROWS_WITHOUT_FILE_NAME = "reach_rows_without_file.json"
# How many of those videos the run's output names; the file holds them all.
ROWS_WITHOUT_FILE_SHOWN = 20


def rows_without_file_path() -> Path:
    return require("mousedb_root") / "logs" / ROWS_WITHOUT_FILE_NAME


def rows_without_a_filed_file(conn: sqlite3.Connection, listed: Iterable[str],
                              in_review: Iterable[str]) -> List[str]:
    """Videos with rows in reach_data or video_pellets whose results file is
    neither among ``listed`` (the results files this run found) nor in a
    review queue (``in_review``), sorted.

    WHY: rows leave the database only on positive evidence -- the video's
    results sit in a review queue (``videos_back_in_review``). A results file
    that leaves Analyzed any other way (deleted, moved somewhere the import
    does not look) leaves its rows in the database, and in every export, as
    if the video were still filed, and nothing said so. Deleting them here
    would turn a failed or partial listing into lost data, so this only
    REPORTS them: a person looks, and either puts the file back or hands the
    question to whoever looks after the database. There is deliberately no
    route here, or anywhere in this tool, that takes the rows out.

    Matching. ``listed`` and ``in_review`` hold names as the RESULTS FILES
    are named (the file name minus ``_features.json``). The rows carry the
    name written INSIDE the file (``video_name``) and also the name of the
    file they came from (``source_file``). The two names agree unless a file
    was renamed without re-processing, so the rows' ``source_file`` is what is
    matched: a renamed file that is there is not reported. ``video_name``
    stands in only for rows that recorded no ``source_file``.

    Cost: the first DISTINCT reads walk an index, not the table (reach_data's
    unique (video_name, reach_id) and video_pellets' primary key both start
    with video_name). Names that match a listed file outright are done; only
    the few left over have their ``source_file`` looked up, one indexed read
    each, so the nightly cost does not grow with the size of reach_data."""
    listed, in_review = set(listed), set(in_review)
    known = listed | in_review
    tables = ["reach_data", "video_pellets"]
    have = set()
    for t in list(tables):
        try:
            have |= {r[0] for r in conn.execute("SELECT DISTINCT video_name FROM %s" % t)}
        except sqlite3.OperationalError:
            tables.remove(t)            # a database the import has never written to
    out = []
    for v in sorted(x for x in have - known if x):
        sources = set()
        for t in tables:
            sources |= {r[0] for r in conn.execute(
                "SELECT DISTINCT source_file FROM %s WHERE video_name = ?" % t, (v,))}
        stems = {Path(str(f)).name[:-len(FEATURES_SUFFIX)]
                 if str(f).endswith(FEATURES_SUFFIX) else Path(str(f)).stem
                 for f in sources if f}
        if None in sources or "" in sources:
            stems.add(v)                # rows that recorded no file: match by name
        if not stems & known:
            out.append(v)
    return out


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
        limit: Optional[int] = None, force: bool = False, log=print,
        verbose: bool = False) -> ImportResult:
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
    # Stops the run if a ledger exists but cannot be read. None means there is
    # no ledger file at all, and only then is "no ledger yet" said.
    ledger = read_ledger()
    if ledger is None:
        ledger = {}
        if files and not all_files:
            log("import-reaches: no import ledger yet at %s -- every results file will be imported"
                % ledger_path())
    moved_from = _moved_entries(ledger, root, files) if not all_files else {}
    res.moved = len(moved_from)
    # Filed videos whose NEWER run waits in a review queue: their filed results
    # are not imported at all, and not entered in the ledger. WHY: the step
    # after the loop takes such a video's rows out (it is not done while the
    # new run waits for a person). Importing the old filed file first meant
    # that every night the same videos were imported, withdrawn again, and
    # their cohorts re-labelled for nothing. Skipped here, they add no rows
    # and touch no cohort; with no ledger entry, the new run is imported as
    # usual once it is filed. Rows such a video already has are still taken
    # out by the withdrawal step. The same test as that step
    # (videos_back_in_review), worked out once here before the loop.
    held = (set(videos_back_in_review(root, {f.name[:-len(FEATURES_SUFFIX)]: f for f in files}))
            if files else set())
    held_names: List[str] = []
    todo = []
    for p in files:
        key = str(p.relative_to(root)).replace("\\", "/")
        if p.name[:-len(FEATURES_SUFFIX)] in held:
            res.held += 1
            held_names.append(p.name[:-len(FEATURES_SUFFIX)])
            continue
        # A file can be gone between the listing and this read: the watcher
        # refiles a video by replacing its results, and a re-run moves a bundle
        # into a queue. Skipped here like the vanished-file case further down,
        # and found again next run. WHY (2026-11-03): the 2026-11-02 21:00 run
        # stopped on one such file and imported nothing, while a version bump
        # was rewriting thousands of results files a day.
        try:
            st = p.stat()
            stamp = "%d:%d" % (st.st_size, int(st.st_mtime))
        except FileNotFoundError:
            res.skipped += 1
            res.messages.append("%s: vanished while listing -- skipped" % p.name)
            log("  [skip] %s vanished while listing" % p.name)
            continue
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
        try:
            h = file_hash(p)
        except FileNotFoundError:
            res.skipped += 1
            res.messages.append("%s: vanished while listing -- skipped" % p.name)
            log("  [skip] %s vanished while listing" % p.name)
            continue
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
    if held_names:
        log("import-reaches: %d filed videos skipped: a newer run is in review" % len(held_names))
        res.messages.append("%d filed videos skipped: a newer run is in review" % len(held_names))
        if verbose:
            for name in sorted(held_names):
                log("  [held] %s" % name)
    # Cohorts an earlier run imported into but stopped before re-labelling
    # (see PENDING_PHASES_NAME). Their videos are in the ledger, so this run
    # would not touch them again; they are re-labelled at the end regardless.
    pending = load_pending_phases() if not dry_run else set()
    if pending:
        log("import-reaches: %d cohort(s) still to re-label from an earlier run that stopped: %s"
            % (len(pending), ", ".join(sorted(pending))))

    def checkpoint() -> None:
        # Pending cohorts FIRST, then the ledger: a video must never be marked
        # done in the ledger while nothing records that its cohort still needs
        # re-labelling. Each video's ledger entry is set only after its
        # transaction committed (below), so the ledger never runs ahead of the
        # database; a kill loses at most the entries since the last save, and
        # those videos are simply imported again by the restart.
        # The file is RE-READ and added to, never overwritten from what this
        # run read at its start: another program adds to it while the import
        # runs (a save in the window whose phase refresh failed --
        # Database._queue_for_reach_import), and writing only this run's set
        # would drop that cohort, leaving its labels wrong for good. The
        # read and the write are one locked step (update_pending_phases), so
        # that program's write cannot land between them either.
        update_pending_phases(lambda now: now | pending | res.cohorts_touched, PART_WAY)
        save_ledger(ledger)

    last_save = time.monotonic()
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
                        ledger[key] = h   # import_file returned: its rows are committed
                        if time.monotonic() - last_save >= LEDGER_SAVE_SECONDS:
                            try:
                                checkpoint()
                            except (OSError, StateFileUnreadable, LockWaitTimeout) as e:
                                # Not fatal: the import goes on and the save at
                                # the end tries again (and fails the run if it
                                # still cannot write, or still cannot read the
                                # list of cohorts it adds to). Caught HERE so
                                # it is never counted as this video failing:
                                # the video's rows are committed.
                                res.messages.append("ledger save during the run failed: %s" % e)
                                log("  [!] ledger save during the run failed: %s" % e)
                            last_save = time.monotonic()
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
                    conn, root, filed, ledger, backup, dry_run=dry_run, log=log,
                    checkpoint=checkpoint)
                res.withdrawn, res.withdrawn_reaches = v, r
                res.messages.extend(msgs)
                # Rows whose results file is gone without passing through a
                # review queue: reported, never removed (rows_without_a_filed_file).
                # Reported HERE, before the summary line, because the update
                # job shows each step's last line and that must stay the summary.
                # ``held`` is the review-queue test worked out before the
                # loop over the same listing; no second walk of the queues.
                res.without_file = rows_without_a_filed_file(conn, filed, held)
                if res.without_file:
                    shown = res.without_file[:ROWS_WITHOUT_FILE_SHOWN]
                    line = ("%d videos have rows in the database but no results file in the "
                            "pipeline and are not in review -- nothing was taken out; a person "
                            "should look (%s%s)" % (
                                len(res.without_file), ", ".join(shown),
                                ", ..." if len(res.without_file) > len(shown) else ""))
                    res.messages.append(line)
                    log("  [!] " + line)
                if not dry_run:
                    try:
                        _write_json_atomically(rows_without_file_path(), {
                            "checked_at": datetime.now().isoformat(timespec="seconds"),
                            "count": len(res.without_file),
                            "videos": res.without_file})
                    except OSError as e:
                        res.messages.append("could not save the list of rows without a "
                                            "results file: %s" % e)
                        log("  [!] could not save the list of rows without a results file: %s" % e)
            else:
                res.messages.append("no filed results found; nothing taken out")
        finally:
            conn.close()
        if not dry_run:
            checkpoint()  # also persists refreshed stamps for unchanged files
            # Re-read: a cohort queued by another program during this run
            # (see checkpoint) is re-labelled now rather than a night later.
            relabel = load_pending_phases(PART_WAY) | pending | res.cohorts_touched
            left = set(relabel)
            if relabel:
                try:
                    from .backfill import backfill_phases
                    from .prefetch import warmed
                    # Re-labelling reads the whole reach table once per cohort.
                    # Read the file sequentially first and hold it open for all
                    # the cohorts, so those scans run from memory: 3 hours cold
                    # against seconds warm on a network share (2026-10-16).
                    with warmed(db, log=log):
                        for cohort in sorted(relabel):
                            # The cohort's queue count BEFORE re-labelling it.
                            # If a save queues it again before the re-label is
                            # done, that save may have landed after the
                            # re-label read the cohort's dates (they are read
                            # outside any write transaction), so this re-label
                            # may be stale: the count has changed and the
                            # cohort stays on the list for the next run (see
                            # load_pending_entries).
                            seen = load_pending_entries(PART_WAY).get(cohort)
                            backfill_phases(cohort_id=cohort)
                            left.discard(cohort)

                            # Re-read and change, as in checkpoint(): the
                            # cohort just re-labelled leaves the list unless it
                            # was queued again meanwhile -- a run killed after
                            # this point need not re-label it again. Every
                            # cohort not yet re-labelled stays; a cohort done
                            # earlier in this loop and queued again since
                            # stays too.
                            def done(entries, c=cohort, seen=seen):
                                out = dict(entries)
                                if out.get(c) == seen:
                                    out.pop(c, None)
                                for other in left:
                                    out.setdefault(other, 1)
                                return out
                            _change_pending_entries(done, PART_WAY)
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
    ap.add_argument("--verbose", action="store_true",
                    help="Also list each filed video skipped because a newer run is in review")
    ap.add_argument("--pellets-only", action="store_true",
                    help="Only (re)fill the per-pellet table (video_pellets) from every features "
                         "file, except a filed video whose newer run waits in review; reaches, "
                         "animals and the ledger are not touched")
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
                  limit=args.limit, force=args.force, log=(lambda *_: None) if args.json else print,
                  verbose=args.verbose)
    except Exception as e:
        print("[FAIL] %s" % e)
        return 1
    if args.json:
        print(json.dumps(res.as_dict(), indent=1))
    return 1 if res.errors else 0


if __name__ == "__main__":
    sys.exit(main())
