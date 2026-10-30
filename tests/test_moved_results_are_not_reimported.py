"""A results file that only moved folder is not imported again.

WHY (2026-10-30): the finished-work folders were regrouped (each project's
folder put under its grant). The reach import's ledger is keyed by path, so
without this every moved file would have been hashed and re-imported: every
reach of every moved cohort rewritten over the share, the multi-hour import
that held the database lock on 2026-10-11. A moved file keeps its ledger entry;
a moved file whose content changed is still imported.
"""
import json
import shutil

from mousedb import import_reaches as ir

from tests.test_videos_back_in_review_leave_the_database import (
    OTHER, VIDEO, _count, _features, _setup)


def test_a_moved_folder_is_carried_over_not_reimported(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    old = root / "Analyzed" / "Project" / "PROJA01"
    _features(old, VIDEO)
    _features(old, OTHER)
    assert ir.run(log=lambda *_: None).imported == 2

    new = root / "Analyzed" / "Grant" / "Project" / "PROJA_01"
    new.parent.mkdir(parents=True)
    shutil.move(str(old), str(new))
    said = []
    res = ir.run(log=said.append)
    assert (res.imported, res.moved, res.skipped) == (0, 2, 2)
    assert any("2 of them moved folders" in s for s in said)
    ledger = ir.load_ledger()
    assert all(k.startswith("Analyzed/Grant/Project/PROJA_01/") for k in ledger)
    assert _count(db, VIDEO) == (2, 20)

    # Nothing left to carry over: the next run is a plain no-op.
    assert (ir.run(log=lambda *_: None).moved, ir.run(log=lambda *_: None).imported) == (0, 0)


def test_a_moved_file_whose_content_changed_is_imported(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    old = root / "Analyzed" / "Project" / "PROJA01"
    _features(old, VIDEO)
    ir.run(log=lambda *_: None)
    new = root / "Analyzed" / "Grant" / "Project" / "PROJA_01"
    path = _features(new, VIDEO)
    shutil.rmtree(old)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["segments"][2]["reaches"] = [dict(doc["segments"][0]["reaches"][0], reach_id=3)]
    path.write_text(json.dumps(doc), encoding="utf-8")
    res = ir.run(log=lambda *_: None)
    assert (res.moved, res.imported) == (1, 1)
    assert _count(db, VIDEO)[0] == 3


def test_a_name_that_two_old_entries_share_is_not_guessed(tmp_path, monkeypatch):
    root, db = _setup(tmp_path, monkeypatch)
    a = root / "Analyzed" / "A" / "PROJA01"
    b = root / "Analyzed" / "B" / "PROJA01"
    _features(a, VIDEO)
    _features(b, VIDEO)
    ir.run(log=lambda *_: None)
    new = root / "Analyzed" / "C" / "PROJA01"
    new.parent.mkdir(parents=True)
    shutil.move(str(a), str(new))
    shutil.rmtree(b)
    res = ir.run(log=lambda *_: None)
    assert res.moved == 0 and res.imported == 1
