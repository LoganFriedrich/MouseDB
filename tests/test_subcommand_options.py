"""Every option of a `mousedb <subcommand>` works when typed after the subcommand.

WHY: `mousedb missing-videos` and `mousedb sheet-progress` handed everything
after their name on as one block (argparse.REMAINDER). argparse refuses such a
block when it starts with an option, so every option failed with
"unrecognized arguments": --help, --cohort, --write, and the
`mousedb missing-videos --acknowledge ...` line that the FIX THESE files and
the Where Is My Data tab tell a person to run. These tests run the command
lines exactly as a person types them -- the acknowledge ones are taken from
the very text the FIX THESE lines print. Every input is synthetic, under
tmp_path.
"""
import csv
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from mousedb import cli, missing_videos as mv
from mousedb.output_folders import hand_scores_dir

VIEW = "2026-03-20T02:00:00"
REC = "20260302_PROJA0101,PROJA0102_P2.mkv"
REC_KEYS = ["20260302_PROJA0101_P2", "20260302_PROJA0102_P2"]


def _hand(root, sessions):
    """sessions: (subject, YYYY-MM-DD, tray, cohort). Two pellet rows each."""
    d = hand_scores_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "hand_pellet_scores.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["video_key", "pellet", "score", "subject_id", "cohort_id", "project",
                    "session_date", "tray"])
        for sid, day, tray, cohort in sessions:
            key = "%s_%s_%s" % (day.replace("-", ""), sid.replace("_", ""), tray)
            for p in (1, 2):
                w.writerow([key, p, 0, sid, cohort, cohort.split("_")[0], day, tray])
    (d / "hand_pellet_scores.json").write_text(json.dumps({"sheets_seen": ["PROJA_01"]}))


def _census(root, failed_recording=False):
    c = {"generated_at": VIEW, "sessions": {}}
    if failed_recording:
        for k in REC_KEYS:
            c["sessions"][k] = {"element": "recording_issue", "finished": False,
                                "recording_issue": {"collage": REC, "reason": "0.08 s"}}
        c["recording_issues"] = [{"collage": REC, "reason": "0.08 s",
                                  "flagged_by": "intake check", "sessions": REC_KEYS,
                                  "flagged_at": "2026-03-02T16:00:00"}]
    d = Path(root) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pipeline_census.json").write_text(json.dumps(c))


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "db"
    r.mkdir()
    monkeypatch.setenv("MOUSEDB_ROOT", str(r))
    monkeypatch.setenv("MOUSEREACH_PIPELINE_ROOT", str(tmp_path / "pipe"))
    return r


def _mousedb(monkeypatch, capsys, command_line):
    """Run `command_line` (starting with "mousedb ") through mousedb's own
    command line, as the console script does. Returns (exit code, output)."""
    argv = shlex.split(command_line)
    assert argv[0] == "mousedb"
    monkeypatch.setattr(sys, "argv", argv)
    try:
        cli.main()
        code = 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    out = capsys.readouterr()
    return code, out.out + out.err


def _printed_command(says):
    """The `mousedb missing-videos ...` command inside a FIX THESE line, with its
    placeholders filled in as a person would."""
    m = re.search(r"mousedb missing-videos .*?--reason <why>", says)
    assert m, says
    return (m.group(0).replace("<your name>", '"A Person"')
            .replace("<why>", '"not on the recording PC or any backup"'))


# ------------------------------------------------------------------ --help

@pytest.mark.parametrize("sub, option", [
    ("missing-videos", "--acknowledge"),
    ("sheet-progress", "--cohort"),
    ("export-subset", "--save"),
    ("recording-issues", "--import"),
])
def test_help_after_the_subcommand_shows_its_options(monkeypatch, capsys, sub, option):
    code, out = _mousedb(monkeypatch, capsys, "mousedb %s --help" % sub)
    assert code == 0, out
    assert "unrecognized arguments" not in out
    assert option in out


def test_the_installed_style_command_line_takes_options(tmp_path):
    """The same through a separate process, as the console script runs it."""
    src = str(Path(cli.__file__).resolve().parents[1])
    env = dict(os.environ, PYTHONPATH=src)
    r = subprocess.run([sys.executable, "-c", "from mousedb.cli import main; main()",
                        "missing-videos", "--help"],
                       capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--acknowledge" in r.stdout


# ------------------------------------------------------------------ the options themselves

def test_missing_videos_cohort_and_grace_days(root, monkeypatch, capsys):
    _hand(root, [("PROJA_01_01", "2026-03-02", "P1", "PROJA_01")])
    _census(root)
    code, out = _mousedb(monkeypatch, capsys,
                         "mousedb missing-videos --cohort PROJA_01 --grace-days 0")
    assert code == 0, out
    assert "grace 0 days" in out
    assert "PROJA_01" in out


def test_missing_videos_write_writes_the_list(root, monkeypatch, capsys):
    _hand(root, [("PROJA_01_01", "2026-03-02", "P1", "PROJA_01")])
    _census(root)
    code, out = _mousedb(monkeypatch, capsys, "mousedb missing-videos --write")
    assert code == 0, out
    assert mv.csv_path(root).is_file()


def test_sheet_progress_cohort(root, monkeypatch, capsys):
    _hand(root, [("PROJA_01_01", "2026-03-02", "P1", "PROJA_01")])
    _census(root)
    code, out = _mousedb(monkeypatch, capsys, "mousedb sheet-progress --cohort PROJA_01")
    assert code == 0, out
    assert "Progress against the tracking sheets" in out


def test_sheet_progress_json(root, monkeypatch, capsys):
    _hand(root, [("PROJA_01_01", "2026-03-02", "P1", "PROJA_01")])
    _census(root)
    code, out = _mousedb(monkeypatch, capsys, "mousedb sheet-progress --json --list")
    assert code == 0, out
    assert json.loads(out)["grace_days"] >= 0


# ------------------------------------------------------------------ the printed FIX THESE commands

def test_the_acknowledge_line_a_missing_recording_prints_works_as_printed(
        root, monkeypatch, capsys):
    _hand(root, [("PROJA_01_01", "2026-03-02", "P1", "PROJA_01"),
                 ("PROJA_01_02", "2026-03-02", "P1", "PROJA_01")])
    _census(root)
    rec = mv.recordings(mv.check()["gaps"])[0]
    command = _printed_command(mv.says(rec))
    assert "--acknowledge PROJA_01:2026-03-02" in command
    code, out = _mousedb(monkeypatch, capsys, command)
    assert code == 0, out
    assert "[OK] 2 session(s) acknowledged" in out
    acks = mv.read_acks(mv.ack_path(root))
    assert sorted(acks) == ["20260302_PROJA0101_P1", "20260302_PROJA0102_P1"]
    assert acks["20260302_PROJA0101_P1"]["by"] == "A Person"
    assert {g["status"] for g in mv.check()["gaps"]} == {"acknowledged"}


def test_the_acknowledge_line_a_failed_recording_prints_works_as_printed(
        root, monkeypatch, capsys):
    """The failed-recording line names the recording, commas and all, in quotes."""
    _hand(root, [("PROJA_01_01", "2026-03-02", "P2", "PROJA_01")])
    _census(root, failed_recording=True)
    rec = mv.failed_recordings(mv.check()["gaps"])[0]
    command = _printed_command(mv.says_failed(rec))
    assert REC in command
    code, out = _mousedb(monkeypatch, capsys, command)
    assert code == 0, out
    assert sorted(mv.read_acks(mv.ack_path(root))) == REC_KEYS


def test_an_acknowledge_naming_nothing_writes_nothing(root, monkeypatch, capsys):
    _hand(root, [("PROJA_01_01", "2026-03-02", "P1", "PROJA_01")])
    _census(root)
    code, out = _mousedb(monkeypatch, capsys,
                         'mousedb missing-videos --acknowledge PROJA_09:2020-01-01 '
                         '--by "A Person" --reason "test"')
    assert code == 1
    assert "names no session" in out
    assert not mv.ack_path(root).exists()
