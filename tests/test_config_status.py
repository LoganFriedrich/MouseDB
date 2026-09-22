"""Where this machine keeps things, reported as data so every reader agrees.

The command line and the Setup tab both show this. They read one structure rather
than each deciding what "derived" or "missing" means, because two readers that decide
separately will eventually disagree, and a person comparing a screen against a
terminal is then left with no way to tell which is lying.
"""
import json

import pytest

from mousedb import config as cfg


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    """A machine with nothing configured and nothing inherited from this one."""
    for env in cfg.KEYS.values():
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / ".mousedb" / "config.json")
    return tmp_path


def test_every_key_is_described_for_a_person(clean_env):
    for entry in cfg.status():
        assert entry["help"], "%s has no explanation a person could act on" % entry["key"]
        assert entry["kind"] in ("dir", "file", "text")


def test_an_unconfigured_machine_says_what_is_missing(clean_env):
    trouble = cfg.problems()
    assert trouble, "a machine with nothing set must not report itself ready"
    for key in cfg.REQUIRED_KEYS:
        assert any(key in p for p in trouble), "%s must be named as missing" % key


def test_a_path_that_is_not_there_is_reported_not_silently_accepted(clean_env, tmp_path):
    cfg.set_value("mousedb_root", str(tmp_path / "no" / "such" / "folder"))
    cfg.set_value("snapshot_dir", str(tmp_path))
    entry = next(e for e in cfg.status() if e["key"] == "mousedb_root")
    assert entry["exists"] is False
    assert any("not there" in p for p in cfg.problems()), (
        "a mistyped or disconnected path must be caught where it is entered, not "
        "hours later inside some other job")


def test_a_configured_machine_reports_ready(clean_env, tmp_path):
    root = tmp_path / "root"
    snap = tmp_path / "snap"
    root.mkdir()
    snap.mkdir()
    cfg.set_value("mousedb_root", str(root))
    cfg.set_value("snapshot_dir", str(snap))
    assert cfg.problems() == []


def test_a_database_that_does_not_exist_yet_is_not_a_fault(clean_env, tmp_path):
    """The normal state of a machine configured but not yet initialised.

    Reporting it would tell someone who has just done everything right that their
    setup is broken, which teaches them to ignore the warnings that matter.
    """
    root = tmp_path / "root"
    root.mkdir()
    cfg.set_value("mousedb_root", str(root))
    cfg.set_value("snapshot_dir", str(tmp_path))
    entry = next(e for e in cfg.status() if e["key"] == "db_path")
    assert entry["exists"] is False, "the database file genuinely is not there yet"
    assert cfg.problems() == [], "and that is not something to complain about"


def test_a_read_only_input_that_is_missing_IS_a_fault(clean_env, tmp_path):
    """The opposite case: somewhere we only read from has to be there already."""
    root = tmp_path / "root"
    root.mkdir()
    cfg.set_value("mousedb_root", str(root))
    cfg.set_value("snapshot_dir", str(tmp_path))
    cfg.set_value("cohort_sheets_dir", str(tmp_path / "gone"))
    assert any("cohort_sheets_dir" in p for p in cfg.problems())


def test_the_environment_is_shown_as_the_source_when_it_overrides(clean_env, tmp_path, monkeypatch):
    cfg.set_value("lab_name", "from the file")
    monkeypatch.setenv(cfg.KEYS["lab_name"], "from the environment")
    entry = next(e for e in cfg.status() if e["key"] == "lab_name")
    assert entry["value"] == "from the environment"
    assert entry["source"].startswith("environment"), (
        "a person editing the file needs to be told something else is winning")


def test_describe_reports_the_same_problems_it_finds(clean_env):
    text = cfg.describe()
    for p in cfg.problems():
        assert p.split(" ")[0] in text


def test_the_setup_tab_imports_cleanly():
    pytest.importorskip("PyQt5")
    from mousedb.gui import setup_tab
    assert hasattr(setup_tab, "SetupTab")
    assert "terminal" in setup_tab.__doc__
