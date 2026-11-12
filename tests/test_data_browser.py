"""The data browser shows a private, read-only copy -- never the live database,
and never holding a snapshot file open.

WHY THESE TESTS: the browser exists for people who leave windows open. A
viewer holding the live database blocks the imports ("database is locked",
2026-10); a viewer holding a snapshot file stops the nightly job replacing it.
Each test pins one part of how that is prevented (mousedb/data_browser).
"""
import os
import sqlite3
import stat
import sys

import pandas as pd
import pytest

from mousedb.data_browser import build as B
from mousedb.data_browser import site, views


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A tiny snapshot, a private-copy folder, and a mousedb root and live
    database elsewhere -- all under tmp_path, none of them the machine's."""
    root = tmp_path / "mousedb_root"
    root.mkdir()
    live = root / "connectome.db"
    sqlite3.connect(live).close()
    snap = tmp_path / "snapshot"
    snap.mkdir()
    cache = tmp_path / "browser_cache"
    monkeypatch.setenv("MOUSEDB_ROOT", str(root))
    monkeypatch.setenv("MOUSEDB_DB_PATH", str(live))
    monkeypatch.setenv("MOUSEDB_SNAPSHOT_DIR", str(snap))
    monkeypatch.setenv("MOUSEDB_BROWSER_CACHE", str(cache))
    reach = pd.DataFrame({
        "id": [1, 2, 3], "subject_id": ["PRJ_01_01", "PRJ_01_01", "PRJ_01_02"],
        "video_name": ["20250101_PRJ0101_P1"] * 2 + ["20250101_PRJ0102_P1"],
        "session_date": ["2025-01-01"] * 3, "tray_type": ["P"] * 3,
        "run_number": [1, 1, 1], "segment_num": [1, 2, 1], "reach_num": [1, 1, 1],
        "outcome": ["retrieved", None, "untouched"],
        "segment_outcome": ["retrieved", "displaced_sa", "untouched"],
        "test_phase": ["Pillar"] * 3, "duration_frames": [10, 12, 9],
        "extended_features": ['{"a": 1}'] * 3})
    reach.to_parquet(snap / "reach_data.parquet", index=False)
    pd.DataFrame({
        "id": range(1, 5), "subject_id": ["PRJ_01_01"] * 2 + ["PRJ_01_02"] * 2,
        "session_date": ["2025-01-01"] * 4, "test_phase": ["Pillar"] * 4,
        "tray_type": ["P"] * 4, "tray_number": [1] * 4, "pellet_number": [1, 2, 1, 2],
        "score": [2, 1, 0, 0]}).to_parquet(snap / "pellet_scores.parquet", index=False)
    pd.DataFrame({"subject_id": ["PRJ_01_01", "PRJ_01_02"], "cohort_id": ["PRJ_01"] * 2,
                  "sex": ["F", "M"]}).to_parquet(snap / "subjects.parquet", index=False)
    pd.DataFrame({"video_name": ["20250101_PRJ0101_P1"] * 2, "segment_num": [1, 2],
                  "subject_id": ["PRJ_01_01"] * 2, "session_date": ["2025-01-01"] * 2,
                  "tray_type": ["P"] * 2, "outcome": ["retrieved", "displaced_sa"]}
                 ).to_parquet(snap / "video_pellets.parquet", index=False)
    return {"root": root, "live": live, "snap": snap, "cache": cache}


def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)


def test_copy_is_made_in_the_private_folder_with_the_ready_made_tables(env):
    r = B.build(progress=lambda *_: None)
    assert r.built_now
    assert B._inside(r.path, env["cache"])
    con = _ro(r.path)
    names = {n for (n,) in con.execute("SELECT name FROM sqlite_master")}
    assert {"reach_data", "pellet_scores", "animals", "sessions", "reaches",
            B.ABOUT_TABLE} <= names
    # the JSON blob is left out (it is in the exports, one column per value)
    cols = [c[1] for c in con.execute("PRAGMA table_info(reach_data)")]
    assert "extended_features" not in cols and "cohort_id" in cols
    s = con.execute("SELECT hand_retrieved, hand_displaced, algo_retrieved, algo_displaced, "
                    "reaches FROM sessions WHERE subject_id = 'PRJ_01_01'").fetchone()
    assert s == (1, 1, 1, 1, 2)
    assert con.execute("SELECT cohort_id, sex FROM animals WHERE subject_id='PRJ_01_02'"
                       ).fetchone() == ("PRJ_01", "M")
    con.close()


def test_the_copy_is_read_only_on_disk(env):
    r = B.build(progress=lambda *_: None)
    assert not os.stat(r.path).st_mode & stat.S_IWRITE
    with pytest.raises(sqlite3.OperationalError):
        con = sqlite3.connect("file:%s?immutable=1" % r.path.as_posix(), uri=True)
        con.execute("DELETE FROM reach_data")
        con.commit()


def test_an_unchanged_snapshot_reuses_the_copy_and_a_new_one_replaces_it(env):
    first = B.build(progress=lambda *_: None)
    again = B.build(progress=lambda *_: None)
    assert again.path == first.path and not again.built_now
    # the nightly job writes a new table file
    pd.DataFrame({"subject_id": ["PRJ_01_01"], "cohort_id": ["PRJ_01"], "sex": ["F"]}
                 ).to_parquet(env["snap"] / "subjects.parquet", index=False)
    os.utime(env["snap"] / "subjects.parquet", (1e9, 2e9))
    newer = B.build(progress=lambda *_: None)
    assert newer.built_now and newer.path != first.path
    assert not first.path.parent.exists(), "the previous copy is deleted"


def test_no_snapshot_file_is_left_open_after_the_copy(env):
    """The nightly job must be able to replace every snapshot file the moment
    the copy is made -- on Windows a replace fails on a file that is open."""
    B.build(progress=lambda *_: None)
    for f in env["snap"].glob("*.parquet"):
        tmp = f.with_name(f.name + ".tmp")
        tmp.write_bytes(f.read_bytes())
        os.replace(tmp, f)


def test_waits_while_the_snapshot_job_is_writing_a_table(env, monkeypatch):
    (env["snap"] / "reach_data.parquet.tmp").write_bytes(b"half")
    with pytest.raises(B.BrowserError, match="being rewritten"):
        B.wait_for_refresh(env["snap"], lambda *_: None, wait_seconds=0.3, poll=0.1)


def test_refuses_to_build_inside_mousedbs_own_folder(env, monkeypatch):
    with pytest.raises(B.BrowserError, match="only mousedb's importers"):
        B.build(root=env["root"] / "browser", progress=lambda *_: None)


def test_refuses_to_build_inside_the_snapshot_folder(env):
    with pytest.raises(B.BrowserError, match="snapshot folder"):
        B.build(root=env["snap"] / "browser", progress=lambda *_: None)


def test_says_what_to_do_when_there_is_no_snapshot(env):
    (env["snap"] / "reach_data.parquet").unlink()
    with pytest.raises(B.BrowserError, match="no snapshot to browse yet"):
        B.build(progress=lambda *_: None)


def test_the_viewer_refuses_the_live_database_and_any_file_it_did_not_make(env):
    from mousedb.data_browser import launch
    with pytest.raises(B.BrowserError, match="LIVE database"):
        launch.check_servable(env["live"])
    stray = env["cache"] / "elsewhere" / "other.sqlite"
    stray.parent.mkdir(parents=True)
    sqlite3.connect(stray).close()
    with pytest.raises(B.BrowserError, match="only opens the private copy"):
        launch.check_servable(stray)
    launch.check_servable(B.build(progress=lambda *_: None).path)   # its own copy: fine


def test_every_column_the_browser_shows_has_a_description(env):
    """Plain-English descriptions are the point for people who do not read
    code. A column added to the snapshot or a ready-made table without one
    fails here."""
    r = B.build(progress=lambda *_: None)
    descriptions = site.column_descriptions()
    con = _ro(r.path)
    missing = []
    for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')"):
        for c in con.execute('PRAGMA table_info("%s")' % t):
            if not descriptions.get(t, {}).get(c[1]):
                missing.append("%s.%s" % (t, c[1]))
    con.close()
    assert missing == []


def test_every_ready_made_table_has_a_description_and_listed_tables_exist():
    for t in views.READY_MADE:
        assert t in site.TABLE_TEXT
    assert set(site.START_HERE) <= set(views.READY_MADE)


def test_writes_through_the_viewer_are_refused(env):
    datasette = pytest.importorskip("datasette")
    del datasette
    import asyncio
    from mousedb.data_browser import launch
    ds = launch.make_datasette(B.build(progress=lambda *_: None))

    async def go():
        await ds.invoke_startup()
        db = ds.get_database(B.DB_NAME)
        with pytest.raises(Exception):
            await db.execute_write("DELETE FROM reach_data")
        bad = await ds.client.get("/%s.json?sql=DELETE+FROM+reach_data" % B.DB_NAME)
        assert bad.status_code == 400
        sneaky = await ds.client.get(
            "/%s.json?sql=WITH+a+AS+(SELECT+1)+DELETE+FROM+reach_data" % B.DB_NAME)
        assert sneaky.status_code == 400
        rows = await ds.client.get("/%s/reach_data.json?_shape=array" % B.DB_NAME)
        assert len(rows.json()) == 3
    asyncio.run(go())


def test_the_server_answers_only_requests_carrying_its_key():
    httpx = pytest.importorskip("httpx")
    import asyncio
    from mousedb.data_browser.launch import KeyGate

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"data"})

    gate = KeyGate(app, "secret", 9999)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate),
                                     base_url="http://127.0.0.1:9999") as c:
            assert (await c.get("/mousedb")).status_code == 403
            assert (await c.get("/mousedb?_key=wrong")).status_code == 403
            r = await c.get("/mousedb?_key=secret&x=1")
            assert r.status_code == 302 and r.headers["location"] == "/mousedb?x=1"
            c.cookies.clear()
            c.cookies.set("mousedb_browser_9999", "secret")
            ok = await c.get("/mousedb")
            assert ok.status_code == 200 and ok.text == "data"
            assert (await c.get("/-/mousedb-alive?_key=secret")).status_code == 200
    asyncio.run(go())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop shortcuts")
def test_desktop_shortcut_runs_this_environment_without_an_exe_shim(tmp_path):
    from mousedb.data_browser import launch
    link = launch.make_desktop_shortcut(desktop=tmp_path)
    assert link.is_file() and link.name == launch.SHORTCUT_NAME


def test_brain_tables_get_their_ready_made_tables_once_in_the_snapshot(env):
    pd.DataFrame({"id": [7], "subject_id": ["PRJ_01_01"], "brain_id": ["1_PRJ_01_01_1x_z4"],
                  "status": ["analyzed"], "registration_approved": [1]}
                 ).to_parquet(env["snap"] / "brain_samples.parquet", index=False)
    pd.DataFrame({"id": [1], "brain_sample_id": [7], "region_id": [1], "region_name": ["R"],
                  "region_acronym": ["R"], "hemisphere": ["left"], "cell_count": [5],
                  "is_final": [1]}).to_parquet(env["snap"] / "region_counts.parquet", index=False)
    pd.DataFrame({"id": [1], "brain_sample_id": [7], "group_name": ["G"], "hemisphere": ["both"],
                  "cell_count": [9], "is_final": [1]}
                 ).to_parquet(env["snap"] / "elife_region_counts.parquet", index=False)
    r = B.build(progress=lambda *_: None)
    con = _ro(r.path)
    assert con.execute("SELECT subject_id, cohort_id, region_name, cell_count "
                       "FROM brain_region_counts").fetchall() == [("PRJ_01_01", "PRJ_01", "R", 5)]
    assert con.execute("SELECT group_name, cell_count FROM brain_group_counts"
                       ).fetchall() == [("G", 9)]
    descriptions = site.column_descriptions()
    for view in ("brain_region_counts", "brain_group_counts"):
        for c in con.execute('PRAGMA table_info("%s")' % view):
            assert descriptions[view].get(c[1]), "%s.%s" % (view, c[1])
    con.close()
