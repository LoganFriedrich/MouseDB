"""Where the bench-vs-pipeline scan writes its worklist.

WHY: the default was a bare relative filename, so the scheduled task dropped
never_reviewed_worklist.json into whatever folder the scheduler started it
in -- the code folder. The default is now <mousedb_root>/logs/, resolved when
the scan runs (not at import) so an unconfigured machine can still import
the module and is told the exact fix when it runs.
"""
import json

import pytest

from mousedb import bench_scan, config

ITEM = {"video_id": "20260601_CNT0501_P1", "segment_nums": [2, 5]}


def _unconfigure(tmp_path, monkeypatch):
    monkeypatch.delenv("MOUSEDB_ROOT", raising=False)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "no-such-config.json")


def test_default_out_is_under_mousedb_root_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("MOUSEDB_ROOT", str(tmp_path))
    assert bench_scan.default_out() == tmp_path / "logs" / "never_reviewed_worklist.json"


def test_default_out_names_the_fix_when_unconfigured(tmp_path, monkeypatch):
    _unconfigure(tmp_path, monkeypatch)
    with pytest.raises(config.ConfigError) as ei:
        bench_scan.default_out()
    assert "mousedb config --set mousedb_root" in str(ei.value)


def test_main_writes_to_the_default_location_and_creates_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MOUSEDB_ROOT", str(tmp_path))
    monkeypatch.setattr(bench_scan, "build_worklist", lambda: [ITEM])
    assert bench_scan.main([]) == 0
    out = tmp_path / "logs" / "never_reviewed_worklist.json"
    assert json.loads(out.read_text()) == [ITEM]
    assert str(out) in capsys.readouterr().out


def test_out_override_is_honoured(tmp_path, monkeypatch):
    monkeypatch.setenv("MOUSEDB_ROOT", str(tmp_path / "root"))
    monkeypatch.setattr(bench_scan, "build_worklist", lambda: [])
    elsewhere = tmp_path / "elsewhere" / "worklist.json"
    assert bench_scan.main(["--out", str(elsewhere)]) == 0
    assert json.loads(elsewhere.read_text()) == []
    assert not (tmp_path / "root" / "logs").exists()


def test_unconfigured_machine_stops_before_scanning(tmp_path, monkeypatch, capsys):
    _unconfigure(tmp_path, monkeypatch)

    def must_not_scan():
        raise AssertionError("the scan must not run when its output cannot be placed")

    monkeypatch.setattr(bench_scan, "build_worklist", must_not_scan)
    assert bench_scan.main([]) == 1
    out = capsys.readouterr().out
    assert out.startswith("[FAIL]") and "mousedb config --set mousedb_root" in out
