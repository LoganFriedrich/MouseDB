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

from ..config import require, snapshot_dir as _snapshot_dir

CURRENT_DIR = (Path(DEFAULT_EXPORT_PATH) / "current") if DEFAULT_EXPORT_PATH else None
DEFAULT_SNAPSHOT_DIR = _snapshot_dir()  # None until configured

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
                          The 161-value extended feature block per reach (per
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

    # The extended block (161 per-paw-point values, stored as one JSON string
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


def _odc_reaches_csvs(snapshot_dir: Path, out_dir: Path, manifest: dict) -> None:
    """Per-cohort per-reach ODC files (see exporters.odc_reaches).

    Skipped when nothing they are built from has changed since the last write
    (same snapshot table sizes and row counts, same study-facts file): they are
    large, and every refresh would otherwise rewrite hundreds of MB on the
    share for no change. The manifest then carries the previous entries."""
    import pyarrow.parquet as pq
    from . import odc_reaches
    from .. import study_facts
    sig = {}
    for name in ("reach_data", "subjects", "pellet_scores", "animal_records"):
        f = snapshot_dir / ("%s.parquet" % name)
        if f.exists():
            sig[name] = [f.stat().st_size, pq.ParquetFile(f).metadata.num_rows]
    ff = study_facts.facts_path()
    sig["study_facts"] = ff.read_text(encoding="utf-8") if ff.exists() else ""
    # The signature covers the INPUTS. It cannot notice that the exporter itself now
    # writes different columns or different words, so this number stands for the output
    # format and is bumped by hand whenever that changes -- otherwise a node still
    # holding the previous code skips the rewrite and the old files look current.
    # BUMP THIS whenever the exporter writes a different column, a different word
    # in a cell, or a different dictionary entry. Forgetting is not a small
    # mistake: the run silently skips the export, reuses the previous run's
    # record of what it wrote, and reports the state of files it did not look
    # at -- which happened on 2026-10-14, where two freshly written dictionary
    # entries were reported missing because nothing had been rewritten.
    #   2 = letter cohorts carry their letter (ASPA_04_D)
    #   3 = two documents per cohort (complete record + shareable summary), the extended
    #       per-reach measurements included, and no empty cells in either
    #   9 = the analysis document: numbers in numeric columns, NA for absent, an
    #       entry index, the session totals on every reach row
    #  10 = every measurement we hold is carried (the flat per-reach columns, the
    #       whole-hand speeds, per-reach attention, the day's body weight), column
    #       names obey ODC's naming rules, and no cell can break a CSV reader
    #  11 = the body-weight percentage in one unit: cohorts stored as whole
    #       percentages were being multiplied by 100 again (9,000%)
    #  12 = entries with an identified problem are left out of the analysis
    #       documents (export_exclusions), with an analysis_EXCLUSIONS.csv
    #       report; survival counts only on a surgery proven by its weight
    #  13 = one data dictionary per kind of document instead of one per cohort
    #       (ODC_reaches_DATA_DICTIONARY.csv, reaches_summary_DATA_DICTIONARY.csv);
    #       the manifest names every report file
    #  14 = pellets_presented is the tray's 20 and every percentage is out of it
    #       (retrieved, displaced, contacted, missed, from both streams); the
    #       count of pellets with a reach is kept as pellets_reached_for
    #  15 = pellets are measured: pellets_in_video (from the per-pellet table) and
    #       pellets_hand_scored; video totals count every pellet; a count other
    #       than 20 is an identified error (export_exclusions check 7)
    #  16 = files grouped into <project>/<cohort>/ folders, plus a combined file
    #       per kind per project (exporters.layout)
    #  17 = a project listed under a grant sits in the grant's folder
    sig["output_format"] = 17
    # Which grant holds which project decides where the files go, so a change
    # to it must re-export even when no data changed.
    from ..grants import grants as _grants
    sig["grants"] = _grants()
    sig_file = out_dir / ".odc_reaches_signature.json"
    try:
        old = json.loads(sig_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    if old.get("signature") == sig and old.get("files") and all(
            (out_dir / n).exists() for n in old["files"] if old["files"][n].get("rows")):
        manifest["files"].update(old["files"])
        return
    part = {"files": {}, "problems": []}
    odc_reaches.export(snapshot_dir, out_dir, manifest=part, staging_dir=_staging_dir(snapshot_dir))
    for k in ("archived_flat_layout_files", "archived_per_cohort_dictionaries"):
        if k in part:
            manifest[k] = part[k]
    manifest["files"].update(part["files"])
    manifest["problems"].extend(part["problems"])
    if not part["problems"]:
        sig_file.write_text(json.dumps({"signature": sig, "files": part["files"]}, indent=1),
                            encoding="utf-8")


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
            manifest["problems"].append("ODC_sessions_%s: %s: %s" % (cid, type(e).__name__, e))
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


def refresh_current(snapshot_dir: Path = None, out_dir: Path = None,
                    db_ok: bool = False) -> dict:
    """Rewrite the current-exports folder. Returns the manifest.

    ``db_ok``: the caller vouches that reading connectome.db is safe right
    now (the watcher's own loop, or no watcher running). Without it the
    per-cohort ODC session files are left as they were and the manifest
    says so."""
    snapshot_dir = snapshot_dir or require("snapshot_dir")
    out_dir = out_dir or CURRENT_DIR or (require("mousedb_root") / "exports" / "current")
    snapshot_dir, out_dir = Path(snapshot_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Start from an empty staging folder: every file copied onto the share
    # this run was written this run (see _staging_dir).
    import shutil
    shutil.rmtree(_staging_dir(snapshot_dir), ignore_errors=True)
    _staging_dir(snapshot_dir)
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "snapshot_dir": str(snapshot_dir),
        "files": {}, "problems": [], "complete": True,
        "odc_sessions_refreshed": bool(db_ok),
    }
    for fn in (_reach_data_csv, _manual_scores_csv, _odc_reaches_csvs):
        try:
            fn(snapshot_dir, out_dir, manifest)
        except Exception as e:
            manifest["problems"].append("%s: %s: %s" % (fn.__name__, type(e).__name__, e))
    if db_ok:
        try:
            _odc_sessions_csvs(out_dir, manifest)
        except Exception as e:
            manifest["problems"].append("ODC sessions: %s: %s" % (type(e).__name__, e))
    else:
        manifest["problems"].append(
            "ODC_sessions_*.csv not refreshed this run (database not readable now); "
            "the files present are from the previous refresh")

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


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    ap.add_argument("--out-dir", type=Path, default=CURRENT_DIR)
    ap.add_argument("--db-ok", action="store_true",
                    help="Also refresh the per-cohort ODC session files (reads "
                         "connectome.db -- only when no watcher is writing).")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    m = refresh_current(args.snapshot_dir, args.out_dir, db_ok=args.db_ok)
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
