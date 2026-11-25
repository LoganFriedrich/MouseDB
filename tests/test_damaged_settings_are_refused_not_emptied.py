"""A damaged settings file is never read as "nothing set", and never written over.

WHY: a settings file that was not valid JSON (a hand edit that lost a comma,
a save cut off half-way) would otherwise read as EMPTY. Every location and every
pinned sheet in it silently became "not configured" -- and the next
``mousedb config --set``, "Set sheets folder" or "This is the sheet" wrote the
file back holding that ONE new value, so every other setting and every pin was
gone for good. Now:

  * reading a damaged file raises (config.SettingsFileDamaged, a ConfigError
    whose message names the file and the fix);
  * a write aimed at it saves nothing, keeps a copy beside it as
    ``<name>.damaged-<time>``, and says so;
  * the status displays (``mousedb config --show``, the Setup tab) still open
    and name the damaged file first, and importing mousedb still works, so
    the place a person fixes it is never the thing that breaks;
  * a file with a byte-order mark (Notepad) is read, not taken for damage;
  * a write goes through a temporary file swapped in, so a failed save leaves
    the old file whole.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mousedb import cohort_sheets as cs
from mousedb import config

DAMAGED = '{"mousedb_root": "/somewhere", "pinned_sheets": {"CNT_99": "a.xlsx"'


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """This person's settings file (shared by config and cohort_sheets, as in
    real use) and a machine settings file, both private to the test."""
    for env in list(config.KEYS.values()) + [cs.ENV_VAR, cs.ASPA_ENV_VAR]:
        monkeypatch.delenv(env, raising=False)
    own = tmp_path / "home" / ".mousedb" / "config.json"
    own.parent.mkdir(parents=True)
    monkeypatch.setattr(config, "CONFIG_PATH", own)
    monkeypatch.setattr(cs, "CONFIG_PATH", own)
    machine = tmp_path / "machine" / "config.json"
    monkeypatch.setenv(config.MACHINE_CONFIG_ENV, str(machine))
    return own


@pytest.fixture
def sheets(tmp_path):
    d = tmp_path / "sheets"
    d.mkdir()
    (d / "Connectome_99_Animal_Tracking.xlsx").write_bytes(b"x")
    return d


def _copies(path):
    return sorted(path.parent.glob(path.name + ".damaged-*"))


def test_setting_a_value_never_writes_over_a_damaged_file(settings):
    settings.write_text(DAMAGED, encoding="utf-8")
    with pytest.raises(config.ConfigError) as ei:
        config.set_value("lab_name", "A lab")
    assert settings.read_text(encoding="utf-8") == DAMAGED, "the damaged file was overwritten"
    copies = _copies(settings)
    assert copies and copies[0].read_text(encoding="utf-8") == DAMAGED
    msg = str(ei.value)
    assert str(settings) in msg and str(copies[0]) in msg and "damaged" in msg


def test_a_pin_never_writes_over_a_damaged_file(settings, sheets):
    settings.write_text(DAMAGED, encoding="utf-8")
    with pytest.raises(config.ConfigError):
        cs.pin_cohort_sheet("CNT_99", "Connectome_99_Animal_Tracking.xlsx", sheets_dir=sheets)
    assert settings.read_text(encoding="utf-8") == DAMAGED
    assert _copies(settings)


def test_setting_the_sheets_folder_never_writes_over_a_damaged_file(settings, sheets):
    settings.write_text(DAMAGED, encoding="utf-8")
    with pytest.raises(config.ConfigError):
        cs.set_cohort_sheets_dir(sheets)
    with pytest.raises(config.ConfigError):
        cs.set_aspa_data_dir(sheets)
    assert settings.read_text(encoding="utf-8") == DAMAGED


def test_a_damaged_file_is_not_read_as_nothing_set(settings):
    settings.write_text(DAMAGED, encoding="utf-8")
    with pytest.raises(config.ConfigError) as ei:
        config.get("mousedb_root")
    assert str(settings) in str(ei.value)
    with pytest.raises(config.ConfigError):
        cs.cohort_sheets_dir()
    with pytest.raises(config.ConfigError):
        cs.pinned_sheet("CNT_99")


def test_a_damaged_machine_file_is_refused_the_same_way(settings, tmp_path):
    machine = Path(os.environ[config.MACHINE_CONFIG_ENV])
    machine.parent.mkdir(parents=True)
    machine.write_text("[1, 2]", encoding="utf-8")       # JSON, but not settings
    with pytest.raises(config.ConfigError):
        config.get("snapshot_dir")
    with pytest.raises(config.ConfigError):
        config.set_value("lab_name", "x", machine=True)
    assert machine.read_text(encoding="utf-8") == "[1, 2]"


def test_the_status_displays_still_open_and_name_the_damaged_file(settings):
    settings.write_text(DAMAGED, encoding="utf-8")
    trouble = config.problems()
    assert str(settings) in trouble[0] and "damaged" in trouble[0], trouble
    assert str(settings) in config.describe()
    assert config.status()                                  # does not raise


def test_a_file_saved_with_a_byte_order_mark_is_read(settings, sheets):
    settings.write_text(json.dumps({"lab_name": "A lab"}), encoding="utf-8-sig")
    assert config.lab_name() == "A lab"
    cs.pin_cohort_sheet("CNT_99", "Connectome_99_Animal_Tracking.xlsx", sheets_dir=sheets)
    saved = json.loads(settings.read_text(encoding="utf-8"))
    assert saved["lab_name"] == "A lab", "the other settings were lost"
    assert saved["pinned_sheets"] == {"CNT_99": "Connectome_99_Animal_Tracking.xlsx"}


def test_a_save_that_cannot_finish_leaves_the_old_file_whole(settings, monkeypatch):
    settings.write_text(json.dumps({"lab_name": "A lab", "snapshot_dir": "/s"}),
                        encoding="utf-8")
    before = settings.read_text(encoding="utf-8")

    def refuse(*a, **k):
        raise PermissionError("held open by another program")
    monkeypatch.setattr(config.os, "replace", refuse)
    monkeypatch.setattr(config, "_REPLACE_WAIT", 0, raising=False)
    with pytest.raises(PermissionError):
        config.set_value("lab_name", "Another lab")
    assert settings.read_text(encoding="utf-8") == before
    assert not list(settings.parent.glob("*.tmp")), "a temporary file was left behind"


def test_mousedb_still_starts_with_a_damaged_settings_file(tmp_path):
    """The Setup tab and `mousedb config --show` are where a damaged file is
    fixed, so importing mousedb must not fail on it."""
    home = tmp_path / "home"
    (home / ".mousedb").mkdir(parents=True)
    (home / ".mousedb" / "config.json").write_text(DAMAGED, encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("MOUSEDB_", "MOUSEREACH_", "MOUSEBRAIN_"))
           and k.upper() != "CONNECTOME_ROOT"}
    env.update(HOME=str(home), USERPROFILE=str(home),
               MOUSEDB_MACHINE_CONFIG=str(tmp_path / "no_machine_settings.json"),
               PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    code = ("import mousedb, mousedb.data_status, mousedb.exporters.current, "
            "mousedb.exporters.refresh_snapshot; from mousedb import config; "
            "print(config.describe())")
    r = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "damaged" in r.stdout
