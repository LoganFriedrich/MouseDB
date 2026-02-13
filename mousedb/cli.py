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


def cmd_import(args):
    """Import Excel tracking sheets."""
    from .database import init_database
    from .importers import ExcelImporter, import_all_cohorts

    init_database()

    if args.all:
        # Import all cohorts from directory
        cohorts_dir = Path(args.directory)
        if not cohorts_dir.exists():
            print(f"Error: Directory not found: {cohorts_dir}")
            sys.exit(1)
        results = import_all_cohorts(cohorts_dir, dry_run=args.dry_run)
    else:
        # Import single file
        excel_path = Path(args.file)
        if not excel_path.exists():
            print(f"Error: File not found: {excel_path}")
            sys.exit(1)
        importer = ExcelImporter()
        result = importer.import_cohort_file(excel_path, dry_run=args.dry_run)

        if result['errors']:
            print("\nErrors:")
            for error in result['errors']:
                print(f"  - {error}")

        if result['warnings']:
            print("\nWarnings:")
            for warning in result['warnings'][:10]:  # Limit warnings shown
                print(f"  - {warning}")
            if len(result['warnings']) > 10:
                print(f"  ... and {len(result['warnings']) - 10} more warnings")

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


def cmd_export(args):
    """Export data to Excel or Parquet."""
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
                from . import DEFAULT_DB_PATH
                full_report = CompletenessReport(
                    cohorts=[report_single],
                    db_path=str(DEFAULT_DB_PATH),
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
    output_dir = Path(args.output) if args.output else Path("Y:/2_Connectome/Databases/database_dump")
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


def main():
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        prog='mousedb',
        description='MouseDB - Data Management for Connectomics Grant'
    )
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # mousedb-status
    status_parser = subparsers.add_parser('status', help='Show database status')
    status_parser.set_defaults(func=cmd_status)

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
    import_parser = subparsers.add_parser('import', help='Import Excel files')
    import_parser.add_argument('--file', help='Single Excel file to import')
    import_parser.add_argument('--all', action='store_true', help='Import all cohorts from directory')
    import_parser.add_argument('--directory', default='Y:/2_Connectome/Behavior/3_Connectome_Animal_Cohorts',
                               help='Directory containing Excel files')
    import_parser.add_argument('--dry-run', action='store_true', help='Validate without importing')
    import_parser.set_defaults(func=cmd_import)

    # mousedb-export
    export_parser = subparsers.add_parser('export', help='Export data')
    export_parser.add_argument('--cohort', help='Cohort to export')
    export_parser.add_argument('--unified', action='store_true', help='Export unified reaches parquet')
    export_parser.add_argument('--odc', action='store_true', help='Export in ODC format (calculated stats)')
    export_parser.add_argument('--all-formats', action='store_true', help='Export all formats')
    export_parser.add_argument('--output', '-o', help='Output path (file or directory)')
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
    dump_parser.add_argument('--output', '-o', help='Output directory (default: Y:/2_Connectome/Databases/database_dump)')
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

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.func(args)


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


if __name__ == '__main__':
    main()
