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
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from .database import Database, get_db
from .schema import (
    Project, Cohort, Subject, Weight, PelletScore, Surgery, RampEntry,
    LadderEntry, ArchivedSummary, VirusPrep,
    derive_cohort_id, validate_subject_id, TIMELINE,
    BrainSample, RegionCount, DetectedCell, CalibrationRun
)
from datetime import date as date_type
from .validators import (
    validate_weight, validate_pellet_score, validate_sex,
    ValidationError
)


class ExcelImporter:
    """Import existing Excel tracking sheets into SQLite database."""

    def __init__(self, db: Optional[Database] = None):
        self.db = db or get_db()
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.imported_counts: Dict[str, int] = {}

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
            xl = pd.ExcelFile(excel_path)
            available_sheets = xl.sheet_names
        except Exception as e:
            self.errors.append(f"Failed to open Excel file: {e}")
            return self._get_result()

        # Extract cohort info from filename
        cohort_id = self._extract_cohort_from_filename(excel_path.name)
        if not cohort_id:
            self.errors.append(f"Could not determine cohort from filename: {excel_path.name}")
            return self._get_result()

        print(f"  Detected cohort: {cohort_id}")

        with self.db.session() as session:
            # Ensure cohort exists
            cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
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
                    print(f"  Created cohort: {cohort_id} (start: {start_date})")
                else:
                    self.warnings.append(f"Could not determine start date for cohort {cohort_id}")

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
            if '3b_Manual_Tray' in available_sheets:
                self._import_pellet_scores(xl, cohort_id, session, dry_run)

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

            if not dry_run and not self.errors:
                session.commit()
                print(f"  Committed to database")
            elif dry_run:
                session.rollback()
                print(f"  [DRY RUN] Would import: {self.imported_counts}")

        return self._get_result()

    def _extract_cohort_from_filename(self, filename: str) -> Optional[str]:
        """Extract cohort ID from filename like 'Connectome_05_Animal_Tracking.xlsx'."""
        # Try various patterns
        patterns = [
            r'Connectome_(\d+)_',  # Connectome_05_Animal_Tracking.xlsx
            r'Connectome(\d+)_',   # Connectome05_Animal_Tracking.xlsx
            r'CNT_(\d+)',          # CNT_05_tracking.xlsx
            r'CNT(\d+)',           # CNT05_tracking.xlsx
        ]

        for pattern in patterns:
            match = re.search(pattern, filename, re.IGNORECASE)
            if match:
                cohort_num = int(match.group(1))
                return f"CNT_{cohort_num:02d}"

        return None

    def _detect_start_date(self, xl: pd.ExcelFile, sheets: List[str]) -> Optional[datetime]:
        """Try to detect cohort start date from the data."""
        # Try to get earliest date from weight or tray data
        for sheet_name in ['1_Weight', '3b_Manual_Tray']:
            if sheet_name in sheets:
                try:
                    df = pd.read_excel(xl, sheet_name=sheet_name)
                    if 'Date' in df.columns:
                        dates = pd.to_datetime(df['Date'], errors='coerce')
                        min_date = dates.min()
                        if pd.notna(min_date):
                            # Subtract 4 days (training starts day 4)
                            from datetime import timedelta
                            return (min_date - timedelta(days=4)).date()
                except Exception:
                    pass
        return None

    def _import_metadata(self, xl: pd.ExcelFile, cohort_id: str,
                         session, dry_run: bool):
        """Import 0a_Metadata sheet."""
        try:
            df = pd.read_excel(xl, sheet_name='0a_Metadata')
        except Exception as e:
            self.warnings.append(f"Failed to read 0a_Metadata: {e}")
            return

        for _, row in df.iterrows():
            subject_id = row.get('SubjectID') or row.get('Animal')
            if not subject_id or pd.isna(subject_id):
                continue

            # Normalize subject ID
            subject_id = self._normalize_subject_id(subject_id, cohort_id)
            if not subject_id:
                continue

            # Check if subject already exists
            existing = session.query(Subject).filter_by(subject_id=subject_id).first()
            if existing:
                continue

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

    def _import_weights(self, xl: pd.ExcelFile, cohort_id: str,
                        session, dry_run: bool):
        """Import 1_Weight sheet."""
        try:
            df = pd.read_excel(xl, sheet_name='1_Weight')
        except Exception as e:
            self.warnings.append(f"Failed to read 1_Weight: {e}")
            return

        for _, row in df.iterrows():
            subject_id = row.get('Animal') or row.get('SubjectID')
            if not subject_id or pd.isna(subject_id):
                continue

            subject_id = self._normalize_subject_id(subject_id, cohort_id)
            if not subject_id:
                continue

            date_val = self._parse_date(row.get('Date'))
            if not date_val:
                continue

            weight_val = row.get('Weight')
            if pd.isna(weight_val):
                continue

            try:
                weight_val = float(weight_val)
            except (ValueError, TypeError):
                continue

            valid, msg = validate_weight(weight_val)
            if not valid:
                self.warnings.append(f"Invalid weight for {subject_id} on {date_val}: {msg}")
                continue

            # Ensure subject exists
            self._ensure_subject(session, subject_id, cohort_id, dry_run)

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
            return

        if len(df) < 2:
            return

        # Detect format A: vertical/long with column headers
        first_row_vals = [str(df.iloc[0, i]).strip() if pd.notna(df.iloc[0, i]) else ''
                          for i in range(min(6, len(df.columns)))]
        if 'Date' in first_row_vals and ('Animal' in first_row_vals or 'Weight_g' in first_row_vals):
            self._import_weights_vertical(df, cohort_id, session, dry_run)
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

        # Process each animal (rows 2+)
        for row_idx in range(2, len(df)):
            row = df.iloc[row_idx]
            subject_id = row.iloc[0]

            if pd.isna(subject_id):
                continue

            subject_id = self._normalize_subject_id(subject_id, cohort_id)
            if not subject_id:
                continue

            # Ensure subject exists
            self._ensure_subject(session, subject_id, cohort_id, dry_run)

            # Process each date column
            for col_idx, (date_val, phase) in date_cols.items():
                weight_val = row.iloc[col_idx]

                if pd.isna(weight_val):
                    continue

                try:
                    weight_val = float(weight_val)
                except (ValueError, TypeError):
                    continue

                valid, msg = validate_weight(weight_val)
                if not valid:
                    self.warnings.append(f"Invalid weight for {subject_id} on {date_val}: {msg}")
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
                    notes=f"Phase: {phase}" if phase else None,
                    entered_by='excel_import',
                )

                if not dry_run:
                    session.add(weight)
                self.imported_counts['weights'] += 1

    def _import_weights_vertical(self, df: pd.DataFrame, cohort_id: str,
                                  session, dry_run: bool):
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
            subject_id = row.get('Animal')
            if not subject_id or pd.isna(subject_id):
                continue

            subject_id = self._normalize_subject_id(str(subject_id), cohort_id)
            if not subject_id:
                continue

            date_val = self._parse_date(row.get('Date'))
            if not date_val:
                continue

            weight_val = row.get('Weight_g')
            if pd.isna(weight_val):
                continue

            try:
                weight_val = float(weight_val)
            except (ValueError, TypeError):
                continue

            valid, msg = validate_weight(weight_val)
            if not valid:
                self.warnings.append(f"Invalid weight for {subject_id} on {date_val}: {msg}")
                continue

            # Ensure subject exists
            self._ensure_subject(session, subject_id, cohort_id, dry_run)

            # Check for duplicate
            existing = session.query(Weight).filter_by(
                subject_id=subject_id, date=date_val
            ).first()
            if existing:
                continue

            weight_pct = self._parse_float(row.get('Weight_Pct_Baseline'))
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

        Columns: Mouse ID, Date, Weight, % body weight, Tray Start (g), Tray End (g), Dif
        """
        try:
            df = pd.read_excel(xl, sheet_name='3a_Manual_Ramp')
        except Exception as e:
            self.warnings.append(f"Failed to read 3a_Manual_Ramp: {e}")
            return

        for _, row in df.iterrows():
            subject_id = row.get('Mouse ID') or row.get('Animal') or row.get('Subject_ID')
            if not subject_id or pd.isna(subject_id):
                continue

            subject_id = self._normalize_subject_id(subject_id, cohort_id)
            if not subject_id:
                continue

            date_val = self._parse_date(row.get('Date'))
            if not date_val:
                continue

            # Ensure subject exists
            self._ensure_subject(session, subject_id, cohort_id, dry_run)

            # Import weight from ramp data
            weight_val = row.get('Weight')
            weight_pct = self._parse_float(row.get('% body weight'))
            tray_start = self._parse_float(row.get('Tray Start (g)') or row.get('Tray_Start'))
            tray_end = self._parse_float(row.get('Tray End (g)') or row.get('Tray_End'))
            food_consumed = self._parse_float(row.get('Dif') or row.get('Food_Consumed'))

            if pd.notna(weight_val):
                try:
                    weight_val = float(weight_val)
                    valid, _ = validate_weight(weight_val)
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
                    weight_val = None
            else:
                weight_val = None

            # Create RampEntry record with full data
            existing_ramp = session.query(RampEntry).filter_by(
                subject_id=subject_id, date=date_val
            ).first()
            if not existing_ramp and weight_val:
                # Calculate ramp day (0-3) based on cohort start date
                ramp_day = None
                cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
                if cohort and cohort.start_date:
                    ramp_day = (date_val - cohort.start_date).days

                ramp_entry = RampEntry(
                    subject_id=subject_id,
                    date=date_val,
                    ramp_day=ramp_day,
                    body_weight_grams=weight_val,
                    weight_percent_baseline=weight_pct,
                    tray_start_grams=tray_start,
                    tray_end_grams=tray_end,
                    food_consumed_grams=food_consumed if food_consumed else (
                        tray_start - tray_end if tray_start and tray_end else None
                    ),
                    entered_by='excel_import',
                )
                if not dry_run:
                    session.add(ramp_entry)
                self.imported_counts['ramp_entries'] += 1

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
            return

        for _, row in df.iterrows():
            # Try various column name possibilities
            subject_id = (row.get('Subject_ID') or row.get('Animal') or
                         row.get('SubjectID') or row.get('Subject'))
            if not subject_id or pd.isna(subject_id):
                continue

            subject_id = self._normalize_subject_id(subject_id, cohort_id)
            if not subject_id:
                continue

            date_val = self._parse_date(row.get('Surgery_Date') or row.get('Date'))
            if not date_val:
                continue

            # Ensure subject exists
            self._ensure_subject(session, subject_id, cohort_id, dry_run)

            # Update subject death status from Survived column
            survived = row.get('Survived')
            if pd.notna(survived):
                survived_str = str(survived).strip().upper()
                if survived_str in ('N', 'NO', 'FALSE', '0'):
                    # Mark subject as inactive (dead)
                    subject = session.query(Subject).filter_by(subject_id=subject_id).first()
                    if subject and not dry_run:
                        subject.is_active = 0
                        if not subject.date_of_death:
                            subject.date_of_death = date_val

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
                # Map actual column names to our schema
                force_kdyn=self._parse_float(row.get('Actual_kd') or row.get('Force_kDyn')),
                displacement_um=self._parse_float(row.get('Actual_displacement') or row.get('Displacement_um')),
                velocity_mm_s=self._parse_float(row.get('Actual_Velocity') or row.get('Velocity_mm_s')),
                dwell_time_s=self._parse_float(row.get('Actual_Dwell') or row.get('Dwell_time_s')),
                surgeon=row.get('Surgeon') if pd.notna(row.get('Surgeon')) else None,
                anesthesia=row.get('Anesthetic') if pd.notna(row.get('Anesthetic')) else None,
                notes=self._build_surgery_notes(row),
                entered_by='excel_import',
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
            return

        # Pellet columns can be int or str
        pellet_cols = [str(i) for i in range(1, 21)]
        if 1 in df.columns:
            pellet_cols = list(range(1, 21))

        for _, row in df.iterrows():
            subject_id = row.get('Animal') or row.get('SubjectID')
            if not subject_id or pd.isna(subject_id):
                continue

            subject_id = self._normalize_subject_id(subject_id, cohort_id)
            if not subject_id:
                continue

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

            test_phase = row.get('Test_Phase', '')
            if pd.isna(test_phase):
                test_phase = ''

            # Ensure subject exists
            self._ensure_subject(session, subject_id, cohort_id, dry_run)

            # Import each pellet score
            for pellet_num in range(1, 21):
                col = pellet_num if pellet_num in df.columns else str(pellet_num)
                if col not in df.columns:
                    continue

                score = row.get(col)
                if pd.isna(score):
                    continue

                try:
                    score = int(score)
                except (ValueError, TypeError):
                    continue

                valid, msg = validate_pellet_score(score)
                if not valid:
                    self.warnings.append(
                        f"Invalid score for {subject_id} {date_val} T{tray_number}P{pellet_num}: {msg}"
                    )
                    continue

                # Check for duplicate
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
                    test_phase=str(test_phase),
                    tray_type=tray_type,
                    tray_number=tray_number,
                    pellet_number=pellet_num,
                    score=score,
                    entered_by='excel_import',
                )

                if not dry_run:
                    session.add(pellet_score)
                self.imported_counts['pellet_scores'] += 1

    def _import_surgeries(self, xl: pd.ExcelFile, cohort_id: str,
                          session, surgery_type: str, dry_run: bool):
        """Import surgery details sheet."""
        sheet_name = '4_Contusion_Injury_Details' if surgery_type == 'contusion' else '5_SC_Injection_Details'

        try:
            df = pd.read_excel(xl, sheet_name=sheet_name)
        except Exception as e:
            self.warnings.append(f"Failed to read {sheet_name}: {e}")
            return

        for _, row in df.iterrows():
            subject_id = row.get('Animal') or row.get('SubjectID')
            if not subject_id or pd.isna(subject_id):
                continue

            subject_id = self._normalize_subject_id(subject_id, cohort_id)
            if not subject_id:
                continue

            date_val = self._parse_date(row.get('Date') or row.get('Surgery_Date'))
            if not date_val:
                continue

            # Ensure subject exists
            self._ensure_subject(session, subject_id, cohort_id, dry_run)

            # Check for duplicate
            existing = session.query(Surgery).filter_by(
                subject_id=subject_id,
                surgery_date=date_val,
                surgery_type=surgery_type
            ).first()
            if existing:
                continue

            surgery = Surgery(
                subject_id=subject_id,
                surgery_date=date_val,
                surgery_type=surgery_type,
                entered_by='excel_import',
            )

            # Add type-specific fields
            if surgery_type == 'contusion':
                surgery.force_kdyn = self._parse_float(row.get('Force_kDyn') or row.get('Force'))
                surgery.displacement_um = self._parse_float(row.get('Displacement_um') or row.get('Displacement'))
                surgery.velocity_mm_s = self._parse_float(row.get('Velocity_mm_s') or row.get('Velocity'))
                surgery.surgeon = row.get('Surgeon') if pd.notna(row.get('Surgeon')) else None
            elif surgery_type == 'tracing':
                surgery.virus_name = row.get('Virus_Name') if pd.notna(row.get('Virus_Name')) else None
                surgery.volume_nl = self._parse_float(row.get('Volume_nL') or row.get('Volume'))
                surgery.injection_site = row.get('Injection_Site') if pd.notna(row.get('Injection_Site')) else None

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

        first_cell = str(df.iloc[0, 0]).strip().lower() if pd.notna(df.iloc[0, 0]) else ''

        if 'parameter' in first_cell:
            # Vertical format (CNT_05 style)
            self._import_injection_vertical(df, cohort_id, session, dry_run)
        else:
            # Horizontal format (CNT_01 style)
            self._import_injection_horizontal(df, cohort_id, session, dry_run)

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

        # Use today as prep_date since vertical format doesn't have a date
        prep_date = date_type.today()

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
        """Import 6_Ladder sheet -> LadderEntry table."""
        try:
            df = pd.read_excel(xl, sheet_name='6_Ladder', header=None)
        except Exception as e:
            self.warnings.append(f"Failed to read 6_Ladder: {e}")
            return

        if df.shape[0] < 2:
            self.warnings.append("6_Ladder: sheet is empty")
            return

        # Row 0 is headers
        headers = [str(h).strip().lower() if pd.notna(h) else '' for h in df.iloc[0]]

        def find_col(keywords):
            for i, h in enumerate(headers):
                if all(k in h for k in keywords):
                    return i
            return None

        col_animal = find_col(['animal']) or 0
        col_date = find_col(['date']) or 1
        col_test_type = find_col(['test', 'type']) or find_col(['type'])
        col_quality = find_col(['quality'])
        col_succeeded = find_col(['suceeded']) or find_col(['succeeded']) or find_col(['success'])
        col_missed = find_col(['missed']) or find_col(['miss'])
        col_total = find_col(['total'])
        col_pct = find_col(['%']) or find_col(['pct'])

        for row_idx in range(1, len(df)):
            row = df.iloc[row_idx]

            subject_id = row.iloc[col_animal] if pd.notna(row.iloc[col_animal]) else None
            if not subject_id:
                continue

            subject_id = self._normalize_subject_id(subject_id, cohort_id)
            if not subject_id:
                continue

            date_val = self._parse_date(row.iloc[col_date])
            if not date_val:
                continue

            self._ensure_subject(session, subject_id, cohort_id, dry_run)

            # Check for duplicate
            existing = session.query(LadderEntry).filter_by(
                subject_id=subject_id, date=date_val
            ).first()
            if existing:
                continue

            ladder_entry = LadderEntry(
                subject_id=subject_id,
                date=date_val,
                test_type=str(row.iloc[col_test_type]).strip() if col_test_type is not None and pd.notna(row.iloc[col_test_type]) else None,
                camera_type=str(row.iloc[col_quality]).strip() if col_quality is not None and pd.notna(row.iloc[col_quality]) else None,
                steps_succeeded=int(self._parse_float(row.iloc[col_succeeded])) if col_succeeded is not None and pd.notna(row.iloc[col_succeeded]) else None,
                steps_missed=int(self._parse_float(row.iloc[col_missed])) if col_missed is not None and pd.notna(row.iloc[col_missed]) else None,
                steps_total=int(self._parse_float(row.iloc[col_total])) if col_total is not None and pd.notna(row.iloc[col_total]) else None,
                miss_pct=self._parse_float(row.iloc[col_pct]) if col_pct is not None else None,
                entered_by='excel_import',
            )

            if not dry_run:
                session.add(ladder_entry)
            self.imported_counts['ladder_entries'] += 1

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
                subject_id = self._normalize_subject_id(row.iloc[0], cohort_id) if pd.notna(row.iloc[0]) else None
                if not subject_id:
                    continue

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
            if pd.isna(subject_raw):
                continue

            subject_id = self._normalize_subject_id(subject_raw, cohort_id)
            if not subject_id:
                continue  # Skip average rows

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
        """Normalize various subject ID formats to standard format."""
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

    def _ensure_cohort(self, session, cohort_id: str, dry_run: bool):
        """Ensure cohort exists, create if not."""
        existing = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
        if not existing:
            # Create a minimal cohort entry
            project_code = cohort_id.split('_')[0]

            # Ensure project exists
            proj = session.query(Project).filter_by(project_code=project_code).first()
            if not proj:
                proj = Project(project_code=project_code, project_name=project_code)
                if not dry_run:
                    session.add(proj)
                    session.flush()

            cohort = Cohort(
                cohort_id=cohort_id,
                project_code=project_code,
                start_date=date_type.today(),  # Will be updated later if we find data
                notes="Auto-created during import"
            )
            if not dry_run:
                session.add(cohort)
                session.flush()

    def _ensure_subject(self, session, subject_id: str, cohort_id: str, dry_run: bool):
        """Ensure subject exists, create if not."""
        # First ensure cohort exists
        self._ensure_cohort(session, cohort_id, dry_run)

        existing = session.query(Subject).filter_by(subject_id=subject_id).first()
        if not existing:
            subject = Subject(subject_id=subject_id, cohort_id=cohort_id)
            if not dry_run:
                session.add(subject)
                session.flush()
            self.imported_counts['subjects'] += 1

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
        return {
            'success': len(self.errors) == 0,
            'errors': self.errors,
            'warnings': self.warnings,
            'imported': self.imported_counts,
        }


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

        with self.db.session() as session:
            for _, row in df.iterrows():
                brain_id = row.get('brain') or row.get('brain_id')
                if not brain_id or pd.isna(brain_id):
                    continue

                # Parse brain name
                brain_info = self.parse_brain_name(brain_id)
                if not brain_info:
                    self.warnings.append(f"Could not parse brain ID: {brain_id}")
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

        return self._get_result()

    def import_region_counts(self, csv_path: Path, brain_id: Optional[str] = None,
                             calibration_run_id: Optional[int] = None,
                             is_final: bool = False,
                             dry_run: bool = False) -> Dict:
        """
        Import region counts from a CSV file.

        Expected CSV columns: region_id, region_name, hemisphere, cell_count
        Or BrainGlobe format with atlas region hierarchy.

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
                return self._get_result()

            # Detect CSV format and column names
            col_map = self._detect_region_csv_format(df)
            if not col_map:
                self.errors.append("Could not detect CSV format")
                return self._get_result()

            for _, row in df.iterrows():
                region_id = self._parse_int(row.get(col_map.get('region_id', 'region_id')))
                region_name = row.get(col_map.get('region_name', 'region_name'))
                cell_count = self._parse_int(row.get(col_map.get('cell_count', 'cell_count')))

                if region_id is None or cell_count is None:
                    continue

                hemisphere = row.get(col_map.get('hemisphere', 'hemisphere'), 'both')
                if pd.isna(hemisphere):
                    hemisphere = 'both'

                # Check for duplicate
                existing = session.query(RegionCount).filter_by(
                    brain_sample_id=brain_sample.id,
                    region_id=region_id,
                    hemisphere=hemisphere,
                    cell_type='all'
                ).first()
                if existing:
                    continue

                region_count = RegionCount(
                    brain_sample_id=brain_sample.id,
                    region_id=region_id,
                    region_name=str(region_name) if region_name else f"Region_{region_id}",
                    region_acronym=row.get(col_map.get('acronym', 'acronym')) if col_map.get('acronym') else None,
                    parent_region_id=self._parse_int(row.get(col_map.get('parent_id', 'parent_structure_id'))),
                    hemisphere=str(hemisphere),
                    cell_count=cell_count,
                    cell_density=self._parse_float(row.get(col_map.get('density', 'cell_density'))),
                    region_volume_mm3=self._parse_float(row.get(col_map.get('volume', 'total_volume_mm3'))),
                    cell_type='all',
                    calibration_run_id=calibration_run_id,
                    is_final=1 if is_final else 0,
                    source_file=str(csv_path),
                )

                if not dry_run:
                    session.add(region_count)
                self.imported_counts['region_counts'] += 1

            if not dry_run and not self.errors:
                session.commit()
                print(f"  Committed {self.imported_counts['region_counts']} region counts")

        return self._get_result()

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
            # Create minimal subject
            self.warnings.append(f"Subject {subject_id} not found, creating placeholder")
            cohort_id = brain_info['cohort_id']

            # Ensure cohort exists
            from .schema import Cohort, Project
            cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
            if not cohort:
                project_code = brain_info['project_code']
                project = session.query(Project).filter_by(project_code=project_code).first()
                if not project:
                    project = Project(project_code=project_code, project_name=project_code)
                    if not dry_run:
                        session.add(project)
                        session.flush()

                cohort = Cohort(
                    cohort_id=cohort_id,
                    project_code=project_code,
                    start_date=date_type.today(),
                    notes="Auto-created during BrainGlobe import"
                )
                if not dry_run:
                    session.add(cohort)
                    session.flush()

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

    def _extract_brain_from_path(self, path: Path) -> Optional[str]:
        """Try to extract brain ID from file path."""
        # Check each part of the path
        for part in reversed(path.parts):
            if self.parse_brain_name(part):
                return part
        return None

    def _detect_region_csv_format(self, df: pd.DataFrame) -> Optional[Dict]:
        """Detect column names in region CSV."""
        col_map = {}

        # Common column name variations
        region_id_cols = ['region_id', 'id', 'structure_id', 'atlas_id', 'ID']
        region_name_cols = ['region_name', 'name', 'structure_name', 'region', 'Name']
        cell_count_cols = ['cell_count', 'count', 'cells', 'n_cells', 'total_cells', 'Cell Count']
        hemisphere_cols = ['hemisphere', 'side', 'Hemisphere']
        acronym_cols = ['acronym', 'abbrev', 'short_name', 'Acronym']
        parent_cols = ['parent_id', 'parent_structure_id', 'parent']
        density_cols = ['cell_density', 'density', 'cells_per_mm3']
        volume_cols = ['total_volume_mm3', 'volume', 'region_volume']

        for col_list, key in [
            (region_id_cols, 'region_id'),
            (region_name_cols, 'region_name'),
            (cell_count_cols, 'cell_count'),
            (hemisphere_cols, 'hemisphere'),
            (acronym_cols, 'acronym'),
            (parent_cols, 'parent_id'),
            (density_cols, 'density'),
            (volume_cols, 'volume'),
        ]:
            for col in col_list:
                if col in df.columns:
                    col_map[key] = col
                    break

        # Must have at least region_id and cell_count
        if 'region_id' not in col_map or 'cell_count' not in col_map:
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
