"""Tracking-sheet status and import, with a memory of what was imported when.

WHY THIS EXISTS
---------------
The lab fills the tracking spreadsheets "eventually" and the database is
only as current as the last import. Until 2026-08-28 nothing recorded when a
sheet was last imported, nothing compared that to when the sheet was last
edited, and an import that failed rolled back in silence (CNT_05: every
hour, for weeks). This module is the one place that knows, per cohort:

  * which file IS the sheet (all candidates listed; a person can pin one),
  * when it was last edited,
  * when it was last imported, by what, with what result,
  * therefore whether it NEEDS importing, and whether the last try failed.

Every import goes through ``import_cohorts`` and is appended to the ledger
(``Databases/logs/sheet_imports.jsonl``) -- success or failure, with the
error text -- so the GUI and the hourly job read the same history.

CLI (the GUI in MouseReach calls these with --json):
    mousedb-sheets status [--json]
    mousedb-sheets import [--cohort CNT_05 ...] [--dry-run] [--json]
    mousedb-sheets pin CNT_05 "Connectome_05_Animal_Tracking.xlsx"
    mousedb-sheets unpin CNT_05
    mousedb-sheets set-dir "<folder>"      # where the CNT sheets live

ASCII-only console output (Windows cp1252 consoles cannot print Unicode).
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from . import DEFAULT_LOG_PATH
from .cohort_sheets import (
    CONFIG_PATH, available_cohorts, cohort_sheet_candidates, cohort_sheets_dir,
    find_cohort_sheet, pin_cohort_sheet, pinned_sheet, set_cohort_sheets_dir,
)

LEDGER = Path(DEFAULT_LOG_PATH) / "sheet_imports.jsonl"


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------

def _read_ledger() -> List[dict]:
    if not LEDGER.is_file():
        return []
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _append_ledger(entry: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def last_import(cohort_id: str) -> Optional[dict]:
    """The most recent ledger entry for this cohort (success or failure)."""
    entries = [e for e in _read_ledger() if e.get("cohort_id") == cohort_id]
    return entries[-1] if entries else None


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def cohort_status(cohort_num: str) -> dict:
    cohort_id = "CNT_%s" % cohort_num
    cands = cohort_sheet_candidates(cohort_id)
    chosen = find_cohort_sheet(cohort_id)
    pin = pinned_sheet(cohort_id)
    last = last_import(cohort_id)
    edited = _iso(chosen.stat().st_mtime) if chosen else None

    if chosen is None:
        state, why = "no_sheet", "no tracking sheet found for this cohort"
    elif last is None:
        state, why = "never_imported", "this sheet has never been imported"
    elif not last.get("success"):
        state, why = "last_import_failed", (last.get("error") or "unknown error")[:300]
    elif last.get("sheet_mtime") and edited and edited > last["sheet_mtime"]:
        state, why = "sheet_newer", "the sheet was edited after the last import"
    elif last.get("sheet_name") and chosen.name != last.get("sheet_name"):
        state, why = "sheet_newer", "a different file is now the sheet (%s -> %s)" % (
            last.get("sheet_name"), chosen.name)
    else:
        state, why = "up_to_date", "database matches the sheet's last edit"

    return {
        "cohort_id": cohort_id,
        "sheet": chosen.name if chosen else None,
        "sheet_path": str(chosen) if chosen else None,
        "sheet_edited": edited,
        "candidates": [{"name": c.name, "edited": _iso(c.stat().st_mtime),
                        "size": c.stat().st_size} for c in cands],
        "ambiguous": len(cands) > 1 and not pin,
        "pinned": pin,
        "last_import": last,
        "state": state,
        "why": why,
    }


def status() -> dict:
    """Everything the Tracking Sheets tab shows, as one JSON-able dict."""
    d = cohort_sheets_dir()
    out = {
        "config_path": str(CONFIG_PATH),
        "cnt_sheets_dir": str(d) if d else None,
        "configured": d is not None,
        "ledger": str(LEDGER),
        "cohorts": [],
        "problem": None,
    }
    if d is None:
        out["problem"] = ("No tracking-sheet folder is configured (or the configured "
                          "folder holds no Connectome_NN_Animal_Tracking.xlsx). "
                          "Use 'Set sheets folder' / mousedb-sheets set-dir.")
        return out
    for n in available_cohorts(d):
        try:
            out["cohorts"].append(cohort_status(n))
        except Exception as e:
            out["cohorts"].append({"cohort_id": "CNT_%s" % n, "state": "error",
                                   "why": "status failed: %s" % e})
    return out


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

def import_cohorts(cohorts: Optional[List[str]] = None, dry_run: bool = False,
                   triggered_by: str = "manual") -> dict:
    """Import the chosen sheet of each cohort (all cohorts if None).

    One cohort failing never stops the others, and NOTHING is swallowed:
    each cohort's outcome -- counts, warnings, or the full error -- is
    returned AND (unless dry_run) appended to the ledger."""
    from .importers import ExcelImporter

    d = cohort_sheets_dir()
    results = {"triggered_by": triggered_by, "dry_run": dry_run,
               "sheets_dir": str(d) if d else None, "cohorts": []}
    if d is None:
        results["problem"] = "no tracking-sheet folder configured"
        return results

    wanted = None
    if cohorts:
        wanted = {("CNT_%02d" % int(c.split("_")[-1])) for c in cohorts}

    for n in available_cohorts(d):
        cohort_id = "CNT_%s" % n
        if wanted and cohort_id not in wanted:
            continue
        sheet = find_cohort_sheet(cohort_id)
        entry = {"cohort_id": cohort_id, "sheet_name": sheet.name if sheet else None,
                 "sheet_path": str(sheet) if sheet else None,
                 "sheet_mtime": _iso(sheet.stat().st_mtime) if sheet else None,
                 "started": datetime.now().isoformat(timespec="seconds"),
                 "triggered_by": triggered_by, "dry_run": dry_run}
        if sheet is None:
            entry.update(success=False, error="no sheet found")
        else:
            try:
                imp = ExcelImporter()
                r = imp.import_cohort_file(sheet, dry_run=dry_run)
                entry.update(success=bool(r.get("success")),
                             imported=r.get("imported"),
                             warnings=r.get("warnings", [])[:50],
                             error="; ".join(r.get("errors", [])) or None)
            except Exception as e:
                entry.update(success=False,
                             error="%s: %s" % (type(e).__name__, e),
                             traceback=traceback.format_exc()[-2000:])
        entry["finished"] = datetime.now().isoformat(timespec="seconds")
        results["cohorts"].append(entry)
        if not dry_run:
            _append_ledger(entry)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_status(st: dict) -> None:
    print("Tracking sheets folder: %s" % (st["cnt_sheets_dir"] or "(NOT CONFIGURED)"))
    if st.get("problem"):
        print("  [!] %s" % st["problem"])
        return
    print("%-8s %-14s %-19s %-19s %s" % ("cohort", "state", "sheet edited", "last import", "sheet"))
    for c in st["cohorts"]:
        li = c.get("last_import") or {}
        print("%-8s %-14s %-19s %-19s %s%s" % (
            c["cohort_id"], c["state"], c.get("sheet_edited") or "-",
            li.get("finished") or "never", c.get("sheet") or "-",
            "   [%d files match -- pin one]" % len(c["candidates"]) if c.get("ambiguous") else ""))
        if c["state"] in ("last_import_failed", "error"):
            print("         %s" % c["why"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mousedb-sheets", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="Per-cohort: which file, edited when, imported when, needs import?")
    s.add_argument("--json", action="store_true")
    i = sub.add_parser("import", help="Import the sheets (all cohorts, or --cohort ...)")
    i.add_argument("--cohort", action="append", help="e.g. CNT_05; repeatable")
    i.add_argument("--dry-run", action="store_true")
    i.add_argument("--json", action="store_true")
    i.add_argument("--triggered-by", default="manual")
    p = sub.add_parser("pin", help="Say which file IS a cohort's sheet")
    p.add_argument("cohort")
    p.add_argument("filename")
    u = sub.add_parser("unpin", help="Forget a pin (newest file wins again)")
    u.add_argument("cohort")
    sd = sub.add_parser("set-dir", help="Where the CNT tracking sheets live")
    sd.add_argument("folder")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        st = status()
        print(json.dumps(st, indent=1, default=str)) if args.json else _print_status(st)
        return 0
    if args.cmd == "import":
        r = import_cohorts(args.cohort, dry_run=args.dry_run, triggered_by=args.triggered_by)
        if args.json:
            print(json.dumps(r, indent=1, default=str))
        else:
            for c in r["cohorts"]:
                tag = "OK  " if c.get("success") else "FAIL"
                print("%s %s %s %s" % (tag, c["cohort_id"], c.get("sheet_name") or "-",
                                       c.get("imported") or c.get("error")))
            if r.get("problem"):
                print("[!] %s" % r["problem"])
        return 0 if all(c.get("success") for c in r["cohorts"]) else 1
    if args.cmd == "pin":
        print("pinned %s -> %s (in %s)" % (args.cohort, args.filename,
                                           pin_cohort_sheet(args.cohort, args.filename)))
        return 0
    if args.cmd == "unpin":
        pin_cohort_sheet(args.cohort, None)
        print("unpinned %s" % args.cohort)
        return 0
    if args.cmd == "set-dir":
        print("sheets folder recorded in %s" % set_cohort_sheets_dir(args.folder))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
