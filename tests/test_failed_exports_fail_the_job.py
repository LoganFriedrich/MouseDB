"""A snapshot run whose exports failed, or came out incomplete, fails the task.

WHY: the nightly snapshot job is what keeps exports/current up to date. When
rewriting the exports failed it printed "[warn] current exports not refreshed"
and exited 0, and an incomplete export (MANIFEST.json complete=False) exited 0
too -- the task looked healthy while people kept reading old files, and the
checks that watch the scheduled jobs look for [FAIL] and a non-zero exit, not
for [warn].
"""
import pytest

from mousedb.exporters import current as current_mod
from mousedb.exporters import refresh_snapshot as rs


@pytest.fixture(autouse=True)
def _no_watcher(monkeypatch):
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: False)


def test_exports_that_could_not_be_written_fail_the_job(monkeypatch, capsys):
    def broken(**_k):
        raise OSError("the exports folder cannot be reached")
    monkeypatch.setattr(current_mod, "refresh_current", broken)
    assert rs._finish({"reach_data": 1}, force=False) == 1
    out = capsys.readouterr().out
    assert "[FAIL] current exports not refreshed" in out
    assert "[warn]" not in out


def test_incomplete_exports_fail_the_job(monkeypatch, capsys):
    monkeypatch.setattr(current_mod, "refresh_current", lambda **_k: {
        "files": {}, "complete": False, "problems": ["reach_data.csv has 1 column(s) "
                                                     "with no data-dictionary entry"]})
    assert rs._finish({"reach_data": 1}, force=False) == 1
    assert "[FAIL] current exports are incomplete" in capsys.readouterr().out


def test_complete_exports_pass_and_their_notes_are_shown(monkeypatch, capsys):
    monkeypatch.setattr(current_mod, "refresh_current", lambda **_k: {
        "files": {"a.csv": {}}, "complete": True, "problems": [],
        "notes": ["a dictionary describes columns only some cohorts have"]})
    assert rs._finish({"reach_data": 1}, force=False) == 0
    out = capsys.readouterr().out
    assert "Done: reach_data=1" in out and "[note] a dictionary" in out


def test_a_part_that_fails_makes_the_export_incomplete(tmp_path, monkeypatch):
    """One writer raising used to leave complete=True: the problem was listed,
    but the flag people and the job judge by said all was well."""
    def raises(snapshot_dir, out_dir, manifest):
        raise ValueError("cannot read the snapshot table")
    monkeypatch.setattr(current_mod, "_reach_data_csv", raises)
    monkeypatch.setattr(current_mod, "_manual_scores_csv", lambda *a: None)
    monkeypatch.setattr(current_mod, "_odc_reaches_csvs", lambda *a: None)
    m = current_mod.refresh_current(tmp_path / "snap", tmp_path / "out", db_ok=False)
    assert m["complete"] is False
    assert any("cannot read the snapshot table" in p for p in m["problems"])


def test_a_cohort_file_that_failed_makes_the_export_incomplete(tmp_path, monkeypatch):
    def one_cohort_failed(snapshot_dir, out_dir, manifest):
        msg = "ODC_reaches_PROJ_01: KeyError: 'subject_id'"
        manifest["problems"].append(msg)
        manifest.setdefault("failures", []).append(msg)
    monkeypatch.setattr(current_mod, "_reach_data_csv", lambda *a: None)
    monkeypatch.setattr(current_mod, "_manual_scores_csv", lambda *a: None)
    monkeypatch.setattr(current_mod, "_odc_reaches_csvs", one_cohort_failed)
    m = current_mod.refresh_current(tmp_path / "snap", tmp_path / "out", db_ok=False)
    assert m["complete"] is False
