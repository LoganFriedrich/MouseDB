"""Where mousedb writes: the name of every output folder, in ONE place.

WHY THIS MODULE EXISTS
----------------------
People browse these folders, so their names are chosen to say what is inside
and are changed when a better name is found. With each name written out
wherever a path was built -- the exporter, the limited exports, the hand-score
file, the missing-video list, the weekly page, the status tab, the analysis
mirror -- one rename meant finding every copy, and a copy that was missed
went on writing into the old folder beside the new one. Every writer and
every reader inside mousedb therefore asks here. To rename a folder, change
its name below; nothing else in the code holds it.

THE LAYOUT (under <mousedb_root>)
---------------------------------
    exports/
        ANALYSES_MANIFEST.json            the analysis mirror's summary
        Behavior/
            All_Cohorts/                  the full export: everything the
                                          database holds (exporters.current)
            Selected_Cohorts/<name>/      limited exports (exporters.subsets)
            Hand_Scores/                  the hand-score file the reaching
                                          pipeline reads (hand_scores_file)
            Sessions_Without_Video/       hand-scored sessions with no video
                                          (missing_videos)
        Tissue/
            <analysis>/                   the mirror of each tissue analysis
                                          (import_analyses)
    reports/
        MouseReach_Video_Processing_Progress/    the weekly page (weekly_report)
    _archived/
        exports_current/, exports_subsets/       files the exporters replaced

Moving an existing installation to new names: stop the jobs, rename the
folders on disk to match, then install this code. A folder left under its old
name is not read and not rewritten.

A program outside mousedb that reads one of these folders is given the path
by configuration (the reaching pipeline's hand-score path, for one), so a
rename here also means setting that path again.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

EXPORTS = "exports"
BEHAVIOR = "Behavior"
TISSUE = "Tissue"
ALL_COHORTS = "All_Cohorts"
SELECTED_COHORTS = "Selected_Cohorts"
HAND_SCORES = "Hand_Scores"
SESSIONS_WITHOUT_VIDEO = "Sessions_Without_Video"

REPORTS = "reports"
VIDEO_PROGRESS = "MouseReach_Video_Processing_Progress"
# The weekly page's file names start with this ("<stem> YYYY-MM-DD.html",
# "<stem> - LATEST.html"): the folder's name in words.
VIDEO_PROGRESS_FILE_STEM = "MouseReach video processing progress"

ARCHIVE = "_archived"
ARCHIVE_ALL_COHORTS = "exports_current"
ARCHIVE_SELECTED_COHORTS = "exports_subsets"


def _root(root: Optional[Path] = None) -> Path:
    """``root`` when given, else the configured mousedb_root (unset: stops with
    the command that sets it)."""
    if root is not None:
        return Path(root)
    from .config import require
    return Path(require("mousedb_root"))


def exports_dir(root: Optional[Path] = None) -> Path:
    return _root(root) / EXPORTS


def behavior_dir(root: Optional[Path] = None) -> Path:
    return exports_dir(root) / BEHAVIOR


def all_cohorts_dir(root: Optional[Path] = None) -> Path:
    """The full export ("current exports"): everything the database holds."""
    return behavior_dir(root) / ALL_COHORTS


def selected_cohorts_dir(root: Optional[Path] = None) -> Path:
    """The limited exports, one folder per saved definition."""
    return behavior_dir(root) / SELECTED_COHORTS


def hand_scores_dir(root: Optional[Path] = None) -> Path:
    return behavior_dir(root) / HAND_SCORES


def sessions_without_video_dir(root: Optional[Path] = None) -> Path:
    return behavior_dir(root) / SESSIONS_WITHOUT_VIDEO


def tissue_dir(root: Optional[Path] = None) -> Path:
    """Where the analysis mirror keeps each tissue analysis's export files."""
    return exports_dir(root) / TISSUE


def video_progress_dir(root: Optional[Path] = None) -> Path:
    """Where the weekly page is written."""
    return _root(root) / REPORTS / VIDEO_PROGRESS


def shown(folder_of, *more: str) -> str:
    """A folder as the help texts and messages name it: its path inside
    <mousedb_root>, with forward slashes -- ``shown(hand_scores_dir)`` is
    "exports/Behavior/Hand_Scores". Built from the same function the code
    writes through, so the words cannot name a folder the code does not use."""
    return (folder_of(Path(".")).joinpath(*more)).as_posix()


def _root_when(folder: Path, folder_of) -> Optional[Path]:
    """The mousedb root ``folder`` sits in when it is laid out as
    ``folder_of(<root>)``, else None. Decided by the folder's own path, so it
    holds for any root (a test's scratch root as well as the configured one)."""
    folder = Path(folder)
    depth = len(folder_of(Path(".")).parts)
    if len(folder.parts) < depth:
        return None
    root = folder.parents[depth - 1]
    return root if folder_of(root) == folder else None


def all_cohorts_archive(folder: Path) -> Optional[Path]:
    """Where files replaced in the full export go when ``folder`` is a root's
    full-export folder: <root>/_archived/exports_current. None for any other
    folder (the caller then archives inside the folder itself)."""
    root = _root_when(folder, all_cohorts_dir)
    return root / ARCHIVE / ARCHIVE_ALL_COHORTS if root is not None else None


def selected_cohorts_archive(folder: Path) -> Optional[Path]:
    """The same for the limited exports: <root>/_archived/exports_subsets."""
    root = _root_when(folder, selected_cohorts_dir)
    return root / ARCHIVE / ARCHIVE_SELECTED_COHORTS if root is not None else None
