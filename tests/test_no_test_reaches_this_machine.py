"""The suite runs with this computer's configuration hidden, and a guard fails
any test that reaches the live folders (tests/conftest.py says why).

These pin both halves, so neither can quietly stop working: mousedb sees no
configuration at all, and the guard really does notice a file opened, written
or listed under a live location.
"""
import os
import sqlite3

import pytest

from tests import conftest as suite


def test_nothing_is_configured_inside_a_test():
    import mousedb
    from mousedb import config
    for name in ("MOUSEDB_ROOT", "DEFAULT_DB_PATH", "DEFAULT_EXPORT_PATH", "DEFAULT_LOG_PATH"):
        assert getattr(mousedb, name) is None, name
    for key in config.KEYS:
        assert config.get(key) is None, key
    assert not [k for k in os.environ if k.upper().startswith("MOUSEREACH_")]


def test_the_home_folder_is_the_tests_own(_isolated_from_this_machine):
    from pathlib import Path
    assert Path.home() == _isolated_from_this_machine
    from mousedb import config
    assert config.CONFIG_PATH.parent.parent == _isolated_from_this_machine


@pytest.fixture
def pretend_live(tmp_path, monkeypatch):
    """A folder the guard treats as live for one test; whatever the test did
    there is taken back off the guard's list before the test ends, so only
    the assertions below decide the outcome."""
    live = tmp_path / "live"
    live.mkdir()
    monkeypatch.setattr(suite, "LIVE_ROOTS",
                        suite.LIVE_ROOTS + (os.path.normcase(os.path.abspath(str(live))),))
    yield live
    suite._GUARD["hits"].clear()


def test_the_guard_sees_a_file_written_read_or_listed(pretend_live):
    (pretend_live / "a.txt").write_text("x")
    (pretend_live / "a.txt").read_text()
    os.listdir(pretend_live)
    hits = " ".join(suite._GUARD["hits"])
    assert "open('w')" in hits and "open('r')" in hits and "os.listdir" in hits


def test_the_guard_sees_a_database_and_a_parquet_file(pretend_live):
    import pandas as pd
    sqlite3.connect(str(pretend_live / "x.db")).close()
    pd.DataFrame({"a": [1]}).to_parquet(pretend_live / "t.parquet")
    pd.read_parquet(pretend_live / "t.parquet")
    hits = " ".join(suite._GUARD["hits"])
    assert "sqlite3.connect" in hits
    assert "DataFrame.to_parquet" in hits and "pandas.read_parquet" in hits


def test_a_path_elsewhere_is_not_flagged(pretend_live, tmp_path):
    (tmp_path / "elsewhere.txt").write_text("x")
    assert not suite._GUARD["hits"]
