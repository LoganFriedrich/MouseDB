"""The watcher views (`mousedb video-status`, `mousedb archive-status`, the web
dashboard) read the watcher database the watcher actually writes, and say
whose records they are.

WHY: a node may point its watcher at a database other than the default
(``db_path`` in the ``watcher`` section of ~/.mousereach/config.json, set
because SQLite over a network share loses writes). The watcher then writes
ONLY that file; the default ``<processing_root>/watcher.db`` may still sit
beside it, full of old rows. mousedb would otherwise open the default, so its views
showed a stale copy as the pipeline's state. And even the
right file holds only THIS computer's watcher records -- other computers'
work and the review queues' folders are not in it -- so every view now says
so, with the file and when it was last written.
"""
import json
import os
import sqlite3
import time
from argparse import Namespace
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _watcher_db(path: Path, states):
    """A minimal watcher database: one video per entry of ``states``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT UNIQUE NOT NULL, source_path TEXT NOT NULL,
            animal_id TEXT, state TEXT NOT NULL, date TEXT, tray_type TEXT,
            tray_position INTEGER, error_message TEXT, error_count INTEGER DEFAULT 0,
            last_error_at TEXT, crystallized_label TEXT,
            discovered_at TEXT, validated_at TEXT, dlc_started_at TEXT,
            dlc_completed_at TEXT, processing_started_at TEXT,
            processing_completed_at TEXT, archived_at TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE collages (id INTEGER PRIMARY KEY);
        CREATE TABLE processing_log (id INTEGER PRIMARY KEY, video_id TEXT,
            step TEXT, status TEXT, message TEXT, duration_seconds REAL,
            machine TEXT, created_at TEXT);
    """)
    for i, st in enumerate(states):
        conn.execute(
            "INSERT INTO videos (video_id, source_path, animal_id, state) "
            "VALUES (?, ?, ?, ?)", (f"20990101_PROJA0101_P{i}", "x", "PROJA0101", st))
    conn.commit()
    conn.close()


@pytest.fixture
def node(_isolated_from_this_machine, tmp_path):
    """A node whose watcher writes a configured database; the default
    location beside it holds an older, different copy (the decoy)."""
    home = _isolated_from_this_machine
    proc = tmp_path / "processing"
    live = tmp_path / "local" / "watcher_live.db"
    _watcher_db(proc / "watcher.db", ["archived"] * 5)          # decoy
    _watcher_db(live, ["processing"] * 3)                       # what the watcher writes
    cfg = home / ".mousereach" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({
        "processing_root": str(proc),
        "watcher": {"db_path": str(live)},
    }), encoding="utf-8")
    return {"home": home, "proc": proc, "live": live, "cfg": cfg}


def test_the_configured_watcher_database_is_read_not_the_default(node):
    from mousedb.watcher_bridge import WatcherBridge, find_watcher_db
    st = find_watcher_db()
    assert st.available and Path(st.db_path) == node["live"]
    assert WatcherBridge().get_pipeline_summary().total_videos == 3


def test_a_configured_database_that_is_missing_is_not_replaced_by_the_default(node):
    """The configured file IS the live one: when it is missing, the default
    beside it is a stale copy, so nothing is shown rather than the copy."""
    node["live"].unlink()
    from mousedb.watcher_bridge import find_watcher_db
    st = find_watcher_db()
    assert not st.available
    assert str(node["live"]) in st.message


def test_without_a_configured_database_the_default_is_read(node):
    node["cfg"].write_text(json.dumps({"processing_root": str(node["proc"])}),
                           encoding="utf-8")
    from mousedb.watcher_bridge import WatcherBridge
    assert WatcherBridge().get_pipeline_summary().total_videos == 5


def test_the_environment_variable_still_wins(node, tmp_path, monkeypatch):
    other = tmp_path / "elsewhere.db"
    _watcher_db(other, ["failed"])
    monkeypatch.setenv("MOUSEDB_WATCHER_DB", str(other))
    from mousedb.watcher_bridge import WatcherBridge
    assert WatcherBridge().get_pipeline_summary().total_videos == 1


def _when_written(path):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path)))


@pytest.mark.parametrize("command", ["cmd_video_status", "cmd_archive_status"])
def test_both_commands_say_these_are_one_computers_records(node, capsys, command):
    from mousedb import cli
    getattr(cli, command)(Namespace(json=False, by_animal=False, cohort=None,
                                    show_errors=False, verbose=False))
    out = capsys.readouterr().out
    assert "this computer's watcher only" in out
    assert str(node["live"]) in out
    assert _when_written(node["live"]) in out
    assert "MouseReach Video Processing Progress" in out
    out.encode("ascii")  # printed to a Windows console: ASCII only


@pytest.mark.parametrize("command", ["cmd_video_status", "cmd_archive_status"])
def test_both_commands_json_name_the_scope(node, capsys, command):
    from mousedb import cli
    getattr(cli, command)(Namespace(json=True, by_animal=False, cohort=None,
                                    show_errors=False, verbose=False))
    out = json.loads(capsys.readouterr().out)
    assert out["watcher_db"] == str(node["live"])
    assert out["scope"] == "this_computer_watcher_records_only"
    assert out["watcher_db_last_written"]


def test_a_file_named_by_the_environment_variable_is_not_called_this_computers(
        node, tmp_path, monkeypatch, capsys):
    """MOUSEDB_WATCHER_DB (or mousedb's pipeline folder) can name any
    watcher's database; the views say which rule found it instead of calling
    it this computer's watcher."""
    other = tmp_path / "elsewhere.db"
    _watcher_db(other, ["failed"])
    monkeypatch.setenv("MOUSEDB_WATCHER_DB", str(other))
    from mousedb import cli
    cli.cmd_video_status(Namespace(json=False, by_animal=False, cohort=None,
                                   show_errors=False))
    out = capsys.readouterr().out
    assert "this computer's watcher only" not in out
    assert "MOUSEDB_WATCHER_DB" in out
    assert str(other) in out
    cli.cmd_video_status(Namespace(json=True, by_animal=False, cohort=None,
                                   show_errors=False))
    assert json.loads(capsys.readouterr().out)["scope"] == \
        "one_watcher_database_records_only"


def test_a_file_found_in_mousedbs_pipeline_folder_is_not_called_this_computers(
        _isolated_from_this_machine, tmp_path, monkeypatch):
    from mousedb import watcher_bridge
    import mousedb.config
    root = tmp_path / "pipeline"
    _watcher_db(root / "watcher.db", ["archived"])
    monkeypatch.setattr(mousedb.config, "mousereach_pipeline_root", lambda: root)
    st = watcher_bridge.find_watcher_db()
    assert st.available
    text = " ".join(watcher_bridge.watcher_scope_lines(st))
    assert "this computer's watcher only" not in text
    assert "pipeline folder" in text


# --- Web dashboard: the scope line is refreshed with the cards -------------

def test_web_dashboard_refreshes_the_scope_line(tmp_path):
    """The dashboard re-fetches its numbers every 30 s; the line saying
    whose records they are (and when the file was last written) must be
    re-fetched too, or it shows a stale time beside fresh numbers."""
    import ast
    import re
    import jinja2
    from mousedb.watcher_bridge import WatcherBridge, watcher_scope_lines

    web = Path(__file__).resolve().parents[1] / "mousedb" / "web"
    page = (web / "templates" / "dashboard.html").read_text(encoding="utf-8")
    # The scope lines sit inside an element the page re-fetches from
    # /dashboard/scope on a timer.
    m = re.search(r'<div[^>]*hx-get="/dashboard/scope"[^>]*hx-trigger="every[^"]*"'
                  r'[^>]*>(.*?)</div>', page, re.S)
    assert m, "no periodically refreshed scope element on the dashboard"
    assert 'include "components/watcher_scope.html"' in m.group(1)

    # ... and the router serves that partial.
    tree = ast.parse((web / "routers" / "dashboard.py").read_text(encoding="utf-8"))
    routes = [d.args[0].value for f in ast.walk(tree)
              if isinstance(f, ast.AsyncFunctionDef) for d in f.decorator_list
              if isinstance(d, ast.Call) and d.args
              and isinstance(d.args[0], ast.Constant)]
    assert "/scope" in routes

    # The partial renders the same lines the commands show.
    db = tmp_path / "w.db"
    _watcher_db(db, ["archived"])
    lines = watcher_scope_lines(WatcherBridge(db_path=db).status)
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(web / "templates")))
    html = env.get_template("components/watcher_scope.html").render(scope_lines=lines)
    for line in lines:
        assert line in html
