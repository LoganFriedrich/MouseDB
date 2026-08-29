"""Quick export of unified behavioural data with surgery metadata, for a
presentation or a hand-off.

READS  (from <dir>/generated/, where <dir> is the current working directory
        unless --dir says otherwise):
    all_cohorts_pellet_level.csv     one row per pellet
    all_cohorts_tray_summaries.csv   one row per tray
    plus, only when the optional mousereach package is importable, the
    surgery metadata it can load for those animals (otherwise the export is
    the two inputs unchanged).

WRITES (into the same <dir>/generated/ folder, overwriting):
    unified_pellet_level.csv         pellets + surgery columns, days_post_injury, Timepoint
    unified_tray_level.csv           trays + the same
    unified_behavioral_data.xlsx     both as sheets (+ Surgery_Metadata when available)

Nothing else is read or written; the database is not touched.
"""
import argparse
from pathlib import Path

import pandas as pd

# Optional: mousereach is a separate package/env - not a dependency of mousedb
try:
    from mousereach.analysis.data import load_all_surgery_metadata
except ImportError:
    load_all_surgery_metadata = None

GENERATED = "generated"
PELLET_INPUT = "all_cohorts_pellet_level.csv"
TRAY_INPUT = "all_cohorts_tray_summaries.csv"


def _parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="mousedb-quick-export", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", type=Path, default=None,
                    help="folder that holds %s/ (default: the current working "
                         "directory)" % GENERATED)
    return ap.parse_args(argv)


def main(argv=None) -> int:
    """Main export function. Returns the process exit code."""
    args = _parse_args(argv)
    base_dir = Path(args.dir) if args.dir else Path.cwd()
    gen_dir = base_dir / GENERATED

    # WHY check up front: a missing folder used to surface as a pandas
    # FileNotFoundError traceback that said nothing about what this command
    # expects or where it looked.
    if not gen_dir.is_dir():
        print("[FAIL] %s does not exist." % gen_dir)
        print("  mousedb-quick-export reads <dir>/%s/%s and %s."
              % (GENERATED, PELLET_INPUT, TRAY_INPUT))
        print("  Run it from the folder that holds %s/, or pass --dir <folder>." % GENERATED)
        return 1
    missing = [name for name in (PELLET_INPUT, TRAY_INPUT) if not (gen_dir / name).is_file()]
    if missing:
        print("[FAIL] missing input file(s) in %s: %s" % (gen_dir, ", ".join(missing)))
        return 1

    # Load existing pellet-level data
    print("Loading pellet-level data...")
    pellet_df = pd.read_csv(gen_dir / PELLET_INPUT)
    print(f"  {len(pellet_df):,} pellet outcomes")

    # Load tray summaries
    print("Loading tray summaries...")
    tray_df = pd.read_csv(gen_dir / TRAY_INPUT)
    print(f"  {len(tray_df):,} tray records")

    # Load surgery metadata
    print("\nLoading surgery metadata...")
    if load_all_surgery_metadata:
        surgery_df = load_all_surgery_metadata(base_dir)
    else:
        surgery_df = pd.DataFrame()

    if len(surgery_df) > 0:
        print(f"  {len(surgery_df)} mice with surgery data")

        # Normalize animal IDs for merge
        pellet_df['_animal_norm'] = pellet_df['Animal'].str.replace('_', '').str.upper()
        tray_df['_animal_norm'] = tray_df['Animal'].str.replace('_', '').str.upper()
        surgery_df['_animal_norm'] = surgery_df['mouse_id'].str.upper()

        # Select surgery columns
        surgery_cols = [c for c in surgery_df.columns
                       if c not in ['mouse_id', 'source_file', '_animal_norm']]

        # Merge surgery data into pellet-level
        pellet_merged = pellet_df.merge(
            surgery_df[['_animal_norm'] + surgery_cols],
            on='_animal_norm',
            how='left'
        )

        # Merge surgery data into tray-level
        tray_merged = tray_df.merge(
            surgery_df[['_animal_norm'] + surgery_cols],
            on='_animal_norm',
            how='left'
        )

        # Compute days post injury
        pellet_merged['Date'] = pd.to_datetime(pellet_merged['Date'])
        if 'surgery_date' in pellet_merged.columns:
            pellet_merged['days_post_injury'] = (
                pellet_merged['Date'] - pd.to_datetime(pellet_merged['surgery_date'])
            ).dt.days

        tray_merged['Date'] = pd.to_datetime(tray_merged['Date'])
        if 'surgery_date' in tray_merged.columns:
            tray_merged['days_post_injury'] = (
                tray_merged['Date'] - pd.to_datetime(tray_merged['surgery_date'])
            ).dt.days

        # Clean up
        pellet_merged = pellet_merged.drop(columns=['_animal_norm'])
        tray_merged = tray_merged.drop(columns=['_animal_norm'])

        # Add timepoint category
        def categorize_timepoint(phase):
            if pd.isna(phase):
                return None
            phase = str(phase)
            if 'Training' in phase:
                return 'Training'
            elif 'Pre-Injury' in phase:
                return 'Pre-Injury'
            elif 'Post-Injury' in phase:
                return 'Post-Injury'
            elif 'Rehab_Easy' in phase:
                return 'Rehab_Easy'
            elif 'Rehab_Flat' in phase:
                return 'Rehab_Flat'
            elif 'Rehab_Pillar' in phase:
                return 'Rehab_Pillar'
            return phase

        pellet_merged['Timepoint'] = pellet_merged['Test_Phase'].apply(categorize_timepoint)
        tray_merged['Timepoint'] = tray_merged['Test_Phase'].apply(categorize_timepoint)

    else:
        pellet_merged = pellet_df
        tray_merged = tray_df

    # Export -- into the same generated/ folder the inputs came from
    output_dir = gen_dir

    # Pellet-level with surgery
    pellet_out = output_dir / 'unified_pellet_level.csv'
    pellet_merged.to_csv(pellet_out, index=False)
    print(f"\nExported: {pellet_out}")
    print(f"  {len(pellet_merged):,} rows, {len(pellet_merged.columns)} columns")

    # Tray-level with surgery
    tray_out = output_dir / 'unified_tray_level.csv'
    tray_merged.to_csv(tray_out, index=False)
    print(f"Exported: {tray_out}")
    print(f"  {len(tray_merged):,} rows, {len(tray_merged.columns)} columns")

    # Excel with multiple sheets
    xlsx_out = output_dir / 'unified_behavioral_data.xlsx'
    with pd.ExcelWriter(xlsx_out, engine='openpyxl') as writer:
        tray_merged.to_excel(writer, sheet_name='Tray_Summaries', index=False)
        pellet_merged.to_excel(writer, sheet_name='Pellet_Level', index=False)
        if len(surgery_df) > 0:
            surgery_df.to_excel(writer, sheet_name='Surgery_Metadata', index=False)

    print(f"Exported: {xlsx_out}")

    # Summary
    print("\n" + "=" * 60)
    print("UNIFIED DATA READY FOR PRESENTATION")
    print("=" * 60)
    print(f"Animals: {tray_merged['Animal'].nunique()}")
    print(f"Cohorts: {sorted(tray_merged['Cohort'].unique())}")
    if 'Timepoint' in tray_merged.columns:
        print(f"Timepoints: {sorted(tray_merged['Timepoint'].dropna().unique())}")
    if 'days_post_injury' in tray_merged.columns:
        valid_dpi = tray_merged['days_post_injury'].notna().sum()
        print(f"Days post injury available for: {valid_dpi}/{len(tray_merged)} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
