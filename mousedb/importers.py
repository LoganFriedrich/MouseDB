"""
Excel importers for migrating existing cohort tracking sheets to SQLite.

Handles the actual Excel format with sheets:
- 3d_Weights: Transposed format (dates as columns, animals as rows)
- 3b_Manual_Tray: Pellet scores (20 pellets × 4 trays)
- 4_Contusion_Injury_Details: Surgery parameters (Actual_kd, etc.)
- 3a_Manual_Ramp: Ramp phase data (weight + tray before/after)
"""

import re
import json
import pandas as pd
from contextlib import contextmanager
from pathlib import Path
from datetime import date, datetime
from typing import List, Dict, Optional

# The rule for "did this surgery happen" lives in animal_records, the other
# reader of the same surgery tabs; the importer keeps no copy of it, so the two
# cannot store different surgeries for one sheet (see _performed_surgery).
from .animal_records import (
    HELD, PLANNED, PLANNED_SURGERY_GRACE_DAYS, SURGERY_PROOF_COLUMNS,
    SURGERY_WEIGHT_COLUMNS, planned_surgery_overdue, surgery_date,
    surgery_verdict, surgery_weight,
)
from .database import Database, get_db
from .phases import assign_phases_for_cohort
from .schema import (
    Project, Cohort, Subject, Weight, PelletScore, Surgery, RampEntry,
    LadderEntry, ArchivedSummary, VirusPrep,
    derive_cohort_id, validate_subject_id, TIMELINE,
    BrainSample, RegionCount, DetectedCell, CalibrationRun
)
from .validators import (
    validate_weight, validate_pellet_score, validate_sex,
    ValidationError
)


def _jsonable(value):
    """A stored or sheet value as plain JSON for the audit log and the ledger:
    dates as YYYY-MM-DD, numpy numbers as Python numbers, NaN as None."""
    if value is None:
        return None
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    if hasattr(value, 'item'):
        value = value.item()
    if isinstance(value, float) and value != value:
        return None
    return value


def _iso(value) -> Optional[str]:
    """A date as YYYY-MM-DD for reports and the ledger (None stays None)."""
    if value is None:
        return None
    return value.isoformat() if hasattr(value, 'isoformat') else str(value)


class _NotCommitted(Exception):
    """Raised inside an import's database session to leave it WITHOUT a commit.

    Database.session() commits whenever its block ends normally, so skipping
    the importer's own commit is not enough to keep a failed import out of the
    database; leaving the block by an exception is what makes it roll back.
    import_cohort_file catches it straight away -- it never reaches a caller."""


class ExcelImporter:
    """Import existing Excel tracking sheets into SQLite database."""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_db()
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.imported_counts: Dict[str, int] = {}
        # The day a planned surgery's date is compared with, to tell an overdue
        # plan from one that is merely ahead (see _note_planned_surgery). None
        # means the day the import runs; a test sets a fixed day. It is only
        # ever compared with -- never stored as any record's date.
        self.today: Optional[date] = None
        # (cohort, tab, date as typed) of each year typo already reported, so
        # a tab read by two passes reports it once (see _tab_date_repairs).
        self._reported_date_repairs = set()

    def import_cohort_file(self, excel_path: Path, dry_run: bool = False) -> Dict:
        """
        Import a single cohort Excel file.

        Args:
            excel_path: Path to the Excel file
            dry_run: If True, validate only without writing to database

        Returns:
            Dict with import statistics and any errors
        """
        self.errors = []
        self.warnings = []
        self._reset_run_state()
        self.imported_counts = {
            'subjects': 0,
            'weights': 0,
            'pellet_scores': 0,
            'surgeries': 0,
            'ramp_entries': 0,
            'virus_preps': 0,
            'ladder_entries': 0,
            'archived_summaries': 0,
            'planning_notes': 0,
        }

        excel_path = Path(excel_path)
        if not excel_path.exists():
            self.errors.append(f"File not found: {excel_path}")
            return self._get_result()

        print(f"{'[DRY RUN] ' if dry_run else ''}Importing: {excel_path.name}")

        # Read all sheets
        try:
            # readable_workbook reads a sheet someone has open in Excel, so
            # keeping it open does not stop the import (see file_holders).
            from .file_holders import readable_workbook
            xl = pd.ExcelFile(readable_workbook(excel_path))
            available_sheets = xl.sheet_names
        except PermissionError as e:
            # Rare now: a program holding the file so that nothing can read it.
            # Say which program, so a person knows what to close.
            from .file_holders import holders
            held = holders(excel_path)
            if held:
                self.errors.append(
                    "Could not read the workbook: it is open in %s on this computer. "
                    "Close it there (save first if you changed it); the next import "
                    "reads it." % ", ".join(held))
            else:
                self.errors.append(f"Failed to open Excel file: {e}")
            return self._get_result()
        except Exception as e:
            self.errors.append(f"Failed to open Excel file: {e}")
            return self._get_result()

        # Extract cohort info from filename
        cohort_id = self._extract_cohort_from_filename(excel_path.name)
        if not cohort_id:
            self.errors.append(f"Could not determine cohort from filename: {excel_path.name}")
            return self._get_result()

        print(f"  Detected cohort: {cohort_id}")

        # Phase refreshes the database runs when this import commits (its
        # phase hook: Database.phase_refreshes) -- read back below.
        refreshes = getattr(self.db, 'phase_refreshes', None)
        refreshes_before = len(refreshes) if isinstance(refreshes, list) else None

        with self._import_session(dry_run) as session:
            # Ensure cohort exists
            cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
            if (cohort and str(cohort.notes or '').lower().startswith('auto-created')
                    and not self._held_back(
                        "replacing %s's placeholder start date" % cohort_id)):
                # The pipeline created this cohort from a video before the
                # sheet was imported (videos arrive daily, sheets "eventually"),
                # with the video's date as a placeholder start. The sheet is
                # the authority on the start date and roster size: fill them in
                # now and drop the placeholder marker.
                start_date = self._detect_start_date(xl, available_sheets)
                if start_date:
                    if not dry_run:
                        cohort.start_date = start_date
                        # Both placeholder makers start their notes with
                        # "auto-created": a video seen before the sheet
                        # (import_reaches) and an earlier sheet import that
                        # found no start date (_ensure_cohort).
                        cohort.notes = (f"Imported from {excel_path.name} "
                                        f"(start date read from the sheet, replacing a "
                                        f"placeholder set when the record was first created)")
                        session.flush()
                    print(f"  Updated auto-created cohort {cohort_id}: start {start_date} (from sheet)")
            if not cohort:
                # Need to determine start date from the data
                start_date = self._detect_start_date(xl, available_sheets)
                if start_date:
                    project_code = cohort_id.split('_')[0]
                    cohort = Cohort(
                        cohort_id=cohort_id,
                        project_code=project_code,
                        start_date=start_date,
                        notes=f"Imported from {excel_path.name}"
                    )
                    if not dry_run:
                        session.add(cohort)
                        session.flush()
                    else:
                        # A dry run adds nothing, so later tabs would find no
                        # cohort and report a placeholder that a real import
                        # would never make. Remember that it would exist.
                        self.__dict__.setdefault('_dry_run_cohorts', set()).add(cohort_id)
                    print(f"  Created cohort: {cohort_id} (start: {start_date})")
                else:
                    self.warnings.append(
                        f"Could not determine a start date for cohort {cohort_id} from "
                        f"the sheet: no dated weigh-in on 3d/3e_Weights or "
                        f"3a_Manual_Ramp and no dated row on 3b_Manual_Tray")

            # Import each sheet type
            # Old format: 0a_Metadata, New format: subjects created from tray/weight data
            if '0a_Metadata' in available_sheets:
                self._import_metadata(xl, cohort_id, session, dry_run)

            # Old format: 1_Weight (row per day), New format: 3d_Weights (transposed)
            if '3d_Weights' in available_sheets:
                self._import_weights_transposed(xl, cohort_id, session, dry_run)
            elif '3e_Weights' in available_sheets:
                self._import_weights_transposed(xl, cohort_id, session, dry_run, sheet='3e_Weights')
            elif '1_Weight' in available_sheets:
                self._import_weights(xl, cohort_id, session, dry_run)

            # Pellet scores - same format in both
            # The grids above carry grams only; the percentage is on the tray tab.
            self._import_weight_percent_from_tray(xl, cohort_id, session, dry_run)

            if '3b_Manual_Tray' in available_sheets:
                self._import_pellet_scores(xl, cohort_id, session, dry_run)
            else:
                self._refuse_unread_tray_tab(session, cohort_id,
                                             "the workbook has no 3b_Manual_Tray tab")

            # Ramp data (food deprivation phase with tray weights)
            if '3a_Manual_Ramp' in available_sheets:
                self._import_ramp_data(xl, cohort_id, session, dry_run)

            # Surgery data - actual column names differ from expected
            if '4_Contusion_Injury_Details' in available_sheets:
                self._import_contusion_surgeries(xl, cohort_id, session, dry_run)

            if '5_SC_Injection_Details' in available_sheets:
                self._import_surgeries(xl, cohort_id, session, 'tracing', dry_run)

            # Virus preparation / injection calculations
            if '0_Injection_Calculations' in available_sheets:
                self._import_injection_calculations(xl, cohort_id, session, dry_run)

            # Ladder testing
            if '6_Ladder' in available_sheets:
                self._import_ladder(xl, cohort_id, session, dry_run)

            # Experiment planning (archived as cohort notes)
            if '1_Experiment_Planning' in available_sheets:
                self._import_experiment_planning(xl, cohort_id, session, dry_run)

            # Manual summaries (archived for validation)
            self._import_manual_summary(xl, cohort_id, session, dry_run,
                                         source_file=str(excel_path))

            # Stats (archived for validation)
            self._import_stats(xl, cohort_id, session, dry_run,
                                source_file=str(excel_path))

            # The sheet wins (see _sync_table): once every tab is read, the
            # rows earlier imports wrote are brought in step with the sheet.
            for table in ('weights', 'ramp_entries', 'surgeries'):
                self._sync_table(session, cohort_id, table, dry_run)
            if self.sync_blocked:
                # Said even when no table held an earlier row to refuse over
                # (a first import): the cohort stays NOT SYNCED until pinned.
                self._sync_refuse('every table', self.sync_blocked)

            # Log skipped sheets
            TEMPLATE_SHEETS = {'2_OCD_Animal_Tracking', '2_ODC_Animal_Tracking',
                               '8_BrainGlobe', '9_DLC_Kinematics'}
            SCRATCH_SHEETS = {'Sheet1'}
            HANDLED_SHEETS = {'0a_Metadata', '3d_Weights', '3e_Weights', '1_Weight',
                              '3b_Manual_Tray', '3a_Manual_Ramp',
                              '4_Contusion_Injury_Details', '5_SC_Injection_Details',
                              '0_Injection_Calculations', '6_Ladder', '1_Experiment_Planning',
                              '3c_Manual_Summary', '3d_Manual_Summary - survivors', '7_Stats'}

            for sheet in available_sheets:
                if sheet in TEMPLATE_SHEETS:
                    print(f"  [SKIP] {sheet} - empty template (data from separate pipeline)")
                elif sheet in SCRATCH_SHEETS:
                    print(f"  [SKIP] {sheet} - scratch/working sheet")
                elif sheet not in HANDLED_SHEETS:
                    print(f"  [UNKNOWN] {sheet} - not recognized by importer")

            # Whether anything is saved is decided by _import_session when this
            # block ends: only a real import with no error is committed.
            if dry_run:
                print(f"  [DRY RUN] Would import: {self.imported_counts}")
            elif self.errors:
                print(f"  [FAIL] {len(self.errors)} error(s): nothing from this "
                      f"workbook is saved")
            else:
                # Not "committed": inside a GUI Replace the caller commits once,
                # after this returns, and may still undo everything.
                print(f"  Import finished without errors; saving")

        if self.errors and not dry_run:
            self._report_not_committed(excel_path)
        elif refreshes_before is not None and not dry_run:
            # The commit re-derived the phases of every cohort whose scores
            # were added or removed (the database's phase hook, which also
            # relabels the cohort's reaches). Recorded in the result so the
            # import's output and ledger say what was relabelled -- or that
            # the re-derivation FAILED, which the hook itself only prints.
            done = [r for r in refreshes[refreshes_before:] if r.get('cohort_id') == cohort_id]
            if done:
                record = dict(done[-1])
                # The import relabels the cohort's pellet scores itself before
                # its commit (_assign_pellet_phases), so the hook finds them
                # already right; count those here, or the report would say no
                # hand score was relabelled.
                record['pellet_rows_relabelled'] = (record.get('pellet_rows_relabelled', 0)
                                                    + self.__dict__.get('_pellets_relabelled', 0))
                self.__dict__['_phase_refresh'] = record
        return self._get_result()

    @contextmanager
    def _import_session(self, dry_run: bool):
        """The database session one workbook is imported in, committed ONLY when
        the import is real (not a dry run) and recorded no error.

        WHY: Database.session() commits whenever its block ends normally. The
        importer used to skip its own commit when self.errors was not empty,
        and the session then committed anyway on the way out -- an import that
        recorded an error would have saved half a workbook. No reader records
        an error part way today (they record warnings), so this guards the
        next one that does: such an import rolls back as a whole.

        The rollback is asked for explicitly before leaving, because a caller
        may hand the importer a database whose session() neither commits nor
        rolls back on its own (the GUI Replace runs the whole import inside
        its own transaction and treats a rollback as "the import failed").
        """
        try:
            with self.db.session() as session:
                yield session
                if dry_run or self.errors:
                    session.rollback()
                    raise _NotCommitted()
                session.commit()
        except _NotCommitted:
            pass

    def _report_not_committed(self, excel_path: Path):
        """Make the result of an import that was rolled back say so.

        Its counts move out of 'imported' (they are rows that were never
        saved, and the sheet-import ledger records 'imported' as rows written)
        into 'not_saved', and a placeholder cohort start is reported as one
        that WOULD be made, not one that was."""
        self.errors.append(
            "Nothing from %s was saved: the import stopped on the error(s) above, "
            "and the whole workbook was undone rather than saving part of it. Fix "
            "the error and import again." % Path(excel_path).name)
        self.__dict__['_not_saved_counts'] = dict(self.imported_counts)
        self.imported_counts = {table: 0 for table in self.imported_counts}
        if self.__dict__.get('_sheet_sync'):
            # Its changes and removals were undone with everything else.
            self.__dict__['_sheet_sync']['not_saved'] = True
        placeholders = self.__dict__.get('_placeholder_starts', {})
        for cohort_id, (start, _dry) in list(placeholders.items()):
            placeholders[cohort_id] = (start, True)

    def _extract_cohort_from_filename(self, filename: str) -> Optional[str]:
        """The cohort a workbook's file name says it is, or None.

        First by the project sheet names in the lab settings
        (sheet_projects: "ProjectA_05_Animal_Tracking.xlsx" -> PROJA_05); then a
        name that STARTS with a configured project's own cohort id
        ("PROJA_05_tracking.xlsx", "PROJA05 tracking.xlsx"). Only configured
        projects: WHY not any letters-and-digits, as the patterns here once
        were -- a name of one project read as another's would file its animals
        under the wrong cohort, and the old fixed patterns named one project
        and nobody else's."""
        from . import sheet_projects
        cohort_id, _tail, _clash = sheet_projects.match(filename)
        if cohort_id:
            return cohort_id
        for project in sorted(sheet_projects.definitions(), key=len, reverse=True):
            m = re.match(r"^%s_?(\d{1,2})(?![0-9])" % re.escape(project), filename, re.IGNORECASE)
            if m:
                return "%s_%02d" % (project, int(m.group(1)))
        return None

    def _detect_start_date(self, xl: pd.ExcelFile, sheets: List[str]) -> Optional[datetime]:
        """Detect cohort start date (Day 0 = the earliest food-dep weigh-in).

        Day 0 is the first weigh-in, wherever it was recorded. In the current
        (CNT_05) format that's the earliest date in 3d_Weights; in older cohorts the
        first weigh-in lived in the ramp sheet (3a_Manual_Ramp). So we take the
        EARLIEST date found across both sheets, which covers both layouts. Only if
        neither exists do we fall back to 3b_Manual_Tray (training ~day 4, minus 4).
        The legacy 1_Weight sheet is not consulted, and 3b_Manual_Tray is never used
        for the primary anchor because it can carry stale/earlier date blocks (CNT_05).
        """
        from datetime import timedelta

        candidates = []
        # Earliest weigh-in from the weights sheet (3d preferred, else 3e)
        for sheet_name in ('3d_Weights', '3e_Weights'):
            if sheet_name in sheets:
                d = self._earliest_weight_date(xl, sheet_name)
                if d:
                    candidates.append(d)
                break
        # Earliest weigh-in from the ramp sheet (old cohorts keep Day 0 here)
        if '3a_Manual_Ramp' in sheets:
            d = self._earliest_ramp_date(xl)
            if d:
                candidates.append(d)

        if candidates:
            return min(candidates)

        # Fallback: manual tray sheet (training starts ~day 4)
        if '3b_Manual_Tray' in sheets:
            try:
                df = pd.read_excel(xl, sheet_name='3b_Manual_Tray')
                if 'Date' in df.columns:
                    dates = pd.to_datetime(df['Date'], errors='coerce')
                    min_date = dates.min()
                    if pd.notna(min_date):
                        return (min_date - timedelta(days=4)).date()
            except Exception:
                pass
        return None

    def _earliest_ramp_date(self, xl: pd.ExcelFile) -> Optional[datetime]:
        """Earliest date in the 3a_Manual_Ramp sheet, or None."""
        try:
            df = pd.read_excel(xl, sheet_name='3a_Manual_Ramp')
        except Exception:
            return None
        if 'Date' not in df.columns:
            return None
        dates = [d for d in (self._parse_date(v) for v in df['Date']) if d]
        return min(dates) if dates else None

    def _earliest_weight_date(self, xl: pd.ExcelFile, sheet: str) -> Optional[datetime]:
        """Earliest parseable date in a weights sheet (3d/3e_Weights), or None.

        Mirrors _import_weights_transposed's format detection: a vertical layout
        with a 'Date' column, or a transposed matrix with dates in row 0 or row 1.
        """
        try:
            df = pd.read_excel(xl, sheet_name=sheet, header=None)
        except Exception:
            return None
        if len(df) < 2:
            return None

        # Format A: vertical/long with a 'Date' column header
        first_row = [str(df.iloc[0, i]).strip() if pd.notna(df.iloc[0, i]) else ''
                     for i in range(min(6, len(df.columns)))]
        if 'Date' in first_row:
            try:
                df2 = pd.read_excel(xl, sheet_name=sheet)
                dates = pd.to_datetime(df2['Date'], errors='coerce')
                return dates.min().date() if pd.notna(dates.min()) else None
            except Exception:
                return None

        # Formats B/C: dates in row 0 or row 1 (whichever parses as dates)
        dates_in_row0 = dates_in_row1 = 0
        for col_idx in range(1, min(10, len(df.columns))):
            if self._parse_date(df.iloc[0, col_idx]) is not None:
                dates_in_row0 += 1
            if self._parse_date(df.iloc[1, col_idx]) is not None:
                dates_in_row1 += 1
        date_row_idx = 1 if dates_in_row1 > dates_in_row0 else 0

        parsed = [self._parse_date(df.iloc[date_row_idx, c])
                  for c in range(1, len(df.columns))]
        parsed = [d for d in parsed if d]
        return min(parsed) if parsed else None

    def _import_metadata(self, xl: pd.ExcelFile, cohort_id: str,
                         session, dry_run: bool):
        """Import 0a_Metadata sheet."""
        try:
            df = pd.read_excel(xl, sheet_name='0a_Metadata')
        except Exception as e:
            self.warnings.append(f"Failed to read 0a_Metadata: {e}")
            return

        for _, row in df.iterrows():
            raw_id = row.get('SubjectID') or row.get('Animal')
            if self._blank(raw_id):
                continue  # blank animal cell: no row here (see _resolve_subject_id)

            subject_id = self._resolve_subject_id(raw_id, cohort_id, '0a_Metadata')
            if not subject_id:
                continue  # _resolve_subject_id recorded why

            # Check if subject already exists
            existing = session.query(Subject).filter_by(subject_id=subject_id).first()
            if existing:
                self._fill_subject_facts(session, existing, row, dry_run)
                continue

            # The cohort row must exist before an animal is filed under it:
            # foreign keys are enforced, so a new cohort's metadata used to make
            # the whole import fail. 0a carries no cohort date, so this never
            # creates a cohort -- it refuses, with a warning (_ensure_cohort).
            if not self._ensure_cohort(session, cohort_id, dry_run):
                continue
            if dry_run:
                # Counted here, so the tabs that follow must not count it again.
                seen = self.__dict__.setdefault('_dry_run_subjects', set())
                if subject_id in seen:
                    continue
                seen.add(subject_id)

            subject = Subject(
                subject_id=subject_id,
                cohort_id=cohort_id,
                date_of_birth=self._parse_date(row.get('Date_of_Birth')),
                date_of_death=self._parse_date(row.get('Date_of_Death')),
                sex=self._parse_sex(row.get('Sex')),
                notes=row.get('Notes') if pd.notna(row.get('Notes')) else None,
            )

            if not dry_run:
                session.add(subject)
            self.imported_counts['subjects'] += 1

        session.flush()

    # The animal facts on 0a_Metadata, as (subject column, sheet heading, reader).
    _SUBJECT_FACTS = (
        ('date_of_birth', 'Date_of_Birth', '_parse_date'),
        ('date_of_death', 'Date_of_Death', '_parse_date'),
        ('sex', 'Sex', '_parse_sex'),
    )

    def _fill_subject_facts(self, session, subject, row, dry_run: bool):
        """Bring an existing animal's date of birth, date of death and sex in
        from 0a_Metadata -- filling only what the database holds EMPTY.

        WHY: an animal is often created before its metadata row is filled in
        (from a video, or from an earlier tab), and the import used to read
        0a_Metadata only for animals it was creating, so these facts never
        reached animals that already existed. Unlike the rows synced above,
        a subject records no origin: a value already present may have been
        typed in the GUI on purpose. So a present value is never overwritten;
        when the sheet says something else, a warning (and an item in the
        FIX THESE file) asks a person to settle it in the sheet or the GUI.
        Every fill goes to the audit log. A date of death later than today is
        not taken: it is a plan, not an event."""
        today = getattr(self, 'today', None) or date.today()
        for column, heading, reader in self._SUBJECT_FACTS:
            value = getattr(self, reader)(row.get(heading))
            if value is None:
                continue
            if column == 'date_of_death' and value > today:
                continue
            stored = getattr(subject, column)
            if isinstance(stored, datetime):
                stored = stored.date()
            if stored is not None and stored == value:
                continue
            # One verdict per animal and fact per import, however many rows
            # name the animal.
            done = self.__dict__.setdefault('_facts_seen', set())
            if (subject.subject_id, column) in done:
                continue
            done.add((subject.subject_id, column))
            report = self._sync_state().setdefault('subjects', {})
            item = {'subject_id': subject.subject_id, 'field': column,
                    'database': _jsonable(stored), 'sheet': _jsonable(value)}
            if self._blank(stored):
                report.setdefault('filled', []).append(item)
                self.imported_counts['subject_facts_filled'] = (
                    self.imported_counts.get('subject_facts_filled', 0) + 1)
                if not dry_run:
                    self._audit(session, 'UPDATE', 'subjects', subject.subject_id,
                                {column: None}, {column: _jsonable(value)})
                    setattr(subject, column, value)
                continue
            report.setdefault('differs', []).append(item)
            self.warnings.append(self._ascii(
                "0a_Metadata: %s's %s is %s in the database but %s in the sheet; the "
                "database value was kept (where it came from is not recorded). Correct "
                "whichever is wrong -- the sheet, or the animal in the GUI"
                % (subject.subject_id, column.replace('_', ' '), item['database'],
                   item['sheet'])))

    def _import_weights(self, xl: pd.ExcelFile, cohort_id: str,
                        session, dry_run: bool):
        """Import 1_Weight sheet."""
        try:
            df = pd.read_excel(xl, sheet_name='1_Weight')
        except Exception as e:
            self.warnings.append(f"Failed to read 1_Weight: {e}")
            self._tab_unread('weights', "the 1_Weight tab could not be read (%s)" % e)
            return

        for _, row in df.iterrows():
            raw_id = row.get('Animal') or row.get('SubjectID')
            if self._blank(raw_id):
                continue  # blank animal cell: no row here (see _resolve_subject_id)

            subject_id = self._resolve_subject_id(raw_id, cohort_id, '1_Weight')
            if not subject_id:
                continue  # _resolve_subject_id recorded why

            date_val = self._parse_date(row.get('Date'))
            if not date_val:
                continue

            weight_val = row.get('Weight')
            if pd.isna(weight_val):
                continue

            try:
                weight_val = float(weight_val)
            except (ValueError, TypeError):
                self._seen('weights', (subject_id, date_val), None)
                continue

            valid, msg = validate_weight(weight_val)
            if not valid:
                self.warnings.append(f"Invalid weight for {subject_id} on {date_val}: {msg}")
                self._seen('weights', (subject_id, date_val), None)
                continue
            self._seen('weights', (subject_id, date_val), {
                'weight_grams': weight_val,
                # The percentage this row stores (see _weights_as_the_sheet_says).
                'weight_percent': self._parse_float(row.get('Weight %'))})

            # Ensure subject exists (False: refused, and a warning says why)
            if not self._ensure_subject(session, subject_id, cohort_id, dry_run,
                                        first_seen=date_val):
                continue

            # Check for duplicate
            existing = session.query(Weight).filter_by(
                subject_id=subject_id, date=date_val
            ).first()
            if existing:
                continue

            weight = Weight(
                subject_id=subject_id,
                date=date_val,
                weight_grams=weight_val,
                weight_percent=row.get('Weight %') if pd.notna(row.get('Weight %')) else None,
                entered_by='excel_import',
            )

            if not dry_run:
                session.add(weight)
            self.imported_counts['weights'] += 1

    def _import_weights_transposed(self, xl: pd.ExcelFile, cohort_id: str,
                                    session, dry_run: bool, sheet: str = '3d_Weights'):
        """
        Import 3d_Weights or 3e_Weights sheet.

        Handles three formats:
        A) Vertical/long: columns = Date, Day, Animal, Weight_g, ...
        B) Transposed matrix (dates Row 0, phases Row 1): CNT_02/03/04 style
        C) Transposed matrix (phases Row 0, dates Row 1): CNT_01 style
        """
        try:
            df = pd.read_excel(xl, sheet_name=sheet, header=None)
        except Exception as e:
            self.warnings.append(f"Failed to read {sheet}: {e}")
            self._tab_unread('weights', "the %s tab could not be read (%s)" % (sheet, e))
            return

        if len(df) < 2:
            return

        # Detect format A: vertical/long with column headers
        first_row_vals = [str(df.iloc[0, i]).strip() if pd.notna(df.iloc[0, i]) else ''
                          for i in range(min(6, len(df.columns)))]
        if 'Date' in first_row_vals and ('Animal' in first_row_vals or 'Weight_g' in first_row_vals):
            self._import_weights_vertical(df, cohort_id, session, dry_run, sheet=sheet)
            return

        # Detect which row has dates (format B vs C)
        # Check a few columns in both rows for parseable dates
        dates_in_row0 = 0
        dates_in_row1 = 0
        for col_idx in range(1, min(10, len(df.columns))):
            if self._parse_date(df.iloc[0, col_idx]) is not None:
                dates_in_row0 += 1
            if self._parse_date(df.iloc[1, col_idx]) is not None:
                dates_in_row1 += 1

        if dates_in_row1 > dates_in_row0:
            # Format C: phases in row 0, dates in row 1
            date_row_idx, phase_row_idx = 1, 0
        else:
            # Format B: dates in row 0, phases in row 1
            date_row_idx, phase_row_idx = 0, 1

        date_row = df.iloc[date_row_idx]
        phase_row = df.iloc[phase_row_idx]

        # Build mapping of column index to (date, phase)
        date_cols = {}
        for col_idx in range(1, len(df.columns)):
            date_val = date_row.iloc[col_idx]
            phase_val = phase_row.iloc[col_idx]

            # Skip "Average" columns
            if isinstance(phase_val, str) and phase_val.strip().lower() == 'average':
                continue
            if pd.isna(date_val) or (isinstance(date_val, str) and 'average' in date_val.lower()):
                continue

            parsed_date = self._parse_date(date_val)
            if parsed_date:
                date_cols[col_idx] = (parsed_date, str(phase_val) if pd.notna(phase_val) else '')

        # The grid's earliest date, in case the cohort has to be created with a
        # placeholder start (see _ensure_cohort).
        grid_first = min(d for d, _ in date_cols.values()) if date_cols else None

        # Process each animal (rows 2+)
        for row_idx in range(2, len(df)):
            row = df.iloc[row_idx]
            raw_id = row.iloc[0]
            if self._blank(raw_id):
                continue  # blank animal cell: no row here (see _resolve_subject_id)

            subject_id = self._resolve_subject_id(raw_id, cohort_id, sheet)
            if not subject_id:
                continue  # _resolve_subject_id recorded why

            # Ensure subject exists (False: refused, and a warning says why)
            if not self._ensure_subject(session, subject_id, cohort_id, dry_run,
                                        first_seen=grid_first):
                continue

            # Process each date column
            for col_idx, (date_val, phase) in date_cols.items():
                weight_val = row.iloc[col_idx]

                if pd.isna(weight_val):
                    continue

                try:
                    weight_val = float(weight_val)
                except (ValueError, TypeError):
                    self._seen('weights', (subject_id, date_val), None)
                    continue

                valid, msg = validate_weight(weight_val)
                if not valid:
                    self.warnings.append(f"Invalid weight for {subject_id} on {date_val}: {msg}")
                    self._seen('weights', (subject_id, date_val), None)
                    continue
                self._seen('weights', (subject_id, date_val), {'weight_grams': weight_val})

                # Check for duplicate
                existing = session.query(Weight).filter_by(
                    subject_id=subject_id, date=date_val
                ).first()
                if existing:
                    continue

                weight = Weight(
                    subject_id=subject_id,
                    date=date_val,
                    weight_grams=weight_val,
                    notes=f"Phase: {phase}" if phase else None,
                    entered_by='excel_import',
                )

                if not dry_run:
                    session.add(weight)
                self.imported_counts['weights'] += 1

    def _import_weight_percent_from_tray(self, xl, cohort_id: str, session, dry_run: bool):
        """Fill weight_percent from 3b_Manual_Tray's "Weight %" column.

        The weight GRIDS (3e_Weights and its older cousins) carry grams only, so
        every session weight was stored with no percentage -- while the tray tab
        held one on every row, for every cohort, unread. This fills them in by
        animal and date.

        It UPDATES existing rows rather than inserting: by the time this runs the
        weight row exists, put there by the grid importer. A row that already has
        a percentage is left alone here, so the ramp tab's own numbers are not
        overwritten. A percentage that CHANGES in the sheet is brought in step
        afterwards by the sheet-wins sync (_sync_table, through
        _weights_as_the_sheet_says), for which this records the tray's numbers.
        """
        if '3b_Manual_Tray' not in xl.sheet_names:
            return
        try:
            df = pd.read_excel(xl, sheet_name='3b_Manual_Tray')
        except Exception as e:
            self.warnings.append("Failed to read 3b_Manual_Tray for weight %%: %s" % e)
            return

        # The column has been spelled a few ways across cohorts; take whichever
        # is present rather than failing on a sheet that has the data under a
        # different heading.
        pct_column = None
        for candidate in ('Weight %', 'Weight_%', 'Weight_Pct', 'Weight Pct',
                          '% body weight', 'Weight_Pct_Baseline'):
            if candidate in df.columns:
                pct_column = candidate
                break
        if pct_column is None or 'Animal' not in df.columns or 'Date' not in df.columns:
            return

        seen = set()
        filled = 0
        # The same year-typo reading as the scores on this tab, so a day's
        # weight % lands on the day its scores are filed under.
        repairs = self._tab_date_repairs(
            cohort_id, '3b_Manual_Tray', [self._parse_date(v) for v in df['Date']])
        for _, row in df.iterrows():
            raw_id = row.get('Animal')
            if self._blank(raw_id):
                continue  # blank animal cell: no row here (see _resolve_subject_id)
            # Through the same gate as the scores: a foreign id must not fill
            # in the percentage of another cohort's animal.
            subject_id = self._resolve_subject_id(raw_id, cohort_id, '3b_Manual_Tray')
            date_val = self._parse_date(row.get('Date'))
            if subject_id and date_val in repairs:
                # On a guess a row keeps its typed date only where its own tray
                # is still stored under it, exactly as the scores on this tab
                # are decided (_guess_reads_as_typed), so a day's weight %
                # lands on the day its scores are filed under.
                tray_key = self._tray_key(subject_id, date_val, row)
                if not (self.sync_blocked and tray_key is not None
                        and self._guess_reads_as_typed(session, tray_key,
                                                       repairs[date_val])):
                    date_val = repairs[date_val]
            # No id: _resolve_subject_id recorded why. No date: a session not
            # held yet (generated workbooks pre-write the future rows with the
            # animal and no date), so there is no weighing to fill in.
            if not subject_id or not date_val:
                continue
            key = (subject_id, date_val)
            if key in seen:
                # Four tray rows per animal per day, all carrying the same
                # weight: the first is enough and the rest are the same number.
                continue
            seen.add(key)

            pct = self._parse_float(row.get(pct_column))
            if pct is None:
                continue
            # The sheet writes it as a fraction (0.87); the table stores it the
            # same way, so a value above 2 is a sheet that wrote 87 instead and
            # is converted rather than stored as an 8700%% animal.
            if pct > 2:
                pct = pct / 100.0
            # What the tray tab says, for the sheet-wins sync of this weight's
            # percentage (_weights_as_the_sheet_says): the tray's number is the
            # one stored when the row's own tab gave none.
            self.__dict__.setdefault('_tray_weight_percent', {})[key] = pct

            existing = session.query(Weight).filter_by(
                subject_id=subject_id, date=date_val).first()
            if existing is None or existing.weight_percent is not None:
                continue
            if not dry_run:
                existing.weight_percent = pct
            filled += 1

        if filled:
            self.imported_counts['weight_percent_filled'] = (
                self.imported_counts.get('weight_percent_filled', 0) + filled)

    def _import_weights_vertical(self, df: pd.DataFrame, cohort_id: str,
                                  session, dry_run: bool, sheet: str = '3d_Weights'):
        """
        Import weights from vertical/long format (CNT_05 style).

        Columns: Date, Day, Animal, Weight_g, Weight_Pct_Baseline, Notes
        """
        # Use first row as headers
        headers = [str(df.iloc[0, i]).strip() if pd.notna(df.iloc[0, i]) else f'col_{i}'
                   for i in range(len(df.columns))]
        df_data = df.iloc[1:].copy()
        df_data.columns = headers

        for _, row in df_data.iterrows():
            raw_id = row.get('Animal')
            if self._blank(raw_id):
                continue  # blank animal cell: no row here (see _resolve_subject_id)

            subject_id = self._resolve_subject_id(raw_id, cohort_id, sheet)
            if not subject_id:
                continue  # _resolve_subject_id recorded why

            date_val = self._parse_date(row.get('Date'))
            if not date_val:
                continue

            weight_val = row.get('Weight_g')
            if pd.isna(weight_val):
                continue

            try:
                weight_val = float(weight_val)
            except (ValueError, TypeError):
                self._seen('weights', (subject_id, date_val), None)
                continue

            valid, msg = validate_weight(weight_val)
            if not valid:
                self.warnings.append(f"Invalid weight for {subject_id} on {date_val}: {msg}")
                self._seen('weights', (subject_id, date_val), None)
                continue
            weight_pct = self._parse_float(row.get('Weight_Pct_Baseline'))
            # With the percentage this row stores (see _weights_as_the_sheet_says).
            self._seen('weights', (subject_id, date_val),
                       {'weight_grams': weight_val, 'weight_percent': weight_pct})

            # Ensure subject exists (False: refused, and a warning says why)
            if not self._ensure_subject(session, subject_id, cohort_id, dry_run,
                                        first_seen=date_val):
                continue

            # Check for duplicate
            existing = session.query(Weight).filter_by(
                subject_id=subject_id, date=date_val
            ).first()
            if existing:
                continue

            notes_val = row.get('Notes')
            notes_str = str(notes_val) if pd.notna(notes_val) else None

            weight = Weight(
                subject_id=subject_id,
                date=date_val,
                weight_grams=weight_val,
                weight_percent=weight_pct,
                notes=notes_str,
                entered_by='excel_import',
            )

            if not dry_run:
                session.add(weight)
            self.imported_counts['weights'] += 1

    def _import_ramp_data(self, xl: pd.ExcelFile, cohort_id: str,
                          session, dry_run: bool):
        """
        Import 3a_Manual_Ramp sheet (food deprivation phase).

        Columns (old): Mouse ID, Date, Weight, % body weight, Tray Start (g), Tray End (g), Dif
        Columns (new/CNT_05): Animal, Date, Day, Weight, Food_Start_(g), Food_End_(g), Food_Eaten_(g)
        """
        try:
            df = pd.read_excel(xl, sheet_name='3a_Manual_Ramp')
        except Exception as e:
            self.warnings.append(f"Failed to read 3a_Manual_Ramp: {e}")
            self._tab_unread('weights', "the 3a_Manual_Ramp tab could not be read (%s)" % e)
            self._tab_unread('ramp_entries', "the 3a_Manual_Ramp tab could not be read (%s)" % e)
            return

        # Number ramp days anchored to Day 0 = the cohort start date (the earliest
        # weigh-in, detected across the weights + ramp sheets). If a ramp date equals
        # the start, it IS Day 0 -- older cohorts logged the pre-food-dep weigh-in in
        # this sheet. In the CNT_05 format Day 0 lives in 3d_Weights, so the earliest
        # ramp date becomes Day 1. Reads real dates; no calendar-gap arithmetic.
        cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
        start = cohort.start_date if cohort else None
        if 'Date' in df.columns:
            ramp_dates = sorted({d for d in (self._parse_date(v) for v in df['Date']) if d})
            if start:
                ordered = sorted(set(ramp_dates) | {start})
                ramp_day_map = {d: ordered.index(d) for d in ramp_dates}
            else:
                ramp_day_map = {d: i + 1 for i, d in enumerate(ramp_dates)}
        else:
            ramp_day_map = {}

        for _, row in df.iterrows():
            raw_id = row.get('Mouse ID') or row.get('Animal') or row.get('Subject_ID')
            if self._blank(raw_id):
                continue  # blank animal cell (the column-average rows under the table)

            subject_id = self._resolve_subject_id(raw_id, cohort_id, '3a_Manual_Ramp')
            if not subject_id:
                continue  # _resolve_subject_id recorded why

            date_val = self._parse_date(row.get('Date'))
            if not date_val:
                continue

            # Ensure subject exists (False: refused, and a warning says why)
            if not self._ensure_subject(session, subject_id, cohort_id, dry_run,
                                        first_seen=date_val):
                continue

            # Import weight from ramp data
            weight_val = row.get('Weight')
            weight_pct = self._parse_float(row.get('% body weight'))
            tray_start = self._parse_float(
                row.get('Tray Start (g)') or row.get('Tray_Start') or row.get('Food_Start_(g)'))
            tray_end = self._parse_float(
                row.get('Tray End (g)') or row.get('Tray_End') or row.get('Food_End_(g)'))
            food_consumed = self._parse_float(
                row.get('Dif') or row.get('Food_Consumed') or row.get('Food_Eaten_(g)'))

            if pd.notna(weight_val):
                try:
                    weight_val = float(weight_val)
                    valid, _ = validate_weight(weight_val)
                    self._seen('weights', (subject_id, date_val),
                               {'weight_grams': weight_val, 'weight_percent': weight_pct}
                               if valid else None)
                    if valid:
                        # Also save to weights table for compatibility
                        existing_weight = session.query(Weight).filter_by(
                            subject_id=subject_id, date=date_val
                        ).first()
                        if not existing_weight:
                            weight = Weight(
                                subject_id=subject_id,
                                date=date_val,
                                weight_grams=weight_val,
                                weight_percent=weight_pct,
                                notes='Ramp phase',
                                entered_by='excel_import',
                            )
                            if not dry_run:
                                session.add(weight)
                            self.imported_counts['weights'] += 1
                    else:
                        weight_val = None
                except (ValueError, TypeError):
                    self._seen('weights', (subject_id, date_val), None)
                    weight_val = None
            else:
                weight_val = None

            # Food eaten: the sheet's own column, else start minus end.
            eaten = food_consumed if food_consumed else (
                tray_start - tray_end if tray_start and tray_end else None)
            if weight_val:
                # What the sheet says for this ramp day (see _sync_table).
                self._seen('ramp_entries', (subject_id, date_val), {
                    'body_weight_grams': weight_val, 'weight_percent_baseline': weight_pct,
                    'tray_start_grams': tray_start, 'tray_end_grams': tray_end,
                    'food_consumed_grams': eaten})

            # Create RampEntry record with full data
            existing_ramp = session.query(RampEntry).filter_by(
                subject_id=subject_id, date=date_val
            ).first()
            if not existing_ramp and weight_val:
                # Ramp day comes from the sheet's own date ordering (earliest = Day 1),
                # not from the cohort start date -- see ramp_day_map above.
                ramp_day = ramp_day_map.get(date_val)

                ramp_entry = RampEntry(
                    subject_id=subject_id,
                    date=date_val,
                    ramp_day=ramp_day,
                    body_weight_grams=weight_val,
                    weight_percent_baseline=weight_pct,
                    tray_start_grams=tray_start,
                    tray_end_grams=tray_end,
                    food_consumed_grams=eaten,
                    entered_by='excel_import',
                )
                if not dry_run:
                    session.add(ramp_entry)
                self.imported_counts['ramp_entries'] += 1

    @staticmethod
    def _surgery_cells(row) -> Dict:
        """A surgery-tab row as the shared rule judges it: {heading: cell}.

        Headings are stripped of surrounding spaces because animal_records
        reads them that way; a weight heading typed as 'Subject_Weight (g) '
        must not prove a surgery to one reader and be invisible to the other.
        When two headings differ only by such spaces, the FIRST column is the
        one judged, as in animal_records (a plain dict would keep the last, and
        the two readers would judge different cells of the same row)."""
        cells = {}
        for k, v in row.items():
            cells.setdefault(str(k).strip(), v)
        return cells

    def _surgery_rows_as_typed(self, xl, tab: str) -> Dict:
        """{row index: cells} for a surgery tab, each cell as it was typed, for
        the shared rule to judge. The index matches pandas' ordinary read of
        the same tab, which the readers keep for the values they store.

        WHY a second read: pandas' ordinary read changes cells before anyone
        sees them. Text that looks like a number becomes one when the whole
        column can ('1e1' -> 10.0, 'inf' -> infinity, '2025' -> 2025), and text
        such as 'n/a' or 'NaN' becomes a blank. animal_records reads the cells
        as typed, so judging the ordinary read let the two readers take
        different surgeries from the same row ('1e1' proved a 10 g surgery
        here and was refused there). Read as text-or-value with nothing
        converted, both judge the same cells. Blank cells come back as ''."""
        typed = pd.read_excel(xl, sheet_name=tab, dtype=object,
                              keep_default_na=False, na_values=[])
        return {idx: self._surgery_cells(r) for idx, r in typed.iterrows()}

    def _surgery_tab_has_weight_column(self, df: pd.DataFrame, tab: str) -> bool:
        """True when a surgery tab has a column the weight on the day can be
        read from. Without one no row can be shown to have happened, so
        nothing is imported from the tab -- importing every row would let the
        plans through -- and, when the tab holds any row with more than its
        animal id on it, a person is told to restore the heading. (Without
        that line every surgery would quietly look like a plan.)"""
        headings = {str(c).strip() for c in df.columns}
        if any(c in headings for c in SURGERY_PROOF_COLUMNS[tab]):
            return True
        if (df.notna().sum(axis=1) >= 2).any():
            self.warnings.append(self._ascii(
                "%s: no '%s' column, so no row on it can be shown to be a surgery "
                "that happened; nothing imported from this tab (restore that "
                "column heading)" % (tab, SURGERY_WEIGHT_COLUMNS[0])))
        return False

    def _note_blank_animal_surgery(self, row, idx, tab: str):
        """Record a surgery row whose animal cell is blank but whose weight on
        the day is typed.

        A blank animal cell is normally padding under the table and stays
        silent (see _resolve_subject_id). A weighed row is different: by the
        shared rule (animal_records.surgery_verdict) the weight is what shows
        a surgery happened, and dropping it without a word would lose a real
        surgery without a trace.
        """
        cells = self._surgery_cells(row)
        if surgery_weight(cells, tab) is None:
            return
        date_val = surgery_date(cells)
        # read_excel took the sheet's first row as the header, so data row
        # index 0 is the sheet's row 2 -- the number a person can find.
        try:
            sheet_row = int(idx) + 2
        except (TypeError, ValueError):
            sheet_row = idx
        self._note_unimported(
            tab, "surgery not imported: the weight on the day is typed but the "
                 "animal id is blank",
            "sheet row %s (%s)" % (sheet_row, date_val or 'no date'))

    def _note_planned_surgery(self, cells: Dict, label: str, tab: str):
        """A planned surgery row (no weight on the day): tell a person only
        when it is overdue.

        A plan is not a problem: the sheet is filled in ahead of time on
        purpose, so every row of a cohort still in training is a plan, and
        listing them on every run buried the warnings that matter. An undated
        plan is never announced; a dated one is announced only once its date
        is more than animal_records.PLANNED_SURGERY_GRACE_DAYS past (the weight
        is typed late by nature), because by then the surgery was done and not
        written down, or not done and the date not updated.

        WHY here and not in animal_records too: both read the same surgery
        rows, and the sheet import ran both, so each plan was announced twice.
        The importer is the one that announces, because it runs on every route
        (the scheduled and command-line sheet import, and the GUI's Import and
        Replace); animal_records runs only on the first, after the importer.
        """
        # getattr: some callers build the importer without __init__ to use one
        # reader on its own.
        today = getattr(self, 'today', None) or date.today()
        day = planned_surgery_overdue(cells, tab, today)
        if day is None:
            return
        self._note_unimported(
            tab, "surgery planned but not recorded: its date is more than %d days "
                 "past and no weight on the day is typed. If it was done, type the "
                 "weight; if not, correct the planned date"
                 % PLANNED_SURGERY_GRACE_DAYS,
            "%s (%s)" % (label, day))

    def _performed_surgery(self, row, raw_id, cohort_id: str, tab: str):
        """(subject_id, surgery_date, grams) for a row that records a performed
        surgery, else None -- with the reason recorded for a person where
        there is one.

        WHETHER the surgery happened is decided by the shared rule,
        animal_records.surgery_verdict, and nowhere else: the weight on the
        day (the one number that cannot exist before the day) on a row whose
        date reads as a date. Everything else on these tabs -- date, type,
        virus, titre, target, depths, coordinates, drugs, Survived,
        Actual_Dwell -- can be typed, generated or carried over from the
        previous cohort's workbook before the surgery; counting any of it as
        proof is how planned injections were imported and exported as
        performed. animal_records copies the same tabs with the same rule, so
        the typed surgeries table and the copied records hold the same
        surgeries (tests/test_animal_records_proof.py fails if they differ).

        The verdict comes first: a plan is not checked any further (its id,
        date and constants are pre-filled for every animal) and is announced
        only when overdue (_note_planned_surgery). A held row -- one that looks
        done but cannot be taken as done -- is named every run with what to
        fix. A performed row must also name an animal of this workbook's
        cohort (_resolve_subject_id records why not).
        """
        cells = self._surgery_cells(row)
        label = (self._normalize_subject_id(raw_id, cohort_id)
                 or repr(str(raw_id).strip()))
        verdict, why = surgery_verdict(cells, tab)
        if verdict == PLANNED:
            self._note_planned_surgery(cells, label, tab)
            return None
        date_val = surgery_date(cells)
        if verdict == HELD:
            # One line per row: each says what to fix on that row, so rows with
            # different reasons cannot share a line. Held rows are rare.
            self._warn_once(
                (tab, label, why),
                "%s: surgery for %s (%s) not imported -- %s"
                % (tab, label, date_val or 'no date', why))
            return None

        subject_id = self._resolve_subject_id(raw_id, cohort_id, tab)
        if not subject_id:
            return None  # _resolve_subject_id recorded why
        return subject_id, date_val, surgery_weight(cells, tab)

    def _import_contusion_surgeries(self, xl: pd.ExcelFile, cohort_id: str,
                                     session, dry_run: bool):
        """
        Import 4_Contusion_Injury_Details with actual column names.

        Actual columns: Subject_ID, Surgery_Date, Surgery_Type, Actual_kd,
                       Actual_displacement, Actual_Velocity, Actual_Dwell, Survived
        """
        try:
            df = pd.read_excel(xl, sheet_name='4_Contusion_Injury_Details')
        except Exception as e:
            self.warnings.append(f"Failed to read 4_Contusion_Injury_Details: {e}")
            self._tab_unread('surgeries', "the 4_Contusion_Injury_Details tab could "
                                          "not be read (%s)" % e)
            return
        if not self._surgery_tab_has_weight_column(df, '4_Contusion_Injury_Details'):
            if (df.notna().sum(axis=1) >= 2).any():
                self._tab_unread('surgeries', "the 4_Contusion_Injury_Details tab has no "
                                              "weight column, so no row on it could be read")
            return
        # The gate judges the cells as typed; the stored values come from df.
        as_typed = self._surgery_rows_as_typed(xl, '4_Contusion_Injury_Details')

        for idx, row in df.iterrows():
            cells = as_typed.get(idx, row)
            # Try various column name possibilities
            raw_id = (row.get('Subject_ID') or row.get('Animal') or
                      row.get('SubjectID') or row.get('Subject'))
            if self._blank(raw_id):
                # Padding stays silent; a weighed row is reported.
                self._note_blank_animal_surgery(cells, idx, '4_Contusion_Injury_Details')
                continue

            # Plans, held rows and foreign animals stop here, each announced
            # as the policy says (see _performed_surgery).
            performed = self._performed_surgery(
                cells, raw_id, cohort_id, '4_Contusion_Injury_Details')
            if performed is None:
                continue
            # The weight is not stored on a contusion row: none of the
            # contusions already imported carries it, and this importer only
            # adds rows, so storing it from now on would split the table.
            subject_id, date_val, _grams = performed

            # Ensure subject exists (False: refused, and a warning says why)
            if not self._ensure_subject(session, subject_id, cohort_id, dry_run,
                                        first_seen=date_val):
                continue

            # Update subject death status from Survived column
            survived = row.get('Survived')
            if pd.notna(survived):
                survived_str = str(survived).strip().upper()
                if survived_str in ('N', 'NO', 'FALSE', '0'):
                    # Remembered, so removing an older contusion row cannot
                    # undo a death this sheet still records (_undo_copied_death).
                    self.__dict__.setdefault('_marked_dead', set()).add(subject_id)
                    # Mark subject as inactive (dead)
                    subject = session.query(Subject).filter_by(subject_id=subject_id).first()
                    if (subject and subject.is_active != 0 and self._held_back(
                            "recording %s as not survived" % subject_id)):
                        subject = None
                    if subject and not dry_run:
                        subject.is_active = 0
                        if not subject.date_of_death:
                            subject.date_of_death = date_val

            # Map actual column names to our schema. Built before the
            # duplicate check: the sheet's values are also what an earlier
            # import's row is compared with (_sync_table).
            values = dict(
                force_kdyn=self._parse_float(row.get('Actual_kd') or row.get('Force_kDyn')),
                displacement_um=self._parse_float(row.get('Actual_displacement') or row.get('Displacement_um')),
                velocity_mm_s=self._parse_float(row.get('Actual_Velocity') or row.get('Velocity_mm_s')),
                dwell_time_s=self._parse_float(row.get('Actual_Dwell') or row.get('Dwell_time_s')),
                surgeon=row.get('Surgeon') if pd.notna(row.get('Surgeon')) else None,
                anesthesia=row.get('Anesthetic') if pd.notna(row.get('Anesthetic')) else None,
                notes=self._build_surgery_notes(row),
            )
            self._seen('surgeries', (subject_id, date_val, 'contusion'), values)

            # Check for duplicate
            existing = session.query(Surgery).filter_by(
                subject_id=subject_id,
                surgery_date=date_val,
                surgery_type='contusion'
            ).first()
            if existing:
                continue

            surgery = Surgery(
                subject_id=subject_id,
                surgery_date=date_val,
                surgery_type='contusion',
                entered_by='excel_import',
                **values,
            )

            if not dry_run:
                session.add(surgery)
            self.imported_counts['surgeries'] += 1

    def _build_surgery_notes(self, row) -> Optional[str]:
        """Build surgery notes from additional columns."""
        notes_parts = []

        # Collect additional info that might be useful
        for col, label in [
            ('Surgery_Severity', 'Severity'),
            ('Contusion_Location', 'Location'),
            ('Intended_kd', 'Intended kD'),
            ('Stage_Height', 'Stage Height'),
        ]:
            val = row.get(col)
            if pd.notna(val):
                notes_parts.append(f"{label}: {val}")

        return '; '.join(notes_parts) if notes_parts else None

    def _import_pellet_scores(self, xl: pd.ExcelFile, cohort_id: str,
                              session, dry_run: bool):
        """Import 3b_Manual_Tray sheet."""
        try:
            df = pd.read_excel(xl, sheet_name='3b_Manual_Tray')
        except Exception as e:
            self.warnings.append(f"Failed to read 3b_Manual_Tray: {e}")
            self._refuse_unread_tray_tab(session, cohort_id,
                                         "the 3b_Manual_Tray tab could not be read (%s)" % e)
            return

        # PASS 1: parse every scorable row. Nothing is inserted yet, because
        # the phase a session belongs to depends on the cohort's WHOLE date
        # structure (gap detection), which is only known once every row is
        # read. Test_Phase in the sheet itself is operator-entered and
        # unreliable, so it is ignored.
        rows = []  # (subject_id, date, tray_type, tray_number, {pellet: score})
        for _, row in df.iterrows():
            raw_id = row.get('Animal') or row.get('SubjectID')
            if self._blank(raw_id):
                continue  # blank animal cell: no row here (see _resolve_subject_id)

            subject_id = self._resolve_subject_id(raw_id, cohort_id, '3b_Manual_Tray')
            if not subject_id:
                continue  # _resolve_subject_id recorded why

            date_val = self._parse_date(row.get('Date'))
            if not date_val:
                continue

            # Parse tray type/number (e.g., "P1", "F2")
            tray_info = row.get('Tray Type/Number') or row.get('Tray')
            if pd.isna(tray_info):
                continue

            tray_str = str(tray_info).strip()
            if len(tray_str) < 2:
                continue

            tray_type = tray_str[0].upper()
            try:
                tray_number = int(tray_str[1:])
            except ValueError:
                continue

            scores = {}
            for pellet_num in range(1, 21):
                col = pellet_num if pellet_num in df.columns else str(pellet_num)
                if col not in df.columns:
                    continue
                score = row.get(col)
                if pd.isna(score):
                    continue
                # A cell that holds something but not a score is kept in the
                # parse as None ("the sheet has a value here, unreadable"),
                # never dropped: the sync below removes a stored score only
                # when its cell is EMPTY, and a typo in a cell must not read as
                # the score having been deleted. None is never stored.
                try:
                    score = int(score)
                except (ValueError, TypeError):
                    scores[pellet_num] = None
                    continue
                valid, msg = validate_pellet_score(score)
                if not valid:
                    self.warnings.append(
                        f"Invalid score for {subject_id} {date_val} T{tray_number}P{pellet_num}: {msg}"
                    )
                    scores[pellet_num] = None
                    continue
                scores[pellet_num] = score
            rows.append((subject_id, date_val, tray_type, tray_number, scores))

        # A session typed a year off (2025 for 2026) is read as the year it
        # was meant to be, and scores an earlier import already filed under
        # the typed year are moved to it -- otherwise the phases of the whole
        # cohort are derived around a year-long false gap (date_typos).
        repairs = self._tab_date_repairs(cohort_id, '3b_Manual_Tray', [r[1] for r in rows])
        move_repairs = repairs
        if repairs and self.sync_blocked:
            # On a guess (sync_blocked) the rows READ are still re-dated -- the
            # usual case is a cohort whose stored scores an earlier import
            # already moved to the date meant, and reading the typo rows under
            # the typed date would store the same tray a second time, a year
            # away. Only the MOVE of stored rows is held back. The choice is
            # made PER TRAY, never per date: a row is read as typed only when
            # its own tray is still stored under the typed date by an earlier
            # import, and not under the date meant (_guess_reads_as_typed) --
            # then the read row matches those stored rows instead of being
            # added beside them. WHY per tray: one stray score under the typed
            # date (typed in by a person, or one the move left because the
            # date meant already had that pellet) used to send the WHOLE day
            # back to the typed date, storing every other tray of it twice.
            move_repairs = {}
            typed_trays = set()
            redated = []
            for s_, d, tt, tn, sc in rows:
                if d in repairs and self._guess_reads_as_typed(
                        session, (s_, d, tt, tn), repairs[d]):
                    typed_trays.add((s_, d, tt, tn))
                    redated.append((s_, d, tt, tn, sc))
                else:
                    redated.append((s_, repairs.get(d, d), tt, tn, sc))
            if typed_trays:
                self._held_back(
                    "re-dating %d 3b_Manual_Tray tray%s stored under a date typed a year "
                    "off (%s); those rows are read as dated in the file" % (
                        len(typed_trays), "" if len(typed_trays) == 1 else "s",
                        ", ".join("%s -> %s" % (t, repairs[t]) for t in
                                  sorted({k[1] for k in typed_trays}))))
            rows = redated
        elif repairs:
            rows = [(s, repairs.get(d, d), tt, tn, sc) for s, d, tt, tn, sc in rows]
        if move_repairs:
            self._move_misdated_pellet_rows(session, cohort_id, move_repairs, dry_run)

        # A weekend-dated run inside a weekday's block -- a formula date counting
        # from the wrong row after a row was cut -- is read as that weekday, and
        # its scores already filed under the weekend date are moved (date_typos).
        from .date_typos import block_date_repairs
        islands = block_date_repairs([(s, d, tt, tn) for s, d, tt, tn, _ in rows])
        tray_moves = {}
        if islands:
            self._report_block_date_repairs(cohort_id, rows, islands)
            tray_moves = {(rows[i][0], rows[i][1], rows[i][2], rows[i][3]): meant
                          for i, meant in islands.items()}
            if self.sync_blocked:
                # As for the year typos above: the rows read are re-dated, the
                # stored rows are not moved, and a tray an earlier import still
                # has under the weekend date (and not under the weekday) is
                # read as dated so it is never stored twice.
                still = {k for k, meant in tray_moves.items()
                         if self._guess_reads_as_typed(session, k, meant)}
                if still:
                    self._held_back(
                        "re-dating %d stored weekend-dated 3b_Manual_Tray tray%s to the "
                        "weekday they sit among; those rows are read as dated in the "
                        "file" % (len(still), "" if len(still) == 1 else "s"))
                islands = {i: meant for i, meant in islands.items()
                           if (rows[i][0], rows[i][1], rows[i][2], rows[i][3]) not in still}
                tray_moves = {}
            else:
                self._move_misdated_tray_rows(session, cohort_id, tray_moves, dry_run)
            rows = [(s, islands.get(i, d), tt, tn, sc)
                    for i, (s, d, tt, tn, sc) in enumerate(rows)]

        # The sheet is the source of record for what the sheet import wrote:
        # scores changed in the sheet are changed here, trays no longer in the
        # sheet are removed -- BEFORE inserting, so the new rows' phases are
        # derived from the cohort's real set of dates (see _sync_pellet_rows).
        # A dry run moves nothing, so it is told where the two moves above
        # would have put the stored rows.
        self._sync_pellet_rows(
            session, cohort_id, rows, dry_run,
            moved=(dict(move_repairs or {}), tray_moves) if dry_run else None)

        self._insert_pellet_rows(session, cohort_id, rows, dry_run)

    # ------------------------------------------------------------------
    # The sheet wins: keeping what the import wrote in step with the sheet
    # ------------------------------------------------------------------
    # WHY: the import used to only ADD. A score corrected in the sheet kept
    # its old value in the database, and a session typed into the wrong
    # cohort's workbook, imported, then moved to the right workbook stayed in
    # the database under the wrong cohort for good -- a phantom session that
    # no sheet holds. Because a cohort's phases are derived from its set of
    # session dates, one phantom date relabelled that cohort's real sessions
    # (baseline read as training, post-injury tests as baseline) in the
    # database and every export.
    #
    # Now every import makes the rows IT wrote match the sheet: a changed
    # value is changed, a row whose session/tray is gone from the sheet is
    # removed. Rows anyone else wrote (typed in by hand in the GUI, or with no
    # record of who wrote them) are never touched: only rows marked
    # entered_by = SHEET_IMPORT_MARK can be rebuilt from the sheet.
    #
    # The one exception is a read that looks broken: a tab that could not be
    # read, a sheet that parses to nothing, or one that would take away more
    # than SYNC_MAX_REMOVED_FRACTION of what the database holds for the
    # cohort (or overwrite more than SYNC_MAX_CHANGED_FRACTION of its values) is
    # far more likely a tab renamed, a file caught mid-save or columns shifted
    # than a real edit. Then NOTHING is removed or changed in that table (new
    # rows are still added, as before), and the import says so loudly: a
    # parse failure must never wipe data. Changes of up to SYNC_SMALL rows (or
    # trays) never count as "too much": correcting one surgery of a cohort
    # that has four is an edit, not a broken read.
    #
    # A person who has checked the sheet and really did take that much out
    # imports that one cohort with allow_large_removal (mousedb-sheets import
    # --cohort <id> --allow-large-removal), or uses Replace in the GUI's
    # cohort setup, which rebuilds the cohort from the sheet on purpose. Even
    # then a tab that could not be read, or that reads as empty, removes
    # nothing.

    SHEET_IMPORT_MARK = 'excel_import'
    SYNC_MAX_REMOVED_FRACTION = 0.25
    SYNC_MAX_CHANGED_FRACTION = 0.25
    SYNC_SMALL = 5

    # Set by the caller for ONE cohort a person has checked (see above).
    allow_large_removal = False

    # Set by the caller when the workbook being read may not be the cohort's
    # sheet at all: a sentence saying why (sheet_sync sets it when several
    # files match the cohort's sheet name and none is pinned, so the newest was
    # read as a guess). Then NOTHING already stored is changed, moved or
    # removed, in any table, whatever allow_large_removal says: the sheet-wins
    # sync is refused (_sync_guard), and every other step that would change a
    # stored row is held back (_held_back): moving stored scores typed on a
    # wrong date (the rows READ are still re-dated, so they match scores an
    # earlier import already moved), removing a virus prep copied from an earlier cohort's workbook,
    # replacing a placeholder note or start date, recording a death. New rows
    # are still added and EMPTY values filled in (a weight's percentage), as
    # an add-only import always did. WHY: bringing the database in step with
    # a file nobody chose would change and remove rows to match a draft or an
    # old copy. Recorded once as a refusal, even when the cohort has no earlier
    # rows yet, so the cohort shows as NOT SYNCED until a person pins its sheet.
    sync_blocked = None

    # What a surgery row stores from its tab, per type: the values compared
    # with the sheet when the import syncs (_sync_table).
    _SURGERY_FIELDS = {
        'contusion': ('force_kdyn', 'displacement_um', 'velocity_mm_s', 'dwell_time_s',
                      'surgeon', 'anesthesia', 'notes'),
        'tracing': ('virus_name', 'virus_lot', 'volume_nl', 'injection_site',
                    'pre_surgery_weight_grams', 'anesthesia', 'notes', 'survived'),
    }

    def _seen(self, table: str, key, values: Optional[Dict]):
        """Record that the sheet holds a row with this key, and what it says
        (None: the cell holds something, but nothing that can be stored -- the
        stored row is then neither changed nor removed). The first reading of a
        key wins, as it does for the row the import inserts."""
        self.__dict__.setdefault('_seen_rows', {}).setdefault(table, {}).setdefault(key, values)

    def _tab_unread(self, table: str, why: str):
        """A tab feeding ``table`` could not be read: nothing may be removed
        from that table on the strength of this read."""
        self.__dict__.setdefault('_unread_tabs', {}).setdefault(table, why)

    def _sync_guard(self, table: str, unit: str, n_units: int, n_gone: int,
                    n_rows: int, n_changed: int, sheet_empty: bool) -> Optional[str]:
        """Why this table must NOT be brought in step with the sheet on this
        read, or None. n_units/n_gone: the stored units (rows, or trays for
        pellet scores) the import wrote, and how many the sheet no longer has;
        n_rows/n_changed: stored rows and how many of them the sheet would
        OVERWRITE -- a stored value replaced by a different one. Filling in a
        value that was stored empty is not counted: columns typed in later
        (accessory details, backfilled days after the scores) fill blanks,
        while a shifted column or a broken read overwrites values."""
        if self.sync_blocked:
            # First: no other reason, and no permission, matters when the
            # workbook may not be the cohort's sheet (see sync_blocked).
            return self.sync_blocked
        unread = self.__dict__.get('_unread_tabs', {}).get(table)
        if unread:
            return unread
        if sheet_empty:
            return ("the sheet read as holding none of them, while the database holds "
                    "%d %s the import wrote for this cohort" % (n_units, unit))
        if self.allow_large_removal:
            return None
        if n_gone > self.SYNC_SMALL and n_gone > self.SYNC_MAX_REMOVED_FRACTION * n_units:
            return ("the sheet would remove %d of the %d %s the import wrote for this "
                    "cohort (more than %d%%), which looks like a broken read rather than an "
                    "edit" % (n_gone, n_units, unit, round(100 * self.SYNC_MAX_REMOVED_FRACTION)))
        if n_changed > self.SYNC_SMALL and n_changed > self.SYNC_MAX_CHANGED_FRACTION * n_rows:
            return ("the sheet would overwrite values in %d of the %d rows the import wrote "
                    "for this cohort (more than %d%%), which looks like shifted columns rather "
                    "than corrections" % (n_changed, n_rows, round(100 * self.SYNC_MAX_CHANGED_FRACTION)))
        return None

    def _held_back(self, what: str) -> bool:
        """True when the workbook may not be the cohort's sheet (sync_blocked):
        the caller then does NOT make the change ``what`` describes to a stored
        row. Each change held back is listed once, under the result's
        sheet_sync.held_back and as a warning, so the person who pins the sheet
        can see what the next import will do. False otherwise."""
        if not self.sync_blocked:
            return False
        held = self._sync_state().setdefault('held_back', [])
        text = self._ascii("HELD BACK until the cohort's sheet is pinned: %s" % what)
        if text not in held:
            held.append(text)
            self.warnings.append(text)
        return True

    def _sync_state(self) -> Dict:
        """This import's record of what the sheet changed (see _get_result)."""
        return self.__dict__.setdefault('_sheet_sync', {})

    def _sync_refuse(self, table: str, why: str):
        """Record a refusal to remove or change anything in one table, as the
        FIRST warning (the ledger keeps only the first warnings) and under the
        result's sheet_sync.refused. A refusal because the workbook may not be
        the cohort's sheet (sync_blocked) is said ONCE for every table, with
        what to do about it: pin the sheet."""
        # By name too, for the steps that follow the importer: the sheet import
        # leaves a refused table's animal-detail tabs as they were (sheet_sync).
        self._sync_state().setdefault('refused_tables', []).append(table)
        if self.sync_blocked and why == self.sync_blocked:
            if self.__dict__.get('_blocked_said'):
                return
            self.__dict__['_blocked_said'] = True
            text = self._ascii(
                "NOT SYNCED any table: %s. Nothing already stored for this cohort was "
                "removed, changed or moved; new rows were still added and empty "
                "values filled in. Say which "
                "file is the sheet with 'mousedb-sheets pin <cohort> \"<file name>\"' "
                "(or the Tracking Sheets tab), then import again." % why)
            self._sync_state().setdefault('refused', []).append(text)
            self.warnings.insert(0, text)
            return
        text = self._ascii(
            "NOT SYNCED %s: %s. Nothing was removed or changed in %s for this "
            "cohort; new rows were still added. If the sheet is wrong, fix it and "
            "the next import syncs. If the sheet is right and really lost that "
            "much, import this cohort alone with 'mousedb-sheets import --cohort "
            "<cohort> --allow-large-removal', or use Replace in the GUI's cohort "
            "setup." % (table, why, table))
        self._sync_state().setdefault('refused', []).append(text)
        self.warnings.insert(0, text)

    def _audit(self, session, action: str, table: str, record_id, old=None, new=None):
        """One audit-log entry (table audit_log and logs/<date>_changes.jsonl)
        per row the sync changes or removes, committed with the change. A
        database object without log_change (a test double) logs nothing."""
        log = getattr(self.db, 'log_change', None)
        if callable(log):
            log(session, action, table, str(record_id), old_values=old, new_values=new)

    @classmethod
    def _same_value(cls, a, b) -> bool:
        """Whether a stored value and the sheet's say the same thing: both
        blank, the same number (to rounding), or the same text."""
        if cls._blank(a) or cls._blank(b):
            return cls._blank(a) and cls._blank(b)
        try:
            return abs(float(a) - float(b)) < 1e-9
        except (TypeError, ValueError):
            return str(a).strip() == str(b).strip()

    @classmethod
    def _same_field(cls, field: str, a, b) -> bool:
        """_same_value, except that a weight percentage is compared in one
        unit. WHY: the tray tab's percentage is stored as a fraction (0.87;
        a number above 2 is read as a percent and divided by 100), while the
        weight and ramp tabs' own percentage columns are stored as typed, so
        the database holds both 0.87 and 87 for the same thing. Compared as
        typed, every such row would read as changed and be rewritten."""
        if field in ('weight_percent', 'weight_percent_baseline'):
            a, b = cls._as_fraction(a), cls._as_fraction(b)
        return cls._same_value(a, b)

    @staticmethod
    def _as_fraction(v):
        """A percentage as a fraction: above 2 it was typed as a percent."""
        try:
            x = float(v)
        except (TypeError, ValueError):
            return v
        return x / 100.0 if x > 2 else x

    def _weights_as_the_sheet_says(self, seen: Dict) -> Dict:
        """The weights the sheet holds, with each one's percentage as the
        import STORES it, for the sheet-wins sync.

        WHY: until this was added the sync compared the grams only, so a
        percentage corrected in the sheet kept its old value in the database
        (and the exports) for good. The percentage reaches a weight row two
        ways: the tab that writes the row stores its own percentage column
        when it has one (1_Weight 'Weight %', the long 3d/3e layout's
        'Weight_Pct_Baseline', the ramp tab's '% body weight'), and when that
        gives none, the tray tab's 'Weight %' fills it in
        (_import_weight_percent_from_tray). So the sheet's value for one key
        is the first reading's own percentage, else the tray's -- the same
        order. A key for which no tab gives a percentage carries none here,
        so an emptied percentage cell leaves the stored one as it is (it may
        have come from the other tab)."""
        tray = self.__dict__.get('_tray_weight_percent', {})
        out = {}
        for key, said in seen.items():
            if not said:
                out[key] = said
                continue
            said = dict(said)
            pct = said.get('weight_percent')
            if pct is None:
                pct = tray.get(key)
            if pct is None:
                said.pop('weight_percent', None)
            else:
                said['weight_percent'] = pct
            out[key] = said
        return out

    # table -> (model, date column, key of a stored row, description of a key)
    _SYNCED_TABLES = {
        'weights': (Weight, 'date',
                    lambda w: (w.subject_id, w.date),
                    lambda k: {'subject_id': k[0], 'date': _iso(k[1])}),
        'ramp_entries': (RampEntry, 'date',
                         lambda r: (r.subject_id, r.date),
                         lambda k: {'subject_id': k[0], 'date': _iso(k[1])}),
        'surgeries': (Surgery, 'surgery_date',
                      lambda s: (s.subject_id, s.surgery_date, s.surgery_type),
                      lambda k: {'subject_id': k[0], 'date': _iso(k[1]), 'type': k[2]}),
    }

    def _sync_table(self, session, cohort_id: str, table: str, dry_run: bool):
        """Bring one table's sheet-imported rows for this cohort in step with
        what this import read from the sheet (recorded by _seen): a stored
        value the sheet now gives differently is changed, a row the sheet no
        longer holds is removed -- under the same rules and guard as the pellet
        scores (see "The sheet wins" above). Rows typed in by hand are never
        touched; when the sheet says otherwise for one, a warning says which
        value stands. Every change and removal goes to the audit log with its
        old and new values and is listed in the result's sheet_sync."""
        model, _date_col, key_of, describe = self._SYNCED_TABLES[table]
        seen = self.__dict__.get('_seen_rows', {}).get(table, {})
        stored = session.query(model).filter(model.subject_id.in_(
            session.query(Subject.subject_id).filter_by(cohort_id=cohort_id))).all()
        ours = [r for r in stored if r.entered_by == self.SHEET_IMPORT_MARK]
        if not ours:
            return

        if table == 'weights':
            seen = self._weights_as_the_sheet_says(seen)

        def fields_of(r):
            if table == 'weights':
                return ('weight_grams', 'weight_percent')
            if table == 'ramp_entries':
                return ('body_weight_grams', 'weight_percent_baseline',
                        'tray_start_grams', 'tray_end_grams', 'food_consumed_grams')
            return self._SURGERY_FIELDS.get(r.surgery_type, ())

        for r in stored:
            if r.entered_by == self.SHEET_IMPORT_MARK:
                continue
            said = seen.get(key_of(r))
            if said and any(f in said and not self._same_field(f, getattr(r, f), said[f])
                            for f in fields_of(r)):
                self._warn_once(
                    ('hand-kept', table, key_of(r)),
                    "%s: %s was typed in by hand (by %s) and differs from the sheet; the "
                    "hand-typed values stand -- correct them by hand if the sheet is right"
                    % (table, ' '.join(str(v) for v in describe(key_of(r)).values()),
                       r.entered_by or 'no record of who'))

        gone, changed = [], []
        for r in ours:
            k = key_of(r)
            if k not in seen:
                gone.append(r)
                continue
            said = seen[k]
            if said is None:
                continue  # present, but unreadable: left as it is
            diff = {f: (getattr(r, f), said[f]) for f in fields_of(r)
                    if f in said and not self._same_field(f, getattr(r, f), said[f])}
            if diff:
                changed.append((r, diff))

        # Rows whose stored value would be OVERWRITTEN (not merely filled in):
        # what the shifted-column guard counts (see _sync_guard).
        overwritten = sum(1 for _r, diff in changed
                          if any(not self._blank(old) for old, _new in diff.values()))
        why = self._sync_guard(table, 'rows', len(ours), len(gone), len(ours),
                               overwritten, not seen)
        if why:
            self._sync_refuse(table, why)
            return
        if not gone and not changed:
            return

        report = self._sync_state().setdefault(table, {})
        for r, diff in changed:
            report.setdefault('changed', []).append(dict(
                describe(key_of(r)),
                values={f: {'old': _jsonable(o), 'new': _jsonable(n)}
                        for f, (o, n) in diff.items()}))
            if not dry_run:
                self._audit(session, 'UPDATE', table, r.id,
                            {f: _jsonable(o) for f, (o, n) in diff.items()},
                            {f: _jsonable(n) for f, (o, n) in diff.items()})
                for f, (_o, n) in diff.items():
                    setattr(r, f, n)
        for r in gone:
            old = {c.name: _jsonable(getattr(r, c.name)) for c in model.__table__.columns}
            report.setdefault('removed', []).append(dict(
                describe(key_of(r)),
                values={f: _jsonable(getattr(r, f)) for f in fields_of(r)}))
            if not dry_run:
                self._audit(session, 'DELETE', table, r.id, old=old)
                session.delete(r)
            if table == 'surgeries' and r.surgery_type == 'contusion':
                self._undo_copied_death(session, r, dry_run)
        if not dry_run:
            session.flush()
        if changed:
            self.imported_counts['%s_changed' % table] = len(changed)
        if gone:
            self.imported_counts['%s_removed' % table] = len(gone)

    def _undo_copied_death(self, session, surgery, dry_run: bool):
        """A contusion row being removed may be what marked its animal dead:
        the contusion reader sets is_active = 0 and date_of_death = the
        surgery's date when the row says the animal did not survive. A row
        copied in from another cohort's workbook therefore also copied that
        cohort's deaths onto living animals. When the removed row is the one
        that did it -- the animal is marked inactive, its death is dated that
        surgery's day, and nothing in this sheet marks it dead now -- the
        death is undone, logged in the audit log and listed in sheet_sync."""
        subject = session.query(Subject).filter_by(subject_id=surgery.subject_id).first()
        if (subject is None or subject.is_active not in (0, False)
                or subject.date_of_death != surgery.surgery_date
                or subject.subject_id in self.__dict__.get('_marked_dead', set())):
            return
        report = self._sync_state().setdefault('subjects', {})
        report.setdefault('death_undone', []).append({
            'subject_id': subject.subject_id, 'date_of_death': _iso(subject.date_of_death),
            'because': 'the contusion row of that day, which said the animal did not '
                       'survive, is no longer in the sheet'})
        if not dry_run:
            self._audit(session, 'UPDATE', 'subjects', subject.subject_id,
                        {'is_active': 0, 'date_of_death': _iso(subject.date_of_death)},
                        {'is_active': 1, 'date_of_death': None})
            subject.is_active = 1
            subject.date_of_death = None

    def _refuse_unread_tray_tab(self, session, cohort_id: str, why: str):
        """The tray tab could not be read at all: if the database holds scores
        the import wrote for this cohort, say loudly that none were synced."""
        held = session.query(PelletScore.id).filter(
            PelletScore.entered_by == self.SHEET_IMPORT_MARK,
            PelletScore.subject_id.in_(
                session.query(Subject.subject_id).filter_by(cohort_id=cohort_id))).first()
        if held is not None:
            self._sync_refuse('pellet_scores', why)

    def _sync_pellet_rows(self, session, cohort_id: str, rows, dry_run: bool, moved=None):
        """Make this cohort's sheet-imported pellet scores match the parsed
        sheet (rows: (subject, date, tray type, tray number, {pellet: score
        or None for an unreadable cell})).

        * a stored score whose cell holds a different score is changed;
        * a stored score whose TRAY (animal, date, tray) is no longer in the
          sheet is removed, and so is one whose cell in a tray still in the
          sheet is now empty. An unreadable cell is not an empty one: its
          stored score stays (a warning already names the cell);
        * a score typed in by hand is never touched; when the sheet says
          something else for it, a warning says which value stands.
        Every change and removal goes to the audit log with its old and new
        value, is counted in imported_counts and listed in the result's
        sheet_sync. ``moved`` (dry run only): (year-typo date map, tray move
        map) -- where the date repairs would have put stored rows."""
        sheet: Dict = {}
        for s, d, tt, tn, sc in rows:
            tray = sheet.setdefault((s, d, tt, tn), {})
            for p, v in sc.items():
                if p in tray and tray[p] != v and tray[p] is not None and v is not None:
                    self._warn_once(
                        ('dup-tray', s, d, tt, tn, p),
                        "3b_Manual_Tray: %s %s %s%s pellet %s is on two rows with different "
                        "scores (%s and %s); the first is used. Delete the extra row."
                        % (s, d, tt, tn, p, tray[p], v))
                    continue
                tray.setdefault(p, v)

        cohort_subjects = session.query(Subject.subject_id).filter_by(cohort_id=cohort_id)
        stored = session.query(PelletScore).filter(
            PelletScore.subject_id.in_(cohort_subjects)).all()
        if not stored:
            return
        date_map, tray_map = moved or ({}, {})

        def key_of(ps):
            d = date_map.get(ps.session_date, ps.session_date)
            d = tray_map.get((ps.subject_id, d, ps.tray_type, ps.tray_number), d)
            return (ps.subject_id, d, ps.tray_type, ps.tray_number)

        ours = [ps for ps in stored if ps.entered_by == self.SHEET_IMPORT_MARK]
        theirs = [ps for ps in stored if ps.entered_by != self.SHEET_IMPORT_MARK]

        for ps in theirs:
            v = sheet.get(key_of(ps), {}).get(ps.pellet_number)
            if v is not None and v != ps.score:
                self._warn_once(
                    ('hand-kept', ps.subject_id, ps.session_date, ps.tray_type, ps.tray_number),
                    "3b_Manual_Tray: %s %s %s%s has scores typed in by hand (by %s) that "
                    "differ from the sheet; the hand-typed values stand -- correct them by "
                    "hand if the sheet is right" % (
                        ps.subject_id, ps.session_date, ps.tray_type, ps.tray_number,
                        ps.entered_by or 'no record of who'))

        changed, gone_cells, gone_trays = [], [], {}
        for ps in ours:
            k = key_of(ps)
            tray = sheet.get(k)
            if tray is None:
                gone_trays.setdefault(k, []).append(ps)
            elif ps.pellet_number not in tray:
                gone_cells.append(ps)
            elif tray[ps.pellet_number] is not None and tray[ps.pellet_number] != ps.score:
                changed.append((ps, tray[ps.pellet_number]))

        if not ours:
            return
        stored_trays = {key_of(ps) for ps in ours}
        why = self._sync_guard('pellet_scores', 'trays', len(stored_trays), len(gone_trays),
                               len(ours), len(changed) + len(gone_cells), not sheet)
        if why:
            self._sync_refuse('pellet_scores', why)
            return

        report = self._sync_state().setdefault('pellet_scores', {})
        for ps, new in changed:
            report.setdefault('changed', []).append({
                'subject_id': ps.subject_id, 'date': _iso(ps.session_date),
                'tray': '%s%s' % (ps.tray_type, ps.tray_number),
                'pellet': ps.pellet_number, 'old': ps.score, 'new': new})
            if not dry_run:
                self._audit(session, 'UPDATE', 'pellet_scores', ps.id,
                            {'score': ps.score}, {'score': new})
                ps.score = new
        for ps in gone_cells:
            report.setdefault('removed_cells', []).append({
                'subject_id': ps.subject_id, 'date': _iso(ps.session_date),
                'tray': '%s%s' % (ps.tray_type, ps.tray_number),
                'pellet': ps.pellet_number, 'old': ps.score})
        for (s, d, tt, tn), pss in sorted(gone_trays.items()):
            pss.sort(key=lambda p: p.pellet_number)
            report.setdefault('removed_trays', []).append({
                'subject_id': s, 'date': _iso(d), 'tray': '%s%s' % (tt, tn),
                'pellets': len(pss),
                # The removed scores, pellet by pellet: '1:2 2:0 ...'
                'old_scores': ' '.join('%d:%d' % (p.pellet_number, p.score) for p in pss)})
        removed = gone_cells + [ps for pss in gone_trays.values() for ps in pss]
        if not dry_run:
            for ps in removed:
                self._audit(session, 'DELETE', 'pellet_scores', ps.id, old={
                    'subject_id': ps.subject_id, 'session_date': _iso(ps.session_date),
                    'tray_type': ps.tray_type, 'tray_number': ps.tray_number,
                    'pellet_number': ps.pellet_number, 'score': ps.score,
                    'test_phase': ps.test_phase, 'entered_by': ps.entered_by})
                # session.delete, not a bulk delete: the database's phase hook
                # sees the removal and re-derives the cohort's phases after
                # the commit (Database._register_phase_hooks).
                session.delete(ps)
            session.flush()
        if changed:
            self.imported_counts['pellet_scores_changed'] = len(changed)
        if removed:
            self.imported_counts['pellet_scores_removed'] = len(removed)
        # A date with no stored score left changes the cohort's set of session
        # dates, and with it the phase of every session (phases.py).
        removed_ids = {id(ps) for ps in removed}
        left = {ps.session_date for ps in stored if id(ps) not in removed_ids}
        dates_gone = sorted({ps.session_date for ps in removed} - left
                            - {k[1] for k in sheet})
        if dates_gone:
            report['dates_removed'] = [_iso(d) for d in dates_gone]
        if not report:
            self._sync_state().pop('pellet_scores', None)

    def _report_block_date_repairs(self, cohort_id: str, rows, islands: Dict):
        """One REPAIRED warning per weekend date read as its block's day, naming
        each animal and tray, so the sheet gets corrected."""
        by_date: Dict = {}
        for i, meant in sorted(islands.items()):
            subject, typed, tray_type, tray_number, _ = rows[i]
            by_date.setdefault((typed, meant), []).append(f"{subject} {tray_type}{tray_number}")
        for (typed, meant), which in sorted(by_date.items()):
            key = (cohort_id, '3b_Manual_Tray', 'block', typed, meant)
            if key in self._reported_date_repairs:
                continue
            self._reported_date_repairs.add(key)
            self.warnings.append(
                f"REPAIRED {cohort_id} 3b_Manual_Tray: rows {', '.join(which)} are dated "
                f"{typed} (a {typed.strftime('%A')}, when nothing is recorded) but sit among "
                f"rows dated {meant}, and are read as {meant}. The date cells are formulas "
                f"counting a fixed number of rows up the sheet; a cut or blank row made them "
                f"land on another day. Type the date into those cells.")

    def _move_misdated_tray_rows(self, session, cohort_id: str, repairs: Dict, dry_run: bool):
        """Re-date imported pellet scores of particular trays: repairs maps
        (animal, date as filed, tray type, tray number) to the date meant. Only
        rows the sheet import wrote are moved; a score already present on the
        meant date is left where it is and reported, never overwritten."""
        moved = 0
        for (subject, typed, tray_type, tray_number), meant in repairs.items():
            stale = session.query(PelletScore).filter(
                PelletScore.subject_id == subject, PelletScore.session_date == typed,
                PelletScore.tray_type == tray_type, PelletScore.tray_number == tray_number,
                PelletScore.entered_by == 'excel_import',
            ).all()
            for ps in stale:
                clash = session.query(PelletScore).filter_by(
                    subject_id=ps.subject_id, session_date=meant, tray_type=ps.tray_type,
                    tray_number=ps.tray_number, pellet_number=ps.pellet_number).first()
                if clash is not None:
                    self.warnings.append(
                        f"{ps.subject_id} {ps.tray_type}{ps.tray_number} pellet "
                        f"{ps.pellet_number}: a score is filed under both {typed} and "
                        f"{meant}; the {typed} one was left as it is")
                    continue
                if not dry_run:
                    ps.session_date = meant
                moved += 1
        if moved:
            self.imported_counts['pellet_scores_redated'] = (
                self.imported_counts.get('pellet_scores_redated', 0) + moved)
            if not dry_run:
                session.flush()

    def _tab_date_repairs(self, cohort_id: str, tab: str, dates) -> Dict:
        """The year typos in one tab's dates (date_typos.year_typo_repairs),
        each reported once per import as a warning naming the tab, the date as
        typed and the date it is read as -- so the sheet gets corrected."""
        from .date_typos import year_typo_repairs
        repairs = year_typo_repairs(dates)
        for typed, meant in sorted(repairs.items()):
            if (cohort_id, tab, typed) in self._reported_date_repairs:
                continue
            self._reported_date_repairs.add((cohort_id, tab, typed))
            self.warnings.append(
                f"REPAIRED {cohort_id} {tab}: date {typed} read as {meant} -- a year "
                f"typo (every other date on the tab is in {meant.year}, and this one "
                f"falls among them with its year corrected). Correct it in the sheet.")
        return repairs

    def _guess_reads_as_typed(self, session, key, meant) -> bool:
        """On a guess (sync_blocked), whether a read row of the tray ``key`` --
        (animal, date as typed, tray type, tray number) -- keeps its typed date
        instead of being re-dated to ``meant``.

        True only when an earlier sheet import (entered_by = SHEET_IMPORT_MARK)
        still has this tray stored under the typed date AND has none of it
        under the date meant: the stored rows are not moved on a guess, so the
        read row is kept beside them, where it matches them. Every other row
        is re-dated to the date meant, where an earlier import's move put it.

        WHY only the import's own rows: those are the only rows the repair
        ever moves, so they are the only sign of where the import filed this
        tray. A score a person typed in under the typed date is never moved
        and says nothing about it; counting it read the whole tray as typed
        and stored its other pellets a second time, a year away. WHY "none
        under the date meant": a tray whose move left one pellet behind (the
        date meant already had that pellet) is filed under the date meant;
        the leftover must not pull the rest of the tray back."""
        subject_id, typed, tray_type, tray_number = key

        def stored(day):
            return session.query(PelletScore.subject_id).filter(
                PelletScore.subject_id == subject_id, PelletScore.session_date == day,
                PelletScore.tray_type == tray_type, PelletScore.tray_number == tray_number,
                PelletScore.entered_by == self.SHEET_IMPORT_MARK).first() is not None

        return stored(typed) and not stored(meant)

    def _tray_key(self, subject_id, date_val, row):
        """(animal, date, tray type, tray number) for one 3b_Manual_Tray row,
        parsed as _import_pellet_scores parses it; None when the tray cell
        cannot be read."""
        tray_info = row.get('Tray Type/Number') or row.get('Tray')
        if tray_info is None or pd.isna(tray_info):
            return None
        tray_str = str(tray_info).strip()
        try:
            return (subject_id, date_val, tray_str[0].upper(), int(tray_str[1:]))
        except (IndexError, ValueError):
            return None

    def _move_misdated_pellet_rows(self, session, cohort_id: str, repairs: Dict, dry_run: bool):
        """Re-date this cohort's imported pellet scores filed under a typo'd
        date. Only rows the sheet import wrote (entered_by='excel_import') are
        moved; a score already present on the corrected date is left where it
        is and reported, never overwritten or deleted."""
        cohort_subjects = session.query(Subject.subject_id).filter_by(cohort_id=cohort_id)
        moved = 0
        for typed, meant in repairs.items():
            stale = session.query(PelletScore).filter(
                PelletScore.session_date == typed,
                PelletScore.entered_by == 'excel_import',
                PelletScore.subject_id.in_(cohort_subjects),
            ).all()
            for ps in stale:
                clash = session.query(PelletScore).filter_by(
                    subject_id=ps.subject_id, session_date=meant, tray_type=ps.tray_type,
                    tray_number=ps.tray_number, pellet_number=ps.pellet_number).first()
                if clash is not None:
                    self.warnings.append(
                        f"{ps.subject_id} {ps.tray_type}{ps.tray_number} pellet "
                        f"{ps.pellet_number}: a score is filed under both {typed} and "
                        f"{meant}; the {typed} one was left as it is")
                    continue
                if not dry_run:
                    ps.session_date = meant
                moved += 1
        if moved:
            self.imported_counts['pellet_scores_redated'] = (
                self.imported_counts.get('pellet_scores_redated', 0) + moved)
            if not dry_run:
                session.flush()

    def _insert_pellet_rows(self, session, cohort_id: str, rows, dry_run: bool):
        """Insert parsed pellet rows -- (subject_id, date, tray_type, tray_number,
        {pellet: score}) -- with phases derived first. Shared by the CNT
        tracking-sheet path above and the ASPA workbook importer
        (cohort_tools.import_aspa_scores), so both cohorts' scores go through
        the same phase derivation, dedup and subject creation."""
        if not rows:
            return

        # PASS 2: derive each date's phase BEFORE inserting. pellet_scores.
        # test_phase is NOT NULL in the live database (older DDL than the ORM,
        # which allows NULL) -- the previous insert-NULL-then-update design
        # violated it and rolled back every import of a new cohort, silently,
        # for the hourly sheet import too. The date set is this sheet's dates
        # plus whatever the db already holds for the cohort, so the gap
        # structure is computed over the same union _assign_pellet_phases uses.
        pairs = {(d, t) for _, d, t, _, _ in rows}
        if not dry_run:
            pairs |= {
                (d, t) for d, t in
                session.query(PelletScore.session_date, PelletScore.tray_type)
                .join(Subject, Subject.subject_id == PelletScore.subject_id)
                .filter(Subject.cohort_id == cohort_id).distinct().all()
            }
        phase_by_date = {a.session_date: a for a in assign_phases_for_cohort(sorted(pairs))}

        for subject_id, date_val, tray_type, tray_number, scores in rows:
            if not self._ensure_subject(session, subject_id, cohort_id, dry_run,
                                        first_seen=date_val):
                continue  # refused; _ensure_subject recorded why
            a = phase_by_date.get(date_val)
            test_phase = a.test_phase if a else 'unassigned'
            phase_group = a.phase_group if a else None
            if a is None:
                self.warnings.append(
                    f"No phase derivable for {cohort_id} session {date_val} "
                    f"({tray_type}) -- stored as 'unassigned'")

            for pellet_num, score in scores.items():
                if score is None:
                    continue  # a cell with something unreadable in it (see _import_pellet_scores)
                existing = session.query(PelletScore).filter_by(
                    subject_id=subject_id,
                    session_date=date_val,
                    tray_type=tray_type,
                    tray_number=tray_number,
                    pellet_number=pellet_num
                ).first()
                if existing:
                    continue

                pellet_score = PelletScore(
                    subject_id=subject_id,
                    session_date=date_val,
                    tray_type=tray_type,
                    tray_number=tray_number,
                    pellet_number=pellet_num,
                    score=score,
                    test_phase=test_phase,
                    phase_group=phase_group,
                    entered_by='excel_import',
                )

                if not dry_run:
                    session.add(pellet_score)
                self.imported_counts['pellet_scores'] += 1

        if not dry_run:
            session.flush()
            # Normalization pass over the whole cohort (legacy rows included).
            self._assign_pellet_phases(session, cohort_id)

    def _assign_pellet_phases(self, session, cohort_id: str):
        """
        Derive test_phase/phase_group for all pellet_scores rows in a cohort.

        Uses gap-structure detection (:func:`mousedb.phases.assign_phases_for_cohort`)
        over the union of session dates+tray types observed in pellet_scores. Always
        overwrites any existing values so legacy inconsistent labels are normalized.
        """
        pairs = (
            session.query(PelletScore.session_date, PelletScore.tray_type)
            .join(Subject, Subject.subject_id == PelletScore.subject_id)
            .filter(Subject.cohort_id == cohort_id)
            .distinct()
            .all()
        )
        if not pairs:
            return

        assignments = assign_phases_for_cohort(pairs)
        from sqlalchemy import or_
        relabelled = 0
        for a in assignments:
            # Only rows whose label is not already right are written (and
            # counted): the count is reported as the pellet rows this import
            # relabelled (see import_cohort_file, phases_rederived).
            relabelled += session.query(PelletScore).filter(
                PelletScore.session_date == a.session_date,
                PelletScore.subject_id.in_(
                    session.query(Subject.subject_id).filter_by(cohort_id=cohort_id)
                ),
                or_(PelletScore.test_phase.is_(None), PelletScore.test_phase != a.test_phase,
                    PelletScore.phase_group.is_(None), PelletScore.phase_group != a.phase_group),
            ).update(
                {"test_phase": a.test_phase, "phase_group": a.phase_group},
                synchronize_session=False,
            ) or 0
        if relabelled:
            self.__dict__['_pellets_relabelled'] = (
                self.__dict__.get('_pellets_relabelled', 0) + relabelled)

    def _import_surgeries(self, xl: pd.ExcelFile, cohort_id: str,
                          session, surgery_type: str, dry_run: bool):
        """Import surgery details sheet."""
        sheet_name = '4_Contusion_Injury_Details' if surgery_type == 'contusion' else '5_SC_Injection_Details'

        try:
            df = pd.read_excel(xl, sheet_name=sheet_name)
        except Exception as e:
            self.warnings.append(f"Failed to read {sheet_name}: {e}")
            self._tab_unread('surgeries', "the %s tab could not be read (%s)" % (sheet_name, e))
            return
        if not self._surgery_tab_has_weight_column(df, sheet_name):
            if (df.notna().sum(axis=1) >= 2).any():
                self._tab_unread('surgeries', "the %s tab has no weight column, so no row "
                                              "on it could be read" % sheet_name)
            return
        # The gate judges the cells as typed; the stored values come from df.
        as_typed = self._surgery_rows_as_typed(xl, sheet_name)

        for idx, row in df.iterrows():
            cells = as_typed.get(idx, row)
            raw_id = (row.get('Subject_ID') or row.get('Animal')
                      or row.get('SubjectID') or row.get('Mouse ID'))
            if self._blank(raw_id):
                # Padding stays silent; a weighed row is reported.
                self._note_blank_animal_surgery(cells, idx, sheet_name)
                continue

            # Plans (virus, titre, target, depths and drugs are pre-filled for
            # every animal), held rows and foreign animals stop here, each
            # announced as the policy says (see _performed_surgery).
            performed = self._performed_surgery(cells, raw_id, cohort_id, sheet_name)
            if performed is None:
                continue
            subject_id, date_val, grams = performed

            # Ensure subject exists (False: refused, and a warning says why)
            if not self._ensure_subject(session, subject_id, cohort_id, dry_run,
                                        first_seen=date_val):
                continue

            surgery = Surgery(
                subject_id=subject_id,
                surgery_date=date_val,
                surgery_type=surgery_type,
                entered_by='excel_import',
            )

            # Add type-specific fields
            if surgery_type == 'contusion':
                surgery.force_kdyn = self._parse_float(row.get('Force_kDyn') or row.get('Force')
                                                       or row.get('Actual_kd'))
                surgery.displacement_um = self._parse_float(row.get('Displacement_um')
                                                            or row.get('Displacement')
                                                            or row.get('Actual_displacement'))
                surgery.velocity_mm_s = self._parse_float(row.get('Velocity_mm_s')
                                                          or row.get('Velocity')
                                                          or row.get('Actual_Velocity'))
                surgery.surgeon = row.get('Surgeon') if pd.notna(row.get('Surgeon')) else None
            elif surgery_type == 'tracing':
                # Map Excel column names from 5_SC_Injection_Details
                surgery.virus_name = (row.get('Injected_Virus') or row.get('Virus_Name')
                                      if pd.notna(row.get('Injected_Virus') or row.get('Virus_Name'))
                                      else None)
                surgery.virus_lot = (row.get('Virus_Titer')
                                     if pd.notna(row.get('Virus_Titer')) else None)
                surgery.volume_nl = self._parse_float(
                    row.get('Volume_nL') or row.get('Volume')
                    or row.get('Injection_Volume_nL'))
                surgery.injection_site = (
                    row.get('Injection_Target') or row.get('Injection_Site')
                    if pd.notna(row.get('Injection_Target') or row.get('Injection_Site'))
                    else None)
                # The weight that proved the surgery (read by the shared rule),
                # so '22.4 g' or a weight under another accepted heading is
                # stored, not proved and then stored as blank.
                surgery.pre_surgery_weight_grams = grams
                surgery.anesthesia = (row.get('Anesthetic')
                                      if pd.notna(row.get('Anesthetic')) else None)
                # Build notes from coordinate fields
                notes_parts = []
                for field, label in [('Depths (D/V)', 'D/V'),
                                     ('Coordinates (M/L)', 'M/L'),
                                     ('Signal Post Perfusion', 'Signal')]:
                    val = row.get(field)
                    if pd.notna(val):
                        notes_parts.append(f"{label}: {val}")
                if notes_parts:
                    surgery.notes = '; '.join(notes_parts)

            # Survived field (both types)
            survived_val = row.get('Survived')
            if pd.notna(survived_val):
                surgery.survived = 1 if str(survived_val).strip().upper() in ('Y', 'YES', '1', 'TRUE') else 0

            # What the sheet says for this surgery, which an earlier import's
            # row is also compared with (_sync_table). Read off the object
            # built above, so the two can never disagree.
            self._seen('surgeries', (subject_id, date_val, surgery_type),
                       {f: getattr(surgery, f) for f in self._SURGERY_FIELDS[surgery_type]})

            # Check for duplicate
            existing = session.query(Surgery).filter_by(
                subject_id=subject_id,
                surgery_date=date_val,
                surgery_type=surgery_type
            ).first()
            if existing:
                continue

            if not dry_run:
                session.add(surgery)
            self.imported_counts['surgeries'] += 1

    def _import_injection_calculations(self, xl: pd.ExcelFile, cohort_id: str,
                                         session, dry_run: bool):
        """Import 0_Injection_Calculations sheet -> VirusPrep table."""
        try:
            df = pd.read_excel(xl, sheet_name='0_Injection_Calculations', header=None)
        except Exception as e:
            self.warnings.append(f"Failed to read 0_Injection_Calculations: {e}")
            return

        if df.empty or df.shape[0] < 2:
            self.warnings.append("0_Injection_Calculations: sheet is empty or too small")
            return

        # The lab's required layout (batch blocks -- see
        # cohort_tools.make_sheets.INJECTION_CALC_HEADERS) is tried first and
        # wins if present anywhere on the sheet. The two legacy layouts are
        # kept only for cohorts written before it was settled.
        if self._import_injection_batches(df, cohort_id, session, dry_run):
            return

        first_cell = str(df.iloc[0, 0]).strip().lower() if pd.notna(df.iloc[0, 0]) else ''

        if 'parameter' in first_cell:
            # Legacy vertical Parameter/Value/Units calculator
            self._import_injection_vertical(df, cohort_id, session, dry_run)
        else:
            # Legacy single-row horizontal format (CNT_01 style)
            self._import_injection_horizontal(df, cohort_id, session, dry_run)

    def _carried_over_batch(self, cohort_id: str, prep_date, virus_name: str,
                            session) -> Optional[str]:
        """Why a batch block looks copied from an earlier cohort's workbook, or
        None. Two tests, because neither alone is enough: a prep dated before its
        cohort began cannot be that cohort's, but a copy made into a cohort already
        under way is dated after its start; and the same date and the same viruses
        already recorded under an EARLIER cohort is a copy, whatever the dates."""
        cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
        start = cohort.start_date if cohort else None
        if start is not None and prep_date < start:
            return "dated %s, before this cohort began on %s" % (prep_date, start)
        same = session.query(VirusPrep).filter_by(
            prep_date=prep_date, virus_name=virus_name).all()
        earlier = sorted(p.cohort_id for p in same if p.cohort_id < cohort_id)
        if earlier:
            return "the same date and viruses are already recorded for %s" % earlier[0]
        return None

    def _import_injection_batches(self, df, cohort_id: str, session, dry_run: bool) -> bool:
        """Parse every batch block on the sheet into VirusPrep records.

        A block is a title row ("Batch <label> - YYYYMMDD"), a header row
        containing "Solution Parts Total", one row per virus, and a "Totals"
        row. One VirusPrep per BATCH (the schema's unique key is cohort +
        prep_date, and a batch IS one prep): the mix's viruses are joined into
        virus_name, per-virus starting/parts/final/disambiguated/mice go into
        calculation_notes line by line, final_titer is the batch's
        disambiguated total, num_animals the sum of Mouse Quantity, and the
        batch label is kept in preparation_notes.

        Returns True if the sheet is in batch layout (even if every block is
        an unfilled template), False if no batch header exists at all.
        """
        import re

        def cell(r, c):
            if c >= df.shape[1]:
                return None
            v = df.iloc[r, c]
            return None if pd.isna(v) else v

        def text(r, c):
            v = cell(r, c)
            return str(v).strip() if v is not None else ''

        header_rows = [
            r for r in range(df.shape[0])
            if any('solution parts total' in text(r, c).lower()
                   for c in range(df.shape[1]))
        ]
        if not header_rows:
            return False

        for h in header_rows:
            headers = [text(h, c).lower() for c in range(df.shape[1])]

            def col(*keywords):
                for i, name in enumerate(headers):
                    if all(k in name for k in keywords):
                        return i
                return None

            c_virus = col('virus')
            c_start = col('starting')
            c_total = col('solution', 'parts')
            c_parts = col('parts', 'for')
            c_final = col('final')
            c_disamb = col('disambig')
            c_mice = col('mouse')
            if c_virus is None:
                c_virus = 0

            title = text(h - 1, 0) if h > 0 else ''
            label, prep_date = title, None
            m = re.search(r'(\d{8})\s*$', title)
            if m:
                prep_date = self._parse_date(m.group(1))
                label = title[:m.start()].rstrip(' -').strip()
            if label.lower().startswith('batch'):
                label = label[5:].strip(' :-')

            viruses = []
            r = h + 1
            while r < df.shape[0]:
                name = text(r, c_virus)
                if not name or name.lower().startswith('total'):
                    break
                if name.startswith('<') or name.startswith('['):
                    r += 1  # unfilled template placeholder
                    continue
                viruses.append({
                    'name': name,
                    'start': self._parse_float(cell(r, c_start)) if c_start is not None else None,
                    'total_parts': self._parse_float(cell(r, c_total)) if c_total is not None else None,
                    'parts': self._parse_float(cell(r, c_parts)) if c_parts is not None else None,
                    'final': self._parse_float(cell(r, c_final)) if c_final is not None else None,
                    'disamb': self._parse_float(cell(r, c_disamb)) if c_disamb is not None else None,
                    'mice': self._parse_float(cell(r, c_mice)) if c_mice is not None else None,
                })
                r += 1

            if not viruses:
                self.warnings.append(
                    "0_Injection_Calculations: batch %r has no virus rows (template?)"
                    % (title or '(untitled)'))
                continue
            if prep_date is None:
                self.warnings.append(
                    "0_Injection_Calculations: batch %r has no YYYYMMDD date in its "
                    "title -- skipped (the date is the record's key)" % title)
                continue

            existing = session.query(VirusPrep).filter_by(
                cohort_id=cohort_id, prep_date=prep_date
            ).first()
            virus_name = ' + '.join(v['name'] for v in viruses)
            carried = self._carried_over_batch(
                cohort_id, prep_date, virus_name, session)
            if carried:
                # WHY (2026-10-30): a new workbook starts as a copy of an earlier
                # cohort's, and CNT_05's batch reached CNT_06 and CNT_07 that way
                # -- recorded as those cohorts' prep. A row this importer wrote
                # from such a block is taken back, so a sheet that was copied
                # corrects itself; a row a person entered is never touched.
                removed = False
                if (existing and existing.entered_by == 'excel_import'
                        and not self._held_back(
                            "removing %s's earlier import of copied batch %r" % (
                                cohort_id, title))
                        and not dry_run):
                    session.delete(existing)
                    removed = True
                self.warnings.append(
                    "0_Injection_Calculations: batch %r not imported: %s. Clear the block "
                    "or give it this cohort's own date%s"
                    % (title, carried,
                       "; the earlier import of it was removed" if removed else ""))
                continue
            if existing:
                continue

            disamb_total = sum(v['disamb'] for v in viruses if v['disamb'] is not None)
            mice_total = sum(int(v['mice']) for v in viruses if v['mice'] is not None)
            notes = ["%s: start %s, parts %s/%s, final %s (%.3g), mice %s" % (
                v['name'], v['start'], v['parts'], v['total_parts'], v['final'],
                v['disamb'] if v['disamb'] is not None else float('nan'),
                int(v['mice']) if v['mice'] is not None else '-') for v in viruses]

            virus_prep = VirusPrep(
                cohort_id=cohort_id,
                prep_date=prep_date,
                virus_name=virus_name,
                final_titer=disamb_total if disamb_total > 0 else None,
                num_animals=mice_total or None,
                preparation_notes=label or None,
                calculation_notes='\n'.join(notes),
                entered_by='excel_import',
            )
            if not dry_run:
                session.add(virus_prep)
            self.imported_counts['virus_preps'] += 1

        return True

    def _import_injection_horizontal(self, df, cohort_id: str, session, dry_run: bool):
        """Import horizontal injection calculations (CNT_01 format)."""
        if df.shape[0] < 2:
            return

        headers = [str(h).strip() if pd.notna(h) else '' for h in df.iloc[0]]

        for row_idx in range(1, len(df)):
            row = df.iloc[row_idx]

            prep_date = self._parse_date(row.iloc[0]) if len(row) > 0 else None
            if not prep_date:
                continue

            # Check for duplicate
            existing = session.query(VirusPrep).filter_by(
                cohort_id=cohort_id, prep_date=prep_date
            ).first()
            if existing:
                continue

            # Map columns by header name
            def get_col(keywords):
                for i, h in enumerate(headers):
                    h_lower = h.lower()
                    if all(k in h_lower for k in keywords):
                        val = row.iloc[i] if i < len(row) else None
                        return val if pd.notna(val) else None
                return None

            virus_prep = VirusPrep(
                cohort_id=cohort_id,
                prep_date=prep_date,
                virus_name=str(get_col(['virus', 'name'])) if get_col(['virus', 'name']) else 'Unknown',
                virus_source=str(get_col(['source'])) if get_col(['source']) else None,
                stock_titer=self._parse_float(get_col(['starting', 'scinot'])),
                target_titer=self._parse_float(get_col(['target', 'concentration'])),
                final_titer=self._parse_float(get_col(['final', 'scinot'])),
                calculation_notes=f"Parts virus: {get_col(['parts', 'virus'])}, Parts AAV protect: {get_col(['parts', 'aav'])}",
                entered_by='excel_import',
            )

            if not dry_run:
                session.add(virus_prep)
            self.imported_counts['virus_preps'] += 1

    def _import_injection_vertical(self, df, cohort_id: str, session, dry_run: bool):
        """Import vertical injection calculations (CNT_05 format)."""
        # Build parameter -> value dict from rows 1+
        params = {}
        for row_idx in range(1, len(df)):
            param_name = str(df.iloc[row_idx, 0]).strip() if pd.notna(df.iloc[row_idx, 0]) else ''
            param_value = df.iloc[row_idx, 1] if df.shape[1] > 1 else None
            if param_name:
                params[param_name.lower()] = param_value

        # Check if all values are NaN (empty template)
        has_data = any(pd.notna(v) for v in params.values())
        if not has_data:
            self.warnings.append("0_Injection_Calculations: vertical format is empty template")
            return

        def get_param(keywords):
            for key, val in params.items():
                if all(k in key for k in keywords):
                    return val if pd.notna(val) else None
            return None

        virus_name = get_param(['virus', 'name'])
        if not virus_name:
            self.warnings.append("0_Injection_Calculations: no virus name found")
            return

        # The prep's date must come from the sheet. This calculator layout has
        # no date row, and the importer used to stamp the day it ran instead:
        # the prep looked made on whatever day the import happened, and as the
        # date is the record's key (cohort + prep_date), every new day the
        # import ran added another copy. A parameter named like a date is read
        # if someone added one; otherwise the prep is not imported.
        prep_date = None
        for name, value in params.items():
            # 'date' as a word, so 'update notes' is not one -- but bounded by
            # letters only, not \b: '_' is a word character to \b, and the
            # sheets name things Prep_Date / Surgery_Date, which \b would miss
            # while the warning below tells the person to add exactly that.
            if re.search(r'(?<![a-z])date(?![a-z])', name):
                prep_date = self._parse_date(value)
                if prep_date:
                    break
        if prep_date is None:
            self.warnings.append(self._ascii(
                "0_Injection_Calculations: the virus prep for %r was NOT imported -- "
                "this calculator has no date, and a prep is recorded by its date. "
                "Enter it as a dated batch block (a title row ending in YYYYMMDD) "
                "or add a date row to the calculator." % str(virus_name)))
            return

        existing = session.query(VirusPrep).filter_by(
            cohort_id=cohort_id, prep_date=prep_date
        ).first()
        if existing:
            return

        virus_prep = VirusPrep(
            cohort_id=cohort_id,
            prep_date=prep_date,
            virus_name=str(virus_name),
            stock_titer=self._parse_float(get_param(['virus', 'titer'])),
            target_titer=self._parse_float(get_param(['target', 'titer'])),
            injection_volume_nl=self._parse_float(get_param(['volume', 'injection'])),
            num_injection_sites=int(self._parse_float(get_param(['number', 'injection'])) or 0) or None,
            num_animals=int(self._parse_float(get_param(['number', 'animal'])) or 0) or None,
            total_volume_needed_ul=self._parse_float(get_param(['total', 'volume'])),
            stock_volume_ul=self._parse_float(get_param(['volume', 'virus', 'stock'])),
            diluent_volume_ul=self._parse_float(get_param(['volume', 'diluent'])),
            preparation_notes=str(get_param(['notes'])) if get_param(['notes']) else None,
            entered_by='excel_import',
        )

        if not dry_run:
            session.add(virus_prep)
        self.imported_counts['virus_preps'] += 1

    def _import_ladder(self, xl: pd.ExcelFile, cohort_id: str,
                        session, dry_run: bool):
        """Import 6_Ladder results -> LadderEntry, for animals already known.

        The lab abandoned the ladder test, and the tab is routinely half
        filled: an animal, a date and a test type with the step counts blank.
        That is a test that was scheduled, not a result. So:
          * a row is data only if a step count is filled (a number above 0: a
            crossing always takes steps, so an all-zero row is what a formula
            over blank cells shows, not a result). Rows without counts are
            ignored WITHOUT a word, and so is a tab with no rows at all: the
            test was abandoned, its empty rows are expected on every sheet and
            will never be filled in, and a line about them on every import
            would only teach people to skip the warnings.
          * a ladder row never creates an animal. It used to, before reading
            the counts, and so made an animal that existed nowhere else (two
            count-less ladder rows were its only trace). A result for an
            animal the database does not know is skipped with a warning.
        """
        tab = '6_Ladder'
        try:
            df = pd.read_excel(xl, sheet_name=tab, header=None)
        except Exception as e:
            self.warnings.append(f"Failed to read 6_Ladder: {e}")
            return

        if df.shape[0] < 2:
            return  # header only: an abandoned test's tab, never announced

        # Row 0 is headers
        headers = [str(h).strip().lower() if pd.notna(h) else '' for h in df.iloc[0]]

        def find_col(*alternatives):
            # Explicit None checks, never `find_col(...) or 1`: a match in
            # column 0 is falsy, and that idiom read the generated layout's
            # animal column (B) as its date because the date is column A.
            for keywords in alternatives:
                for i, h in enumerate(headers):
                    if all(k in h for k in keywords):
                        return i
            return None

        col_animal = find_col(['animal'])
        col_date = find_col(['date'])
        if col_animal is None:
            col_animal = 0
        if col_date is None:
            col_date = 1
        col_test_type = find_col(['test', 'type'], ['type'])
        # The hand-made tabs head the camera column 'Quality' (it holds the
        # camera's name); the generated tab says 'Camera_Type'.
        col_camera = find_col(['camera'], ['quality'])
        col_succeeded = find_col(['suceeded'], ['succeeded'], ['success'])
        col_missed = find_col(['missed'], ['miss'])
        col_total = find_col(['total'])
        col_pct = find_col(['%'], ['pct'])

        def count(row, col):
            """(number or None, the cell's text when it is not a number)."""
            if col is None or col >= len(row) or self._blank(row.iloc[col]):
                return None, None
            value = self._parse_float(row.iloc[col])
            if value is None:
                return None, str(row.iloc[col]).strip()
            return value, None

        for row_idx in range(1, len(df)):
            row = df.iloc[row_idx]
            if row.isna().all():
                continue  # an empty line, not a ladder row

            counts = {name: count(row, col) for name, col in (
                ('succeeded', col_succeeded), ('missed', col_missed), ('total', col_total))}
            numbers = [v for v, _ in counts.values() if v is not None]
            not_numbers = [t for _, t in counts.values() if t is not None]
            if not not_numbers and not any(v > 0 for v in numbers):
                # A scheduled test, not a result: ignored silently, and no
                # other check (animal, date) runs on it (see the docstring).
                continue

            raw_id = row.iloc[col_animal]
            date_val = self._parse_date(row.iloc[col_date])
            when = str(date_val) if date_val else 'no date'
            if self._blank(raw_id):
                self._note_unimported(
                    tab, "ladder result not imported: the row has step counts but "
                         "no animal id", "sheet row %d (%s)" % (row_idx + 1, when))
                continue
            subject_id = self._resolve_subject_id(raw_id, cohort_id, tab)
            if not subject_id:
                continue  # _resolve_subject_id recorded why
            if not_numbers:
                self._note_unimported(
                    tab, "ladder result not imported: a step-count cell holds "
                         "something that is not a number",
                    "%s (%s): %s" % (subject_id, when, ', '.join(repr(t) for t in not_numbers)))
                continue
            if not date_val:
                self._note_unimported(
                    tab, "ladder result not imported: the row has step counts but "
                         "no date", subject_id)
                continue
            if not self._subject_known(session, subject_id):
                self._note_unimported(
                    tab, "ladder result not imported: the animal has no other record "
                         "(ladder rows never create an animal)",
                    "%s (%s)" % (subject_id, when))
                continue

            # Check for duplicate
            existing = session.query(LadderEntry).filter_by(
                subject_id=subject_id, date=date_val
            ).first()
            if existing:
                continue

            def as_int(name):
                value = counts[name][0]
                return int(value) if value is not None else None

            ladder_entry = LadderEntry(
                subject_id=subject_id,
                date=date_val,
                test_type=str(row.iloc[col_test_type]).strip() if col_test_type is not None and pd.notna(row.iloc[col_test_type]) else None,
                camera_type=str(row.iloc[col_camera]).strip() if col_camera is not None and pd.notna(row.iloc[col_camera]) else None,
                steps_succeeded=as_int('succeeded'),
                steps_missed=as_int('missed'),
                steps_total=as_int('total'),
                miss_pct=self._parse_float(row.iloc[col_pct]) if col_pct is not None else None,
                entered_by='excel_import',
            )

            if not dry_run:
                session.add(ladder_entry)
            self.imported_counts['ladder_entries'] = self.imported_counts.get('ladder_entries', 0) + 1

    def _import_experiment_planning(self, xl: pd.ExcelFile, cohort_id: str,
                                      session, dry_run: bool):
        """Import 1_Experiment_Planning -> cohort notes as JSON archive."""
        try:
            df = pd.read_excel(xl, sheet_name='1_Experiment_Planning', header=None)
        except Exception as e:
            self.warnings.append(f"Failed to read 1_Experiment_Planning: {e}")
            return

        if df.empty:
            return

        # Detect format: check if row 0 has Phase/Start_Day headers
        first_row = [str(h).strip().lower() if pd.notna(h) else '' for h in df.iloc[0]]
        is_structured = 'phase' in first_row and ('start_day' in first_row or 'start day' in first_row)

        planning_data = {}
        if is_structured:
            # CNT_05 structured format
            phases = []
            for row_idx in range(1, len(df)):
                row = df.iloc[row_idx]
                phase_name = str(row.iloc[0]).strip() if pd.notna(row.iloc[0]) else None
                if not phase_name:
                    continue
                phase_info = {
                    'phase': phase_name,
                    'start_day': int(self._parse_float(row.iloc[1]) or 0) if df.shape[1] > 1 else None,
                    'end_day': int(self._parse_float(row.iloc[2]) or 0) if df.shape[1] > 2 else None,
                    'start_date': str(self._parse_date(row.iloc[3])) if df.shape[1] > 3 and pd.notna(row.iloc[3]) else None,
                    'end_date': str(self._parse_date(row.iloc[4])) if df.shape[1] > 4 and pd.notna(row.iloc[4]) else None,
                    'duration_days': int(self._parse_float(row.iloc[5]) or 0) if df.shape[1] > 5 else None,
                    'notes': str(row.iloc[6]).strip() if df.shape[1] > 6 and pd.notna(row.iloc[6]) else None,
                }
                phases.append(phase_info)
            planning_data = {'format': 'structured', 'phases': phases}
        else:
            # Free-form Gantt format - archive all cell values
            gantt_data = []
            for row_idx in range(len(df)):
                row_data = []
                for col_idx in range(len(df.columns)):
                    val = df.iloc[row_idx, col_idx]
                    if pd.notna(val):
                        if isinstance(val, datetime):
                            row_data.append({'col': col_idx, 'value': str(val.date())})
                        else:
                            row_data.append({'col': col_idx, 'value': str(val)})
                if row_data:
                    gantt_data.append({'row': row_idx, 'cells': row_data})
            planning_data = {'format': 'gantt', 'data': gantt_data}

        # Store in cohort notes
        if planning_data and not dry_run:
            cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
            if cohort:
                existing_notes = cohort.notes or ''
                planning_json = json.dumps(planning_data, default=str)
                if '[PLANNING]' not in existing_notes:
                    cohort.notes = (existing_notes + '\n[PLANNING] ' + planning_json).strip()

        self.imported_counts['planning_notes'] += 1

    def _import_manual_summary(self, xl: pd.ExcelFile, cohort_id: str,
                                 session, dry_run: bool, source_file: str = ''):
        """Import 3c_Manual_Summary and 3d_Manual_Summary - survivors -> ArchivedSummary."""
        for sheet_name in ['3c_Manual_Summary', '3d_Manual_Summary - survivors']:
            if sheet_name not in xl.sheet_names:
                continue

            try:
                df = pd.read_excel(xl, sheet_name=sheet_name, header=None)
            except Exception as e:
                self.warnings.append(f"Failed to read {sheet_name}: {e}")
                continue

            if df.shape[0] < 3 or df.shape[1] < 2:
                self.warnings.append(f"{sheet_name}: too small to contain data")
                continue

            # Parse the transposed matrix format
            # Row 0 has block labels (Retrieved/Contacted) and phase names
            # Row 1 has dates
            # Rows 2+ have subject_id in col 0 and values

            current_metric = None  # 'retrieved' or 'contacted'
            date_row = df.iloc[1]

            # Build column -> date mapping (skip Average columns and col 0)
            date_cols = {}
            for col_idx in range(1, len(df.columns)):
                date_val = self._parse_date(date_row.iloc[col_idx])
                if date_val:
                    # Check phase from row 0
                    phase_val = df.iloc[0, col_idx]
                    phase = str(phase_val).strip() if pd.notna(phase_val) else ''
                    if phase.lower() == 'average':
                        continue
                    date_cols[col_idx] = (date_val, phase)

            for row_idx in range(0, len(df)):
                row = df.iloc[row_idx]
                first_cell = str(row.iloc[0]).strip().lower() if pd.notna(row.iloc[0]) else ''

                # Detect metric block
                if first_cell in ('retrieved', 'contacted'):
                    current_metric = first_cell + '_pct'
                    continue

                if not current_metric:
                    continue

                # Try to parse as subject_id
                if self._blank(row.iloc[0]):
                    continue  # blank animal cell: no row here (see _resolve_subject_id)
                subject_id = self._resolve_subject_id(row.iloc[0], cohort_id, sheet_name)
                if not subject_id:
                    continue  # _resolve_subject_id recorded why

                # Read values for each date column
                for col_idx, (date_val, phase) in date_cols.items():
                    value = row.iloc[col_idx] if col_idx < len(row) else None
                    if pd.isna(value):
                        continue

                    metric_val = self._parse_float(value)
                    if metric_val is None:
                        continue

                    # Check for duplicate
                    existing = session.query(ArchivedSummary).filter_by(
                        cohort_id=cohort_id,
                        sheet_name=sheet_name,
                        subject_id=subject_id,
                        date=date_val,
                        metric_name=current_metric,
                    ).first()
                    if existing:
                        continue

                    archived = ArchivedSummary(
                        cohort_id=cohort_id,
                        sheet_name=sheet_name,
                        subject_id=subject_id,
                        date=date_val,
                        phase=phase if phase else None,
                        metric_name=current_metric,
                        metric_value=metric_val,
                        source_file=source_file,
                    )

                    if not dry_run:
                        session.add(archived)
                    self.imported_counts['archived_summaries'] += 1

    def _import_stats(self, xl: pd.ExcelFile, cohort_id: str,
                        session, dry_run: bool, source_file: str = ''):
        """Import 7_Stats sheet -> ArchivedSummary table."""
        if '7_Stats' not in xl.sheet_names:
            return

        try:
            df = pd.read_excel(xl, sheet_name='7_Stats', header=None)
        except Exception as e:
            self.warnings.append(f"Failed to read 7_Stats: {e}")
            return

        if df.shape[0] < 2 or df.shape[1] < 3:
            self.warnings.append("7_Stats: too small to contain data")
            return

        # Row 0 has headers: [empty, "Mouse", phase1, phase2, ...]
        phase_names = []
        for col_idx in range(2, len(df.columns)):
            phase = str(df.iloc[0, col_idx]).strip() if pd.notna(df.iloc[0, col_idx]) else ''
            phase_names.append(phase)

        if not phase_names:
            self.warnings.append("7_Stats: no phase columns found")
            return

        current_metric = None
        for row_idx in range(1, len(df)):
            row = df.iloc[row_idx]
            first_cell = str(row.iloc[0]).strip().lower() if pd.notna(row.iloc[0]) else ''

            # Detect metric block
            if first_cell in ('retrieved', 'contacted'):
                current_metric = first_cell + '_pct'

            if not current_metric:
                continue

            # Get subject_id from col 1
            subject_raw = row.iloc[1] if df.shape[1] > 1 else None
            if self._blank(subject_raw):
                continue  # blank animal cell: no row here (see _resolve_subject_id)

            # Average rows and the like are recorded as "not an animal id"; an
            # id of another cohort (a tab copied from the previous cohort's
            # workbook still names that cohort's animals) is recorded and kept
            # out, rather than archived under this cohort.
            subject_id = self._resolve_subject_id(subject_raw, cohort_id, '7_Stats')
            if not subject_id:
                continue  # _resolve_subject_id recorded why

            # Read values for each phase column
            for phase_idx, phase_name in enumerate(phase_names):
                col_idx = phase_idx + 2
                if col_idx >= len(row):
                    continue

                value = row.iloc[col_idx]
                if pd.isna(value):
                    continue

                metric_val = self._parse_float(value)
                if metric_val is None:
                    continue

                # Check for duplicate
                existing = session.query(ArchivedSummary).filter_by(
                    cohort_id=cohort_id,
                    sheet_name='7_Stats',
                    subject_id=subject_id,
                    phase=phase_name if phase_name else None,
                    metric_name=current_metric,
                ).first()
                if existing:
                    continue

                archived = ArchivedSummary(
                    cohort_id=cohort_id,
                    sheet_name='7_Stats',
                    subject_id=subject_id,
                    phase=phase_name if phase_name else None,
                    metric_name=current_metric,
                    metric_value=metric_val,
                    source_file=source_file,
                )

                if not dry_run:
                    session.add(archived)
                self.imported_counts['archived_summaries'] += 1

    def _normalize_subject_id(self, value, cohort_id: str) -> Optional[str]:
        """Normalize various subject ID formats to standard format.

        Format only: this says which id a cell spells, not whether the row may
        be filed under it. Rows go through _resolve_subject_id, which also
        refuses an id that belongs to another cohort.
        """
        if pd.isna(value):
            return None

        value = str(value).strip().upper()

        # Already in correct format
        if validate_subject_id(value):
            return value

        # Just a number (e.g., "01" or "1")
        if value.isdigit() or (value.startswith('0') and value[1:].isdigit()):
            subject_num = int(value)
            return f"{cohort_id}_{subject_num:02d}"

        # Format like "CNT0501" -> "CNT_05_01"
        match = re.match(r'^([A-Z]+)(\d{2})(\d{2})$', value)
        if match:
            return f"{match.group(1)}_{match.group(2)}_{match.group(3)}"

        return None

    # ------------------------------------------------------------------
    # Rows that are not imported, and why
    # ------------------------------------------------------------------
    # A row the importer decides not to import is recorded through these
    # helpers and reported as a warning when the import finishes
    # (_get_result). A warning nobody can see is a silent drop.

    # How many animals/rows one warning line names before "... and N more".
    _MAX_LISTED = 40

    # Notes for a cohort whose start date is a placeholder. It starts with
    # "auto-created" on purpose: import_cohort_file replaces the start date of
    # any cohort whose notes start that way once the sheet yields a real one.
    PLACEHOLDER_COHORT_NOTE = (
        "auto-created by the tracking-sheet import: start date NOT known (the "
        "sheet had no dated weigh-in and no dated tray row). start_date holds a "
        "placeholder -- the earliest dated record imported for this cohort -- "
        "only because the column cannot be empty. The first sheet import that "
        "finds a real start date replaces it.")

    @staticmethod
    def _ascii(text) -> str:
        """Plain-ASCII form of a message. Warnings are printed to Windows
        consoles, which crash on characters outside their code page, and a
        cell's text (an unrecognised id, a weight typed with a unit) can carry
        any character."""
        return str(text).encode('ascii', 'backslashreplace').decode('ascii')

    @staticmethod
    def _blank(value) -> bool:
        """True for a cell with nothing in it (None, NaN, NaT, or only spaces)."""
        if value is None:
            return True
        try:
            if pd.isna(value):
                return True
        except (TypeError, ValueError):
            return False
        return isinstance(value, str) and not value.strip()

    def _reset_run_state(self):
        """Forget what the previous import noted. One importer is reused for
        every workbook (import_all_cohorts), and one cohort's set-aside rows or
        placeholder must never be reported against the next."""
        for name in ('_unimported', '_placeholder_starts', '_warned_once',
                     '_dry_run_subjects', '_dry_run_cohorts', '_not_saved_counts',
                     '_sheet_sync', '_phase_refresh', '_pellets_relabelled', '_marked_dead',
                     '_facts_seen', '_seen_rows', '_unread_tabs',
                     '_notes_cleared'):
            self.__dict__.pop(name, None)

    def _warn_once(self, key, text: str):
        """Append a warning the first time `key` is seen in this import, for a
        refusal that would otherwise repeat on every row of one animal."""
        seen = self.__dict__.setdefault('_warned_once', set())
        if key in seen:
            return
        seen.add(key)
        self.warnings.append(self._ascii(text))

    def _note_unimported(self, tab: str, reason: str, item: str):
        """Set one row aside. `reason` is shown once per tab; `item` (the
        animal, with the row's date where it has one) is listed under it. An
        item noted twice is listed once: 3b_Manual_Tray is read by two passes,
        and one animal must not look like two."""
        pending = self.__dict__.setdefault('_unimported', {})
        items = pending.setdefault((tab, reason), [])
        if item not in items:
            items.append(item)

    def _flush_unimported(self):
        """Turn everything set aside during this import into warnings: one line
        per tab and reason, naming every animal (and date, where the row has
        one). One line per reason rather than one per row, because every
        caller shows a capped number of warnings (the scheduled sheet import
        keeps 50) and one copy per animal of the same reason (a cohort's
        overdue plans, a borrowed animal's rows) would push the warning that
        matters off the end."""
        pending = self.__dict__.pop('_unimported', {})
        for (tab, reason), items in pending.items():
            shown = items[:self._MAX_LISTED]
            text = "%s: %s (%d): %s" % (tab, reason, len(items), ', '.join(shown))
            if len(items) > len(shown):
                text += ", ... and %d more" % (len(items) - len(shown))
            self.warnings.append(self._ascii(text))

        for cohort_id, (start, dry_run) in sorted(
                self.__dict__.pop('_placeholder_starts', {}).items()):
            self.warnings.append(self._ascii(
                "Cohort %s was not in the database and its tracking sheet gives no "
                "start date (no dated weigh-in on 3d/3e_Weights or 3a_Manual_Ramp, "
                "no dated row on 3b_Manual_Tray). The database requires one, so the "
                "cohort %s created with a PLACEHOLDER start of %s -- the earliest "
                "dated record imported for it, NOT the day food restriction began. "
                "Type the day-0 weigh-in into the sheet; the next import replaces "
                "the placeholder."
                % (cohort_id, 'would be' if dry_run else 'was', start)))

    def _resolve_subject_id(self, raw, cohort_id: str, tab: str) -> Optional[str]:
        """The animal id a row is filed under, or None when the row must not be
        imported -- with the reason recorded for a person.

        Besides a good id there are three outcomes:
          * a blank cell -> None, nothing recorded. A blank animal cell names
            no animal; in the sheets such rows are column averages and
            formatting under a table, and a warning per padding row would bury
            the warnings that matter.
          * text that is not an animal id at all -> recorded. If it was meant
            as an animal, its data is being dropped and a person must know.
          * an id of ANOTHER cohort (PROJECT_02_05 typed in cohort 01's
            workbook: animals are borrowed between studies, and workbooks are made by
            copying the previous cohort's) -> recorded, never filed. Filed
            under this workbook's cohort it became a second animal in the wrong
            cohort; filed under its own id it let the borrower's copy write
            onto the home animal's record. An animal is imported only from its
            own cohort's workbook.
        """
        if self._blank(raw):
            return None
        subject_id = self._normalize_subject_id(raw, cohort_id)
        # validate_subject_id as well: a bare number of three digits in an
        # animal cell (a percentage in a summary block) normalizes to an id
        # like CNT_04_100, which no animal can have and derive_cohort_id
        # would raise on.
        if subject_id is None or not validate_subject_id(subject_id):
            self._note_unimported(
                tab, "rows not imported: the animal cell is not an animal id "
                     "(expected PROJECT_NN_NN such as CNT_05_01, or the animal "
                     "number alone)",
                repr(str(raw).strip()))
            return None
        home = derive_cohort_id(subject_id)
        if home != cohort_id:
            self._note_unimported(
                tab, "rows not imported: these animals belong to cohort %s, not to "
                     "this workbook's cohort %s, and an animal is imported only "
                     "from its own cohort's workbook" % (home, cohort_id),
                subject_id)
            return None
        return subject_id

    def _ensure_cohort(self, session, cohort_id: str, dry_run: bool,
                       first_seen=None) -> bool:
        """Make sure the cohort row exists before an animal is filed under it.

        Returns True when it exists (or, in a dry run, would), False when it
        cannot be created -- a warning then says why and the caller skips the
        row.

        WHY no invented date: this used to create the cohort with today's date
        as its start and the note "Auto-created during import". A cohort whose
        sheet had no weigh-in got, as its start, whatever day the import
        happened to run -- a pilot cohort "started" months after its own
        surgeries -- and nothing marked the date as made up.

        cohorts.start_date is NOT NULL (schema.Cohort), so a cohort row cannot
        hold "unknown". The least-wrong honest option:
          * the row being imported has a date (first_seen): the cohort is
            created with the EARLIEST dated record this import files under it
            as a placeholder start. That date really is in the sheet and comes
            before every imported record, so no record lands before day 0. The
            notes (PLACEHOLDER_COHORT_NOTE) say in words that the start is not
            known, a warning says so at the end of the import, and the next
            import that can read a real start replaces it.
          * there is no date at all: nothing is created and the row is not
            imported. The import runs again on its schedule and picks the row
            up once the sheet has a start date.
        """
        placeholders = self.__dict__.setdefault('_placeholder_starts', {})
        existing = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
        if existing is not None:
            # A placeholder made by THIS import moves earlier when an earlier
            # record turns up (sheet order is not date order). A cohort that
            # existed before this import is never touched: the importer only
            # adds rows.
            if cohort_id in placeholders and first_seen is not None and (
                    existing.start_date is None or first_seen < existing.start_date):
                existing.start_date = first_seen
                placeholders[cohort_id] = (first_seen, dry_run)
            return True
        if dry_run and cohort_id in self.__dict__.get('_dry_run_cohorts', ()):
            # import_cohort_file found a real start date but, being a dry run,
            # added nothing: the cohort would exist.
            return True
        if cohort_id in placeholders:
            # Dry run: the placeholder was never added, so the cohort still
            # looks absent; keep tracking what its start would be.
            start, _ = placeholders[cohort_id]
            if first_seen is not None and first_seen < start:
                placeholders[cohort_id] = (first_seen, dry_run)
            return True
        if first_seen is None:
            self._warn_once(
                ('no-cohort', cohort_id),
                "Cohort %s is not in the database and its tracking sheet gives no "
                "start date, so nothing could be filed under it: rows naming its "
                "animals were NOT imported. They will be on the first import after "
                "a dated day-0 weigh-in is typed into 3d/3e_Weights or "
                "3a_Manual_Ramp." % cohort_id)
            return False

        project_code = cohort_id.split('_')[0]
        proj = session.query(Project).filter_by(project_code=project_code).first()
        if not proj:
            proj = Project(project_code=project_code, project_name=project_code)
            if not dry_run:
                session.add(proj)
                session.flush()

        cohort = Cohort(
            cohort_id=cohort_id,
            project_code=project_code,
            start_date=first_seen,
            notes=self.PLACEHOLDER_COHORT_NOTE,
        )
        if not dry_run:
            session.add(cohort)
            session.flush()
        placeholders[cohort_id] = (first_seen, dry_run)
        return True

    def _ensure_subject(self, session, subject_id: str, cohort_id: str, dry_run: bool,
                        first_seen=None) -> bool:
        """Make sure the animal exists, under its OWN cohort.

        Returns True when it exists (or, in a dry run, would), False when it
        must not or cannot be created -- a warning then says why, and the
        caller skips the row. first_seen is the date of the record being
        imported, passed on in case the cohort has to be created (see
        _ensure_cohort).
        """
        # Second line of defence behind _resolve_subject_id (callers that build
        # ids themselves, such as the frozen-workbook score import, come
        # straight here): an id's own cohort part decides where the animal
        # lives, never the workbook it was typed in.
        try:
            home = derive_cohort_id(subject_id)
        except ValueError:
            home = None
        if home != cohort_id:
            self._warn_once(
                ('foreign-subject', subject_id, cohort_id),
                "Animal %s was NOT filed under cohort %s: %s. None of its rows "
                "in this import were stored."
                % (subject_id, cohort_id,
                   ("its id says it belongs to cohort %s" % home) if home
                   else "it is not a valid animal id"))
            return False

        if not self._ensure_cohort(session, cohort_id, dry_run, first_seen):
            return False

        existing = session.query(Subject).filter_by(subject_id=subject_id).first()
        if not existing:
            # In a dry run nothing is added, so the same animal would look new
            # on every one of its rows and be counted each time ('subjects:
            # 1583' for a 16-mouse cohort). Count each id once.
            seen = self.__dict__.setdefault('_dry_run_subjects', set())
            if dry_run and subject_id in seen:
                return True
            seen.add(subject_id)
            subject = Subject(subject_id=subject_id, cohort_id=cohort_id)
            if not dry_run:
                session.add(subject)
                session.flush()
            self.imported_counts['subjects'] = self.imported_counts.get('subjects', 0) + 1
        elif (str(existing.notes or '').lower().startswith('auto-created')
              and not self._held_back(
                  "replacing the placeholder note of %s, first created from a video"
                  % subject_id)):
            # The animal was first created from a video, before its sheet named
            # it, with a placeholder note saying so (import_reaches). The sheet
            # names it now, so the note is no longer true -- and it is how the
            # data status counts "animals created from video only". Replaced
            # once, the first time a sheet row names the animal (the same rule
            # import_cohort_file applies to an auto-created cohort).
            self._clear_auto_created_note(session, existing, dry_run)
        return True

    def _clear_auto_created_note(self, session, subject, dry_run: bool):
        """Replace a subject's "auto-created" placeholder note, keeping what it
        said as history, logged in the audit log and listed in sheet_sync."""
        done = self.__dict__.setdefault('_notes_cleared', set())
        if subject.subject_id in done:
            return
        done.add(subject.subject_id)
        old = subject.notes
        new = "Named by its tracking sheet (before that: %s)" % old
        report = self._sync_state().setdefault('subjects', {})
        report.setdefault('notes_cleared', []).append(
            {'subject_id': subject.subject_id, 'old': old, 'new': new})
        self.imported_counts['subject_notes_cleared'] = (
            self.imported_counts.get('subject_notes_cleared', 0) + 1)
        if not dry_run:
            self._audit(session, 'UPDATE', 'subjects', subject.subject_id,
                        {'notes': old}, {'notes': new})
            subject.notes = new

    def _subject_known(self, session, subject_id: str) -> bool:
        """True if the animal is in the database already -- or, in a dry run,
        would have been created by an earlier tab of this import."""
        if subject_id in self.__dict__.get('_dry_run_subjects', ()):
            return True
        return session.query(Subject).filter_by(subject_id=subject_id).first() is not None

    def _parse_date(self, value) -> Optional[datetime]:
        """Parse various date formats."""
        if pd.isna(value):
            return None
        if isinstance(value, datetime):
            return value.date()
        try:
            return pd.to_datetime(value).date()
        except Exception:
            return None

    def _parse_float(self, value) -> Optional[float]:
        """Parse float value."""
        if pd.isna(value):
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def _parse_sex(self, value) -> Optional[str]:
        """Parse sex value."""
        if pd.isna(value):
            return None
        value = str(value).strip().upper()
        if value in ('M', 'MALE'):
            return 'M'
        if value in ('F', 'FEMALE'):
            return 'F'
        return None

    def _get_result(self) -> Dict:
        """Get import result summary."""
        # Rows set aside during the import become warnings here: every import
        # path ends by building its result, so none can end without them.
        self._flush_unimported()
        result = {
            'success': len(self.errors) == 0,
            'errors': self.errors,
            'warnings': self.warnings,
            'imported': self.imported_counts,
        }
        if self.__dict__.get('_phase_refresh'):
            result['phases_rederived'] = self.__dict__['_phase_refresh']
        if self.__dict__.get('_sheet_sync'):
            # What the sheet changed or removed in the database (or, refused,
            # why it did not): see _sync_pellet_rows. Kept whole in the result
            # and the ledger -- it is the record of what was taken out.
            result['sheet_sync'] = self.__dict__['_sheet_sync']
        if '_not_saved_counts' in self.__dict__:
            # What a rolled-back import had read before it was undone (see
            # _report_not_committed): for diagnosis, never counted as saved.
            result['not_saved'] = self.__dict__['_not_saved_counts']
        return result


def import_all_cohorts(cohorts_dir: Path, dry_run: bool = False) -> List[Dict]:
    """
    Import all cohort Excel files from a directory.

    Args:
        cohorts_dir: Directory containing Excel files
        dry_run: If True, validate only

    Returns:
        List of import results for each file
    """
    results = []
    importer = ExcelImporter()

    excel_files = list(cohorts_dir.glob('Connectome_*_Animal_Tracking*.xlsx'))
    # Filter out temp files and backups
    excel_files = [f for f in excel_files if not f.name.startswith('~') and '(1)' not in f.name]

    print(f"Found {len(excel_files)} cohort files to import")

    for excel_path in sorted(excel_files):
        result = importer.import_cohort_file(excel_path, dry_run=dry_run)
        result['file'] = excel_path.name
        results.append(result)

    # Summary
    total_subjects = sum(r['imported']['subjects'] for r in results)
    total_weights = sum(r['imported']['weights'] for r in results)
    total_pellets = sum(r['imported']['pellet_scores'] for r in results)
    total_surgeries = sum(r['imported']['surgeries'] for r in results)
    total_ramp = sum(r['imported'].get('ramp_entries', 0) for r in results)
    total_virus_preps = sum(r['imported'].get('virus_preps', 0) for r in results)
    total_ladder = sum(r['imported'].get('ladder_entries', 0) for r in results)
    total_archived = sum(r['imported'].get('archived_summaries', 0) for r in results)
    total_planning = sum(r['imported'].get('planning_notes', 0) for r in results)

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Import Summary:")
    print(f"  Subjects: {total_subjects}")
    print(f"  Weights: {total_weights}")
    print(f"  Pellet scores: {total_pellets}")
    print(f"  Surgeries: {total_surgeries}")
    print(f"  Ramp entries: {total_ramp}")
    print(f"  Virus preps: {total_virus_preps}")
    print(f"  Ladder entries: {total_ladder}")
    print(f"  Archived summaries: {total_archived}")
    print(f"  Planning notes: {total_planning}")

    return results


# =============================================================================
# BRAINGLOBE DATA IMPORTER
# =============================================================================

# ---------------------------------------------------------------------------
# eLife group counts from MouseBrain's own aggregate (2_Data_Summary/elife_counts.csv)
# ---------------------------------------------------------------------------

def _elife_column_groups() -> Dict[str, str]:
    """Reverse MouseBrain's column naming in elife_counts.csv: a group is
    written as ``group_<name>`` / ``group_left_<name>`` / ``group_right_<name>``
    with ``name.replace(' ', '_').replace('/', '_')``, and the unmapped bucket
    (the group ``[Unmapped]``) as ``Unmapped``. The canonical names come from
    mousedb.region_priors, so no mousebrain import is needed."""
    from .region_priors import PRIORS
    out = {"Unmapped": "[Unmapped]"}
    for prior in PRIORS.values():
        for name in prior.ordered_regions:
            if name.startswith("["):
                continue
            out[name.replace(' ', '_').replace('/', '_')] = name
    return out


def read_elife_summary_rows(csv_path: Path, brain_id: str) -> Dict[str, Dict[str, int]]:
    """{group name: {'both': n, 'left': n, 'right': n}} for one brain, read from
    MouseBrain's elife_counts.csv (one row per brain: the ``brain`` column is
    ``<mouse folder>/<brain id>`` and ``brain_id`` is the bare brain number).

    Matching, in order: the brain id itself (the whole ``brain`` value or its
    last path component), then the brain number alone when it is unique in the
    file. Raises LookupError when nothing (or more than one candidate) matches,
    so the caller can say exactly why nothing was imported. A blank cell is
    ABSENT from the result, not 0: a hemisphere MouseBrain did not count is not
    a hemisphere with zero cells.
    """
    import csv as csv_mod
    decode = _elife_column_groups()
    number = brain_id.split('_')[0]
    exact, by_number = [], []
    with open(csv_path, 'r', encoding='utf-8', newline='') as f:
        for row in csv_mod.DictReader(f):
            brain = (row.get('brain') or '').strip()
            leaf = brain.replace('\\', '/').rsplit('/', 1)[-1]
            if brain == brain_id or leaf == brain_id:
                exact.append(row)
            elif (row.get('brain_id') or '').strip() == number:
                by_number.append(row)
    if exact:
        row = exact[-1]  # MouseBrain rewrites a brain's row in place; the last one is current
    elif len(by_number) == 1:
        row = by_number[0]
    elif by_number:
        raise LookupError("%d rows in %s carry brain number %s and none names %s exactly"
                          % (len(by_number), csv_path, number, brain_id))
    else:
        raise LookupError("no row for %s in %s (MouseBrain step 6 has not run for this brain, "
                          "or its brain id differs)" % (brain_id, csv_path))
    out: Dict[str, Dict[str, int]] = {}
    for col, val in row.items():
        if not col or not col.startswith('group_') or val is None or str(val).strip() == '':
            continue
        rest = col[len('group_'):]
        hemisphere = 'both'
        for h in ('left', 'right'):
            if rest.startswith(h + '_'):
                hemisphere, rest = h, rest[len(h) + 1:]
                break
        try:
            n = int(float(val))
        except ValueError:
            continue
        out.setdefault(decode.get(rest, rest), {})[hemisphere] = n
    return out


class BrainGlobeImporter:
    """
    Import BrainGlobe cell detection and region analysis results.

    Handles:
    - Region counts from CSV files (6_Region_Analysis/)
    - Calibration runs from tracker CSV (2_Data_Summary/calibration_runs.csv)
    - Cell coordinates from detection XML files (4_Cell_Candidates/)
    - Brain sample metadata from folder structure

    Brain naming convention: {BRAIN#}_{PROJECT}_{COHORT}_{SUBJECT}_{MAG}x_z{ZSTEP}
    Example: 349_CNT_01_02_1p625x_z4
    """

    # Brain name regex pattern
    BRAIN_NAME_PATTERN = re.compile(
        r'^(\d+)_([A-Z]+)_(\d{2})_(\d{2})_(\d+p?\d*)x_z(\d+)$'
    )

    # Cached acronym -> {id, name} lookup
    _acronym_to_id_cache = None

    @classmethod
    def get_acronym_to_id(cls, atlas_name='allen_mouse_10um'):
        """
        Build acronym -> (region_id, region_name) lookup from Allen atlas.

        Tries live BrainGlobeAtlas first, falls back to static JSON.

        Returns:
            Dict mapping acronym to {'id': int, 'name': str}
        """
        if cls._acronym_to_id_cache is not None:
            return cls._acronym_to_id_cache

        # Try live atlas first
        try:
            from brainglobe_atlasapi import BrainGlobeAtlas
            atlas = BrainGlobeAtlas(atlas_name)
            lookup = {}
            for rid, info in atlas.structures.items():
                acr = info.get('acronym', '')
                if acr:
                    lookup[acr] = {'id': int(rid), 'name': info.get('name', acr)}
            cls._acronym_to_id_cache = lookup
            return lookup
        except (ImportError, Exception):
            pass

        # Fallback to static JSON
        json_path = Path(__file__).parent / 'data' / f'{atlas_name}_lookup.json'
        if json_path.exists():
            import json
            with open(json_path, 'r') as f:
                cls._acronym_to_id_cache = json.load(f)
            return cls._acronym_to_id_cache

        return {}

    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_db()
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.imported_counts: Dict[str, int] = {}

    def parse_brain_name(self, brain_id: str) -> Optional[Dict]:
        """
        Parse brain ID into components.

        Args:
            brain_id: e.g., "349_CNT_01_02_1p625x_z4"

        Returns:
            Dict with brain_number, project, cohort, subject, magnification, z_step
        """
        match = self.BRAIN_NAME_PATTERN.match(brain_id)
        if not match:
            return None

        mag_str = match.group(5).replace('p', '.')
        return {
            'brain_number': int(match.group(1)),
            'project_code': match.group(2),
            'cohort_num': int(match.group(3)),
            'subject_num': int(match.group(4)),
            'magnification': float(mag_str),
            'z_step_um': float(match.group(6)),
            'subject_id': f"{match.group(2)}_{match.group(3)}_{match.group(4)}",
            'cohort_id': f"{match.group(2)}_{match.group(3)}",
        }

    def import_calibration_runs(self, csv_path: Path, dry_run: bool = False) -> Dict:
        """
        Import calibration runs from tracker CSV.

        Args:
            csv_path: Path to calibration_runs.csv
            dry_run: If True, validate only

        Returns:
            Dict with import statistics
        """
        from .schema import BrainSample, CalibrationRun

        self.errors = []
        self.warnings = []
        self.imported_counts = {'calibration_runs': 0, 'brain_samples': 0}

        if not csv_path.exists():
            self.errors.append(f"File not found: {csv_path}")
            return self._get_result()

        print(f"{'[DRY RUN] ' if dry_run else ''}Importing calibration runs: {csv_path}")

        try:
            df = pd.read_csv(csv_path)
        except Exception as e:
            self.errors.append(f"Failed to read CSV: {e}")
            return self._get_result()

        # Runs whose "brain" is not a brain (training data, test runs) are
        # skipped and reported ONCE, by name. WHY: the tracker records every
        # calibration run, including runs on training cubes and test data; one
        # warning per such row, every night, buried any real problem in the
        # task log ("... and 13 more warnings", 2026-10).
        not_brains: dict = {}
        with self.db.session() as session:
            for _, row in df.iterrows():
                brain_id = row.get('brain') or row.get('brain_id')
                if not brain_id or pd.isna(brain_id):
                    continue
                brain_id = str(brain_id).strip()

                # Handle composite names like "349_CNT_01_02/349_CNT_01_02_1p625x_z4"
                # Try parts separated by / to find the parseable brain ID
                brain_info = self.parse_brain_name(brain_id)
                if not brain_info and '/' in brain_id:
                    for part in brain_id.split('/'):
                        brain_info = self.parse_brain_name(part.strip())
                        if brain_info:
                            brain_id = part.strip()
                            break
                # Also try searching within the string for the pattern
                if not brain_info:
                    match = self._BRAIN_NAME_SEARCH.search(brain_id)
                    if match:
                        candidate = match.group(1)
                        brain_info = self.parse_brain_name(candidate)
                        if brain_info:
                            brain_id = candidate

                if not brain_info:
                    not_brains[brain_id] = not_brains.get(brain_id, 0) + 1
                    continue

                # Ensure brain sample exists
                brain_sample = self._ensure_brain_sample(
                    session, brain_id, brain_info, dry_run
                )
                if not brain_sample:
                    continue

                # Check for duplicate run
                run_id = row.get('exp_id') or row.get('run_id')
                if not run_id or pd.isna(run_id):
                    run_id = f"{brain_id}_{len(brain_sample.calibration_runs) + 1}"

                existing = session.query(CalibrationRun).filter_by(
                    brain_sample_id=brain_sample.id if brain_sample.id else 0,
                    run_id=str(run_id)
                ).first()
                if existing:
                    continue

                # Create calibration run
                cal_run = CalibrationRun(
                    brain_sample_id=brain_sample.id,
                    run_id=str(run_id),
                    ball_xy_um=self._parse_float(row.get('ball_xy') or row.get('ball_xy_um')),
                    ball_z_um=self._parse_float(row.get('ball_z') or row.get('ball_z_um')),
                    soma_diameter_um=self._parse_float(row.get('soma_diameter')),
                    threshold=self._parse_float(row.get('threshold')),
                    cells_detected=self._parse_int(row.get('det_cells_found') or row.get('cells_detected')),
                    detection_time_s=self._parse_float(row.get('detection_time_s')),
                    status=row.get('status') if pd.notna(row.get('status')) else 'unknown',
                    is_best=1 if row.get('is_best') in (True, 1, '1', 'True', 'true') else 0,
                    run_date=self._parse_datetime(row.get('timestamp') or row.get('run_date')),
                    user=row.get('user') if pd.notna(row.get('user')) else None,
                    notes=row.get('notes') if pd.notna(row.get('notes')) else None,
                    source_csv=str(csv_path),
                )

                if not dry_run:
                    session.add(cal_run)
                self.imported_counts['calibration_runs'] += 1

            if not dry_run and not self.errors:
                session.commit()
                print(f"  Committed {self.imported_counts['calibration_runs']} calibration runs")

        if not_brains:
            names = ", ".join("%s (%d)" % (n, c) for n, c in sorted(not_brains.items()))
            self.warnings.append(
                "%d calibration run(s) name no brain and were skipped (training or test "
                "runs, by name and count): %s" % (sum(not_brains.values()), names))
        return self._get_result()

    def import_region_counts(self, csv_path: Path, brain_id: Optional[str] = None,
                             calibration_run_id: Optional[int] = None,
                             is_final: bool = False,
                             dry_run: bool = False) -> Dict:
        """
        Import region counts from a CSV file.

        Supports two formats:
        - Long format with region_id, region_name, hemisphere, cell_count
        - Per-brain format with region_acronym, region_name, cell_count, left_count, right_count
          (acronyms are resolved to Allen Atlas integer IDs via lookup)

        Args:
            csv_path: Path to region counts CSV
            brain_id: Brain ID if not in filename/data
            calibration_run_id: Link to calibration run if known
            is_final: Mark as production counts
            dry_run: If True, validate only

        Returns:
            Dict with import statistics
        """
        from .schema import BrainSample, RegionCount

        self.errors = []
        self.warnings = []
        self.imported_counts = {'region_counts': 0}

        if not csv_path.exists():
            self.errors.append(f"File not found: {csv_path}")
            return self._get_result()

        print(f"{'[DRY RUN] ' if dry_run else ''}Importing region counts: {csv_path}")

        try:
            df = pd.read_csv(csv_path)
        except Exception as e:
            self.errors.append(f"Failed to read CSV: {e}")
            return self._get_result()

        with self.db.session() as session:
            # Try to determine brain_id from filename or data
            if not brain_id:
                brain_id = self._extract_brain_from_path(csv_path)

            if not brain_id:
                self.errors.append("Could not determine brain ID")
                return self._get_result()

            brain_info = self.parse_brain_name(brain_id)
            if not brain_info:
                self.errors.append(f"Could not parse brain ID: {brain_id}")
                return self._get_result()

            # Ensure brain sample exists
            brain_sample = self._ensure_brain_sample(session, brain_id, brain_info, dry_run)
            if not brain_sample:
                # A failure, not a quiet success with nothing imported: the
                # caller would print [OK] and go on to the eLife counts. The
                # reason is IN the error, not only in a warning: the
                # import-brains --all loop prints a brain's errors but never
                # its warnings, and _ensure_brain_sample has one refusal.
                self.errors.append(
                    f"Region counts for {brain_id} NOT imported: its cohort "
                    f"{brain_info['cohort_id']} is not in the database yet (import "
                    f"the cohort's tracking sheet or one of its videos first)")
                return self._get_result()

            # Detect CSV format and column names
            col_map = self._detect_region_csv_format(df)
            if not col_map:
                self.errors.append("Could not detect CSV format")
                return self._get_result()

            # Load acronym-to-ID lookup if we need it
            acronym_lookup = {}
            if 'region_id' not in col_map and 'acronym' in col_map:
                acronym_lookup = self.get_acronym_to_id()
                if not acronym_lookup:
                    self.errors.append(
                        "CSV has acronyms but no region_id column, "
                        "and acronym-to-ID lookup is unavailable"
                    )
                    return self._get_result()

            # Check if CSV has left/right hemisphere columns
            has_hemisphere_cols = 'left_count' in col_map and 'right_count' in col_map

            for _, row in df.iterrows():
                # Get region identification
                region_id = self._parse_int(row.get(col_map.get('region_id', '__none__')))
                acronym = None
                if col_map.get('acronym'):
                    acronym_val = row.get(col_map['acronym'])
                    acronym = str(acronym_val) if pd.notna(acronym_val) else None

                region_name = None
                if col_map.get('region_name'):
                    name_val = row.get(col_map['region_name'])
                    region_name = str(name_val) if pd.notna(name_val) else None

                # Resolve acronym to region_id if needed
                if region_id is None and acronym:
                    info = acronym_lookup.get(acronym)
                    if info:
                        region_id = info['id']
                        if not region_name:
                            region_name = info['name']
                    else:
                        self.warnings.append(f"Unknown acronym: {acronym}")
                        continue

                cell_count = self._parse_int(row.get(col_map.get('cell_count', '__none__')))
                if region_id is None or cell_count is None:
                    continue

                if not region_name:
                    region_name = f"Region_{region_id}"

                # Insert total count (hemisphere='both')
                if not has_hemisphere_cols:
                    # Original format: single hemisphere column
                    hemisphere = 'both'
                    if col_map.get('hemisphere'):
                        h_val = row.get(col_map['hemisphere'])
                        hemisphere = str(h_val) if pd.notna(h_val) else 'both'

                    self._insert_region_count(
                        session, brain_sample, region_id, region_name, acronym,
                        hemisphere, cell_count, col_map, row,
                        calibration_run_id, is_final, csv_path, dry_run
                    )
                else:
                    # Per-brain format with left_count/right_count columns
                    # Insert total
                    self._insert_region_count(
                        session, brain_sample, region_id, region_name, acronym,
                        'both', cell_count, col_map, row,
                        calibration_run_id, is_final, csv_path, dry_run
                    )
                    # Insert left
                    left_count = self._parse_int(row.get(col_map['left_count']))
                    if left_count is not None and left_count > 0:
                        self._insert_region_count(
                            session, brain_sample, region_id, region_name, acronym,
                            'left', left_count, col_map, row,
                            calibration_run_id, is_final, csv_path, dry_run
                        )
                    # Insert right
                    right_count = self._parse_int(row.get(col_map['right_count']))
                    if right_count is not None and right_count > 0:
                        self._insert_region_count(
                            session, brain_sample, region_id, region_name, acronym,
                            'right', right_count, col_map, row,
                            calibration_run_id, is_final, csv_path, dry_run
                        )

            if not dry_run and not self.errors:
                session.commit()
                print(f"  Committed {self.imported_counts['region_counts']} region counts")

        return self._get_result()

    def _insert_region_count(self, session, brain_sample, region_id, region_name,
                              acronym, hemisphere, cell_count, col_map, row,
                              calibration_run_id, is_final, csv_path, dry_run):
        """Insert a single RegionCount row, skipping duplicates."""
        from .schema import RegionCount

        # Check for duplicate
        existing = session.query(RegionCount).filter_by(
            brain_sample_id=brain_sample.id,
            region_id=region_id,
            hemisphere=hemisphere,
            cell_type='all'
        ).first()
        if existing:
            return

        region_count = RegionCount(
            brain_sample_id=brain_sample.id,
            region_id=region_id,
            region_name=region_name,
            region_acronym=acronym,
            parent_region_id=self._parse_int(
                row.get(col_map.get('parent_id', '__none__'))
            ),
            hemisphere=hemisphere,
            cell_count=cell_count,
            cell_density=self._parse_float(
                row.get(col_map.get('density', '__none__'))
            ),
            region_volume_mm3=self._parse_float(
                row.get(col_map.get('volume', '__none__'))
            ),
            cell_type='all',
            calibration_run_id=calibration_run_id,
            is_final=1 if is_final else 0,
            source_file=str(csv_path),
        )

        if not dry_run:
            session.add(region_count)
        self.imported_counts['region_counts'] += 1

    def import_elife_counts(self, brain_sample_id: int,
                             region_counts_dict: Dict[str, int],
                             hemisphere_counts: Optional[Dict[str, Dict[str, int]]] = None,
                             calibration_run_id: Optional[int] = None,
                             is_final: bool = False,
                             source_file: Optional[str] = None,
                             dry_run: bool = False,
                             brain_id: Optional[str] = None,
                             elife_summary_csv: Optional[Path] = None) -> Dict:
        """
        Import eLife 25-group aggregated counts.

        Two sources, in this order:
          1. mousebrain importable here -> recompute from ``region_counts_dict``
             with MouseBrain's own mapping (``aggregate_to_elife``).
          2. otherwise -> read the brain's row from MouseBrain's aggregate,
             ``elife_summary_csv`` (2_Data_Summary/elife_counts.csv, written by
             MouseBrain's step 6). Needs ``brain_id`` to find the row.
        When neither is possible the result carries an explicit error line;
        it never skips silently.

        Args:
            brain_sample_id: FK to brain_samples.id
            region_counts_dict: {acronym: total_count} for all Allen regions
            hemisphere_counts: Optional {acronym: {'left': N, 'right': N}}
            calibration_run_id: Link to calibration run if known
            is_final: Mark as production counts
            source_file: Source CSV path for provenance
            dry_run: If True, validate only
            brain_id: The brain's id (row key in elife_summary_csv)
            elife_summary_csv: MouseBrain's elife_counts.csv (source 2)

        Returns:
            Dict with import statistics
        """
        import json as json_mod
        from .schema import ElifeRegionCount

        self.errors = []
        self.warnings = []
        self.imported_counts = {'elife_region_counts': 0}

        # Recompute with MouseBrain's own mapping when it is importable here.
        aggregate_fn = None
        try:
            from mousebrain.region_mapping import aggregate_to_elife
            aggregate_fn = aggregate_to_elife
        except ImportError:
            pass

        if aggregate_fn is None:
            # WHY: mousedb is the integrator; it must not need mousebrain
            # installed to hold MouseBrain's numbers. MouseBrain's step 6
            # already writes the aggregate this method would recompute (one row
            # per brain in elife_counts.csv), so read that. And every way of NOT
            # importing is an error line: the old "skip when mousebrain is
            # missing" is how eLife counts never reached the database for months
            # without anyone seeing it (2026-08).
            return self._import_elife_counts_from_summary(
                brain_sample_id, brain_id, elife_summary_csv,
                calibration_run_id, is_final, dry_run)

        print(f"{'[DRY RUN] ' if dry_run else ''}Computing eLife group counts "
              f"for brain_sample_id={brain_sample_id}")

        with self.db.session() as session:
            # Aggregate total counts
            elife_total = aggregate_fn(region_counts_dict)

            # Aggregate hemisphere counts if available
            elife_left = {}
            elife_right = {}
            if hemisphere_counts:
                left_dict = {acr: hc.get('left', 0) for acr, hc in hemisphere_counts.items()
                             if hc.get('left', 0) > 0}
                right_dict = {acr: hc.get('right', 0) for acr, hc in hemisphere_counts.items()
                              if hc.get('right', 0) > 0}
                if left_dict:
                    elife_left = aggregate_fn(left_dict)
                if right_dict:
                    elife_right = aggregate_fn(right_dict)

            # Insert rows for each group and hemisphere
            for group_name, group_data in elife_total.items():
                count = group_data['count'] if isinstance(group_data, dict) else 0
                constituents = group_data.get('constituents', {}) if isinstance(group_data, dict) else {}

                self._insert_elife_count(
                    session, brain_sample_id, group_name, 'both', count,
                    constituents, calibration_run_id, is_final, source_file, dry_run
                )

                # Left hemisphere
                if group_name in elife_left:
                    left_data = elife_left[group_name]
                    left_count = left_data['count'] if isinstance(left_data, dict) else 0
                    left_constituents = left_data.get('constituents', {}) if isinstance(left_data, dict) else {}
                    if left_count > 0:
                        self._insert_elife_count(
                            session, brain_sample_id, group_name, 'left', left_count,
                            left_constituents, calibration_run_id, is_final, source_file, dry_run
                        )

                # Right hemisphere
                if group_name in elife_right:
                    right_data = elife_right[group_name]
                    right_count = right_data['count'] if isinstance(right_data, dict) else 0
                    right_constituents = right_data.get('constituents', {}) if isinstance(right_data, dict) else {}
                    if right_count > 0:
                        self._insert_elife_count(
                            session, brain_sample_id, group_name, 'right', right_count,
                            right_constituents, calibration_run_id, is_final, source_file, dry_run
                        )

            if not dry_run and not self.errors:
                session.commit()
                print(f"  Committed {self.imported_counts['elife_region_counts']} eLife region counts")

        return self._get_result()

    def _import_elife_counts_from_summary(self, brain_sample_id, brain_id, elife_summary_csv,
                                          calibration_run_id, is_final, dry_run) -> Dict:
        """Source 2 of import_elife_counts: the brain's row in MouseBrain's
        elife_counts.csv. Constituent regions are not in that file, so the
        rows carry none. Every failure is an error line (see the WHY above)."""
        where = ("MouseBrain's elife_counts.csv was not given" if elife_summary_csv is None
                 else "MouseBrain's elife_counts.csv does not exist at %s" % elife_summary_csv)
        if elife_summary_csv is None or not Path(elife_summary_csv).is_file():
            self.errors.append(
                "eLife counts NOT imported for brain_sample_id=%s: mousebrain.region_mapping is not "
                "importable in this environment and %s" % (brain_sample_id, where))
            return self._get_result()
        if not brain_id:
            self.errors.append(
                "eLife counts NOT imported for brain_sample_id=%s: brain_id is needed to find the "
                "brain's row in %s" % (brain_sample_id, elife_summary_csv))
            return self._get_result()
        try:
            rows = read_elife_summary_rows(Path(elife_summary_csv), brain_id)
        except (LookupError, OSError, ValueError) as e:
            self.errors.append("eLife counts NOT imported for %s: %s" % (brain_id, e))
            return self._get_result()

        known = set(_elife_column_groups().values())
        for name in sorted(rows):
            if name not in known:
                self.warnings.append("group %r in %s is not a known eLife group name; stored as written"
                                     % (name, Path(elife_summary_csv).name))

        print(f"{'[DRY RUN] ' if dry_run else ''}Reading eLife group counts for {brain_id} "
              f"from {elife_summary_csv} (mousebrain not importable here)")
        with self.db.session() as session:
            for group_name, by_hemisphere in rows.items():
                for hemisphere in ('both', 'left', 'right'):
                    if hemisphere not in by_hemisphere:
                        continue
                    self._insert_elife_count(
                        session, brain_sample_id, group_name, hemisphere,
                        by_hemisphere[hemisphere], {}, calibration_run_id, is_final,
                        str(elife_summary_csv), dry_run)
            if not dry_run and not self.errors:
                session.commit()
                print(f"  Committed {self.imported_counts['elife_region_counts']} eLife region counts")
        return self._get_result()

    def _insert_elife_count(self, session, brain_sample_id, group_name, hemisphere,
                             cell_count, constituents, calibration_run_id, is_final,
                             source_file, dry_run):
        """Insert a single ElifeRegionCount row, skipping duplicates."""
        import json as json_mod
        from .schema import ElifeRegionCount

        existing = session.query(ElifeRegionCount).filter_by(
            brain_sample_id=brain_sample_id,
            group_name=group_name,
            hemisphere=hemisphere,
        ).first()
        if existing:
            return

        elife_count = ElifeRegionCount(
            brain_sample_id=brain_sample_id,
            group_name=group_name,
            hemisphere=hemisphere,
            cell_count=cell_count,
            constituent_regions=json_mod.dumps(sorted(constituents.keys())) if constituents else None,
            calibration_run_id=calibration_run_id,
            is_final=1 if is_final else 0,
            source_file=source_file,
        )

        if not dry_run:
            session.add(elife_count)
        self.imported_counts['elife_region_counts'] += 1

    def import_cells_from_xml(self, xml_path: Path, brain_id: Optional[str] = None,
                               calibration_run_id: Optional[int] = None,
                               dry_run: bool = False) -> Dict:
        """
        Import detected cells from BrainGlobe/cellfinder XML file.

        Args:
            xml_path: Path to cell detection XML
            brain_id: Brain ID if not in filename
            calibration_run_id: Link to calibration run
            dry_run: If True, validate only

        Returns:
            Dict with import statistics
        """
        from .schema import BrainSample, DetectedCell
        import xml.etree.ElementTree as ET

        self.errors = []
        self.warnings = []
        self.imported_counts = {'detected_cells': 0}

        if not xml_path.exists():
            self.errors.append(f"File not found: {xml_path}")
            return self._get_result()

        print(f"{'[DRY RUN] ' if dry_run else ''}Importing cells from XML: {xml_path}")

        # Try to determine brain_id from path
        if not brain_id:
            brain_id = self._extract_brain_from_path(xml_path)

        if not brain_id:
            self.errors.append("Could not determine brain ID")
            return self._get_result()

        brain_info = self.parse_brain_name(brain_id)
        if not brain_info:
            self.errors.append(f"Could not parse brain ID: {brain_id}")
            return self._get_result()

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
        except Exception as e:
            self.errors.append(f"Failed to parse XML: {e}")
            return self._get_result()

        with self.db.session() as session:
            # Ensure brain sample exists
            brain_sample = self._ensure_brain_sample(session, brain_id, brain_info, dry_run)
            if not brain_sample:
                # A failure, not a quiet success with nothing imported; the
                # reason is in the error itself (see import_region_counts).
                self.errors.append(
                    f"Detected cells for {brain_id} NOT imported: its cohort "
                    f"{brain_info['cohort_id']} is not in the database yet (import "
                    f"the cohort's tracking sheet or one of its videos first)")
                return self._get_result()

            # Parse cells from XML - BrainGlobe format has <Cell> elements with x, y, z attributes
            cells = root.findall('.//Cell') or root.findall('.//Marker')

            for cell_elem in cells:
                x = self._parse_float(cell_elem.get('x') or cell_elem.find('x'))
                y = self._parse_float(cell_elem.get('y') or cell_elem.find('y'))
                z = self._parse_float(cell_elem.get('z') or cell_elem.find('z'))

                if x is None or y is None or z is None:
                    # Try nested elements
                    x_elem = cell_elem.find('x')
                    y_elem = cell_elem.find('y')
                    z_elem = cell_elem.find('z')
                    if x_elem is not None and y_elem is not None and z_elem is not None:
                        x = self._parse_float(x_elem.text)
                        y = self._parse_float(y_elem.text)
                        z = self._parse_float(z_elem.text)

                if x is None or y is None or z is None:
                    continue

                cell = DetectedCell(
                    brain_sample_id=brain_sample.id,
                    x_um=x,
                    y_um=y,
                    z_um=z,
                    cell_type=cell_elem.get('type') or cell_elem.get('class'),
                    confidence=self._parse_float(cell_elem.get('confidence') or cell_elem.get('probability')),
                    calibration_run_id=calibration_run_id,
                )

                if not dry_run:
                    session.add(cell)
                self.imported_counts['detected_cells'] += 1

            if not dry_run and not self.errors:
                session.commit()
                print(f"  Committed {self.imported_counts['detected_cells']} detected cells")

        return self._get_result()

    def import_brain_folder(self, brain_folder: Path, dry_run: bool = False) -> Dict:
        """
        Import all BrainGlobe data from a brain's pipeline folder.

        Looks for:
        - 6_Region_Analysis/*.csv - Region counts
        - 4_Cell_Candidates/*.xml - Detected cells
        - 3_Registered_Atlas/ - Registration info

        Args:
            brain_folder: Path to brain folder (e.g., 1_Brains/349_CNT_01_02/349_CNT_01_02_1p625x_z4/)
            dry_run: If True, validate only

        Returns:
            Dict with import statistics
        """
        self.errors = []
        self.warnings = []
        self.imported_counts = {
            'brain_samples': 0,
            'calibration_runs': 0,
            'region_counts': 0,
            'detected_cells': 0,
        }

        brain_id = brain_folder.name
        brain_info = self.parse_brain_name(brain_id)

        if not brain_info:
            self.errors.append(f"Could not parse brain folder name: {brain_id}")
            return self._get_result()

        print(f"{'[DRY RUN] ' if dry_run else ''}Importing brain folder: {brain_folder}")

        # Import region counts
        region_folder = brain_folder / '6_Region_Analysis'
        if region_folder.exists():
            for csv_file in region_folder.glob('*.csv'):
                result = self.import_region_counts(csv_file, brain_id=brain_id,
                                                   is_final=True, dry_run=dry_run)
                self.imported_counts['region_counts'] += result.get('imported', {}).get('region_counts', 0)

        # Import detected cells (optional - can be large)
        cell_folder = brain_folder / '4_Cell_Candidates'
        if cell_folder.exists():
            for xml_file in cell_folder.glob('*.xml'):
                # Only import if reasonably sized (< 100MB)
                if xml_file.stat().st_size < 100 * 1024 * 1024:
                    result = self.import_cells_from_xml(xml_file, brain_id=brain_id, dry_run=dry_run)
                    self.imported_counts['detected_cells'] += result.get('imported', {}).get('detected_cells', 0)
                else:
                    self.warnings.append(f"Skipping large XML file: {xml_file}")

        return self._get_result()

    def _ensure_brain_sample(self, session, brain_id: str, brain_info: Dict,
                              dry_run: bool):
        """Ensure brain sample exists, create if not."""
        from .schema import BrainSample, Subject

        # First ensure subject exists
        subject_id = brain_info['subject_id']
        subject = session.query(Subject).filter_by(subject_id=subject_id).first()
        if not subject:
            cohort_id = brain_info['cohort_id']

            # The cohort must already exist. This used to create it with today's
            # date as its start -- a date nobody recorded, stored as if it were
            # the start of the study. A brain name carries no date to start a
            # cohort with, and cohorts.start_date cannot be empty, so the brain
            # waits instead: the cohort appears as soon as its tracking sheet or
            # one of its videos is imported, and the next brain import takes it.
            from .schema import Cohort
            cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
            if not cohort:
                self.warnings.append(
                    f"Brain {brain_id} NOT imported: its cohort {cohort_id} is not in "
                    f"the database yet, and a brain name carries no date to create "
                    f"the cohort with. It is imported by the first brain import after "
                    f"the cohort's tracking sheet, or one of its videos, has been "
                    f"imported.")
                return None

            # Create minimal subject
            self.warnings.append(f"Subject {subject_id} not found, creating placeholder")
            subject = Subject(subject_id=subject_id, cohort_id=cohort_id)
            if not dry_run:
                session.add(subject)
                session.flush()

        # Check if brain sample exists
        brain_sample = session.query(BrainSample).filter_by(
            subject_id=subject_id, brain_id=brain_id
        ).first()

        if not brain_sample:
            brain_sample = BrainSample(
                subject_id=subject_id,
                brain_id=brain_id,
                brain_number=brain_info['brain_number'],
                magnification=brain_info['magnification'],
                z_step_um=brain_info['z_step_um'],
                voxel_size_z_um=brain_info['z_step_um'],
                status='imported',
            )
            if not dry_run:
                session.add(brain_sample)
                session.flush()
            self.imported_counts['brain_samples'] = self.imported_counts.get('brain_samples', 0) + 1

        return brain_sample

    # Non-anchored pattern for searching within filenames
    _BRAIN_NAME_SEARCH = re.compile(
        r'(\d+_[A-Z]+_\d{2}_\d{2}_\d+p?\d*x_z\d+)'
    )

    def _extract_brain_from_path(self, path: Path) -> Optional[str]:
        """Try to extract brain ID from file path or filename."""
        # Check each part of the path (directory names)
        for part in reversed(path.parts):
            if self.parse_brain_name(part):
                return part

        # Try to find brain ID pattern within the filename stem
        # Handles filenames like "349_CNT_01_02_349_CNT_01_02_1p625x_z4_counts.csv"
        stem = path.stem
        match = self._BRAIN_NAME_SEARCH.search(stem)
        if match:
            candidate = match.group(1)
            if self.parse_brain_name(candidate):
                return candidate

        return None

    def _detect_region_csv_format(self, df: pd.DataFrame) -> Optional[Dict]:
        """Detect column names in region CSV."""
        col_map = {}

        # Common column name variations
        region_id_cols = ['region_id', 'id', 'structure_id', 'atlas_id', 'ID']
        region_name_cols = ['region_name', 'name', 'structure_name', 'region', 'Name']
        cell_count_cols = ['cell_count', 'count', 'cells', 'n_cells', 'total_cells', 'Cell Count']
        hemisphere_cols = ['hemisphere', 'side', 'Hemisphere']
        acronym_cols = ['region_acronym', 'acronym', 'abbrev', 'short_name', 'Acronym']
        parent_cols = ['parent_id', 'parent_structure_id', 'parent']
        density_cols = ['cell_density', 'density', 'cells_per_mm3']
        volume_cols = ['total_volume_mm3', 'volume', 'region_volume']
        left_count_cols = ['left_count', 'left', 'left_cells']
        right_count_cols = ['right_count', 'right', 'right_cells']

        for col_list, key in [
            (region_id_cols, 'region_id'),
            (region_name_cols, 'region_name'),
            (cell_count_cols, 'cell_count'),
            (hemisphere_cols, 'hemisphere'),
            (acronym_cols, 'acronym'),
            (parent_cols, 'parent_id'),
            (density_cols, 'density'),
            (volume_cols, 'volume'),
            (left_count_cols, 'left_count'),
            (right_count_cols, 'right_count'),
        ]:
            for col in col_list:
                if col in df.columns:
                    col_map[key] = col
                    break

        # Must have cell_count and at least one of region_id or acronym
        if 'cell_count' not in col_map:
            return None
        if 'region_id' not in col_map and 'acronym' not in col_map:
            return None

        return col_map

    def _parse_float(self, value) -> Optional[float]:
        """Parse float value."""
        if pd.isna(value) if hasattr(pd, 'isna') else value is None:
            return None
        if hasattr(value, 'text'):
            value = value.text
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def _parse_int(self, value) -> Optional[int]:
        """Parse int value."""
        if pd.isna(value) if hasattr(pd, 'isna') else value is None:
            return None
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return None

    def _parse_datetime(self, value) -> Optional[datetime]:
        """Parse datetime value."""
        if pd.isna(value) if hasattr(pd, 'isna') else value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return pd.to_datetime(value)
        except Exception:
            return None

    def _get_result(self) -> Dict:
        """Get import result summary."""
        return {
            'success': len(self.errors) == 0,
            'errors': self.errors,
            'warnings': self.warnings,
            'imported': self.imported_counts,
        }


def import_brainglobe_data(brains_dir: Path, tracker_csv: Optional[Path] = None,
                            dry_run: bool = False) -> List[Dict]:
    """
    Import all BrainGlobe data from a directory structure.

    Args:
        brains_dir: Path to 1_Brains directory
        tracker_csv: Optional path to calibration_runs.csv
        dry_run: If True, validate only

    Returns:
        List of import results
    """
    results = []
    importer = BrainGlobeImporter()

    # Import calibration runs first if tracker CSV provided
    if tracker_csv and tracker_csv.exists():
        result = importer.import_calibration_runs(tracker_csv, dry_run=dry_run)
        result['file'] = str(tracker_csv)
        results.append(result)

    # Find and import brain folders
    # Structure: 1_Brains/{mouse_id}/{brain_id}/
    brain_folders = []
    for mouse_folder in brains_dir.iterdir():
        if mouse_folder.is_dir() and not mouse_folder.name.startswith('.'):
            for brain_folder in mouse_folder.iterdir():
                if brain_folder.is_dir() and importer.parse_brain_name(brain_folder.name):
                    brain_folders.append(brain_folder)

    print(f"Found {len(brain_folders)} brain folders to import")

    for brain_folder in sorted(brain_folders):
        result = importer.import_brain_folder(brain_folder, dry_run=dry_run)
        result['folder'] = str(brain_folder)
        results.append(result)

    # Summary
    total_brains = sum(r.get('imported', {}).get('brain_samples', 0) for r in results)
    total_runs = sum(r.get('imported', {}).get('calibration_runs', 0) for r in results)
    total_regions = sum(r.get('imported', {}).get('region_counts', 0) for r in results)
    total_cells = sum(r.get('imported', {}).get('detected_cells', 0) for r in results)

    print(f"\n{'[DRY RUN] ' if dry_run else ''}BrainGlobe Import Summary:")
    print(f"  Brain samples: {total_brains}")
    print(f"  Calibration runs: {total_runs}")
    print(f"  Region counts: {total_regions}")
    print(f"  Detected cells: {total_cells}")

    return results


# =============================================================================
# BRIDGE FUNCTIONS FOR AUTO-SYNC
# =============================================================================

def sync_brain_to_db(brain_name: str,
                     per_brain_csv=None,
                     region_counts_dict: Optional[Dict[str, int]] = None,
                     hemisphere_counts: Optional[Dict[str, Dict[str, int]]] = None,
                     detection_params: Optional[Dict] = None,
                     is_final: bool = True,
                     exp_id: Optional[str] = None):
    """
    One-call sync: push a brain's region counts and eLife groups to connectome.db.

    Called from pipeline step 6 after writing CSV. Handles:
    - Creating/finding brain_sample
    - Clearing stale data if brain was re-counted (replace semantics)
    - Importing region counts (with acronym-to-ID resolution)
    - Computing and importing eLife grouped counts
    - Linking to calibration run via exp_id

    Never raises -- catches all exceptions and prints warnings.

    Args:
        brain_name: Brain name from pipeline, e.g. "349_CNT_01_02/349_CNT_01_02_1p625x_z4"
                    or just "349_CNT_01_02_1p625x_z4"
        per_brain_csv: Path to the per-brain long-format CSV (string or Path)
        region_counts_dict: Optional pre-parsed {acronym: count}
        hemisphere_counts: Optional {acronym: {'left': N, 'right': N}}
        detection_params: Dict of detection parameters (for provenance)
        is_final: Whether this is a production count
        exp_id: Optional tracker experiment ID to link counts to calibration run
    """
    try:
        from .database import get_db
        from .schema import Base, BrainSample, RegionCount, ElifeRegionCount

        # Extract brain_id from brain_name (handle "349_CNT_01_02/349_CNT_01_02_1p625x_z4")
        brain_id = brain_name
        if '/' in brain_name:
            # Try the part after / first (full brain id with mag and z)
            parts = brain_name.split('/')
            for part in reversed(parts):
                if BrainGlobeImporter.BRAIN_NAME_PATTERN.match(part):
                    brain_id = part
                    break

        db = get_db()

        # Ensure table exists
        Base.metadata.create_all(db.engine)

        importer = BrainGlobeImporter(db)

        # Clear existing counts for this brain (replace semantics for re-counts)
        # This ensures re-running step 6 with new parameters replaces stale data
        brain_info = importer.parse_brain_name(brain_id)
        if brain_info:
            with db.session() as session:
                bs = session.query(BrainSample).filter_by(
                    subject_id=brain_info['subject_id'], brain_id=brain_id
                ).first()
                if bs:
                    old_rc = session.query(RegionCount).filter_by(
                        brain_sample_id=bs.id).count()
                    old_ec = session.query(ElifeRegionCount).filter_by(
                        brain_sample_id=bs.id).count()
                    if old_rc > 0 or old_ec > 0:
                        session.query(RegionCount).filter_by(
                            brain_sample_id=bs.id).delete()
                        session.query(ElifeRegionCount).filter_by(
                            brain_sample_id=bs.id).delete()
                        session.commit()
                        print(f"    [DB] Cleared {old_rc} old region counts, "
                              f"{old_ec} old eLife counts")

        # Resolve exp_id to calibration_run.id (DB FK)
        cal_run_db_id = None
        if exp_id and brain_info:
            from .schema import CalibrationRun
            with db.session() as session:
                bs = session.query(BrainSample).filter_by(
                    subject_id=brain_info['subject_id'], brain_id=brain_id
                ).first()
                if bs:
                    cal_run = session.query(CalibrationRun).filter_by(
                        brain_sample_id=bs.id, run_id=str(exp_id)
                    ).first()
                    if cal_run:
                        cal_run_db_id = cal_run.id

        # Import region counts from CSV if provided
        if per_brain_csv:
            per_brain_csv = Path(per_brain_csv)
            if per_brain_csv.exists():
                result = importer.import_region_counts(
                    per_brain_csv, brain_id=brain_id,
                    calibration_run_id=cal_run_db_id,
                    is_final=is_final, dry_run=False
                )
                if result.get('errors'):
                    for err in result['errors']:
                        print(f"    [DB] Error: {err}")

        # Get or parse region counts for eLife aggregation
        if region_counts_dict is None and per_brain_csv and per_brain_csv.exists():
            # Parse from CSV
            import csv
            region_counts_dict = {}
            hemisphere_counts = hemisphere_counts or {}
            with open(per_brain_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
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

        # Import eLife grouped counts
        if region_counts_dict:
            # Find the brain_sample_id
            brain_info = importer.parse_brain_name(brain_id)
            if brain_info:
                with db.session() as session:
                    bs = session.query(BrainSample).filter_by(
                        subject_id=brain_info['subject_id'],
                        brain_id=brain_id
                    ).first()
                    if bs:
                        elife_result = importer.import_elife_counts(
                            brain_sample_id=bs.id,
                            region_counts_dict=region_counts_dict,
                            hemisphere_counts=hemisphere_counts,
                            calibration_run_id=cal_run_db_id,
                            is_final=is_final,
                            source_file=str(per_brain_csv) if per_brain_csv else None,
                            dry_run=False,
                        )
                        if elife_result.get('errors'):
                            for err in elife_result['errors']:
                                print(f"    [DB] eLife error: {err}")

    except ImportError as e:
        print(f"    [DB] mousedb not available: {e}")
    except Exception as e:
        print(f"    [DB] Warning: brain sync failed: {e}")


def sync_calibration_to_db(brain_name: str, exp_id: str, params_dict: Dict):
    """
    Push a single calibration run to connectome.db.

    Called from napari tuning_widget after detection completes.
    Never raises -- catches all exceptions.

    Args:
        brain_name: Full brain ID, e.g. "349_CNT_01_02_1p625x_z4"
        exp_id: Experiment/run ID from tracker
        params_dict: Dict with keys like ball_xy, ball_z, threshold,
                     cells_detected, status, etc.
    """
    try:
        from .database import get_db
        from .schema import Base, BrainSample, CalibrationRun

        db = get_db()
        Base.metadata.create_all(db.engine)

        importer = BrainGlobeImporter(db)
        brain_info = importer.parse_brain_name(brain_name)
        if not brain_info:
            return

        with db.session() as session:
            brain_sample = importer._ensure_brain_sample(
                session, brain_name, brain_info, dry_run=False
            )
            if not brain_sample:
                return

            # Check for duplicate
            existing = session.query(CalibrationRun).filter_by(
                brain_sample_id=brain_sample.id,
                run_id=str(exp_id)
            ).first()
            if existing:
                return

            cal_run = CalibrationRun(
                brain_sample_id=brain_sample.id,
                run_id=str(exp_id),
                ball_xy_um=params_dict.get('ball_xy'),
                ball_z_um=params_dict.get('ball_z'),
                soma_diameter_um=params_dict.get('soma_diameter'),
                threshold=params_dict.get('threshold'),
                cells_detected=params_dict.get('cells_detected'),
                detection_time_s=params_dict.get('detection_time_s'),
                status=params_dict.get('status', 'completed'),
                is_best=1 if params_dict.get('is_best') else 0,
                source_csv='napari_live_sync',
            )
            session.add(cal_run)
            session.commit()

    except ImportError:
        pass  # mousedb not available in this env
    except Exception:
        pass  # Never break the GUI/pipeline
