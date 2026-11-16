"""A brain's counts in the database follow its counts file.

WHY: the nightly brain import (``import-brains --all``) only ADDED rows a
brain did not have yet. A brain recounted after its registration was
corrected kept its FIRST counts in the database, marked final, for good, and
nothing said so. These tests run the command's own body on a scratch
database: a recount replaces the stored counts, an unchanged file leaves
them alone, and a file that may be half written, or whose eLife groups
cannot be read, never leaves old and new numbers mixed.
"""
import argparse
import os
import sys
import time
from datetime import date

import pytest

from mousedb import database as dbmod
from mousedb.schema import (BrainSample, CalibrationRun, Cohort, ElifeRegionCount, Project,
                            RegionCount)

BRAIN = '901_PRJ_01_07_1p625x_z4'
OLD = time.time() - 3600          # a file written an hour ago is finished


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A scratch database with one cohort, a 2_Data_Summary folder, and the
    command's body pointed at that database. mousebrain is hidden, so the
    eLife groups come from elife_counts.csv (the path taken wherever mousebrain
    is not installed alongside mousedb)."""
    monkeypatch.setitem(sys.modules, "mousebrain", None)
    monkeypatch.setitem(sys.modules, "mousebrain.region_mapping", None)
    monkeypatch.setattr(dbmod, "DEFAULT_LOG_PATH", None)
    db = dbmod.Database(tmp_path / "scratch.db")
    db.init_db()
    with db.session() as s:
        if not s.query(Project).filter_by(project_code='PRJ').first():
            s.add(Project(project_code='PRJ', project_name='Project A'))
        s.add(Cohort(cohort_id='PRJ_01', project_code='PRJ', start_date=date(2026, 1, 5)))
    monkeypatch.setattr(dbmod, "init_database", lambda *a, **k: db)
    summary = tmp_path / "2_Data_Summary"
    summary.mkdir()
    return {"db": db, "summary": summary}


def write_counts(summary, total, left, right, age=OLD):
    path = summary / (BRAIN + '_counts.csv')
    path.write_text('region_id,region_acronym,region_name,cell_count,left_count,right_count\n'
                    '1,RN,Red Nucleus,%d,%d,%d\n2,XX,Region two,5,5,0\n'
                    % (total, left, right))
    os.utime(path, (age, age))
    return path


def write_elife(summary, both, left, right, brain=BRAIN, age=OLD):
    path = summary / 'elife_counts.csv'
    path.write_text('brain,brain_id,group_Red_Nucleus,group_left_Red_Nucleus,'
                    'group_right_Red_Nucleus\n'
                    'mouse/%s,%s,%d,%d,%d\n' % (brain, brain.split('_')[0], both, left, right))
    os.utime(path, (age, age))
    return path


def run(env, capsys, **kw):
    from mousedb import cli
    args = dict(summary_dir=str(env["summary"]), all=True, csv=None, calibration=None,
                brain=None, update=False, dry_run=False)
    args.update(kw)
    rc = cli._import_brains_body(argparse.Namespace(**args), env["summary"])
    return rc, capsys.readouterr().out


def stored(env):
    with env["db"].session() as s:
        rc = {(r.region_id, r.hemisphere): r.cell_count for r in s.query(RegionCount)}
        ec = {(r.group_name, r.hemisphere): r.cell_count for r in s.query(ElifeRegionCount)}
        ids = sorted(r.id for r in s.query(RegionCount))
    return rc, ec, ids


def test_a_recount_replaces_the_stored_counts(env, capsys):
    write_counts(env["summary"], 100, 60, 40)
    write_elife(env["summary"], 100, 60, 40)
    rc, out = run(env, capsys)
    assert rc == 0, out
    assert stored(env)[0][(1, 'both')] == 100

    write_counts(env["summary"], 130, 70, 60)
    write_elife(env["summary"], 130, 70, 60)
    rc, out = run(env, capsys)
    assert rc == 0, out
    regions, groups, _ = stored(env)
    assert regions == {(1, 'both'): 130, (1, 'left'): 70, (1, 'right'): 60,
                       (2, 'both'): 5, (2, 'left'): 5}
    assert groups == {('Red Nucleus', 'both'): 130, ('Red Nucleus', 'left'): 70,
                      ('Red Nucleus', 'right'): 60}
    assert 'REPLACED' in out


def test_an_unchanged_file_leaves_the_stored_rows_alone(env, capsys):
    write_counts(env["summary"], 100, 60, 40)
    write_elife(env["summary"], 100, 60, 40)
    assert run(env, capsys)[0] == 0
    before = stored(env)
    rc, out = run(env, capsys)
    assert rc == 0, out
    assert stored(env) == before          # same rows, same row ids
    assert 'REPLACED' not in out and 'unchanged' in out


def test_update_replaces_even_unchanged_counts_in_one_go(env, capsys):
    write_counts(env["summary"], 100, 60, 40)
    write_elife(env["summary"], 100, 60, 40)
    assert run(env, capsys)[0] == 0
    regions, groups, _ = stored(env)
    rc, out = run(env, capsys, update=True)
    assert rc == 0, out
    assert 'region counts REPLACED (--update) (5 rows)' in out
    assert 'eLife groups REPLACED (--update) (3 rows)' in out
    assert stored(env)[:2] == (regions, groups)


def test_a_file_being_written_is_left_for_the_next_run(env, capsys):
    write_counts(env["summary"], 100, 60, 40)
    write_elife(env["summary"], 100, 60, 40)
    assert run(env, capsys)[0] == 0
    write_counts(env["summary"], 7, 7, 0, age=time.time())    # just written
    write_elife(env["summary"], 7, 7, 0)
    rc, out = run(env, capsys)
    assert rc == 1
    assert out.strip().splitlines()[-1].startswith('[FAIL]') and BRAIN in out
    assert stored(env)[0][(1, 'both')] == 100                 # the stored counts are kept


def test_a_file_without_region_rows_never_wipes_a_brain(env, capsys):
    write_counts(env["summary"], 100, 60, 40)
    write_elife(env["summary"], 100, 60, 40)
    assert run(env, capsys)[0] == 0
    path = env["summary"] / (BRAIN + '_counts.csv')
    path.write_text('region_id,region_acronym,region_name,cell_count,left_count,right_count\n')
    os.utime(path, (OLD, OLD))
    rc, out = run(env, capsys)
    assert rc == 1 and BRAIN in out.strip().splitlines()[-1]
    assert stored(env)[0][(1, 'both')] == 100


def test_old_groups_never_sit_beside_new_counts(env, capsys):
    write_counts(env["summary"], 100, 60, 40)
    write_elife(env["summary"], 100, 60, 40)
    assert run(env, capsys)[0] == 0
    write_counts(env["summary"], 130, 70, 60)
    write_elife(env["summary"], 1, 1, 0, brain='902_PRJ_01_08_1p625x_z4')   # no row for BRAIN
    rc, out = run(env, capsys)
    regions, groups, _ = stored(env)
    assert regions[(1, 'both')] == 130          # the recount landed
    assert groups == {}                          # the groups of the OLD count are gone
    assert rc == 1 and 'eLife' in out.strip().splitlines()[-1]


def test_a_dry_run_reports_the_replacement_and_writes_nothing(env, capsys):
    write_counts(env["summary"], 100, 60, 40)
    write_elife(env["summary"], 100, 60, 40)
    assert run(env, capsys)[0] == 0
    before = stored(env)
    write_counts(env["summary"], 130, 70, 60)
    write_elife(env["summary"], 130, 70, 60)
    rc, out = run(env, capsys, dry_run=True)
    assert rc == 0, out
    assert 'REPLACED' in out
    assert stored(env) == before


def test_a_refused_brain_keeps_nothing_new(env, capsys):
    """A brain of a cohort the database does not know is refused whole."""
    other = '903_PRJ_09_01_1p625x_z4'
    path = env["summary"] / (other + '_counts.csv')
    path.write_text('region_id,region_acronym,region_name,cell_count\n1,RN,Red Nucleus,3\n')
    os.utime(path, (OLD, OLD))
    rc, out = run(env, capsys)
    assert rc == 1 and other in out.strip().splitlines()[-1]
    with env["db"].session() as s:
        assert s.query(BrainSample).count() == 0 and s.query(CalibrationRun).count() == 0


def test_groups_with_the_same_counts_from_the_other_source_are_left_alone(env, capsys):
    """Groups computed with MouseBrain's mapping carry their constituent
    regions; groups read from elife_counts.csv carry none. Same counts from
    the other source must not rewrite the groups (and drop the constituents)."""
    write_counts(env["summary"], 100, 60, 40)
    write_elife(env["summary"], 100, 60, 40)
    assert run(env, capsys)[0] == 0
    with env["db"].session() as s:
        for row in s.query(ElifeRegionCount):
            row.constituent_regions = '["RN"]'
    rc, out = run(env, capsys)
    assert rc == 0 and 'eLife groups unchanged' in out
    with env["db"].session() as s:
        assert {r.constituent_regions for r in s.query(ElifeRegionCount)} == {'["RN"]'}


def _cut_off(summary, text):
    path = summary / (BRAIN + '_counts.csv')
    path.write_text(text)
    os.utime(path, (OLD, OLD))


HEADER = 'region_id,region_acronym,region_name,cell_count,left_count,right_count\n'


@pytest.mark.parametrize('text', [
    # the last row lost its right_count (cut off between two fields)
    HEADER + '1,RN,Red Nucleus,130,70,60\n2,XX,Region two,9,9\n',
    # a row that names a region but has no count at all
    HEADER + '1,RN,Red Nucleus,130,70,60\n2,XX,Region two,,,\n',
    # cut off in the middle of the last number: no line break at the end
    HEADER + '1,RN,Red Nucleus,130,70,60\n2,XX,Region two,5,5,1',
], ids=['missing-field', 'no-count', 'no-line-break'])
def test_a_cut_off_file_never_replaces_the_stored_counts(env, capsys, text):
    """A counts file cut off part-way is refused: the stored counts stay,
    the run fails naming the brain. (Replacing would store only the rows that
    happened to be complete and drop the rest of the brain's counts.)"""
    write_counts(env["summary"], 100, 60, 40)
    write_elife(env["summary"], 100, 60, 40)
    assert run(env, capsys)[0] == 0
    before = stored(env)
    _cut_off(env["summary"], text)
    rc, out = run(env, capsys)
    last = out.strip().splitlines()[-1]
    assert rc == 1 and last.startswith('[FAIL]') and BRAIN in last, out
    assert 'cut off' in out
    assert stored(env) == before                 # every stored row kept, same ids


def test_groups_from_an_elife_file_older_than_the_counts_file_are_not_stored(env, capsys):
    """MouseBrain writes the counts file first and elife_counts.csv after. An
    elife_counts.csv older than the counts file holds the groups of an
    EARLIER count (step 6 stopped between the two): those groups must not be
    stored beside the new region counts."""
    write_counts(env["summary"], 100, 60, 40)
    write_elife(env["summary"], 100, 60, 40)
    assert run(env, capsys)[0] == 0
    write_counts(env["summary"], 130, 70, 60)                 # the recount ...
    write_elife(env["summary"], 100, 60, 40, age=OLD - 600)   # ... its groups never written
    rc, out = run(env, capsys)
    regions, groups, _ = stored(env)
    assert regions[(1, 'both')] == 130
    assert groups == {}                                       # the old groups are gone
    last = out.strip().splitlines()[-1]
    assert rc == 1 and last.startswith('[FAIL]') and 'eLife' in last and BRAIN in last
    assert 'earlier count' in out
    # The next run does not bring the old row back either.
    rc, out = run(env, capsys)
    assert rc == 1 and stored(env)[1] == {}


def test_a_new_brain_does_not_get_groups_from_an_older_elife_file(env, capsys):
    write_counts(env["summary"], 130, 70, 60)
    write_elife(env["summary"], 100, 60, 40, age=OLD - 600)
    rc, out = run(env, capsys)
    regions, groups, _ = stored(env)
    assert regions[(1, 'both')] == 130 and groups == {}
    assert rc == 1 and 'eLife' in out.strip().splitlines()[-1]


def test_an_elife_failure_names_the_brain_not_a_row_number(env, capsys):
    write_counts(env["summary"], 100, 60, 40)
    rc, out = run(env, capsys)                                # no elife_counts.csv at all
    assert rc == 1
    assert 'brain_sample_id=' not in out
    assert 'eLife counts NOT imported for brain %s' % BRAIN in out


# --- elife_counts.csv cut off -------------------------------------------------

ELIFE_HEADER = 'brain,brain_id,group_Red_Nucleus,group_left_Red_Nucleus,group_right_Red_Nucleus\n'


@pytest.mark.parametrize('text', [
    # cut off in the middle of the brain's last number: no line break at the end
    ELIFE_HEADER + 'mouse/%s,901,7' % BRAIN,
    # the brain's row lost its last fields, and the file still ends with a line break
    ELIFE_HEADER + 'mouse/%s,901,7\n' % BRAIN,
], ids=['no-line-break', 'short-row'])
def test_a_cut_off_elife_file_never_replaces_the_stored_groups(env, capsys, text):
    """MouseBrain rewrites elife_counts.csv in place. A cut-off copy must not
    replace the brain's stored groups with the cells that happened to be
    written: the groups count as failed, the stored ones stay (the region
    counts did not change), and the run ends with [FAIL]."""
    write_counts(env["summary"], 100, 60, 40)
    write_elife(env["summary"], 100, 60, 40)
    assert run(env, capsys)[0] == 0
    before = stored(env)
    path = env["summary"] / 'elife_counts.csv'
    path.write_text(text)
    os.utime(path, (OLD, OLD))
    rc, out = run(env, capsys)
    last = out.strip().splitlines()[-1]
    assert rc == 1 and last.startswith('[FAIL]') and 'eLife' in last and BRAIN in last, out
    assert 'cut off' in out
    assert stored(env) == before


# --- a counts file cut off at a row boundary ----------------------------------

def write_region_summary(summary, total, regions, brain=BRAIN, age=OLD):
    """MouseBrain's wide region_counts.csv: one row per brain, written by
    step 6 AFTER the per-brain counts file, from the same count."""
    cols = ['region_%s' % r for r in regions]
    path = summary / 'region_counts.csv'
    path.write_text('brain,run_date,brain_id,total_cells,%s\nmouse/%s,x,%s,%d,%s\n'
                    % (','.join(cols), brain, brain.split('_')[0], total,
                       ','.join('1' for _ in cols)))
    os.utime(path, (age, age))
    return path


def test_a_file_cut_off_at_a_row_boundary_is_refused(env, capsys):
    """Every remaining row is complete and the file ends with a line break,
    but region_counts.csv, written after it, says the brain has more regions
    and more cells: the file lost rows, and the stored counts stay."""
    write_counts(env["summary"], 100, 60, 40)
    write_elife(env["summary"], 100, 60, 40)
    assert run(env, capsys)[0] == 0
    before = stored(env)
    _cut_off(env["summary"], HEADER + '1,RN,Red Nucleus,130,70,60\n')      # XX is gone
    write_region_summary(env["summary"], 135, ['RN', 'XX'], age=OLD + 5)
    rc, out = run(env, capsys)
    last = out.strip().splitlines()[-1]
    assert rc == 1 and last.startswith('[FAIL]') and BRAIN in last, out
    assert 'cut off' in out and 'XX' in out
    assert stored(env) == before


def test_a_recount_that_agrees_with_region_counts_is_replaced(env, capsys):
    write_counts(env["summary"], 100, 60, 40)
    write_elife(env["summary"], 100, 60, 40)
    assert run(env, capsys)[0] == 0
    write_counts(env["summary"], 130, 70, 60)
    write_region_summary(env["summary"], 135, ['RN', 'XX'], age=OLD + 5)
    write_elife(env["summary"], 130, 70, 60, age=OLD + 6)
    rc, out = run(env, capsys)
    assert rc == 0, out
    assert stored(env)[0][(1, 'both')] == 130


def test_an_older_region_counts_file_proves_nothing(env, capsys):
    """region_counts.csv older than the counts file holds an EARLIER count
    (step 6 stopped between the two writes): it is not compared."""
    write_counts(env["summary"], 100, 60, 40)
    write_elife(env["summary"], 100, 60, 40)
    assert run(env, capsys)[0] == 0
    write_counts(env["summary"], 130, 70, 60)
    write_region_summary(env["summary"], 105, ['RN', 'XX'], age=OLD - 600)
    rc, out = run(env, capsys)
    assert stored(env)[0][(1, 'both')] == 130


# --- warnings are printed -----------------------------------------------------

def test_a_region_left_out_is_printed(env, capsys, monkeypatch):
    """A region whose acronym the atlas does not know is left out of the
    counts; the import says so under the brain's line."""
    from mousedb.importers import BrainGlobeImporter
    monkeypatch.setattr(BrainGlobeImporter, '_acronym_to_id_cache',
                        {'RN': {'id': 1, 'name': 'Red Nucleus'}})
    path = env["summary"] / (BRAIN + '_counts.csv')
    path.write_text('region_acronym,region_name,cell_count,left_count,right_count\n'
                    'RN,Red Nucleus,100,60,40\nZZQ,Nowhere,5,5,0\n')
    os.utime(path, (OLD, OLD))
    write_elife(env["summary"], 100, 60, 40)
    rc, out = run(env, capsys)
    assert '[!] %s: Unknown acronym: ZZQ' % BRAIN in out, out


def test_an_unknown_elife_group_is_printed(env, capsys):
    write_counts(env["summary"], 100, 60, 40)
    path = env["summary"] / 'elife_counts.csv'
    path.write_text('brain,brain_id,group_Red_Nucleus,group_Made_Up_Group\n'
                    'mouse/%s,901,100,3\n' % BRAIN)
    os.utime(path, (OLD, OLD))
    rc, out = run(env, capsys, all=False, csv=str(env["summary"] / (BRAIN + '_counts.csv')))
    assert rc == 0, out
    assert any('[!]' in line and BRAIN in line and 'Made_Up_Group' in line
               for line in out.splitlines()), out


# --- two files naming one brain -----------------------------------------------

def test_two_files_naming_one_brain_import_neither(env, capsys):
    """A stale bare '<brain>_counts.csv' beside '<mouse>_<brain>_counts.csv':
    neither is imported (the one that sorts last must not win every night),
    the stored counts stay, and the [FAIL] line names both files."""
    write_counts(env["summary"], 100, 60, 40)
    write_elife(env["summary"], 100, 60, 40)
    assert run(env, capsys)[0] == 0
    before = stored(env)
    other = env["summary"] / ('PRJ_01_07_' + BRAIN + '_counts.csv')
    other.write_text(HEADER + '1,RN,Red Nucleus,7,7,0\n2,XX,Region two,5,5,0\n')
    os.utime(other, (OLD, OLD))
    rc, out = run(env, capsys)
    last = out.strip().splitlines()[-1]
    assert rc == 1 and last.startswith('[FAIL]') and BRAIN in last, out
    assert other.name in last and (BRAIN + '_counts.csv') in last
    assert stored(env) == before
