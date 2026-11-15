"""Suite-wide guards.

A test never sees this computer's configuration, and never touches its live
folders.

WHY: mousedb takes every location from configuration (environment variables,
the person's ~/.mousedb/config.json, the machine's shared settings file,
~/.mousereach/config.json). A test that runs on a configured computer used
to pick all of that up silently: export tests read the real pipeline's
collage list and the real sheets folder, and an import test wrote audit lines
into the real database's change log. A test must pass or fail on its own
synthetic inputs, the same on any machine. So:

  * before mousedb is imported (this file runs first), every configuration
    source is pointed away: HOME/USERPROFILE and the per-user and per-machine
    application folders go to a throwaway folder, every MOUSEDB_* and
    MOUSEREACH_* environment variable is removed, and the machine settings
    file is a file that does not exist. Values computed when mousedb is
    imported (mousedb.DEFAULT_DB_PATH and the like) are therefore unset;
  * each test then gets its OWN empty home folder and settings files
    (_isolated_from_this_machine), so a test that writes settings writes them
    there, and an unconfigured value is unset, not the lab's;
  * a guard records every file opened, listed, created, renamed or removed,
    and every database connected to, under the locations the REAL
    configuration names (read once, before it is hidden). Any such access
    fails the test that made it, naming the path. It sees what Python itself
    does (Python's audit events: open, listing, rename, remove, mkdir, copy,
    sqlite3.connect) plus the parquet calls by name, because Arrow opens files
    in C++ without an event. It does not see other native code that opens
    files itself, nor child processes -- the hidden configuration is what
    keeps those away from the live folders.

A unit test must also never contend for the PRODUCTION database-task lock:
when a real hourly task held it, the suite silently hung for minutes
(2026-09-04). Constructing TaskMutex without an explicit base_dir resolves to
the production logs dir -- that is an instant, loud failure instead.
"""
import json
import os
import sys
import tempfile

import pytest

# ---------------------------------------------------------------------------
# 1. What the REAL configuration names, read before anything is hidden.
# ---------------------------------------------------------------------------

# Where the synced cloud folders are: mousedb looks for the tracking sheets
# under them (cohort_sheets.discover).
_SYNCED_FOLDER_VARS = ("ONEDRIVE", "ONEDRIVECOMMERCIAL", "ONEDRIVECONSUMER")

# Configuration values that name programs, not data -- reading them is not
# touching the lab's data, and they are never written by a test.
_NOT_DATA_KEYS = {"mousereach_env", "mousereach_route_cmd", "nas_drive"}


def _json_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _real_live_roots():
    home = os.path.expanduser("~")
    programdata = os.environ.get("PROGRAMDATA") or "C:\\ProgramData"
    local = os.environ.get("LOCALAPPDATA")
    machine = os.environ.get("MOUSEDB_MACHINE_CONFIG") or os.path.join(
        programdata, "mousedb", "config.json")
    found = [os.path.join(home, ".mousedb"), os.path.join(home, ".mousereach"),
             os.path.join(programdata, "mousedb")]
    if local:
        found.append(os.path.join(local, "mousedb"))
    values = []
    for cfg in (os.path.join(home, ".mousedb", "config.json"), machine,
                os.path.join(home, ".mousereach", "config.json")):
        values += [v for k, v in _json_file(cfg).items()
                   if isinstance(v, str) and k not in _NOT_DATA_KEYS]
    values += [v for k, v in os.environ.items()
               if k.upper().startswith(("MOUSEDB_", "MOUSEREACH_"))
               or k.upper() in _SYNCED_FOLDER_VARS]
    for v in values:
        v = v.strip()
        if not v or not os.path.isabs(v):
            continue
        n = os.path.normcase(os.path.abspath(v))
        # A bare drive ("Y:\") is not a data location: it would make every
        # path on that drive count.
        if os.path.dirname(n.rstrip("\\/")) == n.rstrip("\\/") or len(n.rstrip("\\/")) <= 2:
            continue
        found.append(v)
    roots = []
    for r in found:
        n = os.path.normcase(os.path.abspath(r)).rstrip("\\/")
        if n and n not in roots:
            roots.append(n)
    return tuple(roots)


LIVE_ROOTS = _real_live_roots()

# ---------------------------------------------------------------------------
# 2. Hide every configuration source before mousedb is imported.
# ---------------------------------------------------------------------------

_SESSION_HOME = tempfile.mkdtemp(prefix="mousedb_tests_home_")


def _point_machine_settings_away(home, setenv):
    """Every place mousedb (and the reaching tool's settings it reads) looks
    for configuration -- or, for the tracking sheets, searches -- aimed inside
    ``home``."""
    for var in ("USERPROFILE", "HOME"):
        setenv(var, home)
    for var, sub in (("LOCALAPPDATA", "localappdata"), ("APPDATA", "appdata"),
                     ("PROGRAMDATA", "programdata"), ("OneDrive", "onedrive"),
                     ("OneDriveCommercial", "onedrive"), ("OneDriveConsumer", "onedrive")):
        setenv(var, os.path.join(home, sub))
    # A settings file that does not exist: nothing is configured machine-wide.
    setenv("MOUSEDB_MACHINE_CONFIG", os.path.join(home, "programdata", "mousedb", "config.json"))


for _k in list(os.environ):
    if _k.upper().startswith(("MOUSEDB_", "MOUSEREACH_")):
        del os.environ[_k]
_point_machine_settings_away(_SESSION_HOME, os.environ.__setitem__)

import mousedb  # noqa: E402  (must come after the environment is cleared)
from mousedb.task_mutex import TaskMutex  # noqa: E402

_orig_init = TaskMutex.__init__

# Values mousedb works out when it is imported. With every source hidden they
# must all be unset; if one is not, a source was missed and every test after
# it would run against this machine's folders.
_IMPORT_TIME = ("MOUSEDB_ROOT", "DEFAULT_DB_PATH", "DEFAULT_EXPORT_PATH", "DEFAULT_LOG_PATH")
_leaked = {n: getattr(mousedb, n) for n in _IMPORT_TIME if getattr(mousedb, n, None)}
if _leaked:
    raise RuntimeError("test isolation failed: mousedb was configured from this machine "
                       "when imported: %s" % _leaked)

# ---------------------------------------------------------------------------
# 3. The guard: nothing under the real locations is touched.
# ---------------------------------------------------------------------------

_GUARD = {"on": False, "hits": []}

# Audit events that touch a path (sys.addaudithook; see the Python
# documentation's "Audit events table"), and which of their arguments are
# paths.
_PATH_EVENTS = {
    "open": (0,), "os.listdir": (0,), "os.scandir": (0,), "os.mkdir": (0,),
    "os.remove": (0,), "os.rmdir": (0,), "os.truncate": (0,), "os.utime": (0,),
    "os.chmod": (0,), "os.rename": (0, 1), "os.symlink": (0, 1), "os.link": (0, 1),
    "shutil.copyfile": (0, 1), "shutil.copymode": (0, 1), "shutil.copystat": (0, 1),
    "shutil.copytree": (0, 1), "shutil.rmtree": (0,), "shutil.move": (0, 1),
    "sqlite3.connect": (0,), "glob.glob": (0,),
}


def _live(path):
    """The normalised path when it lies under a real location, else None.
    Pure string work: an audit hook must not open files itself."""
    try:
        p = os.fspath(path)
    except TypeError:
        return None
    if isinstance(p, bytes):
        p = os.fsdecode(p)
    if not isinstance(p, str) or not p or p == ":memory:":
        return None
    if p.startswith("file:"):                      # sqlite URI
        p = p[5:].split("?", 1)[0]
        if len(p) > 2 and p[0] == "/" and p[2] == ":":
            p = p[1:]
    try:
        n = os.path.normcase(os.path.abspath(p))
    except (TypeError, ValueError):
        return None
    for root in LIVE_ROOTS:
        if n == root or n.startswith(root + os.sep):
            return n
    return None


def _audit(event, args):
    if not _GUARD["on"]:
        return
    where = _PATH_EVENTS.get(event)
    if where is None:
        return
    for i in where:
        if i < len(args):
            hit = _live(args[i])
            if hit:
                how = event
                if event == "open" and len(args) > 1:
                    how = "open(%r)" % (args[1] if args[1] is not None else args[2],)
                _GUARD["hits"].append("%s %s" % (how, hit))


sys.addaudithook(_audit)


def _watch_native_file_calls():
    """Parquet files are opened by Arrow's C++ code, which raises no Python
    audit event, so the calls that read and write them are watched by name:
    the path each is given goes through the same check."""
    import functools

    def watched(fn, label):
        @functools.wraps(fn)
        def call(*args, **kwargs):
            if _GUARD["on"]:
                for a in list(args[:2]) + [kwargs.get(k) for k in ("path", "source", "where")]:
                    if isinstance(a, (str, os.PathLike)):
                        hit = _live(a)
                        if hit:
                            _GUARD["hits"].append("%s %s" % (label, hit))
            return fn(*args, **kwargs)
        return call

    try:
        import pandas as pd
        pd.read_parquet = watched(pd.read_parquet, "pandas.read_parquet")
        pd.DataFrame.to_parquet = watched(pd.DataFrame.to_parquet, "DataFrame.to_parquet")
    except ImportError:
        pass
    try:
        import pyarrow.parquet as pq
        pq.read_table = watched(pq.read_table, "pyarrow.read_table")
        pq.write_table = watched(pq.write_table, "pyarrow.write_table")
        pq.ParquetFile.__init__ = watched(pq.ParquetFile.__init__, "pyarrow.ParquetFile")
    except ImportError:
        pass


_watch_native_file_calls()


@pytest.fixture(autouse=True)
def _isolated_from_this_machine(tmp_path_factory, monkeypatch):
    """Each test: its own empty home and settings, no MOUSEDB_* or
    MOUSEREACH_* variable, and the live-folder guard switched on. A test
    that wants a setting writes it into this home (or sets the variable
    itself), so nothing it does depends on, or reaches, this computer's
    configuration."""
    home = str(tmp_path_factory.mktemp("home"))
    for k in list(os.environ):
        if k.upper().startswith(("MOUSEDB_", "MOUSEREACH_")):
            monkeypatch.delenv(k, raising=False)
    _point_machine_settings_away(home, monkeypatch.setenv)
    cfg = os.path.join(home, ".mousedb", "config.json")
    # Module constants fixed when mousedb was imported, from the home then.
    from pathlib import Path
    from mousedb import cohort_sheets, config, sheet_sync, study_facts
    for mod in (config, cohort_sheets, sheet_sync, study_facts):
        monkeypatch.setattr(mod, "CONFIG_PATH", Path(cfg))
    _GUARD["hits"].clear()
    _GUARD["on"] = True
    yield Path(home)
    _GUARD["on"] = False
    hits = list(dict.fromkeys(_GUARD["hits"]))
    _GUARD["hits"].clear()
    if hits:
        pytest.fail("this test reached this computer's live folders (it must use "
                    "synthetic inputs under tmp_path):\n  " + "\n  ".join(hits[:20]),
                    pytrace=False)


@pytest.fixture(autouse=True)
def _no_production_hand_scores(_isolated_from_this_machine, monkeypatch):
    """An import in a test never rewrites the hand-scores file: its location is
    this machine's configuration (sheet_sync.WRITE_HAND_SCORES)."""
    from mousedb import sheet_sync
    monkeypatch.setattr(sheet_sync, "WRITE_HAND_SCORES", False)
    # nor writes a FIX THESE file into a sheets folder
    monkeypatch.setattr(sheet_sync, "WRITE_FIX_THESE", False)


@pytest.fixture(autouse=True)
def _lab_settings_for_tests(_isolated_from_this_machine, tmp_path_factory, monkeypatch):
    """Every test reads its OWN lab settings file, never the lab's: one project
    whose sheets are named Connectome_NN_Animal_Tracking.xlsx, the name the
    suite's sheet fixtures use. WHY suite-wide: which file names are sheets is
    lab configuration (mousedb.sheet_projects) with no built-in default, and a
    test must not depend on, or write to, the machine's real settings. A test
    about the settings themselves points MOUSEDB_LAB_CONFIG somewhere else."""
    f = tmp_path_factory.mktemp("lab_settings") / "mousedb_lab_config.json"
    f.write_text(json.dumps({"sheet_projects": {
        "CNT": {"sheet_name": "Connectome_{NN}_Animal_Tracking.xlsx"}}}), encoding="utf-8")
    monkeypatch.setenv("MOUSEDB_LAB_CONFIG", str(f))


@pytest.fixture(autouse=True)
def _no_machine_settings(_isolated_from_this_machine, tmp_path_factory, monkeypatch):
    """No test reads this computer's shared settings file (mousedb.config,
    source 3): a test that expects "not configured" must not be configured by
    the machine it happens to run on. A test about the machine file points
    MOUSEDB_MACHINE_CONFIG at a file of its own."""
    missing = tmp_path_factory.mktemp("machine_settings") / "config.json"
    monkeypatch.setenv("MOUSEDB_MACHINE_CONFIG", str(missing))


@pytest.fixture(autouse=True)
def _exports_lock_in_tmp(tmp_path_factory, monkeypatch):
    """The current-exports lock (exporters.current.EXPORTS_LOCK) goes in a
    folder of the test's own: a test that rewrites an exports folder must
    never take, or wait on, the lock the real refreshes use."""
    from mousedb.exporters import current
    d = tmp_path_factory.mktemp("exports_lock")
    monkeypatch.setattr(current, "_lock_dir", lambda: d, raising=False)


@pytest.fixture(autouse=True)
def _no_production_lock(monkeypatch):
    def guarded(self, name="central-db", stale_minutes=45, base_dir=None):
        if base_dir is None:
            pytest.fail(
                "test constructed TaskMutex with the PRODUCTION lock dir; "
                "pass base_dir=tmp_path (or stub task_mutex.hold)")
        _orig_init(self, name=name, stale_minutes=stale_minutes,
                   base_dir=base_dir)
    monkeypatch.setattr(TaskMutex, "__init__", guarded)
