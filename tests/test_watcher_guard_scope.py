"""The database-writer guard yields only to a watcher that can write the central database."""
import json
import sys
import types
import pytest

import mousedb.bench_scan as bs


@pytest.fixture
def running_watcher(monkeypatch):
    monkeypatch.setattr(bs, "watcher_running", lambda: True)
    monkeypatch.delenv("MOUSEREACH_CENTRAL_DB", raising=False)


def _point_home(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))


def test_no_watcher_never_blocks(monkeypatch):
    monkeypatch.setattr(bs, "watcher_running", lambda: False)
    assert bs.watcher_blocks_db() is False


def test_watcher_without_central_db_does_not_block(running_watcher, monkeypatch, tmp_path):
    _point_home(monkeypatch, tmp_path)
    (tmp_path / ".mousereach").mkdir()
    (tmp_path / ".mousereach" / "config.json").write_text(json.dumps({"nas_root": "x"}), encoding="utf-8")
    assert bs.watcher_blocks_db() is False


def test_watcher_with_central_db_blocks(running_watcher, monkeypatch, tmp_path):
    _point_home(monkeypatch, tmp_path)
    (tmp_path / ".mousereach").mkdir()
    (tmp_path / ".mousereach" / "config.json").write_text(json.dumps({"central_db": "somewhere.db"}), encoding="utf-8")
    assert bs.watcher_blocks_db() is True


def test_env_var_blocks(running_watcher, monkeypatch, tmp_path):
    _point_home(monkeypatch, tmp_path)
    monkeypatch.setenv("MOUSEREACH_CENTRAL_DB", "somewhere.db")
    assert bs.watcher_blocks_db() is True


def test_unreadable_config_fails_safe(running_watcher, monkeypatch, tmp_path):
    _point_home(monkeypatch, tmp_path)
    (tmp_path / ".mousereach").mkdir()
    (tmp_path / ".mousereach" / "config.json").write_text("{not json", encoding="utf-8")
    assert bs.watcher_blocks_db() is True


def test_missing_config_means_watcher_cannot_write(running_watcher, monkeypatch, tmp_path):
    _point_home(monkeypatch, tmp_path)
    assert bs.watcher_blocks_db() is False
