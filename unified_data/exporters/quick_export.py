"""Quick export of unified data with surgery metadata for presentation."""
import pandas as pd
from pathlib import Path

# Import from mousereach package (installed as dependency)
try:
    from mousereach.analysis.data import load_all_surgery_metadata
except ImportError:
    print("Warning: mousereach package not available, surgery metadata will be skipped")
    load_all_surgery_metadata = None


def main():
    """Main export function."""
    # Load existing pellet-level data
    print("Loading pellet-level data...")
    base_dir = Path.cwd()
    pellet_df = pd.read_csv(base_dir / 'generated' / 'all_cohorts_pellet_level.csv')
    print(f"  {len(pellet_df):,} pellet outcomes")

    # Load tray summaries
    print("Loading tray summaries...")
    tray_df = pd.read_csv(base_dir / 'generated' / 'all_cohorts_tray_summaries.csv')
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

    # Export
    output_dir = base_dir / 'generated'
    output_dir.mkdir(exist_ok=True)

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


if __name__ == "__main__":
    main()
