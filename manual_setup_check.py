#!/usr/bin/env python
"""
Interactive smoke check of a MouseDB install (not collected by pytest).

Run with:
    python manual_setup_check.py

It:
1. checks that the package imports
2. opens the configured database (creating it if it does not exist yet)
3. shows the database's row counts
4. lists the tracking sheets and, ONLY if the database holds no animals yet,
   offers to import them: a dry run first (reads every sheet, writes
   nothing), then -- if you answer y again -- the real import
5. tries the QueryExporter
6. checks whether the GUI's Qt library is installed

Step 4 imports through mousedb.sheet_sync.import_cohorts, the route every
other sheet import takes (the scheduled job, the Tracking Sheets tab,
`mousedb import --all`, `mousedb-sheets import`). WHY: this script used to
call the importer directly (importers.import_all_cohorts), so its import was
not written to the sheet-import ledger (the Tracking Sheets tab then showed
the cohorts as never imported), did not wait for the lock that keeps two
database tasks from writing at once, did not refresh each animal's details
(animal_records), and read every file whose name matched a pattern -- so a
cohort with a second matching file (a sync-conflict copy, an export made
from the sheet) was imported from both. Now each cohort is imported from its
ONE chosen sheet and recorded like any other import, with triggered_by
"manual_setup_check".
"""

import sys
from pathlib import Path

# Add package to path for development
package_dir = Path(__file__).parent
sys.path.insert(0, str(package_dir))

# How this script's sheet imports are labelled in the sheet-import ledger.
TRIGGERED_BY = "manual_setup_check"


def _indented(line):
    """Print one line of an import result under this script's step heading."""
    print("    " + line)


def _check_tracking_sheets(db, stats):
    """Step 4: list the tracking sheets and, only into a database that holds no
    animals yet, offer to import them -- a dry run first, then the real import."""
    from mousedb import sheet_sync
    st = sheet_sync.status()
    # The same listing 'mousedb-sheets status' prints: which file is each
    # cohort's sheet, when it was edited and imported, and any file that is
    # not read as a sheet (with the reason).
    sheet_sync.main(["status"])
    has_sheets = any(c.get("sheet") for c in st.get("cohorts", [])
                     if c.get("project") == sheet_sync.SHEET_FOLDER_PROJECT)
    if st.get("problem") or not has_sheets:
        print("    Nothing to import (see above).")
    elif stats.get('subjects', 0) != 0:
        print("    The database already holds animals, so nothing is imported here. "
              "Import from the Tracking Sheets tab, or: mousedb-sheets import")
    else:
        print("\n    Database is empty. Import the tracking sheets? (y/n): ", end="")
        response = input().strip().lower()
        if response == 'y':
            print("\n    Dry run first (reads every sheet, writes nothing)...")
            results = sheet_sync.import_cohorts(None, dry_run=True, triggered_by=TRIGGERED_BY)
            sheet_sync.print_import_result(results, log=_indented)
            if results.get("problem"):
                print("    [!] Nothing can be imported until the problem above is fixed.")
            else:
                print("\n    Looks good? Import for real? (y/n): ", end="")
                response = input().strip().lower()
                if response == 'y':
                    results = sheet_sync.import_cohorts(None, dry_run=False,
                                                        triggered_by=TRIGGERED_BY)
                    if sheet_sync.print_import_result(results, log=_indented):
                        print("\n    Import complete!")
                    else:
                        print("\n    [!] Import finished with failures: see the FAIL lines "
                              "above. 'mousedb-sheets status' shows each cohort's state.")
                    print(f"    New stats: {db.get_stats()}")


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
        from mousedb import importers, sheet_sync  # noqa: F401 (the import check itself)
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
    # Empty unless read: a database whose counts cannot be read must not crash
    # the steps below (they ask whether it holds any animals).
    stats = {}
    try:
        stats = db.get_stats()
        for key, value in stats.items():
            print(f"    {key}: {value}")
    except Exception as e:
        print(f"    Error: {e}")

    # Step 4: The tracking sheets -- listed, and imported only into an empty
    # database, through sheet_sync like every other sheet import (see the
    # module docstring for why).
    print("\n[4] Checking the tracking sheets...")
    try:
        _check_tracking_sheets(db, stats)
    except Exception as e:
        # Reported, not raised, like steps 2 and 3: a smoke check that dies
        # here never reaches steps 5 and 6. sheet_sync can raise where the old
        # folder listing could not -- e.g. its import history lives in
        # mousedb_root's logs folder, and a machine that configured only the
        # database file has no such folder yet.
        print(f"    Error: {type(e).__name__}: {e}")

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
