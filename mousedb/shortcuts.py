"""Desktop and Start-menu shortcuts for MouseDB, so nobody needs a terminal.

WHAT IT MAKES
-------------
Two shortcuts, each in two places:

* **MouseDB** -- the MouseDB window (tracking sheets, Where Is My Data, data
  entry). Runs ``pythonw -m mousedb.gui``.
* **MouseDB - Browse the data** -- the read-only data browser. Runs
  ``pythonw -m mousedb.data_browser``.

on the Desktop, and in a "MouseDB" folder of the Start menu.

FOR WHOM
--------
``mousedb-make-shortcuts``                for you only (your Desktop, your
                                          Start menu); no special rights needed.
``mousedb-make-shortcuts --all-users``    for EVERYONE who logs in to this
                                          computer (the Public Desktop and the
                                          all-users Start menu). Windows allows
                                          that only to an administrator: run it
                                          from an administrator PowerShell.
``mousedb-make-shortcuts --folder DIR``   both shortcuts into one existing
                                          folder, e.g. another person's Desktop
                                          that you are allowed to write to.

The same thing without the console script: ``python -m mousedb.shortcuts``.

WHY THIS WAY
------------
* ``pythonw`` (no console window) of the environment mousedb is installed in,
  ``-m <module>``: never a ``Scripts\\*.exe`` shim, because a shim held open by
  a running window blocks ``pip install`` of the package (a reviewer's open
  window once stopped a deploy). Nothing is copied, so a shortcut keeps working
  after the code is updated.
* The icons are Windows' own (imageres.dll), present on every Windows computer,
  so the tool ships no image files.
* Working folder: the person's home folder (``%USERPROFILE%``), so an
  all-users shortcut never starts in one particular person's folder.
* Running it again replaces the shortcuts in place; it never creates a Desktop
  or a profile folder that is not already there.
* A shortcut is only useful if mousedb knows where things are for the person
  who clicks it: on a shared computer set the locations once in the machine
  settings file (``mousedb config --machine --set ...``, see mousedb.config).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

START_MENU_FOLDER = "MouseDB"


class ShortcutError(RuntimeError):
    """A shortcut could not be made. The message says why, in words."""


@dataclass(frozen=True)
class Shortcut:
    name: str            # file name without .lnk; what people see
    module: str          # run as: pythonw -m <module>
    description: str     # the tooltip
    icon_index: int      # icon number inside imageres.dll

    @property
    def filename(self) -> str:
        return self.name + ".lnk"


# Icon numbers in %SystemRoot%\System32\imageres.dll: 76 a checklist (records,
# data entry), 13 a page under a magnifying glass (looking through data).
GUI = Shortcut("MouseDB", "mousedb.gui",
               "Open MouseDB: tracking sheets, Where Is My Data, data entry", 76)
BROWSER = Shortcut("MouseDB - Browse the data", "mousedb.data_browser",
                   "Browse what mousedb holds (read-only copy of the snapshot)", 13)
SHORTCUTS = (GUI, BROWSER)


def pythonw() -> Path:
    """The window-less Python of this environment (no console window flashes)."""
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return w if w.is_file() else exe


def icon_location(shortcut: Shortcut) -> str:
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    return "%s,%d" % (Path(root) / "System32" / "imageres.dll", shortcut.icon_index)


def _shell_folder(name: str) -> Optional[Path]:
    """A Windows special folder by its .NET name (Desktop, Programs,
    CommonDesktopDirectory, CommonPrograms). Asked of Windows because these
    can be redirected (e.g. a Desktop inside OneDrive)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Environment]::GetFolderPath('%s')" % name],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        p = out.stdout.strip()
        if out.returncode == 0 and p:
            return Path(p)
    except Exception:
        pass
    return None


def desktop_dir() -> Path:
    return _shell_folder("Desktop") or (Path.home() / "Desktop")


def target_folders(all_users: bool = False, desktop: bool = True,
                   start_menu: bool = True) -> List[Path]:
    """Where the shortcuts go: Desktop and/or Start menu, yours or everyone's."""
    out = []
    if desktop:
        d = _shell_folder("CommonDesktopDirectory" if all_users else "Desktop")
        if d is None:
            raise ShortcutError("Windows did not say where the %s Desktop is."
                                % ("all-users" if all_users else "your"))
        out.append(d)
    if start_menu:
        p = _shell_folder("CommonPrograms" if all_users else "Programs")
        if p is None:
            raise ShortcutError("Windows did not say where the %s Start menu is."
                                % ("all-users" if all_users else "your"))
        out.append(p / START_MENU_FOLDER)
    return out


def _check_writable(folder: Path, may_create: bool) -> None:
    """Fail early, in words, instead of half-making shortcuts."""
    if not folder.is_dir():
        if not may_create:
            raise ShortcutError("%s does not exist; nothing was made there." % folder)
        try:
            folder.mkdir()
        except PermissionError:
            raise ShortcutError(
                "Windows refused to create %s. Shortcuts for everyone on this computer "
                "can only be made by an administrator: run this again from an "
                "administrator PowerShell." % folder) from None
        except OSError as e:
            raise ShortcutError("%s could not be created: %s" % (folder, e)) from None
    probe = folder / ".mousedb-write-test.tmp"
    try:
        probe.write_text("x", encoding="ascii")
        probe.unlink()
    except PermissionError:
        raise ShortcutError(
            "Windows refused to write into %s. Shortcuts for everyone on this computer "
            "can only be made by an administrator: run this again from an administrator "
            "PowerShell (or make them for yourself without --all-users)." % folder) from None
    except OSError as e:
        raise ShortcutError("%s cannot be written to: %s" % (folder, e)) from None


def write_shortcut(shortcut: Shortcut, folder: Path) -> Path:
    """Write (or replace) one .lnk in an existing folder; returns its path."""
    if sys.platform != "win32":
        raise ShortcutError("Shortcuts are made on Windows only.")
    link = Path(folder) / shortcut.filename
    env = dict(os.environ, MDB_LNK=str(link), MDB_TGT=str(pythonw()),
               MDB_ARGS="-m %s" % shortcut.module, MDB_DESC=shortcut.description,
               MDB_ICON=icon_location(shortcut))
    # %USERPROFILE% stays unexpanded in the shortcut, so each person who
    # clicks it starts in their own home folder.
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:MDB_LNK); "
              "$s.TargetPath = $env:MDB_TGT; $s.Arguments = $env:MDB_ARGS; "
              "$s.WorkingDirectory = '%USERPROFILE%'; $s.Description = $env:MDB_DESC; "
              "$s.IconLocation = $env:MDB_ICON; $s.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       env=env, capture_output=True, text=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0 or not link.is_file():
        raise ShortcutError("The shortcut %s could not be made (%s)."
                            % (link, r.stderr.strip() or "no reason given"))
    return link


def make_shortcuts(all_users: bool = False, desktop: bool = True, start_menu: bool = True,
                   folder: Optional[Path] = None) -> List[Path]:
    """Make both shortcuts in every chosen place. Every place is checked
    before anything is written, so a refusal leaves nothing half-made."""
    if sys.platform != "win32":
        raise ShortcutError("Shortcuts are made on Windows only.")
    if folder is not None:
        places: List[Tuple[Path, bool]] = [(Path(folder), False)]
    else:
        places = [(p, p.name == START_MENU_FOLDER)
                  for p in target_folders(all_users, desktop, start_menu)]
    for p, may_create in places:
        _check_writable(p, may_create)
    return [write_shortcut(s, p) for p, _ in places for s in SHORTCUTS]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="mousedb-make-shortcuts",
        description="Put 'MouseDB' and 'MouseDB - Browse the data' on the Desktop and "
                    "in the Start menu, so MouseDB opens with a double-click.")
    ap.add_argument("--all-users", action="store_true",
                    help="For everyone who logs in to this computer (Public Desktop, "
                         "all-users Start menu). Needs an administrator PowerShell.")
    ap.add_argument("--folder", type=Path, default=None,
                    help="Put both shortcuts into this existing folder instead.")
    ap.add_argument("--no-desktop", action="store_true", help="Start menu only.")
    ap.add_argument("--no-start-menu", action="store_true", help="Desktop only.")
    args = ap.parse_args(argv)
    try:
        made = make_shortcuts(all_users=args.all_users, desktop=not args.no_desktop,
                              start_menu=not args.no_start_menu, folder=args.folder)
    except ShortcutError as e:
        print("[FAIL] %s" % e)
        return 1
    for p in made:
        print("[OK] %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
