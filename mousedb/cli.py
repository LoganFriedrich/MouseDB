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
# mousedb figures commands
# =========================================================================

def cmd_figures_status(args):
    """Show figure registry status: total figures, staleness, recent activity."""
    from .registry import FigureRegistry

    registry = FigureRegistry()
    report = registry.status_report()

    print("\n=== Figure Registry Status ===")
    print(f"  Total registered : {report['total_records']}")
    print(f"  Current          : {report['current_figures']}")
    print(f"  Archived         : {report['archived_figures']}")
    print(f"  Stale            : {report['stale_figures']}")

    if report.get('by_category'):
        print("\n  By category:")
        for cat, count in sorted(report['by_category'].items()):
            print(f"    {cat:20s} {count}")

    if report.get('stale_details'):
        print("\n  Stale figures (source data changed since generation):")
        for detail in report['stale_details']:
            reasons = ', '.join(s['reason'] for s in detail.get('stale_sources', []))
            print(f"    {detail['accession']}  {detail.get('recipe_name', ''):30s}  "
                  f"{reasons}")

    print()


def cmd_figures_list(args):
    """List registered figures, optionally filtered by category."""
    from .registry.models import FigureRecord
    from .database import get_db

    db = get_db()
    current_only = not getattr(args, 'all', False)
    category = getattr(args, 'category', None)

    with db.session() as session:
        q = session.query(FigureRecord)
        if current_only:
            q = q.filter(FigureRecord.is_current == 1)
        if category:
            q = q.filter(FigureRecord.category == category)
        records = q.order_by(FigureRecord.generated_at.desc()).all()

        if not records:
            print("\n  No figures found.")
            if category:
                print(f"  (filtered to category={category})")
            return

        print(f"\n=== Registered Figures ({len(records)}) ===\n")
        print(f"  {'Accession':<22} {'Recipe':<30} {'Category':<14} "
              f"{'Theme':<8} {'Current':<9} {'Generated'}")
        print(f"  {'-' * 105}")

        for r in records:
            current_str = "yes" if r.is_current else "no"
            gen_at = str(r.generated_at)[:16] if r.generated_at else "unknown"
            print(f"  {(r.accession or ''):22s} {(r.recipe_name or ''):30s} "
                  f"{(r.category or ''):14s} {(r.theme or ''):8s} "
                  f"{current_str:<9} {gen_at}")

        print()


def cmd_figures_show(args):
    """Show full details for a figure by accession number."""
    from .registry.models import FigureRecord
    from .database import get_db

    accession = args.accession.upper()
    db = get_db()

    with db.session() as session:
        record = session.query(FigureRecord).filter(
            FigureRecord.accession == accession
        ).first()

        if not record:
            print(f"\n  No figure found with accession: {accession}")
            return

        print(f"\n=== {record.accession} ===")
        print(f"  Title         : {record.title}")
        print(f"  Recipe        : {record.recipe_name}")
        print(f"  Category      : {record.category}")
        print(f"  Theme         : {record.theme}")
        print(f"  Mode          : {record.mode}")
        print(f"  File          : {record.file_path}")
        print(f"  Sidecar       : {record.sidecar_path}")
        print(f"  DPI           : {record.dpi}")
        print(f"  Size          : {record.width_px}x{record.height_px} px")
        print(f"  Current       : {'yes' if record.is_current else 'no (archived)'}")
        print(f"  Generated at  : {record.generated_at}")
        print(f"  Generated by  : {record.generated_by}")
        print(f"  Generation    : {record.generation_ms} ms")
        print(f"  Method hash   : {record.method_hash}")

        # Data sources (eager-loaded via relationship)
        if record.data_sources:
            print(f"\n  Data Sources ({len(record.data_sources)}):")
            for s in record.data_sources:
                print(f"    [{s.source_type}] {s.source_path}")
                if s.query_filter:
                    print(f"           filter: {s.query_filter}")
                if s.source_hash:
                    print(f"           hash: {s.source_hash[:16]}...")
                if s.record_count:
                    print(f"           records: {s.record_count}")

        # Tool versions
        if record.tool_versions:
            print(f"\n  Tool Versions ({len(record.tool_versions)}):")
            for t in record.tool_versions:
                print(f"    {t.tool_name:20s} {t.tool_version}")

        # Parameters
        if record.parameters:
            print(f"\n  Parameters ({len(record.parameters)}):")
            for p in record.parameters:
                print(f"    {p.param_key:30s} = {p.param_value}")

        print()


def cmd_figures_regenerate(args):
    """Regenerate stale or all figures from recipes."""
    from .registry import FigureRegistry
    from .recipes import FigureRecipe

    # Discover all recipe classes
    recipe_classes = _discover_recipes()

    if not recipe_classes:
        print("\n  No recipes found.")
        return

    recipe_name = getattr(args, 'recipe', None)
    regen_all = getattr(args, 'all', False)
    theme = getattr(args, 'theme', 'light')

    if recipe_name:
        # Regenerate specific recipe
        if recipe_name not in recipe_classes:
            print(f"\n  Unknown recipe: {recipe_name}")
            print(f"  Available: {', '.join(sorted(recipe_classes.keys()))}")
            return
        targets = {recipe_name: recipe_classes[recipe_name]}
    elif regen_all:
        targets = recipe_classes
    else:
        # Only stale figures
        registry = FigureRegistry()
        stale = registry.check_staleness()
        stale_names = {r['recipe_name'] for r in stale}
        targets = {
            name: cls for name, cls in recipe_classes.items()
            if name in stale_names
        }
        if not targets:
            print("\n  All figures are current. Nothing to regenerate.")
            print("  Use --all to regenerate everything anyway.")
            return

    print(f"\n=== Regenerating {len(targets)} recipe(s) (theme={theme}) ===\n")

    results = []
    for name, cls in sorted(targets.items()):
        try:
            recipe = cls()
            result = recipe.generate(theme=theme)
            results.append((name, result['path'], 'OK'))
        except Exception as e:
            results.append((name, None, str(e)))
            print(f"  [FAIL] {name}: {e}")

    print(f"\n=== Regeneration Summary ===")
    ok = sum(1 for _, _, s in results if s == 'OK')
    fail = len(results) - ok
    print(f"  Success: {ok}  Failed: {fail}")
    for name, path, status in results:
        marker = "[OK]  " if status == 'OK' else "[FAIL]"
        print(f"  {marker} {name:30s} {path or status}")
    print()


def cmd_figures_audit(args):
    """Run compliance audit on all figure scripts and PNGs."""
    from .figures.audit import run_audit

    report = run_audit(verbose=getattr(args, 'verbose', False))

    if getattr(args, 'json', False):
        import json
        print(json.dumps(report, indent=2, default=str))


def cmd_figures_backfill(args):
    """Register untracked PNGs with sidecars into FigureRegistry."""
    from .figures.audit import backfill_registry

    backfill_registry(dry_run=getattr(args, 'dry_run', False))


def _discover_recipes():
    """Find all FigureRecipe subclasses in mousedb.recipes."""
    from .recipes.base import FigureRecipe
    recipe_classes = {}

    # Import known recipe modules (each import registers subclasses)
    _recipe_modules = [
        'behavior', 'kinematics', 'tissue', 'recovery',
        'kinematic_recovery', 'kinematic_stratified', 'lab_overview',
    ]
    for _mod in _recipe_modules:
        try:
            __import__(f'mousedb.recipes.{_mod}')
        except ImportError:
            pass

    # Collect all subclasses
    for cls in FigureRecipe.__subclasses__():
        if cls.name:
            recipe_classes[cls.name] = cls

    return recipe_classes


# Default summary directory for BrainGlobe data
_DEFAULT_BRAIN_SUMMARY_DIR = Path(
    r"Y:\2_Connectome\Tissue\MouseBrain_Pipeline\3D_Cleared\2_Data_Summary"
)


def cmd_backfill_phases(args):
    """Backfill test_phase and phase_group across pellet_scores and reach_data."""
    from .backfill import backfill_phases, print_stats

    stats = backfill_phases(dry_run=args.dry_run)
    print_stats(stats)


def cmd_import_brains(args):
    """Import BrainGlobe brain data into connectome.db."""
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

    summary_dir = Path(args.summary_dir) if args.summary_dir else _DEFAULT_BRAIN_SUMMARY_DIR

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

        # Also import eLife counts
        if result['success'] and not dry_run:
            _import_elife_from_csv(importer, db, csv_path, brain_id, dry_run)
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
                elife_count = _import_elife_from_csv(
                    importer, db, csv_path, brain_id, dry_run)
                total_elife += elife_count

        # Summary
        print(f"\n{'=' * 60}")
        print(f"{prefix}Import Complete:")
        print(f"  Region counts: {total_regions}")
        print(f"  eLife group counts: {total_elife}")

        # Show table stats
        if not dry_run:
            with db.session() as session:
                print(f"\nDatabase table counts:")
                print(f"  brain_samples: {session.query(BrainSample).count()}")
                print(f"  region_counts: {session.query(RegionCount).count()}")
                print(f"  elife_region_counts: {session.query(ElifeRegionCount).count()}")
                from .schema import CalibrationRun
                print(f"  calibration_runs: {session.query(CalibrationRun).count()}")
        return

    # No action specified
    print("Specify --all, --csv, or --calibration. Use --help for details.")
    sys.exit(1)


def _import_elife_from_csv(importer, db, csv_path, brain_id, dry_run):
    """Parse a per-brain CSV and import eLife grouped counts. Returns count."""
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

    if not region_counts_dict:
        return 0

    brain_info = importer.parse_brain_name(brain_id)
    if not brain_info:
        return 0

    with db.session() as session:
        bs = session.query(BrainSample).filter_by(
            subject_id=brain_info['subject_id'], brain_id=brain_id
        ).first()
        if not bs:
            return 0

        result = importer.import_elife_counts(
            brain_sample_id=bs.id,
            region_counts_dict=region_counts_dict,
            hemisphere_counts=hemisphere_counts,
            is_final=True,
            source_file=str(csv_path),
            dry_run=dry_run,
        )
        return result.get('imported', {}).get('elife_region_counts', 0)


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

    # mousedb figures
    figures_parser = subparsers.add_parser('figures',
        help='Figure registry: status, list, show, regenerate')
    figures_parser.set_defaults(func=lambda a: figures_parser.print_help())
    figures_sub = figures_parser.add_subparsers(dest='figures_command',
        help='Figure registry commands')

    # mousedb figures status
    fig_status = figures_sub.add_parser('status',
        help='Show figure registry status and staleness')
    fig_status.set_defaults(func=cmd_figures_status)

    # mousedb figures list
    fig_list = figures_sub.add_parser('list',
        help='List registered figures')
    fig_list.add_argument('--category', '-c',
        help='Filter by category (behavior, tissue, grant, lab_meeting)')
    fig_list.add_argument('--all', '-a', action='store_true',
        help='Include archived (non-current) figures')
    fig_list.set_defaults(func=cmd_figures_list)

    # mousedb figures show
    fig_show = figures_sub.add_parser('show',
        help='Show details for a figure by accession')
    fig_show.add_argument('accession',
        help='Figure accession number (e.g., FIG-20260304-0001)')
    fig_show.set_defaults(func=cmd_figures_show)

    # mousedb figures regenerate
    fig_regen = figures_sub.add_parser('regenerate',
        help='Regenerate stale or all figures')
    fig_regen.add_argument('--recipe', '-r',
        help='Regenerate specific recipe only')
    fig_regen.add_argument('--all', '-a', action='store_true',
        help='Regenerate all recipes (not just stale)')
    fig_regen.add_argument('--theme', '-t', default='light',
        help='Theme: light, dark, print (default: light)')
    fig_regen.set_defaults(func=cmd_figures_regenerate)

    # mousedb figures audit
    fig_audit = figures_sub.add_parser('audit',
        help='Audit all figures for compliance with rules and tracking')
    fig_audit.add_argument('--verbose', '-v', action='store_true',
        help='Show detailed per-file results')
    fig_audit.add_argument('--json', '-j', action='store_true',
        help='Output as JSON')
    fig_audit.set_defaults(func=cmd_figures_audit)

    # mousedb figures backfill
    fig_backfill = figures_sub.add_parser('backfill',
        help='Register untracked PNGs with sidecars into FigureRegistry')
    fig_backfill.add_argument('--dry-run', action='store_true',
        help='Show what would be registered without doing it')
    fig_backfill.set_defaults(func=cmd_figures_backfill)

    # mousedb backfill-phases
    backfill_parser = subparsers.add_parser('backfill-phases',
        help='Derive test_phase and phase_group from cohort testing structure')
    backfill_parser.add_argument('--dry-run', action='store_true',
        help='Report what would change without modifying the database')
    backfill_parser.set_defaults(func=cmd_backfill_phases)

    # mousedb import-brains
    brain_parser = subparsers.add_parser('import-brains',
        help='Import BrainGlobe brain data (region counts, calibration runs, eLife groups)')
    brain_parser.add_argument('--csv', help='Single per-brain counts CSV file')
    brain_parser.add_argument('--brain', '-b', help='Brain ID (if not in filename)')
    brain_parser.add_argument('--calibration', help='Path to calibration_runs.csv')
    brain_parser.add_argument('--all', '-a', action='store_true',
        help='Import all data from default 2_Data_Summary directory')
    brain_parser.add_argument('--summary-dir',
        help='Path to 2_Data_Summary directory (overrides default)')
    brain_parser.add_argument('--update', action='store_true',
        help='Delete existing counts for brain(s) before re-importing')
    brain_parser.add_argument('--dry-run', action='store_true',
        help='Validate without writing to database')
    brain_parser.set_defaults(func=cmd_import_brains)

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
