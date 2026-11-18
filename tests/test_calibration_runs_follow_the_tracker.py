"""Calibration runs in the database follow the tracker's calibration_runs.csv.

WHY: a run already in the database was skipped by every later import, so a
run first imported while it was still 'started', or before it was marked best,
kept that first state for good. And the importer read column names the
tracker does not write (ball_xy, threshold, is_best, detection_time_s ...)
instead of the tracker's own (det_ball_xy, det_threshold, marked_best,
duration_seconds ...), so every run arrived with no detection settings and was
never best.
"""
from datetime import date, datetime

import pandas as pd
import pytest

from mousedb import database as dbmod
from mousedb.importers import BrainGlobeImporter
from mousedb.schema import CalibrationRun, Cohort, Project

BRAIN = '901_PRJ_01_07_1p625x_z4'


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DEFAULT_LOG_PATH", None)
    d = dbmod.Database(tmp_path / "scratch.db")
    d.init_db()
    with d.session() as s:
        if not s.query(Project).filter_by(project_code='PRJ').first():
            s.add(Project(project_code='PRJ', project_name='Project A'))
        s.add(Cohort(cohort_id='PRJ_01', project_code='PRJ', start_date=date(2026, 1, 5)))
    return d


def tracker(tmp_path, rows):
    """A calibration_runs.csv with the tracker's own column names."""
    path = tmp_path / 'calibration_runs.csv'
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def run(**kw):
    row = {'exp_id': 'run_a', 'exp_type': 'detection', 'brain': 'mouse/' + BRAIN,
           'created_at': '2026-02-03T10:20:30.123456', 'status': 'started',
           'duration_seconds': None, 'det_ball_xy': 6, 'det_ball_z': 15,
           'det_soma_diameter': 16, 'det_threshold': 10, 'det_cells_found': None,
           'marked_best': None}
    row.update(kw)
    return row


def stored(db, run_id='run_a'):
    with db.session() as s:
        r = s.query(CalibrationRun).filter_by(run_id=run_id).one()
        return dict(status=r.status, cells=r.cells_detected, time=r.detection_time_s,
                    best=r.is_best, ball_xy=r.ball_xy_um, threshold=r.threshold,
                    when=r.run_date)


def test_the_tracker_column_names_are_read(db, tmp_path):
    result = BrainGlobeImporter(db).import_calibration_runs(tracker(tmp_path, [run()]))
    assert result['success'], result['errors']
    got = stored(db)
    assert (got['ball_xy'], got['threshold']) == (6, 10)
    assert got['when'] == datetime(2026, 2, 3, 10, 20, 30, 123456)


def test_a_run_finished_and_marked_best_after_its_first_import_is_updated(db, tmp_path):
    BrainGlobeImporter(db).import_calibration_runs(tracker(tmp_path, [run()]))
    assert stored(db)['status'] == 'started'

    path = tracker(tmp_path, [run(status='completed', det_cells_found=1234,
                                  duration_seconds=12.5, marked_best='True')])
    imp = BrainGlobeImporter(db)
    result = imp.import_calibration_runs(path)
    assert result['success'], result['errors']
    assert result['imported']['calibration_runs_updated'] == 1
    got = stored(db)
    assert (got['status'], got['cells'], got['time'], got['best']) == ('completed', 1234, 12.5, 1)

    # Read again unchanged: nothing to update.
    assert BrainGlobeImporter(db).import_calibration_runs(path)['imported'][
        'calibration_runs_updated'] == 0


def test_a_dry_run_updates_nothing(db, tmp_path):
    BrainGlobeImporter(db).import_calibration_runs(tracker(tmp_path, [run()]))
    path = tracker(tmp_path, [run(status='completed')])
    result = BrainGlobeImporter(db).import_calibration_runs(path, dry_run=True)
    assert result['imported']['calibration_runs_updated'] == 1
    assert stored(db)['status'] == 'started'


def test_a_run_without_a_tracker_id_is_never_rewritten(db, tmp_path):
    no_id = run(exp_id=None)
    BrainGlobeImporter(db).import_calibration_runs(tracker(tmp_path, [no_id]))
    with db.session() as s:
        (made_up,) = [r.run_id for r in s.query(CalibrationRun)]
    BrainGlobeImporter(db).import_calibration_runs(
        tracker(tmp_path, [dict(no_id, status='completed')]))
    with db.session() as s:
        statuses = {r.run_id: r.status for r in s.query(CalibrationRun)}
    assert statuses[made_up] == 'started'


# --- a file missing columns never overwrites what is stored --------------------

def best_finished_run(db, tmp_path):
    BrainGlobeImporter(db).import_calibration_runs(tracker(tmp_path, [run(
        status='completed', det_cells_found=1234, duration_seconds=12.5, marked_best='True')]))
    got = stored(db)
    assert (got['status'], got['best'], got['ball_xy']) == ('completed', 1, 6)
    return got


@pytest.mark.parametrize('dropped', [('marked_best',), ('status',), ('status', 'marked_best')])
def test_a_file_without_the_trackers_status_or_best_column_is_refused(db, tmp_path, dropped):
    """A copy written by an older tracker (which drops columns it does not
    know) or edited by hand: every stored best would become 'not best' and
    every status 'unknown'. Refused whole instead, stored runs untouched."""
    before = best_finished_run(db, tmp_path)
    row = {k: v for k, v in run(status='completed', det_cells_found=999,
                                marked_best='True').items() if k not in dropped}
    result = BrainGlobeImporter(db).import_calibration_runs(tracker(tmp_path, [row]))
    assert not result['success']
    assert all(name in ' '.join(result['errors']) for name in dropped), result['errors']
    assert stored(db) == before


def test_a_column_the_file_lacks_leaves_the_stored_value(db, tmp_path):
    """Only fields whose column is in the file are updated."""
    best_finished_run(db, tmp_path)
    row = {k: v for k, v in run(status='completed', det_cells_found=1300, duration_seconds=12.5,
                                marked_best='True').items()
           if k not in ('det_ball_xy', 'created_at')}
    result = BrainGlobeImporter(db).import_calibration_runs(tracker(tmp_path, [row]))
    assert result['success'], result['errors']
    got = stored(db)
    assert got['cells'] == 1300                       # a column that is there: updated
    assert got['ball_xy'] == 6                        # a column that is not: kept
    assert got['when'] == datetime(2026, 2, 3, 10, 20, 30, 123456)


def test_a_cleared_best_cell_still_unmarks_the_run(db, tmp_path):
    """The tracker clears marked_best with an empty cell when another run of
    the brain becomes best: that is a real change, not a missing value."""
    best_finished_run(db, tmp_path)
    BrainGlobeImporter(db).import_calibration_runs(tracker(tmp_path, [run(
        status='completed', det_cells_found=1234, duration_seconds=12.5, marked_best='')]))
    assert stored(db)['best'] == 0
