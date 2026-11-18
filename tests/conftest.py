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
  * a guard watches every file opened, listed, created, renamed or removed,
    and every database connected to, under the locations the REAL
    configuration names (section 1 below says where it finds them). A WRITE
    there -- a file opened for writing, a file or folder created, renamed,
    replaced, copied onto, removed, a database connected to, a parquet file
    written -- is REFUSED: the call raises LiveFolderRefused and nothing is
    written. A READ or a listing there is recorded. Either way the test that
    did it fails, naming the path. It sees what Python itself does (Python's
    audit events) plus the parquet calls by name, because Arrow opens files
    in C++ without an event. It does not see other native code that opens
    files itself, nor child processes -- the hidden configuration is what
    keeps those away from the live folders;
  * the code's own checkout (the folder holding this tests/ folder) is NOT
    lab data, even when it sits inside a live folder (a working copy may be
    kept inside the database folder). Its files are exempt; the
    folders and files beside it (exports, logs, the database) stay guarded.

A unit test must also never contend for the PRODUCTION database-task lock:
when a real hourly task held it, the suite silently hung for minutes
(2026-09-04). Constructing TaskMutex without an explicit base_dir resolves to
the production logs dir -- that is an instant, loud failure instead.
"""
import itertools
import json
import os
import sys
import tempfile

import pytest

# ---------------------------------------------------------------------------
# 1. What the REAL configuration names, read before anything is hidden.
#
# WHY so many sources: the person or script running the suite usually hides
# this computer's configuration BEFORE pytest starts (HOME, USERPROFILE,
# APPDATA, LOCALAPPDATA and PROGRAMDATA pointed at a scratch folder, every
# MOUSE* variable unset), so the configuration the environment points at is
# empty. The guard must still know the real locations, so it also reads them
# from where the tool would find them on a normal login, which no environment
# variable can move:
#   * the settings files in the REAL home folder and the REAL machine-wide
#     program-data folder (Windows tells us those folders itself);
#   * the user's and the machine's SAVED environment variables (on Windows,
#     the registry), in any upper/lower case -- a variable unset in this
#     process is usually still saved there;
#   * the environment and the settings files the environment points at, as
#     before;
#   * a list the runner supplies: TEST_GUARD_REAL_ROOTS (folders separated by
#     the path separator, ";" on Windows) and/or TEST_GUARD_REAL_ROOTS_FILE (a
#     text file, one folder per line, "#" starts a comment). Their names
#     deliberately contain neither the tool's nor the project's name, so a
#     runner's "no such variable is set" check does not trip on them.
# No location is written in this file: it only reads where the locations are
# configured.
# ---------------------------------------------------------------------------

# Where the synced cloud folders are: mousedb looks for the tracking sheets
# under them (cohort_sheets.discover).
_SYNCED_FOLDER_VARS = ("ONEDRIVE", "ONEDRIVECOMMERCIAL", "ONEDRIVECONSUMER")

# Prefixes of the variables the lab's tools read their locations from.
_TOOL_VAR_PREFIXES = ("MOUSEDB_", "MOUSEREACH_", "MOUSEBRAIN_", "CONNECTOME_ROOT")

# Configuration values that name programs, not data -- reading them is not
# touching the lab's data, and they are never written by a test.
_NOT_DATA_KEYS = {"mousereach_env", "mousereach_route_cmd", "nas_drive"}

# The runner's own list of real locations (see above).
_RUNNER_ROOTS_VAR = "TEST_GUARD_REAL_ROOTS"
_RUNNER_ROOTS_FILE_VAR = "TEST_GUARD_REAL_ROOTS_FILE"

_IS_WINDOWS = os.name == "nt"


def _json_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _known_folders():
    """The REAL home, program-data and local application-data folders of
    this login, whatever HOME/USERPROFILE/PROGRAMDATA/LOCALAPPDATA say.
    Windows: SHGetKnownFolderPath, which reads the profile from the system,
    not from the environment. Elsewhere: the password database's home."""
    found = {"home": [], "programdata": [], "localappdata": []}
    if _IS_WINDOWS:
        try:
            import ctypes
            import uuid
            from ctypes import wintypes

            class _GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

            def known(guid):
                u = uuid.UUID(guid)
                g = _GUID(u.fields[0], u.fields[1], u.fields[2],
                          (ctypes.c_ubyte * 8)(*u.bytes[8:]))
                out = ctypes.c_wchar_p()
                hr = ctypes.windll.shell32.SHGetKnownFolderPath(
                    ctypes.byref(g), 0, None, ctypes.byref(out))
                try:
                    return out.value if hr == 0 else None
                finally:
                    ctypes.windll.ole32.CoTaskMemFree(out)

            for key, guid in (("home", "5E6C858F-0E22-4760-9AFE-EA3317B67173"),
                              ("programdata", "62AB5D82-FDC1-4DC3-A9DD-070D1D495D97"),
                              ("localappdata", "F1B32785-6FBA-4FCF-9D55-7B8E7F157091")):
                p = known(guid)
                if p:
                    found[key].append(p)
        except Exception:                          # noqa: BLE001 - best effort
            pass
    else:
        try:
            import pwd
            found["home"].append(pwd.getpwuid(os.getuid()).pw_dir)
        except Exception:                          # noqa: BLE001 - best effort
            pass
    return found


def _registry_environment():
    """(name, value) of the environment variables saved for this user and
    for the machine. Windows only (the registry); elsewhere nothing."""
    if not _IS_WINDOWS:
        return []
    try:
        import winreg
    except ImportError:
        return []
    out = []
    for hive, key in ((winreg.HKEY_CURRENT_USER, "Environment"),
                      (winreg.HKEY_LOCAL_MACHINE,
                       r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
        try:
            with winreg.OpenKey(hive, key) as k:
                i = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    if isinstance(value, str):
                        out.append((name, value))
        except OSError:
            continue
    return out


def _runner_roots():
    """The runner's own list (TEST_GUARD_REAL_ROOTS and the file named by
    TEST_GUARD_REAL_ROOTS_FILE)."""
    out = [p for p in os.environ.get(_RUNNER_ROOTS_VAR, "").split(os.pathsep) if p.strip()]
    listed = os.environ.get(_RUNNER_ROOTS_FILE_VAR)
    if listed:
        try:
            with open(listed, encoding="utf-8") as f:
                for line in f:
                    line = line.split("#", 1)[0].strip()
                    if line:
                        out.append(line)
        except OSError:
            # A list that was named but cannot be read must not leave the
            # guard quietly blind to what it names.
            raise RuntimeError("test guard: %s names %r, which cannot be read"
                               % (_RUNNER_ROOTS_FILE_VAR, listed))
    return out


def _path_values(data):
    """Every string value in a settings file, nested ones included (the
    reaching tool keeps some locations in sub-sections), except the keys
    that name programs."""
    if isinstance(data, dict):
        for k, v in data.items():
            if k not in _NOT_DATA_KEYS:
                yield from _path_values(v)
    elif isinstance(data, list):
        for v in data:
            yield from _path_values(v)
    elif isinstance(data, str):
        yield data


def _is_tool_var(name):
    u = name.upper()
    return u.startswith(_TOOL_VAR_PREFIXES) or u in _SYNCED_FOLDER_VARS


def _real_live_roots():
    known = _known_folders()
    homes = [os.path.expanduser("~")] + known["home"]
    programdatas = [os.environ.get("PROGRAMDATA") or "C:\\ProgramData"] + known["programdata"]
    locals_ = [p for p in [os.environ.get("LOCALAPPDATA")] + known["localappdata"] if p]
    machine_files = [os.path.join(p, "mousedb", "config.json") for p in programdatas]
    if os.environ.get("MOUSEDB_MACHINE_CONFIG"):
        machine_files.append(os.environ["MOUSEDB_MACHINE_CONFIG"])

    found = []
    for h in homes:
        found += [os.path.join(h, ".mousedb"), os.path.join(h, ".mousereach")]
    found += [os.path.join(p, "mousedb") for p in programdatas]
    found += [os.path.join(p, "mousedb") for p in locals_]

    values = []
    for cfg in ([os.path.join(h, ".mousedb", "config.json") for h in homes]
                + machine_files
                + [os.path.join(h, ".mousereach", "config.json") for h in homes]):
        values += list(_path_values(_json_file(cfg)))
    values += [v for k, v in os.environ.items() if _is_tool_var(k)]
    values += [v for k, v in _registry_environment()
               if _is_tool_var(k) and "%" not in v]
    values += _runner_roots()
    for v in values:
        v = v.strip()
        if not v or not os.path.isabs(v):
            continue
        n = os.path.normcase(os.path.abspath(v))
        # A bare drive ("Q:\") is not a data location: it would make every
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


# --- every spelling of a folder ---------------------------------------------
#
# WHY: the guard compares strings (an audit hook must not touch the disk), and
# one folder has many spellings: upper/lower case and "/" or "\" (normcase
# folds those), a Windows short name per part ("PROGRA~1"), the server path of
# a mapped network drive ("\\server\share\..." for "Q:\..."), and what
# realpath resolves to. All of them are worked out HERE, once, when this file
# is imported, so the hook itself stays pure string work.

_MAX_SPELLINGS_PER_FOLDER = 512


def _drive_map():
    """{"q:": "\\\\server\\share"} for each mapped network drive (Windows),
    normcased."""
    out = {}
    if not _IS_WINDOWS:
        return out
    try:
        import ctypes
        from ctypes import wintypes
        mpr = ctypes.WinDLL("mpr")
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if mpr.WNetGetConnectionW("%s:" % letter, buf, ctypes.byref(size)) == 0 and buf.value:
                out[os.path.normcase(letter + ":")] = os.path.normcase(buf.value).rstrip("\\/")
    except Exception:                              # noqa: BLE001 - best effort
        pass
    return out


def _short_and_long(path):
    """The Windows short and long names of an EXISTING path (or nothing)."""
    out = []
    if not _IS_WINDOWS:
        return out
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        for fn in (k32.GetShortPathNameW, k32.GetLongPathNameW):
            buf = ctypes.create_unicode_buffer(32768)
            n = fn(path, buf, len(buf))
            if 0 < n < len(buf):
                out.append(buf.value)
    except Exception:                              # noqa: BLE001 - best effort
        pass
    return out


def _strip_long_prefix(n):
    """'\\\\?\\c:\\x' -> 'c:\\x', '\\\\?\\unc\\srv\\share' -> '\\\\srv\\share'
    (already normcased). String work only: also used by the hook."""
    if n.startswith("\\\\?\\unc\\"):
        return "\\\\" + n[8:]
    if n.startswith("\\\\?\\") or n.startswith("\\\\.\\"):
        return n[4:]
    return n


def _norm(p):
    return _strip_long_prefix(os.path.normcase(os.path.abspath(p))).rstrip("\\/")


def _split_anchor(n):
    """('c:', ['users', 'x']) or ('\\\\srv\\share', [...]) of a normcased
    absolute path."""
    drive, rest = os.path.splitdrive(n)
    return drive, [part for part in rest.replace("/", "\\").split("\\") if part]


def _spellings(path, drive_map=None, resolve=True):
    """Every spelling of ``path`` the guard should recognise, normcased, with
    no trailing separator: as given, abspath, realpath, each part long or
    short (mixed too), and the drive-letter <-> server form of a mapped
    network drive. ``drive_map`` defaults to this computer's mapped drives;
    ``resolve`` False skips the disk lookups (realpath, short names)."""
    if drive_map is None:
        drive_map = _DRIVE_MAP
    bases = {_norm(path)}
    if resolve:
        try:
            bases.add(_norm(os.path.realpath(path)))
        except (OSError, ValueError):
            pass
    unc_to_drive = {v: k for k, v in drive_map.items()}
    out = set()
    # The whole-path forms go in first, before the part-by-part mixes below,
    # which are capped: a deep folder must never lose its plain long or
    # plain short spelling to the cap.
    for base in list(bases):
        whole = {base}
        if resolve:
            whole |= {_norm(alt) for alt in _short_and_long(base)}
        for w in whole:
            drive, rest = os.path.splitdrive(w)
            for anchor in {drive, drive_map.get(drive), unc_to_drive.get(drive)} - {None}:
                out.add((anchor + rest).rstrip("\\/") or os.sep)
    for base in list(bases):
        drive, parts = _split_anchor(base)
        anchors = {drive}
        if drive in drive_map:
            anchors.add(drive_map[drive])
        if drive in unc_to_drive:
            anchors.add(unc_to_drive[drive])
        # Each part: its own spelling plus the short/long name of the folder
        # it names (looked up on disk, only when it exists).
        choices = []
        for i, part in enumerate(parts):
            names = {part}
            if resolve:
                prefix = drive + os.sep + os.sep.join(parts[:i + 1])
                for alt in _short_and_long(prefix):
                    names.add(os.path.normcase(os.path.basename(alt.rstrip("\\/"))))
            choices.append(sorted(names))
        for anchor in anchors:
            for combo in itertools.islice(itertools.product(*choices),
                                          _MAX_SPELLINGS_PER_FOLDER):
                out.add((anchor + os.sep + os.sep.join(combo)).rstrip("\\/") or os.sep)
    return out


_DRIVE_MAP = _drive_map()

LIVE_ROOTS = _real_live_roots()

# The code's own checkout: the folder that holds this tests/ folder.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _all_spellings(paths):
    out = set()
    for p in paths:
        out |= _spellings(p)
    return tuple(sorted(out))


# Every spelling of every live location, and of the folders that are code,
# not lab data: this checkout, and the Python installation running the tests
# (a live location configured above them must not make reading the standard
# library a "live read").
_ROOT_SPELLINGS = _all_spellings(LIVE_ROOTS)
_EXEMPT_SPELLINGS = _all_spellings(
    {REPO_ROOT, sys.prefix, sys.base_prefix, sys.exec_prefix})


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


class LiveFolderRefused(Exception):
    """A test tried to write into a real lab location; the write did not
    happen. Deliberately NOT an OSError: code under test that retries or
    shrugs off OSError must not quietly carry on as if the disk had failed.
    (Code that swallows every Exception still fails its test: the attempt is
    recorded too.)"""


# Audit events that touch a path (sys.addaudithook; see the Python
# documentation's "Audit events table"): which arguments are paths that are
# only READ (recorded, the test fails afterwards) and which are WRITTEN,
# CREATED or REMOVED (refused on the spot). "open" is decided by its mode
# (see _opens_for_writing). tempfile.mkstemp/mkdtemp create through os.open
# and os.mkdir, which are refused before anything exists; their own events
# come after the creation and are listed only as a backstop. os.makedirs
# creates through os.mkdir; os.replace and os.unlink raise "os.rename" and
# "os.remove".
_PATH_EVENTS = {
    "open": ((), ()),
    "os.listdir": ((0,), ()), "os.scandir": ((0,), ()), "glob.glob": ((0,), ()),
    "os.mkdir": ((), (0,)), "os.remove": ((), (0,)), "os.rmdir": ((), (0,)),
    "os.truncate": ((), (0,)), "os.utime": ((), (0,)), "os.chmod": ((), (0,)),
    "os.chown": ((), (0,)), "os.setxattr": ((), (0,)), "os.removexattr": ((), (0,)),
    "os.rename": ((), (0, 1)), "os.symlink": ((), (0, 1)), "os.link": ((), (0, 1)),
    "shutil.copyfile": ((0,), (1,)), "shutil.copymode": ((0,), (1,)),
    "shutil.copystat": ((0,), (1,)), "shutil.copytree": ((0,), (1,)),
    "shutil.rmtree": ((), (0,)), "shutil.move": ((), (0, 1)),
    "shutil.make_archive": ((), (0,)), "shutil.unpack_archive": ((0,), (1,)),
    "tempfile.mkstemp": ((), (0,)), "tempfile.mkdtemp": ((), (0,)),
    # Connecting can create the file and its journal: a write.
    "sqlite3.connect": ((), (0,)),
}

_WRITE_FLAGS = (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)


def _opens_for_writing(args):
    """The "open" event carries (path, mode, flags): built-in open() gives a
    mode string such as 'w', 'ab', 'r+'; os.open() gives mode None and the
    os.O_* flags."""
    mode = args[1] if len(args) > 1 else None
    flags = args[2] if len(args) > 2 else 0
    if isinstance(mode, str) and any(c in mode for c in "wax+"):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _longest_prefix(n, spellings):
    best = -1
    for s in spellings:
        if (n == s or n.startswith(s + os.sep)) and len(s) > best:
            best = len(s)
    return best


def _live(path):
    """The normalised path when it lies under a real location (and not in
    the code's own checkout), else None. Pure string work: an audit hook must
    not open files itself. When a path lies under both a live location and
    an exempt one, the deeper of the two decides -- so the checkout inside the
    database folder is exempt while the files beside it are not."""
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
        n = _norm(p)
    except (TypeError, ValueError):
        return None
    # LIVE_ROOTS is read here as well as its precomputed spellings, so a
    # location added to it later (as the guard's own tests do) counts too.
    live = _longest_prefix(n, _ROOT_SPELLINGS + tuple(LIVE_ROOTS))
    if live < 0:
        return None
    if _longest_prefix(n, _EXEMPT_SPELLINGS) > live:
        return None
    return n


def _refuse(how, hit):
    if _GUARD["on"]:
        _GUARD["hits"].append("REFUSED %s %s" % (how, hit))
    raise LiveFolderRefused(
        "test guard: refused %s %s -- a test must never write into this "
        "computer's live folders (use tmp_path)" % (how, hit))


def _audit(event, args):
    spec = _PATH_EVENTS.get(event)
    if spec is None:
        return
    reads, writes = spec
    if event == "open":
        if _opens_for_writing(args):
            writes = (0,)
        else:
            reads = (0,)
    # A write is refused whether or not a test is running: nothing in the
    # suite (collection, fixtures, teardown) may write there either.
    for i in writes:
        if i < len(args):
            hit = _live(args[i])
            if hit:
                how = event
                if event == "open":
                    how = "open(%r)" % (args[1] if args[1] is not None else args[2],)
                _refuse(how, hit)
    if not _GUARD["on"]:
        return
    for i in reads:
        if i < len(args):
            hit = _live(args[i])
            if hit:
                how = event
                if event == "open":
                    how = "open(%r)" % (args[1] if args[1] is not None else args[2],)
                _GUARD["hits"].append("read %s %s" % (how, hit))


sys.addaudithook(_audit)


def _watch_native_file_calls():
    """Parquet files are opened by Arrow's C++ code, which raises no Python
    audit event, so the calls that read and write them are watched by name:
    the path each is given goes through the same check, and a write is
    refused before Arrow is called."""
    import functools

    def watched(fn, label, writes):
        @functools.wraps(fn)
        def call(*args, **kwargs):
            for a in list(args[:2]) + [kwargs.get(k) for k in ("path", "source", "where")]:
                if isinstance(a, (str, os.PathLike)):
                    hit = _live(a)
                    if hit:
                        if writes:
                            _refuse(label, hit)
                        elif _GUARD["on"]:
                            _GUARD["hits"].append("read %s %s" % (label, hit))
            return fn(*args, **kwargs)
        return call

    try:
        import pandas as pd
        pd.read_parquet = watched(pd.read_parquet, "pandas.read_parquet", False)
        pd.DataFrame.to_parquet = watched(pd.DataFrame.to_parquet, "DataFrame.to_parquet", True)
    except ImportError:
        pass
    try:
        import pyarrow.parquet as pq
        pq.read_table = watched(pq.read_table, "pyarrow.read_table", False)
        pq.write_table = watched(pq.write_table, "pyarrow.write_table", True)
        pq.ParquetFile.__init__ = watched(pq.ParquetFile.__init__, "pyarrow.ParquetFile", False)
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
                    "synthetic inputs under tmp_path; REFUSED = a write the guard "
                    "stopped, read = a read it recorded):\n  " + "\n  ".join(hits[:20]),
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
    def guarded(self, name="central-db", stale_minutes=45, base_dir=None, **kw):
        if base_dir is None:
            pytest.fail(
                "test constructed TaskMutex with the PRODUCTION lock dir; "
                "pass base_dir=tmp_path (or stub task_mutex.hold)")
        _orig_init(self, name=name, stale_minutes=stale_minutes,
                   base_dir=base_dir, **kw)
    monkeypatch.setattr(TaskMutex, "__init__", guarded)
