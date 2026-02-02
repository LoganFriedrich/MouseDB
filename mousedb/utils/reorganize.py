import pandas as pd
import os
import glob

def list_excel_files():
    """Find all Excel files in current directory"""
    excel_files = glob.glob("*.xlsx") + glob.glob("*.xls")
    return excel_files

def list_sheets(file_path):
    """List all sheets in an Excel file"""
    try:
        xl_file = pd.ExcelFile(file_path)
        return xl_file.sheet_names
    except PermissionError:
        print(f"\n✗ ERROR: Cannot access {file_path}")
        print("  The file is likely open in Excel or another program.")
        print("  Please close it and try again.\n")
        return None

def get_user_selection(items, item_type="item"):
    """Let user select items from a numbered list"""
    print(f"\nAvailable {item_type}s:")
    for i, item in enumerate(items, 1):
        print(f"{i}. {item}")
    
    print(f"\nEnter {item_type} numbers separated by commas (e.g., 1,3,5)")
    print(f"Or enter 'all' to select all {item_type}s")
    selection = input("Selection: ").strip()
    
    if selection.lower() == 'all':
        return items
    
    try:
        indices = [int(x.strip()) - 1 for x in selection.split(',')]
        selected = [items[i] for i in indices if 0 <= i < len(items)]
        return selected
    except (ValueError, IndexError):
        print("Invalid selection. Please try again.")
        return get_user_selection(items, item_type)

def reorganize_sheet(df):
    """Reshape one sheet from wide to long format"""
    
    print(f"  Found {len(df.columns)} columns: {df.columns.tolist()}")
    
    # Keep metadata columns
    meta_cols = ['Date', 'Animal', 'Sex', 'Weight', 'Tray Type/Number']
    
    # Check if all expected columns exist
    missing_cols = [col for col in meta_cols if col not in df.columns]
    if missing_cols:
        print(f"  ✗ Missing expected columns: {missing_cols}")
        return None
    
    # Identify pellet columns - anything after the metadata columns
    # They could be integers, strings, or mixed
    pellet_cols = [col for col in df.columns if col not in meta_cols]
    
    # Filter to get only the first 20 pellet columns (in case there are extras)
    # Try to identify numeric columns that represent pellet positions
    numeric_pellet_cols = []
    for col in pellet_cols:
        # Check if column name can be converted to int and is in range 1-20
        try:
            col_num = int(col)
            if 1 <= col_num <= 20:
                numeric_pellet_cols.append(col)
        except (ValueError, TypeError):
            # Not a numeric column name, skip it
            pass
    
    if len(numeric_pellet_cols) < 20:
        print(f"  ⚠️  Warning: Only found {len(numeric_pellet_cols)} pellet columns")
        print(f"      Using these columns: {numeric_pellet_cols}")
    
    if len(numeric_pellet_cols) == 0:
        print(f"  ✗ Could not identify pellet columns (expected columns labeled 1-20)")
        print(f"      Non-metadata columns found: {pellet_cols[:10]}...")
        return None
    
    # Use the identified pellet columns
    pellet_cols = numeric_pellet_cols
    
    # Split Tray Type/Number into separate columns
    # Handle formats like "F1", "P2", "E3", etc.
    df['Tray_Type'] = df['Tray Type/Number'].str.extract(r'([A-Za-z]+)')
    df['Tray_Number'] = df['Tray Type/Number'].str.extract(r'(\d+)').astype(int)
    
    # Reshape from wide to long
    id_vars = ['Date', 'Animal', 'Sex', 'Weight', 'Tray_Type', 'Tray_Number']
    
    long_df = df.melt(
        id_vars=id_vars,
        value_vars=pellet_cols,
        var_name='Pellet_Number',
        value_name='Score'
    )
    
    # Convert Pellet_Number to integer (it might be string or int)
    long_df['Pellet_Number'] = pd.to_numeric(long_df['Pellet_Number'], errors='coerce').astype(int)
    
    # Sort for readability
    long_df = long_df.sort_values(['Date', 'Animal', 'Tray_Type', 'Tray_Number', 'Pellet_Number'])
    
    return long_df

def main():
    print("=" * 60)
    print("DATA REORGANIZATION SCRIPT")
    print("=" * 60)
    print("\n⚠️  IMPORTANT: Close all Excel files before running this script!")
    print("=" * 60)
    
    # Find Excel files
    excel_files = list_excel_files()
    
    if not excel_files:
        print("\nNo Excel files found in current directory!")
        return
    
    # Select file(s)
    selected_files = get_user_selection(excel_files, "file")
    
    if not selected_files:
        print("No files selected. Exiting.")
        return
    
    # Process each selected file
    all_data = []
    
    for file_path in selected_files:
        print(f"\n{'=' * 60}")
        print(f"Processing file: {file_path}")
        print('=' * 60)
        
        # List sheets
        sheet_names = list_sheets(file_path)
        
        if sheet_names is None:
            print(f"Skipping {file_path} due to access error.")
            continue
        
        # Select sheets
        selected_sheets = get_user_selection(sheet_names, "sheet")
        
        if not selected_sheets:
            print(f"No sheets selected from {file_path}. Skipping.")
            continue
        
        # Process each selected sheet
        for sheet_name in selected_sheets:
            print(f"\nProcessing sheet: {sheet_name}")
            
            try:
                df = pd.read_excel(file_path, sheet_name=sheet_name)
                long_df = reorganize_sheet(df)
                
                if long_df is not None:
                    # Add source tracking
                    long_df['Source_File'] = file_path
                    long_df['Source_Sheet'] = sheet_name
                    all_data.append(long_df)
                    print(f"  ✓ Reshaped {len(df)} rows into {len(long_df)} rows")
                else:
                    print(f"  ✗ Failed to reshape {sheet_name}")
                    
            except Exception as e:
                print(f"  ✗ Error processing {sheet_name}: {e}")
    
    # Combine all data
    if all_data:
        combined_df = pd.concat(all_data, ignore_index=True)
        
        # Save output
        output_file = "reorganized_data_long_format.csv"
        combined_df.to_csv(output_file, index=False)
        
        print(f"\n{'=' * 60}")
        print(f"SUCCESS!")
        print(f"{'=' * 60}")
        print(f"Total rows processed: {len(combined_df)}")
        print(f"Output saved to: {output_file}")
        print(f"\nColumns in output:")
        for col in combined_df.columns:
            print(f"  - {col}")
        
        # Show preview
        print(f"\nFirst few rows:")
        print(combined_df.head(10).to_string())
        
    else:
        print("\nNo data was successfully processed.")

if __name__ == "__main__":
    main()