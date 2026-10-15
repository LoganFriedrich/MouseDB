"""Whether the overnight jobs succeeded is reported, not just how old their output is.

WHY THIS TEST EXISTS
--------------------
The snapshot refresh failed five nights running and nothing said so. The
status screen judged the snapshot's AGE, and a person running the refresh by
hand reset that clock -- so the artifact looked fresh while the job that makes
it was broken. Age cannot tell you a job works, because anyone can make the
artifact by hand.

So the status also reads the nightly health check's report and says, in one
sentence, which jobs are failing and for how many runs. These tests pin that
sentence down, including the three ways the first version got it wrong:
naming one job once per bad night, naming the same job twice under its log
name and its task name, and reporting the health check itself as failing.
"""
import json

import pytest

from mousedb import data_status


def _report(detail, jobs=None, failing=True, checked_at=None):
    import datetime as dt
    return {
        "checked_at": checked_at or dt.datetime.now().isoformat(timespec="seconds"),
        "checked_age": "under an hour",
        "failing": failing,
        "detail": detail,
        "jobs": jobs or [],
    }


LOCKED = ("[FAIL] The brain import did not run: another program kept the "
          "database locked")


def test_a_job_that_failed_three_nights_is_named_once_with_its_count():
    report = _report([
        "[FAIL] scheduled tasks with failing recent runs -- find out what each would do",
        "  import_brains.log | === 2026-10-12 22:30 === | " + LOCKED,
        "  import_brains.log | === 2026-10-13 22:30 === | " + LOCKED,
        "  import_brains.log | === 2026-10-14 22:30 === | " + LOCKED,
    ])
    sentence = data_status._failing_jobs_sentence(report)
    assert "import brains (3 runs)" in sentence
    assert sentence.count("import brains") == 1


def test_a_job_seen_in_its_log_and_its_exit_code_is_named_once():
    report = _report([
        "  snapshot_refresh.log | === 2026-10-15 00:00 === | [FAIL] locked",
        "  (task) MouseDB analysis snapshot refresh | 10/15/2026 | last result 1",
    ])
    sentence = data_status._failing_jobs_sentence(report)
    assert "snapshot refresh (1 run)" in sentence
    assert "MouseDB analysis snapshot refresh" not in sentence


def test_a_task_with_no_log_lines_is_still_named():
    report = _report(["  (task) MouseDB bench disagreement scan | x | last result 2"])
    assert "bench disagreement scan" in data_status._failing_jobs_sentence(report)


def test_the_health_check_never_reports_itself():
    """The observer reporting itself as failing would never stop doing so."""
    report = _report([
        "  health_check.log | === 2026-10-15 09:10 === | [FAIL] scheduled tasks",
        "  (task) MouseDB nightly health check | x | last result 1",
        "  import_brains.log | === 2026-10-14 22:30 === | " + LOCKED,
    ])
    sentence = data_status._failing_jobs_sentence(report)
    assert "health check" not in sentence
    assert "import brains" in sentence


def test_a_job_killed_at_its_time_limit_is_reported(tmp_path, monkeypatch):
    """Result 267014 is whitelisted by the pre-deploy check as 'stopped on
    purpose'; that whitelist is how a nightly kill stayed invisible for weeks."""
    path = tmp_path / "health.json"
    path.write_text(json.dumps(_report([], failing=False, jobs=[
        {"task": "MouseDB import reaches", "last_result": 267014},
        {"task": "MouseDB nightly health check", "last_result": 267014},
        {"task": "MouseDB import sheets", "last_result": 0},
    ])), encoding="utf-8")
    monkeypatch.setattr(data_status, "health_file", lambda: path)
    problems = []
    data_status.scheduled_job_health(problems)
    assert len(problems) == 1
    assert "MouseDB import reaches" in problems[0]
    assert "health check" not in problems[0]


def test_no_report_at_all_is_itself_a_problem(tmp_path, monkeypatch):
    """Silence must never read as 'everything is fine'."""
    monkeypatch.setattr(data_status, "health_file", lambda: tmp_path / "absent.json")
    problems = []
    assert data_status.scheduled_job_health(problems) is None
    assert problems and "nothing has checked" in problems[0]


def test_a_report_nobody_has_refreshed_for_days_is_called_out(tmp_path, monkeypatch):
    path = tmp_path / "health.json"
    path.write_text(json.dumps(_report([], failing=False,
                                       checked_at="2026-01-01T06:00:00")),
                    encoding="utf-8")
    monkeypatch.setattr(data_status, "health_file", lambda: path)
    problems = []
    data_status.scheduled_job_health(problems)
    assert any("nothing is currently watching" in p for p in problems)


def test_powershell_writing_one_job_as_a_bare_object_is_handled(tmp_path, monkeypatch):
    """ConvertTo-Json writes a one-element list as an object, not a list."""
    path = tmp_path / "health.json"
    path.write_text(json.dumps(_report([], failing=False, jobs={
        "task": "MouseDB import reaches", "last_result": 267014})), encoding="utf-8")
    monkeypatch.setattr(data_status, "health_file", lambda: path)
    problems = []
    data_status.scheduled_job_health(problems)
    assert any("MouseDB import reaches" in p for p in problems)
