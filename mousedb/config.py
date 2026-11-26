"""Where this machine keeps things -- the ONLY place lab paths are allowed.

WHY THIS MODULE EXISTS
----------------------
mousedb is an optional integrator: it reads the outputs of the lab's tools
(MouseReach, MouseBrain) and holds the colony/experiment records. Every one
of those locations is a property of ONE lab's machines, not of the tool, so
none of them may be written into the source (this is a public repository).
Per-module drive-letter defaults would give a new machine, a moved share, or
another lab silent wrong answers, so every location is read from here.

Where values come from, in order:
  1. an environment variable (per key, listed below) -- for one-off runs,
  2. ``~/.mousedb/config.json`` -- this person's own settings; set with
        mousedb config --set <key> <value>
     and inspect with ``mousedb config --show``,
  3. the MACHINE settings file, shared by everyone who logs in to this
     computer: ``%ProgramData%\\mousedb\\config.json`` on Windows,
     ``/etc/mousedb/config.json`` elsewhere (``MOUSEDB_MACHINE_CONFIG`` names
     another file). Set with ``mousedb config --machine --set <key> <value>``.
     WHY it exists: the locations are a property of the COMPUTER, not of the
     person. With only a per-person file, every new login on a shared
     computer met "mousedb does not know where the snapshot is" the first
     time they double-clicked a MouseDB shortcut, and had to be talked
     through a terminal command -- the thing the shortcuts exist to avoid.
     A person's own file still wins, key by key, so one person can point
     somewhere else without changing it for everyone.
  4. for the MouseReach pipeline root only: MouseReach's own
     ``~/.mousereach/config.json`` (``nas_root``), because on a machine that
     runs MouseReach that file already says where the pipeline is.

There is NO built-in default. An unset value is ``None`` from the plain
accessor, and ``require()`` raises ConfigError with the exact command to fix
it. Nothing in this module touches the filesystem beyond reading the
settings files and, when asked to set a value, writing one of them.

Every settings file -- here and in mousedb.cohort_sheets, which keeps the
sheets folder and the pins in the same files -- is read by ONE reader
(read_settings_file) and written by ONE writer (update_settings_file). A
damaged file is never read as "nothing set" and never written over
(SettingsFileDamaged says why). The sheets settings are read merged, exactly
like the keys below; only writes are per file.

Keys (config.json name -> environment variable):
  mousedb_root              MOUSEDB_ROOT            folder holding connectome.db, exports/, logs/
  db_path                   MOUSEDB_DB_PATH         the database file itself (default: <mousedb_root>/connectome.db)
  snapshot_dir              MOUSEDB_SNAPSHOT_DIR    parquet snapshot folder (read while a watcher may hold the db)
  mousereach_pipeline_root  MOUSEREACH_PIPELINE_ROOT   MouseReach's shared pipeline folder (Analyzed/, Processing/ ...)
  mousebrain_pipeline_root  MOUSEBRAIN_PIPELINE_ROOT   MouseBrain's pipeline folder
  mousebrain_registry_root  MOUSEBRAIN_REGISTRY_ROOT   MouseBrain's analysis registry (default: <pipeline>/Registry;
                                                       the same variable MouseBrain itself honors, so both agree)
  cohort_sheets_dir         MOUSEDB_COHORT_SHEETS   (managed by mousedb.cohort_sheets)
  mousereach_route_cmd / mousereach_env             (managed by mousedb.bench_scan)
  lab_name                  MOUSEDB_LAB_NAME        the "Laboratory" value written into generated sheets / ODC exports
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path.home() / ".mousedb" / "config.json"
MACHINE_CONFIG_ENV = "MOUSEDB_MACHINE_CONFIG"


def machine_config_path() -> Path:
    """The settings file shared by every login on this computer (see the
    module docstring, source 3)."""
    override = os.environ.get(MACHINE_CONFIG_ENV)
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("PROGRAMDATA") or "C:\\ProgramData"
        return Path(base) / "mousedb" / "config.json"
    return Path("/etc/mousedb/config.json")

KEYS = {
    "mousedb_root": "MOUSEDB_ROOT",
    "db_path": "MOUSEDB_DB_PATH",
    "snapshot_dir": "MOUSEDB_SNAPSHOT_DIR",
    "mousereach_pipeline_root": "MOUSEREACH_PIPELINE_ROOT",
    "mousebrain_pipeline_root": "MOUSEBRAIN_PIPELINE_ROOT",
    "mousebrain_registry_root": "MOUSEBRAIN_REGISTRY_ROOT",
    "cohort_sheets_dir": "MOUSEDB_COHORT_SHEETS",
    "mousereach_route_cmd": "MOUSEDB_MOUSEREACH_ROUTE_CMD",
    "mousereach_env": "MOUSEDB_MOUSEREACH_ENV",
    "lab_name": "MOUSEDB_LAB_NAME",
    "missing_video_grace_days": "MOUSEDB_MISSING_VIDEO_GRACE_DAYS",
}


class ConfigError(RuntimeError):
    """A required location is not configured. The message says how to set it."""


class SettingsFileDamaged(ConfigError):
    """A settings file exists but cannot be read as settings (not JSON, not a
    set of named values, or not readable at all).

    WHY a separate failure, never "no settings": until this existed a damaged
    file read as EMPTY. Everything it held -- the database folder, the sheets
    folder, every pinned sheet -- silently became "not configured", and the
    next ``mousedb config --set`` or "This is the sheet" click wrote the file
    back holding that ONE value, so every other setting and pin was gone for
    good. Now a damaged file stops whatever needed it with a message naming
    the file, and nothing is ever written over it (see update_settings_file).
    """

    def __init__(self, path, why: str, backup=None):
        self.path, self.why, self.backup = Path(path), why, backup
        msg = ("the mousedb settings file %s is damaged and was not read (%s).\n"
               "  Open it in a text editor and put it right (it must be JSON: "
               "{\"key\": \"value\", ...}), or delete it and set the values again "
               "(mousedb config --show lists them)." % (path, why))
        if backup is not None:
            msg += ("\n  Nothing was saved: a copy of the damaged file is at %s"
                    % backup)
        super().__init__(msg)


def read_settings_file(path) -> dict:
    """One settings file, as a dict. The ONE reader every settings file in
    mousedb goes through (this module and mousedb.cohort_sheets).

      missing (or empty)           -> {}  (nothing set there: the normal state)
      not readable / not JSON /
      JSON that is not {key: ...}  -> raises SettingsFileDamaged

    Read as utf-8-sig. WHY: a file saved from Windows Notepad may start with a
    byte-order mark, which plain utf-8 JSON parsing rejects -- and a file
    rejected for that reason would otherwise read as empty (see SettingsFileDamaged).
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as e:
        raise SettingsFileDamaged(path, "%s: %s" % (type(e).__name__, e))
    if not text.strip():
        # An empty file holds nothing; that is not damage (a person may have
        # emptied it on purpose) and writing into it loses nothing.
        return {}
    try:
        data = json.loads(text)
    except ValueError as e:
        raise SettingsFileDamaged(path, "not valid JSON: %s" % e)
    if not isinstance(data, dict):
        raise SettingsFileDamaged(path, "it holds a %s, not named settings"
                                  % type(data).__name__)
    return data


def settings_file_problems() -> list:
    """(path, why) for each settings file that is damaged -- the person's own
    and the machine's. Empty when both read (or are absent). For the status
    displays, which must SAY a file is damaged rather than fail to open."""
    out = []
    for path in (CONFIG_PATH, machine_config_path()):
        try:
            read_settings_file(path)
        except SettingsFileDamaged as e:
            out.append((e.path, e.why))
    return out


# How long a write keeps retrying a file another program holds open. WHY:
# on Windows a reader (the GUI, a scheduled job, a virus scanner) that has the
# file open makes the final rename fail with PermissionError for a moment.
_REPLACE_TRIES, _REPLACE_WAIT = 20, 0.25


def _keep_damaged_copy(path: Path) -> Optional[Path]:
    """Copy a damaged settings file aside as <name>.damaged-<time>, so the
    person can see and mend what was in it. None when even that fails."""
    import shutil
    from datetime import datetime
    dest = path.with_name("%s.damaged-%s" % (path.name,
                                             datetime.now().strftime("%Y%m%d-%H%M%S")))
    try:
        shutil.copy2(str(path), str(dest))
        return dest
    except OSError:
        return None


def update_settings_file(path, change) -> Path:
    """Read ONE settings file, let ``change(dict)`` alter it in place, and
    write it back. Returns the path written.

    The ONE writer every settings file in mousedb goes through. It:
      * refuses a damaged file (SettingsFileDamaged): a copy is kept as
        <name>.damaged-<time>, the file itself is left exactly as it was, and
        the message names both. WHY: writing back "what could be read" from a
        damaged file is writing back almost nothing -- every other setting
        and pin would otherwise be lost that way;
      * writes a temporary file beside it and swaps it in with os.replace, so
        a crash or a full disk mid-write leaves the old file whole rather than
        a half-written one (which the next read would find damaged);
      * retries the swap for a few seconds while another program holds the
        file open (Windows refuses the rename then).
    Reads and writes ONLY the file named -- never the merged view -- so a
    person's file never gains copies of the machine file's values.
    """
    import time
    path = Path(path)
    try:
        cfg = read_settings_file(path)
    except SettingsFileDamaged as e:
        backup = _keep_damaged_copy(path) if path.is_file() else None
        raise SettingsFileDamaged(path, e.why, backup) from None
    change(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(str(tmp), str(path))
            return path
        except PermissionError:
            if attempt == _REPLACE_TRIES - 1:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(_REPLACE_WAIT)
    return path


def read_user_config() -> dict:
    """This person's own file only. Raises SettingsFileDamaged if it is damaged."""
    return read_settings_file(CONFIG_PATH)


def read_machine_config() -> dict:
    """The file shared by every login on this computer only. Raises
    SettingsFileDamaged if it is damaged."""
    return read_settings_file(machine_config_path())


def merge_settings(machine: dict, user: dict) -> dict:
    """The settings in force from the two files: the machine file, with this
    person's own file laid over it key by key (theirs wins). One function so
    every reader (here and mousedb.cohort_sheets) merges the same way."""
    merged = dict(machine)
    merged.update(user)
    return merged


def read_config() -> dict:
    """The settings in force: the machine file, with this person's own file
    laid over it key by key (theirs wins). Raises SettingsFileDamaged if
    either file is damaged -- never reads a damaged file as "nothing set"."""
    return merge_settings(read_machine_config(), read_user_config())


def set_value(key: str, value: Optional[str], machine: bool = False) -> Path:
    """Write one key (None removes it) to this person's file, or with
    ``machine=True`` to the file every login on this computer shares.
    Returns the file written.

    Only the chosen file is read and rewritten -- never the merged view --
    so setting one key for yourself never copies the machine's values into
    your own file (where they would then hide later changes to the machine
    file). A damaged file is refused, never overwritten (update_settings_file)."""
    if key not in KEYS:
        raise KeyError("unknown mousedb config key %r (known: %s)" % (key, ", ".join(KEYS)))
    path = machine_config_path() if machine else CONFIG_PATH

    def change(cfg):
        if value is None:
            cfg.pop(key, None)
        else:
            cfg[key] = str(value)
    return update_settings_file(path, change)


def get(key: str) -> Optional[str]:
    """Raw string value for a key (env first, then the merged settings files),
    or None. Raises SettingsFileDamaged when a settings file it has to read is
    damaged (an environment variable that answers first needs no file)."""
    env = KEYS.get(key)
    if env and os.environ.get(env):
        return os.environ[env]
    v = read_config().get(key)
    return str(v) if v else None


def at_import(accessor):
    """``accessor()``, or None when a settings file is damaged. ONLY for the
    few values computed when a module is first imported (mousedb.MOUSEDB_ROOT
    and the like).

    WHY: an import that raised would stop every mousedb command from even
    starting -- including ``mousedb config --show``, the place that says
    which file is damaged. The damage is not hidden by this: the first thing
    that actually needs a location (require(), get()) reads the files again
    and refuses loudly. The data-entry window is one such thing (it opens the
    database as it is built); its start-up catches that refusal and opens a
    window holding only the Setup tab instead (gui/app.py,
    open_main_window)."""
    try:
        return accessor()
    except SettingsFileDamaged:
        return None


def _path(key: str) -> Optional[Path]:
    v = get(key)
    return Path(v) if v else None


def mousedb_root() -> Optional[Path]:
    return _path("mousedb_root")


def db_path() -> Optional[Path]:
    p = _path("db_path")
    if p:
        return p
    root = mousedb_root()
    return root / "connectome.db" if root else None


def export_path() -> Optional[Path]:
    # The folder's name, like every output folder's, is in output_folders.
    from .output_folders import exports_dir
    root = mousedb_root()
    return exports_dir(root) if root else None


def log_path() -> Optional[Path]:
    root = mousedb_root()
    return root / "logs" if root else None


def snapshot_dir() -> Optional[Path]:
    return _path("snapshot_dir")


def mousereach_pipeline_root() -> Optional[Path]:
    p = _path("mousereach_pipeline_root")
    if p:
        return p
    # Source 3: MouseReach's own configuration on this machine.
    try:
        rc = Path.home() / ".mousereach" / "config.json"
        if rc.is_file():
            nas = json.loads(rc.read_text(encoding="utf-8")).get("nas_root")
            if nas:
                return Path(nas)
    except Exception:
        pass
    return None


def mousebrain_pipeline_root() -> Optional[Path]:
    return _path("mousebrain_pipeline_root")


def mousebrain_registry_root() -> Optional[Path]:
    """Where MouseBrain keeps its analysis registry: explicit key/env, else
    <mousebrain_pipeline_root>/Registry (MouseBrain's own default), else None.
    WHY a separate key: MouseBrain lets a lab move its registry with
    MOUSEBRAIN_REGISTRY_ROOT; the puller must follow the same setting."""
    p = _path("mousebrain_registry_root")
    if p:
        return p
    pipe = mousebrain_pipeline_root()
    return pipe / "Registry" if pipe else None


_ACCESSORS = {
    "mousedb_root": mousedb_root,
    "db_path": db_path,
    "snapshot_dir": snapshot_dir,
    "mousereach_pipeline_root": mousereach_pipeline_root,
    "mousebrain_pipeline_root": mousebrain_pipeline_root,
    "mousebrain_registry_root": mousebrain_registry_root,
}


def require(key: str) -> Path:
    """The configured path for ``key``; raises ConfigError telling the person
    exactly what to type if it is not set."""
    v = _ACCESSORS[key]()
    if v is None:
        raise ConfigError(
            "mousedb does not know '%s' on this machine.\n"
            "  Set it once for everyone on this computer:\n"
            "                   mousedb config --machine --set %s <path>\n"
            "  or just for you: mousedb config --set %s <path>\n"
            "  or for this run: set the environment variable %s\n"
            "  (files: %s, %s)" % (key, key, key, KEYS[key],
                                   machine_config_path(), CONFIG_PATH))
    return v


def lab_name() -> str:
    """The laboratory name written into generated tracking sheets and ODC
    exports; empty when not configured (mousedb config --set lab_name "...")."""
    return get("lab_name") or ""


# What each key is, in words a person who has never read this code can act on, and
# what kind of thing it points at so a chooser can offer the right dialog.
# KIND: "dir" a folder, "file" a single file, "text" free text.
KEY_HELP = {
    "mousedb_root": ("dir", "The folder mousedb keeps its own things in: the database "
                            "file, the exports it writes, and its logs."),
    "db_path": ("file", "The database file itself. Leave unset unless it lives "
                        "somewhere other than inside the folder above."),
    "snapshot_dir": ("dir", "Where the fast read-only copy of the database is kept. "
                            "Analyses read this instead of the live database, so they "
                            "work while the pipeline is writing. Put it on local disk."),
    "mousereach_pipeline_root": ("dir", "The reaching pipeline's shared folder, the one "
                                        "holding Analyzed/ and Processing/. Read only."),
    "mousebrain_pipeline_root": ("dir", "The tissue pipeline's folder. Read only."),
    "mousebrain_registry_root": ("dir", "Where the tissue tool records its analysis "
                                        "outputs. Leave unset to use the standard place "
                                        "inside the folder above."),
    "cohort_sheets_dir": ("dir", "The folder holding the tracking workbooks. Read only "
                                 "-- nothing here ever writes into it."),
    "mousereach_route_cmd": ("text", "The command used to ask the reaching tool to hold "
                                     "a video for review. Leave unset unless it was "
                                     "installed somewhere unusual."),
    "mousereach_env": ("text", "The environment the reaching tool is installed in, when "
                               "it is not the same one as this tool."),
    "lab_name": ("text", "The laboratory name written into generated sheets and shared "
                         "exports."),
    "missing_video_grace_days": ("text", "How many days after a hand-scored session its "
                                         "video may still be on its way before the session "
                                         "is listed as having no video (default 3)."),
}

# Set before anything works. Everything else has a sensible fallback or is optional.
REQUIRED_KEYS = ("mousedb_root", "snapshot_dir")

# Locations this tool CREATES when it first needs them. Their not existing yet is the
# normal state of a new machine, not a fault, so it is never reported as a problem --
# telling someone who has just configured correctly that their setup is broken teaches
# them to ignore the warnings that matter. Everything else is somewhere we only READ,
# where a path that is not there means a typo, a renamed folder, or a share that is
# not connected.
CREATED_ON_DEMAND = ("db_path", "snapshot_dir")


def status() -> list:
    """One entry per key: what it is, its value, where that came from, and whether
    the place it names actually exists.

    Returned as data rather than text so the command line and the GUI show the SAME
    answer -- two readers formatting one structure cannot drift, whereas two readers
    each deciding what "derived" means will.

    Each entry: {key, value, source, kind, help, required, exists}
      source  "environment <VAR>" | "config file" | "machine config file"
              | "derived" | "not set"
      exists  True / False for a path, None when there is nothing to check
    """
    # A damaged file contributes nothing HERE (and is named by problems()),
    # so the setup screen and `mousedb config --show` still open and can say
    # what is wrong. Everything else that reads the settings refuses instead.
    def _tolerant(reader):
        try:
            return reader()
        except SettingsFileDamaged:
            return {}
    user, machine = _tolerant(read_user_config), _tolerant(read_machine_config)
    out = []
    for key, env in KEYS.items():
        kind, help_text = KEY_HELP.get(key, ("text", ""))
        if os.environ.get(env):
            source, value = "environment %s" % env, os.environ[env]
        elif user.get(key):
            source, value = "config file", str(user[key])
        elif machine.get(key):
            source, value = "machine config file", str(machine[key])
        else:
            fn = _ACCESSORS.get(key)
            try:
                derived = fn() if fn else None
            except SettingsFileDamaged:
                derived = None
            source, value = ("derived", str(derived)) if derived else ("not set", "")
        exists = None
        if value and kind in ("dir", "file"):
            try:
                p = Path(value)
                exists = p.is_dir() if kind == "dir" else p.is_file()
            except OSError:
                exists = False
        out.append({"key": key, "value": value, "source": source, "kind": kind,
                    "help": help_text, "required": key in REQUIRED_KEYS,
                    "exists": exists})
    return out


def problems() -> list:
    """Plain sentences for everything wrong with this machine's configuration.

    Empty means the tool can run. Used by the setup screen so a person is told what
    is wrong in one place instead of meeting one ConfigError at a time, hours apart.
    """
    # Said first: a damaged settings file makes every value it held look
    # "not set", and that is the real cause to fix.
    found = ["the settings file %s is damaged and is not being read (%s). Open it "
             "in a text editor and put it right, or delete it and set its values "
             "again." % (path, why) for path, why in settings_file_problems()]
    for entry in status():
        if entry["required"] and not entry["value"]:
            found.append("%s is not set. %s" % (entry["key"], entry["help"]))
        elif (entry["value"] and entry["exists"] is False
              and entry["key"] not in CREATED_ON_DEMAND):
            found.append("%s points at something that is not there: %s"
                         % (entry["key"], entry["value"]))
    return found


def describe() -> str:
    """Human-readable table of every key, its value and where it came from."""
    lines = ["mousedb configuration",
             "  your own file:   %s" % CONFIG_PATH,
             "  machine file:    %s  (shared by everyone on this computer)"
             % machine_config_path(), ""]
    for entry in status():
        mark = ""
        if entry["exists"] is False:
            mark = "  <-- NOT THERE"
        elif not entry["value"]:
            mark = ""
        lines.append("  %-26s %-40s [%s]%s"
                     % (entry["key"], entry["value"] or "-",
                        entry["source"].upper() if entry["source"] == "not set"
                        else entry["source"], mark))
    trouble = problems()
    if trouble:
        lines.append("")
        lines.append("Problems:")
        lines += ["  - %s" % p for p in trouble]
    return "\n".join(lines)
