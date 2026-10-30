"""Where each export file lives: one folder per project, one per cohort inside it,
and a combined file per kind beside the cohort folders.

    exports/current/
        reach_data.csv, manual_scores.csv, ...       every project, as before
        <kind>_DATA_DICTIONARY.csv                    one per kind, shared by every file of it
        analysis_EXCLUSIONS.csv
        [<GRANT>/]<PROJECT>/                          e.g. GrantA/PROJA, or PROJC in no grant
            <PROJECT>_all_cohorts files               every cohort of the project, one file per kind
            <COHORT>/                                 e.g. PROJA_05, PROJC_04_D
                ODC_reaches_<COHORT>.csv, reaches_<COHORT>_summary.csv,
                reaches_<COHORT>_analysis.csv, sessions_<COHORT>_analysis.csv,
                ODC_sessions_<COHORT>.csv

WHY (Logan, 2026-10-29): with every cohort's files side by side in one folder, a
person looking for one cohort, or one project, read past a hundred names. Grouped
by project and then cohort, the folder answers "where is CNT_05?" by its shape.
The combined file answers the other common question -- "all of CNT at once" --
without each person concatenating the cohort files themselves. A project the lab
settings list under a grant (mousedb.grants) sits inside the grant's folder
(Logan, 2026-10-30: one grant funds two projects, and they belong together).

The cohort label stays in every file name although the folder already says it:
a file copied out of its folder must still say which cohort it is.

WHY the combined file is built from the cohort files ON DISK, after they are
written, and not from the data in memory: it must be exactly those files put
together, and an export of one cohort (``--cohort``) must still produce a
combined file holding every cohort -- the ones not rewritten this run included.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from . import missing

ALL_COHORTS = "all_cohorts"

# Every per-cohort kind: (file name pattern with {label}, word for a column the
# file of one cohort does not have, whether the file carries an entry index).
#
# The word for an absent column follows the kind's own rule. The ODC documents
# may not hold an empty cell (an ODC-SCI upload refuses one), and a column one
# cohort's file lacks is a measurement that cohort's sheet did not record. The
# analysis documents spell every absence NA, which is what a statistics package
# reads as missing.
KINDS = {
    "ODC_reaches": ("ODC_reaches_{label}.csv", missing.NOT_RECORDED, False),
    "reaches_summary": ("reaches_{label}_summary.csv", missing.NOT_RECORDED, False),
    "reaches_analysis": ("reaches_{label}_analysis.csv", "NA", True),
    "sessions_analysis": ("sessions_{label}_analysis.csv", "NA", True),
    "ODC_sessions": ("ODC_sessions_{label}.csv", missing.NOT_RECORDED, False),
}
REACH_KINDS = ("ODC_reaches", "reaches_summary", "reaches_analysis", "sessions_analysis")
SESSION_KINDS = ("ODC_sessions",)

# The entry index is "a plain counter over the rows of this file" (its dictionary
# entry). In the combined file it is counted again over the combined rows, so it
# keeps meaning exactly that; each cohort's own file keeps its own count.
ENTRY_INDEX = "entry_index"

# A per-cohort file at the top of the folder: the layout before 2026-10-29.
_FLAT_COHORT_FILE = re.compile(
    r"^(ODC_reaches_[A-Z]+_\d{2}(_[A-Z])?|ODC_sessions_[A-Z]+_\d{2}(_[A-Z])?"
    r"|(reaches|sessions)_[A-Z]+_\d{2}(_[A-Z])?_(summary|analysis))\.csv$")

CHUNK_ROWS = 200_000


def project_of(cohort_id: str) -> str:
    """CNT_05 -> CNT; ASPA_04 -> ASPA; PROJAB_02 -> PROJAB (the letters, never a prefix
    match: PROJAB is its own project, not a kind of PROJA)."""
    return str(cohort_id).rsplit("_", 1)[0].upper()


def project_of_label(label: str) -> str:
    """The project of a file-name label: CNT_05 -> CNT, ASPA_04_D -> ASPA."""
    return project_of(re.sub(r"_[A-Z]$", "", str(label)))


def file_name(kind: str, label: str) -> str:
    return KINDS[kind][0].format(label=label)


def project_dir(root: Path, project: str) -> Path:
    """A project's folder: <root>/<GRANT>/<PROJECT> when the lab settings list the
    project under a grant (mousedb.grants), else <root>/<PROJECT>. WHY: one grant
    may fund several projects, and its projects belong together (2026-10-30)."""
    from ..grants import grant_of
    grant = grant_of(project)
    return Path(root) / grant / project if grant else Path(root) / project


def cohort_dir(root: Path, cohort_id: str, label: str) -> Path:
    return project_dir(root, project_of(cohort_id)) / label


def cohort_file(root: Path, kind: str, cohort_id: str, label: str) -> Path:
    return cohort_dir(root, cohort_id, label) / file_name(kind, label)


def combined_file(root: Path, kind: str, project: str) -> Path:
    return project_dir(root, project) / file_name(kind, "%s_%s" % (project, ALL_COHORTS))


def current_root() -> Path:
    """The live exports folder: <mousedb_root>/exports/current."""
    from ..config import export_path, require
    require("mousedb_root")              # unset: stops with the command that sets it
    return export_path() / "current"


def locate(kind: str, cohort_id: str, root: Optional[Path] = None) -> Path:
    """Where the export file of one kind for one cohort is. Raises FileNotFoundError.

    For anything OUTSIDE the exporter that reads the exports (an analysis script,
    a notebook): ask here instead of writing the path out. WHY (2026-10-29): when
    the files moved into project/cohort folders, a script that had the old path
    written into it broke; one that asks here follows any later move unchanged.

    ``kind`` is a key of KINDS. Give a cohort for its own file,
    locate("reaches_analysis", "CNT_05"), or a project for the combined file,
    locate("reaches_analysis", "CNT") -- every CNT cohort in one file.
    A cohort's folder may carry more than its id (ASPA_04 -> ASPA_04_D).
    """
    root = Path(root) if root else current_root()
    if re.fullmatch(r"[A-Za-z]+", str(cohort_id)):
        path = combined_file(root, kind, str(cohort_id).upper())
        if path.is_file():
            return path
        raise FileNotFoundError("no combined %s file for project %s at %s" % (kind, cohort_id, path))
    cohort_id = str(cohort_id).upper()
    pdir = project_dir(root, project_of(cohort_id))
    labels = sorted(d.name for d in pdir.iterdir() if d.is_dir()
                    and (d.name == cohort_id or re.fullmatch(re.escape(cohort_id) + r"_[A-Z]", d.name))
                    ) if pdir.is_dir() else []
    for label in labels:
        path = cohort_file(root, kind, cohort_id, label)
        if path.is_file():
            return path
    raise FileNotFoundError("no %s file for cohort %s under %s" % (kind, cohort_id, pdir))


def key(root: Path, path: Path) -> str:
    """A file's name in MANIFEST.json: its path inside the exports folder, with /."""
    return Path(path).relative_to(Path(root)).as_posix()


def _cohort_files(root: Path, kind: str, project: str) -> List[Path]:
    """This project's per-cohort files of one kind, in cohort order."""
    pdir = project_dir(root, project)
    if not pdir.is_dir():
        return []
    found = []
    for d in sorted(p for p in pdir.iterdir() if p.is_dir() and not p.name.startswith("_")):
        f = d / file_name(kind, d.name)
        if f.is_file():
            found.append(f)
    return found


def _header(path: Path) -> List[str]:
    return list(pd.read_csv(path, nrows=0, dtype=str).columns)


def combine(root: Path, kind: str, project: str, staging: Optional[Path] = None) -> Optional[dict]:
    """Write <PROJECT>/<kind>_<PROJECT>_all_cohorts: every cohort file of the kind,
    one after the other, in cohort order. Returns its manifest entry (None when
    the project has no file of this kind).

    Cells are carried as text, untouched, so a value reads exactly as it does in
    its cohort's file. Columns are the union, in the order first met; a column a
    cohort's file lacks is filled with the kind's word for absent (see KINDS).
    Read and written in chunks: a project's combined record runs to gigabytes.
    """
    root = Path(root)
    sources = _cohort_files(root, kind, project)
    if not sources:
        return None
    columns: List[str] = []
    lacking: Dict[str, List[str]] = {}
    headers = {f: _header(f) for f in sources}
    for f in sources:
        for c in headers[f]:
            if c not in columns:
                columns.append(c)
    for f in sources:
        missing_here = [c for c in columns if c not in headers[f]]
        if missing_here:
            lacking[f.parent.name] = missing_here
    _, absent, counted = KINDS[kind]
    final = combined_file(root, kind, project)
    write_to = (Path(staging) / project / final.name) if staging else final.with_name(final.name + ".tmp")
    write_to.parent.mkdir(parents=True, exist_ok=True)
    rows, first = 0, True
    with open(write_to, "w", encoding="utf-8", newline="") as out:
        for f in sources:
            for chunk in pd.read_csv(f, dtype=str, keep_default_na=False, chunksize=CHUNK_ROWS):
                chunk = chunk.reindex(columns=columns, fill_value=absent)
                if counted and ENTRY_INDEX in chunk.columns:
                    chunk[ENTRY_INDEX] = [str(i) for i in range(rows + 1, rows + len(chunk) + 1)]
                chunk.to_csv(out, index=False, header=first)
                first = False
                rows += len(chunk)
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.with_name(final.name + ".tmp")
    if staging:
        shutil.copyfile(write_to, tmp)
    tmp.replace(final)
    entry = {"rows": rows, "columns": len(columns),
             "cohorts": [f.parent.name for f in sources],
             "note": "every %s cohort's %s file, one after the other" % (project, kind)}
    if lacking:
        entry["columns_filled_for_cohorts_without_them"] = {
            label: {"columns": cols, "filled_with": absent} for label, cols in lacking.items()}
    return entry


def combine_all(root: Path, kinds: Iterable[str], projects: Iterable[str],
                manifest: dict, staging: Optional[Path] = None) -> None:
    """combine() every kind for every project, recording each in the manifest.
    A failure is a manifest problem, never a crash: the cohort files are already
    written and stay usable without the combined one."""
    for project in sorted(set(projects)):
        for kind in kinds:
            try:
                entry = combine(root, kind, project, staging)
                if entry is not None:
                    manifest["files"][key(root, combined_file(root, kind, project))] = entry
            except Exception as e:
                manifest["problems"].append("combined %s %s file not written: %s: %s"
                                            % (project, kind, type(e).__name__, e))


def archive_flat_layout(root: Path) -> List[str]:
    """Move per-cohort files left at the top of the folder by the layout before
    2026-10-29 into the archive (never deleted), once their replacements exist in
    the cohort folders. One left behind is harmless but misleading: it looks
    current and is not rewritten."""
    from .odc_reaches import archive_old_names
    root = Path(root)
    stale = []
    for f in sorted(root.glob("*.csv")):
        if not _FLAT_COHORT_FILE.match(f.name):
            continue
        stem = f.name[:-4]
        label = None
        for kind, (pattern, _, _) in KINDS.items():
            m = re.fullmatch(pattern.replace(".csv", "").replace("{label}", "(?P<label>.+)"), stem)
            if m:
                label = m.group("label")
                if (project_dir(root, project_of_label(label)) / label / f.name).is_file():
                    stale.append(f.name)
                break
    return archive_old_names(root, stale, "flat_layout") if stale else []


def archive_moved_project_folders(root: Path) -> List[str]:
    """Move a project's folder left at <root>/<PROJECT> into the archive (never
    deleted) once the project's grant folder holds its replacement, i.e. once a
    grant has been set for it and the export has written the new folder. One
    left behind is harmless but misleading: it looks current and is not
    rewritten. Returns what was moved."""
    from ..grants import grants
    from .odc_reaches import archive_old_names
    root = Path(root)
    moved: List[str] = []
    for grant, projects in grants().items():
        for project in projects:
            old, new = root / project, root / grant / project
            if old.is_dir() and new.is_dir() and any(new.iterdir()):
                moved += archive_old_names(root, [project], "grant_layout")
    return moved
