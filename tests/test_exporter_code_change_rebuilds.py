"""A change to the exporter's code rebuilds the per-reach files.

WHY: the per-reach files are kept when nothing they are built from has
changed. The exporter's own code was represented by a number bumped by hand;
a change made without bumping it was skipped, and the previous files (and
the previous run's record of them) kept looking current. And the Where Is My
Data button ran the exporter inside a window that may have been opened
before new code was installed, so it could write with the old code.
"""
import os
from types import SimpleNamespace

from mousedb.exporters import current
from tests.test_export_skip_never_hides_a_change import world  # noqa: F401  (fixture)


def _package(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub" / "writer.py").write_text("COLUMNS = ['a', 'b']\n", encoding="utf-8")
    return pkg


def test_a_code_change_alone_changes_the_signature(world, tmp_path, monkeypatch):  # noqa: F811
    pkg = _package(tmp_path)
    monkeypatch.setattr(current, "_package_root", lambda: pkg, raising=False)
    before = current.odc_reaches_signature(world["snap"])
    # The exporter now writes one more column; no input changed.
    (pkg / "sub" / "writer.py").write_text("COLUMNS = ['a', 'b', 'c']\n", encoding="utf-8")
    after = current.odc_reaches_signature(world["snap"])
    assert before != after


def test_the_same_code_signs_the_same_whatever_its_line_endings(tmp_path):
    pkg = _package(tmp_path)
    f = pkg / "sub" / "writer.py"
    f.write_bytes(b"COLUMNS = ['a', 'b']\n")
    lf = current.exporter_code_hash(pkg)
    f.write_bytes(b"COLUMNS = ['a', 'b']\r\n")
    assert current.exporter_code_hash(pkg) == lf
    (pkg / "sub" / "__pycache__").mkdir()
    (pkg / "sub" / "__pycache__" / "writer.cpython-310.pyc").write_bytes(b"compiled")
    assert current.exporter_code_hash(pkg) == lf


def test_the_button_runs_the_installed_code_as_a_fresh_program(monkeypatch):
    """The button must not call the exporter inside the window."""
    import subprocess
    from mousedb.exporters import current as current_mod
    from mousedb.gui import data_status_tab as tab_mod

    in_window, started = [], []
    monkeypatch.setattr(current_mod, "refresh_current",
                        lambda *a, **k: in_window.append(1) or {"files": {}, "problems": []})

    seen_kw = []

    def fake_run(cmd, **kw):
        started.append(cmd)
        seen_kw.append(kw)
        return SimpleNamespace(returncode=0, stdout=b"complete for ODC upload: True")
    monkeypatch.setattr(subprocess, "run", fake_run)

    jobs = []
    stub = SimpleNamespace(exports=SimpleNamespace(append=lambda s: None),
                           _on_exports_done=lambda r: None, _busy=lambda: False,
                           _run=lambda fn, on_done: jobs.append(fn) or True)
    tab_mod.DataStatusTab.refresh_exports(stub)
    assert jobs, "the button started nothing"
    jobs[0]()
    assert not in_window, "the exporter ran inside the window, with the code it was opened with"
    assert started and started[0][1:3] == ["-m", "mousedb.exporters.current"]
    assert "--no-wait" in started[0]
    # Started in a neutral folder: with -m, the working directory comes first
    # on the import path, so the window's folder (perhaps a source checkout
    # holding a mousedb/ tree) would decide which code runs.
    import os
    from pathlib import Path
    cwd = seen_kw[0].get("cwd")
    assert cwd is not None, "the program inherits the window's working directory"
    assert Path(cwd) != Path(os.getcwd())
    assert not (Path(cwd) / "mousedb" / "__init__.py").exists()


def test_a_busy_exporter_program_is_reported_as_busy(monkeypatch):
    import subprocess
    from mousedb.gui import data_status_tab as tab_mod
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: SimpleNamespace(
        returncode=current.EXIT_BUSY, stdout=b"[!] already being written"))
    r = tab_mod.run_exports_program()
    assert r["busy"] is True and r["returncode"] == current.EXIT_BUSY


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
