"""MouseDB opens with a double-click: the shortcuts mousedb-make-shortcuts makes.

Lab members who have never used a terminal must be able to find and open
MouseDB, so the shortcuts are the product here. What must hold:

* each runs pythonw -m <module> of this environment, never an exe shim (an
  open shim blocks pip reinstalls);
* the data browser's own "Put a shortcut on my desktop" button makes the very
  same shortcut, so the two can never drift apart;
* a place that cannot be written is refused in words BEFORE anything is
  written, and a folder that does not exist is never created (it could be
  someone's profile).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from mousedb import shortcuts

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows shortcuts")


def _read_lnk(path: Path) -> dict:
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut('%s'); "
              "@{t=$s.TargetPath; a=$s.Arguments; i=$s.IconLocation; w=$s.WorkingDirectory}"
              " | ConvertTo-Json" % str(path).replace("'", "''"))
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=60)
    return json.loads(out.stdout)


def test_both_shortcuts_run_a_module_of_this_environment(tmp_path):
    made = shortcuts.make_shortcuts(folder=tmp_path)
    assert sorted(p.name for p in made) == ["MouseDB - Browse the data.lnk", "MouseDB.lnk"]
    for s in shortcuts.SHORTCUTS:
        lnk = _read_lnk(tmp_path / s.filename)
        assert Path(lnk["t"]).name.lower() in ("pythonw.exe", "python.exe")
        assert Path(lnk["t"]).parent == Path(sys.executable).parent
        assert lnk["a"] == "-m %s" % s.module
        assert lnk["i"].lower().endswith("imageres.dll,%d" % s.icon_index)


def test_the_browser_button_makes_the_same_shortcut(tmp_path):
    from mousedb.data_browser import launch
    assert launch.SHORTCUT_NAME == shortcuts.BROWSER.filename
    assert launch.make_desktop_shortcut(desktop=tmp_path) == tmp_path / launch.SHORTCUT_NAME


def test_a_folder_that_is_not_there_is_refused_not_created(tmp_path):
    missing = tmp_path / "someone" / "Desktop"
    with pytest.raises(shortcuts.ShortcutError, match="does not exist"):
        shortcuts.make_shortcuts(folder=missing)
    assert not missing.exists() and not missing.parent.exists()


def test_every_module_a_shortcut_runs_exists():
    import importlib.util
    for s in shortcuts.SHORTCUTS:
        assert importlib.util.find_spec(s.module + ".__main__"), s.module
