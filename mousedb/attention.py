"""What needs a person, gathered from every tool: shown in red on the Where Is
My Data tab, and written per cohort into a FIX THESE file beside its sheet.

WHY
---
A sheet whose date had to be read as a typo, an import that failed, a video
the pipeline cannot file because its hand-score check cannot run: each is
something only a person can fix, usually in the tracking sheet. Left in a log
it is never seen. So it is put where people look -- the tab they open to find
their data, and the folder where the sheet itself is edited. (A session with
no hand scores at all is not listed: it was never scored by hand, so there is
nothing to compare and nothing to fix -- Logan, 2026-10-26.)

WHAT IS GATHERED
----------------
1. The reaching pipeline's own lists (<mousereach_pipeline_root>/attention/,
   items marked needs_person): a video it cannot file because its check
   against the hand scores could not run (a setting or file to fix), and a
   LABEL MISMATCH -- a video whose results match another tray's hand scores
   of the same animal and day, not its own, so the sheet or the video's name
   has the trays swapped. The pipeline holds such a video outside every
   review queue (no review can settle it); its line here is written by
   mousedb from the item's fields (label_mismatch_says), names both trays,
   and asks a person to decide whether the name or the sheet is wrong (a
   sheet error is kept as it is and recorded, never edited). mousedb only reads the lists; it never imports the pipeline.
2. The tracking-sheet imports (the import ledger): for each cohort in the
   sheets folder, its last import if it failed, every date that import
   had to repair (its REPAIRED warnings) -- correct those in the sheet --
   every table it refused to bring in step with the sheet because the
   sheet read as broken (its NOT SYNCED lines), every animal fact
   (birth, death, sex) the sheet gives differently from the database, and
   every animal the database holds that the sheet no longer names and that
   nothing is recorded for.
3. Hand-scored sessions with no video anywhere in the reaching pipeline
   (missing_videos.py): one item per missing recording (day + tray), saying
   which animals and what to do. The tab folds them into one line per cohort
   (missing_videos.fold_for_tab).
Frozen workbooks (the letter cohorts) are not listed: nobody corrects them.

THE FIX THESE FILE
------------------
After every import that writes, each cohort in the sheets folder with
anything listed gets "!! <cohort> - FIX THESE.xlsx" beside its workbook: one
line per thing to fix, what is wrong and what to do. A cohort with nothing
listed has the file removed. It is the one file mousedb writes into the
sheets folder besides a new cohort's blank sheet (cohort_sheets says why);
its name can never be read as a sheet.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

FIX_THESE_NAME = "!! %s - FIX THESE.xlsx"


def mousereach_items(pipeline_root: Optional[Path] = None) -> List[dict]:
    """Items a person must act on from the reaching pipeline's lists."""
    if pipeline_root is None:
        from .config import mousereach_pipeline_root
        pipeline_root = mousereach_pipeline_root()
    out: List[dict] = []
    d = Path(pipeline_root) / "attention" if pipeline_root else None
    if not d or not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            out.append({"cohort_id": None, "source": "reaching pipeline",
                        "says": "The pipeline's list %s could not be read (%s)." % (f.name, e)})
            continue
        for i in data.get("items") or []:
            if i.get("needs_person"):
                says = i.get("says") or str(i)
                if i.get("kind") == LABEL_MISMATCH_KIND:
                    says = label_mismatch_says(i) or says
                out.append({"cohort_id": i.get("cohort_id"), "source": "reaching pipeline",
                            "kind": i.get("kind"), "says": says})
    return out


# The reaching pipeline's attention kind for a video whose results match
# ANOTHER tray's hand scores, not its own (a label mismatch).
LABEL_MISMATCH_KIND = "label_mismatch"


def _tray(key) -> str:
    return str(key or "?").rsplit("_", 1)[-1]


def label_mismatch_says(item: dict) -> Optional[str]:
    """The FIX THESE line for a label mismatch, written from the item's own
    fields (video_id, own_row, matched_rows, partner_back), or None when they
    are not there (an older pipeline): the caller then shows the pipeline's
    own text.

    WHY written here and not copied: the line must name BOTH trays and BOTH
    fixes in words the person at the sheet acts on, whichever version of the
    pipeline wrote the list. Such a video is held outside every review queue
    -- no review can fix a swapped tray -- so this line and the Where Is My
    Data tab are where a person learns of it. Which fix is right is a
    question about what happened at the bench, so both are named. The lab's
    rule: a sheet error is kept as it is and recorded (the bench's error rate
    is itself measured), so the line never tells anyone to edit the sheet."""
    vid = item.get("video_id")
    own = item.get("own_row") or {}
    matched = [m for m in (item.get("matched_rows") or []) if isinstance(m, dict)]
    if not vid or not matched or not own:
        return None
    parts = str(vid).split("_")
    who = "%s on %s" % (parts[1], parts[0]) if len(parts) >= 3 else str(vid)
    mine = _tray(vid)
    others = " / ".join(_tray(m.get("key")) for m in matched)
    scores = " and ".join("%s's (%s/%s pellets)" % (_tray(m.get("key")), m.get("agree"),
                                                    m.get("compared")) for m in matched)
    line = ("LABEL MISMATCH: video %s (%s, tray %s) matches the hand scores of tray %s, "
            "not its own: %s against %s's %s/%s." % (
                vid, who, mine, others, scores, mine, own.get("agree"), own.get("compared")))
    back = item.get("partner_back") or {}
    if back.get("video_id"):
        line += (" The other way round agrees: video %s matches %s's scores (%s/%s)." % (
            back.get("video_id"), mine, back.get("agree"), back.get("compared")))
    line += (" Decide whether the recording's NAME or the SHEET is wrong. A wrong name is "
             "corrected the documented way (original and a record kept in "
             "Archive/manual_corrections). A sheet error is kept as it is and recorded -- "
             "the sheet is never edited -- and the video is filed with the disagreement. "
             "Record the decision with mousereach-label-mismatch (--name-wrong or "
             "--sheet-error; --labels-right if neither is wrong). Until then the video is held, not sent to review." )
    if item.get("answers_kept"):
        line += (" Answers already given on pellet(s) %s are kept."
                 % ", ".join(str(x) for x in item["answers_kept"]))
    return line


def sheet_items(cohort_ids: Optional[List[str]] = None) -> List[dict]:
    """From each cohort's last sheet import: a failure, and every REPAIRED
    date. ``cohort_ids``: the cohorts to look at (default: those in the
    sheets folder)."""
    from .sheet_sync import _read_ledger
    if cohort_ids is None:
        from .cohort_sheets import available_cohorts
        cohort_ids = list(available_cohorts())
    last: Dict[str, dict] = {}
    for e in _read_ledger():
        if e.get("cohort_id") in cohort_ids and not e.get("dry_run"):
            last[e["cohort_id"]] = e
    out = []
    for cid in cohort_ids:
        e = last.get(cid)
        if not e:
            continue
        if not e.get("success"):
            out.append({"cohort_id": cid, "source": "sheet import",
                        "says": "The last import of %s's sheet (%s) failed: %s" % (
                            cid, e.get("finished") or e.get("started") or "?",
                            e.get("error") or "no reason recorded")})
        # The import refused to bring the database in step with the sheet,
        # because the sheet read as broken (importers: "The sheet wins").
        for refusal in ((e.get("sheet_sync") or {}).get("refused") or []):
            out.append({"cohort_id": cid, "source": "sheet import", "says": str(refusal)})
        # An animal fact (birth, death, sex) the sheet gives differently from
        # the database, which kept its own: a person decides which is right.
        for d in (((e.get("sheet_sync") or {}).get("subjects") or {}).get("differs") or []):
            out.append({"cohort_id": cid, "source": "sheet import", "says": (
                "%s's %s is %s in the database but %s in the sheet (0a_Metadata); the "
                "database value was kept. Correct whichever is wrong -- the sheet, or the "
                "animal in the GUI" % (d.get("subject_id"), str(d.get("field")).replace("_", " "),
                                       d.get("database"), d.get("sheet")))})
        # An animal the database holds, that the sheet no longer names and
        # that nothing is recorded for: a person decides whether it is real
        # (importers._report_animals_not_in_sheet). Nothing removes it.
        for sid in (((e.get("sheet_sync") or {}).get("subjects") or {}).get("not_in_sheet") or []):
            out.append({"cohort_id": cid, "source": "sheet import", "says": (
                "%s is in the database but not in %s's sheet (a 6_Ladder row without "
                "step counts does not count), and nothing is recorded for it. If the "
                "animal exists, add it to the sheet. If it never did, it has to be "
                "deleted from the database by whoever looks after it: no import, button "
                "or mousedb command removes an animal" % (sid, cid))})
        for w in e.get("warnings") or []:
            if str(w).startswith("REPAIRED"):
                out.append({"cohort_id": cid, "source": "sheet import", "says": str(w)})
    return out


def items() -> List[dict]:
    """Everything that needs a person, from every source. A source that
    cannot be read is itself an item, never silence."""
    out: List[dict] = []
    from .missing_videos import items as missing_video_items
    for name, fn in (("reaching pipeline", mousereach_items), ("sheet import", sheet_items),
                     ("missing videos", missing_video_items)):
        try:
            out += fn()
        except Exception as e:
            out.append({"cohort_id": None, "source": name,
                        "says": "Could not check the %s for things to fix (%s)." % (name, e)})
    return out


def write_fix_these(cohort_id: str, cohort_items: List[dict], sheets_dir: Path) -> Optional[Path]:
    """Write (or, with nothing listed, remove) the cohort's FIX THESE file in
    the sheets folder. Returns the path written, else None. Raises when it
    cannot be written (e.g. open in Excel): the caller reports it."""
    dest = Path(sheets_dir) / (FIX_THESE_NAME % cohort_id)
    if not cohort_items:
        if dest.exists():
            dest.unlink()
        return None
    import shutil
    import tempfile
    import openpyxl
    from openpyxl.styles import Alignment, Font
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Fix these"
    ws.append(["%s: %d thing(s) to fix" % (cohort_id, len(cohort_items))])
    ws["A1"].font = Font(bold=True, size=14, color="B71C1C")
    ws.append(["Written by mousedb after the sheet import of %s. Fix each line in the "
               "cohort's tracking sheet (or, where it says so, the video's name). This file "
               "is rewritten after every import and disappears by itself once nothing is "
               "left. Do not edit it -- it is not read." % datetime.now().strftime("%Y-%m-%d %H:%M")])
    ws.append([])
    ws.append(["What needs fixing", "Found by"])
    for c in "AB":
        ws["%s4" % c].font = Font(bold=True)
    for i in cohort_items:
        ws.append([i.get("says", ""), i.get("source", "")])
    ws.column_dimensions["A"].width = 120
    ws.column_dimensions["B"].width = 20
    for row in ws.iter_rows(min_row=2):
        row[0].alignment = Alignment(wrap_text=True, vertical="top")
    tmp = Path(tempfile.mkdtemp()) / dest.name
    wb.save(tmp)
    shutil.copyfile(tmp, dest)
    shutil.rmtree(tmp.parent, ignore_errors=True)
    return dest


def write_all_fix_these(sheets_dir: Path, cohort_ids: List[str]) -> Dict[str, str]:
    """Write or remove each cohort's FIX THESE file. Returns {cohort: what
    happened} -- 'written (N)', 'none', or 'FAILED: ...'."""
    everything = items()
    out = {}
    for cid in cohort_ids:
        mine = [i for i in everything if i.get("cohort_id") == cid]
        try:
            write_fix_these(cid, mine, sheets_dir)
            out[cid] = "written (%d)" % len(mine) if mine else "none"
        except Exception as e:
            out[cid] = "FAILED: %s: %s" % (type(e).__name__, e)
    return out
