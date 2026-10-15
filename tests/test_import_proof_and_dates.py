"""The tracking-sheet importer must stop creating new bad data.

WHY these tests exist. Five habits of the sheet importer (mousedb.importers
.ExcelImporter) each put something into the database that never happened:

1. Planned surgeries stored as performed. A row counted as a real surgery if
   any of a dozen fields was filled -- virus, titre, target, depths,
   coordinates, Survived, Actual_Dwell. Every one of those can be typed (or
   generated, or carried over from the previous cohort's workbook) before the
   surgery, so planned injections reached the database and the published
   exports as performed. The only proof is the measurement taken on the day:
   the animal's weight on the table. A row without it is a plan and is
   skipped. A plan is not a problem -- the sheet is filled in ahead of time
   on purpose -- so it is reported only once it is overdue: dated more than
   animal_records.PLANNED_SURGERY_GRACE_DAYS ago. An undated plan is never
   reported. The rule itself is animal_records.surgery_verdict, shared with
   the other reader of these tabs (tests/test_animal_records_proof.py).

2. Invented dates. A cohort with no readable start date was created with the
   day the import happened to run as its start; a virus calculator with no
   date became a prep dated "today" (and, the date being the record's key, a
   new copy every day the import ran); a brain whose cohort was unknown created
   that cohort with today's date too. None of those dates was recorded by
   anyone. Now: a placeholder start, when one is unavoidable, is the earliest
   date really in the sheet and is labelled as a placeholder in words; a
   record with no date at all is not imported, and a warning says so.

3. Borrowed animals filed under the borrower. An id of one cohort typed in
   another cohort's workbook was filed under the borrower (a second, wrong animal), or
   the borrower's copy wrote onto the home animal. The id's own cohort decides;
   a foreign id is skipped with a warning naming the id, the tab and the
   workbook's cohort. Text that is not an id at all is reported too.

4. Animals created by an abandoned test. The ladder tab is half filled
   (animal, date, no counts) and every row used to create its animal before
   the counts were read -- one subject existed only because of two count-less
   ladder rows. Ladder rows never create animals now. The test was abandoned,
   so rows without counts are ignored without a word.

5. Silent skips. Every row the importer decides not to import because
   something is WRONG is in the result's warnings with the tab, the animal
   (and date) and the reason -- a warning nobody can see is a silent drop.
   (Plans and the abandoned ladder test are not wrong; see 1 and 4.)

6. Half a workbook saved. The import runs in one database session, which
   commits whenever its block ends normally -- even after the importer
   recorded an error. An import that records an error must save nothing.

The importer stays add-only: these tests never expect an existing row to be
changed, only that bad rows stop arriving.
"""
from datetime import date, timedelta

import openpyxl
import pandas as pd
import pytest

from mousedb import database as dbmod
from mousedb.animal_records import PLANNED_SURGERY_GRACE_DAYS
from mousedb.importers import BrainGlobeImporter, ExcelImporter
from mousedb.schema import (
    Cohort, LadderEntry, PelletScore, Subject, Surgery, VirusPrep, Weight,
)


CONTUSION_HEADERS = [
    'Subject_ID', 'Surgery_Date', 'Surgery_Type', 'Surgery_Severity',
    'Contusion_Location', 'Subject_Weight (g)', 'Anesthetic', 'Anesthetic_Dose',
    'Anesthetic_Volume', 'Analgesic', 'Analgesic_Dose', 'Analgesic_Volume',
    'Intended_kd', 'Intended_Dwell', 'Stage_Height', 'Actual_kd',
    'Actual_displacement', 'Actual_Velocity', 'Actual_Dwell', 'Survived',
]
INJECTION_HEADERS = [
    'Subject_ID', 'Surgery_Date', 'Subject_Weight (g)', 'Surgery_Type',
    'Injected_Virus', 'Virus_Titer', 'Injection_Target', 'Depths (D/V)',
    'Coordinates (M/L)', 'Anesthetic', 'Anesthetic_Dose', 'Anesthetic_Volume',
    'Analgesic', 'Analgesic_Dose', 'Analgesic_Volume', 'Survived',
    'Signal Post Perfusion',
]
WEIGHTS_HEADERS = ['Date', 'Day', 'Animal', 'Weight_g', 'Weight_Pct_Baseline', 'Notes']
TRAY_HEADERS = (['Date', 'Animal', 'Sex', 'Weight', 'Weight %', 'Test_Phase',
                 'Tray Type/Number'] + list(range(1, 21)))
OLD_LADDER_HEADERS = ['Animal ID', 'Date', 'Test Type', 'Quality', 'Steps suceeded',
                      'Steps missed', 'Steps taken total', '%Missed']
NEW_LADDER_HEADERS = ['Date', 'Animal', 'Test_Type', 'Camera_Type', 'Total_Steps',
                      'Total_Misses', 'Miss_Pct', 'Notes']

DAY0 = date(2026, 3, 1)


def contusion(animal, when=None, weight=None, **extra):
    """One 4_Contusion row. Defaults are a PLAN: the constants a generated or
    copied workbook pre-fills for every animal, and no weight."""
    row = dict.fromkeys(CONTUSION_HEADERS)
    row.update({'Subject_ID': animal, 'Surgery_Date': when, 'Surgery_Type': 'Contusion',
                'Surgery_Severity': 'severity S', 'Contusion_Location': 'level L',
                'Anesthetic': 'drug A', 'Analgesic': 'drug B', 'Intended_kd': 60,
                'Stage_Height': 0, 'Actual_Dwell': 0, 'Subject_Weight (g)': weight})
    row.update(extra)
    return [row[h] for h in CONTUSION_HEADERS]


def injection(animal, when=None, weight=None, **extra):
    """One 5_SC row; by default a PLAN (virus, titre, target, depths and
    coordinates typed in advance, no weight)."""
    row = dict.fromkeys(INJECTION_HEADERS)
    row.update({'Subject_ID': animal, 'Surgery_Date': when, 'Surgery_Type': 'Injection',
                'Injected_Virus': 'virus X', 'Virus_Titer': '1x10^12',
                'Injection_Target': 'target T', 'Depths (D/V)': '-1/-2',
                'Coordinates (M/L)': '+/-1', 'Anesthetic': 'drug A', 'Survived': 'Y',
                'Subject_Weight (g)': weight})
    row.update(extra)
    return [row[h] for h in INJECTION_HEADERS]


def weights(*animals, when=DAY0):
    """A long-format weights tab: one day-0 weigh-in per animal (gives the
    cohort a real start date and creates the animals)."""
    return [WEIGHTS_HEADERS] + [[when, 0, a, 20.0, 100, None] for a in animals]


def tray_row(animal, when, weight_pct=None):
    return [when, animal, 'F', 20.0, weight_pct, None, 'P1'] + [2] * 20


def workbook(tmp_path, cohort_num, tabs):
    """Write a workbook named the way the importer reads the cohort from it."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in tabs.items():
        ws = wb.create_sheet(title)
        for r in rows:
            ws.append(list(r))
    path = tmp_path / ("Connectome_%02d_Animal_Tracking.xlsx" % cohort_num)
    wb.save(path)
    return path


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A scratch database; audit log files stay under tmp_path."""
    monkeypatch.setattr(dbmod, "DEFAULT_LOG_PATH", None)
    d = dbmod.Database(tmp_path / "scratch.db")
    d.init_db()
    return d


def warnings_with(result, *needles):
    return [w for w in result['warnings'] if all(n in w for n in needles)]


# ---------------------------------------------------------------------------
# 1. Only the weight on the day proves a surgery happened
# ---------------------------------------------------------------------------

class TestSurgeryProof:
    @staticmethod
    def _gate(row, tab, cohort='CNT_05'):
        """The importer's real per-row gate, as both surgery readers call it."""
        imp = ExcelImporter.__new__(ExcelImporter)
        imp.warnings = []
        return imp._performed_surgery(row, row['Subject_ID'], cohort, tab)

    def test_prefilled_constants_and_outcome_flags_are_not_proof(self):
        planned_contusion = pd.Series(dict(zip(CONTUSION_HEADERS, contusion(
            'CNT_05_02', DAY0, Survived='N', Actual_Dwell=0))))
        planned_injection = pd.Series(dict(zip(INJECTION_HEADERS, injection(
            'CNT_05_02', DAY0))))
        assert self._gate(planned_contusion, '4_Contusion_Injury_Details') is None
        assert self._gate(planned_injection, '5_SC_Injection_Details') is None

    @pytest.mark.parametrize("weight, performed", [
        (22.4, True),
        ("22.4 g", True),      # typed with its unit: still the weight
        ("22,4", True),        # decimal comma
        (0, False),            # what an unfilled template row holds
        (None, False),
        ("n/a", False),
    ])
    def test_the_weight_on_the_day_is_the_proof(self, weight, performed):
        row = pd.Series(dict(zip(CONTUSION_HEADERS, contusion('CNT_05_01', DAY0, weight))))
        assert (self._gate(row, '4_Contusion_Injury_Details') is not None) is performed

    def test_the_weight_that_proved_an_injection_is_the_weight_stored(self, tmp_path, db):
        # '22.4 g' proves the surgery under the shared rule; it used to be
        # stored as blank because the stored value was parsed separately.
        path = workbook(tmp_path, 5, {
            '3d_Weights': weights('CNT_05_01'),
            '5_SC_Injection_Details': [INJECTION_HEADERS,
                                       injection('CNT_05_01', date(2026, 4, 1), '22.4 g')],
        })
        result = ExcelImporter(db).import_cohort_file(path)
        assert result['success'], result['errors']
        with db.session() as s:
            assert s.query(Surgery).one().pre_surgery_weight_grams == 22.4

    def test_plans_are_skipped_and_only_overdue_ones_reported(self, tmp_path, db):
        # A plan is not a problem: the sheet is filled in ahead of time on
        # purpose. Only a DATED plan whose day is more than the grace window
        # past is reported (done and not written down, or not done); an
        # undated plan or one still inside the window is silent.
        today = date(2026, 6, 1)
        overdue = today - timedelta(days=PLANNED_SURGERY_GRACE_DAYS + 1)
        on_the_edge = today - timedelta(days=PLANNED_SURGERY_GRACE_DAYS)
        ahead = today + timedelta(days=3)
        path = workbook(tmp_path, 5, {
            '3d_Weights': weights('CNT_05_01', 'CNT_05_02', 'CNT_05_03',
                                  'CNT_05_04', 'CNT_05_05'),
            '4_Contusion_Injury_Details': [
                CONTUSION_HEADERS,
                contusion('CNT_05_01', overdue, 22.0, Actual_kd=61, Survived='Y'),
                # a plan carrying an outcome flag: must not be stored, and must
                # not mark a living animal dead
                contusion('CNT_05_02', overdue, Survived='N'),
                contusion('CNT_05_03'),                 # undated: never reported
                contusion('CNT_05_04', on_the_edge),    # inside the window
                contusion('CNT_05_05', ahead),          # not due yet
            ],
            '5_SC_Injection_Details': [
                INJECTION_HEADERS,
                injection('CNT_05_01', overdue, 21.5),
                injection('CNT_05_02', overdue),
                injection('CNT_05_03'),
            ],
        })
        imp = ExcelImporter(db)
        imp.today = today
        result = imp.import_cohort_file(path)
        assert result['success'], result['errors']

        with db.session() as s:
            stored = sorted((x.subject_id, x.surgery_type) for x in s.query(Surgery).all())
            planned_animal = s.query(Subject).filter_by(subject_id='CNT_05_02').one()
            assert planned_animal.is_active == 1
            assert planned_animal.date_of_death is None
        assert stored == [('CNT_05_01', 'contusion'), ('CNT_05_01', 'tracing')]

        contusion_warning = warnings_with(
            result, '4_Contusion_Injury_Details', 'surgery planned but not recorded')
        assert len(contusion_warning) == 1
        assert '(1): CNT_05_02 (%s)' % overdue in contusion_warning[0]
        assert '%d days' % PLANNED_SURGERY_GRACE_DAYS in contusion_warning[0]
        assert warnings_with(result, '5_SC_Injection_Details', 'planned but not recorded',
                             'CNT_05_02 (%s)' % overdue)
        # Nothing at all is said about the plans that are not overdue.
        for quiet in ('CNT_05_03', 'CNT_05_04', 'CNT_05_05'):
            assert not [w for w in result['warnings'] if quiet in w], quiet

    def test_a_cohort_still_in_training_imports_without_a_word_about_its_plans(
            self, tmp_path, db):
        # Every animal has an undated surgery row pre-filled with the protocol
        # constants: what a generated workbook looks like before the injury.
        animals = ['CNT_05_%02d' % n for n in range(1, 9)]
        path = workbook(tmp_path, 5, {
            '3d_Weights': weights(*animals),
            '4_Contusion_Injury_Details': [CONTUSION_HEADERS] + [contusion(a) for a in animals],
            '5_SC_Injection_Details': [INJECTION_HEADERS] + [injection(a) for a in animals],
        })
        result = ExcelImporter(db).import_cohort_file(path)
        assert result['success'], result['errors']
        assert not [w for w in result['warnings'] if 'Injury_Details' in w or '5_SC' in w]

    def test_a_weighed_row_without_a_date_is_reported_not_stored(self, tmp_path, db):
        path = workbook(tmp_path, 5, {
            '3d_Weights': weights('CNT_05_01'),
            '4_Contusion_Injury_Details': [CONTUSION_HEADERS,
                                           contusion('CNT_05_01', None, 22.0)],
        })
        result = ExcelImporter(db).import_cohort_file(path)
        with db.session() as s:
            assert s.query(Surgery).count() == 0
        found = warnings_with(result, '4_Contusion_Injury_Details', 'CNT_05_01 (no date)',
                              'not imported', 'no Surgery_Date')
        assert len(found) == 1

    @pytest.mark.parametrize("extra, needle", [
        ({'Subject_Weight (g)': '22 kg'}, 'not a weight in grams'),
        ({'Subject_Weight (g)': None, 'Actual_kd': 61, 'Actual_displacement': 500},
         'device reading'),
        ({'Subject_Weight (g)': 22.0, 'Surgery_Date': 'TBD'}, 'not a date'),
    ])
    def test_a_row_that_looks_done_but_cannot_be_taken_as_done_is_named_every_run(
            self, tmp_path, db, extra, needle):
        # Held, not planned: unlike a plan these are problems a person must fix,
        # so they are reported whatever their date, on every import.
        row = contusion('CNT_05_01', date(2026, 4, 1), **extra)
        path = workbook(tmp_path, 5, {
            '3d_Weights': weights('CNT_05_01'),
            '4_Contusion_Injury_Details': [CONTUSION_HEADERS, row],
        })
        for _ in range(2):
            result = ExcelImporter(db).import_cohort_file(path)
            found = warnings_with(result, '4_Contusion_Injury_Details', 'CNT_05_01', needle)
            assert len(found) == 1, result['warnings']
            assert all(w.isascii() for w in result['warnings'])
        with db.session() as s:
            assert s.query(Surgery).count() == 0

    def test_a_tab_without_a_weight_column_imports_nothing_and_says_so(self, tmp_path, db):
        headers = [h for h in CONTUSION_HEADERS if h != 'Subject_Weight (g)']
        rows = [[v for h, v in zip(CONTUSION_HEADERS, contusion(a, date(2026, 4, 1), 22.0))
                 if h != 'Subject_Weight (g)'] for a in ('CNT_05_01', 'CNT_05_02')]
        path = workbook(tmp_path, 5, {
            '3d_Weights': weights('CNT_05_01', 'CNT_05_02'),
            '4_Contusion_Injury_Details': [headers] + rows,
        })
        result = ExcelImporter(db).import_cohort_file(path)
        with db.session() as s:
            assert s.query(Surgery).count() == 0
        found = warnings_with(result, '4_Contusion_Injury_Details',
                              "no 'Subject_Weight (g)' column", 'restore')
        assert len(found) == 1
        # One line for the cause, not one per row.
        assert not [w for w in result['warnings'] if 'CNT_05_02' in w]

    def test_a_weighed_row_without_an_animal_is_reported_not_dropped(self, tmp_path, db):
        # A blank animal cell is usually padding and stays silent, but a row
        # with the weight on the day typed is a surgery that happened: losing
        # it without a word is a silent drop.
        path = workbook(tmp_path, 5, {
            '3d_Weights': weights('CNT_05_01'),
            '4_Contusion_Injury_Details': [
                CONTUSION_HEADERS,
                contusion('CNT_05_01', date(2026, 4, 1), 22.0),
                contusion(None, date(2026, 4, 2), 21.0),   # sheet row 3
                contusion(None),                            # padding: silent
            ],
            '5_SC_Injection_Details': [INJECTION_HEADERS,
                                       injection(None, date(2026, 4, 3), 20.5)],
        })
        result = ExcelImporter(db).import_cohort_file(path)
        with db.session() as s:
            assert s.query(Surgery).count() == 1
        contusion_rows = warnings_with(result, '4_Contusion_Injury_Details',
                                       'animal id is blank')
        assert len(contusion_rows) == 1
        assert '(1): sheet row 3 (2026-04-02)' in contusion_rows[0]
        assert warnings_with(result, '5_SC_Injection_Details', 'animal id is blank',
                             'sheet row 2 (2026-04-03)')


# ---------------------------------------------------------------------------
# 2. No date is ever invented
# ---------------------------------------------------------------------------

class TestNoInventedDates:
    def test_a_cohort_without_a_start_gets_its_earliest_record_as_a_labelled_placeholder(
            self, tmp_path, db):
        # A pilot-like workbook: surgeries only, no weigh-in, no tray rows.
        # The later surgery comes first in the sheet: the placeholder must
        # still be the earliest date, so no record lands before day 0.
        path = workbook(tmp_path, 7, {
            '4_Contusion_Injury_Details': [
                CONTUSION_HEADERS,
                contusion('CNT_07_01', date(2026, 3, 10), 22.0),
                contusion('CNT_07_02', date(2026, 3, 3), 21.0),
            ],
        })
        result = ExcelImporter(db).import_cohort_file(path)
        assert result['success'], result['errors']

        with db.session() as s:
            cohort = s.query(Cohort).filter_by(cohort_id='CNT_07').one()
            assert cohort.start_date == date(2026, 3, 3)
            assert cohort.start_date != date.today()
            # The marker that lets the next import replace it, and words saying
            # the date is not a real start.
            assert cohort.notes.lower().startswith('auto-created')
            assert 'NOT known' in cohort.notes
            assert s.query(Surgery).count() == 2
        assert warnings_with(result, 'CNT_07', 'PLACEHOLDER', '2026-03-03')

    def test_the_placeholder_is_replaced_once_the_sheet_has_a_start(self, tmp_path, db):
        surgeries = [CONTUSION_HEADERS, contusion('CNT_07_01', date(2026, 3, 10), 22.0)]
        ExcelImporter(db).import_cohort_file(
            workbook(tmp_path, 7, {'4_Contusion_Injury_Details': surgeries}))

        later = tmp_path / "later"
        later.mkdir()
        ExcelImporter(db).import_cohort_file(workbook(later, 7, {
            '3d_Weights': weights('CNT_07_01', when=date(2026, 2, 20)),
            '4_Contusion_Injury_Details': surgeries,
        }))
        with db.session() as s:
            cohort = s.query(Cohort).filter_by(cohort_id='CNT_07').one()
            assert cohort.start_date == date(2026, 2, 20)
            assert not cohort.notes.lower().startswith('auto-created')

    def test_with_no_date_at_all_no_cohort_is_created(self, db):
        imp = ExcelImporter(db)
        with db.session() as s:
            assert imp._ensure_cohort(s, 'CNT_08', dry_run=False) is False
            assert imp._ensure_subject(s, 'CNT_08_01', 'CNT_08', dry_run=False) is False
            assert s.query(Cohort).filter_by(cohort_id='CNT_08').first() is None
            assert s.query(Subject).filter_by(subject_id='CNT_08_01').first() is None
        assert [w for w in imp.warnings if 'CNT_08' in w and 'NOT imported' in w]

    def test_metadata_for_a_cohort_with_no_start_is_reported_not_a_crash(self, tmp_path, db):
        # Foreign keys are enforced: this used to file the animal under a
        # cohort row that did not exist, and the whole import failed.
        path = workbook(tmp_path, 8, {'0a_Metadata': [
            ['SubjectID', 'Date_of_Birth', 'Date_of_Death', 'Sex', 'Cohort', 'Notes'],
            ['CNT_08_01', date(2026, 1, 1), None, 'F', 'CNT_08', None],
        ]})
        result = ExcelImporter(db).import_cohort_file(path)
        assert result['success'], result['errors']
        with db.session() as s:
            assert s.query(Subject).count() == 0
            assert s.query(Cohort).filter_by(cohort_id='CNT_08').first() is None
        assert warnings_with(result, 'Cohort CNT_08', 'NOT imported')

    def test_a_dry_run_of_a_new_cohort_reports_no_placeholder(self, tmp_path, db):
        # The cohort would get a real start date; a dry run adds nothing, and
        # must not therefore claim that a placeholder would be made.
        path = workbook(tmp_path, 9, {
            '3d_Weights': weights('CNT_09_01'),
            '4_Contusion_Injury_Details': [CONTUSION_HEADERS,
                                           contusion('CNT_09_01', date(2026, 4, 1), 22.0)],
        })
        result = ExcelImporter(db).import_cohort_file(path, dry_run=True)
        assert not warnings_with(result, 'PLACEHOLDER')
        assert not warnings_with(result, 'nothing could be filed')
        assert result['imported']['surgeries'] == 1
        with db.session() as s:
            assert s.query(Cohort).filter_by(cohort_id='CNT_09').first() is None

    def test_a_virus_calculator_without_a_date_imports_nothing(self):
        imp = ExcelImporter.__new__(ExcelImporter)
        imp.warnings, imp.errors = [], []
        imp.imported_counts = {'virus_preps': 0}
        session = _FakeSession()
        calculator = pd.DataFrame([['Parameter', 'Value', 'Units'],
                                   ['Virus Name', 'virus X', None],
                                   ['Virus Titer (GC/mL)', 1e12, 'GC/mL']])
        imp._import_injection_vertical(calculator, 'CNT_05', session, False)
        assert session.added == []
        assert [w for w in imp.warnings if 'NOT imported' in w and 'no date' in w]

    def test_a_virus_calculator_with_a_date_row_uses_it(self):
        imp = ExcelImporter.__new__(ExcelImporter)
        imp.warnings, imp.errors = [], []
        imp.imported_counts = {'virus_preps': 0}
        session = _FakeSession()
        calculator = pd.DataFrame([['Parameter', 'Value', 'Units'],
                                   ['Virus Name', 'virus X', None],
                                   ['Prep date', pd.Timestamp(2026, 8, 26), None]])
        imp._import_injection_vertical(calculator, 'CNT_05', session, False)
        assert [str(v.prep_date) for v in session.added] == ['2026-08-26']

    def test_a_brain_of_an_unknown_cohort_waits_instead_of_inventing_one(self, db):
        imp = BrainGlobeImporter(db)
        brain = '357_CNT_09_08_1p625x_z4'
        with db.session() as s:
            assert imp._ensure_brain_sample(s, brain, imp.parse_brain_name(brain), False) is None
            assert s.query(Cohort).filter_by(cohort_id='CNT_09').first() is None
        assert [w for w in imp.warnings if brain in w and 'NOT imported' in w]

    def test_a_calculator_date_named_with_an_underscore_is_read(self):
        # The sheets name dates Prep_Date / Surgery_Date. To a regex \b the
        # underscore is part of the word, so such a row went unseen and the
        # prep was refused while the warning asked for exactly that row.
        imp = ExcelImporter.__new__(ExcelImporter)
        imp.warnings, imp.errors = [], []
        imp.imported_counts = {'virus_preps': 0}
        session = _FakeSession()
        calculator = pd.DataFrame([['Parameter', 'Value', 'Units'],
                                   ['Virus Name', 'virus X', None],
                                   ['Update notes', 'none', None],
                                   ['Prep_Date', pd.Timestamp(2026, 8, 26), None]])
        imp._import_injection_vertical(calculator, 'CNT_05', session, False)
        assert [str(v.prep_date) for v in session.added] == ['2026-08-26']

    def test_a_refused_brain_says_why_in_its_error(self, tmp_path, db):
        # `import-brains --all` prints each brain's errors but never its
        # warnings, so an error that only points at a warning hides the reason.
        brain = '357_CNT_09_08_1p625x_z4'
        csv_path = tmp_path / (brain + '_counts.csv')
        csv_path.write_text('region,count\nroot,1\n')
        result = BrainGlobeImporter(db).import_region_counts(csv_path, brain_id=brain)
        assert not result['success']
        assert [e for e in result['errors']
                if brain in e and 'CNT_09' in e and 'not in the database' in e]

    def test_a_brain_of_a_known_cohort_still_gets_its_placeholder_animal(self, db):
        with db.session() as s:
            s.add(Cohort(cohort_id='CNT_02', project_code='CNT', start_date=DAY0))
        imp = BrainGlobeImporter(db)
        brain = '357_CNT_02_08_1p625x_z4'
        with db.session() as s:
            assert imp._ensure_brain_sample(s, brain, imp.parse_brain_name(brain), False) is not None
            assert s.query(Subject).filter_by(subject_id='CNT_02_08').one().cohort_id == 'CNT_02'


class _FakeSession:
    """Just enough session for the calculator importer: nothing exists yet."""
    def __init__(self):
        self.added = []

    def query(self, *a):
        return self

    def filter_by(self, **kw):
        return self

    def first(self):
        return None

    def add(self, obj):
        self.added.append(obj)


# ---------------------------------------------------------------------------
# 3. An animal is imported only from its own cohort's workbook
# ---------------------------------------------------------------------------

class TestIdsOfOtherCohorts:
    def test_a_borrowed_animal_is_not_filed_under_the_borrowing_cohort(self, tmp_path, db):
        path = workbook(tmp_path, 2, {
            '3d_Weights': weights('CNT_02_01'),
            '4_Contusion_Injury_Details': [
                CONTUSION_HEADERS,
                contusion('CNT_02_01', date(2026, 4, 1), 22.0),
                contusion('CNT_03_05', date(2026, 4, 1), 23.0),
            ],
        })
        result = ExcelImporter(db).import_cohort_file(path)
        with db.session() as s:
            assert [x.subject_id for x in s.query(Surgery).all()] == ['CNT_02_01']
            assert s.query(Subject).filter_by(subject_id='CNT_03_05').first() is None
        found = warnings_with(result, '4_Contusion_Injury_Details', 'CNT_03_05',
                              'cohort CNT_03', "workbook's cohort CNT_02")
        assert len(found) == 1

    def test_a_borrowed_copy_does_not_write_onto_the_home_animal(self, tmp_path, db):
        session_day = date(2026, 3, 10)
        with db.session() as s:
            s.add(Cohort(cohort_id='CNT_03', project_code='CNT', start_date=DAY0))
            s.flush()
            s.add(Subject(subject_id='CNT_03_05', cohort_id='CNT_03'))
            s.flush()
            s.add(Weight(subject_id='CNT_03_05', date=session_day, weight_grams=20.0))
        path = workbook(tmp_path, 2, {
            '3d_Weights': weights('CNT_02_01'),
            '3b_Manual_Tray': [TRAY_HEADERS, tray_row('CNT_03_05', session_day, 0.9)],
            '4_Contusion_Injury_Details': [CONTUSION_HEADERS,
                                           contusion('CNT_03_05', date(2026, 4, 1), 23.0)],
        })
        ExcelImporter(db).import_cohort_file(path)
        with db.session() as s:
            assert s.query(Surgery).filter_by(subject_id='CNT_03_05').count() == 0
            assert s.query(PelletScore).filter_by(subject_id='CNT_03_05').count() == 0
            home = s.query(Weight).filter_by(subject_id='CNT_03_05').one()
            assert home.weight_percent is None
            assert s.query(Subject).filter_by(subject_id='CNT_03_05').one().cohort_id == 'CNT_03'

    def test_text_that_is_not_an_id_is_reported_in_plain_ascii(self, tmp_path, db):
        path = workbook(tmp_path, 5, {
            '3d_Weights': weights('CNT_05_01'),
            '3b_Manual_Tray': [TRAY_HEADERS,
                               tray_row('CNT_05_01', DAY0),
                               tray_row('Mouse \u00b5', DAY0)],
        })
        result = ExcelImporter(db).import_cohort_file(path)
        found = warnings_with(result, '3b_Manual_Tray', 'not an animal id', 'Mouse')
        assert len(found) == 1
        # Warnings are printed to Windows consoles, which crash on non-ASCII.
        assert all(w.isascii() for w in result['warnings'])

    def test_ensure_subject_refuses_an_id_of_another_cohort(self, db):
        imp = ExcelImporter(db)
        with db.session() as s:
            s.add(Cohort(cohort_id='CNT_02', project_code='CNT', start_date=DAY0))
            s.flush()
            assert imp._ensure_subject(s, 'CNT_03_05', 'CNT_02', dry_run=False) is False
            assert s.query(Subject).filter_by(subject_id='CNT_03_05').first() is None
        assert [w for w in imp.warnings if 'CNT_03_05' in w and 'CNT_02' in w]

    def test_a_three_digit_number_is_reported_not_a_crash(self):
        # A bare number spells "animal N of this cohort"; with three digits it
        # spells an id no animal can have, which used to raise mid-import.
        imp = ExcelImporter.__new__(ExcelImporter)
        imp.warnings = []
        assert imp._resolve_subject_id(5, 'CNT_04', '7_Stats') == 'CNT_04_05'
        assert imp._resolve_subject_id('100', 'CNT_04', '7_Stats') is None
        imp._flush_unimported()
        assert [w for w in imp.warnings if w.startswith('7_Stats') and "'100'" in w]


# ---------------------------------------------------------------------------
# 4. Ladder rows never create animals; count-less rows are not data
# ---------------------------------------------------------------------------

class TestLadder:
    def test_count_less_rows_create_nothing_and_are_ignored_silently(self, tmp_path, db):
        # The ladder test was abandoned: its empty rows will never be filled in,
        # so a line about them on every import would only be noise.
        path = workbook(tmp_path, 4, {
            '3d_Weights': weights('CNT_04_01'),
            '6_Ladder': [OLD_LADDER_HEADERS,
                         ['CNT_04_01', date(2026, 3, 20), 'Uninjured', 'Camera C', None, None, None, None],
                         ['CNT_04_16', date(2026, 3, 20), 'Uninjured', 'Camera C', None, None, None, None],
                         ['CNT_04_16', date(2026, 4, 20), 'Injured', 'Camera C', None, None, None, None]],
        })
        result = ExcelImporter(db).import_cohort_file(path)
        with db.session() as s:
            assert s.query(Subject).filter_by(subject_id='CNT_04_16').first() is None
            assert s.query(LadderEntry).count() == 0
        assert not [w for w in result['warnings'] if '6_Ladder' in w]
        assert not [w for w in result['warnings'] if 'CNT_04_16' in w]

    def test_a_ladder_tab_with_only_its_header_is_not_announced(self, tmp_path, db):
        path = workbook(tmp_path, 5, {'3d_Weights': weights('CNT_05_01'),
                                      '6_Ladder': [NEW_LADDER_HEADERS]})
        result = ExcelImporter(db).import_cohort_file(path)
        assert result['success'], result['errors']
        assert not [w for w in result['warnings'] if '6_Ladder' in w]

    def test_results_are_imported_only_for_animals_already_known(self, tmp_path, db):
        path = workbook(tmp_path, 4, {
            '3d_Weights': weights('CNT_04_01'),
            '6_Ladder': [OLD_LADDER_HEADERS,
                         ['CNT_04_01', date(2026, 3, 20), 'Uninjured', 'Camera C', 18, 2, 20, 0.1],
                         ['CNT_04_15', date(2026, 3, 20), 'Uninjured', 'Camera C', 17, 3, 20, 0.15]],
        })
        result = ExcelImporter(db).import_cohort_file(path)
        with db.session() as s:
            entries = [(e.subject_id, e.steps_succeeded, e.steps_missed, e.steps_total, e.camera_type)
                       for e in s.query(LadderEntry).all()]
            assert s.query(Subject).filter_by(subject_id='CNT_04_15').first() is None
        assert entries == [('CNT_04_01', 18, 2, 20, 'Camera C')]
        assert warnings_with(result, '6_Ladder', 'no other record', 'CNT_04_15 (2026-03-20)')

    def test_the_generated_layout_reads_its_date_from_column_a(self, tmp_path, db):
        # Its Date is column A (index 0); `find_col(...) or 1` used to turn
        # that into column B and read the animal id as the date.
        path = workbook(tmp_path, 5, {
            '3d_Weights': weights('CNT_05_01'),
            '6_Ladder': [NEW_LADDER_HEADERS,
                         [date(2026, 3, 20), 'CNT_05_01', 'Uninjured', 'Camera C', 20, 2, 0.1, None]],
        })
        ExcelImporter(db).import_cohort_file(path)
        with db.session() as s:
            entry = s.query(LadderEntry).one()
            assert (entry.subject_id, entry.date, entry.camera_type) == (
                'CNT_05_01', date(2026, 3, 20), 'Camera C')


# ---------------------------------------------------------------------------
# 6. An import that records an error saves nothing
# ---------------------------------------------------------------------------

def _record_an_error_part_way(monkeypatch):
    """Make a reader that runs after the weights and surgeries are written
    record an error, the way a future reader that refuses something it cannot
    leave half done would. No reader records an error part way today, so this
    is the only way to exercise the guard."""
    def _stats(self, xl, cohort_id, session, dry_run, source_file=None):
        self.errors.append("7_Stats: a reader refused this workbook")
    monkeypatch.setattr(ExcelImporter, '_import_stats', _stats)


def _surgery_workbook(tmp_path, cohort_num=5):
    animal = 'CNT_%02d_01' % cohort_num
    return workbook(tmp_path, cohort_num, {
        '3d_Weights': weights(animal),
        '4_Contusion_Injury_Details': [CONTUSION_HEADERS,
                                       contusion(animal, date(2026, 4, 1), 22.0)],
    })


class TestNothingSavedOnError:
    def test_an_error_part_way_undoes_the_whole_workbook(self, tmp_path, db, monkeypatch):
        # Database.session() commits whenever its block ends normally; the
        # importer used to only skip its OWN commit on an error, and the
        # weights, animals and surgeries read before the error were saved.
        from mousedb import backfill
        refreshed = []
        monkeypatch.setattr(backfill, 'backfill_phases',
                            lambda db=None, cohort_id=None, **kw: refreshed.append(cohort_id))
        _record_an_error_part_way(monkeypatch)

        result = ExcelImporter(db).import_cohort_file(_surgery_workbook(tmp_path))

        with db.session() as s:
            assert s.query(Cohort).filter_by(cohort_id='CNT_05').first() is None
            assert s.query(Subject).count() == 0
            assert s.query(Weight).count() == 0
            assert s.query(Surgery).count() == 0
        assert not result['success']
        assert result['errors'][0] == "7_Stats: a reader refused this workbook"
        assert 'Nothing from Connectome_05_Animal_Tracking.xlsx was saved' in result['errors'][-1]
        # The ledger records 'imported' as rows written: none were.
        assert set(result['imported'].values()) == {0}
        assert result['not_saved']['surgeries'] == 1 and result['not_saved']['weights'] == 1
        # Nothing was committed, so no phase refresh ran for a cohort that is not there.
        assert refreshed == []

    def test_the_rollback_does_not_depend_on_the_callers_session(self, tmp_path, db, monkeypatch):
        # The GUI Replace hands the importer a database whose session() neither
        # commits nor rolls back, and decides itself afterwards. The importer
        # must roll back explicitly, or the caller's commit would save half a
        # workbook.
        from contextlib import contextmanager
        _record_an_error_part_way(monkeypatch)
        shared = db.SessionLocal()

        class _CallerOwnsTheTransaction:
            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                return getattr(self._real, name)

            @contextmanager
            def session(self):
                yield shared

        try:
            result = ExcelImporter(_CallerOwnsTheTransaction(db)).import_cohort_file(
                _surgery_workbook(tmp_path))
            shared.commit()          # whatever the importer left behind
        finally:
            shared.close()
        assert not result['success']
        with db.session() as s:
            assert s.query(Cohort).filter_by(cohort_id='CNT_05').first() is None
            assert s.query(Surgery).count() == 0

    def test_an_undone_placeholder_cohort_is_reported_as_not_made(self, tmp_path, db, monkeypatch):
        # A workbook with surgeries only would make a placeholder cohort; when
        # the import is undone the warning must not say it "was created".
        _record_an_error_part_way(monkeypatch)
        path = workbook(tmp_path, 7, {'4_Contusion_Injury_Details': [
            CONTUSION_HEADERS, contusion('CNT_07_01', date(2026, 3, 10), 22.0)]})
        result = ExcelImporter(db).import_cohort_file(path)
        placeholder = warnings_with(result, 'CNT_07', 'PLACEHOLDER')
        assert len(placeholder) == 1
        assert 'would be created' in placeholder[0] and 'was created' not in placeholder[0]
        with db.session() as s:
            assert s.query(Cohort).filter_by(cohort_id='CNT_07').first() is None

    def test_a_clean_import_is_still_saved(self, tmp_path, db):
        result = ExcelImporter(db).import_cohort_file(_surgery_workbook(tmp_path))
        assert result['success'], result['errors']
        assert 'not_saved' not in result
        with db.session() as s:
            assert s.query(Surgery).count() == 1 and s.query(Weight).count() == 1
