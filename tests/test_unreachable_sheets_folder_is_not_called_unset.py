"""A sheets folder that is set but cannot be reached is said as such -- never
"no folder is configured".

WHY: cohort_sheets_dir answers only "a folder, or None", and the status, the
import and `mousedb cohort-sheets` turned every None into "no tracking-sheet
folder is configured". A folder set correctly on a network drive that was
merely not connected was reported as never set, sending the person to set
again what was already right instead of reconnecting the drive. The reason
now comes from cohort_sheets_dir_state: not set / set but unreachable / set
and reachable but no sheet in it / a settings file damaged.
"""
import json

import pytest

from mousedb import cohort_sheets as cs
from mousedb import config, sheet_sync


@pytest.fixture
def own(tmp_path, monkeypatch):
    for env in list(config.KEYS.values()) + [cs.ENV_VAR, cs.ASPA_ENV_VAR]:
        monkeypatch.delenv(env, raising=False)
    f = tmp_path / "home" / ".mousedb" / "config.json"
    monkeypatch.setattr(config, "CONFIG_PATH", f)
    monkeypatch.setattr(cs, "CONFIG_PATH", f)
    monkeypatch.setenv(config.MACHINE_CONFIG_ENV, str(tmp_path / "no_machine.json"))
    monkeypatch.setattr(sheet_sync, "LEDGER", tmp_path / "sheet_imports.jsonl")
    return f


def _set(own, folder):
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text(json.dumps({"cohort_sheets_dir": str(folder)}), encoding="utf-8")


def test_an_unreachable_folder_is_said_to_be_unreachable(own, tmp_path):
    gone = tmp_path / "share_not_connected" / "Cohorts"
    _set(own, gone)
    problem = sheet_sync.status()["problem"]
    assert "cannot be reached" in problem and str(gone) in problem, problem
    assert "not configured" not in problem.lower()
    assert "not set" not in problem.lower()
    r = sheet_sync.import_cohorts(None, dry_run=True)
    assert "cannot be reached" in r["problem"] and str(gone) in r["problem"], r["problem"]
    text = cs.describe()
    assert "cannot be reached" in text and str(gone) in text, text
    assert "No cohort tracking sheets are configured" not in text


def test_the_state_names_the_reason_and_the_value_set(own, tmp_path):
    gone = tmp_path / "gone"
    _set(own, gone)
    st = sheet_sync.status()
    assert str(gone) in st["problem"]
    assert cs.cohort_sheets_dir_state() == (None, cs.DIR_UNREACHABLE, str(gone))
    assert st["sheets_dir_state"] == "unreachable"
    assert st["configured_raw"] == str(gone)


def test_a_reachable_folder_with_no_sheet_says_so(own, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "notes.txt").write_text("x")
    _set(own, empty)
    problem = sheet_sync.status()["problem"]
    assert "can be reached" in problem and "nothing in it is named as a sheet" in problem
    assert cs.cohort_sheets_dir_state()[1] == cs.DIR_NO_SHEET


def test_nothing_set_is_still_said_as_not_set(own):
    problem = sheet_sync.status()["problem"]
    assert "no tracking-sheet folder is set" in problem.lower(), problem
    assert cs.cohort_sheets_dir_state() == (None, cs.DIR_UNSET, None)


def test_the_aspa_folder_tells_the_same_reasons(own, tmp_path):
    gone = tmp_path / "aspa_gone"
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text(json.dumps({"aspa_data_dir": str(gone)}), encoding="utf-8")
    text = cs.describe()
    assert "ASPA" in text and "cannot be reached" in text and str(gone) in text, text
    assert cs.aspa_data_dir_state() == (None, cs.DIR_UNREACHABLE, str(gone))


def test_a_damaged_settings_file_is_named_not_called_unset(own):
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text('{"cohort_sheets_dir": ', encoding="utf-8")
    problem = sheet_sync.status()["problem"]
    assert "damaged" in problem and str(own) in problem, problem
    text = cs.describe()
    assert "damaged" in text and str(own) in text, text


def test_a_damaged_settings_file_reaches_status_and_import_as_one_sentence(own, monkeypatch,
                                                                         capsys):
    """No ledger location handed in, as in real use: the ledger's folder is
    itself a setting, and working it out from a damaged file raised -- so
    `mousedb-sheets status` and `import` (the scheduled sheet import) ended in
    a traceback instead of naming the file to fix."""
    monkeypatch.setattr(sheet_sync, "LEDGER", None)
    monkeypatch.setattr(sheet_sync, "DEFAULT_LOG_PATH", None)
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text('{"cohort_sheets_dir": ', encoding="utf-8")

    st = sheet_sync.status()
    assert "damaged" in st["problem"] and str(own) in st["problem"], st["problem"]
    assert st["sheets_dir_state"] == cs.DIR_DAMAGED

    r = sheet_sync.import_cohorts(None, dry_run=True)
    assert r["problem"] == st["problem"], "the import does not give the status's sentence"

    capsys.readouterr()
    assert sheet_sync.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Tracking sheets folder: (NOT IN USE: a settings file is damaged" in out, out
    assert "NOT CONFIGURED" not in out
    assert str(own) in out

    assert sheet_sync.main(["import", "--dry-run"]) == 1
    out = capsys.readouterr().out
    assert "NOTHING IMPORTED" in out and str(own) in out and "damaged" in out, out
    assert own.read_text(encoding="utf-8") == '{"cohort_sheets_dir": ', "the file was changed"


def test_a_command_that_needs_a_location_from_a_damaged_file_says_one_line(own, monkeypatch,
                                                                          capsys):
    """A folder named by the environment variable is in use even with a
    damaged settings file, but the import still needs the ledger's location
    from the settings: the command stops with one [FAIL] line naming the file
    and exits 1, never a traceback."""
    monkeypatch.setattr(sheet_sync, "LEDGER", None)
    monkeypatch.setattr(sheet_sync, "DEFAULT_LOG_PATH", None)
    folder = own.parent.parent / "sheets"
    folder.mkdir(parents=True)
    (folder / "Connectome_99_Animal_Tracking.xlsx").write_bytes(b"x")
    monkeypatch.setenv(cs.ENV_VAR, str(folder))
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text('{"mousedb_root": ', encoding="utf-8")
    capsys.readouterr()
    assert sheet_sync.main(["import", "--dry-run"]) == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out and "damaged" in out and str(own) in out, out
