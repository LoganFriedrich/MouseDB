"""The CURRENT exports: what mousedb knows, as CSVs a person can open now.

WHY THIS EXISTS
---------------
The end of the lab's workflow is a person asking "where is my data?" and
expecting a current CSV of everything the database holds, formatted to
ODC-SCI, with a data dictionary beside it. Until 2026-08-28 the answer was:
a 200 MB dump the sync overwrote, an ODC Excel export run by hand for one
cohort in January, and no dictionary anywhere. This module writes, and the
hourly refresh keeps current, one folder:

    Databases/exports/current/
        reach_data.csv                    one row per detected reach
        reach_data_DATA_DICTIONARY.csv
        manual_scores.csv                 one row per hand-scored pellet
        manual_scores_DATA_DICTIONARY.csv
        ODC_sessions_<cohort>.csv         one row per animal per session, per cohort
        ODC_sessions_DATA_DICTIONARY.csv
        MANIFEST.json                     when, from what, how many rows, problems
        README.txt                        what each file is, in plain words

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
from .. import DEFAULT_EXPORT_PATH

CURRENT_DIR = Path(DEFAULT_EXPORT_PATH) / "current"
DEFAULT_SNAPSHOT_DIR = Path("C:/LAB_ROOT/_analysis_snapshot")

# Column order of reach_data.csv: identity first, then the reach, then
# provenance. extended_features (a JSON blob per row) goes LAST so a person
# opening the file in Excel sees the numbers before the wall of text.
REACH_DATA_ORDER_FIRST = [
    "subject_id", "video_name", "session_date", "tray_type", "run_number",
    "segment_num", "reach_id", "reach_num",
]
REACH_DATA_ORDER_LAST = ["extended_features"]

README = """CURRENT EXPORTS -- everything mousedb knows, as of the time in MANIFEST.json.
These files are regenerated automatically (hourly on the processing server)
and by the "Where Is My Data" tab in MouseReach. Do not edit them; edit the
source (tracking sheets, review tools) and they will be regenerated.

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
                          (0 missed / 1 displaced / 2 retrieved), with the
                          session's derived phase. Definitions:
                          manual_scores_DATA_DICTIONARY.csv.
ODC_sessions_<cohort>.csv One row per animal per session in the ODC-SCI
                          2_ODC_Animal_Tracking shape (per-tray and daily
                          counts and percentages, weight, injury). Definitions:
                          ODC_sessions_DATA_DICTIONARY.csv.
MANIFEST.json             When these were written, from which snapshot, row
                          counts, and any problems (e.g. columns missing a
                          dictionary entry, which would fail an ODC upload).

ODC-SCI submission = a dataset CSV + its DATA_DICTIONARY CSV, together.
See Databases/docs/ODC-SCI_submission_standard.md.
"""


def _reach_data_csv(snapshot_dir: Path, out_dir: Path, manifest: dict) -> None:
    src = snapshot_dir / "reach_data.parquet"
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
    out = out_dir / "reach_data.csv"
    df[cols].to_csv(out, index=False)
    dd.write_dictionary("reach_data", out_dir / "reach_data_DATA_DICTIONARY.csv")
    manifest["files"]["reach_data.csv"] = {
        "rows": int(len(df)), "columns": int(len(cols)),
        "source": str(src), "source_mtime": _mtime(src),
        "undocumented_columns": dd.undocumented_columns("reach_data", cols),
    }
    if "extended_features" in df.columns:
        key = [c for c in ("subject_id", "video_name", "segment_num", "reach_id") if c in df.columns]
        side = out_dir / "reach_data_extended.parquet"
        df[key + ["extended_features"]].to_parquet(side, index=False)
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
    df.to_csv(out, index=False)
    dd.write_dictionary("manual_scores", out_dir / "manual_scores_DATA_DICTIONARY.csv")
    manifest["files"]["manual_scores.csv"] = {
        "rows": int(len(df)), "columns": int(len(cols)),
        "source": str(src), "source_mtime": _mtime(src),
        "undocumented_columns": dd.undocumented_columns("manual_scores", cols),
    }


def _odc_sessions_csvs(out_dir: Path, manifest: dict) -> None:
    """Per-cohort ODC session tables. Needs the ORM (database read)."""
    from ..database import get_db
    from .cohort_reports import export_odc_format
    from ..schema import Cohort

    db = get_db()
    with db.session() as s:
        cohorts = [c.cohort_id for c in s.query(Cohort).order_by(Cohort.cohort_id).all()]
    dd.write_dictionary("ODC_sessions", out_dir / "ODC_sessions_DATA_DICTIONARY.csv")
    for cid in cohorts:
        xlsx = out_dir / ("_tmp_%s_ODC.xlsx" % cid)
        try:
            export_odc_format(db, cid, xlsx)
            if not xlsx.exists():
                manifest["files"]["ODC_sessions_%s.csv" % cid] = {"rows": 0, "note": "no data"}
                continue
            df = pd.read_excel(xlsx)
            out = out_dir / ("ODC_sessions_%s.csv" % cid)
            df.to_csv(out, index=False)
            manifest["files"][out.name] = {
                "rows": int(len(df)), "columns": int(len(df.columns)),
                "undocumented_columns": dd.undocumented_columns("ODC_sessions", df.columns),
            }
        except Exception as e:
            manifest["problems"].append("ODC_sessions_%s: %s: %s" % (cid, type(e).__name__, e))
        finally:
            if xlsx.exists():
                xlsx.unlink()


def _mtime(p: Path) -> Optional[str]:
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return None


def refresh_current(snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR, out_dir: Path = CURRENT_DIR,
                    db_ok: bool = False) -> dict:
    """Rewrite the current-exports folder. Returns the manifest.

    ``db_ok``: the caller vouches that reading connectome.db is safe right
    now (the watcher's own loop, or no watcher running). Without it the
    per-cohort ODC session files are left as they were and the manifest
    says so."""
    snapshot_dir, out_dir = Path(snapshot_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "snapshot_dir": str(snapshot_dir),
        "files": {}, "problems": [], "complete": True,
        "odc_sessions_refreshed": bool(db_ok),
    }
    for fn in (_reach_data_csv, _manual_scores_csv):
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
