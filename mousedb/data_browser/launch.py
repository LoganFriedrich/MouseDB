"""Start the data browser: build the private copy, serve it, open the web browser.

HOW IT RUNS, AND WHY THIS WAY
-----------------------------
One process per person, with a small window that says what is happening:

* The window is how the browser is stopped. Closing it stops the web server,
  so nothing is left running in the background, and a person who opens the
  data browser twice gets the one already open (``running.json`` in the
  private-copy folder says which), so servers cannot pile up.
* Datasette runs INSIDE this process, in a thread, not as a separate server
  program. A separate program could outlive the window (a crash, a killed
  launcher) and keep a file open; a thread cannot outlive its process.
* Started with ``pythonw -m mousedb.data_browser`` (the button and the desktop
  shortcut both do this), never through a ``Scripts\\*.exe`` shim: a shim held
  open by a running program blocks ``pip install`` of the package, which is
  how a reviewer's open window once stopped a deploy.

ON THIS COMPUTER ONLY. The server listens on 127.0.0.1, which no other
computer can reach. Other people logged in to the SAME computer could reach
the port, so every launch also makes a random key: the web browser it opens
gets the key once (it becomes a cookie) and any request without it is refused
with a page saying to use the button. That also stops an old bookmark from
silently landing on someone else's copy.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode

from . import build as _build
from .build import DB_NAME, BrowserError, BuildResult

TITLE = "MouseDB data browser"
STATE_FILE = "running.json"
SHORTCUT_NAME = "MouseDB - Browse the data.lnk"
MODULE = "mousedb.data_browser"
STATIC_DIR = Path(__file__).with_name("static")
INSTALL_HINT = (
    "The data browser is not installed in this Python environment.\n"
    "Whoever looks after mousedb installs it once, in the MouseDB environment:\n"
    "    pip install \"mousedb[browse]\"\n"
    "(from a copy of the code: pip install -e \".[browse]\")")


def datasette_missing_message() -> Optional[str]:
    """None when the viewer can run here, otherwise what to tell the person."""
    if importlib.util.find_spec("datasette") is None:
        return INSTALL_HINT
    return None


def pythonw() -> Path:
    """The window-less Python of this environment (no console window flashes)."""
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return w if w.is_file() else exe


# --------------------------------------------------------------------------
# the key gate
# --------------------------------------------------------------------------

REFUSED_PAGE = (
    "<!doctype html><meta charset=utf-8><title>%s</title>"
    "<body style='font-family:sans-serif;max-width:40em;margin:3em auto'>"
    "<h2>Open the data browser from MouseDB</h2>"
    "<p>This address belongs to a data browser window that you did not open from "
    "here, or that has since been closed and reopened.</p>"
    "<p>Use the <b>Browse the data</b> button on MouseDB's <i>Where Is My Data</i> "
    "tab, or the <b>MouseDB - Browse the data</b> shortcut on your desktop.</p>"
    % TITLE).encode()


class KeyGate:
    """ASGI wrapper: lets through only requests carrying this launch's key."""

    def __init__(self, app, key: str, port: int):
        self.app = app
        self.key = key
        self.cookie = "mousedb_browser_%d" % port   # cookies ignore ports; names do not

    def _cookie_ok(self, scope) -> bool:
        for name, value in scope.get("headers") or ():
            if name == b"cookie":
                for part in value.decode("latin-1").split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == self.cookie and secrets.compare_digest(v, self.key):
                        return True
        return False

    async def _reply(self, send, status, body=b"", headers=()):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"text/html; charset=utf-8"),
                                *headers]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"),
                         keep_blank_values=True)
        given = (query.get("_key") or [""])[0]
        key_given = bool(given) and secrets.compare_digest(given, self.key)
        if scope["path"] == "/-/mousedb-alive":
            return await self._reply(send, 200 if key_given else 403,
                                     b"ok" if key_given else b"")
        if self._cookie_ok(scope):
            return await self.app(scope, receive, send)
        if key_given:
            rest = {k: v for k, v in query.items() if k != "_key"}
            location = scope["path"] + ("?" + urlencode(rest, doseq=True) if rest else "")
            cookie = "%s=%s; Path=/; HttpOnly; SameSite=Strict" % (self.cookie, self.key)
            return await self._reply(send, 302, b"", [(b"location", location.encode()),
                                                      (b"set-cookie", cookie.encode())])
        return await self._reply(send, 403, REFUSED_PAGE)


# --------------------------------------------------------------------------
# the server
# --------------------------------------------------------------------------

def present_tables(path: Path):
    import sqlite3
    con = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    try:
        return {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
    finally:
        con.close()


def check_servable(path: Path) -> None:
    """The ONLY file the viewer may open is a private copy made by build().

    Checked again here, immediately before serving, so that no future caller
    can point the viewer at the live database or at the snapshot folder.
    """
    _build.check_not_live(path, "file to show")
    root = _build.cache_root()
    if not _build._inside(path, root) or path.name != _build.DB_FILE:
        raise BrowserError(
            "The data browser only opens the private copy it makes itself "
            "(inside %s); it was asked to open %s." % (root, path))


def make_datasette(built: BuildResult):
    from datasette.app import Datasette
    from . import site
    check_servable(built.path)
    present = present_tables(built.path)
    return Datasette(
        files=[],
        # immutable: opened with SQLite's immutable=1 -- read only, and no
        # locking at all, since nothing else can be writing this file.
        immutables=[str(built.path)],
        metadata=site.metadata(built, present),
        settings=dict(site.SETTINGS),
        static_mounts=[("mousedb-static", str(STATIC_DIR))],
    )


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """Datasette on 127.0.0.1 in a background thread of this process."""

    HOST = "127.0.0.1"

    def __init__(self, built: BuildResult, port: Optional[int] = None):
        import uvicorn
        self.built = built
        self.port = port or free_port()
        self.key = secrets.token_urlsafe(24)
        self.ds = make_datasette(built)
        app = KeyGate(self.ds.app(), self.key, self.port)
        # log_config=None: under pythonw there is no console, and uvicorn's
        # default logging setup writes to one.
        self._server = uvicorn.Server(uvicorn.Config(
            app, host=self.HOST, port=self.port, log_level="warning",
            log_config=None, lifespan="on"))
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        return "http://%s:%d/%s?_key=%s" % (self.HOST, self.port, DB_NAME, self.key)

    def _run(self):
        async def main():
            await self.ds.invoke_startup()
            await self._server.serve()
        asyncio.run(main())

    def start(self, wait: float = 30.0) -> None:
        self._thread = threading.Thread(target=self._run, name="data-browser", daemon=True)
        self._thread.start()
        deadline = time.time() + wait
        while time.time() < deadline:
            if alive(self.port, self.key):
                return
            if not self._thread.is_alive():
                break
            time.sleep(0.2)
        raise BrowserError("The data browser's web server did not start (port %d). "
                           "Close this window and try again." % self.port)

    def stop(self) -> None:
        self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(10)


def alive(port: int, key: str) -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/-/mousedb-alive?_key=%s"
                                    % (port, key), timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


# --------------------------------------------------------------------------
# one per person
# --------------------------------------------------------------------------

def _state_path() -> Path:
    return _build.cache_root() / STATE_FILE


def already_running() -> Optional[str]:
    """The URL of this person's data browser if one is open, else None."""
    try:
        st = json.loads(_state_path().read_text(encoding="utf-8"))
        if alive(int(st["port"]), st["key"]):
            return "http://127.0.0.1:%d/%s?_key=%s" % (int(st["port"]), DB_NAME, st["key"])
    except Exception:
        pass
    return None


def _write_state(server: Server) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"pid": os.getpid(), "port": server.port, "key": server.key,
                             "copy": str(server.built.path)}), encoding="utf-8")


def _clear_state(server: Server) -> None:
    try:
        st = json.loads(_state_path().read_text(encoding="utf-8"))
        if st.get("pid") == os.getpid():
            _state_path().unlink()
    except Exception:
        pass


# --------------------------------------------------------------------------
# desktop shortcut
# --------------------------------------------------------------------------

def desktop_dir() -> Path:
    """This person's Desktop folder, wherever Windows keeps it (it can be
    redirected, e.g. into OneDrive, so ~/Desktop is not good enough)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Environment]::GetFolderPath('Desktop')"],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        p = Path(out.stdout.strip())
        if out.returncode == 0 and str(p) and p.is_dir():
            return p
    except Exception:
        pass
    return Path.home() / "Desktop"


def shortcut_path(desktop: Optional[Path] = None) -> Path:
    return (desktop or desktop_dir()) / SHORTCUT_NAME


def make_desktop_shortcut(desktop: Optional[Path] = None) -> Path:
    """Put "MouseDB - Browse the data" on this person's desktop.

    It runs ``pythonw -m mousedb.data_browser`` of THIS environment, so it keeps
    working after the code is updated (no exe shim, no copied file)."""
    if sys.platform != "win32":
        raise BrowserError("Desktop shortcuts are made on Windows only.")
    link = shortcut_path(desktop)
    env = dict(os.environ, MDB_LNK=str(link), MDB_TGT=str(pythonw()),
               MDB_ARGS="-m %s" % MODULE, MDB_WD=str(Path.home()),
               MDB_DESC="Browse what mousedb holds (read-only copy of the snapshot)")
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:MDB_LNK); "
              "$s.TargetPath = $env:MDB_TGT; $s.Arguments = $env:MDB_ARGS; "
              "$s.WorkingDirectory = $env:MDB_WD; $s.Description = $env:MDB_DESC; $s.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       env=env, capture_output=True, text=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0 or not link.is_file():
        raise BrowserError("The desktop shortcut could not be made (%s)."
                           % (r.stderr.strip() or "no reason given"))
    return link


def start_detached() -> None:
    """Start the data browser as its own window-less process (the GUI button)."""
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(pythonw()), "-m", MODULE], cwd=str(Path.home()),
                     creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------

def _redirect_output_if_windowless() -> None:
    """Under pythonw there is no console: print() would fail. Send output to a
    log file beside the private copies, where a maintainer can read it."""
    if sys.stdout is None or sys.stderr is None:
        root = _build.cache_root()
        root.mkdir(parents=True, exist_ok=True)
        f = open(root / "data_browser.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


def run_window(open_browser: bool = True, port: Optional[int] = None) -> int:
    from PyQt5.QtCore import QThread, QTimer, pyqtSignal
    from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMessageBox,
                                 QPushButton, QVBoxLayout, QWidget)

    app = QApplication.instance() or QApplication(sys.argv)

    missing = datasette_missing_message()
    if missing:
        QMessageBox.warning(None, TITLE, missing)
        return 1
    url = already_running()
    if url:
        # One per person: show the open one instead of starting another.
        if open_browser:
            webbrowser.open(url)
        return 0

    class Builder(QThread):
        progress = pyqtSignal(str)
        done = pyqtSignal(object)
        failed = pyqtSignal(str)

        def run(self):
            try:
                self.done.emit(_build.build(progress=self.progress.emit))
            except BrowserError as e:
                self.failed.emit(str(e))
            except Exception as e:      # anything else: still said in words
                self.failed.emit("The private copy could not be made: %s: %s\n\n"
                                 "Close this message and try again; if it happens "
                                 "again, tell whoever looks after mousedb."
                                 % (type(e).__name__, e))

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.server: Optional[Server] = None
            self.built: Optional[BuildResult] = None
            self.setWindowTitle(TITLE)
            self.setMinimumWidth(520)
            lay = QVBoxLayout(self)
            self.status = QLabel("Getting ready...")
            self.status.setWordWrap(True)
            lay.addWidget(self.status)
            self.newer = QLabel("")
            self.newer.setWordWrap(True)
            self.newer.setStyleSheet("color:#b05000;")
            lay.addWidget(self.newer)
            row = QHBoxLayout()
            self.open_b = QPushButton("Open in my web browser")
            self.open_b.setEnabled(False)
            self.open_b.clicked.connect(lambda: webbrowser.open(self.server.url))
            self.short_b = QPushButton("Put a shortcut on my desktop")
            self.short_b.clicked.connect(self.make_shortcut)
            close_b = QPushButton("Close the data browser")
            close_b.clicked.connect(self.close)
            for b in (self.open_b, self.short_b, close_b):
                row.addWidget(b)
            lay.addLayout(row)
            self._shortcut_state()
            self.builder = Builder()
            self.builder.progress.connect(self.status.setText)
            self.builder.done.connect(self.ready)
            self.builder.failed.connect(self.failed)
            self.builder.start()
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.check_newer)
            self.timer.start(10 * 60 * 1000)

        def _shortcut_state(self):
            try:
                if shortcut_path().is_file():
                    self.short_b.setEnabled(False)
                    self.short_b.setText("Shortcut is on your desktop")
            except Exception:
                pass

        def make_shortcut(self):
            try:
                p = make_desktop_shortcut()
                QMessageBox.information(self, TITLE, "Done: \"%s\" is on your desktop."
                                        % p.stem)
            except Exception as e:
                QMessageBox.warning(self, TITLE, str(e))
            self._shortcut_state()

        def ready(self, built: BuildResult):
            from . import site
            self.built = built
            try:
                self.server = Server(built, port=port)
                self.server.start()
            except Exception as e:
                return self.failed(str(e))
            _write_state(self.server)
            self.open_b.setEnabled(True)
            self.status.setText(
                "The data browser is open in your web browser (if not, press "
                "\"Open in my web browser\").\n\n%s\n\nKeep this window open while you "
                "browse; you can minimize it. Closing it closes the data browser. "
                "It is read-only: nothing you do there changes the database."
                % site.snapshot_line(built))
            if open_browser:
                webbrowser.open(self.server.url)

        def check_newer(self):
            if self.built is None:
                return
            try:
                from .. import config
                files = _build.snapshot_files(config.snapshot_dir())
                if _build.fingerprint(files) != self.built.path.parent.name:
                    self.newer.setText("A newer snapshot has been written since this "
                                       "window opened. Close the data browser and open "
                                       "it again to see it.")
            except Exception:
                pass

        def failed(self, message: str):
            QMessageBox.critical(self, TITLE, message)
            self.close()

        def closeEvent(self, event):
            if self.server is not None:
                self.server.stop()
                _clear_state(self.server)
            event.accept()

    w = Window()
    w.show()
    return app.exec_()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="mousedb-data-browser",
        description="Browse what mousedb holds, read-only, in your web browser. "
                    "Opens a private copy of the snapshot, never the live database. "
                    "See docs/DATA_BROWSER.md.")
    ap.add_argument("--make-shortcut", action="store_true",
                    help="Put 'MouseDB - Browse the data' on your desktop and stop.")
    ap.add_argument("--build-only", action="store_true",
                    help="Make (or reuse) your private copy, say where it is, and stop.")
    ap.add_argument("--no-browser", action="store_true",
                    help="Do not open the web browser (the window still opens).")
    ap.add_argument("--port", type=int, default=None,
                    help="Port on 127.0.0.1 to use (default: any free one).")
    args = ap.parse_args(argv)
    _redirect_output_if_windowless()
    try:
        if args.make_shortcut:
            print("[OK] shortcut: %s" % make_desktop_shortcut())
            return 0
        if args.build_only:
            r = _build.build()
            print("[OK] private copy: %s (%s)" % (r.path, "made now" if r.built_now
                                                    else "already up to date"))
            for name, t in sorted(r.tables.items()):
                print("  %-22s %10s rows  snapshot %s" % (name, t["rows"], t["written"]))
            return 0
    except BrowserError as e:
        print("[FAIL] %s" % e)
        return 1
    return run_window(open_browser=not args.no_browser, port=args.port)
