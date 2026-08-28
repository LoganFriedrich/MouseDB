"""Where this machine keeps things -- the ONLY place lab paths are allowed.

WHY THIS MODULE EXISTS
----------------------
mousedb is an optional integrator: it reads the outputs of the lab's tools
(MouseReach, MouseBrain) and holds the colony/experiment records. Every one
of those locations is a property of ONE lab's machines, not of the tool, so
none of them may be written into the source (this is a public repository).
Until 2026-08-28 a dozen modules each carried their own drive-letter default;
a new machine, a moved share, or another lab got silent wrong answers.

Where values come from, in order:
  1. an environment variable (per key, listed below) -- for one-off runs,
  2. ``~/.mousedb/config.json`` -- the normal place; set with
        mousedb config --set <key> <value>
     and inspect with ``mousedb config --show``,
  3. for the MouseReach pipeline root only: MouseReach's own
     ``~/.mousereach/config.json`` (``nas_root``), because on a machine that
     runs MouseReach that file already says where the pipeline is.

There is NO built-in default. An unset value is ``None`` from the plain
accessor, and ``require()`` raises ConfigError with the exact command to fix
it. Nothing in this module touches the filesystem beyond reading the file.

Keys (config.json name -> environment variable):
  mousedb_root              MOUSEDB_ROOT            folder holding connectome.db, exports/, logs/
  db_path                   MOUSEDB_DB_PATH         the database file itself (default: <mousedb_root>/connectome.db)
  snapshot_dir              MOUSEDB_SNAPSHOT_DIR    parquet snapshot folder (read while a watcher may hold the db)
  mousereach_pipeline_root  MOUSEREACH_PIPELINE_ROOT   MouseReach's shared pipeline folder (Analyzed/, Processing/ ...)
  mousebrain_pipeline_root  MOUSEBRAIN_PIPELINE_ROOT   MouseBrain's pipeline folder
  mousebrain_registry_root  MOUSEBRAIN_REGISTRY_ROOT   MouseBrain's analysis registry (default: <pipeline>/Registry;
                                                       the same variable MouseBrain itself honours, so both agree)
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
}


class ConfigError(RuntimeError):
    """A required location is not configured. The message says how to set it."""


def read_config() -> dict:
    try:
        if CONFIG_PATH.is_file():
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def set_value(key: str, value: Optional[str]) -> Path:
    """Write one key (None removes it). Returns the config file path."""
    if key not in KEYS:
        raise KeyError("unknown mousedb config key %r (known: %s)" % (key, ", ".join(KEYS)))
    cfg = read_config()
    if value is None:
        cfg.pop(key, None)
    else:
        cfg[key] = str(value)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return CONFIG_PATH


def get(key: str) -> Optional[str]:
    """Raw string value for a key (env first, then the file), or None."""
    env = KEYS.get(key)
    if env and os.environ.get(env):
        return os.environ[env]
    v = read_config().get(key)
    return str(v) if v else None


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
    root = mousedb_root()
    return root / "exports" if root else None


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
            "  Set it once:   mousedb config --set %s <path>\n"
            "  or for this run: set the environment variable %s\n"
            "  (config file: %s)" % (key, key, KEYS[key], CONFIG_PATH))
    return v


def lab_name() -> str:
    """The laboratory name written into generated tracking sheets and ODC
    exports; empty when not configured (mousedb config --set lab_name "...")."""
    return get("lab_name") or ""


def describe() -> str:
    """Human-readable table of every key, its value and where it came from."""
    lines = ["mousedb configuration (%s)" % CONFIG_PATH, ""]
    cfg = read_config()
    for key, env in KEYS.items():
        if os.environ.get(env):
            src, val = "environment %s" % env, os.environ[env]
        elif cfg.get(key):
            src, val = "config file", cfg[key]
        else:
            fn = _ACCESSORS.get(key)
            derived = fn() if fn else None
            src, val = ("derived", str(derived)) if derived else ("NOT SET", "-")
        lines.append("  %-26s %-40s [%s]" % (key, val, src))
    return "\n".join(lines)
