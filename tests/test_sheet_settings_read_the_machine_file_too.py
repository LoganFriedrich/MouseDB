"""The sheets settings are read like every other setting: your own file laid
over the machine settings file every login on the computer shares.

WHY: the tracking-sheets folder, the pins, the ASPA folder and the bench-scan
routing command were read from the person's own file only, while every other
setting was read from both. A sheets folder set once for the computer with
``mousedb config --machine --set cohort_sheets_dir`` was therefore invisible
to them: every other login -- and a scheduled job running as one -- had "no
sheets folder", while the Setup tab showed one set. Writes still go to the
person's own file only, so it never gains copies of the machine's values.
"""
import json
import os
import time
from pathlib import Path

import pytest

from mousedb import bench_scan, config
from mousedb import cohort_sheets as cs

SHEET = "Connectome_99_Animal_Tracking.xlsx"
OTHER = "Connectome_99_Animal_Tracking1.xlsx"


@pytest.fixture
def files(tmp_path, monkeypatch):
    """(own settings file, machine settings file), both private to the test."""
    for env in list(config.KEYS.values()) + [cs.ENV_VAR, cs.ASPA_ENV_VAR]:
        monkeypatch.delenv(env, raising=False)
    own = tmp_path / "home" / ".mousedb" / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", own)
    monkeypatch.setattr(cs, "CONFIG_PATH", own)
    machine = tmp_path / "machine" / "config.json"
    machine.parent.mkdir(parents=True)
    monkeypatch.setenv(config.MACHINE_CONFIG_ENV, str(machine))
    return own, machine


@pytest.fixture
def sheets(tmp_path):
    d = tmp_path / "sheets"
    d.mkdir()
    (d / SHEET).write_bytes(b"x")
    later = d / OTHER
    later.write_bytes(b"x")
    t = time.time()
    os.utime(d / SHEET, (t - 3600, t - 3600))   # the older file; OTHER is newest
    return d


def _machine(machine, **values):
    machine.write_text(json.dumps(values), encoding="utf-8")


def test_a_sheets_folder_set_for_the_whole_computer_is_found(files, sheets):
    own, machine = files
    _machine(machine, cohort_sheets_dir=str(sheets))
    assert cs.cohort_sheets_dir() == sheets
    assert cs.available_cohorts() == ["CNT_99"]


def test_your_own_sheets_folder_still_wins(files, sheets, tmp_path):
    own, machine = files
    mine = tmp_path / "mine"
    mine.mkdir()
    (mine / SHEET).write_bytes(b"x")
    _machine(machine, cohort_sheets_dir=str(sheets))
    cs.set_cohort_sheets_dir(mine)
    assert cs.cohort_sheets_dir() == mine


def test_a_pin_set_for_the_whole_computer_is_honoured(files, sheets):
    own, machine = files
    _machine(machine, cohort_sheets_dir=str(sheets), pinned_sheets={"CNT_99": SHEET})
    assert cs.pinned_sheet("CNT_99") == SHEET
    assert cs.find_cohort_sheet("CNT_99") == sheets / SHEET, \
        "the machine's pin was ignored and the newest file read instead"


def test_an_aspa_folder_set_for_the_whole_computer_is_found(files, tmp_path):
    own, machine = files
    a = tmp_path / "aspa"
    a.mkdir()
    (a / "J.xlsx").write_bytes(b"x")
    _machine(machine, aspa_data_dir=str(a))
    assert cs.aspa_data_dir() == a


def test_the_routing_command_set_for_the_whole_computer_is_found(files, tmp_path):
    own, machine = files
    cmd = str(tmp_path / "route-to-queue")
    _machine(machine, mousereach_route_cmd=cmd)
    assert bench_scan.route_command() == cmd


def test_writes_go_to_your_own_file_only(files, sheets):
    own, machine = files
    _machine(machine, cohort_sheets_dir=str(sheets), lab_name="A lab")
    before = machine.read_text(encoding="utf-8")
    cs.pin_cohort_sheet("CNT_99", SHEET)
    assert json.loads(own.read_text(encoding="utf-8")) == {"pinned_sheets": {"CNT_99": SHEET}}, \
        "the machine's values were copied into the person's own file"
    assert machine.read_text(encoding="utf-8") == before


def test_unpinning_your_last_pin_lets_the_machines_pins_show_again(files, sheets):
    """An empty set of pins left in your own file would hide the machine's
    pins for good (your own key wins over the machine's whole)."""
    own, machine = files
    _machine(machine, cohort_sheets_dir=str(sheets), pinned_sheets={"CNT_99": SHEET})
    cs.pin_cohort_sheet("CNT_99", OTHER)
    assert cs.find_cohort_sheet("CNT_99") == sheets / OTHER
    cs.pin_cohort_sheet("CNT_99", None)
    assert "pinned_sheets" not in json.loads(own.read_text(encoding="utf-8"))
    assert cs.find_cohort_sheet("CNT_99") == sheets / SHEET
