"""Walk MouseReach's pipeline tree without reading superseded or scratch copies.

WHY THIS EXISTS
---------------
``Analyzed`` holds each video's finished results beside the video. It can also
hold trees whose files carry exactly the SAME names as live results but are
not live:

  * ``Archive/``         superseded generations, kept for history
  * ``DLC Model <N>/``   pose-only generation folders
  * ``Multi-Animal/``    collages
  * ``Folder Template/`` and ``UNKNOWN/``   not cohorts
  * any folder whose name starts with ``.`` or ``_``   scratch, in-flight or
    retired copies

A plain ``rglob`` descends into all of them and reads old results as current.
The worst case is ``mousedb import-reaches``: each import DELETES a video's
rows and inserts the file's rows, so a superseded ``_features.json`` would
silently replace live reach data with an older generation's kinematics.

The rule is the same one MouseReach's own read-only check applies
(``mousereach.watcher.reconcile``). mousedb must not import mousereach (each
tool stands alone; mousedb only reads their outputs), so the rule is restated
here -- keep the two in step.

Before this rule was added, the reach-import ledger was checked: no file it
had ever imported came from one of these folders, so pruning them drops
nothing that was being imported.
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Iterator

SKIP_DIR_NAMES = frozenset({"Archive", "Multi-Animal", "Folder Template", "UNKNOWN"})
SKIP_DIR_PREFIXES = (".", "_", "DLC Model")


def is_skipped_dir(name: str) -> bool:
    """True for a folder whose files must never be read as live results."""
    return name in SKIP_DIR_NAMES or name.startswith(SKIP_DIR_PREFIXES)


def iter_files(root, pattern: str) -> Iterator[Path]:
    """Every file under ``root`` whose name matches ``pattern`` (fnmatch, with
    the platform's case rules, as ``Path.rglob`` has), never descending into a
    skipped folder. ``root`` itself is always walked, whatever its name; a
    missing root yields nothing."""
    root = Path(root) if root else None
    if root is None or not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not is_skipped_dir(d)]
        for name in filenames:
            if fnmatch.fnmatch(name, pattern):
                yield Path(dirpath) / name
