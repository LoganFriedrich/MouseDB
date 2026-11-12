"""Open the MouseDB window: ``pythonw -m mousedb.gui``.

This is what the "MouseDB" desktop and Start-menu shortcuts run (see
mousedb.shortcuts). It does the same as ``mousedb-entry`` / ``mousedb entry``,
with two differences that matter when it is started by a double-click:

* It is started with ``pythonw`` (no console window), never through the
  ``Scripts\\mousedb-entry.exe`` shim: a shim held open by a running window
  blocks ``pip install`` of the package, which is how a reviewer's open window
  once stopped a deploy.
* With no console, a failure would otherwise vanish without a word. So
  anything the window prints goes to a log file, and a failure to start is
  shown in a message box that says what went wrong and where the log is.
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

TITLE = "MouseDB"


def log_file() -> Path:
    """Per person: %LOCALAPPDATA%\\mousedb\\gui.log (~/.mousedb/gui.log elsewhere)."""
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) / "mousedb" if local else Path.home() / ".mousedb"
    return root / "gui.log"


def _redirect_output_if_windowless() -> None:
    if sys.stdout is None or sys.stderr is None:
        try:
            f = log_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            out = open(f, "a", encoding="utf-8", buffering=1)
        except OSError:
            out = open(os.devnull, "w", encoding="utf-8")
        sys.stdout = sys.stdout or out
        sys.stderr = sys.stderr or out


def _say(message: str) -> None:
    """Tell the person, in a window (there is no console to print to)."""
    try:
        from qtpy.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
        QMessageBox.critical(None, TITLE, message)
    except Exception:
        print(message)


def main() -> int:
    _redirect_output_if_windowless()
    try:
        from .app import main as gui_main
        gui_main()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0
    except Exception as e:
        traceback.print_exc()
        _say("MouseDB could not open.\n\n%s: %s\n\nThe details are in %s. If this "
             "keeps happening, send that file to whoever looks after mousedb."
             % (type(e).__name__, e, log_file()))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
