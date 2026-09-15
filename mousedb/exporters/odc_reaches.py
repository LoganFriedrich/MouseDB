"""Per-reach ODC-SCI dataset: one row per reach, with the animal and session repeated.

WHY THIS EXISTS
---------------
Collaborators receive reaching data as ONE flat table per cohort: every row is a
reach, and every row also carries that animal's details (strain, sex, surgery
parameters, drugs, injury level ...) and that session's details (date, tray,
phase), so the file can be filtered and modelled without joins. Building it by
hand once took weeks and left the animal columns empty. This writes it from what
mousedb already holds, for every cohort, beside a data dictionary that describes
every column (an ODC-SCI upload fails without one).

Column blocks, in order:
  1. animal common data elements  (ODC_REACH_ANIMAL in data_dictionary)
  2. session                      (ODC_REACH_SESSION)
  3. tracking-sheet columns       every column of the animal-level tabs, as written,
                                  prefixed by tab: Metadata_, Contusion_, Injection_,
                                  ODC_ (see mousedb.animal_records)
  4. reach                        MouseReach's own reach columns (REACH_DATA)
  5. session totals               video and hand-score counts (ODC_REACH_TOTALS)

Inputs are the analysis SNAPSHOT tables (never connectome.db) plus the study-facts
file and the lab name. Nothing is guessed: a value no source records is empty.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

from . import data_dictionary as dd

TAB_PREFIX = {"0a_Metadata": "Metadata", "3b_Manual_Tray": "Tray", "4_Contusion_Injury_Details": "Contusion",
              "5_SC_Injection_Details": "Injection", "ODC": "ODC"}
REACH_DROP = {"id", "extended_features"}
ANIMAL_COLUMNS = [r["VariableName"] for r in dd.ODC_REACH_ANIMAL]
SESSION_COLUMNS = [r["VariableName"] for r in dd.ODC_REACH_SESSION]
TOTAL_COLUMNS = [r["VariableName"] for r in dd.ODC_REACH_TOTALS]
DISPLACED = ("displaced_sa", "displaced_outside")


def _blank(v) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    t = str(v).strip()
    # "NAME OF ..." is a placeholder a template left in a cell, not a value.
    return t == "" or t == "?" or t.upper().startswith("NAME OF")


def _first(d: dict, *keys) -> str:
    for k in keys:
        v = d.get(k)
        if not _blank(v):
            return str(v)
    return ""


def sheet_column(tab: str, record_no: int, field: str) -> str:
    base = TAB_PREFIX.get(tab, tab)
    return "%s%s_%s" % (base, "" if int(record_no) == 1 else int(record_no), field)


def lab_subject_id(subject_id: str) -> str:
    """The lab's own id: frozen letter cohorts are decoded (ASPA_11_03 -> K03)."""
    if subject_id.startswith("ASPA_"):
        from ..cohort_sheets import aspa_letter
        letter = aspa_letter(subject_id)
        if letter:
            return "%s%s" % (letter, subject_id.rsplit("_", 1)[1])
    return subject_id


def _date(v) -> Optional[date]:
    if _blank(v):
        return None
    try:
        return pd.to_datetime(str(v)).date()
    except (ValueError, TypeError):
        return None


def _animal(sid: str, cohort_id: str, sheet: dict, subject: dict, facts: dict, lab: str) -> dict:
    survived = [str(v).strip().upper() for k, v in sheet.items()
                if k.endswith("_Survived") and not _blank(v)]
    died = any(s in ("N", "NO") for s in survived)
    if died:
        excl, reason = "Total exclusion", "Did not survive a surgery (tracking sheet Survived = N)"
    elif survived:
        excl, reason = "No exclusion", ""
    else:
        excl, reason = "", ""
    details = "; ".join("%s %s" % (label, sheet[k]) for k, label in (
        ("Contusion_Actual_kd", "force (kdyn)"), ("Contusion_Actual_displacement", "displacement (um)"),
        ("Contusion_Actual_Velocity", "velocity (mm/s)"), ("Contusion_Actual_Dwell", "dwell (s)"),
        ("ODC_Observed_Force (k dynes)", "force (kdyn)"), ("ODC_Observed_Displacement (u)", "displacement (um)"),
    ) if not _blank(sheet.get(k)))
    injury = _first(sheet, "Contusion_Surgery_Type", "ODC_Injury_type")
    intended = _first(sheet, "Contusion_Intended_kd")
    if injury and intended:
        injury = "%s-%skD" % (injury, intended)
    sex = subject.get("sex")
    if _blank(sex):
        sex = _first(sheet, "ODC_Organism_Sex", "Metadata_Sex", "Tray_Sex")
    sex = {"FEMALE": "F", "MALE": "M"}.get(str(sex).strip().upper(), str(sex).strip().upper() if sex else "")
    author = " ".join(x for x in (_first(sheet, "ODC_Author_First_Name"), _first(sheet, "ODC_Author_Last_Name")) if x)
    return {
        "SubjectID": lab_subject_id(sid),
        "SpeciesTyp": _first(sheet, "ODC_Organism_Name") or facts.get("SpeciesTyp", ""),
        "SpeciesStrainTyp": _first(sheet, "ODC_Organism_Strain") or facts.get("SpeciesStrainTyp", ""),
        "Animal_origin": _first(sheet, "ODC_Organism_Vendor") or facts.get("AnimalSourceNam", ""),
        "_age_at_surgery": _first(sheet, "ODC_Surgery_age"),
        "_dob": _date(subject.get("date_of_birth")) or _date(sheet.get("Metadata_Date_of_Birth")),
        "BodyWgtMeasrVal": _first(sheet, "Contusion_Subject_Weight (g)", "ODC_Surgery_weight"),
        "SexTyp": sex,
        "InjGroupAssignTyp": _first(sheet, "ODC_Study_Group") or cohort_id,
        "Laboratory": lab,
        "StudyLeader": facts.get("StudyLeader", "") or author,
        "Exclusion_in_origin_study": excl,
        "Exclusion_reason": reason,
        "Cause_of_Death": "",
        "Injury_device": _first(sheet, "ODC_Injury_device") or (facts.get("Injury_device", "") if injury else ""),
        "Injury_level": _first(sheet, "Contusion_Contusion_Location", "ODC_Spine_Injury_level"),
        "Injury_details": details,
        "Injury_Type": injury,
        "_injury_date": _date(sheet.get("Contusion_Surgery_Date")) or _date(sheet.get("ODC_Surgery_Date")),
    }


def build_cohort(cohort_id: str, reach: pd.DataFrame, subjects: pd.DataFrame,
                 records: pd.DataFrame, pellets: pd.DataFrame,
                 facts: Callable[[str], Dict[str, str]], lab: str = ""):
    """(dataframe, dictionary rows) for one cohort; an empty frame when it has no reaches."""
    rc = reach[reach["subject_id"].astype(str).str.startswith(cohort_id + "_")].copy()
    if rc.empty:
        return rc, []

    # 3. tracking-sheet columns, pivoted wide per animal, in tab order then sheet order
    sheet_cols: List[tuple] = []
    wide: Dict[str, dict] = {}
    if records is not None and not records.empty:
        rr = records[records["cohort_id"] == cohort_id].copy()
        if not rr.empty:
            order = {t: i for i, t in enumerate(TAB_PREFIX)}
            rr["_tab_order"] = rr["source_tab"].map(lambda t: order.get(t, len(order)))
            rr["_row"] = range(len(rr))
            rr = rr.sort_values(["_tab_order", "record_no", "_row"])
            rr["column"] = [sheet_column(t, n, f) for t, n, f in zip(rr["source_tab"], rr["record_no"], rr["field"])]
            seen = set()
            for t, n, f, c in zip(rr["source_tab"], rr["record_no"], rr["field"], rr["column"]):
                if c not in seen:
                    seen.add(c)
                    sheet_cols.append((c, t, f, int(n)))
            for sid, g in rr.groupby("subject_id"):
                wide[sid] = dict(zip(g["column"], g["value"]))

    subj = {}
    if subjects is not None and not subjects.empty:
        subj = {r["subject_id"]: r for r in subjects.to_dict("records")}
    animals = {sid: _animal(sid, cohort_id, wide.get(sid, {}), subj.get(sid, {}), facts(sid) or {}, lab)
               for sid in rc["subject_id"].unique()}

    # 4 + 2. reach columns and the per-row session columns
    # Columns are collected in a dict and joined once (adding ~150 columns one by one
    # to a large frame is slow and fragments it).
    reach_cols = [c for c in rc.columns if c not in REACH_DROP]
    test_date = pd.to_datetime(rc["session_date"], errors="coerce")
    tray_id = rc["tray_type"].fillna("").astype(str) + rc["run_number"].astype("Int64").astype(str).replace("<NA>", "")
    a = pd.DataFrame([animals[s] for s in rc["subject_id"]], index=rc.index)
    cols: Dict[str, object] = {c: a[c] for c in ANIMAL_COLUMNS if c in a.columns}
    dob = pd.to_datetime(a["_dob"], errors="coerce")
    weeks = ((test_date - dob).dt.days / 7.0).round(1)
    cols["AgeVal"] = pd.Series([("%s" % w) if pd.notna(w) else s for w, s in zip(weeks, a["_age_at_surgery"])],
                               index=rc.index)
    cols["Test_Date"] = test_date.dt.strftime("%Y-%m-%d")
    cols["Tray_ID"] = tray_id
    cols["Session_ID"] = a["SubjectID"] + "-" + test_date.dt.strftime("%Y%m%d").fillna("") + "-" + tray_id
    cols["Injury_Type"] = a["Injury_Type"]
    cols["Test_Type"] = rc["test_phase"] if "test_phase" in rc else pd.Series("", index=rc.index)
    cols["Test_Type_Grouped"] = rc["phase_group"] if "phase_group" in rc else pd.Series("", index=rc.index)
    dpi = (test_date - pd.to_datetime(a["_injury_date"], errors="coerce")).dt.days
    cols["Days_Post_Injury"] = dpi.where(dpi >= 0).astype("Int64")
    for c, _t, _f, _n in sheet_cols:
        cols[c] = pd.Series([wide.get(s, {}).get(c, "") for s in rc["subject_id"]], index=rc.index)
    for c in reach_cols:
        cols[c] = rc[c]

    # 5. session totals: per video (pipeline) and per tray (hand scores)
    video = rc["video_name"]
    cols["Total_Swipes_AI"] = video.map(video.value_counts())
    seg = rc.groupby(["video_name", "segment_num"]).agg(o=("segment_outcome", "first"),
                                                         att=("attention_score", "first")).reset_index()
    per_video = seg.groupby("video_name").agg(
        att=("att", "mean"),
        disp=("o", lambda s: int(s.isin(DISPLACED).sum())),
        retr=("o", lambda s: int((s == "retrieved").sum())))
    cols["Attention_AI"] = video.map(per_video["att"])
    cols["Video_Displaced"] = video.map(per_video["disp"]).astype("Int64")
    cols["Video_Retrieved"] = video.map(per_video["retr"]).astype("Int64")
    cols["Video_Contacted"] = cols["Video_Displaced"] + cols["Video_Retrieved"]
    empty = pd.Series(pd.array([pd.NA] * len(rc), dtype="Int64"), index=rc.index)
    for c in ("Manual_Displaced", "Manual_Retrieved", "Manual_Contacted", "Contacted_Match"):
        cols[c] = empty
    if pellets is not None and not pellets.empty:
        p = pellets[pellets["subject_id"].astype(str).str.startswith(cohort_id + "_")].copy()
        if not p.empty:
            p["d"] = pd.to_datetime(p["session_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            keys = ["subject_id", "d", "tray_type", "tray_number"]
            g = p.groupby(keys)["score"].agg(disp=lambda s: int((s == 1).sum()),
                                             retr=lambda s: int((s == 2).sum())).reset_index()
            k = pd.DataFrame({"subject_id": rc["subject_id"], "d": cols["Test_Date"],
                              "tray_type": rc["tray_type"], "tray_number": rc["run_number"]})
            m = k.merge(g, on=keys, how="left")
            m.index = rc.index
            cols["Manual_Displaced"] = m["disp"].astype("Int64")
            cols["Manual_Retrieved"] = m["retr"].astype("Int64")
            cols["Manual_Contacted"] = cols["Manual_Displaced"] + cols["Manual_Retrieved"]
            cols["Contacted_Match"] = (cols["Manual_Contacted"] - cols["Video_Contacted"]).abs()

    columns = (ANIMAL_COLUMNS + SESSION_COLUMNS + [c for c, *_ in sheet_cols]
               + reach_cols + TOTAL_COLUMNS)
    out = pd.concat([cols[c].rename(c) for c in columns], axis=1)
    sort = [c for c in ("SubjectID", "Test_Date", "video_name", "segment_num", "reach_num") if c in out.columns]
    out = out.sort_values(sort, kind="stable")

    reach_rows = {r["VariableName"]: r for r in dd.REACH_DATA}
    rows = (dd.ODC_REACH_ANIMAL + dd.ODC_REACH_SESSION
            + [dd.sheet_column_row(c, t, f, n) for c, t, f, n in sheet_cols]
            + [reach_rows[c] for c in reach_cols if c in reach_rows]
            + dd.ODC_REACH_TOTALS)
    return out, rows


def cohorts_in(reach: pd.DataFrame) -> List[str]:
    return sorted({s.rsplit("_", 1)[0] for s in reach["subject_id"].dropna().astype(str) if s.count("_") >= 2})


def write_cohort(out_dir: Path, cohort_id: str, df: pd.DataFrame, rows: List[dict]) -> dict:
    """Write ODC_reaches_<cohort>.csv and its dictionary; returns the manifest entry."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = out_dir / ("ODC_reaches_%s.csv" % cohort_id)
    tmp = data.with_name(data.name + ".tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(data)
    dd.write_rows(rows, out_dir / ("ODC_reaches_%s_DATA_DICTIONARY.csv" % cohort_id))
    documented = {r["VariableName"] for r in rows}
    return {"rows": int(len(df)), "columns": int(len(df.columns)),
            "undocumented_columns": [c for c in df.columns if c not in documented],
            "blank_animal_columns": [c for c in ANIMAL_COLUMNS if df[c].replace("", pd.NA).isna().all()]}


def load_snapshot(snapshot_dir: Path) -> dict:
    """The snapshot tables this export reads; animal_records may be absent (older snapshot)."""
    import pyarrow.parquet as pq
    snapshot_dir = Path(snapshot_dir)
    names = pq.read_schema(snapshot_dir / "reach_data.parquet").names
    t = {"reach": pd.read_parquet(snapshot_dir / "reach_data.parquet",
                                  columns=[c for c in names if c not in REACH_DROP])}
    for key, name in (("subjects", "subjects"), ("pellets", "pellet_scores"), ("records", "animal_records")):
        f = snapshot_dir / ("%s.parquet" % name)
        t[key] = pd.read_parquet(f) if f.exists() else None
    return t


def export(snapshot_dir: Path, out_dir: Path, cohorts: Optional[List[str]] = None,
           manifest: Optional[dict] = None) -> dict:
    """Write the per-reach ODC files for ``cohorts`` (all with reaches if None)."""
    from .. import study_facts
    from ..config import lab_name
    manifest = manifest if manifest is not None else {"files": {}, "problems": []}
    t = load_snapshot(snapshot_dir)
    if t["records"] is None:
        manifest["problems"].append(
            "ODC_reaches: the snapshot has no animal_records table yet (it appears after the next "
            "tracking-sheet import and snapshot); sheet columns are missing from these files")
    wanted = [c.upper() for c in cohorts] if cohorts else cohorts_in(t["reach"])
    for cid in wanted:
        try:
            df, rows = build_cohort(cid, t["reach"], t["subjects"], t["records"], t["pellets"],
                                    study_facts.facts, lab_name())
            if df.empty:
                manifest["files"]["ODC_reaches_%s.csv" % cid] = {"rows": 0, "note": "no reaches"}
                continue
            manifest["files"]["ODC_reaches_%s.csv" % cid] = write_cohort(out_dir, cid, df, rows)
        except Exception as e:
            manifest["problems"].append("ODC_reaches_%s: %s: %s" % (cid, type(e).__name__, e))
    return manifest


def main(argv=None) -> int:
    import argparse
    import json
    from ..config import require
    ap = argparse.ArgumentParser(prog="mousedb export-odc-reaches", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cohort", action="append", help="Cohort id, e.g. PROJECT_02 (repeatable; default all)")
    ap.add_argument("--out-dir", type=Path, help="Folder to write into (default: the current exports folder)")
    ap.add_argument("--snapshot-dir", type=Path)
    args = ap.parse_args(argv)
    snap = args.snapshot_dir or require("snapshot_dir")
    out = args.out_dir or (require("mousedb_root") / "exports" / "current")
    m = export(snap, out, args.cohort)
    for name, info in m["files"].items():
        print("  %-32s %9s rows  %s" % (name, info.get("rows", "-"),
                                         ("blank: " + ", ".join(info["blank_animal_columns"]))
                                         if info.get("blank_animal_columns") else ""))
    for p in m["problems"]:
        print("  [!] %s" % p)
    print("written to %s" % out)
    return 1 if m["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
