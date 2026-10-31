"""
Command-line interface for MouseDB.

Commands:
    mousedb-entry           # Launch PyQt GUI
    mousedb-new-cohort      # Create new cohort
    mousedb-import          # Import Excel files
    mousedb-export          # Export to Excel/Parquet
    mousedb-status          # Show database stats
"""

import argparse
import os
import sys
from pathlib import Path
from datetime import datetime


def cmd_status(args):
    """Show database status and statistics."""
    from .database import get_db, init_database

    db = init_database()
    stats = db.get_stats()

    print("\n=== MouseDB Database Status ===")
    print(f"Database: {stats['db_path']}")
    print(f"Size: {stats['db_size_mb']:.2f} MB")
    print()
    print("Record counts:")
    print(f"  Projects:           {stats['projects']}")
    print(f"  Cohorts:            {stats['cohorts']}")
    print(f"  Subjects:           {stats['subjects']}")
    print(f"  Weights:            {stats['weights']}")
    print(f"  Pellet scores:      {stats['pellet_scores']}")
    print(f"  Surgeries:          {stats['surgeries']}")
    print(f"  Ramp entries:       {stats['ramp_entries']}")
    print(f"  Ladder entries:     {stats['ladder_entries']}")
    print(f"  Virus preps:        {stats['virus_preps']}")
    print(f"  Archived summaries: {stats['archived_summaries']}")


def cmd_init(args):
    """Initialize the database."""
    from .database import init_database

    db = init_database()
    print("Database initialized successfully.")
    cmd_status(args)


def cmd_new_cohort(args):
    """Create a new cohort."""
    from .database import get_db, init_database
    from .schema import Project, Cohort, Subject

    db = init_database()

    cohort_id = args.cohort.upper()
    start_date = datetime.strptime(args.start_date, '%Y-%m-%d').date()
    num_mice = args.mice

    # Validate cohort ID format
    from .validators import validate_cohort_id
    valid, msg = validate_cohort_id(cohort_id)
    if not valid:
        print(f"Error: {msg}")
        sys.exit(1)

    project_code = cohort_id.split('_')[0]

    with db.session() as session:
        # Check if cohort exists
        existing = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
        if existing:
            print(f"Error: Cohort {cohort_id} already exists")
            sys.exit(1)

        # Ensure project exists
        project = session.query(Project).filter_by(project_code=project_code).first()
        if not project:
            project = Project(project_code=project_code, project_name=project_code)
            session.add(project)

        # Create cohort
        cohort = Cohort(
            cohort_id=cohort_id,
            project_code=project_code,
            start_date=start_date,
            num_mice=num_mice,
        )
        session.add(cohort)
        session.flush()

        # Create subjects
        for i in range(1, num_mice + 1):
            subject_id = f"{cohort_id}_{i:02d}"
            subject = Subject(subject_id=subject_id, cohort_id=cohort_id)
            session.add(subject)

        session.commit()

    print(f"Created cohort {cohort_id} with {num_mice} subjects")
    print(f"  Start date: {start_date}")
    print(f"  Subjects: {cohort_id}_01 through {cohort_id}_{num_mice:02d}")


def cmd_cohort_sheets(args):
    """Show, set, or discover where this machine's cohort tracking sheets are.

    The path is lab-specific and this repository is public, so it is never in
    source: it lives in ~/.mousedb/config.json, or the MOUSEDB_COHORT_SHEETS
    environment variable.
    """
    from .cohort_sheets import (
        describe, discover, set_cohort_sheets_dir, available_cohorts,
        find_cohort_sheet, CONFIG_PATH)

    if getattr(args, 'set_aspa', None):
        target = Path(args.set_aspa)
        if not target.is_dir():
            print(f"Not a directory: {target}")
            sys.exit(1)
        from .cohort_sheets import available_aspa_cohorts, set_aspa_data_dir
        letters = available_aspa_cohorts(target)
        if not letters:
            print(f"No ASPA animal sheets in {target}")
            print('Expected workbooks named for a cohort letter, e.g. J.xlsx')
            sys.exit(1)
        cfg = set_aspa_data_dir(target)
        print(f"ASPA animal sheets: {target}")
        print(f"  cohorts with a sheet: {', '.join(letters)}")
        print(f"  saved to {cfg} (local to this machine, never committed)")
        return

    if args.set:
        from .sheet_sync import snapshot_folder_of, _ascii
        # Recorded as an ABSOLUTE path. WHY: a folder typed relative to where
        # this was run was saved as typed, and every later run from anywhere
        # else (the scheduled job, the GUI) found no such folder and said "not
        # configured". abspath, not resolve(): a mapped drive letter is kept.
        target = Path(os.path.abspath(args.set))
        if not target.is_dir():
            print(_ascii(f"Not a directory: {target}"))
            sys.exit(1)
        found = available_cohorts(target)
        if not found:
            from .sheet_projects import describe
            print(_ascii(f"No cohort tracking sheets in {target}"))
            print(_ascii("Expected files named by a project's sheet name -- %s" % describe()))
            sys.exit(1)
        if snapshot_folder_of(target):
            print(_ascii(f"REFUSING: {target} looks like a snapshot folder, not the"))
            print("live sheets. Snapshots go stale -- point at the working copy.")
            sys.exit(1)
        cfg = set_cohort_sheets_dir(target)
        print(_ascii(f"Cohort sheets: {target}"))
        print(f"  cohorts with a sheet: {', '.join(found)}")
        print(_ascii(f"  saved to {cfg} (local to this machine, never committed)"))
        return

    if args.discover:
        print("Looking for cohort tracking sheets...")
        hits = discover()
        if not hits:
            print("Found none. Say where they are:")
            print('    mousedb cohort-sheets --set "<path>"')
            sys.exit(1)
        for i, d in enumerate(hits, 1):
            print(f"  [{i}] {d}")
            print(f"      cohorts: {', '.join(available_cohorts(d)) or '(none)'}")
        if len(hits) == 1:
            cfg = set_cohort_sheets_dir(hits[0])
            print(f"\nSaved to {cfg}")
        else:
            print("\nMore than one. Pick with:")
            print('    mousedb cohort-sheets --set "<path>"')
        return

    print(describe())
    if args.cohort:
        # The cohort is read by the same rule as every other command that takes
        # one. WHY: this used to keep only the trailing number, so asking for
        # another project's cohort 01 (or an animal id ending in 01) showed
        # CNT_01's sheet under the name that was asked for.
        from .sheet_sync import requested_cohort_id, _ascii
        cohort_id, why = requested_cohort_id(args.cohort)
        print()
        if why:
            print(_ascii(why))
            sys.exit(1)
        sheet = find_cohort_sheet(cohort_id)
        if sheet is None:
            print(f"No sheet found for {cohort_id}")
        else:
            import datetime
            m = datetime.date.fromtimestamp(sheet.stat().st_mtime)
            print(_ascii(f"{cohort_id}: {sheet}"))
            print(f"  {sheet.stat().st_size // 1024} KB, last edited {m}")


def cmd_import(args):
    """Import Excel tracking sheets -- through mousedb.sheet_sync, like every
    other route (the Tracking Sheets tab, the scheduled job, the update button).

    WHY it delegates: until 2026-10 this command called the importer itself.
    ``--all`` threw every cohort's errors and warnings away, recorded nothing
    in the import ledger (so the Tracking Sheets tab could not see it), did not
    wait for the database lock the other imports share, and exited 0 even when
    every cohort failed -- and the "Update the database" button ran exactly
    this. Now each cohort's outcome is printed with its warning count, written
    to the ledger (unless --dry-run), and any failure exits 1.

    Every refusal of a folder (none configured, missing, a snapshot) or of a
    named file (missing, in a snapshot folder, named as a copy, a lock file or
    a tool's export) happens BEFORE the database is opened, so it touches
    nothing at all. One refusal comes later: a --file whose name says no
    cohort at all is refused by the importer's own naming rule, after the
    database is opened but before anything is read from the file or imported.
    """
    from . import sheet_sync
    from .cohort_sheets import cohort_sheets_dir, describe

    if not args.all and not args.file:
        # WHY a sentence: with neither, this used to crash on Path(None).
        print("Say what to import: --all (every cohort's sheet) or --file <workbook>.")
        sys.exit(1)

    if args.all:
        # Where the sheets actually are, not where they used to be. The old
        # default pointed at a folder that no longer existed -- so this command
        # found nothing and said so in
        # a way that read like "nothing to import".
        if args.directory:
            # Absolute, so the ledger records where the sheets were read from.
            cohorts_dir = Path(os.path.abspath(args.directory))
            if not cohorts_dir.is_dir():
                # WHY its own sentence: this used to print only the CONFIGURED
                # folder's description, which says nothing about the folder
                # that was actually named.
                print(sheet_sync._ascii(f"Not a folder: {cohorts_dir} -- nothing was imported."))
                sys.exit(1)
        else:
            cohorts_dir = cohort_sheets_dir()
        if cohorts_dir is None or not cohorts_dir.exists():
            print(describe())
            sys.exit(1)
        print(sheet_sync._ascii(f"Reading cohort sheets from: {cohorts_dir}"))
        snapshot = sheet_sync.snapshot_folder_of(cohorts_dir)
        if snapshot:
            print(sheet_sync._ascii(
                f"REFUSED: that folder is inside '{snapshot}', a snapshot or archive "
                "folder, not the live sheets."))
            # WHY each sentence: a snapshot copy stops at the day it was taken,
            # and can be nearly empty while the live sheet holds the full record.
            # This route replaces each cohort's animal records with what the
            # sheet holds and only ADDS its other rows, so an old copy would
            # both wipe newer records and leave rows no later import removes.
            print("Copies kept there go out of date -- one can be nearly empty while")
            print("the live sheet holds the full record. Importing them would replace")
            print("each cohort's animal records with the old copy's, and the rows they")
            print("add stay in the database for good. Point at the live folder instead:")
            print(describe())
            sys.exit(1)
    else:
        excel_path = Path(args.file)
        if not excel_path.is_file():
            print(sheet_sync._ascii(f"Error: File not found: {excel_path}"))
            sys.exit(1)
        # REFUSED, not warned about. WHY: this route REPLACES the cohort's
        # animal records with the file's contents, so a stale copy (it used to
        # be imported after a warning) overwrote good records until the next
        # scheduled import, and its rows stayed for good. The same files the
        # folder route never reads are refused here -- see
        # sheet_sync.refuse_sheet_file.
        refusal = sheet_sync.refuse_sheet_file(excel_path)
        if refusal:
            why, instead = refusal
            print(sheet_sync._ascii(f"REFUSED: {excel_path} was not imported."))
            print(sheet_sync._ascii(f"  Why: {why}."))
            # No full stop after it: the instruction can end in a command,
            # and a copied full stop would become part of that command.
            print(sheet_sync._ascii(f"  Instead: {instead}"))
            sys.exit(1)

    from .database import init_database
    init_database()

    if args.all:
        # One chosen sheet per cohort (a pinned file, else the newest), the
        # same choice the Tracking Sheets tab shows -- not every file whose
        # name matches, which is what this route used to import.
        result = sheet_sync.import_cohorts(
            None, dry_run=args.dry_run, triggered_by="mousedb-import",
            sheets_dir=cohorts_dir if args.directory else None)
        ok = sheet_sync.print_import_result(result)
    else:
        result = sheet_sync.import_sheet_file(
            excel_path, dry_run=args.dry_run, triggered_by="mousedb-import --file")
        # Ten warnings shown, as this route always showed; the count says how
        # many more there were.
        ok = sheet_sync.print_import_result(result, show_warnings=10)

    # Auto-run completeness check after successful (non-dry-run) import
    if not args.dry_run:
        try:
            from .database import get_db
            from .diagnostics import check_all_cohorts, print_completeness_report

            print(f"\n{'=' * 60}")
            print("POST-IMPORT COMPLETENESS CHECK")
            print(f"{'=' * 60}")

            db = get_db()
            with db.session() as session:
                report = check_all_cohorts(session)
                print_completeness_report(report, verbose=False)
        except Exception as e:
            print(f"\nWarning: Could not run completeness check: {e}")

    # Last, after the report: a failed cohort fails the command. WHY: the
    # update button and any scheduler read the exit code, and this command
    # used to exit 0 whatever happened, so a failing import looked green.
    if not ok:
        print("[FAIL] the import did not complete for every cohort -- the reasons are above")
        raise SystemExit(1)


def _sheet_folders():
    """Every folder this machine names as holding the animal sheets mousedb
    imports from: the cohort sheets folder and the ASPA folder, both as
    configured and as found. WHY both: the finders return a folder only while
    it holds sheets, and a configured folder that is briefly offline or empty
    is still the source of record."""
    from . import cohort_sheets as cs
    from .config import get as config_get
    found = []
    for lookup in (lambda: config_get("cohort_sheets_dir"),
                   lambda: config_get("aspa_data_dir"),
                   lambda: os.environ.get(cs.ENV_VAR),
                   lambda: os.environ.get(cs.ASPA_ENV_VAR),
                   cs.cohort_sheets_dir, cs.aspa_data_dir):
        try:
            value = lookup()
        except Exception:  # an unreadable setting must not stop the check
            value = None
        if value:
            found.append(Path(value))
    return found


def _is_inside(path, folder):
    """True if ``path`` is ``folder`` or lies under it, judged on the typed
    path and on the resolved one. WHY both: a mapped drive letter and the
    network name behind it are the same folder."""
    def forms(p):
        out = {os.path.normcase(os.path.abspath(str(p)))}
        try:
            out.add(os.path.normcase(os.path.realpath(str(p))))
        except (OSError, ValueError):
            pass
        return out
    for p in forms(path):
        for f in forms(folder):
            if p == f or p.startswith(f.rstrip("\\/") + os.sep):
                return True
    return False


def _export_refusal(output, is_folder):
    """Why ``mousedb export`` must not write to ``output``, or None.

    WHY this exists: the export writes with pandas' ExcelWriter, which empties
    whatever file is at the path without asking. Pointed at a tracking
    workbook, it replaced the sheet -- the lab's record -- with a rebuild from
    the database: no formulas, and none of the columns the database does not
    hold. So an export is never written:
      * inside a folder the sheets are read from (they are read-only to
        mousedb's exports), whatever its name;
      * under a name the sheet finder takes for a cohort's tracking sheet,
        wherever it is. That name would empty the workbook already there, and
        a file so named that later reaches the sheets folder would be imported
        as the cohort's sheet, replacing its animal records with the rebuild's.
    """
    target = Path(os.path.abspath(output))
    for folder in _sheet_folders():
        if _is_inside(target, folder):
            return ("it is inside %s, a folder the animal sheets are read from, and an "
                    "export never writes there" % folder)
    if not is_folder:
        from .cohort_sheets import _sheet_cohort
        if _sheet_cohort(target.name):
            return ("'%s' is named as a cohort's tracking sheet; saved under that name, the "
                    "export would empty any workbook already there and could later be "
                    "imported as that cohort's sheet" % target.name)
    return None


def cmd_export(args):
    """Export data to Excel or Parquet.

    An --output that is inside a sheets folder, or that is named as a cohort's
    tracking sheet, is refused before the database is opened -- see
    _export_refusal for why."""
    if args.output:
        from .sheet_sync import _ascii
        why = _export_refusal(args.output, is_folder=bool(args.all_formats))
        if why:
            print(_ascii("REFUSED: nothing was exported to %s" % os.path.abspath(args.output)))
            print(_ascii("  Why: %s." % why))
            # No full stop after it: the advice ends in an option a person
            # may copy, and a copied full stop would become part of it.
            print("  Instead: give an --output in another folder, or leave --output out to "
                  "write into mousedb's exports folder")
            sys.exit(1)

    from .database import get_db, init_database
    from .exporters import (
        export_cohort_to_excel, export_unified_to_parquet,
        export_odc_format, export_all_formats
    )

    db = init_database()

    if args.unified:
        output_path = Path(args.output) if args.output else None
        export_unified_to_parquet(db, output_path)
    elif args.all_formats:
        cohort_id = args.cohort.upper()
        output_dir = Path(args.output) if args.output else None
        export_all_formats(db, cohort_id, output_dir)
    elif args.odc:
        cohort_id = args.cohort.upper()
        output_path = Path(args.output) if args.output else None
        export_odc_format(db, cohort_id, output_path)
    else:
        cohort_id = args.cohort.upper()
        output_path = Path(args.output) if args.output else None
        export_cohort_to_excel(db, cohort_id, output_path)


def cmd_check(args):
    """Run data completeness diagnostics."""
    import json
    from .database import init_database
    from .diagnostics import (
        check_all_cohorts, check_cohort_completeness,
        print_completeness_report, print_cohort_report, format_report_as_dict
    )

    db = init_database()

    with db.session() as session:
        if args.cohort:
            cohort_id = args.cohort.upper()
            report_single = check_cohort_completeness(session, cohort_id)
            if args.json:
                # Wrap single cohort in a full report structure for consistency
                from .diagnostics import CompletenessReport
                from .config import require
                full_report = CompletenessReport(
                    cohorts=[report_single],
                    db_path=str(require("db_path")),
                )
                print(json.dumps(format_report_as_dict(full_report), indent=2))
            else:
                print_cohort_report(report_single, verbose=args.verbose)
        else:
            report = check_all_cohorts(session)
            if args.json:
                print(json.dumps(format_report_as_dict(report), indent=2))
            else:
                print_completeness_report(report, verbose=args.verbose)


def cmd_entry(args):
    """Launch the PyQt data entry GUI."""
    try:
        from .gui.app import main as gui_main
        gui_main()
    except ImportError as e:
        print(f"Error: GUI dependencies not available: {e}")
        print("Install PyQt5: pip install PyQt5")
        sys.exit(1)


def cmd_browse(args):
    """Browse database tables in the terminal."""
    from .database import init_database
    from .schema import (
        Project, Cohort, Subject, Weight, PelletScore, Surgery,
        RampEntry, VirusPrep
    )

    db = init_database()

    # Define available tables with their models and columns
    TABLES = {
        'projects': (Project, ['project_code', 'project_name', 'description']),
        'cohorts': (Cohort, ['cohort_id', 'project_code', 'start_date', 'num_mice', 'notes']),
        'subjects': (Subject, ['subject_id', 'cohort_id', 'date_of_birth', 'sex', 'notes']),
        'weights': (Weight, ['id', 'subject_id', 'date', 'weight_grams', 'entered_by']),
        'pellet_scores': (PelletScore, ['id', 'subject_id', 'session_date', 'test_phase', 'tray_type', 'tray_number', 'pellet_number', 'score']),
        'ramp_entries': (RampEntry, ['id', 'subject_id', 'date', 'day_number', 'body_weight', 'food_offered', 'food_remaining']),
        'surgeries': (Surgery, ['id', 'subject_id', 'surgery_date', 'surgery_type', 'force_kdyn', 'displacement_um', 'surgeon']),
        'virus_preps': (VirusPrep, ['id', 'cohort_id', 'prep_date', 'virus_name', 'lot_number', 'original_titer', 'final_titer']),
    }

    table_name = args.table.lower() if args.table else None

    if args.list or table_name is None:
        # List all tables with record counts
        print("\n=== Database Tables ===\n")
        with db.session() as session:
            for name, (model, cols) in TABLES.items():
                count = session.query(model).count()
                print(f"  {name:15} {count:>6,} records")
        print("\nUse: mousedb browse <table_name> [--limit N]")
        return

    if table_name not in TABLES:
        print(f"Error: Unknown table '{table_name}'")
        print(f"Available tables: {', '.join(TABLES.keys())}")
        sys.exit(1)

    model, columns = TABLES[table_name]
    limit = args.limit

    print(f"\n=== {table_name.upper()} ===\n")

    with db.session() as session:
        query = session.query(model)

        # Apply filter if provided
        if args.filter:
            # Simple filter: column=value
            try:
                col_name, value = args.filter.split('=', 1)
                col_name = col_name.strip()
                value = value.strip()
                if hasattr(model, col_name):
                    from sqlalchemy import String
                    col = getattr(model, col_name)
                    # Check if it's a string column for LIKE matching
                    if hasattr(col.type, 'impl') or isinstance(col.type, String):
                        query = query.filter(col.like(f'%{value}%'))
                    else:
                        query = query.filter(col == value)
            except ValueError:
                print(f"Warning: Invalid filter format. Use column=value")

        total = query.count()
        records = query.limit(limit).all()

        # Calculate column widths
        col_widths = {col: len(col) for col in columns}
        for record in records:
            for col in columns:
                val = str(getattr(record, col, '') or '')
                if len(val) > 30:
                    val = val[:27] + '...'
                col_widths[col] = max(col_widths[col], len(val))

        # Print header
        header = ' | '.join(col.ljust(col_widths[col]) for col in columns)
        print(header)
        print('-' * len(header))

        # Print rows
        for record in records:
            row_vals = []
            for col in columns:
                val = str(getattr(record, col, '') or '')
                if len(val) > 30:
                    val = val[:27] + '...'
                row_vals.append(val.ljust(col_widths[col]))
            print(' | '.join(row_vals))

        print(f"\nShowing {len(records)} of {total:,} records")
        if len(records) < total:
            print(f"Use --limit to see more (e.g., --limit 100)")


def cmd_dump(args):
    """Dump database tables to CSV files for human inspection."""
    import pandas as pd
    from .database import init_database
    from .schema import (
        Project, Cohort, Subject, Weight, PelletScore, Surgery,
        RampEntry, VirusPrep, AuditLog
    )

    db = init_database()

    # Output directory
    from .config import require
    output_dir = Path(args.output) if args.output else require("mousedb_root") / "database_dump"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Tables to export
    TABLES = {
        'projects': Project,
        'cohorts': Cohort,
        'subjects': Subject,
        'weights': Weight,
        'pellet_scores': PelletScore,
        'ramp_entries': RampEntry,
        'surgeries': Surgery,
        'virus_preps': VirusPrep,
        'audit_log': AuditLog,
    }

    # Filter to specific table if requested
    if args.table:
        table_name = args.table.lower()
        if table_name not in TABLES:
            print(f"Error: Unknown table '{table_name}'")
            print(f"Available tables: {', '.join(TABLES.keys())}")
            sys.exit(1)
        tables_to_export = {table_name: TABLES[table_name]}
    else:
        tables_to_export = TABLES

    print(f"\n=== Dumping Database Tables to CSV ===")
    print(f"Output directory: {output_dir}\n")

    with db.session() as session:
        for table_name, model in tables_to_export.items():
            # Query all records
            records = session.query(model).all()

            if not records:
                print(f"  {table_name}: (empty)")
                continue

            # Convert to list of dicts
            data = []
            for record in records:
                row = {}
                for col in model.__table__.columns:
                    row[col.name] = getattr(record, col.name)
                data.append(row)

            # Create DataFrame and save
            df = pd.DataFrame(data)
            output_path = output_dir / f"{table_name}.csv"
            df.to_csv(output_path, index=False)
            print(f"  {table_name}: {len(df):,} rows -> {output_path.name}")

    print(f"\nDump complete! Open the CSV files in Excel or any spreadsheet program.")
    print(f"Location: {output_dir}")


def cmd_video_status(args):
    """Show video pipeline status from watcher.db."""
    from .watcher_bridge import (
        WatcherBridge, STATE_DISPLAY, PIPELINE_STATES, ERROR_STATES, DONE_STATES,
    )

    bridge = WatcherBridge()

    if not bridge.is_available:
        print(f"\n  Video pipeline status unavailable.")
        print(f"  {bridge.status.message}")
        print(f"\n  The watcher tracks video processing state in watcher.db.")
        print(f"  It will become available after the watcher processes its first video.")
        return

    summary = bridge.get_pipeline_summary()

    # JSON output
    if getattr(args, 'json', False):
        import json
        output = {
            'watcher_db': str(bridge.status.db_path),
            'summary': {
                'total_videos': summary.total_videos,
                'total_collages': summary.total_collages,
                'by_state': summary.by_state,
                'fully_processed_pct': round(summary.fully_processed_pct, 1),
            },
            'cohorts': bridge.get_cohort_rollup(),
        }
        if getattr(args, 'by_animal', False):
            animals = bridge.get_animal_rollup()
            if getattr(args, 'cohort', None):
                animals = [a for a in animals
                           if a.cohort_id == args.cohort.upper()]
            output['animals'] = [
                {
                    'subject_id': a.subject_id,
                    'cohort_id': a.cohort_id,
                    'total_videos': a.total_videos,
                    'by_state': a.by_state,
                    'failed_videos': a.failed_videos,
                    'latest_activity': a.latest_activity,
                } for a in animals
            ]
        if getattr(args, 'show_errors', False):
            output['failed_videos'] = bridge.get_failed_videos()
        print(json.dumps(output, indent=2, default=str))
        return

    # Text output
    print(f"\n=== Video Pipeline Status ===")
    print(f"Source: {bridge.status.db_path}")

    done = sum(summary.by_state.get(s, 0) for s in DONE_STATES)
    in_progress = (summary.total_videos - done
                   - summary.failed_count - summary.quarantined_count)

    print(f"\nOverall: {summary.total_videos} videos, "
          f"{summary.total_collages} collages")
    print(f"Completed: {summary.fully_processed_pct:.1f}%  "
          f"({done} done, {in_progress} in progress, "
          f"{summary.failed_count} failed)")

    if summary.by_state:
        print(f"\nBy state:")
        for state in PIPELINE_STATES + ERROR_STATES:
            count = summary.by_state.get(state, 0)
            if count > 0:
                label = STATE_DISPLAY.get(state, {}).get('label', state)
                print(f"  {label:15s} {count:>5d}")

    # Per-cohort rollup (default view)
    cohorts = bridge.get_cohort_rollup()
    if cohorts:
        print(f"\nBy cohort:")
        print(f"  {'Cohort':<12} {'Animals':>8} {'Videos':>8} "
              f"{'Done':>8} {'Failed':>8} {'Pct':>6}")
        print(f"  {'-' * 54}")
        for cid, data in sorted(cohorts.items()):
            pct = (data['fully_processed'] / data['total_videos'] * 100
                   if data['total_videos'] > 0 else 0)
            print(f"  {cid:<12} {data['animals']:>8} "
                  f"{data['total_videos']:>8} "
                  f"{data['fully_processed']:>8} "
                  f"{data['failed']:>8} {pct:>5.1f}%")

    # Per-animal breakdown (if requested)
    if getattr(args, 'by_animal', False):
        animals = bridge.get_animal_rollup()
        if getattr(args, 'cohort', None):
            animals = [a for a in animals
                       if a.cohort_id == args.cohort.upper()]

        if animals:
            print(f"\nBy animal:")
            print(f"  {'Subject':<12} {'Total':>6} {'Done':>6} "
                  f"{'In Progress':>12} {'Failed':>8}")
            print(f"  {'-' * 48}")
            for a in animals:
                a_done = sum(a.by_state.get(s, 0) for s in DONE_STATES)
                a_in_progress = a.total_videos - a_done - a.failed_videos
                print(f"  {a.subject_id:<12} {a.total_videos:>6} "
                      f"{a_done:>6} {a_in_progress:>12} "
                      f"{a.failed_videos:>8}")

    # Failed/quarantined details (if requested)
    if getattr(args, 'show_errors', False):
        failed = bridge.get_failed_videos()
        if failed:
            print(f"\nFailed/Quarantined videos ({len(failed)}):")
            for v in failed[:20]:
                sid = v.get('animal_id', '?')
                print(f"  {v['video_id']}  ({sid})  "
                      f"state={v['state']}  errors={v.get('error_count', 0)}")
                if v.get('error_message'):
                    print(f"    {v['error_message'][:80]}")
            if len(failed) > 20:
                print(f"  ... and {len(failed) - 20} more")
        else:
            print(f"\nNo failed or quarantined videos.")


def cmd_archive_status(args):
    """Show archive processing and version compliance status."""
    from .watcher_bridge import (
        WatcherBridge, ARCHIVE_STATE_DISPLAY,
    )

    bridge = WatcherBridge()

    if not bridge.is_available:
        print("\n  Archive status unavailable.")
        print(f"  {bridge.status.message}")
        print("\n  The watcher tracks video processing state in watcher.db.")
        print("  It will become available after the watcher processes its first video.")
        return

    summary = bridge.get_archive_summary()

    # JSON output
    if getattr(args, 'json', False):
        import json
        cohort_rollup = bridge.get_archive_cohort_rollup()
        output = {
            'watcher_db': str(bridge.status.db_path),
            'versions_json': str(summary.versions_path) if summary.versions_path else None,
            'versions': (summary.versions_info.get('versions', {})
                         if summary.versions_info else {}),
            'summary': {
                'total_videos': summary.total_videos,
                'archived': summary.archived,
                'outdated': summary.outdated,
                'crystallized': summary.crystallized,
                'in_pipeline': summary.in_pipeline,
                'failed': summary.failed,
            },
            'cohorts': [
                {
                    'cohort_id': c.cohort_id,
                    'total_videos': c.total_videos,
                    'archived': c.archived,
                    'outdated': c.outdated,
                    'crystallized': c.crystallized,
                    'in_pipeline': c.in_pipeline,
                    'failed': c.failed,
                    'completion_pct': round(c.completion_pct, 1),
                    'crystallized_label': c.crystallized_label,
                }
                for c in cohort_rollup
            ],
        }
        if getattr(args, 'verbose', False) or getattr(args, 'cohort', None):
            cohort_filter = getattr(args, 'cohort', None)
            if cohort_filter:
                cohort_filter = cohort_filter.upper()
            animal_rollup = bridge.get_archive_animal_rollup(cohort=cohort_filter)
            output['animals'] = [
                {
                    'subject_id': a.subject_id,
                    'cohort_id': a.cohort_id,
                    'total_videos': a.total_videos,
                    'archived': a.archived,
                    'outdated': a.outdated,
                    'crystallized': a.crystallized,
                    'in_pipeline': a.in_pipeline,
                    'failed': a.failed,
                    'crystallized_label': a.crystallized_label,
                }
                for a in animal_rollup
            ]
        print(json.dumps(output, indent=2, default=str))
        return

    # Text output
    print("\n=== Archive Processing Status ===")
    source_parts = [f"Source: {bridge.status.db_path}"]
    if summary.versions_path:
        source_parts.append(f"Versions: {summary.versions_path}")
    print('  |  '.join(source_parts))

    if summary.versions_info and summary.versions_info.get('versions'):
        v = summary.versions_info['versions']
        ver_str = ', '.join(f"{k}={val}" for k, val in v.items())
        print(f"Current: {ver_str}")

    print()

    # Per-cohort progress bars
    cohort_rollup = bridge.get_archive_cohort_rollup()
    if cohort_rollup:
        max_cid = max(len(c.cohort_id) for c in cohort_rollup)
        max_total = max(c.total_videos for c in cohort_rollup)
        total_digits = len(str(max_total))

        for c in cohort_rollup:
            bar_width = 16
            if c.total_videos > 0:
                filled = int(bar_width * c.complete_count / c.total_videos)
            else:
                filled = 0
            bar = '=' * filled
            if filled < bar_width and filled > 0:
                bar = bar[:-1] + '>'
            bar = bar.ljust(bar_width)

            details = []
            if c.outdated > 0:
                details.append(f"{c.outdated} outdated")
            if c.in_pipeline > 0:
                details.append(f"{c.in_pipeline} in pipeline")
            if c.failed > 0:
                details.append(f"{c.failed} failed")
            if c.crystallized > 0 and c.crystallized_label:
                details.append(f"crystallized: {c.crystallized_label}")
            elif c.crystallized > 0:
                details.append(f"{c.crystallized} crystallized")

            detail_str = f"  ({', '.join(details)})" if details else ""

            print(f"{c.cohort_id:<{max_cid}}  [{bar}]  "
                  f"{c.complete_count:>{total_digits}}/{c.total_videos:<{total_digits}} videos  "
                  f"{c.completion_pct:>5.1f}%{detail_str}")

    print()

    # Overall summary
    total_current = summary.archived + summary.crystallized
    if summary.total_videos > 0:
        overall_pct = total_current / summary.total_videos * 100
    else:
        overall_pct = 0
    print(f"Version compliance: {summary.archived}/{summary.total_videos} "
          f"current ({overall_pct:.0f}%)")
    if summary.outdated > 0:
        print(f"Outdated (need reprocess): {summary.outdated}")
    if summary.crystallized > 0:
        print(f"Crystallized: {summary.crystallized}")

    # Per-animal breakdown (verbose or specific cohort)
    if getattr(args, 'verbose', False) or getattr(args, 'cohort', None):
        cohort_filter = getattr(args, 'cohort', None)
        if cohort_filter:
            cohort_filter = cohort_filter.upper()
        animal_rollup = bridge.get_archive_animal_rollup(cohort=cohort_filter)

        if animal_rollup:
            header = "Per-animal breakdown"
            if cohort_filter:
                header += f" ({cohort_filter})"
            print(f"\n{header}:")
            print(f"  {'Subject':<12} {'Total':>6} {'Archived':>9} "
                  f"{'Outdated':>9} {'Crystal':>8} {'Pipeline':>9} {'Failed':>7}")
            print(f"  {'-' * 62}")
            for a in animal_rollup:
                print(f"  {a.subject_id:<12} {a.total_videos:>6} "
                      f"{a.archived:>9} {a.outdated:>9} "
                      f"{a.crystallized:>8} {a.in_pipeline:>9} {a.failed:>7}")


# =========================================================================

def cmd_backfill_phases(args):
    """Backfill test_phase and phase_group across pellet_scores and reach_data."""
    from .backfill import backfill_phases, print_stats

    stats = backfill_phases(dry_run=args.dry_run)
    print_stats(stats)


def cmd_hand_scores(args):
    """Write the hand pellet scores file the reaching pipeline reads."""
    from .hand_scores_file import main
    # Its own exit status: the CLI ignores what a command returns, and a
    # failed write must not exit 0.
    raise SystemExit(main([]))


def cmd_import_brains(args):
    """Import BrainGlobe brain data into connectome.db."""
    # Where MouseBrain's 2_Data_Summary is: given, or derived from the configured
    # pipeline root. Resolved BEFORE the database is opened so an unconfigured
    # machine stops with the exact `mousedb config --set` line and touches
    # nothing. WHY: this used to reference a module constant that no longer
    # existed -- a NameError on every run without --summary-dir (2026-08).
    summary_dir = None
    if args.summary_dir:
        summary_dir = Path(args.summary_dir)
    elif args.all:
        from .config import require
        summary_dir = require("mousebrain_pipeline_root") / "3D_Cleared" / "2_Data_Summary"

    if args.dry_run:
        return _import_brains_body(args, summary_dir)
    # Under the cross-task mutex. WHY: this import commits to connectome.db like
    # the reach import and the snapshot's long reads, but did not take the mutex
    # they share -- on 2026-09-15 three scheduled runs in a row failed "database
    # is locked" while another task held the database. Waiting its turn costs
    # seconds; failing costs everything until the next scheduled run. The
    # with-block also releases on the sys.exit calls inside the body.
    #
    # The retry wrapper handles the blockage the mutex CANNOT: a lock held by a
    # program outside this tool (see db_contention). This import normally
    # finishes in seconds, so a run that fails is almost always one that met a
    # neighbour, and without a retry the whole interval until the next
    # scheduled run was lost (a whole day, for a daily job).
    from .task_mutex import hold
    from .db_contention import DatabaseBusy, retry_while_locked

    def once():
        with hold(waiting_for="another database task"):
            return _import_brains_body(args, summary_dir)

    try:
        return retry_while_locked(once, what="The brain import",
                                  announce=lambda m: print(m, flush=True))
    except DatabaseBusy as busy:
        print("  [FAIL] %s" % busy, flush=True)
        return 1


def _import_brains_body(args, summary_dir):
    """The database part of cmd_import_brains (see there)."""
    import csv as csv_mod
    from .database import init_database
    from .importers import BrainGlobeImporter
    from .schema import Base, BrainSample, RegionCount, ElifeRegionCount

    db = init_database()
    # Ensure all tables exist (including new elife_region_counts)
    Base.metadata.create_all(db.engine)

    importer = BrainGlobeImporter(db)
    dry_run = args.dry_run
    prefix = "[DRY RUN] " if dry_run else ""

    if args.calibration:
        # Import calibration runs only
        cal_csv = Path(args.calibration)
        result = importer.import_calibration_runs(cal_csv, dry_run=dry_run)
        _print_import_result("Calibration runs", result)
        return

    if args.csv:
        # Import single per-brain CSV
        csv_path = Path(args.csv)
        brain_id = args.brain
        if not brain_id:
            brain_id = importer._extract_brain_from_path(csv_path)
        if not brain_id:
            print("Error: Could not determine brain ID. Use --brain to specify.")
            sys.exit(1)

        if args.update:
            _delete_brain_data(db, brain_id, dry_run)

        result = importer.import_region_counts(
            csv_path, brain_id=brain_id, is_final=True, dry_run=dry_run)
        _print_import_result(f"Region counts ({brain_id})", result)

        # Also import eLife counts. MouseBrain's aggregate (elife_counts.csv)
        # normally sits beside the per-brain CSV in 2_Data_Summary.
        if result['success'] and not dry_run:
            elife_csv = (summary_dir or csv_path.parent) / 'elife_counts.csv'
            _, elife_errors = _import_elife_from_csv(
                importer, db, csv_path, brain_id, dry_run, elife_csv)
            if elife_errors:
                sys.exit(1)
        return

    if args.all:
        # Import everything from summary directory
        if not summary_dir.exists():
            print(f"Error: Summary directory not found: {summary_dir}")
            sys.exit(1)

        print(f"{prefix}Importing all brain data from: {summary_dir}")
        print("=" * 60)

        # Step 1: Import calibration runs
        cal_csv = summary_dir / 'calibration_runs.csv'
        if cal_csv.exists():
            print(f"\n--- Calibration Runs ---")
            result = importer.import_calibration_runs(cal_csv, dry_run=dry_run)
            _print_import_result("Calibration runs", result)

        # Step 2: Import per-brain CSVs (long format)
        brain_csvs = sorted([
            f for f in summary_dir.glob('*_counts.csv')
            if f.name not in ('region_counts.csv', 'elife_counts.csv',
                              'region_counts_archive.csv', 'elife_counts_archive.csv')
        ])

        if not brain_csvs:
            print("\nNo per-brain CSV files found")
            return

        print(f"\n--- Region Counts ({len(brain_csvs)} brains) ---")
        total_regions = 0
        total_elife = 0
        elife_failed = []
        # MouseBrain's step 6 writes the eLife aggregate itself (one row per
        # brain); it is what the eLife import reads when mousebrain is not
        # importable in this environment.
        elife_csv = summary_dir / 'elife_counts.csv'

        for csv_path in brain_csvs:
            brain_id = importer._extract_brain_from_path(csv_path)
            if not brain_id:
                print(f"  Skipping {csv_path.name}: could not parse brain ID")
                continue

            if args.update:
                _delete_brain_data(db, brain_id, dry_run)

            result = importer.import_region_counts(
                csv_path, brain_id=brain_id, is_final=True, dry_run=dry_run)
            rc = result.get('imported', {}).get('region_counts', 0)
            total_regions += rc

            status = "[OK]" if result['success'] else "[FAIL]"
            errs = f" ({', '.join(result['errors'])})" if result['errors'] else ""
            print(f"  {brain_id}: {rc} region counts {status}{errs}")

            # Import eLife counts
            if result['success']:
                elife_count, elife_errors = _import_elife_from_csv(
                    importer, db, csv_path, brain_id, dry_run, elife_csv)
                total_elife += elife_count
                if elife_errors:
                    elife_failed.append(brain_id)

        # Summary
        print(f"\n{'=' * 60}")
        print(f"{prefix}Import Complete:")
        print(f"  Region counts: {total_regions}")
        print(f"  eLife group counts: {total_elife}")
        if elife_failed:
            print(f"  [FAIL] eLife group counts NOT imported for {len(elife_failed)} brain(s): "
                  f"{', '.join(elife_failed)} -- see the lines above")

        # Show table stats
        if not dry_run:
            with db.session() as session:
                print(f"\nDatabase table counts:")
                print(f"  brain_samples: {session.query(BrainSample).count()}")
                print(f"  region_counts: {session.query(RegionCount).count()}")
                print(f"  elife_region_counts: {session.query(ElifeRegionCount).count()}")
                from .schema import CalibrationRun
                print(f"  calibration_runs: {session.query(CalibrationRun).count()}")
        if elife_failed:
            sys.exit(1)
        return

    # No action specified
    print("Specify --all, --csv, or --calibration. Use --help for details.")
    sys.exit(1)


def _import_elife_from_csv(importer, db, csv_path, brain_id, dry_run, elife_csv=None):
    """Parse a per-brain CSV and import eLife grouped counts.

    Returns (count, errors). Every reason nothing was imported is PRINTED and
    returned -- the silent `return 0` paths this function used to have are
    how eLife counts went unimported for months without anyone noticing.
    ``elife_csv`` is MouseBrain's own aggregate (2_Data_Summary/elife_counts.csv),
    read when mousebrain is not importable here (see importers.import_elife_counts).
    """
    import csv as csv_mod
    from .schema import BrainSample

    region_counts_dict = {}
    hemisphere_counts = {}
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv_mod.DictReader(f)
        for row in reader:
            acr = row.get('region_acronym', '')
            if not acr:
                continue
            total = int(row.get('cell_count', 0) or 0)
            left = int(row.get('left_count', 0) or 0)
            right = int(row.get('right_count', 0) or 0)
            region_counts_dict[acr] = total
            if left > 0 or right > 0:
                hemisphere_counts[acr] = {'left': left, 'right': right}

    def _fail(msg):
        print(f"    [FAIL] eLife counts ({brain_id}): {msg}")
        return 0, [msg]

    if not region_counts_dict:
        return _fail(f"no region rows in {csv_path.name}")

    brain_info = importer.parse_brain_name(brain_id)
    if not brain_info:
        return _fail(f"brain id {brain_id!r} does not parse as BRAIN#_PROJECT_COHORT_SUBJECT_MAGx_zSTEP")

    with db.session() as session:
        bs = session.query(BrainSample).filter_by(
            subject_id=brain_info['subject_id'], brain_id=brain_id
        ).first()
        if not bs:
            return _fail("no brain_samples row (the region-count import should have created it)")

        result = importer.import_elife_counts(
            brain_sample_id=bs.id,
            region_counts_dict=region_counts_dict,
            hemisphere_counts=hemisphere_counts,
            is_final=True,
            source_file=str(csv_path),
            dry_run=dry_run,
            brain_id=brain_id,
            elife_summary_csv=elife_csv,
        )
        for err in result.get('errors', []):
            print(f"    [FAIL] eLife counts ({brain_id}): {err}")
        for warn in result.get('warnings', []):
            print(f"    [!] eLife counts ({brain_id}): {warn}")
        return (result.get('imported', {}).get('elife_region_counts', 0),
                list(result.get('errors', [])))


def _delete_brain_data(db, brain_id, dry_run):
    """Delete existing region/eLife counts for a brain (for re-import)."""
    from .schema import BrainSample, RegionCount, ElifeRegionCount

    with db.session() as session:
        bs = session.query(BrainSample).filter_by(brain_id=brain_id).first()
        if not bs:
            return

        prefix = "[DRY RUN] " if dry_run else ""
        rc_count = session.query(RegionCount).filter_by(brain_sample_id=bs.id).count()
        ec_count = session.query(ElifeRegionCount).filter_by(brain_sample_id=bs.id).count()

        if rc_count > 0 or ec_count > 0:
            print(f"  {prefix}Deleting existing data for {brain_id}: "
                  f"{rc_count} region counts, {ec_count} eLife counts")
            if not dry_run:
                session.query(RegionCount).filter_by(brain_sample_id=bs.id).delete()
                session.query(ElifeRegionCount).filter_by(brain_sample_id=bs.id).delete()
                session.commit()


def _print_import_result(label, result):
    """Print formatted import result."""
    status = "[OK]" if result['success'] else "[FAIL]"
    print(f"  {label}: {status}")
    for key, val in result.get('imported', {}).items():
        if val > 0:
            print(f"    {key}: {val}")
    for err in result.get('errors', []):
        print(f"    Error: {err}")
    for warn in result.get('warnings', [])[:5]:
        print(f"    Warning: {warn}")
    remaining = len(result.get('warnings', [])) - 5
    if remaining > 0:
        print(f"    ... and {remaining} more warnings")


def cmd_import_reaches(args):
    """Pull MouseReach features files into reach_data (see mousedb.import_reaches)."""
    from .import_reaches import main as _main
    argv = []
    if args.all: argv.append("--all")
    if args.dry_run: argv.append("--dry-run")
    if args.include_processing: argv.append("--include-processing")
    if args.force: argv.append("--force")
    if args.limit: argv += ["--limit", str(args.limit)]
    if getattr(args, "pellets_only", False): argv.append("--pellets-only")
    raise SystemExit(_main(argv))


def cmd_update(args):
    """Bring the database current: every pull, in order, under one lock (see mousedb.update)."""
    from .update import run_update, format_summary
    res = run_update(triggered_by="cli", skip=tuple(args.skip or ()))
    print()
    print(format_summary(res))
    raise SystemExit(0 if res.ok else 1)


def cmd_import_analyses(args):
    """Mirror MouseBrain's analysis registry into the mousedb folders (see mousedb.import_analyses)."""
    from .import_analyses import main as _main
    argv = []
    if args.all: argv.append("--all")
    if args.dry_run: argv.append("--dry-run")
    if args.limit: argv += ["--limit", str(args.limit)]
    raise SystemExit(_main(argv))


def cmd_config(args):
    """mousedb config: the machine-specific locations (see mousedb.config)."""
    from . import config as cfg
    if args.set:
        key, value = args.set
        path = cfg.set_value(key, value)
        print("[OK] %s = %s  (saved to %s)" % (key, value, path))
    if args.unset:
        path = cfg.set_value(args.unset, None)
        print("[OK] %s removed  (saved to %s)" % (args.unset, path))
    if args.show or not (args.set or args.unset):
        print(cfg.describe())


def cmd_study_facts(args):
    """mousedb study-facts: study-wide ODC facts per project (see mousedb.study_facts)."""
    from . import study_facts as sf
    if args.set:
        project, field, value = args.set
        path = sf.set_fact(project, field, value)
        print("[OK] %s %s = %s  (saved to %s)" % (project, field, value, path))
    if args.unset:
        project, field = args.unset
        path = sf.set_fact(project, field, None)
        print("[OK] %s %s removed  (saved to %s)" % (project, field, path))
    if args.show or not (args.set or args.unset):
        print(sf.describe(args.project))


def cmd_sheet_projects(args):
    """mousedb sheet-projects: what each project's tracking sheets are called
    (see mousedb.sheet_projects). Exits 1 on a refused setting."""
    from .sheet_sync import _ascii
    from . import sheet_projects as sp
    if args.set:
        project, name = args.set
        try:
            path = sp.set_project(project, name, template=args.template)
        except ValueError as e:
            print(_ascii("[FAIL] %s -- nothing was changed" % e))
            raise SystemExit(1)
        print(_ascii("[OK] %s sheets are named %s  (saved to %s)" % (project.upper(), name, path)))
    try:
        print(_ascii("Lab settings file: %s" % sp.config_path()))
        print(_ascii("Sheet names by project: %s" % sp.describe()))
        for p in sp.definitions().values():
            if p.template:
                print(_ascii("  %s new sheets are made from %s%s" % (
                    p.project, p.template, "" if p.template.is_file() else "  [!] FILE NOT FOUND")))
    except ValueError as e:
        print(_ascii("[FAIL] %s" % e))
        raise SystemExit(1)


def cmd_sheet_template(args):
    """mousedb sheet-template make PROJECT REFERENCE --cohort ID: make a
    project's template from one of its cohort workbooks (see
    mousedb.sheet_templates). Prints the report. Exits 1 if refused."""
    from .sheet_sync import _ascii
    from . import sheet_templates as st
    keep = {}
    for item in args.keep or []:
        tab, _, header = item.partition(":")
        if not tab or not header:
            print(_ascii("[FAIL] --keep takes TAB:HEADER, for example \"4_Surgery:Anesthetic\" "
                         "(got %r) -- nothing was made" % item))
            raise SystemExit(1)
        keep.setdefault(tab, []).append(header)
    try:
        out, rep = st.make_project_template(args.project, args.reference, args.cohort, keep)
    except (ValueError, OSError) as e:
        print(_ascii("[FAIL] %s -- nothing was made" % e))
        raise SystemExit(1)
    print(_ascii(rep.text()))
    print(_ascii("[OK] template written: %s" % out))
    print(_ascii("     report beside it; open the template in Excel to check it. New %s sheets "
                 "are made from it from now on." % args.project.upper()))


def cmd_new_sheet(args):
    """mousedb new-sheet COHORT --animals LIST: a new cohort's tracking
    workbook, made from its project's template, in the sheets folder. A refusal
    prints a "REFUSED:" line on stderr and exits with make_sheets.REFUSED_EXIT
    (the Tracking Sheets tab tells refused from failed by that)."""
    import sys as _sys
    from .sheet_sync import _ascii
    from . import sheet_templates as st
    from .cohort_tools.make_sheets import NewSheetRefused, REFUSED_EXIT
    folder = args.output_dir
    if not folder:
        from .cohort_sheets import cohort_sheets_dir
        folder = cohort_sheets_dir()
    if not folder:
        print("REFUSED: no sheets folder is set (mousedb-sheets set-dir <folder>), and no "
              "--output-dir was given.", file=_sys.stderr)
        raise SystemExit(REFUSED_EXIT)
    try:
        target, problems = st.build_sheet(args.cohort, args.animals, folder)
    except NewSheetRefused as e:
        print(_ascii("REFUSED: %s" % e), file=_sys.stderr)
        print("Nothing was written.", file=_sys.stderr)
        raise SystemExit(REFUSED_EXIT)
    print(_ascii("[OK] created %s" % target))
    for line in problems:
        print(_ascii("  [!] %s" % line))


def cmd_grants(args):
    """mousedb grants: which projects each grant holds (mousedb.grants). A
    grant's projects sit inside its folder in the exports. Exits 1 on a refused
    setting."""
    from .sheet_sync import _ascii
    from . import grants as g
    if args.set:
        grant, projects = args.set[0], args.set[1:]
        try:
            path = g.set_grant(grant, projects)
        except ValueError as e:
            print(_ascii("[FAIL] %s -- nothing was changed" % e))
            raise SystemExit(1)
        print(_ascii("[OK] grant %s: %s  (saved to %s)" % (
            grant, ", ".join(p.upper() for p in projects) or "removed", path)))
    try:
        known = g.grants()
    except ValueError as e:
        print(_ascii("[FAIL] %s" % e))
        raise SystemExit(1)
    if not known:
        print("No grants set: every project's exports are in their own top-level folder. "
              "Set one with: " + g.SET_COMMAND)
    for grant, projects in known.items():
        print(_ascii("%s: %s" % (grant, ", ".join(projects))))



def cmd_export_example(args):
    """mousedb export-example (see mousedb.exporters.example_export).

    The loop this serves: look at the file, say what is wrong, change it, look
    again. So it prints what it wrote, how long each part took, and -- unless
    asked not to -- a few random rows, because the next thing anyone does after
    building it is look at some rows.
    """
    from .exporters import example_export as ex

    result = ex.build(cohort=args.cohort, target_rows=args.rows,
                      out_dir=args.out_dir, use_cache=not args.no_cache)
    if not result["files"]:
        print("%s: %s" % (result["cohort"], result.get("reason", "nothing written")))
        return 1
    print("EXAMPLE %s -- %d sessions sampled" % (result["cohort"],
                                                 result["sessions_sampled"]))
    for entry in result["files"]:
        if "left_alone" in entry:
            print("  [!] %-44s %s -- close it and run this again"
                  % (entry["file"], entry["left_alone"]))
        else:
            print("  %-44s %6d rows %4d cols"
                  % (entry["file"], entry["rows"], entry["columns"]))
    s = result["seconds"]
    print("")
    print("load %.1fs  build %.1fs  write %.1fs  TOTAL %.1fs"
          % (s["load"], s["build"], s["write"], s["total"]))
    print(result["out_dir"])
    if args.sample:
        print()
        print(ex.sample(cohort=result["cohort"], rows=args.sample,
                        out_dir=args.out_dir))
    return 0


def cmd_export_odc_reaches(args):
    """mousedb export-odc-reaches (see mousedb.exporters.odc_reaches)."""
    from .exporters.odc_reaches import main as _main
    argv = []
    for c in args.cohort or []:
        argv += ['--cohort', c]
    if args.out_dir:
        argv += ['--out-dir', args.out_dir]
    raise SystemExit(_main(argv))


from .config import ConfigError


def main():
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        prog='mousedb',
        description='MouseDB - Mouse Colony and Experiment Data Management'
    )
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # mousedb-status
    status_parser = subparsers.add_parser('status', help='Show database status')
    status_parser.set_defaults(func=cmd_status)

    # mousedb config -- where things are on THIS machine (the only place lab paths live)
    config_parser = subparsers.add_parser(
        'config', help='Show or set where this machine keeps the database, snapshot and pipelines')
    config_parser.add_argument('--show', action='store_true', help='Print every key, value and source')
    config_parser.add_argument('--set', nargs=2, metavar=('KEY', 'VALUE'), help='Set one key')
    config_parser.add_argument('--unset', metavar='KEY', help='Remove one key')
    config_parser.set_defaults(func=cmd_config)

    # mousedb study-facts -- study-wide ODC facts per project (strain, supplier,
    # study leader, injury device): one lab's facts, so kept on the machine
    study_parser = subparsers.add_parser(
        'study-facts',
        help='Show or set study-wide ODC facts (strain, supplier, study leader, injury device) per project')
    study_parser.add_argument('--show', action='store_true', help='Print every project and fact')
    study_parser.add_argument('--project', help='With --show: only this project')
    study_parser.add_argument('--set', nargs=3, metavar=('PROJECT', 'FIELD', 'VALUE'),
                              help="Set one fact (PROJECT may be 'default' for every project)")
    study_parser.add_argument('--unset', nargs=2, metavar=('PROJECT', 'FIELD'), help='Remove one fact')
    study_parser.set_defaults(func=cmd_study_facts)

    # mousedb sheet-projects -- each project's tracking-sheet file name, in the
    # lab's settings file on the shared storage (one file for every machine)
    sp_parser = subparsers.add_parser(
        'sheet-projects',
        help="Show or set each project's tracking-sheet file name (e.g. "
             "PROJA -> ProjectA_{NN}_Animal_Tracking.xlsx)")
    sp_parser.add_argument('--show', action='store_true', help='Print every project (the default)')
    sp_parser.add_argument('--set', nargs=2, metavar=('PROJECT', 'SHEET_NAME'),
                           help="Set a project's sheet name; {NN} marks the cohort number")
    sp_parser.add_argument('--template', default=None,
                           help="With --set: the blank workbook new cohorts' sheets are made from, "
                                "relative to the settings file's folder ('' removes it)")
    sp_parser.set_defaults(func=cmd_sheet_projects)

    # mousedb sheet-template make -- a project's template (what its new
    # cohort sheets look like), made from one of its cohort workbooks
    st_parser = subparsers.add_parser(
        'sheet-template',
        help="Make a project's template for new cohort sheets from one of its cohort workbooks")
    st_sub = st_parser.add_subparsers(dest='action')
    st_make = st_sub.add_parser('make', help="Make (or remake) the template; the old one is kept")
    st_make.add_argument('project', help="Project code, e.g. PROJA")
    st_make.add_argument('reference', help="A cohort workbook of that project (only read)")
    st_make.add_argument('--cohort', required=True,
                         help="The cohort that workbook belongs to, e.g. PROJA_05")
    st_make.add_argument('--keep', action='append', metavar='TAB:HEADER',
                         help="Keep a column that is the same for every animal (repeatable)")
    st_make.set_defaults(func=cmd_sheet_template)

    # mousedb new-sheet -- a new cohort's workbook from its project's template
    ns_parser = subparsers.add_parser(
        'new-sheet', help="Make a new cohort's tracking workbook from its project's template")
    ns_parser.add_argument('cohort', help="The new cohort, e.g. PROJA_07")
    ns_parser.add_argument('--animals', required=True,
                           help="Subject numbers: 01-16, or 3,4,16,17,19")
    ns_parser.add_argument('--output-dir', default=None,
                           help="Folder to write into (default: the sheets folder)")
    ns_parser.set_defaults(func=cmd_new_sheet)

    # mousedb grants -- which projects each grant holds (the exports put a
    # grant's projects inside its folder)
    gr_parser = subparsers.add_parser(
        'grants', help="Show or set which projects each grant holds")
    gr_parser.add_argument('--show', action='store_true', help='Print every grant (the default)')
    gr_parser.add_argument('--set', nargs='+', metavar='NAME',
                           help="GRANT followed by its project codes; GRANT alone removes it")
    gr_parser.set_defaults(func=cmd_grants)

    # mousedb export-example -- one cohort, fast, for looking at while changing it
    ex_p = subparsers.add_parser(
        'export-example',
        help='Build ONE cohort of the analysis documents into exports/current/Example, '
             'from a sample of its sessions, in a few seconds -- for checking the format '
             'while changing it. Same code as the full export.')
    ex_p.add_argument('--cohort', default='CNT_01', help='Cohort id (default CNT_01)')
    ex_p.add_argument('--rows', type=int, default=5000,
                      help='About how many reach rows to include (default 5000). Whole '
                           'sessions are taken evenly through the cohort, so every phase '
                           'of the experiment is represented.')
    ex_p.add_argument('--sample', type=int, default=3, metavar='N',
                      help='Also print N random rows, one column per line (default 3; '
                           '0 for none)')
    ex_p.add_argument('--out-dir', help='Folder to write into (default: the Example folder '
                                       'inside the current exports folder)')
    ex_p.add_argument('--no-cache', action='store_true',
                      help="Re-read the cohort's rows from the snapshot instead of the "
                           "local cache. The cache is keyed to the snapshot's own "
                           "timestamp, so this is rarely needed.")
    ex_p.set_defaults(func=cmd_export_example)

    # mousedb export-odc-reaches -- per-reach ODC files (animal + session on every row)
    odc_r = subparsers.add_parser(
        'export-odc-reaches',
        help='Write ODC_reaches_<cohort>.csv (one row per reach, animal and session details '
             'repeated) plus a data dictionary, from the snapshot')
    odc_r.add_argument('--cohort', action='append', help='Cohort id (repeatable; default all)')
    odc_r.add_argument('--out-dir', help='Folder to write into (default: the current exports folder)')
    odc_r.set_defaults(func=cmd_export_odc_reaches)

    # mousedb import-reaches -- pull MouseReach's per-video results into reach_data
    ir = subparsers.add_parser('import-reaches',
                               help="Import MouseReach features files (new or changed) into reach_data")
    ir.add_argument('--all', action='store_true', help='Ignore the ledger; re-import every file')
    ir.add_argument('--dry-run', action='store_true', help='Count only; write nothing')
    ir.add_argument('--include-processing', action='store_true', help='Also scan the Processing folder')
    ir.add_argument('--force', action='store_true', help='Write even if a MouseReach watcher is running')
    ir.add_argument('--limit', type=int, help='At most this many files')
    ir.add_argument('--pellets-only', action='store_true',
                    help='Only (re)fill the per-pellet table from every features file')
    ir.set_defaults(func=cmd_import_reaches)

    # mousedb update -- the "Update the database" button as a command: sheets,
    # reaches, analyses, brains, then the snapshot, in order, under one lock.
    up = subparsers.add_parser('update',
                               help="Bring the database current: run every pull in order (sheets, "
                                    "reaches, analyses, brains, snapshot) under one lock and report")
    up.add_argument('--skip', action='append', choices=['sheets', 'reaches', 'analyses', 'brains', 'snapshot'],
                    help='Leave this step out (repeatable)')
    up.set_defaults(func=cmd_update)

    # mousedb import-analyses -- mirror MouseBrain's analysis registry (files only, no database)
    ia = subparsers.add_parser('import-analyses',
                               help="Mirror MouseBrain's analysis registry (exports, figures, logs, "
                                    "provenance) into the mousedb folders")
    ia.add_argument('--all', action='store_true', help='Ignore the ledger; re-check every file')
    ia.add_argument('--dry-run', action='store_true', help='Count only; write nothing')
    ia.add_argument('--limit', type=int, help='Copy at most this many files')
    ia.set_defaults(func=cmd_import_analyses)

    # mousedb-init
    init_parser = subparsers.add_parser('init', help='Initialize database')
    init_parser.set_defaults(func=cmd_init)

    # mousedb-new-cohort
    new_cohort_parser = subparsers.add_parser('new-cohort', help='Create new cohort')
    new_cohort_parser.add_argument('cohort', help='Cohort ID (e.g., CNT_05)')
    new_cohort_parser.add_argument('--start-date', required=True, help='Start date (YYYY-MM-DD)')
    new_cohort_parser.add_argument('--mice', type=int, default=16, help='Number of mice (default: 16)')
    new_cohort_parser.set_defaults(func=cmd_new_cohort)

    # mousedb-import
    import_parser = subparsers.add_parser(
        'import', help='Import tracking sheets (same import, ledger and exit code as mousedb-sheets import)')
    import_parser.add_argument(
        '--file', help="One cohort's workbook; replaces that cohort's animal records. "
                       "Refused if it sits in a snapshot/archive folder or is not "
                       "named as a sheet (a copy, a lock file, a tool's export)")
    import_parser.add_argument('--all', action='store_true',
                               help="Import every cohort's chosen sheet (pinned, else newest) from the folder")
    sheets_parser = subparsers.add_parser(
        'cohort-sheets',
        help="Show/set where this machine's cohort tracking sheets are")
    sheets_parser.add_argument('--set', metavar='PATH',
                               help='Record the folder holding the CNT cohort sheets')
    sheets_parser.add_argument('--set-aspa', metavar='PATH', dest='set_aspa',
                               help='Record the folder holding the ASPA animal sheets')
    sheets_parser.add_argument('--discover', action='store_true',
                               help='Look for it on this machine')
    sheets_parser.add_argument('--cohort', help='Also show one cohort\'s sheet')
    sheets_parser.set_defaults(func=cmd_cohort_sheets)

    import_parser.add_argument('--directory', default=None,
                               help='Directory containing Excel files')
    import_parser.add_argument('--dry-run', action='store_true', help='Validate without importing')
    import_parser.set_defaults(func=cmd_import)

    # mousedb-export
    export_parser = subparsers.add_parser('export', help='Export data')
    export_parser.add_argument('--cohort', help='Cohort to export')
    export_parser.add_argument('--unified', action='store_true', help='Export unified reaches parquet')
    export_parser.add_argument('--odc', action='store_true', help='Export in ODC format (calculated stats)')
    export_parser.add_argument('--all-formats', action='store_true', help='Export all formats')
    export_parser.add_argument(
        '--output', '-o',
        help="Output path (file or directory). Refused inside a sheets folder or under a "
             "tracking sheet's name (a project's sheet name, 'mousedb sheet-projects'): an export must "
             "never replace a sheet")
    export_parser.set_defaults(func=cmd_export)

    # mousedb-entry
    entry_parser = subparsers.add_parser('entry', help='Launch data entry GUI')
    entry_parser.set_defaults(func=cmd_entry)

    # mousedb-browse
    browse_parser = subparsers.add_parser('browse', help='Browse database tables')
    browse_parser.add_argument('table', nargs='?', help='Table name to browse')
    browse_parser.add_argument('--list', '-l', action='store_true', help='List all tables')
    browse_parser.add_argument('--limit', '-n', type=int, default=20, help='Number of rows to show (default: 20)')
    browse_parser.add_argument('--filter', '-f', help='Filter by column=value (e.g., cohort_id=CNT_01)')
    browse_parser.set_defaults(func=cmd_browse)

    # mousedb-check
    check_parser = subparsers.add_parser('check', help='Run data completeness diagnostics')
    check_parser.add_argument('--cohort', '-c', help='Check specific cohort (e.g., CNT_04)')
    check_parser.add_argument('--verbose', '-v', action='store_true', help='Show INFO-level findings too')
    check_parser.add_argument('--json', '-j', action='store_true', help='Output as JSON')
    check_parser.set_defaults(func=cmd_check)

    # mousedb-dump
    dump_parser = subparsers.add_parser('dump', help='Dump database tables to CSV files')
    dump_parser.add_argument('table', nargs='?', help='Specific table to dump (default: all tables)')
    dump_parser.add_argument('--output', '-o', help='Output directory (default: <mousedb_root>/database_dump)')
    dump_parser.set_defaults(func=cmd_dump)

    # mousedb-video-status
    video_parser = subparsers.add_parser('video-status',
        help='Show video pipeline processing status')
    video_parser.add_argument('--by-animal', '-a', action='store_true',
        help='Show per-animal breakdown')
    video_parser.add_argument('--cohort', help='Filter to specific cohort (e.g., CNT_01)')
    video_parser.add_argument('--show-errors', '-e', action='store_true',
        help='Show failed/quarantined video details')
    video_parser.add_argument('--json', '-j', action='store_true',
        help='Output as JSON')
    video_parser.set_defaults(func=cmd_video_status)

    # mousedb-archive-status
    archive_parser = subparsers.add_parser('archive-status',
        help='Show archive processing and version compliance status')
    archive_parser.add_argument('--cohort', '-c',
        help='Filter to specific cohort (e.g., ENCR_01)')
    archive_parser.add_argument('--verbose', '-v', action='store_true',
        help='Show per-animal breakdown')
    archive_parser.add_argument('--json', '-j', action='store_true',
        help='Output as JSON')
    archive_parser.set_defaults(func=cmd_archive_status)

    # mousedb backfill-phases
    backfill_parser = subparsers.add_parser('backfill-phases',
        help='Derive test_phase and phase_group from cohort testing structure')
    backfill_parser.add_argument('--dry-run', action='store_true',
        help='Report what would change without modifying the database')
    backfill_parser.set_defaults(func=cmd_backfill_phases)

    # mousedb hand-scores
    hs_parser = subparsers.add_parser('hand-scores',
        help='Write every hand pellet score to exports/hand_scores/ for the reaching '
             'pipeline (also done after every sheet import)')
    hs_parser.set_defaults(func=cmd_hand_scores)

    # mousedb import-brains
    brain_parser = subparsers.add_parser('import-brains',
        help='Import BrainGlobe brain data (region counts, calibration runs, eLife groups)')
    brain_parser.add_argument('--csv', help='Single per-brain counts CSV file')
    brain_parser.add_argument('--brain', '-b', help='Brain ID (if not in filename)')
    brain_parser.add_argument('--calibration', help='Path to calibration_runs.csv')
    brain_parser.add_argument('--all', '-a', action='store_true',
        help="Import everything in MouseBrain's 2_Data_Summary folder (calibration runs, "
             "per-brain region counts, eLife group counts)")
    brain_parser.add_argument('--summary-dir',
        help="Path to MouseBrain's 2_Data_Summary folder. Default: "
             "<mousebrain_pipeline_root>/3D_Cleared/2_Data_Summary, from "
             "`mousedb config --set mousebrain_pipeline_root <path>`")
    brain_parser.add_argument('--update', action='store_true',
        help='Delete existing counts for brain(s) before re-importing')
    brain_parser.add_argument('--dry-run', action='store_true',
        help='Validate without writing to database')
    brain_parser.set_defaults(func=cmd_import_brains)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
    except ConfigError as e:  # WHY: the message already names the exact fix; a traceback hides it
        print("[FAIL] %s" % e)
        raise SystemExit(1)


# Entry points for pyproject.toml
def mousedb_status():
    sys.argv = ['mousedb', 'status'] + sys.argv[1:]
    main()

def mousedb_init():
    sys.argv = ['mousedb', 'init'] + sys.argv[1:]
    main()

def mousedb_new_cohort():
    sys.argv = ['mousedb', 'new-cohort'] + sys.argv[1:]
    main()

def mousedb_import():
    sys.argv = ['mousedb', 'import'] + sys.argv[1:]
    main()

def mousedb_import_brains():
    sys.argv = ['mousedb', 'import-brains'] + sys.argv[1:]
    main()

def mousedb_export():
    sys.argv = ['mousedb', 'export'] + sys.argv[1:]
    main()

def mousedb_entry():
    sys.argv = ['mousedb', 'entry'] + sys.argv[1:]
    main()

def mousedb_browse():
    sys.argv = ['mousedb', 'browse'] + sys.argv[1:]
    main()


def mousedb_check():
    sys.argv = ['mousedb', 'check'] + sys.argv[1:]
    main()


def mousedb_dump():
    sys.argv = ['mousedb', 'dump'] + sys.argv[1:]
    main()


def mousedb_video_status():
    sys.argv = ['mousedb', 'video-status'] + sys.argv[1:]
    main()


def mousedb_archive_status():
    sys.argv = ['mousedb', 'archive-status'] + sys.argv[1:]
    main()


if __name__ == '__main__':
    main()
