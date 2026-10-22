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


# ------------------------------------------------ which pellets are compared
# WHY (2026-10-22): a segment with no detected reach has no reach row, so the
# scan never compared it -- yet "the bench says the pellet moved and the
# pipeline found no reach" is a strong disagreement. And the current detector
# version was a constant, which would silently match nothing once it changed.

import pandas as pd


def _versions(tmp_path, monkeypatch, version="6.1.0"):
    (tmp_path / "pipeline_versions.json").write_text(
        json.dumps({"versions": {"outcome_detector": version}}), encoding="utf-8")
    monkeypatch.setenv("MOUSEREACH_PIPELINE_ROOT", str(tmp_path))


def _tables():
    key = dict(subject_id="CNT_05_01", session_date="2026-06-01", tray_type="P")
    rd = pd.DataFrame([dict(key, run_number=1, segment_num=1, segment_outcome="retrieved",
                            outcome_detector_version="6.1.0", video_name="V",
                            reach_id=1, outcome_source="algo")])
    ps = pd.DataFrame([dict(key, tray_number=1, pellet_number=n, score=s)
                       for n, s in ((1, 2), (2, 2), (3, 0))])
    vp = pd.DataFrame([dict(key, video_name="V", run_number=1, segment_num=n,
                            outcome=o, n_reaches=r)
                       for n, o, r in ((1, "retrieved", 1), (2, "untouched", 0),
                                       (3, "untouched", 0))])
    return rd, ps, vp


def test_the_current_version_is_read_from_mousereach(tmp_path, monkeypatch):
    _versions(tmp_path, monkeypatch, "7.0.0")
    assert bench_scan.current_outcome_version() == "7.0.0"


def test_an_unreadable_version_file_names_the_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("MOUSEREACH_PIPELINE_ROOT", str(tmp_path))
    with pytest.raises(config.ConfigError) as ei:
        bench_scan.current_outcome_version()
    assert "pipeline_versions.json" in str(ei.value)


def test_a_segment_with_no_reach_is_compared(tmp_path, monkeypatch):
    _versions(tmp_path, monkeypatch)
    rd, ps, vp = _tables()
    without = bench_scan.pair_current(rd, ps)
    assert sorted(without.pellet_number) == [1], "the old pairing saw only segment 1"
    paired = bench_scan.pair_current(rd, ps, vp=vp)
    assert sorted(paired.pellet_number) == [1, 2, 3]
    two = paired[paired.pellet_number == 2].iloc[0]
    assert (two.human, two.algo, two.outcome_source) == ("retrieved", "missed", "unknown")


def test_the_worklist_includes_a_no_reach_disagreement(tmp_path, monkeypatch):
    _versions(tmp_path, monkeypatch)
    rd, ps, vp = _tables()
    monkeypatch.setattr(bench_scan, "load_paired_pellets", lambda: (rd, ps))
    monkeypatch.setattr(bench_scan, "load_video_pellets", lambda: vp)
    import mousedb.analyzable as analyzable
    monkeypatch.setattr(analyzable, "finished_videos", lambda: {"V"})
    assert bench_scan.build_worklist() == [{"video_id": "V", "segment_nums": [2]}]
