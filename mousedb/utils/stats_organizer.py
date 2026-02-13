import pandas as pd
import numpy as np
import os
from pathlib import Path

class SCIDataReorganizer:
    def __init__(self):
        """
        Simple data reorganizer for SCI behavioral Excel/CSV files
        """
        # Pellet columns can be int or str depending on how Excel saved them
        self.pellet_columns_int = list(range(1, 21))  # [1, 2, 3, ... 20]
        self.pellet_columns_str = [str(i) for i in range(1, 21)]  # ['1', '2', ... '20']

    def get_pellet_columns(self, df):
        """Detect whether pellet columns are int or str and return the right list."""
        if 1 in df.columns:
            return self.pellet_columns_int
        return self.pellet_columns_str

    def get_pellet_value(self, row, pellet_num, df):
        """Get pellet value handling int vs str column names."""
        if 1 in df.columns:
            return row[pellet_num]  # int column
        return row[str(pellet_num)]  # str column

    def parse_tray_info(self, tray_type):
        """
        Extract difficulty and repetition from tray type (e.g., 'P3' -> 'P', 3)
        """
        if pd.isna(tray_type) or len(str(tray_type)) < 2:
            return None, None
            
        tray_str = str(tray_type)
        difficulty = tray_str[0]  # E, F, or P
        try:
            repetition = int(tray_str[1:])  # 1, 2, 3, 4
        except ValueError:
            repetition = None
            
        return difficulty, repetition
    
    def calculate_session_pellet_number(self, tray_repetition, within_tray_pellet):
        """
        Calculate actual pellet number in session (1-80)
        """
        if tray_repetition is None:
            return None
        return (tray_repetition - 1) * 20 + within_tray_pellet
    
    def reorganize_data(self, file_path, output_dir="reorganized_data"):
        """
        Reorganize a single data file into the three analysis levels
        """
        print(f"Processing: {file_path}")
        
        # Read file (Excel or CSV)
        try:
            if file_path.endswith('.xlsx') or file_path.endswith('.xls'):
                raw_data = pd.read_excel(file_path, sheet_name='3b_Manual_Tray')
            else:
                raw_data = pd.read_csv(file_path)
        except ValueError as e:
            if "Worksheet named '3b_Manual_Tray' not found" in str(e):
                print(f"ERROR: Sheet '3b_Manual_Tray' not found in {file_path}")
                print("Available sheets:")
                xl_file = pd.ExcelFile(file_path)
                for sheet in xl_file.sheet_names:
                    print(f"  - {sheet}")
                return None
            else:
                raise e
        
        # Convert date to datetime
        raw_data['Date'] = pd.to_datetime(raw_data['Date'])
        
        # Parse tray information
        tray_info = raw_data['Tray Type/Number'].apply(self.parse_tray_info)
        raw_data['Tray_Difficulty'] = [info[0] for info in tray_info]
        raw_data['Tray_Repetition'] = [info[1] for info in tray_info]
        
        # Create output directory
        os.makedirs(output_dir, exist_ok=True)
        base_name = Path(file_path).stem
        
        # LEVEL 1: Individual pellets (long format)
        pellet_data = []
        for idx, row in raw_data.iterrows():
            for within_tray_pellet in range(1, 21):
                pellet_result = self.get_pellet_value(row, within_tray_pellet, raw_data)
                if pd.notna(pellet_result):
                    session_pellet_num = self.calculate_session_pellet_number(
                        row['Tray_Repetition'], within_tray_pellet)
                    
                    pellet_data.append({
                        'Date': row['Date'],
                        'Animal': row['Animal'],
                        'Sex': row.get('Sex'),
                        'Weight': row['Weight'],
                        'Weight_Percent': row.get('Weight %'),
                        'Test_Phase': row.get('Test_Phase'),
                        'Tray_Difficulty': row['Tray_Difficulty'],
                        'Tray_Repetition': row['Tray_Repetition'], 
                        'Tray_Type_Full': row['Tray Type/Number'],
                        'Within_Tray_Pellet': within_tray_pellet,
                        'Session_Pellet_Number': session_pellet_num,
                        'Pellet_Result': int(pellet_result)
                    })
        
        level1_df = pd.DataFrame(pellet_data)
        level1_path = f"{output_dir}/{base_name}_level1_individual_pellets.csv"
        level1_df.to_csv(level1_path, index=False)
        
        # LEVEL 2: Tray summaries
        pellet_cols = self.get_pellet_columns(raw_data)
        tray_data = []
        for idx, row in raw_data.iterrows():
            pellet_results = [row[col] for col in pellet_cols if col in row.index and pd.notna(row[col])]
            
            miss_count = pellet_results.count(0)
            displaced_count = pellet_results.count(1)
            retrieved_count = pellet_results.count(2)
            total_pellets = len(pellet_results)
            
            # Calculate ratios
            skill_ratio = (displaced_count + retrieved_count) / total_pellets if total_pellets > 0 else 0
            success_ratio = retrieved_count / total_pellets if total_pellets > 0 else 0
            miss_ratio = miss_count / total_pellets if total_pellets > 0 else 0
            displacement_ratio = displaced_count / total_pellets if total_pellets > 0 else 0
            
            tray_data.append({
                'Date': row['Date'],
                'Animal': row['Animal'],
                'Sex': row.get('Sex'),
                'Weight': row['Weight'],
                'Weight_Percent': row.get('Weight %'),
                'Test_Phase': row.get('Test_Phase'),
                'Tray_Difficulty': row['Tray_Difficulty'],
                'Tray_Repetition': row['Tray_Repetition'],
                'Tray_Type_Full': row['Tray Type/Number'],
                'Miss_Count': miss_count,
                'Displaced_Count': displaced_count,
                'Retrieved_Count': retrieved_count,
                'Total_Pellets': total_pellets,
                'Skill_Ratio': skill_ratio,
                'Success_Ratio': success_ratio,
                'Miss_Ratio': miss_ratio,
                'Displacement_Ratio': displacement_ratio
            })
        
        level2_df = pd.DataFrame(tray_data)
        level2_path = f"{output_dir}/{base_name}_level2_tray_summaries.csv"
        level2_df.to_csv(level2_path, index=False)
        
        # LEVEL 3: Daily averages
        # By difficulty
        level3_by_difficulty = level2_df.groupby(['Date', 'Tray_Difficulty']).agg({
            'Miss_Count': 'mean',
            'Displaced_Count': 'mean',
            'Retrieved_Count': 'mean',
            'Skill_Ratio': 'mean',
            'Success_Ratio': 'mean',
            'Miss_Ratio': 'mean',
            'Displacement_Ratio': 'mean',
            'Animal': 'nunique'
        }).rename(columns={'Animal': 'Animals_Tested'}).reset_index()
        
        # Overall
        level3_overall = level2_df.groupby('Date').agg({
            'Miss_Count': 'mean',
            'Displaced_Count': 'mean',
            'Retrieved_Count': 'mean',
            'Skill_Ratio': 'mean',
            'Success_Ratio': 'mean',
            'Miss_Ratio': 'mean',
            'Displacement_Ratio': 'mean',
            'Animal': 'nunique'
        }).rename(columns={'Animal': 'Animals_Tested'}).reset_index()
        level3_overall['Tray_Difficulty'] = 'Overall'
        
        level3_df = pd.concat([level3_by_difficulty, level3_overall], ignore_index=True)
        level3_path = f"{output_dir}/{base_name}_level3_daily_averages.csv"
        level3_df.to_csv(level3_path, index=False)
        
        # Pellet-level fatigue analysis
        if len(level1_df) == 0 or 'Session_Pellet_Number' not in level1_df.columns:
            print(f"  Skipping fatigue analysis (no pellet data)")
            return {
                'level1': level1_path,
                'level2': level2_path,
                'level3': level3_path,
                'fatigue': None
            }

        fatigue_data = level1_df.groupby('Session_Pellet_Number').agg({
            'Pellet_Result': 'mean',
            'Animal': 'nunique'
        }).rename(columns={'Pellet_Result': 'Average_Result', 'Animal': 'Animals_Contributing'})
        
        # Add proportions for each result type
        result_props = level1_df.groupby(['Session_Pellet_Number', 'Pellet_Result']).size().unstack(fill_value=0)
        result_props = result_props.div(result_props.sum(axis=1), axis=0)
        result_props.columns = [f'Prop_Result_{int(col)}' for col in result_props.columns]
        
        fatigue_analysis = fatigue_data.merge(result_props, left_index=True, right_index=True, how='left')
        
        # Calculate skill ratio by pellet number
        if 'Prop_Result_1' in fatigue_analysis.columns and 'Prop_Result_2' in fatigue_analysis.columns:
            fatigue_analysis['Skill_Ratio_By_Pellet'] = fatigue_analysis['Prop_Result_1'] + fatigue_analysis['Prop_Result_2']
        
        fatigue_analysis = fatigue_analysis.reset_index()
        fatigue_path = f"{output_dir}/{base_name}_pellet_fatigue.csv"
        fatigue_analysis.to_csv(fatigue_path, index=False)
        
        print(f"  Created: {level1_path}")
        print(f"  Created: {level2_path}")
        print(f"  Created: {level3_path}")
        print(f"  Created: {fatigue_path}")
        
        return {
            'level1': level1_path,
            'level2': level2_path,
            'level3': level3_path,
            'fatigue': fatigue_path
        }

# Simple usage functions
def reorganize_file(file_path, output_dir="reorganized_data"):
    """
    Reorganize a single file
    """
    reorganizer = SCIDataReorganizer()
    return reorganizer.reorganize_data(file_path, output_dir)


def _extract_cohort_number(filename):
    """
    Extract cohort number from filename.
    Handles: Connectome_01_Animal_Tracking.xlsx, Connectome_4_Animal_Tracking.xlsx, etc.
    Returns integer cohort number or None.
    """
    import re
    # Match patterns like Connectome_01_, Connectome_1_, Connectome_04_
    match = re.search(r'Connectome_(\d+)_', filename)
    if match:
        return int(match.group(1))
    return None


def _count_pellet_data(file_path):
    """
    Count non-NaN pellet values in a tracking file.
    Used to determine which file version has actual data.
    """
    try:
        df = pd.read_excel(file_path, sheet_name='3b_Manual_Tray')
        # Check for pellet columns (could be int or str)
        if 1 in df.columns:
            pellet_cols = list(range(1, 21))
        else:
            pellet_cols = [str(i) for i in range(1, 21)]
            pellet_cols = [c for c in pellet_cols if c in df.columns]

        if pellet_cols:
            return df[pellet_cols].notna().sum().sum()
        return 0
    except Exception:
        return 0


def discover_tracking_files(input_dir):
    """
    Discover all Connectome tracking files, handling naming conflicts.

    When multiple versions exist for the same cohort (e.g., due to Excel file-locking
    creating copies), selects the one with the most pellet data.

    Args:
        input_dir: Directory containing Connectome_XX_Animal_Tracking.xlsx files

    Returns:
        Dict mapping cohort number -> best file path
    """
    input_path = Path(input_dir)

    # Find all potential tracking files
    all_files = list(input_path.glob('Connectome_*_Animal_Tracking*.xlsx'))

    # Skip temp files (Excel lock files start with ~$)
    all_files = [f for f in all_files if not f.name.startswith('~')]

    # Group by cohort number
    cohort_files = {}
    for fpath in all_files:
        cohort_num = _extract_cohort_number(fpath.name)
        if cohort_num is not None:
            if cohort_num not in cohort_files:
                cohort_files[cohort_num] = []
            cohort_files[cohort_num].append(fpath)

    # Select best file for each cohort
    best_files = {}
    for cohort_num, files in sorted(cohort_files.items()):
        if len(files) == 1:
            best_files[cohort_num] = files[0]
        else:
            # Multiple versions exist - pick the one with most pellet data
            print(f"  CNT_{cohort_num:02d}: Found {len(files)} versions, checking for best...")

            file_scores = []
            for fpath in files:
                pellet_count = _count_pellet_data(fpath)
                mtime = fpath.stat().st_mtime
                file_scores.append((fpath, pellet_count, mtime))

            # Sort by pellet count (most data wins), then newest as tiebreaker
            # File with most filled-in experiments is the correct one
            file_scores.sort(key=lambda x: (x[1], x[2]), reverse=True)

            best = file_scores[0]
            best_files[cohort_num] = best[0]

            # Report what we found
            for fpath, pellet_count, mtime in file_scores:
                marker = " <-- SELECTED" if fpath == best[0] else ""
                print(f"    {fpath.name}: {pellet_count} pellet values{marker}")

    return best_files


def reorganize_directory(input_dir, file_pattern="*.xlsx", output_dir="reorganized_data"):
    """
    Reorganize all tracking files in a directory.

    Automatically handles naming conflicts by selecting the file version
    with the most actual data for each cohort.
    """
    reorganizer = SCIDataReorganizer()
    input_path = Path(input_dir)

    # Use smart discovery for Connectome tracking files
    print("Discovering tracking files...")
    best_files = discover_tracking_files(input_dir)

    if not best_files:
        print("No Connectome tracking files found.")
        return {}

    print(f"\nProcessing {len(best_files)} cohorts:")

    results = {}
    for cohort_num, fpath in sorted(best_files.items()):
        try:
            result = reorganizer.reorganize_data(str(fpath), output_dir)
            results[f"CNT_{cohort_num:02d}"] = {
                'source_file': fpath.name,
                'outputs': result
            }
        except Exception as e:
            print(f"Error processing CNT_{cohort_num:02d} ({fpath.name}): {e}")
            results[f"CNT_{cohort_num:02d}"] = {
                'source_file': fpath.name,
                'error': str(e)
            }

    return results


def reorganize_all_cohorts(input_dir=None, output_dir="generated/reorganized"):
    """
    Convenience function to reorganize all cohort tracking files.

    Args:
        input_dir: Directory containing tracking files (default: current directory)
        output_dir: Where to save output CSVs

    Returns:
        Dict with results for each cohort
    """
    if input_dir is None:
        input_dir = Path(__file__).parent

    print("=" * 50)
    print("SCI Data Reorganizer - Processing All Cohorts")
    print("=" * 50)

    results = reorganize_directory(input_dir, output_dir=output_dir)

    print("\n" + "=" * 50)
    print("Summary:")
    print("=" * 50)

    success_count = 0
    for cohort, info in results.items():
        if 'error' in info:
            print(f"  {cohort}: FAILED - {info['error']}")
        elif info.get('outputs') is None:
            print(f"  {cohort}: SKIPPED (missing sheet or no data)")
        elif info.get('outputs', {}).get('fatigue') is None:
            print(f"  {cohort}: OK (no pellet data)")
        else:
            print(f"  {cohort}: OK")
            success_count += 1

    print(f"\nSuccessfully processed: {success_count}/{len(results)} cohorts")
    print(f"Output directory: {output_dir}")

    # Create unified file combining all cohorts
    _create_unified_file(output_dir)

    return results


def get_unified_data(input_dir=None, force_rebuild=False):
    """
    Get the unified tray summaries dataframe, auto-rebuilding if source files changed.

    This is the main entry point for analysis - just call this and get current data.

    Args:
        input_dir: Directory with tracking spreadsheets (default: script directory)
        force_rebuild: If True, rebuild even if up to date

    Returns:
        pandas DataFrame with all cohort tray summaries
    """
    if input_dir is None:
        input_dir = Path(__file__).parent

    input_dir = Path(input_dir)
    unified_path = input_dir / 'generated' / 'all_cohorts_tray_summaries.csv'

    # Check if rebuild needed
    needs_rebuild = force_rebuild or not unified_path.exists()

    if not needs_rebuild:
        # Check if any source file is newer than unified output
        unified_mtime = unified_path.stat().st_mtime
        for xlsx in input_dir.glob('Connectome_*_Animal_Tracking*.xlsx'):
            if xlsx.name.startswith('~'):
                continue
            if xlsx.stat().st_mtime > unified_mtime:
                print(f"Source file changed: {xlsx.name}")
                needs_rebuild = True
                break

    if needs_rebuild:
        print("Rebuilding unified data...")
        reorganize_all_cohorts(input_dir)

    # Load and return the unified data
    import pandas as pd
    return pd.read_csv(unified_path)


def _create_unified_file(output_dir):
    """Create a single unified CSV combining all cohort level2 tray summaries."""
    output_path = Path(output_dir)

    # Find all level2 files (skip old duplicates without leading zeros)
    level2_files = sorted([
        f for f in output_path.glob('*_level2_tray_summaries.csv')
        if f.stat().st_size > 100 and not f.name.startswith('Connectome_4_')
    ])

    if not level2_files:
        print("No level2 files found to combine.")
        return None

    all_dfs = []
    for f in level2_files:
        df = pd.read_csv(f)
        # Extract cohort from filename
        cohort = f.name.split('_')[1]
        df['Cohort'] = f'CNT_{cohort}'
        all_dfs.append(df)

    combined = pd.concat(all_dfs, ignore_index=True)
    combined['Date'] = pd.to_datetime(combined['Date'])

    # Filter out incomplete entries (no pellet data)
    before_filter = len(combined)
    combined = combined[combined['Total_Pellets'] > 0]
    filtered_out = before_filter - len(combined)
    if filtered_out > 0:
        print(f"  Filtered out {filtered_out} rows with no pellet data")

    # Reorder columns
    id_cols = ['Cohort', 'Animal', 'Date', 'Sex', 'Test_Phase', 'Tray_Difficulty', 'Tray_Repetition', 'Tray_Type_Full']
    metric_cols = ['Miss_Count', 'Displaced_Count', 'Retrieved_Count', 'Total_Pellets',
                   'Skill_Ratio', 'Success_Ratio', 'Miss_Ratio', 'Displacement_Ratio',
                   'Weight', 'Weight_Percent']
    cols = [c for c in id_cols if c in combined.columns] + [c for c in metric_cols if c in combined.columns]
    combined = combined[cols]

    # Save to parent directory
    unified_path = output_path.parent / 'all_cohorts_tray_summaries.csv'
    combined.to_csv(unified_path, index=False)

    print()
    print("=" * 50)
    print("UNIFIED FILE CREATED")
    print("=" * 50)
    print(f"  {unified_path}")
    print(f"  {len(combined):,} rows, {combined['Animal'].nunique()} animals")

    return unified_path

# =============================================================================
# UNIFIED AUTO-UPDATE: Watches ALL data sources
# =============================================================================

def _get_newest_mtime(directory, pattern):
    """Get newest modification time of files matching pattern."""
    newest = 0
    for f in Path(directory).glob(pattern):
        if f.name.startswith('~'):
            continue
        try:
            mtime = f.stat().st_mtime
            if mtime > newest:
                newest = mtime
        except Exception:
            pass
    return newest


def get_pipeline_data(pipeline_dir=None, force_rebuild=False):
    """
    Get MouseReach pipeline reach data, auto-loading if sources changed.

    Args:
        pipeline_dir: Path to Seg_Validated folder (default: from MouseReach config)
        force_rebuild: If True, reload regardless of cache state

    Returns:
        pandas DataFrame with reach-level pipeline data, or None if unavailable
    """
    # Optional: mousereach is a separate package/env - not a dependency of mousedb
    try:
        from mousereach.analysis import load_all_data
        from mousereach.config import Paths
    except ImportError:
        return None

    if pipeline_dir is None:
        pipeline_dir = Paths.PROCESSING_ROOT / "Seg_Validated"

    pipeline_dir = Path(pipeline_dir)
    if not pipeline_dir.exists():
        print(f"Pipeline directory not found: {pipeline_dir}")
        return None

    # Load pipeline data (it internally manages caching/loading)
    data = load_all_data(pipeline_dir, use_features=True, exclude_flagged=True)
    return data.df if len(data) > 0 else None


def get_all_data(
    tracking_dir=None,
    pipeline_dir=None,
    force_rebuild=False,
    include_pipeline=True
):
    """
    MASTER ENTRY POINT: Get all behavioral data from all sources, auto-updating.

    This is THE function to call for analysis. It:
    1. Checks if manual spreadsheet data needs rebuilding
    2. Checks if pipeline data needs reloading
    3. Returns merged, current data ready for stats

    Args:
        tracking_dir: Directory with tracking spreadsheets (default: script location)
        pipeline_dir: Directory with pipeline outputs (default: MouseReach Seg_Validated)
        force_rebuild: If True, rebuild everything from scratch
        include_pipeline: If True, also load automated pipeline data (reaches, outcomes)

    Returns:
        dict with:
            'manual': DataFrame of manual tray scoring data
            'pipeline': DataFrame of pipeline reach data (if available and requested)
            'merged': DataFrame combining both where possible (by Animal/Date)
    """
    if tracking_dir is None:
        tracking_dir = Path(__file__).parent
    tracking_dir = Path(tracking_dir)

    result = {
        'manual': None,
        'pipeline': None,
        'merged': None,
        'sources_changed': False
    }

    # 1. Get manual spreadsheet data (auto-rebuilds if needed)
    print("Checking manual scoring data...")
    result['manual'] = get_unified_data(tracking_dir, force_rebuild=force_rebuild)

    # 2. Get pipeline data if requested
    if include_pipeline:
        print("\nChecking pipeline data...")
        pipeline_df = get_pipeline_data(pipeline_dir, force_rebuild=force_rebuild)
        if pipeline_df is not None:
            result['pipeline'] = pipeline_df

    # 3. Attempt to merge if both exist
    if result['manual'] is not None and result['pipeline'] is not None:
        print("\nMerging manual + pipeline data...")
        try:
            manual = result['manual'].copy()
            pipeline = result['pipeline'].copy()

            # Standardize column names for merge
            if 'Animal' in manual.columns:
                manual['mouse_id'] = manual['Animal']
            if 'Date' in manual.columns:
                manual['date'] = pd.to_datetime(manual['Date'])
            if 'date' in pipeline.columns:
                pipeline['date'] = pd.to_datetime(pipeline['date'])

            # Merge on mouse_id + date (session level)
            if 'mouse_id' in manual.columns and 'mouse_id' in pipeline.columns:
                merged = pd.merge(
                    manual,
                    pipeline,
                    on=['mouse_id', 'date'],
                    how='outer',
                    suffixes=('_manual', '_pipeline')
                )
                result['merged'] = merged
                print(f"  Merged: {len(merged)} rows")
        except Exception as e:
            print(f"  Merge failed: {e}")

    # Summary
    print("\n" + "=" * 50)
    print("DATA READY")
    print("=" * 50)
    if result['manual'] is not None:
        print(f"  Manual data:   {len(result['manual']):,} rows")
    if result['pipeline'] is not None:
        print(f"  Pipeline data: {len(result['pipeline']):,} rows")
    if result['merged'] is not None:
        print(f"  Merged data:   {len(result['merged']):,} rows")

    return result


def auto_export(output_path=None, tracking_dir=None, pipeline_dir=None):
    """
    One-click export of all current data to a timestamped file.

    Automatically rebuilds any stale data before export.

    Args:
        output_path: Where to save (default: generated/export_YYYYMMDD.xlsx)
        tracking_dir: Manual data location
        pipeline_dir: Pipeline data location
    """
    data = get_all_data(tracking_dir, pipeline_dir)

    if output_path is None:
        timestamp = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
        output_dir = Path(tracking_dir or Path(__file__).parent) / 'generated'
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f'all_data_export_{timestamp}.xlsx'

    output_path = Path(output_path)

    with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
        if data['manual'] is not None:
            data['manual'].to_excel(writer, sheet_name='Manual_Scoring', index=False)
        if data['pipeline'] is not None:
            data['pipeline'].to_excel(writer, sheet_name='Pipeline_Reaches', index=False)
        if data['merged'] is not None:
            data['merged'].to_excel(writer, sheet_name='Merged', index=False)

    print(f"\nExported to: {output_path}")
    return output_path


# Quick usage examples
if __name__ == "__main__":
    print("SCI Data Reorganizer")
    print("=" * 30)
    print("\nUsage:")
    print("1. Single file: reorganize_file('your_file.xlsx')")
    print("2. Directory: reorganize_directory('path/to/files')")
    print("\nFor auto-updating unified data:")
    print("  from Behavior_Manual_Data_Stats_Organizer import get_all_data")
    print("  data = get_all_data()  # Always returns current data")
    print("  data['manual']    # Manual tray scoring")
    print("  data['pipeline']  # Automated pipeline reaches")
    print("  data['merged']    # Combined where possible")