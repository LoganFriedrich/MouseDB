"""A filed video whose newer run waits in a review queue is not imported.

WHY: the import takes such a video's rows out (it is not done while the new
run waits for a person). It would otherwise import the old filed results first, every
night: the same videos imported, withdrawn again, and their cohorts re-labeled
for nothing. Now the filed results are left out (not imported, not in the
ledger, no cohort re-labeled) until the new run is filed, which is then
imported as usual.
"""
import json
import shutil

from mousedb import import_reaches as ir

from tests.test_videos_back_in_review_leave_the_database import (
    OTHER, VIDEO, _count, _features, _setup)


def _manifest(folder, video, made):
    (folder / (video + "_processing_manifest.json")).write_text(
        json.dumps({"created_at": made}), encoding="utf-8")


def _relabels(monkeypatch):
    import mousedb.backfill as bf
    calls = []
    monkeypatch.setattr(bf, "backfill_phases", lambda **k: calls.append(k["cohort_id"]))
    return calls


def _ledger_names(tmp_path):
    p = tmp_path / "dbroot" / "logs" / ir.LEDGER_NAME
    led = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    return sorted(k.rsplit("/", 1)[-1][:-len(ir.FEATURES_SUFFIX)] for k in led)


def test_no_nightly_import_and_withdraw_loop(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    filed = root / "Analyzed" / "Project" / "PROJA01"
    _features(filed, VIDEO)
    _features(filed, OTHER)
    _manifest(filed, VIDEO, "2026-03-17T15:12:45")
    ir.run(log=lambda *_: None)
    assert _count(db, VIDEO) == (2, 20)

    queue = root / "Processing" / "Review" / "deep_review" / "Pending" / VIDEO
    _features(queue, VIDEO)
    _manifest(queue, VIDEO, "2026-08-19T17:23:00")

    # First night: its rows (from before the new run existed) are taken out.
    res = ir.run(log=lambda *_: None)
    assert res.held == 1 and res.imported == 0 and res.withdrawn == 1
    assert _count(db, VIDEO) == (0, 0) and _ledger_names(tmp_path) == [OTHER]

    # Every night after: nothing imported, nothing withdrawn, nothing re-labeled.
    calls = _relabels(monkeypatch)
    said = []
    res = ir.run(log=said.append)
    assert (res.held, res.imported, res.withdrawn) == (1, 0, 0)
    assert calls == [] and res.cohorts_touched == set()
    assert _count(db, VIDEO) == (0, 0) and _ledger_names(tmp_path) == [OTHER]
    assert any("1 filed videos skipped: a newer run is in review" in s for s in said)
    assert not any("[held]" in s for s in said)

    said = []
    ir.run(log=said.append, verbose=True)
    assert "  [held] %s" % VIDEO in said

    # The new run is filed (replacing the old one): imported as usual.
    for f in list(queue.iterdir()):
        shutil.move(str(f), str(filed / f.name))
    res = ir.run(log=lambda *_: None)
    assert (res.held, res.imported, res.withdrawn) == (0, 1, 0)
    assert calls == ["PROJA_01"]
    assert _count(db, VIDEO) == (2, 20) and _ledger_names(tmp_path) == sorted([VIDEO, OTHER])


def test_never_imported_filed_video_with_newer_run_waiting_stays_out(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    filed = root / "Analyzed" / "Project" / "PROJA01"
    _features(filed, VIDEO)
    _features(filed, OTHER)
    _manifest(filed, VIDEO, "2026-03-17T15:12:45")
    queue = root / "Processing" / "Review" / "triage" / "Pending" / VIDEO
    _features(queue, VIDEO)
    _manifest(queue, VIDEO, "2026-08-19T17:23:00")
    calls = _relabels(monkeypatch)
    res = ir.run(log=lambda *_: None)
    assert (res.held, res.imported, res.withdrawn) == (1, 1, 0)
    assert _count(db, VIDEO) == (0, 0) and _count(db, OTHER) == (2, 20)
    assert _ledger_names(tmp_path) == [OTHER] and calls == ["PROJA_01"]


def test_an_older_leftover_in_a_queue_does_not_hold_the_filed_video(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    filed = root / "Analyzed" / "Project" / "PROJA01"
    _features(filed, VIDEO)
    _manifest(filed, VIDEO, "2026-08-19T17:23:00")
    queue = root / "Processing" / "Review" / "triage" / "Pending" / VIDEO
    _features(queue, VIDEO)
    _manifest(queue, VIDEO, "2026-03-17T15:12:45")
    res = ir.run(log=lambda *_: None)
    assert (res.held, res.imported) == (0, 1) and _count(db, VIDEO) == (2, 20)
