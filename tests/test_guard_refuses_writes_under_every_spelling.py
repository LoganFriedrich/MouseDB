"""The suite's live-folder guard (tests/conftest.py) REFUSES a write into a
real lab location, whatever the location is called, finds the real
locations even when the runner has hidden this computer's configuration, and
leaves the code's own checkout alone even when the checkout sits inside a
live location.

WHY each half matters:
  * a guard that only reports lets the write happen: the test fails, but the
    lab's file is already changed;
  * one folder has many spellings (upper/lower case, "/" or "\\", Windows
    short names such as PROGRA~1, the server path of a mapped drive), and a
    guard that compares one spelling misses the others;
  * the runner usually points HOME, PROGRAMDATA and friends at a scratch
    folder before the suite starts, which would leave a guard that reads
    only the environment with nothing to guard;
  * the lab keeps its working copy of the code inside the database folder:
    a guard that treats the checkout as lab data fails every test run from
    there, and a real hit drowns among the false ones.

Every location here is an invented folder under tmp_path.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests import conftest as suite


def _short_name(path):
    """Windows short (8.3) spelling of an existing path, or None when the
    volume does not make short names."""
    if os.name != "nt":
        return None
    import ctypes
    buf = ctypes.create_unicode_buffer(32768)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))
    if 0 < n < len(buf) and buf.value.lower() != str(path).lower():
        return buf.value
    return None


def _spellings_of(path):
    """What the guard is told about a pretend live folder: every spelling
    the guard itself would work out (or, without that ability, the plain
    one)."""
    spell = getattr(suite, "_spellings", None)
    if spell is not None:
        return tuple(sorted(spell(str(path))))
    return (os.path.normcase(os.path.abspath(str(path))),)


@pytest.fixture
def pretend_live(tmp_path, monkeypatch):
    """A folder the guard treats as live for one test (long name, so it has a
    distinct short name), with one existing file. What the test did there is
    taken off the guard's list before the test ends, so only the assertions
    decide the outcome."""
    live = tmp_path / "pretend_live_location"
    live.mkdir()
    (live / "existing_record.txt").write_text("keep me")
    monkeypatch.setattr(suite, "LIVE_ROOTS", suite.LIVE_ROOTS + _spellings_of(live))
    yield live
    suite._GUARD["hits"].clear()


def _spelled(live, how):
    if how == "as_given":
        return str(live)
    if how == "upper_case_forward_slashes":
        return str(live).upper().replace("\\", "/")
    if how == "short_name":
        short = _short_name(live)
        if short is None:
            pytest.skip("this volume makes no short names")
        return short
    raise AssertionError(how)


def _write(op, base, outside):
    """One kind of write into ``base`` (a spelling of the live folder)."""
    j = os.path.join
    if op == "open_w":
        with open(j(base, "new.csv"), "w") as f:
            f.write("x")
    elif op == "open_append":
        with open(j(base, "existing_record.txt"), "a") as f:
            f.write("changed")
    elif op == "os_open_create":
        os.close(os.open(j(base, "new.log"), os.O_CREAT | os.O_WRONLY))
    elif op == "mkdir":
        os.mkdir(j(base, "new_folder"))
    elif op == "makedirs":
        os.makedirs(j(base, "a_folder", "b_folder"))
    elif op == "mkstemp":
        fd, _ = tempfile.mkstemp(dir=base)
        os.close(fd)
    elif op == "mkdtemp":
        tempfile.mkdtemp(dir=base)
    elif op == "copy_onto":
        shutil.copy(outside, j(base, "copied.csv"))
    elif op == "replace_onto":
        os.replace(outside, j(base, "existing_record.txt"))
    elif op == "move_out":
        os.rename(j(base, "existing_record.txt"), outside + ".moved")
    elif op == "remove":
        os.remove(j(base, "existing_record.txt"))
    elif op == "sqlite_connect":
        sqlite3.connect(j(base, "new.db")).execute("create table t (a)")
    elif op == "parquet":
        import pandas as pd
        pd.DataFrame({"a": [1]}).to_parquet(j(base, "t.parquet"))
    else:
        raise AssertionError(op)


WRITES = ["open_w", "open_append", "os_open_create", "mkdir", "makedirs", "mkstemp",
          "mkdtemp", "copy_onto", "replace_onto", "move_out", "remove",
          "sqlite_connect", "parquet"]


def _unchanged(live):
    assert sorted(os.listdir(live)) == ["existing_record.txt"]
    assert (live / "existing_record.txt").read_text() == "keep me"


@pytest.mark.parametrize("how", ["as_given", "upper_case_forward_slashes", "short_name"])
@pytest.mark.parametrize("op", WRITES)
def test_a_write_is_refused_and_nothing_is_written(pretend_live, tmp_path, op, how):
    base = _spelled(pretend_live, how)
    outside = tmp_path / "outside_source.csv"
    outside.write_text("from outside")
    with pytest.raises(Exception) as raised:
        _write(op, base, str(outside))
    assert "refused" in str(raised.value), raised.value
    _unchanged(pretend_live)


def test_a_swallowed_refusal_still_fails_the_test(pretend_live):
    try:
        with open(pretend_live / "new.csv", "w") as f:
            f.write("x")
    except Exception:                              # noqa: BLE001 - the point of the test
        pass
    assert not (pretend_live / "new.csv").exists()
    assert any("REFUSED" in h for h in suite._GUARD["hits"]), suite._GUARD["hits"]


def test_the_server_form_of_a_mapped_drive_is_the_same_folder():
    """A folder on a mapped network drive is the same folder under its
    server path, and the other way round. (The drive map is invented: the
    real one is read from Windows when the guard starts.)"""
    spell = getattr(suite, "_spellings",
                    lambda p, **kw: {os.path.normcase(os.path.abspath(p))})
    fake = {os.path.normcase("q:"): os.path.normcase("\\\\fileserver\\labshare")}
    by_letter = spell("Q:\\Lab_Data\\Results", drive_map=fake, resolve=False)
    by_server = spell("\\\\FileServer\\LabShare\\Lab_Data\\Results",
                      drive_map=fake, resolve=False)
    want_server = os.path.normcase("\\\\fileserver\\labshare\\lab_data\\results")
    want_letter = os.path.normcase("q:\\lab_data\\results")
    assert want_server in by_letter
    assert want_letter in by_server


def test_the_checkout_is_code_and_the_folders_beside_it_stay_guarded(monkeypatch):
    """With the folder that holds the checkout made live (as the lab's
    database folder is), the checkout's files are not lab data -- under any
    spelling -- while a folder or file beside it still is."""
    repo = Path(suite.__file__).resolve().parent.parent
    parent = repo.parent
    monkeypatch.setattr(suite, "LIVE_ROOTS", suite.LIVE_ROOTS + _spellings_of(parent))
    assert suite._live(str(repo / "mousedb" / "config.py")) is None
    assert suite._live(str(repo / "tests" / "__pycache__" / "x.pyc")) is None
    assert suite._live(str(repo).upper().replace("\\", "/") + "/README.md") is None
    short = _short_name(repo)
    if short:
        assert suite._live(short + os.sep + "README.md") is None
    assert suite._live(str(parent / "exports" / "a.csv")) is not None
    assert suite._live(str(parent / "central.db")) is not None
    assert suite._live(str(repo) + "_old_copy" + os.sep + "x.py") is not None


def test_real_locations_are_found_with_the_environment_hidden(tmp_path, monkeypatch):
    """The runner hid HOME, PROGRAMDATA and every tool variable; the guard
    still finds the locations in the real home's and the real machine's
    settings files (nested sections too), in the saved environment variables
    (any case) and in the runner's own list."""
    real_home = tmp_path / "real_home_folder"
    real_pd = tmp_path / "real_programdata_folder"
    (real_pd / "mousedb").mkdir(parents=True)
    (real_pd / "mousedb" / "config.json").write_text(
        json.dumps({"mousedb_root": str(tmp_path / "decoy_machine_root")}))
    (real_home / ".mousereach").mkdir(parents=True)
    (real_home / ".mousereach" / "config.json").write_text(
        json.dumps({"watcher": {"db_path": str(tmp_path / "decoy_nested" / "w.db")}}))
    monkeypatch.setattr(suite, "_known_folders", lambda: {
        "home": [str(real_home)], "programdata": [str(real_pd)], "localappdata": []},
        raising=False)
    monkeypatch.setattr(suite, "_registry_environment", lambda: [
        ("MouseReach_Processing_Root", str(tmp_path / "decoy_saved_variable")),
        ("UNRELATED_SETTING", str(tmp_path / "not_a_location"))], raising=False)
    listed = tmp_path / "runner_list.txt"
    listed.write_text("# the runner's list\n%s\n" % (tmp_path / "decoy_runner_file"))
    monkeypatch.setenv("TEST_GUARD_REAL_ROOTS_FILE", str(listed))
    monkeypatch.setenv("TEST_GUARD_REAL_ROOTS", str(tmp_path / "decoy_runner_variable"))

    roots = suite._real_live_roots()

    def n(p):
        return os.path.normcase(os.path.abspath(str(p)))
    for want in ("decoy_machine_root", "decoy_saved_variable", "decoy_runner_file",
                 "decoy_runner_variable"):
        assert n(tmp_path / want) in roots, want
    assert n(tmp_path / "decoy_nested" / "w.db") in roots
    assert n(real_home / ".mousedb") in roots
    assert n(tmp_path / "not_a_location") not in roots


def test_pytest_keeps_no_cache_in_the_checkout():
    """pytest's cache plugin would make .pytest_cache / pytest-cache-files-*
    folders in the checkout; the repo's pytest settings switch it off."""
    text = (Path(suite.__file__).resolve().parent.parent / "pyproject.toml").read_text(
        encoding="utf-8")
    section = text.split("[tool.pytest.ini_options]", 1)[1].split("\n[", 1)[0]
    addopts = [ln for ln in section.splitlines() if ln.strip().startswith("addopts")]
    assert addopts and "-p no:cacheprovider" in addopts[0]


# ---------------------------------------------------------------------------
# The whole arrangement, end to end: a checkout placed INSIDE a decoy database
# folder, with decoy exports, logs and a database beside it, run as its own
# pytest process.
# ---------------------------------------------------------------------------

_INNER_TESTS = r'''
import json, os, shutil, sqlite3, tempfile
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
T = json.load(open(os.path.join(HERE, "decoy_targets.json"), encoding="utf-8"))


def test_the_checkout_is_code():
    d = os.path.join(HERE, "scratch_in_checkout")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "x.txt")
    with open(p, "w") as f:
        f.write("x")
    with open(p) as f:
        assert f.read() == "x"
    os.listdir(HERE)
    os.remove(p)
    os.rmdir(d)


def _op(op, root):
    j = os.path.join
    exports, logs, db = j(root, "exports_decoy_folder"), j(root, "logs_decoy_folder"), j(root, "central_decoy.db")
    src = j(tempfile.gettempdir(), "inner_source_%s.csv" % os.getpid())
    with open(src, "w") as f:
        f.write("from outside")
    if op == "open_w":
        open(j(exports, "new.csv"), "w").close()
    elif op == "os_open_create":
        os.close(os.open(j(logs, "new.log"), os.O_CREAT | os.O_WRONLY))
    elif op == "mkdir":
        os.mkdir(j(logs, "new_folder"))
    elif op == "makedirs":
        os.makedirs(j(exports, "a_folder", "b_folder"))
    elif op == "mkstemp":
        os.close(tempfile.mkstemp(dir=logs)[0])
    elif op == "mkdtemp":
        tempfile.mkdtemp(dir=exports)
    elif op == "copy_onto":
        shutil.copy(src, j(exports, "copied.csv"))
    elif op == "replace_onto":
        os.replace(src, j(exports, "existing.csv"))
    elif op == "remove":
        os.remove(j(exports, "existing.csv"))
    elif op == "sqlite_connect":
        con = sqlite3.connect(db)
        con.execute("create table added (a)")
        con.commit()
        con.close()
    elif op == "parquet":
        import pandas as pd
        pd.DataFrame({"a": [1]}).to_parquet(j(logs, "t.parquet"))


@pytest.mark.parametrize("root", T["spellings"])
@pytest.mark.parametrize("op", T["ops"])
def test_decoy_write(op, root):
    try:
        _op(op, root)
    except Exception as e:
        print("RAISED %s: %s" % (type(e).__name__, e))
'''

_INNER_OPS = ["open_w", "os_open_create", "mkdir", "makedirs", "mkstemp", "mkdtemp",
              "copy_onto", "replace_onto", "remove", "sqlite_connect", "parquet"]


def _snapshot(root, skip):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.abspath(dirpath).startswith(os.path.abspath(skip)):
            continue
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            if os.path.abspath(p).startswith(os.path.abspath(skip)):
                continue
            out[os.path.relpath(p, root)] = (
                None if os.path.isdir(p) else Path(p).read_bytes())
    return out


@pytest.mark.parametrize("named_by", ["machine_settings_file", "runner_list"])
def test_a_checkout_inside_the_database_folder_runs_clean_and_its_neighbours_are_refused(
        tmp_path, named_by):
    storage = tmp_path / "decoy_storage_drive"
    db_root = storage / "Databases_decoy_root"
    checkout = db_root / "code_checkout_copy"
    (checkout / "tests").mkdir(parents=True)
    (db_root / "exports_decoy_folder").mkdir()
    (db_root / "exports_decoy_folder" / "existing.csv").write_text("a,b\n1,2\n")
    (db_root / "logs_decoy_folder").mkdir()
    con = sqlite3.connect(str(db_root / "central_decoy.db"))
    con.execute("create table kept (a)")
    con.commit()
    con.close()

    shutil.copy(suite.__file__, checkout / "tests" / "conftest.py")
    (checkout / "tests" / "__init__.py").write_text("")
    (checkout / "tests" / "test_decoy_layout.py").write_text(_INNER_TESTS)
    (checkout / "pytest.ini").write_text("[pytest]\n")

    long_root = str(db_root)
    spellings = [long_root, long_root.upper().replace("\\", "/")]
    short = _short_name(db_root)
    if short:
        spellings.append(short)
    (checkout / "tests" / "decoy_targets.json").write_text(
        json.dumps({"spellings": spellings, "ops": _INNER_OPS}))

    programdata = tmp_path / "decoy_programdata"
    (programdata / "mousedb").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("MOUSEDB_", "MOUSEREACH_", "MOUSEBRAIN_",
                                        "CONNECTOME_ROOT", "TEST_GUARD_REAL_ROOTS"))}
    env["PROGRAMDATA"] = str(programdata)
    if named_by == "machine_settings_file":
        (programdata / "mousedb" / "config.json").write_text(
            json.dumps({"mousedb_root": long_root}))
    else:
        listed = tmp_path / "runner_roots.txt"
        listed.write_text(long_root + "\n")
        env["TEST_GUARD_REAL_ROOTS_FILE"] = str(listed)
    inner_tmp = tmp_path / "inner_tmp"
    inner_tmp.mkdir()
    env["TEMP"] = env["TMP"] = str(inner_tmp)
    import mousedb
    env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(mousedb.__file__)))
    env.pop("PYTHONPYCACHEPREFIX", None)          # the checkout's own __pycache__ is written

    before = _snapshot(db_root, checkout)
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rA", "-q",
         "--rootdir", str(checkout), "-c", str(checkout / "pytest.ini"),
         str(checkout / "tests" / "test_decoy_layout.py")],
        cwd=str(checkout), env=env, capture_output=True, text=True, timeout=600)
    out = run.stdout + run.stderr

    # The checkout's own files are code: writing, reading and listing them
    # neither raises nor fails the test at its end (pytest would list that
    # as ERROR beside the PASSED of the test body).
    assert "PASSED tests/test_decoy_layout.py::test_the_checkout_is_code" in out, out[-6000:]
    assert "ERROR tests/test_decoy_layout.py::test_the_checkout_is_code" not in out, out[-6000:]
    # Nothing beside the checkout changed: no file, folder or table added,
    # removed or rewritten, under any spelling.
    assert _snapshot(db_root, checkout) == before, out[-6000:]
    # Every write was refused, not merely let through: each one raised the
    # guard's refusal, and each such test then failed at its end (pytest lists
    # it as ERROR) because the attempt was recorded.
    n_writes = len(_INNER_OPS) * len(spellings)
    for op in _INNER_OPS:
        for root in spellings:
            # pytest shows each backslash in a test name doubled.
            shown = root.encode("unicode_escape").decode("ascii")
            test_id = "tests/test_decoy_layout.py::test_decoy_write[%s-%s]" % (op, shown)
            assert "ERROR " + test_id in out, (test_id, out[-6000:])
    assert out.count("RAISED LiveFolderRefused") >= n_writes, out[-6000:]
    assert "RAISED " not in out.replace("RAISED LiveFolderRefused", ""), out[-6000:]
    if os.name == "nt":
        assert short, "expected a short-name spelling to test on this volume"
