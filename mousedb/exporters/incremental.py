"""Nightly exports that rewrite only what changed ("diff and replace").

WHY THIS EXISTS
---------------
The per-reach export files (each cohort's complete record, summary, analysis
and sessions files, and each project's combined files) used to be rebuilt
from scratch whenever ANY of their inputs changed -- one new video, one
corrected sheet cell -- and rebuilding took hours, almost all of it spent
re-making files whose content had not changed. The request was: only add
new records, don't rebuild.

Records are not only ever ADDED, though: a video can be analyzed again, cut
again, withdrawn; a sheet cell can be corrected; a cohort ends and its phase
labels are released; an exclusion appears or disappears. So the rule is
"replace what changed, add what is new, keep the rest", and the unit of
replacement is chosen so that keeping a unit can never keep a wrong byte.

THE UNIT: ONE COHORT'S FILES
----------------------------
Every per-reach file is written per cohort, and a row of it depends on more
than its own video: the animal's other trays that day (the day's total), the
animal's sheet records and hand scores, which columns the cohort's sheet and
measurements have (a new sheet column or measurement adds a column to every
row of the cohort), and a running number (entry_index) that counts the rows
of the whole file. A cohort is therefore the smallest unit whose files can be
rebuilt on their own and come out exactly as a full rebuild would write them.
Within a cohort the change is recorded per VIDEO (appended, replaced,
removed), so MANIFEST.json says which records moved and when.

A cohort's files are rebuilt when its FINGERPRINT changes. The fingerprint is
made of parts, each a SHA-256 of one input as the exporter sees it:

    reaches         the cohort's reach rows, every column (the hand-recorded
                    day weight included), in the order the exporter reads them
    animals         its animals' rows of the subjects table and its rows of
                    the tracking-sheet records table
    hand_scores     the hand pellet scores of its animals
    video_pellets   the per-pellet rows of its videos and animals
    video_sessions  the per-session rows of its animals (a filed tray with no
                    reach has only this row; it gets a session row in the
                    sessions file, odc_reaches.session_only_rows)
    exclusions      what the exclusion checks leave out of or blank in its
                    videos and the cohort, its animals' proven injury dates,
                    and whether its phase labels can be checked
    study_facts     the study-facts sections that apply to it (the lab-wide
                    default, its project's, its own)
    running         whether it counts as still running (phase labels withheld)
                    -- this changes with the date alone
    place           its folder and label (grants, the ASPA letter and
                    workbook)
    settings        the lab name and the lab's field definitions

Every part covers columns AND their types, so a change that alters how a
whole column is written (a number column that gains a blank turns 3 into
3.0 on every row) changes every cohort it touches.

WHEN EVERYTHING IS REBUILT
--------------------------
A FULL rebuild -- every cohort and every combined file -- happens when:
  * the exporter's code changed (exporters.current.exporter_code_hash: a new
    install of mousedb), which may change any column of any file;
  * there is no ledger yet (the first night), or it cannot be read;
  * a cohort's files on disk are not the ones the ledger recorded (deleted,
    edited or replaced by hand): that cohort, not everything, is rebuilt.
A settings change (lab name, field definitions) changes every cohort's
fingerprint, so every cohort is rebuilt -- by its own fingerprint, not by a
special case. Anything that cannot be decided is rebuilt, never kept.

THE COMBINED FILES
------------------
<PROJECT>/*_all_cohorts* are the project's cohort files one after the other.
When the cohorts at the front of the list are unchanged and the combined
columns are the same, the new combined file starts with exactly the bytes of
the old one up to the first changed cohort; that front part is copied as it is
(no parsing, no rewriting of cells) and only the rest is written. The file
is still put in place whole, under a temporary name first.

THE LEDGER
----------
``<exports>/current/.export_units.json`` records, per cohort, its fingerprint,
its per-video hashes, what it contributed to the manifest, the dictionaries
and the exclusions report, and the size and time of each file it wrote; and,
per combined file, where each cohort starts in it. It is written only after
the files are in place, and through a temporary file, so a run that stops
part way leaves the old ledger, whose recorded file sizes and times no longer
match the files that were replaced -- those cohorts are rebuilt next time.
Delete it to force a full rebuild (nothing else is lost).

THE CHECK THAT THIS IS SAFE
---------------------------
``mousedb export-incremental-check`` (main below) builds the exports twice
from the same snapshot -- once incrementally on top of a copy of the
previous night's files, once from nothing -- and compares every file byte
for byte (MANIFEST.json is compared without its times and run notes). The
test suite does the same for a new video, a changed video, a withdrawn
video, a phase relabel, an exclusion change and a code change.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

LEDGER_NAME = ".export_units.json"
LEDGER_FORMAT = 1

# The parts of a cohort's fingerprint, in the order a reason is reported.
PARTS = ("reaches", "animals", "hand_scores", "video_pellets", "video_sessions", "exclusions",
         "study_facts", "running", "place", "settings")


# ----------------------------------------------------------------- hashing

def _h(*chunks) -> str:
    h = hashlib.sha256()
    for c in chunks:
        if isinstance(c, str):
            c = c.encode("utf-8")
        h.update(c)
        h.update(b"\0")
    return h.hexdigest()


def _json(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=str)


def _row_hashes(df: Optional[pd.DataFrame]):
    """One 64-bit hash per row, over every column (pandas' own row hash).
    A missing value hashes the same whichever way it is spelled (None or
    NaN): the exporter treats them alike."""
    import numpy as np
    if df is None or df.empty:
        return np.zeros(0, dtype="uint64")
    return pd.util.hash_pandas_object(df, index=False).to_numpy()


def frame_hash(df: Optional[pd.DataFrame]) -> str:
    """Content, column names, column types and row order of a table slice."""
    if df is None:
        return "absent"
    return _h(_json([[str(c), str(t)] for c, t in df.dtypes.items()]),
              _row_hashes(df).tobytes())


def _starts(series: pd.Series, cohort_id: str) -> pd.Series:
    return series.astype(str).str.startswith(cohort_id + "_")


def cohort_fingerprints(t: dict, cohorts: Iterable[str], exclusions, out_dir: Path,
                        lab: str) -> Dict[str, dict]:
    """{cohort: {"parts": {part: hash}, "fingerprint": hash, "videos": {video: hash}}}.

    ``t`` is what odc_reaches.load_snapshot returned -- the very frames the
    files are built from, so a fingerprint can never describe different data
    from what was exported. ``exclusions`` is export_exclusions.compute()'s
    result, read BEFORE any cohort is written (writing a cohort adds notes and
    whole-cohort blanks to it)."""
    from .. import study_facts
    from . import field_definitions, layout
    from .odc_reaches import _cohort_still_on_study, cohort_label, cohort_source

    reach = t["reach"]
    subjects, records = t.get("subjects"), t.get("records")
    pellets, vp, vs = t.get("pellets"), t.get("video_pellets"), t.get("video_sessions")
    try:
        sections = study_facts.read_all() or {}
    except Exception as e:                       # decided by the export itself
        sections = {"_unreadable": type(e).__name__}
    try:
        p = field_definitions.definitions_path()
        defs = Path(p).read_text(encoding="utf-8") if p and Path(p).is_file() else ""
    except OSError as e:
        defs = "unreadable: %s" % type(e).__name__
    settings = _h(_json({"lab": lab, "field_definitions": defs}))

    sids_all = reach["subject_id"].astype(str)
    row_h = _row_hashes(reach)
    reach_types = _json([[str(c), str(tp)] for c, tp in reach.dtypes.items()])
    out = {}
    for cid in cohorts:
        mask = sids_all.str.startswith(cid + "_").to_numpy()
        rc = reach[mask]
        hashes = row_h[mask]
        videos: Dict[str, hashlib._Hash] = {}
        for v, rh in zip(rc["video_name"].astype(str), hashes):
            videos.setdefault(v, hashlib.sha256()).update(int(rh).to_bytes(8, "little"))
        sids = set(rc["subject_id"].astype(str))
        names = set(rc["video_name"].astype(str))
        # The cohort's filed trays with no reach (odc_reaches.session_only_rows)
        # are videos of the cohort too: their session rows are in its files.
        if vs is not None and not vs.empty and "subject_id" in vs:
            vs_c = vs[_starts(vs["subject_id"], cid)]
            parts_vs = frame_hash(vs_c)
            sids |= set(vs_c["subject_id"].astype(str))
            names |= set(vs_c["video_name"].astype(str))
        else:
            parts_vs = "absent" if vs is None else "empty"
        if vp is not None and not vp.empty and "subject_id" in vp:
            vp_c = vp[_starts(vp["subject_id"], cid)]
            names |= set(vp_c["video_name"].astype(str))
        parts = {"reaches": _h(reach_types, hashes.tobytes())}
        an = []
        if subjects is not None and not subjects.empty and "subject_id" in subjects:
            an.append(frame_hash(subjects[subjects["subject_id"].astype(str).isin(sids)]))
        else:
            an.append("absent" if subjects is None else "empty")
        if records is not None and not records.empty and "cohort_id" in records:
            an.append(frame_hash(records[records["cohort_id"] == cid]))
        else:
            an.append("absent" if records is None else "empty")
        parts["animals"] = _h(*an)
        if pellets is not None and not pellets.empty and "subject_id" in pellets:
            parts["hand_scores"] = frame_hash(pellets[_starts(pellets["subject_id"], cid)])
        else:
            parts["hand_scores"] = "absent" if pellets is None else "empty"
        if vp is not None and not vp.empty:
            keep = pd.Series(False, index=vp.index)
            if "video_name" in vp:
                keep |= vp["video_name"].astype(str).isin(names)
            if "subject_id" in vp:
                keep |= vp["subject_id"].astype(str).isin(sids)
            parts["video_pellets"] = frame_hash(vp[keep])
        else:
            parts["video_pellets"] = "absent" if vp is None else "empty"
        parts["video_sessions"] = parts_vs
        parts["exclusions"] = _h(_json(_exclusion_slice(exclusions, cid, names, sids)))
        project = study_facts.project_of(cid)
        parts["study_facts"] = _h(_json({k: sections.get(k) for k in
                                         ("default", project, cid, "_unreadable")}))
        try:
            facts = study_facts.facts(cid) or {}
        except Exception:
            facts = {}
        parts["running"] = _h(_json(bool(_cohort_still_on_study(cid, rc, facts))))
        label = cohort_label(cid)
        try:
            folder = layout.key(out_dir, layout.cohort_dir(out_dir, cid, label))
        except Exception as e:
            folder = "unknown: %s" % type(e).__name__
        parts["place"] = _h(_json([label, folder, cohort_source(cid)]))
        parts["settings"] = settings
        out[cid] = {"parts": parts,
                    "fingerprint": _h(_json([parts[p] for p in PARTS])),
                    "videos": {v: h.hexdigest()[:24] for v, h in videos.items()}}
    return out


def _exclusion_slice(ex, cohort_id: str, videos: set, subjects: set) -> dict:
    if ex is None:
        return {"none": True}
    return {
        "drop": {v: r for v, r in ex.drop_videos.items() if str(v) in videos},
        "blank": {v: b for v, b in ex.blank_fields.items() if str(v) in videos},
        "cohort_blank": ex.blank_cohort_fields.get(cohort_id),
        "injury": {s: d for s, d in ex.proven_injury.items() if str(s) in subjects},
        "checkable": cohort_id in ex.surgery_checkable,
    }


def video_changes(old: Optional[dict], new: dict) -> dict:
    """Which videos of a cohort were appended, replaced or removed."""
    old = old or {}
    return {"videos_appended": sorted(v for v in new if v not in old),
            "videos_replaced": sorted(v for v in new if v in old and old[v] != new[v]),
            "videos_removed": sorted(v for v in old if v not in new)}


def changed_parts(old: Optional[dict], new: dict) -> List[str]:
    if not old:
        return ["not in the ledger"]
    return [p for p in PARTS if old.get(p) != new.get(p)]


# ----------------------------------------------------------------- ledger

def stamp(path: Path):
    """[size, mtime_ns] of a file, or None when it is not there."""
    try:
        st = Path(path).stat()
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return None


def load_ledger(out_dir: Path, code: str) -> Tuple[dict, Optional[str]]:
    """(ledger, why it cannot be used). An unusable ledger means a full rebuild."""
    f = Path(out_dir) / LEDGER_NAME
    try:
        led = json.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, "no ledger yet (first incremental run)"
    except (OSError, ValueError) as e:
        return {}, "the ledger could not be read (%s)" % type(e).__name__
    if led.get("format") != LEDGER_FORMAT:
        return {}, "the ledger is from another version of this check"
    if led.get("code") != code:
        return {}, "the exporter's code changed (a new install), which may change any file"
    return led, None


def save_ledger(out_dir: Path, ledger: dict) -> None:
    f = Path(out_dir) / LEDGER_NAME
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(ledger, indent=0, default=str), encoding="utf-8")
    tmp.replace(f)


def drop_ledger(out_dir: Path) -> Optional[str]:
    """Remove the ledger (the next run rebuilds everything). Returns a problem
    line when it cannot be removed, else None."""
    f = Path(out_dir) / LEDGER_NAME
    try:
        f.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        return ("the export ledger %s could not be removed (%s: %s) -- close any program "
                "that has it open, or delete it by hand when no refresh is running"
                % (f.name, type(e).__name__, e))
    return None


def files_match(final_dir: Path, recorded: Dict[str, list]) -> bool:
    """True when every file a cohort wrote is on disk exactly as recorded."""
    if not recorded:
        return False
    return all(stamp(Path(final_dir) / k) == v for k, v in recorded.items())


def normalized(obj):
    """``obj`` as it reads back from the ledger. Both a rebuilt cohort and a kept
    one go through this, so what they contribute is the same either way."""
    return json.loads(json.dumps(obj, default=str))


def copy_if_changed(src: Path, target: Path, compare_below: int = 64 * 1024 * 1024) -> bool:
    """Put ``src`` at ``target`` (through a temporary name) unless ``target``
    already holds exactly these bytes. Returns True when it was written.

    WHY: a file rewritten with the same bytes gets a new time, and the limited
    exports (exporters.subsets) decide whether to remake a slice by its source
    files' sizes and times -- so an unchanged dictionary rewritten every night
    remade every slice every night. Only files below ``compare_below`` are
    compared; reading a large file back over the network costs about what
    writing it does."""
    src, target = Path(src), Path(target)
    try:
        same_size = target.stat().st_size == src.stat().st_size
    except OSError:
        same_size = False
    if same_size and src.stat().st_size < compare_below:
        if _same_bytes(src, target):
            return False
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    shutil.copyfile(src, tmp)
    tmp.replace(target)
    return True


def _same_bytes(a: Path, b: Path) -> bool:
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            while True:
                x, y = fa.read(1 << 20), fb.read(1 << 20)
                if x != y:
                    return False
                if not x:
                    return True
    except OSError:
        return False


# ----------------------------------------------------------- comparison tool

VOLATILE_MANIFEST_KEYS = ("generated_at", "incremental", "odc_reaches_skipped",
                          "archived_flat_layout_files", "archived_per_cohort_dictionaries",
                          "archived_cohorts_without_reaches", "archived_combined_files",
                          "archived_regrouped_project_folders")


def comparable_manifest(m: dict) -> dict:
    """A manifest without what legitimately differs between two runs of the same
    inputs: when it was written, how it was written, what was archived, and
    notes about this run's own moves. The per-file "archived_old_name" record
    is dropped for the same reason."""
    m = json.loads(json.dumps(m, default=str))
    for k in VOLATILE_MANIFEST_KEYS:
        m.pop(k, None)
    m.pop("snapshot_dir", None)
    m["notes"] = [n for n in m.get("notes", []) if "moved to the archive" not in n]
    for entry in m.get("files", {}).values():
        if isinstance(entry, dict):
            entry.pop("archived_old_name", None)
            entry.pop("source", None)
            entry.pop("source_mtime", None)
    return m


def compare_trees(a: Path, b: Path) -> List[str]:
    """Every difference between two export folders: files only in one, files whose
    bytes differ, and manifest differences (comparable_manifest). Hidden files,
    temporary files and archive folders (names starting with "_") are skipped."""
    a, b = Path(a), Path(b)

    def listing(root):
        out = {}
        for f in root.rglob("*"):
            rel = f.relative_to(root)
            if (not f.is_file() or any(p.startswith((".", "_")) for p in rel.parts)
                    or f.name.endswith(".tmp") or f.name == "MANIFEST.json"):
                continue
            out[rel.as_posix()] = f
        return out

    la, lb = listing(a), listing(b)
    diffs = ["only in %s: %s" % (a, k) for k in sorted(set(la) - set(lb))]
    diffs += ["only in %s: %s" % (b, k) for k in sorted(set(lb) - set(la))]
    for k in sorted(set(la) & set(lb)):
        if la[k].stat().st_size != lb[k].stat().st_size or not _same_bytes(la[k], lb[k]):
            diffs.append("bytes differ: %s" % k)
    ma, mb = a / "MANIFEST.json", b / "MANIFEST.json"
    if ma.exists() or mb.exists():
        try:
            ja = comparable_manifest(json.loads(ma.read_text(encoding="utf-8")))
            jb = comparable_manifest(json.loads(mb.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            diffs.append("MANIFEST.json unreadable: %s" % e)
        else:
            if ja != jb:
                keys = sorted(set(ja) | set(jb))
                bad = [k for k in keys if ja.get(k) != jb.get(k)]
                if "files" in bad:
                    fk = sorted(set(ja["files"]) | set(jb["files"]))
                    bad += ["files/%s" % k for k in fk
                            if ja["files"].get(k) != jb["files"].get(k)]
                diffs.append("MANIFEST.json differs in: %s" % ", ".join(bad))
    return diffs


def main(argv=None) -> int:
    """mousedb export-incremental-check: prove an incremental export equals a full one.

    Builds the current exports twice from ONE snapshot, in two scratch folders
    it creates under --work (never the live exports folder):
      1. incremental: a copy of --previous (last night's exports, with their
         ledger) is updated incrementally;
      2. full: the same exports built from nothing.
    Then compares every file byte for byte and prints the time each took.
    Exit 0 when they are identical, 1 when anything differs (each difference
    printed), 2 when it could not run.

    --previous may be left out: the incremental build then starts from a full
    build of --base-snapshot (a copy of an earlier snapshot), which this tool
    makes first. Nothing is written outside --work."""
    import argparse
    import time
    from . import current
    ap = argparse.ArgumentParser(prog="mousedb export-incremental-check",
                                 description=main.__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot-dir", type=Path, required=True,
                    help="the snapshot to export (a COPY, not the live one: the export "
                         "writes its working files beside it)")
    ap.add_argument("--work", type=Path, required=True,
                    help="an empty scratch folder for the two builds")
    ap.add_argument("--previous", type=Path,
                    help="a copy of the previous night's current exports to update")
    ap.add_argument("--base-snapshot", type=Path,
                    help="instead of --previous: an earlier snapshot copy to build the "
                         "'previous night' from")
    a = ap.parse_args(argv)
    work = Path(a.work)
    if work.exists() and any(work.iterdir()):
        print("[FAIL] --work must be an empty or new folder: %s" % work)
        return 2
    from .odc_reaches import _is_live_exports_folder
    if _is_live_exports_folder(work):
        print("[FAIL] --work is the live exports folder; choose a scratch folder")
        return 2
    inc, full = work / "incremental" / "current", work / "full" / "current"
    locks = work / "locks"

    def run(snap, out, rebuild_all=False):
        t0 = time.time()
        m = current.refresh_current(snap, out, db_ok=False, lock_dir=locks,
                                    rebuild_all=rebuild_all)
        return m, time.time() - t0

    if a.previous:
        shutil.copytree(a.previous, inc)
    elif a.base_snapshot:
        print("building the previous night from %s ..." % a.base_snapshot, flush=True)
        m, s = run(a.base_snapshot, inc)
        print("  previous night built in %.0f s (complete=%s)" % (s, m["complete"]), flush=True)
    else:
        print("[FAIL] give --previous or --base-snapshot")
        return 2
    print("incremental run ...", flush=True)
    mi, si = run(a.snapshot_dir, inc)
    print("  incremental: %.0f s, complete=%s" % (si, mi["complete"]), flush=True)
    rec = mi.get("incremental") or {}
    print("  mode %s; cohorts rewritten: %s; kept: %d" % (
        rec.get("mode"), ", ".join(sorted(rec.get("cohorts_rewritten", {}))) or "none",
        len(rec.get("cohorts_kept", {}))), flush=True)
    print("full run ...", flush=True)
    mf, sf = run(a.snapshot_dir, full, rebuild_all=True)
    print("  full: %.0f s, complete=%s" % (sf, mf["complete"]), flush=True)
    diffs = compare_trees(inc, full)
    for d in diffs:
        print("  [!] %s" % d)
    print("[OK] identical: every file byte for byte" if not diffs
          else "[FAIL] %d difference(s)" % len(diffs))
    print("time: incremental %.0f s, full %.0f s" % (si, sf))
    return 0 if not diffs else 1


if __name__ == "__main__":
    raise SystemExit(main())
