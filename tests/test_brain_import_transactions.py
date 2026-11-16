"""A brain import that reports an error writes nothing.

WHY: the brain imports ran inside Database.session(), which commits whenever
its block ends normally, and they returned out of that block on an error. A
per-brain CSV whose layout could not be read was refused only AFTER the
placeholder animal and the brain row had been added -- so an import that
printed [FAIL] still saved an animal and a brain with no counts.
"""
from datetime import date

import pytest

from mousedb import database as dbmod
from mousedb.importers import BrainGlobeImporter
from mousedb.schema import BrainSample, Cohort, Project, RegionCount, Subject

BRAIN = '901_PRJ_01_07_1p625x_z4'


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A scratch database with one cohort and no animals."""
    monkeypatch.setattr(dbmod, "DEFAULT_LOG_PATH", None)
    d = dbmod.Database(tmp_path / "scratch.db")
    d.init_db()
    with d.session() as s:
        if not s.query(Project).filter_by(project_code='PRJ').first():
            s.add(Project(project_code='PRJ', project_name='Project A'))
        s.add(Cohort(cohort_id='PRJ_01', project_code='PRJ', start_date=date(2026, 1, 5)))
    return d


def _rows(db):
    with db.session() as s:
        return (s.query(Subject).filter_by(subject_id='PRJ_01_07').count(),
                s.query(BrainSample).count(), s.query(RegionCount).count())


def test_an_unreadable_counts_file_leaves_no_animal_and_no_brain_behind(db, tmp_path):
    csv_path = tmp_path / (BRAIN + '_counts.csv')
    # No cell-count column: the layout cannot be read.
    csv_path.write_text('region_id,region_name\n1,Region one\n')
    result = BrainGlobeImporter(db).import_region_counts(csv_path, brain_id=BRAIN,
                                                         is_final=True)
    assert not result['success']
    assert _rows(db) == (0, 0, 0)


def test_a_readable_counts_file_still_lands_with_its_placeholder_animal(db, tmp_path):
    csv_path = tmp_path / (BRAIN + '_counts.csv')
    csv_path.write_text('region_id,region_name,cell_count\n1,Region one,12\n')
    result = BrainGlobeImporter(db).import_region_counts(csv_path, brain_id=BRAIN,
                                                         is_final=True)
    assert result['success'], result['errors']
    assert _rows(db) == (1, 1, 1)


def test_a_dry_run_writes_nothing(db, tmp_path):
    csv_path = tmp_path / (BRAIN + '_counts.csv')
    csv_path.write_text('region_id,region_name,cell_count\n1,Region one,12\n')
    result = BrainGlobeImporter(db).import_region_counts(csv_path, brain_id=BRAIN,
                                                         is_final=True, dry_run=True)
    assert result['success'], result['errors']
    assert result['imported']['region_counts'] == 1
    assert _rows(db) == (0, 0, 0)
