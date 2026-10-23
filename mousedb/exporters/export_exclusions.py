"""What is left out of the analysis exports, and why -- recomputed on every export.

THE RULE
--------
If an entry has a problem, the entry is not exported. Only that entry: the
smallest scope that contains the problem.

* A video whose identity is contested is left out together with every video cut
  from the same multi-animal recording, because the recording's typed name list
  is what assigned the identities and it is the thing in doubt.
* A column that is doubtful for a session is blanked (written as the absent
  value) and the session is kept.
* Nothing is ever edited or corrected here. This module only decides what not
  to say.

WHY IT IS COMPUTED, NOT LISTED
------------------------------
Every exclusion comes from a check run against the data at export time. Nothing
is kept in a hand-made list. So when the source is corrected -- a video renamed,
a sheet cell fixed -- the check stops firing and the entry comes back on the
next export with nobody having to say so. A list would have to be remembered,
and the one thing guaranteed about a list kept by hand is that it goes stale.

THE CHECKS
----------
Each one is chosen so that it does NOT depend on trusting the hand-typed pellet
scores. Those scores disagree with the pipeline routinely and legitimately -- a
spilled tray, a typo, a scoring convention -- and a disagreement alone is never
a reason to leave anything out. What IS evidence is a near-perfect match: twenty
pellet results in a row form a pattern that is close to unique to one animal's
tray on one day, so a video that reproduces ANOTHER animal's hand-scored pattern
almost exactly, while matching its own label poorly, was filmed under the wrong
name. Calibration on videos nobody disputes: a correctly named video matches its
own animal's hand scores 20/20 and a cagemate's about 6-12/20.

1. identity  -- the pattern match above, discriminating thresholds only.
2. tray      -- the same match, but the best match is the SAME animal's other
                tray that day: the reaches are fine, the hand-score columns were
                attached to the wrong tray. Those columns are blanked.
3. collision -- one single-animal video name produced by two different
                recordings: which recording the reaches came from is unknown.
4. death     -- reaches dated after a death the sheet itself proves (a surgery
                with the day's weight typed, marked not survived). A proven death
                outranks a video label.
5. no sheet  -- an animal of a project cohort that has no tracking workbook at
                all, so nothing about it can be checked (typically a typo in a
                video name invents the cohort).
6. phase     -- a cohort whose experiment-phase labels contradict its own proven
                injury dates (a rehabilitation label before the injury, a
                baseline label for a cohort not yet injured). The labelling
                failed for that cohort, so its phase columns are blanked.
7. pellets   -- a tray presents 20 pellets. A video the pipeline cut into any
                other number has the wrong pellet number on its reaches, so that
                session is left out. A tray whose hand scores number other than
                20 (one left out, one typed under the wrong number) has its
                hand-score columns blanked; its reaches are unaffected.
8. archive   -- a recording whose seats the archive's OWN single-animal cuts
                name for animals the recording's name does not list (an archive
                that kept its own cuts names every animal twice, typed
                separately). Read from the record MouseReach's name audit writes
                (``<pipeline>/pipeline_records/archive_name_conflicts.json``,
                ``mousereach-aspa-name-audit``): every video cut from such a
                recording is left out, because whose video each seat holds is in
                doubt. Rerunning the audit after a person resolves the name
                brings the videos back. No record: the check says it did not run.

Low-information pellet patterns (all the same value, or nearly) are never used
as evidence: they match everything.
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

# The pipeline's outcome words mapped onto the sheets' own pellet codes
# (0 left on the pillar, 1 knocked off, 2 retrieved -- the codes the sheets'
# own COUNTIF formulas use).
OUTCOME_TO_CODE = {"untouched": 0, "displaced_sa": 1, "displaced_outside": 1,
                   "retrieved": 2}

# Evidence thresholds. A match only counts when it is close to perfect AND the
# label's own match is poor AND no third candidate is nearly as good -- each
# guards a different way a coincidence could pass for a misfiling.
MIN_COMPARABLE = 15        # pellets both records scored
MIN_MINORITY = 4           # entries differing from the vector's most common value
BEST_AT_LEAST = 0.95       # agreement with the matching animal
OWN_AT_MOST = 0.75         # agreement with the animal the video is labelled as
RUNNER_UP_AT_MOST = 0.85   # agreement with the next-best candidate

# Every column derived from the hand scores of one tray. Blanking "the hand-score
# columns" must blank ALL of them: the session totals ride on every reach row and
# are computed before this pass runs, so a column missing from this list would
# keep a value this pass has judged doubtful (2026-10-16: three new hand-scored
# percentages were about to do exactly that).
MANUAL_FIELDS = (["pellets_hand_scored", "manual_displaced", "manual_retrieved",
                  "manual_contacted"]
                 + ["percent_%s_hand_scored" % o
                    for o in ("retrieved", "displaced", "contacted", "missed")])
PHASE_FIELDS = ["test_type", "test_type_grouped", "experiment_stage"]
POST_INJURY_STAGES = {"After injury", "Rehabilitation", "After rehabilitation"}


@dataclass
class Exclusions:
    """What the analysis exporter must leave out, with a reason for each."""
    drop_videos: Dict[str, str] = field(default_factory=dict)
    blank_fields: Dict[str, Dict[str, str]] = field(default_factory=dict)  # video -> {field: reason}
    blank_cohort_fields: Dict[str, Dict[str, str]] = field(default_factory=dict)  # cohort -> {field: reason}
    proven_injury: Dict[str, str] = field(default_factory=dict)   # subject -> ISO date
    # Cohorts whose records come from the tabs the surgery checks read. For any
    # other cohort those checks cannot run, which is a different thing from
    # failing, and they must say so rather than blank anything.
    surgery_checkable: Set[str] = field(default_factory=set)
    notes: List[str] = field(default_factory=list)

    def drop(self, video: str, reason: str) -> None:
        self.drop_videos.setdefault(video, reason)

    def blank(self, video: str, fields: List[str], reason: str) -> None:
        slot = self.blank_fields.setdefault(video, {})
        for f in fields:
            slot.setdefault(f, reason)


# ---------------------------------------------------------------- data access

def _single_name(date: str, animal: str, tray: str) -> str:
    return "%s_%s_%s" % (date, animal, tray)


_COLLAGE = re.compile(r"^(\d{8})_(.+)_([A-Za-z]+\d+)\.[A-Za-z0-9]+$")


def collage_members(pipeline_root: Optional[Path]) -> Tuple[Dict[str, Set[str]], Dict[str, List[str]]]:
    """(single video -> recordings it was cut from, recording -> its singles).

    Read from the collage file names the MouseReach watcher databases record:
    a recording is named DATE_<animal list>_<tray>, and each single-animal video
    is DATE_<animal>_<tray>. Opened read-only; a missing or locked database
    means fewer recordings are known, never a crash.
    """
    single_to_rec: Dict[str, Set[str]] = defaultdict(set)
    rec_to_singles: Dict[str, List[str]] = {}
    if pipeline_root is None:
        return single_to_rec, rec_to_singles
    for db_name, table in (("watcher.db", "collages"),
                           ("watcher_central.db", "pipeline_collages")):
        path = Path(pipeline_root) / db_name
        if not path.exists():
            continue
        try:
            con = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True, timeout=5)
            names = [r[0] for r in con.execute("SELECT filename FROM %s" % table)]
            con.close()
        except sqlite3.Error:
            continue
        for name in names:
            m = _COLLAGE.match(str(name).strip())
            if not m:
                continue
            date, animals, tray = m.groups()
            # The recording's identity is its name without the file extension:
            # the same recording saved as .mkv and .mp4 is one recording.
            rec = "%s_%s_%s" % (date, animals, tray)
            singles = [_single_name(date, a.strip(), tray)
                       for a in animals.split(",") if a.strip()]
            rec_to_singles[rec] = singles
            for s in singles:
                single_to_rec[s].add(rec)
    return single_to_rec, rec_to_singles


def _hand_vectors(snapshot: Path) -> Dict[Tuple[str, str, str, int], Dict[int, int]]:
    ps = pd.read_parquet(snapshot / "pellet_scores.parquet",
                         columns=["subject_id", "session_date", "tray_type",
                                  "tray_number", "pellet_number", "score"])
    ps = ps.dropna(subset=["score", "pellet_number", "tray_number"])
    ps["d"] = ps["session_date"].astype(str).str[:10]
    out: Dict[Tuple[str, str, str, int], Dict[int, int]] = defaultdict(dict)
    for sid, d, tt, tn, pn, sc in zip(ps["subject_id"], ps["d"], ps["tray_type"],
                                      ps["tray_number"], ps["pellet_number"], ps["score"]):
        out[(str(sid), d, str(tt), int(tn))][int(pn)] = int(sc)
    return out


def _video_vectors(snapshot: Path) -> Tuple[Dict[str, Dict[int, int]], pd.DataFrame]:
    rd = pd.read_parquet(snapshot / "reach_data.parquet",
                         columns=["video_name", "subject_id", "session_date",
                                  "tray_type", "run_number", "segment_num",
                                  "segment_outcome"])
    seg = rd.drop_duplicates(["video_name", "segment_num"])
    vectors: Dict[str, Dict[int, int]] = defaultdict(dict)
    for v, n, o in zip(seg["video_name"], seg["segment_num"], seg["segment_outcome"]):
        code = OUTCOME_TO_CODE.get(str(o).strip().lower())
        if code is not None and pd.notna(n):
            vectors[str(v)][int(n)] = code
    videos = rd.drop_duplicates("video_name")[
        ["video_name", "subject_id", "session_date", "tray_type", "run_number"]].copy()
    videos["d"] = videos["session_date"].astype(str).str[:10]
    return vectors, videos


def _informative(vec: Dict[int, int]) -> bool:
    if len(vec) < MIN_COMPARABLE:
        return False
    counts = pd.Series(list(vec.values())).value_counts()
    return int(len(vec) - counts.iloc[0]) >= MIN_MINORITY


def _agreement(a: Dict[int, int], b: Dict[int, int]) -> Optional[float]:
    keys = [k for k in a if k in b]
    if len(keys) < MIN_COMPARABLE:
        return None
    return sum(a[k] == b[k] for k in keys) / float(len(keys))


def _surgery_rows(snapshot: Path) -> pd.DataFrame:
    """One row per surgery record from animal_records, with proof and outcome."""
    path = snapshot / "animal_records.parquet"
    if not path.exists():
        return pd.DataFrame()
    ar = pd.read_parquet(path)
    tabs = {"4_Contusion_Injury_Details": "contusion", "5_SC_Injection_Details": "injection"}
    ar = ar[ar["source_tab"].isin(tabs)]
    if ar.empty:
        return pd.DataFrame()
    # Any record from these tabs, proven or planned, means the cohort's workbook
    # is of the kind these checks understand.
    checkable = set(ar["subject_id"].astype(str).str.rsplit("_", n=1).str[0])
    _surgery_rows.checkable = checkable
    wide = ar.pivot_table(index=["subject_id", "source_tab", "record_no"],
                          columns="field", values="value", aggfunc="first").reset_index()
    wide["kind"] = wide["source_tab"].map(tabs)

    def grams(v):
        try:
            g = float(str(v).replace("g", "").replace(",", ".").strip())
            return g if g > 0 else None
        except (TypeError, ValueError):
            return None

    weight_col = next((c for c in wide.columns if str(c).startswith("Subject_Weight")), None)
    date_col = next((c for c in ("Surgery_Date", "Date") if c in wide.columns), None)
    wide["grams"] = wide[weight_col].map(grams) if weight_col else None
    wide["date"] = (pd.to_datetime(wide[date_col], errors="coerce").dt.strftime("%Y-%m-%d")
                    if date_col else None)
    wide["survived"] = (wide["Survived"].astype(str).str.strip().str.upper()
                        if "Survived" in wide.columns else "")
    # Proof that a surgery happened is the weight taken on the day; everything
    # else on the row can be typed in advance for a planned surgery.
    wide["proven"] = wide["grams"].notna() & wide["date"].notna()
    return wide


# -------------------------------------------------------------------- checks

def compute(snapshot_dir, pipeline_root=None, sheets_dir=None) -> Exclusions:
    """Run every check against the snapshot. Read-only throughout."""
    snapshot = Path(snapshot_dir)
    ex = Exclusions()
    single_to_rec, rec_to_singles = collage_members(Path(pipeline_root) if pipeline_root else None)

    def drop_recording(video: str, reason: str) -> None:
        recs = single_to_rec.get(video)
        if not recs:
            ex.drop(video, reason)
            return
        for rec in recs:
            for single in rec_to_singles.get(rec, [video]):
                ex.drop(single, reason + " (left out with every animal in recording %s)" % rec)

    hand = _hand_vectors(snapshot)
    vectors, videos = _video_vectors(snapshot)

    by_day_tray: Dict[Tuple[str, str], List[Tuple[str, str, str, int]]] = defaultdict(list)
    for key in hand:
        by_day_tray[(key[1], key[2])].append(key)

    # 1 and 2: identity and tray, by pellet pattern
    for video, sid, d, tt, tn in zip(videos["video_name"], videos["subject_id"], videos["d"],
                                     videos["tray_type"], videos["run_number"]):
        vec = vectors.get(str(video))
        if not vec or not _informative(vec) or pd.isna(tn):
            continue
        own_key = (str(sid), d, str(tt), int(tn))
        own = _agreement(vec, hand[own_key]) if own_key in hand else None
        scored = []
        for key in by_day_tray.get((d, str(tt)), []):
            if key == own_key or not _informative(hand[key]):
                continue
            a = _agreement(vec, hand[key])
            if a is not None:
                scored.append((a, key))
        if not scored:
            continue
        scored.sort(reverse=True)
        best, best_key = scored[0]
        runner = scored[1][0] if len(scored) > 1 else 0.0
        if best < BEST_AT_LEAST or runner > RUNNER_UP_AT_MOST:
            continue
        if own is not None and own > OWN_AT_MOST:
            continue
        if best_key[0] == str(sid):
            ex.blank(str(video), MANUAL_FIELDS,
                     "tray: this video's pellets match the same animal's tray %s%d hand "
                     "scores (%.0f%%), not tray %s%d's, so the hand-score columns were "
                     "attached to the wrong tray" % (best_key[2], best_key[3], 100 * best,
                                                     tt, int(tn)))
        else:
            drop_recording(str(video),
                           "identity: pellets match %s's hand scores %.0f%% but %s's %s"
                           % (best_key[0], 100 * best, sid,
                              "not at all (no hand scores)" if own is None
                              else "only %.0f%%" % (100 * own)))

    # 3: collision -- one single name produced by two different recordings
    for single, recs in single_to_rec.items():
        if len(recs) > 1:
            ex.drop(single, "collision: this video name was produced by %d different "
                            "recordings (%s); which one these reaches came from is unknown"
                    % (len(recs), "; ".join(sorted(recs))))

    # 4: reaches after a proven death; also collect proven injury dates
    _surgery_rows.checkable = set()
    surg = _surgery_rows(snapshot)
    ex.surgery_checkable = set(_surgery_rows.checkable)
    proven_death: Dict[str, str] = {}
    if not surg.empty:
        dead = surg[surg["proven"] & surg["survived"].isin(["N", "NO"])]
        for sid, d in zip(dead["subject_id"], dead["date"]):
            if sid not in proven_death or d < proven_death[sid]:
                proven_death[str(sid)] = d
        inj = surg[surg["proven"] & (surg["kind"] == "contusion")]
        for sid, d in zip(inj["subject_id"], inj["date"]):
            if sid not in ex.proven_injury or d < ex.proven_injury[sid]:
                ex.proven_injury[str(sid)] = d
    for video, sid, d in zip(videos["video_name"], videos["subject_id"], videos["d"]):
        died = proven_death.get(str(sid))
        if died and d > died:
            drop_recording(str(video),
                           "death: %s is recorded dying on %s (surgery with its weight typed, "
                           "not survived) but this video is dated %s" % (sid, died, d))

    # 7: pellet count -- every tray presents PELLETS_PER_TRAY pellets.
    from ..stats import PELLETS_PER_TRAY
    vp_path = snapshot / "video_pellets.parquet"
    if vp_path.exists():
        found = pd.read_parquet(vp_path, columns=["video_name"])["video_name"].value_counts()
        for video, count in found.items():
            if int(count) != PELLETS_PER_TRAY:
                ex.drop(str(video), "pellet count: the pipeline found %d pellets in this video, "
                                    "not %d, so which pellet each reach was at is not known"
                        % (int(count), PELLETS_PER_TRAY))
    else:
        ex.notes.append("pellet-count check for videos not run: the snapshot has no "
                        "per-pellet table yet (it appears after the next snapshot refresh)")
    for video, sid, d, tt, tn in zip(videos["video_name"], videos["subject_id"], videos["d"],
                                     videos["tray_type"], videos["run_number"]):
        if pd.isna(tn):
            continue
        scored = hand.get((str(sid), d, str(tt), int(tn)))
        if scored and len(scored) != PELLETS_PER_TRAY:
            ex.blank(str(video), MANUAL_FIELDS,
                     "hand-score count: this tray has %d pellets hand scored, not %d"
                     % (len(scored), PELLETS_PER_TRAY))

    # 8: archive names -- a recording whose seats the archive's own cuts name
    # for animals its name does not list. Every video cut from it is left out.
    conflicts = archive_conflicts(Path(pipeline_root) if pipeline_root else None)
    if conflicts is None:
        ex.notes.append("archive-name check not run: no name-audit record in the pipeline "
                        "folder (pipeline_records/archive_name_conflicts.json, written by "
                        "mousereach-aspa-name-audit)")
    else:
        for c in conflicts:
            named = ",".join(sorted({str(x.get("animal")) for x in
                                     c.get("cuts_named_differently", [])}))
            for rec in c.get("pipeline_recordings", []):
                m = re.match(r"^(\d{8})_(.+)_([A-Za-z]+\d+)$", str(rec))
                if not m:
                    continue
                date, animals, tray = m.groups()
                for a in animals.split(","):
                    ex.drop(_single_name(date, a.strip(), tray),
                            "archive: the archive's own cut(s) of recording %s are named %s, "
                            "which its name does not list -- whose video each seat holds is "
                            "in doubt (left out with every animal in recording %s)"
                            % (c.get("recording"), named, rec))

    # 5: an animal of a project cohort with no tracking workbook
    if sheets_dir and Path(sheets_dir).is_dir():
        have = set()
        for p in Path(sheets_dir).iterdir():
            m = re.match(r"^Connectome_(\d{2})_Animal_Tracking", p.name)
            if m:
                have.add("CNT_%s" % m.group(1))
        for video, sid in zip(videos["video_name"], videos["subject_id"]):
            parts = str(sid).rsplit("_", 1)
            cohort = parts[0] if len(parts) == 2 else ""
            if cohort.startswith("CNT_") and cohort not in have:
                ex.drop(str(video), "no sheet: cohort %s has no tracking workbook, so "
                                    "nothing about %s can be checked" % (cohort, sid))
    else:
        ex.notes.append("no-sheet check not run: the tracking-sheet folder is not available")

    return ex


def archive_conflicts(pipeline_root: Optional[Path]) -> Optional[List[dict]]:
    """The contested recordings in MouseReach's name-audit record (check 8), or
    None when there is no record to read. Read-only; an unreadable record is
    treated as absent, never as "no conflicts"."""
    if pipeline_root is None:
        return None
    path = Path(pipeline_root) / "pipeline_records" / "archive_name_conflicts.json"
    if not path.is_file():
        return None
    try:
        import json
        return list(json.loads(path.read_text(encoding="utf-8")).get("conflicts", []))
    except (OSError, ValueError):
        return None


# ----------------------------------------------------------------- applying

def apply(frame: pd.DataFrame, ex: Exclusions, cohort_id: str = "",
          missing_word: str = "NA") -> Tuple[pd.DataFrame, List[dict]]:
    """Return (frame with exclusions applied, report rows).

    Applied to ONE cohort's analysis per-reach frame; cohort_id names it. The
    cohort is passed in rather than read off the rows because the rows carry
    the lab's own animal ids, which for some projects are decoded on the way
    out (ASPA_06_01 becomes F01) and no longer contain the cohort at all. The
    first version split the id and so judged each ASPA animal as a cohort of
    its own. The sessions file is built from these rows, so it inherits every
    exclusion without a second rule.
    """
    report: List[dict] = []
    if frame.empty:
        return frame, report
    out = frame.copy()
    cohort = str(cohort_id)

    def entry(**kw):
        row = {"cohort": cohort}
        row.update(kw)
        return row

    # 6: phase labels contradicted by the cohort's own proven injury dates.
    if cohort and {"subject_id", "test_date", "experiment_stage"}.issubset(out.columns):
        if cohort not in ex.surgery_checkable:
            # Either the workbook has no contusion tab (the letter cohorts), or
            # no surgery is proven yet (none has its day's weight typed -- a
            # cohort still in training). Both read the same from here.
            ex.notes.append("phase check not run for %s: its records hold no proven surgery "
                            "(no contusion tab, or no surgery with its day's weight typed yet) "
                            "to check the labels against, so they are left as they are "
                            "(not checked, which is not the same as wrong)" % cohort)
        else:
            rows = out
            bad = 0
            for sid, d, stage, grouped in zip(rows["subject_id"], rows["test_date"],
                                              rows["experiment_stage"],
                                              rows.get("test_type_grouped", pd.Series([""] * len(rows)))):
                injured = ex.proven_injury.get(str(sid))
                if stage in POST_INJURY_STAGES and (injured is None or str(d) <= injured):
                    bad += 1
                elif "baseline" in str(grouped).lower() and injured is None:
                    bad += 1
            if bad:
                reason = ("phase: %d reach rows in %s carry a post-injury or baseline label that "
                          "contradicts the cohort's own injury dates, so its phase labels were "
                          "derived wrongly and are left out" % (bad, cohort))
                ex.blank_cohort_fields[cohort] = {f: reason for f in PHASE_FIELDS}

    drop = out["video_name"].astype(str).isin(ex.drop_videos)
    for video, group in out[drop].groupby("video_name"):
        report.append(entry(left_out="reach rows", video_name=video,
                            subject_id=group["subject_id"].iloc[0],
                            date=group["test_date"].iloc[0], rows=int(len(group)),
                            fields="", reason=ex.drop_videos[str(video)]))
    # A copy, not a filtered view: columns are blanked and renumbered on it
    # below, and assigning into a view raised pandas' SettingWithCopyWarning
    # in every nightly export log (the values were right; the noise was not).
    out = out[~drop].copy()

    for video, blanks in ex.blank_fields.items():
        mask = out["video_name"].astype(str) == video
        if not mask.any():
            continue
        cols = [c for c in blanks if c in out.columns]
        if cols:
            # The columns being blanked are numeric; the absent value is a word.
            out[cols] = out[cols].astype(object)
            out.loc[mask, cols] = missing_word
            report.append(entry(left_out="columns", video_name=video,
                                subject_id=out.loc[mask, "subject_id"].iloc[0],
                                date=out.loc[mask, "test_date"].iloc[0], rows=int(mask.sum()),
                                fields=", ".join(cols), reason=blanks[cols[0]]))

    blanks = ex.blank_cohort_fields.get(cohort)
    if blanks and len(out):
        cols = [c for c in blanks if c in out.columns]
        if cols:
            out[cols] = out[cols].astype(object)
            out[cols] = missing_word
            report.append(entry(left_out="columns", video_name="(whole cohort)",
                                subject_id=cohort, date="", rows=int(len(out)),
                                fields=", ".join(cols), reason=blanks[cols[0]]))

    if "entry_index" in out.columns:
        # Renumber so the counter stays 1..N with no gaps: it exists so a sorted
        # spreadsheet can be put back in order, and gaps would read as missing rows.
        out["entry_index"] = range(1, len(out) + 1)
    return out, report
