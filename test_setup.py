#!/usr/bin/env python
"""
Quick test script for MouseDB.

Run with:
    python test_setup.py

This will:
1. Install the package (if needed)
2. Initialize the database
3. Import existing Excel data
4. Show database stats
5. Test the QueryExporter
"""

import sys
import subprocess
from pathlib import Path

# Add package to path for development
package_dir = Path(__file__).parent
sys.path.insert(0, str(package_dir))

def main():
    print("=" * 60)
    print("MouseDB - Setup Test")
    print("=" * 60)

    # Step 1: Check imports
    print("\n[1] Checking imports...")
    try:
        from mousedb.database import init_database, get_db
        from mousedb.schema import Project, Cohort, Subject, PelletScore
        from mousedb.exporters import QueryExporter
        from mousedb.importers import ExcelImporter, import_all_cohorts
        print("    All imports successful!")
    except ImportError as e:
        print(f"    Import error: {e}")
        print("    Try: pip install -e .[gui]")
        return

    # Step 2: Initialize database
    print("\n[2] Initializing database...")
    try:
        db = init_database()
        print(f"    Database: {db.db_path}")
        print(f"    Exists: {db.db_path.exists()}")
    except Exception as e:
        print(f"    Error: {e}")
        return

    # Step 3: Show stats
    print("\n[3] Database stats:")
    try:
        stats = db.get_stats()
        for key, value in stats.items():
            print(f"    {key}: {value}")
    except Exception as e:
        print(f"    Error: {e}")

    # Step 4: Check for Excel files to import
    print("\n[4] Checking for Excel files to import...")
    cohorts_dir = Path("Y:/2_Connectome/Behavior/3_Connectome_Animal_Cohorts")
    if cohorts_dir.exists():
        excel_files = list(cohorts_dir.glob("Connectome_*.xlsx"))
        print(f"    Found {len(excel_files)} Excel files in {cohorts_dir}")
        for f in excel_files[:5]:
            print(f"      - {f.name}")
        if len(excel_files) > 5:
            print(f"      ... and {len(excel_files) - 5} more")

        # Ask user if they want to import
        if stats.get('subjects', 0) == 0 and excel_files:
            print("\n    Database is empty. Import Excel files? (y/n): ", end="")
            response = input().strip().lower()
            if response == 'y':
                print("\n    Importing (dry run first)...")
                results = import_all_cohorts(cohorts_dir, dry_run=True)

                print("\n    Looks good? Import for real? (y/n): ", end="")
                response = input().strip().lower()
                if response == 'y':
                    results = import_all_cohorts(cohorts_dir, dry_run=False)
                    print("\n    Import complete!")
                    stats = db.get_stats()
                    print(f"    New stats: {stats}")
    else:
        print(f"    Directory not found: {cohorts_dir}")

    # Step 5: Test QueryExporter
    print("\n[5] Testing QueryExporter...")
    try:
        qe = QueryExporter(db)

        # Show the SQL it would generate
        print("\n    Example query (even subjects, post-injury, retrieved):")
        sql = qe.even_subjects().post_injury().retrieved_only().show_sql()

        # Try to run it
        print("\n    Executing query...")
        count = qe.even_subjects().post_injury().retrieved_only().count()
        print(f"    Result: {count} rows")

        if count > 0:
            print("\n    Preview:")
            df = qe.even_subjects().post_injury().retrieved_only().preview(5)
            print(df)
    except Exception as e:
        print(f"    Error: {e}")
        import traceback
        traceback.print_exc()

    # Step 6: GUI test
    print("\n[6] GUI available?")
    try:
        from PyQt5.QtWidgets import QApplication
        print("    PyQt5 is installed!")
        print("    To launch GUI, run: mousedb entry")
        print("    Or: python -c \"from mousedb.gui.app import main; main()\"")

    except ImportError:
        print("    PyQt5 not installed. Install with: pip install PyQt5")

    print("\n" + "=" * 60)
    print("Setup test complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
