"""Brains whose final counts are in the database but whose counts file has left
MouseBrain's summary folder are REPORTED, never removed.

WHY: the brain import reads only counts files that exist. A brain renamed in
MouseBrain (its cohort or subject corrected in the folder name), or whose
counts were withdrawn, kept its counts in the database marked final under the
old id -- a renamed brain was there twice -- and nothing said so. Removing them
automatically would turn a listing that missed a file into lost counts, so the
import reports them (its output, a list file, the MouseReach Video Processing Progress tab) and
removes nothing, and its exit code does not change.
"""
import json
import os

from mousedb import data_status
from mousedb.schema import RegionCount

from tests.test_brain_counts_follow_recounts import BRAIN, OLD, env, run, stored  # noqa: F401

RENAMED = '901_PRJ_01_08_1p625x_z4'       # the same brain, subject corrected


def write_counts_for(summary, brain, total):
    path = summary / (brain + '_counts.csv')
    path.write_text('region_id,region_acronym,region_name,cell_count,left_count,right_count\n'
                    '1,RN,Red Nucleus,%d,%d,0\n' % (total, total))
    os.utime(path, (OLD, OLD))
    return path


def write_elife_for(summary, *brains):
    path = summary / 'elife_counts.csv'
    path.write_text('brain,brain_id,group_Red_Nucleus\n' + ''.join(
        'mouse/%s,%s,%d\n' % (b, b.split('_')[0], n) for b, n in brains))
    os.utime(path, (OLD, OLD))


def final_rows(env, brain):
    with env["db"].session() as s:
        from mousedb.schema import BrainSample
        return (s.query(RegionCount).join(BrainSample,
                                          RegionCount.brain_sample_id == BrainSample.id)
                .filter(BrainSample.brain_id == brain, RegionCount.is_final == 1).count())


def listed(env):
    """The brains the import's list file names; None when there is no file."""
    p = env["without_file"]
    return json.loads(p.read_text(encoding="utf-8"))["brains"] if p.is_file() else None


def test_a_renamed_brain_is_reported_and_its_old_counts_kept(env, capsys):
    write_counts_for(env["summary"], BRAIN, 100)
    write_elife_for(env["summary"], (BRAIN, 100))
    assert run(env, capsys)[0] == 0

    # MouseBrain renames the brain: the old id's file is gone, a new one is there.
    (env["summary"] / (BRAIN + '_counts.csv')).unlink()
    write_counts_for(env["summary"], RENAMED, 100)
    write_elife_for(env["summary"], (RENAMED, 100))
    rc, out = run(env, capsys)

    assert rc == 0, out                                   # report only: exit code unchanged
    assert final_rows(env, BRAIN) > 0                     # nothing removed
    line = next((l for l in out.splitlines() if '[!]' in l and 'no counts file' in l), None)
    assert line is not None and BRAIN in line and RENAMED not in line, out
    assert out.strip().splitlines()[-1].strip() != line.strip()   # not the last line
    report = json.loads(env["without_file"].read_text(encoding="utf-8"))
    assert report["brains"] == [BRAIN] and report["count"] == 1 and report["checked_at"]


def test_the_list_is_emptied_once_the_file_is_back(env, capsys):
    write_counts_for(env["summary"], BRAIN, 100)
    write_counts_for(env["summary"], RENAMED, 50)
    write_elife_for(env["summary"], (BRAIN, 100), (RENAMED, 50))
    assert run(env, capsys)[0] == 0
    gone = env["summary"] / (RENAMED + '_counts.csv')
    kept = gone.read_text()
    gone.unlink()
    run(env, capsys)
    assert listed(env) == [RENAMED]
    gone.write_text(kept)
    os.utime(gone, (OLD, OLD))
    rc, out = run(env, capsys)
    assert rc == 0, out
    assert listed(env) == []
    assert 'no counts file' not in out


def test_a_dry_run_reports_but_writes_no_list(env, capsys):
    write_counts_for(env["summary"], BRAIN, 100)
    write_counts_for(env["summary"], RENAMED, 50)
    write_elife_for(env["summary"], (BRAIN, 100), (RENAMED, 50))
    assert run(env, capsys)[0] == 0
    env["without_file"].unlink(missing_ok=True)
    (env["summary"] / (RENAMED + '_counts.csv')).unlink()
    rc, out = run(env, capsys, dry_run=True)
    assert any('[!]' in l and RENAMED in l and 'no counts file' in l
               for l in out.splitlines()), out
    assert not env["without_file"].exists()


def test_video_processing_progress_names_them(tmp_path):
    problem = getattr(data_status, "brains_without_file_problem", None)
    assert problem is not None, "MouseReach Video Processing Progress does not report brains without a counts file"
    p = tmp_path / 'brain_counts_without_file.json'
    assert problem(p) is None                             # never checked
    p.write_text(json.dumps({"checked_at": "2026-01-02T22:30:00", "count": 0, "brains": []}),
                 encoding="utf-8")
    assert problem(p) is None                             # checked, none
    p.write_text(json.dumps({"checked_at": "2026-01-02T22:30:00", "count": 1,
                             "brains": [BRAIN]}), encoding="utf-8")
    line = problem(p)
    assert line.startswith("1 brain(s) have final counts in the database but no counts file")
    assert BRAIN in line and "Nothing was removed" in line and str(p) in line
    p.write_text("{", encoding="utf-8")
    assert "could not be read" in problem(p)
