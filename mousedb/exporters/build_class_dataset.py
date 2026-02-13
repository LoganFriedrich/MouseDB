"""
Build a clean, wide-format reach kinematics dataset for class use.

Pulls reach_data from connectome.db, joins subject/surgery metadata,
drops empty columns, adds computed fields, and exports to 4 formats:
  - CSV  (class_reach_kinematics.csv)
  - SQLite (class_reach_kinematics.db)
  - JSON  (class_reach_kinematics.json)
  - HDF5  (class_reach_kinematics.h5)

Run from the MouseDB conda environment:
    conda activate Y:\2_Connectome\envs\MouseDB
    python build_class_dataset.py
"""

import sqlite3
import json
import os
from pathlib import Path

import pandas as pd
import numpy as np

# ── Paths ────────────────────────────────────────────────────────────────────
DB_PATH = Path(r"Y:\2_Connectome\Databases\connectome.db")
OUT_DIR = Path(r"Y:\2_Connectome\Databases\exports\class_data")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Pull raw data ────────────────────────────────────────────────────────────
conn = sqlite3.connect(str(DB_PATH))

# Reach data with subject/cohort metadata and surgery info
query = """
SELECT
    r.*,
    s.cohort_id,
    c.project_code,
    c.start_date AS cohort_start_date,
    surg.surgery_date AS injury_date,
    surg.force_kdyn AS injury_force_kdyn,
    surg.displacement_um AS injury_displacement_um
FROM reach_data r
JOIN subjects s ON r.subject_id = s.subject_id
JOIN cohorts c ON s.cohort_id = c.cohort_id
LEFT JOIN surgeries surg
    ON r.subject_id = surg.subject_id
    AND surg.surgery_type = 'contusion'
ORDER BY r.subject_id, r.session_date, r.video_name, r.reach_id
"""
df = pd.read_sql_query(query, conn)
conn.close()

print(f"Raw rows: {len(df)}, columns: {len(df.columns)}")

# ── Drop columns that are 100% null or purely internal ───────────────────────
always_null = [c for c in df.columns if df[c].isna().all()]
internal_cols = [
    "id", "source_file", "extractor_version", "imported_at",
    "flag_reason",  # 100% null
]
drop_cols = list(set(always_null + internal_cols))
df.drop(columns=[c for c in drop_cols if c in df.columns], inplace=True)
print(f"Dropped {len(drop_cols)} empty/internal columns: {sorted(drop_cols)}")

# ── Computed fields ──────────────────────────────────────────────────────────
# Days post injury
df["session_date"] = pd.to_datetime(df["session_date"])
df["injury_date"] = pd.to_datetime(df["injury_date"])
df["cohort_start_date"] = pd.to_datetime(df["cohort_start_date"])
df["days_post_injury"] = (df["session_date"] - df["injury_date"]).dt.days

# Study day (relative to cohort start)
df["study_day"] = (df["session_date"] - df["cohort_start_date"]).dt.days

# Time period label
def classify_period(dpi):
    if pd.isna(dpi) or dpi < 0:
        return "pre_injury"
    elif dpi <= 7:
        return "acute"
    elif dpi <= 30:
        return "subacute"
    else:
        return "chronic"

df["time_period"] = df["days_post_injury"].apply(classify_period)

# Injury severity bin (by displacement - higher = more severe)
def classify_severity(disp):
    if pd.isna(disp):
        return None
    if disp < 500:
        return "mild"
    elif disp < 600:
        return "moderate"
    else:
        return "severe"

df["injury_severity"] = df["injury_displacement_um"].apply(classify_severity)

# Duration in seconds (assuming 30 fps)
FPS = 30
df["duration_sec"] = df["duration_frames"] / FPS

# Clean up: convert booleans
for col in ["causal_reach", "is_first_reach", "is_last_reach", "flagged_for_review",
            "segment_outcome_flagged"]:
    if col in df.columns:
        df[col] = df[col].astype(bool)

# ── Reorder columns for readability ──────────────────────────────────────────
id_cols = [
    "subject_id", "cohort_id", "project_code",
    "video_name", "session_date", "study_day",
    "tray_type", "run_number",
]
injury_cols = [
    "injury_date", "days_post_injury", "time_period",
    "injury_force_kdyn", "injury_displacement_um", "injury_severity",
]
reach_id_cols = [
    "segment_num", "reach_id", "reach_num",
    "is_first_reach", "is_last_reach", "n_reaches_in_segment",
]
outcome_cols = [
    "outcome", "causal_reach", "interaction_frame",
    "segment_outcome", "segment_outcome_confidence", "segment_outcome_flagged",
]
temporal_cols = [
    "start_frame", "apex_frame", "end_frame",
    "duration_frames", "duration_sec",
]
kinematic_cols = [
    "max_extent_pixels", "max_extent_ruler", "max_extent_mm",
    "velocity_at_apex_px_per_frame", "velocity_at_apex_mm_per_sec",
    "peak_velocity_px_per_frame", "mean_velocity_px_per_frame",
    "trajectory_straightness", "trajectory_smoothness",
]
posture_cols = [
    "hand_angle_at_apex_deg", "hand_rotation_total_deg",
    "head_width_at_apex_mm", "nose_to_slit_at_apex_mm",
    "head_angle_at_apex_deg", "head_angle_change_deg",
]
quality_cols = [
    "mean_likelihood", "frames_low_confidence",
    "flagged_for_review",
    "attention_score", "pellet_position_idealness",
]

ordered = []
for group in [id_cols, injury_cols, reach_id_cols, outcome_cols,
              temporal_cols, kinematic_cols, posture_cols, quality_cols]:
    ordered.extend([c for c in group if c in df.columns])

# Add any remaining columns not yet listed
remaining = [c for c in df.columns if c not in ordered]
if remaining:
    ordered.extend(remaining)
    print(f"Note: {len(remaining)} columns added at end: {remaining}")

# Drop the cohort_start_date helper column
if "cohort_start_date" in ordered:
    ordered.remove("cohort_start_date")
    df.drop(columns=["cohort_start_date"], inplace=True, errors="ignore")

df = df[ordered]

# ── Summary stats ────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"CLEAN DATASET SUMMARY")
print(f"{'='*60}")
print(f"Rows:     {len(df):,}")
print(f"Columns:  {len(df.columns)}")
print(f"Subjects: {df['subject_id'].nunique()}")
print(f"Cohorts:  {df['cohort_id'].nunique()}")
print(f"Sessions: {df['session_date'].nunique()}")
print(f"Date range: {df['session_date'].min().date()} to {df['session_date'].max().date()}")
print(f"\nTime periods:")
for period, count in df["time_period"].value_counts().items():
    print(f"  {period}: {count}")
print(f"\nInjury severity:")
for sev, count in df["injury_severity"].value_counts().items():
    print(f"  {sev}: {count}")
print(f"\nNull rates (>0% only):")
for col in df.columns:
    pct = df[col].isna().mean() * 100
    if pct > 0:
        print(f"  {col}: {pct:.1f}%")

# ── Export: CSV ──────────────────────────────────────────────────────────────
csv_path = OUT_DIR / "class_reach_kinematics.csv"
df.to_csv(csv_path, index=False)
print(f"\n[CSV]    {csv_path}  ({os.path.getsize(csv_path)/1024:.0f} KB)")

# ── Export: SQLite ───────────────────────────────────────────────────────────
db_path = OUT_DIR / "class_reach_kinematics.db"
if db_path.exists():
    db_path.unlink()
out_conn = sqlite3.connect(str(db_path))
df.to_sql("reaches", out_conn, index=False, if_exists="replace")

# Also create a summary view
out_conn.execute("""
CREATE VIEW subject_summary AS
SELECT
    subject_id,
    cohort_id,
    injury_severity,
    injury_force_kdyn,
    injury_displacement_um,
    COUNT(*) AS total_reaches,
    COUNT(DISTINCT session_date) AS n_sessions,
    AVG(duration_sec) AS mean_duration_sec,
    AVG(velocity_at_apex_mm_per_sec) AS mean_apex_velocity,
    AVG(max_extent_mm) AS mean_max_extent,
    AVG(trajectory_straightness) AS mean_straightness,
    AVG(mean_likelihood) AS mean_tracking_quality
FROM reaches
GROUP BY subject_id
""")
out_conn.commit()
out_conn.close()
print(f"[SQLite] {db_path}  ({os.path.getsize(db_path)/1024:.0f} KB)")

# ── Export: JSON ─────────────────────────────────────────────────────────────
json_path = OUT_DIR / "class_reach_kinematics.json"
# Convert to JSON-friendly types
df_json = df.copy()
for col in df_json.select_dtypes(include=["datetime64"]).columns:
    df_json[col] = df_json[col].dt.strftime("%Y-%m-%d")
for col in df_json.columns:
    df_json[col] = df_json[col].where(df_json[col].notna(), None)

# Structure: metadata + records
output = {
    "metadata": {
        "description": "Mouse reach kinematics from spinal cord injury study",
        "n_rows": len(df_json),
        "n_columns": len(df_json.columns),
        "n_subjects": int(df_json["subject_id"].nunique()),
        "date_range": [
            df_json["session_date"].min(),
            df_json["session_date"].max(),
        ],
        "columns": list(df_json.columns),
    },
    "records": df_json.to_dict(orient="records"),
}
with open(json_path, "w") as f:
    json.dump(output, f, indent=2, default=str)
print(f"[JSON]   {json_path}  ({os.path.getsize(json_path)/1024:.0f} KB)")

# ── Export: HDF5 (using h5py directly for portability) ───────────────────────
h5_path = OUT_DIR / "class_reach_kinematics.h5"
try:
    import h5py

    df_h5 = df.copy()
    for col in df_h5.select_dtypes(include=["bool"]).columns:
        df_h5[col] = df_h5[col].astype(int)
    for col in df_h5.select_dtypes(include=["datetime64"]).columns:
        df_h5[col] = df_h5[col].dt.strftime("%Y-%m-%d")
    for col in df_h5.select_dtypes(include=["object"]).columns:
        df_h5[col] = df_h5[col].fillna("").astype(str)

    with h5py.File(str(h5_path), "w") as hf:
        grp = hf.create_group("reaches")
        for col in df_h5.columns:
            data = df_h5[col].values
            if data.dtype.kind in ("U", "O"):  # string
                encoded = np.array([s.encode("utf-8") for s in data.astype(str)])
                grp.create_dataset(col, data=encoded)
            elif data.dtype.kind == "f":  # float (may have NaN)
                grp.create_dataset(col, data=data.astype(np.float64))
            else:
                grp.create_dataset(col, data=data)
        # Store column order as attribute for reconstruction
        grp.attrs["columns"] = [c.encode("utf-8") for c in df_h5.columns]
        grp.attrs["n_rows"] = len(df_h5)

    print(f"[HDF5]   {h5_path}  ({os.path.getsize(h5_path)/1024:.0f} KB)")
except ImportError:
    print("[HDF5]   SKIPPED - h5py not installed in this environment")
    print("         Install with: pip install h5py")
    print("         Or run this script in the MouseBrain env which has h5py")

print(f"\nDone! All 4 files in: {OUT_DIR}")
