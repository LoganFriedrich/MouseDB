"""
DLC trajectory loading and reach-trajectory extraction for spaghetti plots.

Loads frame-by-frame XY coordinates from DLC .h5 files, slices by reach
boundaries, filters by outcome, and groups by experimental phase.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..schema import TIMELINE, INJURY_DAY

logger = logging.getLogger(__name__)

# Bodypart groups for trajectory extraction
HAND_BODYPARTS = ['RightHand', 'RHLeft', 'RHOut', 'RHRight']
CONTEXT_BODYPARTS = ['Nose', 'Pellet']

# Phase groupings for spaghetti plot overlay
PHASE_GROUPS = {
    'Pre-Injury Test': {
        'phases': ['Pre-Injury_Test_Pillar_1', 'Pre-Injury_Test_Pillar_2', 'Pre-Injury_Test_Pillar_3'],
        'color': '#9b59b6',  # Purple
        'day_range': (14, 16),
    },
    'Post-Injury Early': {
        'phases': ['Post-Injury_Test_1'],
        'color': '#e74c3c',  # Red
        'day_range': (25, 25),
    },
    'Post-Injury Late': {
        'phases': ['Post-Injury_Test_4'],
        'color': '#e67e22',  # Orange
        'day_range': (46, 46),
    },
    'Rehab Pillar': {
        'phases': ['Rehab_Pillar_1', 'Rehab_Pillar_2', 'Rehab_Pillar_3',
                   'Rehab_Pillar_4', 'Rehab_Pillar_5', 'Rehab_Pillar_6', 'Rehab_Pillar_7'],
        'color': '#2ecc71',  # Green
        'day_range': (63, 69),
    },
}

# Build phase-name-to-group lookup
_PHASE_TO_GROUP = {}
for group_name, info in PHASE_GROUPS.items():
    for phase in info['phases']:
        _PHASE_TO_GROUP[phase] = group_name


@dataclass
class ReachTrajectory:
    """A single reach trajectory with metadata."""
    x: np.ndarray           # X coordinates per frame (hand centroid)
    y: np.ndarray           # Y coordinates per frame (hand centroid)
    frames: np.ndarray      # Frame indices
    video_name: str
    reach_id: int
    start_frame: int
    apex_frame: int
    end_frame: int
    outcome: str
    phase_group: str
    session_date: str
    duration_frames: int
    ruler_pixels: float     # For mm conversion


@dataclass
class SubjectTrajectories:
    """All trajectories for a subject, organized by phase group."""
    subject_id: str
    by_phase: Dict[str, List[ReachTrajectory]] = field(default_factory=dict)
    total_reaches: int = 0
    errors: List[str] = field(default_factory=list)


def find_dlc_h5(processing_dir: Path, video_name: str) -> Optional[Path]:
    """Find the DLC .h5 file for a video in the Processing directory."""
    pattern = f"{video_name}DLC_*.h5"
    matches = list(processing_dir.glob(pattern))
    if not matches:
        return None
    # Return most recently modified if multiple exist
    return max(matches, key=lambda p: p.stat().st_mtime)


def load_dlc_trajectory(h5_path: Path, bodyparts: List[str],
                        start_frame: int, end_frame: int,
                        likelihood_threshold: float = 0.5) -> Optional[pd.DataFrame]:
    """
    Load XY coordinates for specified bodyparts within a frame range.

    Returns DataFrame with columns: frame, {bodypart}_x, {bodypart}_y
    Frames with likelihood below threshold are set to NaN.
    """
    try:
        df = pd.read_hdf(h5_path)
    except Exception as e:
        logger.warning(f"Failed to load {h5_path}: {e}")
        return None

    # Get the scorer name (first level of multi-index)
    scorer = df.columns.get_level_values(0)[0]

    # Slice to frame range
    frame_slice = df.iloc[start_frame:end_frame + 1]
    if frame_slice.empty:
        return None

    result = {'frame': np.arange(start_frame, min(end_frame + 1, start_frame + len(frame_slice)))}

    for bp in bodyparts:
        try:
            x = frame_slice[(scorer, bp, 'x')].values
            y = frame_slice[(scorer, bp, 'y')].values
            likelihood = frame_slice[(scorer, bp, 'likelihood')].values

            # Mask low-confidence frames
            mask = likelihood < likelihood_threshold
            x = x.astype(float)
            y = y.astype(float)
            x[mask] = np.nan
            y[mask] = np.nan

            result[f'{bp}_x'] = x
            result[f'{bp}_y'] = y
        except KeyError:
            logger.debug(f"Bodypart {bp} not found in {h5_path}")
            continue

    return pd.DataFrame(result)


def compute_hand_centroid(traj_df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    """Compute centroid of hand bodyparts (mean of available RH points)."""
    x_cols = [c for c in traj_df.columns if c.endswith('_x') and c != 'frame']
    y_cols = [c for c in traj_df.columns if c.endswith('_y') and c != 'frame']

    if not x_cols:
        return np.array([]), np.array([])

    x_centroid = traj_df[x_cols].mean(axis=1).values
    y_centroid = traj_df[y_cols].mean(axis=1).values
    return x_centroid, y_centroid


def parse_video_name(video_name: str) -> dict:
    """Parse video name to extract session date and subject ID.

    Format: YYYYMMDD_CNTxxxx_TypeRun
    Example: 20250624_CNT0115_P2 -> date=2025-06-24, subject=CNT_01_15
    """
    parts = video_name.split('_')
    if len(parts) < 3:
        return {'date': None, 'subject_id': None, 'tray_type': None, 'run': None}

    date_str = parts[0]
    subject_raw = parts[1]  # e.g. CNT0115
    tray_run = parts[2] if len(parts) > 2 else ''

    # Parse date
    try:
        session_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    except (IndexError, ValueError):
        session_date = None

    # Parse subject: CNT0115 -> CNT_01_15
    subject_id = None
    import re
    m = re.match(r'^([A-Z]+)(\d{2})(\d{2})$', subject_raw)
    if m:
        subject_id = f"{m.group(1)}_{m.group(2)}_{m.group(3)}"

    # Parse tray type and run number
    tray_type = tray_run[0] if tray_run else None
    run_num = tray_run[1:] if len(tray_run) > 1 else None

    return {
        'date': session_date,
        'subject_id': subject_id,
        'tray_type': tray_type,
        'run': run_num,
    }


def get_phase_for_day_offset(day_offset: int) -> Optional[str]:
    """Look up the phase name for a given day offset from cohort start."""
    for day, phase, _, _ in TIMELINE:
        if day == day_offset:
            return phase
    return None


def get_phase_group(phase_name: str) -> Optional[str]:
    """Map a specific phase name to its spaghetti-plot group."""
    return _PHASE_TO_GROUP.get(phase_name)


def load_reaches_for_video(processing_dir: Path, video_name: str) -> Optional[dict]:
    """Load _reaches.json for a video."""
    reaches_path = processing_dir / f"{video_name}_reaches.json"
    if not reaches_path.exists():
        return None
    with open(reaches_path) as f:
        return json.load(f)


def load_outcomes_for_video(processing_dir: Path, video_name: str) -> Optional[dict]:
    """Load _pellet_outcomes.json for a video."""
    outcomes_path = processing_dir / f"{video_name}_pellet_outcomes.json"
    if not outcomes_path.exists():
        return None
    with open(outcomes_path) as f:
        return json.load(f)


def extract_trajectories_for_subject(
    subject_id: str,
    processing_dir: Path,
    phase_groups: Optional[List[str]] = None,
    outcome_filter: str = 'retrieved',
    bodyparts: Optional[List[str]] = None,
    likelihood_threshold: float = 0.5,
    cohort_start_date: Optional[str] = None,
) -> SubjectTrajectories:
    """
    Extract all reach trajectories for a subject, organized by phase group.

    Args:
        subject_id: e.g. 'CNT_01_15'
        processing_dir: Path to MouseReach_Pipeline/Processing/
        phase_groups: Which phase groups to include (None = all)
        outcome_filter: 'retrieved', 'all', or specific outcome string
        bodyparts: Which bodyparts to track (default: HAND_BODYPARTS)
        likelihood_threshold: DLC confidence threshold
        cohort_start_date: Cohort start date (YYYY-MM-DD) for phase mapping

    Returns:
        SubjectTrajectories with reaches organized by phase group
    """
    if bodyparts is None:
        bodyparts = HAND_BODYPARTS

    result = SubjectTrajectories(subject_id=subject_id)

    # Convert subject_id to video filename format: CNT_01_15 -> CNT0115
    parts = subject_id.split('_')
    if len(parts) != 3:
        result.errors.append(f"Invalid subject_id format: {subject_id}")
        return result
    subject_video_prefix = f"{parts[0]}{parts[1]}{parts[2]}"

    # Find all videos for this subject in Processing/
    video_files = list(processing_dir.glob(f"*_{subject_video_prefix}_*.mp4"))
    video_names = set()
    for vf in video_files:
        # Extract video name (without extension)
        vname = vf.stem
        # Remove DLC suffix if present
        if 'DLC_' in vname:
            continue
        video_names.add(vname)

    if not video_names:
        result.errors.append(f"No videos found for {subject_id} in {processing_dir}")
        return result

    # Also check for h5 files to find video names
    h5_files = list(processing_dir.glob(f"*_{subject_video_prefix}_*DLC_*.h5"))
    for h5f in h5_files:
        vname = h5f.stem.split('DLC_')[0]
        video_names.add(vname)

    # Parse cohort start date for phase mapping
    from datetime import datetime, timedelta
    cohort_start = None
    if cohort_start_date:
        try:
            cohort_start = datetime.strptime(cohort_start_date, '%Y-%m-%d').date()
        except ValueError:
            pass

    for video_name in sorted(video_names):
        meta = parse_video_name(video_name)
        if not meta['date']:
            continue

        # Determine phase group
        phase_group = None
        if cohort_start and meta['date']:
            try:
                session_date = datetime.strptime(meta['date'], '%Y-%m-%d').date()
                day_offset = (session_date - cohort_start).days
                phase_name = get_phase_for_day_offset(day_offset)
                if phase_name:
                    phase_group = get_phase_group(phase_name)
            except ValueError:
                pass

        # If we couldn't map by date, try to infer from tray type and date range
        if phase_group is None and meta['tray_type'] == 'P':
            # Default: use date to guess phase (rough heuristic)
            phase_group = _guess_phase_from_date(meta['date'], cohort_start)

        if phase_group is None:
            continue

        # Skip if not in requested phase groups
        if phase_groups and phase_group not in phase_groups:
            continue

        # Load reaches and outcomes
        reaches_data = load_reaches_for_video(processing_dir, video_name)
        outcomes_data = load_outcomes_for_video(processing_dir, video_name)
        if not reaches_data:
            continue

        # Find DLC h5 file
        h5_path = find_dlc_h5(processing_dir, video_name)
        if not h5_path:
            result.errors.append(f"No DLC h5 found for {video_name}")
            continue

        # Build outcome lookup: segment_num -> outcome
        outcome_lookup = {}
        if outcomes_data and 'segments' in outcomes_data:
            for seg in outcomes_data['segments']:
                outcome_lookup[seg.get('segment_num')] = {
                    'outcome': seg.get('outcome', 'unknown'),
                    'causal_reach_id': seg.get('causal_reach_id'),
                }

        # Extract trajectories for matching reaches
        for segment in reaches_data.get('segments', []):
            seg_num = segment.get('segment_num')
            ruler_pixels = segment.get('ruler_pixels', 1.0)
            seg_outcome_info = outcome_lookup.get(seg_num, {})
            seg_outcome = seg_outcome_info.get('outcome', 'unknown')
            causal_reach_id = seg_outcome_info.get('causal_reach_id')

            for reach in segment.get('reaches', []):
                reach_id = reach.get('reach_id')
                outcome = seg_outcome

                # Filter by outcome
                if outcome_filter == 'retrieved':
                    # Only include causal reaches for retrievals
                    if outcome != 'retrieved':
                        continue
                    if causal_reach_id is not None and reach_id != causal_reach_id:
                        continue
                elif outcome_filter != 'all':
                    if outcome != outcome_filter:
                        continue

                # Skip excluded reaches
                if reach.get('exclude_from_analysis'):
                    continue

                start = reach.get('start_frame', 0)
                apex = reach.get('apex_frame', start)
                end = reach.get('end_frame', start)

                if end <= start:
                    continue

                # Load DLC trajectory for this reach
                traj_df = load_dlc_trajectory(
                    h5_path, bodyparts, start, end,
                    likelihood_threshold=likelihood_threshold
                )
                if traj_df is None or traj_df.empty:
                    continue

                # Compute hand centroid
                x, y = compute_hand_centroid(traj_df)
                if len(x) == 0:
                    continue

                # Create trajectory object
                traj = ReachTrajectory(
                    x=x, y=y,
                    frames=traj_df['frame'].values,
                    video_name=video_name,
                    reach_id=reach_id,
                    start_frame=start,
                    apex_frame=apex,
                    end_frame=end,
                    outcome=outcome,
                    phase_group=phase_group,
                    session_date=meta['date'],
                    duration_frames=end - start,
                    ruler_pixels=ruler_pixels,
                )

                if phase_group not in result.by_phase:
                    result.by_phase[phase_group] = []
                result.by_phase[phase_group].append(traj)
                result.total_reaches += 1

    return result


def _guess_phase_from_date(date_str: str, cohort_start) -> Optional[str]:
    """Rough heuristic to guess phase group when cohort start date is unknown."""
    # Without a cohort start date, we can't accurately map. Return None.
    return None


def align_trajectories(trajectories: List[ReachTrajectory],
                       align_to_start: bool = True) -> List[ReachTrajectory]:
    """Align trajectories so they all start from (0, 0)."""
    aligned = []
    for t in trajectories:
        if len(t.x) == 0:
            continue
        if align_to_start:
            x_aligned = t.x - t.x[0]
            y_aligned = t.y - t.y[0]
        else:
            x_aligned = t.x.copy()
            y_aligned = t.y.copy()

        aligned.append(ReachTrajectory(
            x=x_aligned, y=y_aligned,
            frames=t.frames, video_name=t.video_name,
            reach_id=t.reach_id, start_frame=t.start_frame,
            apex_frame=t.apex_frame, end_frame=t.end_frame,
            outcome=t.outcome, phase_group=t.phase_group,
            session_date=t.session_date,
            duration_frames=t.duration_frames,
            ruler_pixels=t.ruler_pixels,
        ))
    return aligned


def compute_mean_trajectory(trajectories: List[ReachTrajectory],
                            n_points: int = 50) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """Compute mean trajectory by time-normalizing all reaches to n_points."""
    if not trajectories:
        return None

    all_x = []
    all_y = []
    for t in trajectories:
        if len(t.x) < 2:
            continue
        # Interpolate to n_points
        t_norm = np.linspace(0, 1, n_points)
        t_orig = np.linspace(0, 1, len(t.x))
        # Skip if too many NaN
        valid = ~(np.isnan(t.x) | np.isnan(t.y))
        if valid.sum() < 3:
            continue
        x_interp = np.interp(t_norm, t_orig[valid], t.x[valid])
        y_interp = np.interp(t_norm, t_orig[valid], t.y[valid])
        all_x.append(x_interp)
        all_y.append(y_interp)

    if not all_x:
        return None

    mean_x = np.nanmean(all_x, axis=0)
    mean_y = np.nanmean(all_y, axis=0)
    return mean_x, mean_y
