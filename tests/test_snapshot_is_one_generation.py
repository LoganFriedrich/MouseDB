"""The snapshot is one refresh's tables, never a mix of two -- and readers can
tell which refresh they read.

WHY: the refresh would otherwise replace the tables one at a time as it read them. A
run stopped part way (a locked database, the time limit, a person) left the
first tables new and the rest old, and every reader joined them as if they
were one snapshot: reaches labeled by one night's rules beside animals and
pellets from another. Now every table is staged first, all are swapped in
together, and a generation stamp says which refresh the folder holds.
"""
import json
import os
import sqlite3
import sys

import pandas as pd
import pytest

from mousedb import snapshot_generation as sg
from mousedb.exporters import refresh_snapshot as rs


@pytest.fixture
def configured(tmp_path, monkeypatch):
    import contextlib
    from mousedb import task_mutex
    monkeypatch.setattr(task_mutex, "hold", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(rs, "watcher_blocks_db", lambda: False)
    db = tmp_path / "connectome.db"
    con = sqlite3.connect(db)
    for table in rs.TABLES:
        con.execute("CREATE TABLE %s (id INTEGER)" % table)
        con.execute("INSERT INTO %s VALUES (1)" % table)
    con.commit()
    con.close()
    snap = tmp_path / "snap"
    monkeypatch.setenv("MOUSEDB_DB_PATH", str(db))
    monkeypatch.setenv("MOUSEDB_SNAPSHOT_DIR", str(snap))
    return db, snap


def _set_rows(db, value):
    con = sqlite3.connect(db)
    for table in rs.TABLES:
        con.execute("UPDATE %s SET id = ?" % table, (value,))
    con.commit()
    con.close()


def test_a_refresh_writes_one_complete_generation(configured):
    db, snap = configured
    rs.refresh()
    st, stamp, _ = sg.state(snap)
    assert st == "complete"
    assert sorted(stamp["tables"]) == sorted(rs.TABLES)
    rec = stamp["tables"]["reach_data"]
    assert rec["rows"] == 1 and len(rec["sha256"]) == 64
    assert not (snap / sg.STAGING_NAME).exists()


def test_a_refresh_stopped_while_reading_changes_nothing(configured, monkeypatch):
    """Stopped after some tables were read: every table, and the stamp, are
    still the previous generation's -- not a mix."""
    db, snap = configured
    rs.refresh()
    first = sg.read_stamp(snap)["generation"]
    _set_rows(db, 2)
    real = pd.DataFrame.to_parquet
    seen = []

    def stop_at_fourth(self, path, *a, **k):
        seen.append(path)
        if len(seen) == 4:
            raise KeyboardInterrupt("stopped by the time limit")
        return real(self, path, *a, **k)
    monkeypatch.setattr(pd.DataFrame, "to_parquet", stop_at_fourth)
    with pytest.raises(KeyboardInterrupt):
        rs.refresh()
    monkeypatch.setattr(pd.DataFrame, "to_parquet", real)
    assert sg.state(snap)[0] == "complete"
    assert sg.read_stamp(snap)["generation"] == first
    for table in rs.TABLES:
        assert pd.read_parquet(snap / ("%s.parquet" % table))["id"].tolist() == [1]


def test_a_refresh_stopped_while_swapping_is_reported_as_broken(configured, monkeypatch):
    """The swap is short, but a stop inside it must not pass for a whole
    snapshot: the stamp still says "swapping" and its writer is gone."""
    db, snap = configured
    rs.refresh()
    _set_rows(db, 2)
    moved = []

    def swap_then_die(src, dst):
        if len(moved) == 3:
            raise KeyboardInterrupt("stopped mid-swap")
        os.replace(src, dst)
        moved.append(dst)
    monkeypatch.setattr(rs, "swap_in", swap_then_die)
    with pytest.raises(KeyboardInterrupt):
        rs.refresh()
    # The writer was this process; pretend it has since gone.
    stamp = sg.read_stamp(snap)
    assert stamp["state"] == "swapping"
    stamp["writer"]["pid"] = 999999999
    sg.write_stamp(snap, stamp)
    st, _, why = sg.state(snap)
    assert st == "broken" and "two refreshes" in why
    with pytest.raises(sg.SnapshotNotConsistent):
        sg.read_consistent(snap, lambda: None)
    # The next successful refresh makes it whole again.
    monkeypatch.setattr(rs, "swap_in", lambda s, d: os.replace(s, d))
    rs.refresh()
    assert sg.state(snap)[0] == "complete"


def test_a_table_replaced_outside_a_refresh_is_detected(configured):
    db, snap = configured
    rs.refresh()
    pd.DataFrame({"id": [7, 8]}).to_parquet(snap / "pellet_scores.parquet", index=False)
    st, _, why = sg.state(snap)
    assert st == "mixed" and "pellet_scores" in why


def test_a_reader_never_returns_data_from_two_generations(configured):
    """A refresh that lands while a reader is reading makes the reader read
    again; what it returns is from one generation, and it says which."""
    db, snap = configured
    rs.refresh()
    _set_rows(db, 2)
    calls = []

    def read():
        calls.append(1)
        rd = pd.read_parquet(snap / "reach_data.parquet")
        if len(calls) == 1:
            rs.refresh()           # the nightly job lands between two reads
        ps = pd.read_parquet(snap / "pellet_scores.parquet")
        return rd["id"].tolist(), ps["id"].tolist()
    (rd, ps), gen = sg.read_consistent(snap, read)
    assert len(calls) == 2
    assert rd == ps == [2]
    assert gen == sg.read_stamp(snap)["generation"]


def test_a_snapshot_from_before_generations_is_read_but_flagged(tmp_path):
    pd.DataFrame({"id": [1]}).to_parquet(tmp_path / "reach_data.parquet", index=False)
    assert sg.state(tmp_path)[0] == "legacy"
    value, gen = sg.read_consistent(tmp_path, lambda: "read")
    assert value == "read" and gen is None


def test_the_data_browser_refuses_a_mixed_snapshot(configured, tmp_path, monkeypatch):
    from mousedb.data_browser import build as B
    db, snap = configured
    rs.refresh()
    pd.DataFrame({"id": [7, 8]}).to_parquet(snap / "weights.parquet", index=False)
    monkeypatch.setenv("MOUSEDB_ROOT", str(tmp_path / "root"))
    with pytest.raises(B.BrowserError) as e:
        B.build(snap, root=tmp_path / "copies", progress=lambda *_: None)
    assert "next successful snapshot refresh" in str(e.value)


def test_the_data_browser_waits_only_for_the_swap(configured):
    """A refresh reading the database for an hour no longer looks like "being
    rewritten"; only the swap does, and only while its writer runs."""
    from mousedb.data_browser import build as B
    db, snap = configured
    rs.refresh()
    assert not B.refresh_in_progress(snap)
    stamp = sg.read_stamp(snap)
    sg.write_stamp(snap, {"generation": "g2", "state": "swapping",
                          "writer": {"pid": os.getpid(), "host": stamp["writer"]["host"],
                                     "started": stamp["writer"]["started"]}})
    assert B.refresh_in_progress(snap)


def test_the_exports_are_not_rewritten_from_a_mixed_snapshot(configured, tmp_path):
    """Files built from two refreshes would join one night's reaches with
    another night's animals: the previous exports stay, and the caller is told."""
    from mousedb.exporters import current
    db, snap = configured
    rs.refresh()
    pd.DataFrame({"id": [7, 8]}).to_parquet(snap / "subjects.parquet", index=False)
    out = tmp_path / "exports_current"
    out.mkdir()
    (out / "reach_data.csv").write_text("previous\n")
    with pytest.raises(sg.SnapshotNotConsistent) as e:
        current.refresh_current(snap, out)
    assert "not rewritten" in str(e.value)
    assert (out / "reach_data.csv").read_text() == "previous\n"


def test_the_status_page_says_which_generation_and_refuses_a_mix(configured, monkeypatch):
    from mousedb import data_status
    db, snap = configured
    rs.refresh()
    for name, frame in (
            ("reach_data", pd.DataFrame({"subject_id": ["PROJ_01_01"], "video_name": ["v"],
                                         "outcome_source": ["algo"], "segment_num": [1]})),
            ("pellet_scores", pd.DataFrame({"subject_id": ["PROJ_01_01"],
                                            "session_date": ["2026-01-01"]}))):
        frame.to_parquet(snap / ("%s.parquet" % name), index=False)
    # Those two files were replaced outside a refresh: the folder is now mixed.
    st = data_status.status(snap)
    assert any("not consistent" in p for p in st["problems"])
    assert not st["cohorts"]
