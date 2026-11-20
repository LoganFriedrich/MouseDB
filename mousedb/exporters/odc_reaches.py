"""Per-reach ODC-SCI dataset: one row per reach, with the animal and session repeated.

WHY THIS EXISTS
---------------
Collaborators receive reaching data as ONE flat table per cohort: every row is a
reach, and every row also carries that animal's details (strain, sex, surgery
parameters, drugs, injury level ...) and that session's details (date, tray,
phase), so the file can be filtered and modelled without joins. Building it by
hand once took weeks and left the animal columns empty. This writes it from what
mousedb already holds, for every cohort, beside a data dictionary that describes
every column (an ODC-SCI upload fails without one).

Column blocks, in order:
  1. animal common data elements  (ODC_REACH_ANIMAL in data_dictionary)
  2. session                      (ODC_REACH_SESSION)
  3. tracking-sheet columns       every column of the animal-level tabs, as written,
                                  prefixed by tab: Metadata_, Contusion_, Injection_,
                                  ODC_ (see mousedb.animal_records)
  4. reach                        MouseReach's own reach columns (REACH_DATA)
  5. session totals               video and hand-score counts (ODC_REACH_TOTALS)

Inputs are the analysis SNAPSHOT tables (never connectome.db) plus the study-facts
file and the lab name. Nothing is guessed: a value no source records is empty.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

from . import csv_safety
from . import export_exclusions
from . import data_dictionary as dd
from . import extended_features as ext
from . import missing
from . import analysis_reaches
from . import analysis_sessions
from . import reach_summary
from . import trays
from . import layout
from . import levels

TAB_PREFIX = {"0a_Metadata": "Metadata", "3b_Manual_Tray": "Tray", "4_Contusion_Injury_Details": "Contusion",
              "5_SC_Injection_Details": "Injection", "ODC": "ODC"}
REACH_DROP = {"id", "extended_features"}
ANIMAL_COLUMNS = [r["VariableName"] for r in dd.ODC_REACH_ANIMAL]
SESSION_COLUMNS = [r["VariableName"] for r in dd.ODC_REACH_SESSION]
TOTAL_COLUMNS = [r["VariableName"] for r in dd.ODC_REACH_TOTALS]
DISPLACED = ("displaced_sa", "displaced_outside")

# Columns whose empty cells mean the question cannot apply to that row, not that
# anybody failed to answer it. Kept as a named list because each one is a judgement
# about the experiment, not about the data:
#   Days_Post_Injury    the session happened before the injury -- there is no day count
#   Exclusion_reason    the animal was not excluded, so there is no reason to give
#   interaction_frame   only the reach that met the pellet has a frame at which it did
NOT_APPLICABLE_WHEN_EMPTY = {
    "Days_Post_Injury": missing.NOT_APPLICABLE,
    "Exclusion_reason": missing.NOT_APPLICABLE,
    "interaction_frame": missing.NOT_APPLICABLE,
    # Not 'not applicable' but a judgement of the same kind, kept with them: the
    # day's total is empty only when a tray of that day is not filed (or the day
    # has no hand-score record to check against), so the count cannot be
    # committed to yet -- see day_reach_totals.
    "Day_Total_Reaches_AI": missing.UNDETERMINED,
    # The algorithm's day pellet totals are empty for the same reason, or
    # because the day had no pillar tray (day_outcome_totals).
    **{c: missing.UNDETERMINED for c in ("Day_Pellets_AI", "Day_Retrieved_AI",
                                         "Day_Displaced_AI", "Day_Contacted_AI",
                                         "Day_Untouched_AI")},
    # What the reach did is empty only where it is not known: the pellet was
    # not decided, or it was moved and no reach is credited with it
    # (levels.reach_outcome_codes).
    "outcome": missing.UNDETERMINED,
    "contact_group": missing.UNDETERMINED,
    # The session's phase is left empty while its cohort is still running
    # (running_cohorts says why): the label cannot be committed to until the
    # cohort's dates are all in, so it reads 'Undetermined', not 'Not recorded'.
    "Test_Type": missing.UNDETERMINED,
    "Test_Type_Grouped": missing.UNDETERMINED,
}

# Columns of the complete record that answer a question about a pellet. On an easy
# or flat tray none of them has an answer (trays.py says why), so there they read
# 'Not applicable' -- whatever the column's general word for an empty cell, and
# whatever the database put there (causal_reach is 0 on those rows, a claim about
# a pellet nobody tracked; the contact groups said 'missed' before 2026-10-26).
EASY_FLAT_NOT_APPLICABLE = trays.ALGO_PELLET_COLUMNS + (
    "Video_Pellets", "Video_Displaced", "Video_Retrieved", "Video_Contacted",
    "Session_Untouched_AI",
)

# Columns the dictionary does NOT mark as never-computed -- because a legacy detector
# did populate them -- but which no current detector fills, so they are empty on every
# row of every cohort we hold. 'Not measured' is the true word for such a row; the
# dictionary entry keeps the fuller story, including that the legacy value measured the
# sideways axis and should not be compared with anything here.
NOT_MEASURED_BY_CURRENT_DETECTOR = {
    "max_extent_pixels", "max_extent_ruler", "max_extent_mm",
}


def _blank(v) -> bool:
    if v is None:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    t = str(v).strip()
    # "NAME OF ..." is a placeholder a template left in a cell, not a value.
    return t == "" or t == "?" or t.upper().startswith("NAME OF")


def _first(d: dict, *keys) -> str:
    for k in keys:
        v = d.get(k)
        if not _blank(v):
            return str(v)
    return ""


def sheet_column(tab: str, record_no: int, field: str) -> str:
    base = TAB_PREFIX.get(tab, tab)
    return "%s%s_%s" % (base, "" if int(record_no) == 1 else int(record_no), field)


def lab_subject_id(subject_id: str) -> str:
    """The lab's own id: frozen letter cohorts are decoded (ASPA_11_03 -> K03)."""
    if subject_id.startswith("ASPA_"):
        from ..cohort_sheets import aspa_letter
        letter = aspa_letter(subject_id)
        if letter:
            return "%s%s" % (letter, subject_id.rsplit("_", 1)[1])
    return subject_id


def _date(v) -> Optional[date]:
    if _blank(v):
        return None
    try:
        return pd.to_datetime(str(v)).date()
    except (ValueError, TypeError):
        return None


# A cohort whose animals may still be alive. Anything a lab has not declared is
# decided by how long ago its last recorded session was -- a proxy, and only ever
# used to decide whether to stay SILENT about an animal's fate. It never asserts
# a death on its own.
#
# WHY the declaration has to win: a cohort can finish its behaviour and still be
# weeks away from its endpoint, and it can finish long ago while its last videos
# are still arriving. Recency cannot see either. In this lab's own records one
# cohort's last session was ten days old while the owner knew the cohort was
# finished -- so a tool deciding from dates alone would have called it live.
#
# WHY A MONTH: sessions run on a near-daily cadence, so even a fortnight of
# silence is longer than any gap inside a running study -- but a real study has
# real interruptions: a break between stages, a holiday, equipment down, a person
# away. A month clears all of those without having to enumerate them.
#
# Erring long is the cheap direction. The window never asserts anything: inside
# it a cohort is left UNANSWERED, so a window that is too long costs only a few
# blank cells, while one that is too short starts naming an end for animals that
# have not reached one. And the lab's own declaration overrides it either way, so
# the window only ever decides the cases nobody has spoken for.
STILL_ON_STUDY_DAYS = 30
STUDY_STATUS_FIELD = "Study_Status"


def _declared_study_status(cohort_id: str) -> str:
    """``Study_Status`` written against this exact cohort, if the lab wrote one.

    Read from the raw sections rather than through facts(), on purpose: facts()
    resolves by PROJECT, because a strain or a supplier is a property of a whole
    project. Whether a study has finished is a property of ONE COHORT -- CNT_06
    can be running while CNT_05 is done -- so it is looked up by the cohort's own
    name and deliberately does not inherit from the project.
    """
    from .. import study_facts as _sf
    try:
        section = (_sf.read_all() or {}).get(str(cohort_id), {})
    except Exception:
        return ""
    return str(section.get(STUDY_STATUS_FIELD, "")).strip().lower()


def _cohort_still_on_study(cohort_id: str, reaches, cohort_facts: dict) -> bool:
    """True when this cohort's animals must not be given a cause of death.

    The lab's own declaration first (``Study_Status`` against the cohort:
    ``in_progress`` or ``completed``). With nothing declared, fall back to
    recency, and fall back towards SILENCE: a cohort with no datable sessions
    counts as still on study, because the alternative is announcing that animals
    we know nothing about are dead.
    """
    declared = (_declared_study_status(cohort_id)
                or str(cohort_facts.get(STUDY_STATUS_FIELD, "")).strip().lower())
    if declared in ("in_progress", "in progress", "running", "active"):
        return True
    if declared in ("completed", "complete", "done", "finished"):
        return False

    import pandas as pd
    if reaches is None or len(reaches) == 0 or "video_name" not in reaches:
        return True
    when = pd.to_datetime(reaches["video_name"].astype(str).str.slice(0, 8),
                          format="%Y%m%d", errors="coerce").max()
    if pd.isna(when):
        return True
    return (pd.Timestamp.today().normalize() - when).days < STILL_ON_STUDY_DAYS


def running_cohorts(reach, facts=None, cohorts=None) -> List[str]:
    """The cohorts in ``reach`` that are still running, sorted.

    NO PHASE LABELS UNTIL THE COHORT IS OVER. A session's phase label is
    derived from the cohort's WHOLE set of dates (mousedb.phases): which day was
    the last baseline, which the first after injury, which rehabilitation. While
    a cohort is still running, dates that have not happened yet can move the
    labels of sessions already exported, and the earlier file would carry the
    old label with nothing to say it had changed. So every export leaves the
    phase EMPTY for every session of a running cohort, and fills it the first
    time the export runs after the cohort is over.

    "Running" is the one decision the exports already make for this purpose
    (_cohort_still_on_study, which also withholds the cause of death): the
    lab's own ``Study_Status`` for the cohort in the study-facts file
    (``in_progress`` / ``completed``) when it has written one, else a cohort
    whose last recorded session is less than STILL_ON_STUDY_DAYS old -- or that
    has no datable session at all -- counts as running. Nothing here invents a
    schedule or a completion rule: to release a cohort's labels early, declare
    it ``completed``; to hold them longer, declare it ``in_progress``.

    ``reach``: reach rows with ``subject_id`` and ``video_name`` (the dates are
    read from the video names, as for the cause of death, so every export
    reaches the same answer from the same rows). ``facts``: per-cohort study
    facts (default: mousedb.study_facts.facts). ``cohorts``: decide these
    cohorts too (default: every cohort in ``reach``) -- a cohort with hand
    scores and no reach row yet has no datable session here, so it counts as
    running unless the lab declared otherwise.
    """
    if facts is None:
        from ..study_facts import facts as facts
    has_reach = reach is not None and len(reach) > 0 and "subject_id" in reach
    wanted = set(cohorts_in(reach)) if has_reach else set()
    wanted |= {str(c) for c in (cohorts or ()) if str(c)}
    sids = reach["subject_id"].astype(str) if has_reach else None
    out = []
    for cid in sorted(wanted):
        rc = reach[sids.str.startswith(cid + "_")] if has_reach else pd.DataFrame()
        if _cohort_still_on_study(cid, rc, facts(cid) or {}):
            out.append(cid)
    return out


def cohort_of(subject_id) -> str:
    """The cohort part of a subject id (PROJ_01_07 -> PROJ_01); "" when it has none."""
    s = str(subject_id)
    return s.rsplit("_", 1)[0] if s.count("_") >= 2 else ""


def _animal(sid: str, cohort_id: str, sheet: dict, subject: dict, facts: dict,
            lab: str, still_on_study: bool = False) -> dict:
    # A study fact can have per-animal exceptions -- a colony that is one strain except
    # for the animals on a transgenic line, say. WHICH animals, and what their value
    # should be, are facts about one lab, so the rule lives in that lab's study-facts
    # file and is matched here against this animal's own recorded values.
    from .. import study_facts as _sf
    facts = _sf.apply_rules(cohort_id, facts, sheet)
    # WHAT HAPPENED, NOT WHETHER IT COUNTS.
    #
    # This used to read the per-surgery Survived columns and write "Total
    # exclusion" for any animal that died. That is a verdict, and it is not ours
    # to reach: nothing in the tracking sheet excludes an animal, and whether an
    # animal belongs in an analysis depends on the question being asked.
    #
    # It was also wrong in a way that costs data. An animal that died during its
    # injury surgery still has every pre-surgery reach it ever made -- 6,124 of
    # them in one cohort alone -- and those are ordinary baseline measurements.
    # Labelling them "Total exclusion" invites a reader to drop them.
    #
    # So the survival fact is carried plainly and the exclusion column is left to
    # whatever the sheet itself records, which at present is nothing.
    # A Survived flag counts only on a surgery that PROVABLY happened: one whose
    # weight on the day is typed. Survived is pre-filled on planned rows, and a
    # workbook copied from the previous cohort carries that cohort's deaths -- that
    # is how animals that had not yet been operated on were reported as not having
    # survived surgery. Unproven surgeries say nothing about survival.
    def _proven(key):
        weight = sheet.get(key[:-len("_Survived")] + "_Subject_Weight (g)")
        try:
            return float(str(weight).replace("g", "").replace(",", ".").strip()) > 0
        except (TypeError, ValueError):
            return False
    survived = [str(v).strip().upper() for k, v in sheet.items()
                if k.endswith("_Survived") and not _blank(v) and _proven(k)]
    died = any(s in ("N", "NO") for s in survived)
    if died:
        survival = "No"
    elif survived:
        survival = "Yes"
    else:
        survival = ""
    excl = _first(sheet, "ODC_Exclusion_in_origin_study", "Exclusion_in_origin_study")
    reason = _first(sheet, "ODC_Exclusion_reason", "Exclusion_reason")
    details = "; ".join("%s %s" % (label, sheet[k]) for k, label in (
        ("Contusion_Actual_kd", "force (kdyn)"), ("Contusion_Actual_displacement", "displacement (um)"),
        ("Contusion_Actual_Velocity", "velocity (mm/s)"), ("Contusion_Actual_Dwell", "dwell (s)"),
        ("ODC_Observed_Force (k dynes)", "force (kdyn)"), ("ODC_Observed_Displacement (u)", "displacement (um)"),
    ) if not _blank(sheet.get(k)))
    injury = _first(sheet, "Contusion_Surgery_Type", "ODC_Injury_type")
    intended = _first(sheet, "Contusion_Intended_kd")
    if injury and intended:
        injury = "%s-%skD" % (injury, intended)
    sex = subject.get("sex")
    if _blank(sex):
        sex = _first(sheet, "ODC_Organism_Sex", "Metadata_Sex", "Tray_Sex")
    sex = {"FEMALE": "F", "MALE": "M"}.get(str(sex).strip().upper(), str(sex).strip().upper() if sex else "")
    author = " ".join(x for x in (_first(sheet, "ODC_Author_First_Name"), _first(sheet, "ODC_Author_Last_Name")) if x)
    return {
        "SubjectID": lab_subject_id(sid),
        "SpeciesTyp": _first(sheet, "ODC_Organism_Name") or facts.get("SpeciesTyp", ""),
        "SpeciesStrainTyp": _first(sheet, "ODC_Organism_Strain") or facts.get("SpeciesStrainTyp", ""),
        "Animal_origin": _first(sheet, "ODC_Organism_Vendor") or facts.get("AnimalSourceNam", ""),
        "_age_at_surgery": _first(sheet, "ODC_Surgery_age"),
        "_dob": _date(subject.get("date_of_birth")) or _date(sheet.get("Metadata_Date_of_Birth")),
        "BodyWgtMeasrVal": _first(sheet, "Contusion_Subject_Weight (g)", "ODC_Surgery_weight"),
        "SexTyp": sex,
        "InjGroupAssignTyp": _first(sheet, "ODC_Study_Group") or cohort_id,
        "Laboratory": lab,
        "StudyLeader": facts.get("StudyLeader", "") or author,
        "Survived_All_Surgeries": survival,
        "Exclusion_in_origin_study": excl,
        "Exclusion_reason": reason,
        # Cause of death, in the order the records can actually answer it:
        #   1. the sheet, if it says;
        #   2. a surgery the animal did not survive -- the sheet records survival
        #      per surgery, so this is read, never inferred from how close a date
        #      is to a surgery day;
        #   3. the lab's standing meaning for a blank, from its study facts.
        # (3) matters because a blank here is not ignorance in most colonies: a
        # lab that euthanises at the end of study knows what a blank means, and
        # writing "Not recorded" in a shared file would assert nobody knows. What
        # the blank means is a fact about one lab, so it is configuration.
        # (4) and an animal that may still be alive gets NOTHING. Every animal
        # reaches an endpoint eventually, so the lab's default is right for
        # animals that have reached theirs -- and wrong, in the worst direction,
        # for one that has not.
        "Cause_of_Death": (_first(sheet, "ODC_Cause_of_Death", "Cause_of_Death")
                           or ("Did not survive a surgery (the tracking sheet "
                               "records Survived = N)" if died else "")
                           or ("" if still_on_study
                               else facts.get("Cause_of_Death", ""))),
        "Injury_device": _first(sheet, "ODC_Injury_device") or (facts.get("Injury_device", "") if injury else ""),
        # A recorded level always wins. Where none is recorded, a study fact may supply
        # one -- and because the level follows from the injury TYPE (a pyramidotomy is
        # at the medullary pyramids in any lab; where a lab puts a contusion follows
        # from what it studies), that fact is usually set by an exception rule keyed on
        # the type rather than written flat. Both live in the lab's config, not here.
        "Injury_level": (_first(sheet, "Contusion_Contusion_Location", "ODC_Spine_Injury_level")
                         or facts.get("Injury_level", "")),
        "Injury_details": details,
        "Injury_Type": injury,
        "_injury_date": _date(sheet.get("Contusion_Surgery_Date")) or _date(sheet.get("ODC_Surgery_Date")),
    }


DAY_COMPLETE = "yes"
DAY_INCOMPLETE = "no"


def _tray_keys(subject, day, tray_type, number) -> List[tuple]:
    """(subject, YYYY-MM-DD, tray letter, tray number) per row, normalised so the
    reach table's run_number and the hand scores' tray_number compare equal."""
    numbers = pd.to_numeric(pd.Series(list(number)), errors="coerce")
    return [(str(s), str(d), str(t).strip().upper(), None if pd.isna(n) else int(n))
            for s, d, t, n in zip(subject, day, tray_type, numbers)]


def day_reach_totals(rc: pd.DataFrame, test_date: pd.Series,
                     pellets: Optional[pd.DataFrame],
                     video_pellets: Optional[pd.DataFrame]):
    """(Day_Total_Reaches_AI, Day_Total_Reaches_Complete) for every reach row.

    WHY A DAY TOTAL. An earlier reach program reported "Total Swipes": the
    reaches one animal made in one testing session -- one date, every tray of
    it. This export's per-video count (Video_Reaches_AI, which was published as
    Total_Swipes_AI until 2026-11-17) counts one tray only, so it is not that
    number. This is: the reach rows of the animal on that date, all trays.

    WHY IT CAN BE EMPTY. Only filed videos have reach rows, so a day with a tray
    still in review (or never recorded) would give a smaller number that looks
    exactly like a real one. The trays the day SHOULD have are the ones the
    hand-score sheet records for that animal and date (scored at the bench the
    same day, before any video is processed). The day is complete when every
    one of them is filed: it has reach rows, or rows in the per-pellet table (a
    filed pillar tray at which the animal never reached counts, as 0). Then the
    total is given and the flag reads yes. Otherwise the flag reads no and the
    total is left empty -- a partial count is never reported as the day's.
    A day with no hand-score rows at all has nothing to check against and is
    not assumed complete. An easy or flat tray has no per-pellet rows, so one
    that is filed but has no reaches reads as not filed: that day reads no,
    the safe direction.
    """
    day = pd.Series(test_date, index=rc.index).astype(str)
    subject = rc["subject_id"].astype(str)
    filed = set(_tray_keys(subject, day, rc["tray_type"], rc["run_number"]))
    # The per-pellet table names each video's animal, date and tray itself; an
    # older one without those columns adds nothing here (its videos then count as
    # filed only through their reach rows, the safe direction).
    vp_keys = {"subject_id", "session_date", "tray_type", "run_number"}
    if (video_pellets is not None and not video_pellets.empty
            and vp_keys <= set(video_pellets.columns)):
        vp = video_pellets[video_pellets["subject_id"].astype(str).isin(set(subject))]
        if not vp.empty:
            vday = pd.to_datetime(vp["session_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            filed |= set(_tray_keys(vp["subject_id"], vday, vp["tray_type"], vp["run_number"]))
    expected: Dict[tuple, set] = {}
    if pellets is not None and not pellets.empty:
        p = pellets[pellets["subject_id"].astype(str).isin(set(subject))]
        if not p.empty:
            pday = pd.to_datetime(p["session_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            for key in set(_tray_keys(p["subject_id"], pday, p["tray_type"], p["tray_number"])):
                expected.setdefault(key[:2], set()).add(key)
    complete = {k: bool(trays_) and trays_ <= filed for k, trays_ in expected.items()}
    pairs = list(zip(subject, day))
    from collections import Counter
    per_day = Counter(pairs)
    counts = pd.Series([per_day[k] for k in pairs], index=rc.index)
    is_complete = pd.Series([complete.get(k, False) for k in pairs], index=rc.index)
    total = counts.where(is_complete).astype("Int64")
    flag = is_complete.map({True: DAY_COMPLETE, False: DAY_INCOMPLETE})
    return total, flag


# The day-level pellet totals, in the order they are written.
DAY_AI_COLUMNS = ("Day_Pellets_AI", "Day_Retrieved_AI", "Day_Displaced_AI",
                  "Day_Contacted_AI", "Day_Untouched_AI")
DAY_HAND_COLUMNS = ("Day_Pellets_Hand", "Day_Retrieved_Hand", "Day_Displaced_Hand",
                    "Day_Contacted_Hand", "Day_Untouched_Hand")


def day_outcome_totals(rc: pd.DataFrame, test_date: pd.Series, per_video: pd.DataFrame,
                       video_pellets: Optional[pd.DataFrame], pellets: Optional[pd.DataFrame],
                       day_complete: pd.Series) -> Dict[str, pd.Series]:
    """The animal's pellet totals over its whole day, on every reach row of it.

    WHY. Every per-reach row carries four levels (exporters.levels): the reach,
    its pellet, its tray, and its day. The day's reach count was there
    (Day_Total_Reaches_AI); what the day's pellets came to was not, from either
    the algorithm or the hand scores.

    WHICH TRAYS. Pillar trays only, in both streams. The algorithm decides no
    pellet on an easy or flat tray (trays.py), so a day total that counted their
    hand scores would not be comparable with the algorithm's, which is the point
    of carrying both. (The ODC day file, ODC_days_<cohort>.csv, counts every
    tray in its Daily_* columns; these columns do not.)

    WHEN THE ALGORITHM'S IS EMPTY. Exactly when the day's reach total is: a tray
    of the day not filed, or no hand-score record to check the day against
    (day_reach_totals) -- a partial day is never reported as the day. Also empty
    when a filed pillar tray of the day has no per-pellet rows to count (an old
    import), and on a day with no pillar tray at all.

    The hand scores' totals are given whenever the sheet holds pillar scores for
    the day: they are complete at the bench, independent of filing.
    """
    out: Dict[str, pd.Series] = {}
    subject = rc["subject_id"].astype(str)
    day = pd.Series(test_date, index=rc.index).astype(str)
    pairs = list(zip(subject, day))

    # Per pillar video of these animals: pellets cut, displaced, retrieved.
    # From the per-pellet table (every filed video, including one with no reach
    # rows), else from the reach rows' segments (pellets then unknown).
    videos: Dict[str, dict] = {}
    pv = per_video if per_video is not None else pd.DataFrame()
    first = rc.drop_duplicates("video_name")
    for v, s, d, t in zip(first["video_name"], first["subject_id"].astype(str),
                          day.loc[first.index], first["tray_type"]):
        videos[str(v)] = {"key": (s, d), "tray": str(t).strip().upper(), "n": None,
                          "disp": pv["disp"].get(v) if "disp" in pv else None,
                          "retr": pv["retr"].get(v) if "retr" in pv else None}
    need = {"video_name", "subject_id", "session_date", "tray_type", "outcome"}
    if video_pellets is not None and not video_pellets.empty and need <= set(video_pellets.columns):
        vp = video_pellets[video_pellets["subject_id"].astype(str).isin(set(subject))]
        if not vp.empty:
            vday = pd.to_datetime(vp["session_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            vp = vp.assign(_d=vday)
            for v, g in vp.groupby("video_name"):
                o = g["outcome"].astype(str)
                videos[str(v)] = {"key": (str(g["subject_id"].iloc[0]), str(g["_d"].iloc[0])),
                                  "tray": str(g["tray_type"].iloc[0]).strip().upper(),
                                  "n": int(len(g)), "disp": int(o.isin(DISPLACED).sum()),
                                  "retr": int((o == "retrieved").sum())}
    ai: Dict[tuple, Optional[tuple]] = {}
    for rec in videos.values():
        if rec["tray"] in trays.OUTCOME_FREE_TRAYS:
            continue
        k = rec["key"]
        vals = (rec["n"], rec["disp"], rec["retr"])
        if k in ai and ai[k] is None:
            continue
        if any(x is None or pd.isna(x) for x in vals):
            ai[k] = None
            continue
        prev = ai.get(k) or (0, 0, 0)
        ai[k] = tuple(int(a) + int(b) for a, b in zip(prev, vals))
    complete = (pd.Series(day_complete, index=rc.index).astype(str) == DAY_COMPLETE).tolist()

    def col(values):
        return pd.Series(pd.array(values, dtype="Int64"), index=rc.index)

    got = [ai.get(k) if ok else None for k, ok in zip(pairs, complete)]
    n = [g[0] if g else None for g in got]
    disp = [g[1] if g else None for g in got]
    retr = [g[2] if g else None for g in got]
    out["Day_Pellets_AI"] = col(n)
    out["Day_Retrieved_AI"] = col(retr)
    out["Day_Displaced_AI"] = col(disp)
    out["Day_Contacted_AI"] = out["Day_Displaced_AI"] + out["Day_Retrieved_AI"]
    out["Day_Untouched_AI"] = out["Day_Pellets_AI"] - out["Day_Contacted_AI"]

    hand: Dict[tuple, tuple] = {}
    if pellets is not None and not pellets.empty:
        p = pellets[pellets["subject_id"].astype(str).isin(set(subject))]
        p = p[~p["tray_type"].fillna("").astype(str).str.strip().str.upper()
              .isin(trays.OUTCOME_FREE_TRAYS)]
        if not p.empty:
            pday = pd.to_datetime(p["session_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            score = pd.to_numeric(p["score"], errors="coerce")
            for (s, d), sc in score.groupby([p["subject_id"].astype(str), pday]):
                hand[(s, d)] = (int(len(sc)), int((sc == 2).sum()), int((sc == 1).sum()),
                                int((sc == 0).sum()))
    h = [hand.get(k) for k in pairs]
    out["Day_Pellets_Hand"] = col([x[0] if x else None for x in h])
    out["Day_Retrieved_Hand"] = col([x[1] if x else None for x in h])
    out["Day_Displaced_Hand"] = col([x[2] if x else None for x in h])
    out["Day_Contacted_Hand"] = out["Day_Displaced_Hand"] + out["Day_Retrieved_Hand"]
    out["Day_Untouched_Hand"] = col([x[3] if x else None for x in h])
    return out


def build_cohort(cohort_id: str, reach: pd.DataFrame, subjects: pd.DataFrame,
                 records: pd.DataFrame, pellets: pd.DataFrame,
                 facts: Callable[[str], Dict[str, str]], lab: str = "",
                 video_pellets: Optional[pd.DataFrame] = None):
    """(dataframe, dictionary rows) for one cohort; an empty frame when it has no reaches."""
    rc = reach[reach["subject_id"].astype(str).str.startswith(cohort_id + "_")].copy()
    if rc.empty:
        return rc, []

    # 3. tracking-sheet columns, pivoted wide per animal, in tab order then sheet order
    sheet_cols: List[tuple] = []
    wide: Dict[str, dict] = {}
    if records is not None and not records.empty:
        rr = records[records["cohort_id"] == cohort_id].copy()
        if not rr.empty:
            order = {t: i for i, t in enumerate(TAB_PREFIX)}
            rr["_tab_order"] = rr["source_tab"].map(lambda t: order.get(t, len(order)))
            rr["_row"] = range(len(rr))
            rr = rr.sort_values(["_tab_order", "record_no", "_row"])
            rr["column"] = [sheet_column(t, n, f) for t, n, f in zip(rr["source_tab"], rr["record_no"], rr["field"])]
            seen = set()
            for t, n, f, c in zip(rr["source_tab"], rr["record_no"], rr["field"], rr["column"]):
                if c not in seen:
                    seen.add(c)
                    sheet_cols.append((c, t, f, int(n)))
            for sid, g in rr.groupby("subject_id"):
                wide[sid] = dict(zip(g["column"], g["value"]))

    subj = {}
    if subjects is not None and not subjects.empty:
        subj = {r["subject_id"]: r for r in subjects.to_dict("records")}
    on_study = _cohort_still_on_study(cohort_id, rc, facts(cohort_id) or {})
    animals = {sid: _animal(sid, cohort_id, wide.get(sid, {}), subj.get(sid, {}),
                            facts(sid) or {}, lab, on_study)
               for sid in rc["subject_id"].unique()}

    # 4 + 2. reach columns and the per-row session columns
    # Columns are collected in a dict and joined once (adding ~150 columns one by one
    # to a large frame is slow and fragments it).
    reach_cols = [c for c in rc.columns if c not in REACH_DROP]
    test_date = pd.to_datetime(rc["session_date"], errors="coerce")
    tray_id = rc["tray_type"].fillna("").astype(str) + rc["run_number"].astype("Int64").astype(str).replace("<NA>", "")
    a = pd.DataFrame([animals[s] for s in rc["subject_id"]], index=rc.index)
    cols: Dict[str, object] = {c: a[c] for c in ANIMAL_COLUMNS if c in a.columns}
    dob = pd.to_datetime(a["_dob"], errors="coerce")
    weeks = ((test_date - dob).dt.days / 7.0).round(1)
    cols["AgeVal"] = pd.Series([("%s" % w) if pd.notna(w) else s for w, s in zip(weeks, a["_age_at_surgery"])],
                               index=rc.index)
    cols["Test_Date"] = test_date.dt.strftime("%Y-%m-%d")
    cols["Tray_ID"] = tray_id
    cols["Session_ID"] = a["SubjectID"] + "-" + test_date.dt.strftime("%Y%m%d").fillna("") + "-" + tray_id
    cols["Injury_Type"] = a["Injury_Type"]
    cols["Test_Type"] = rc["test_phase"] if "test_phase" in rc else pd.Series("", index=rc.index)
    cols["Test_Type_Grouped"] = rc["phase_group"] if "phase_group" in rc else pd.Series("", index=rc.index)
    if on_study:
        # A running cohort's phase labels can still change as its later dates
        # arrive, so none is exported until it is over (running_cohorts says why).
        cols["Test_Type"] = pd.Series("", index=rc.index, dtype="object")
        cols["Test_Type_Grouped"] = pd.Series("", index=rc.index, dtype="object")
    dpi = (test_date - pd.to_datetime(a["_injury_date"], errors="coerce")).dt.days
    cols["Days_Post_Injury"] = dpi.where(dpi >= 0).astype("Int64")
    for c, _t, _f, _n in sheet_cols:
        cols[c] = pd.Series([wide.get(s, {}).get(c, "") for s in rc["subject_id"]], index=rc.index)
    for c in reach_cols:
        cols[c] = rc[c]
    # The reach's own outcome on EVERY reach, not only the credited one, and
    # the contact column derived from it so the two cannot disagree
    # (levels.reach_outcome_codes says why: naming the reach that moved a
    # pellet answers every other reach at it -- they missed).
    if "segment_outcome" in rc.columns:
        codes = levels.reach_outcome_codes(rc)
        if "outcome" in cols:
            cols["outcome"] = codes
        if "contact_group" in cols:
            cols["contact_group"] = levels.reach_contact(codes)

    # 5. session totals: per video (pipeline) and per tray (hand scores), and
    # the animal's reaches over the whole testing day (day_reach_totals says why)
    video = rc["video_name"]
    cols["Video_Reaches_AI"] = video.map(video.value_counts())
    cols["Day_Total_Reaches_AI"], cols["Day_Total_Reaches_Complete"] = day_reach_totals(
        rc, cols["Test_Date"], pellets, video_pellets)
    seg = rc.groupby(["video_name", "segment_num"]).agg(o=("segment_outcome", "first"),
                                                         att=("attention_score", "first")).reset_index()
    per_video = seg.groupby("video_name").agg(
        att=("att", "mean"),
        disp=("o", lambda s: int(s.isin(DISPLACED).sum())),
        retr=("o", lambda s: int((s == "retrieved").sum())))
    cols["Attention_AI"] = video.map(per_video["att"])
    # Pellet counts come from the per-pellet table when the video has rows there:
    # it holds EVERY pellet the pipeline cut the video into, so a pellet nobody
    # reached for is counted as presented, and a displaced or retrieved pellet
    # whose reach went undetected is still counted. Reach rows can only see
    # pellets that had a reach; they are the fallback for a video imported
    # before that table existed.
    disp, retr = per_video["disp"], per_video["retr"]
    found = pd.Series(dtype="float64")
    if video_pellets is not None and not video_pellets.empty:
        vp = video_pellets[video_pellets["video_name"].isin(set(video))]
        if not vp.empty:
            g = vp.groupby("video_name")["outcome"]
            found = g.size()
            disp = g.apply(lambda s: int(s.isin(DISPLACED).sum())).combine_first(disp)
            retr = g.apply(lambda s: int((s == "retrieved").sum())).combine_first(retr)
    cols["Video_Pellets"] = video.map(found).astype("Int64")
    cols["Video_Displaced"] = video.map(disp).astype("Int64")
    cols["Video_Retrieved"] = video.map(retr).astype("Int64")
    cols["Video_Contacted"] = cols["Video_Displaced"] + cols["Video_Retrieved"]
    # The tray's untouched pellets: presented (cut) and moved by nothing.
    cols["Session_Untouched_AI"] = cols["Video_Pellets"] - cols["Video_Contacted"]
    # Easy and Flat: pellets are presented but not tracked and no outcome is
    # computed (lab decision, 2026-10-23), so their pellet and outcome counts
    # are unknown, not zero. Blank them; the reach count and attention stay.
    outcome_free = trays.outcome_free(rc)
    for c in ("Video_Pellets", "Video_Displaced", "Video_Retrieved", "Video_Contacted",
              "Session_Untouched_AI"):
        cols[c] = cols[c].mask(outcome_free)
    empty = pd.Series(pd.array([pd.NA] * len(rc), dtype="Int64"), index=rc.index)
    for c in ("Manual_Pellets", "Manual_Displaced", "Manual_Retrieved", "Manual_Contacted",
              "Session_Untouched_Hand", "Contacted_Match"):
        cols[c] = empty
    if pellets is not None and not pellets.empty:
        p = pellets[pellets["subject_id"].astype(str).str.startswith(cohort_id + "_")].copy()
        if not p.empty:
            p["d"] = pd.to_datetime(p["session_date"], errors="coerce").dt.strftime("%Y-%m-%d")
            keys = ["subject_id", "d", "tray_type", "tray_number"]
            g = p.groupby(keys)["score"].agg(n="size", disp=lambda s: int((s == 1).sum()),
                                             retr=lambda s: int((s == 2).sum()),
                                             unt=lambda s: int((s == 0).sum())).reset_index()
            k = pd.DataFrame({"subject_id": rc["subject_id"], "d": cols["Test_Date"],
                              "tray_type": rc["tray_type"], "tray_number": rc["run_number"]})
            m = k.merge(g, on=keys, how="left")
            m.index = rc.index
            cols["Manual_Pellets"] = m["n"].astype("Int64")
            cols["Manual_Displaced"] = m["disp"].astype("Int64")
            cols["Manual_Retrieved"] = m["retr"].astype("Int64")
            cols["Manual_Contacted"] = cols["Manual_Displaced"] + cols["Manual_Retrieved"]
            cols["Session_Untouched_Hand"] = m["unt"].astype("Int64")
            cols["Contacted_Match"] = (cols["Manual_Contacted"] - cols["Video_Contacted"]).abs()

    # 5b. the day: the same totals over every pillar tray of the animal's day
    # (day_outcome_totals says why only pillar trays, and why the algorithm's
    # can be empty).
    cols.update(day_outcome_totals(rc, cols["Test_Date"], per_video, video_pellets, pellets,
                                   cols["Day_Total_Reaches_Complete"]))

    # 4b. the extended per-reach measurements, one column each. They go last because
    # they are the widest block and a reader scanning the header should meet identity,
    # then answers, then detail.
    ext_columns: List[str] = []
    ext_rows: List[dict] = []
    if ext.SOURCE_COLUMN in rc.columns:
        ext_keys = ext.keys_in(rc[ext.SOURCE_COLUMN])
        ext_frame = ext.expand(rc[ext.SOURCE_COLUMN], index=rc.index, keys=ext_keys)
        for c in ext_frame.columns:
            cols[c] = ext_frame[c]
        ext_columns = list(ext_frame.columns)
        ext_rows = ext.dictionary_rows(ext_keys)

    columns = (ANIMAL_COLUMNS + SESSION_COLUMNS + [c for c, *_ in sheet_cols]
               + reach_cols + TOTAL_COLUMNS + ext_columns)
    out = pd.concat([cols[c].rename(c) for c in columns], axis=1)
    sort = [c for c in ("SubjectID", "Test_Date", "video_name", "segment_num", "reach_num") if c in out.columns]
    out = out.sort_values(sort, kind="stable")

    reach_rows = {r["VariableName"]: r for r in dd.REACH_DATA}
    rows = (dd.ODC_REACH_ANIMAL + dd.ODC_REACH_SESSION
            + [dd.sheet_column_row(c, t, f, n) for c, t, f, n in sheet_cols]
            + [reach_rows[c] for c in reach_cols if c in reach_rows]
            + dd.ODC_REACH_TOTALS + ext_rows)
    return out, rows


def cohorts_in(reach: pd.DataFrame) -> List[str]:
    return sorted({s.rsplit("_", 1)[0] for s in reach["subject_id"].dropna().astype(str) if s.count("_") >= 2})


def cohort_label(cohort_id: str) -> str:
    """The cohort id as a person reads it in a file name.

    WHY: frozen letter cohorts are ENCODED for the pipeline (cohort number = the
    letter's alphabet position), so 'ASPA_04' means nothing to the person who knows
    that cohort as D. The label keeps the encoded id (it is what the database and
    the pipeline use) and adds the letter: ASPA_04 -> ASPA_04_D. Other ids unchanged."""
    if str(cohort_id).upper().startswith("ASPA_"):
        from ..cohort_sheets import aspa_letter
        letter = aspa_letter(cohort_id)
        if letter:
            return "%s_%s" % (cohort_id, letter)
    return cohort_id


def cohort_source(cohort_id: str) -> str:
    """Which workbook a letter cohort came from (its file name), or '' if not found."""
    if not str(cohort_id).upper().startswith("ASPA_"):
        return ""
    try:
        from ..cohort_sheets import aspa_letter, find_aspa_sheet
        sheet = find_aspa_sheet(cohort_id)
        return "cohort %s, workbook %s" % (aspa_letter(cohort_id), sheet.name) if sheet else \
               "cohort %s" % aspa_letter(cohort_id)
    except Exception:
        return ""


def archive_old_names(out_dir: Path, old_names: List[str], label: str) -> List[str]:
    """Move files left under a superseded name out of the exports folder (never deleted):
    into <mousedb root>/_archived/exports_current/<timestamp>_<label>/ when out_dir is the
    usual exports/current, else into out_dir/_superseded/."""
    from datetime import datetime
    out_dir = Path(out_dir)
    moved = []
    for name in old_names:
        f = out_dir / name
        if not f.exists():
            continue
        if out_dir.name == "current" and out_dir.parent.name == "exports":
            dest = out_dir.parent.parent / "_archived" / "exports_current"
        else:
            dest = out_dir / "_superseded"
        dest = dest / ("%s_%s" % (datetime.now().strftime("%Y-%m-%d_%H%M%S"), label))
        # A name may be a path inside the folder (CNT/CNT_05/x.csv): kept as such.
        (dest / name).parent.mkdir(parents=True, exist_ok=True)
        f.replace(dest / name)
        moved.append(str(dest / name))
    return moved


def _write(df: pd.DataFrame, rows: List[dict], data: Path,
           dictionary: Optional[Path] = None, names: Optional[Dict[str, str]] = None) -> List[dict]:
    """One table, written through a temporary name; returns its dictionary rows.

    The dictionary is written beside the table only when ``dictionary`` is
    given. The per-cohort documents do not pass one: every cohort's file of a
    kind shares ONE dictionary, which export() assembles from the rows returned
    here (see _merge_dictionary).

    WHY the temporary: these files are read by people and by the next import while the
    export runs, and a half-written CSV that still has yesterday's name is worse than
    one that is briefly absent.

    WHY THE RENAME HAPPENS HERE: a table and its dictionary have to be renamed in the
    same breath or the dictionary is left describing a column name that no longer
    exists, and this is the last moment both are in hand. Doing it earlier would be
    worse than untidy: the tracking-sheet values are looked up BY their sheet header
    while the table is being built (see _animal), so a rename before that point would
    break the lookups. (write_cohort applies the same rule to the analysis documents,
    which it writes itself.)
    """
    # The published, level-first names (exporters.levels), renamed with their
    # dictionary rows for the same reason.
    if names:
        df, rows = levels.publish(df, rows, names)
    df, rows = dd.apply_odc_names(df, rows)
    # And made safe to read back: a comma inside a data cell is the classic way a
    # CSV survives being written and dies in someone else's reader, and the
    # tracking sheets put commas in cells ("Pyramid, left"). See csv_safety --
    # every substitution is recorded in that column's own dictionary entry.
    df, changes = csv_safety.make_frame_safe(df)
    rows = csv_safety.note_substitutions(rows, changes)
    rows = csv_safety.make_rows_safe(rows)
    tmp = data.with_name(data.name + ".tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(data)
    if dictionary is not None:
        dd.write_rows(rows, dictionary)
    return rows


# One dictionary per KIND of document, never one per cohort. Every cohort's file
# of a kind has the same columns -- or should: where a cohort's tracking sheet
# adds columns another's lacks, the shared dictionary carries the union and the
# manifest names each variable that only some cohorts' files have, so that
# difference stays visible until it is resolved at the sheets.
SHARED_DICTIONARIES = {
    "complete": "ODC_reaches_DATA_DICTIONARY.csv",
    "summary": "reaches_summary_DATA_DICTIONARY.csv",
}
# The per-cohort names those replaced (2026-10-16); archived when found.
_PER_COHORT_DICTIONARY = re.compile(
    r"^(ODC_reaches_.+|reaches_.+_summary)_DATA_DICTIONARY\.csv$")


def _merge_dictionary(order: List[str], merged: Dict[str, dict], rows: List[dict],
                      label: str, seen_in: Dict[str, List[str]],
                      conflicts: List[str]) -> None:
    """Fold one cohort's dictionary rows into the shared one, in place.

    A variable met for the first time is placed straight after the variable
    that precedes it in this cohort's own list, so the shared dictionary reads
    in the same order as the files. A variable already present keeps its entry;
    if this cohort's entry says something different, the Comments are combined
    and any other difference is reported in ``conflicts`` -- one definition per
    variable is the point of a shared dictionary, and two that disagree is the
    deeper problem it exposes, not one to paper over.
    """
    prev = None
    for row in rows:
        name = row["VariableName"]
        seen_in.setdefault(name, []).append(label)
        if name not in merged:
            merged[name] = dict(row)
            at = order.index(prev) + 1 if prev in merged and prev in order else len(order)
            order.insert(at, name)
        else:
            old = merged[name]
            fields = sorted(k for k in set(old) | set(row)
                            if k not in ("VariableName", "Comments") and old.get(k) != row.get(k))
            if fields:
                conflicts.append("%s: %s's entry differs in %s; the shared dictionary carries %s's"
                                 % (name, label, ", ".join(fields), seen_in[name][0]))
            a, b = str(old.get("Comments", "")), str(row.get("Comments", ""))
            if b and b not in a:
                old["Comments"] = b if a in b else (a + " " + b).strip()
        prev = name


def write_cohort(out_dir: Path, cohort_id: str, df: pd.DataFrame, rows: List[dict],
                 exclusions=None, analysis_only: bool = False) -> dict:
    """Write the documents for one cohort. Their data dictionaries are NOT written
    here: every cohort's document of a kind shares one dictionary, which export()
    assembles from the rows this returns (``_complete_dictionary`` and friends).

    Three files, because one table cannot be all three things:
      ODC_reaches_<label>.csv          the complete record -- every measurement we hold,
                                       including how it was produced and by whom
      reaches_<label>_summary.csv      the shareable table -- what happened, in words,
                                       no empty cells, no pipeline internals
      reaches_<label>_analysis.csv     the analysis table -- the same reaches in the
                                       shape an analysis collaborator sent back, so
                                       their existing work runs against it: numbers in
                                       numeric columns, NA for absent, nothing to join

    The first two spell an absent value as a WORD saying why it is absent, which an
    ODC upload requires and a person reading the file wants. That word is also what
    stops a statistics package loading the column as numbers, which is why the third
    exists rather than one of the first two being bent to cover it.
    """
    root = Path(out_dir)
    label = cohort_label(cohort_id)
    # Each cohort's documents live in its own folder inside its project's
    # (exporters.layout says why); out_dir is the top of the exports folder.
    out_dir = layout.cohort_dir(root, cohort_id, label)
    out_dir.mkdir(parents=True, exist_ok=True)

    # The complete record carries no empty cells either: an ODC-SCI upload refuses them.
    # WHICH word is not guessed from emptiness. A column is 'Not measured' only when its
    # own dictionary entry says no code computes it; a column a source failed to fill is
    # 'Not recorded', and a question that cannot apply to the row is 'Not applicable'.
    # Inferring the word from emptiness got this wrong in exactly the way that matters:
    # the frozen ASPA workbooks record no species, so SpeciesTyp is empty in those
    # cohorts, and an emptiness rule labelled a mouse's species 'Not measured'.
    never = dd.never_computed_names() | NOT_MEASURED_BY_CURRENT_DETECTOR
    reasons = {c: missing.NOT_MEASURED for c in never if c in df.columns}
    for column, reason in NOT_APPLICABLE_WHEN_EMPTY.items():
        if column in df.columns:
            reasons[column] = reason
    # analysis_only skips the complete record and the shareable table. It exists
    # for the case where only the analysis documents are needed in a hurry: those
    # two are most of the bytes written, and writing them was most of an hour.
    summary = pd.DataFrame()
    complete_dictionary, summary_dictionary = [], []
    if not analysis_only:
        full = missing.fill_frame(df, reasons)
        trays.blank_where(full, trays.outcome_free(df), EASY_FLAT_NOT_APPLICABLE,
                          missing.NOT_APPLICABLE)
        full_rows = [dict(r, Comments=" ".join(x for x in (
                         r.get("Comments", ""),
                         missing.dictionary_comment(reasons, r["VariableName"]),
                         (trays.NOTE if r["VariableName"] in EASY_FLAT_NOT_APPLICABLE
                          and trays.NOTE not in r.get("Comments", "") else ""))
                         if x).strip())
                     for r in rows]
        # No dictionary per cohort: export() writes one per kind from these rows.
        complete_dictionary = _write(full, full_rows, out_dir / ("ODC_reaches_%s.csv" % label),
                                     names=levels.complete_names(full.columns))

        summary, summary_rows = reach_summary.build(df)
        summary_dictionary = _write(summary, summary_rows,
                                    out_dir / ("reaches_%s_summary.csv" % label),
                                    names=levels.with_reach_prefix(
                                        levels.SUMMARY,
                                        reach_summary.measurement_columns(summary.columns)))

    # The analysis documents have the SAME columns for every cohort -- unlike the
    # complete record, whose columns follow each cohort's own tracking sheet. So
    # their dictionary is written once for all of them (see export), not thirteen
    # identical times under thirteen names.
    analysis, analysis_rows = analysis_reaches.build(df)
    # Entries with an identified problem are left out of the analysis documents,
    # at the smallest scope that contains the problem (see export_exclusions). The
    # sessions file is built from these rows, so it inherits the same exclusions.
    exclusion_report = []
    if exclusions is not None:
        before = analysis
        analysis, exclusion_report = export_exclusions.apply(analysis, exclusions,
                                                             cohort_id=cohort_id)
        analysis = analysis_reaches.void_day_totals_after_exclusion(before, analysis)
    # The sessions file is built from the analysis rows under their internal
    # names; both are published under the level-first names together below.
    sessions, session_rows = analysis_sessions.build(analysis, analysis_rows)
    (analysis, analysis_rows, sessions, session_rows,
     exclusion_report) = analysis_reaches.publish(analysis, analysis_rows, sessions,
                                                  session_rows, exclusion_report)
    # Named in code, so legal already -- but passed through the same rule as every
    # other document, so that stays true of a column added later by someone who has
    # not read ODC's naming guide.
    analysis, analysis_rows = dd.apply_odc_names(analysis, analysis_rows)
    analysis, changes = csv_safety.make_frame_safe(analysis)
    analysis_rows = csv_safety.make_rows_safe(
        csv_safety.note_substitutions(analysis_rows, changes))
    analysis.to_csv(out_dir / ("reaches_%s_analysis.csv" % label), index=False)

    sessions_file = out_dir / ("sessions_%s_analysis.csv" % label)
    if len(sessions):
        sessions, session_rows = dd.apply_odc_names(sessions, session_rows)
        sessions, changes = csv_safety.make_frame_safe(sessions)
        session_rows = csv_safety.make_rows_safe(
            csv_safety.note_substitutions(session_rows, changes))
        sessions.to_csv(sessions_file, index=False)
    elif sessions_file.exists():
        # Every reach of this cohort was left out, so there are no sessions to
        # write -- and a sessions file from an earlier run beside an empty reach
        # file would claim sessions that are not there. Archived, never deleted.
        archive_old_names(root, [layout.key(root, sessions_file)], label)

    documented = {r["VariableName"] for r in rows}
    entry = {"rows": int(len(df)), "columns": int(len(df.columns)),
             "summary_columns": int(len(summary.columns)),
             "never_measured_columns": sorted(reasons),
             "undocumented_columns": [c for c in df.columns if c not in documented],
             "blank_animal_columns": [c for c in ANIMAL_COLUMNS if df[c].replace("", pd.NA).isna().all()]}
    entry["analysis_columns"] = int(len(analysis.columns))
    entry["analysis_rows"] = int(len(analysis))          # after the exclusion pass
    entry["session_rows"] = int(len(sessions))
    entry["session_columns"] = int(len(sessions.columns)) if len(sessions) else 0
    entry["_analysis_dictionary"] = analysis_rows
    entry["_sessions_dictionary"] = session_rows
    entry["_complete_dictionary"] = complete_dictionary
    entry["_summary_dictionary"] = summary_dictionary
    entry["_exclusion_report"] = exclusion_report
    if label != cohort_id:
        entry["cohort"] = cohort_source(cohort_id) or label
        moved = archive_old_names(root, ["ODC_reaches_%s.csv" % cohort_id,
                                            "ODC_reaches_%s_DATA_DICTIONARY.csv" % cohort_id], label)
        if moved:
            entry["archived_old_name"] = moved
    return entry, label


def session_weights(snapshot_dir) -> "pd.DataFrame":
    """The hand-recorded body weight for each animal on each day, or empty.

    Absent on an older snapshot, which is not an error: the table was added to
    the snapshot on 2026-10-14 and a snapshot taken before that simply has no
    weights. The columns are then missing from the export rather than wrong,
    which is the behaviour every other optional table already has.
    """
    path = Path(snapshot_dir) / "weights.parquet"
    if not path.exists():
        return pd.DataFrame()
    weights = pd.read_parquet(path)
    keep = [c for c in ("subject_id", "date", "weight_grams", "weight_percent")
            if c in weights.columns]
    if len(keep) < 4:
        return pd.DataFrame()
    weights = weights[keep].copy()
    weights["date"] = weights["date"].astype(str).str[:10]
    # weight_percent is stored in TWO UNITS, depending on which import path wrote
    # it: cohorts whose weights came from the transposed weight grid and the tray
    # tab hold a fraction (0.87), while cohorts imported through the vertical
    # weight tab hold the sheet's own percentage (87). Multiplying everything by
    # 100 -- what this did until 2026-10-15 -- turned one cohort's 90% into 9,000%.
    #
    # The value itself says which it is, and unambiguously: no animal is kept at
    # 2% of its free-feeding weight, and none weighs 200% of it. So anything at or
    # below 2 is a fraction and is scaled; anything above is already a percentage.
    # This is the same threshold the importer's tray path uses, and it stays here
    # until the import boundary stores one unit (see the sheet ingest plan, K5).
    pct = pd.to_numeric(weights["weight_percent"], errors="coerce")
    weights["weight_percent"] = pct.where(pct > 2, pct * 100.0)
    # One weight per animal per day. Where a day somehow holds two, the first is
    # taken and the duplicate dropped rather than the reach rows being doubled
    # by the join -- a silent row explosion is far worse than a repeated weight.
    return weights.drop_duplicates(subset=["subject_id", "date"], keep="first")


def attach_weights(reach: "pd.DataFrame", weights: "pd.DataFrame") -> "pd.DataFrame":
    """Add Session_Weight_g and Session_Weight_Pct to the reach rows."""
    if reach.empty or weights.empty:
        return reach
    if "subject_id" not in reach.columns or "session_date" not in reach.columns:
        return reach
    out = reach.copy()
    out["_day"] = out["session_date"].astype(str).str[:10]
    joined = out.merge(
        weights.rename(columns={"date": "_day",
                                "weight_grams": "Session_Weight_g",
                                "weight_percent": "Session_Weight_Pct"}),
        on=["subject_id", "_day"], how="left")
    joined.index = out.index
    return joined.drop(columns=["_day"])


def load_snapshot(snapshot_dir: Path) -> dict:
    """The snapshot tables this export reads; animal_records may be absent (older snapshot)."""
    import pyarrow.parquet as pq
    snapshot_dir = Path(snapshot_dir)
    names = pq.read_schema(snapshot_dir / "reach_data.parquet").names
    # Only the surrogate key is left behind. extended_features IS read: it carries the
    # per-reach measurements (174 from extractor 2.6.0) the complete document exists to contain (see
    # mousedb.exporters.extended_features for what dropping it used to cost).
    t = {"reach": pd.read_parquet(snapshot_dir / "reach_data.parquet",
                                  columns=[c for c in names if c != "id"])}
    for key, name in (("subjects", "subjects"), ("pellets", "pellet_scores"), ("records", "animal_records"),
                      ("video_pellets", "video_pellets")):
        f = snapshot_dir / ("%s.parquet" % name)
        t[key] = pd.read_parquet(f) if f.exists() else None
    # The day's body weight rides on the reach rows from here on, so every
    # document built from them carries it and none can be the one that forgot.
    t["reach"] = attach_weights(t["reach"], session_weights(snapshot_dir))
    return t


class _Incremental:
    """The bookkeeping of an incremental export (exporters.incremental says why).

    Disabled (``enabled`` False) it keeps nothing and writes no ledger: every
    cohort is rebuilt and every combined file written, exactly as before.
    Enabled, it decides per cohort whether its files can be kept, records what
    each rebuilt cohort contributed, and at the end writes the ledger and the
    manifest's "incremental" record."""

    def __init__(self, final_dir: Path, t: dict, wanted: List[str], exclusions, lab: str,
                 enabled: bool, rebuild_all: bool = False):
        from . import incremental
        from .current import exporter_code_hash
        self.final_dir, self.enabled = Path(final_dir), enabled
        self.started = __import__("datetime").datetime.now().isoformat(timespec="seconds")
        self.old: dict = {}
        self.why_full: Optional[str] = None
        self.records: Dict[str, dict] = {}
        self.kept_ids: List[str] = []
        self.rebuilt_ids: List[str] = []
        self.failed_ids: set = set()
        self.fps: Dict[str, dict] = {}
        self.kept_combined: Dict[str, dict] = {}
        self.gone: List[str] = []
        if not enabled:
            return
        self.code = exporter_code_hash()
        if rebuild_all:
            self.why_full = "a full rebuild was asked for"
        else:
            self.old, self.why_full = incremental.load_ledger(self.final_dir, self.code)
        # Fingerprints are taken before any cohort is written: writing one adds
        # notes and whole-cohort blanks to ``exclusions``.
        self.fps = incremental.cohort_fingerprints(t, wanted, exclusions, self.final_dir, lab)

    def kept(self, cid: str) -> Optional[dict]:
        """The ledger's record of ``cid`` (entry and notes, ready to use) when its
        files can be kept; None when it must be rebuilt."""
        from . import incremental
        if not self.enabled or self.why_full:
            return None
        rec = (self.old.get("cohorts") or {}).get(cid)
        if (not rec or rec.get("fingerprint") != self.fps[cid]["fingerprint"]
                or not incremental.files_match(self.final_dir, rec.get("files"))):
            return None
        # ... and no file of the cohort's kinds beside them that the ledger does
        # not know of (put there by hand, or left by an older layout).
        label = cohort_label(cid)
        on_disk = {layout.key(self.final_dir, layout.cohort_file(self.final_dir, k, cid, label))
                   for k in layout.REACH_KINDS
                   if layout.cohort_file(self.final_dir, k, cid, label).is_file()}
        if on_disk != set(rec["files"]):
            return None
        self.records[cid] = rec
        self.kept_ids.append(cid)
        return {"entry": incremental.normalized(rec["entry"]), "notes": list(rec.get("notes", []))}

    def rebuilt(self, cid: str, entry: dict, notes: List[str]) -> dict:
        """Record a cohort written this run; return its entry as the ledger will
        hold it (normalized), for the caller to use in place of the original."""
        from . import incremental
        entry = incremental.normalized(entry)
        self.rebuilt_ids.append(cid)
        if self.enabled:
            fp = self.fps[cid]
            self.records[cid] = {"fingerprint": fp["fingerprint"], "parts": fp["parts"],
                                 "videos": fp["videos"], "entry": incremental.normalized(entry),
                                 "notes": list(notes), "written_at": self.started}
        return entry

    def failed(self, cid: str) -> None:
        self.failed_ids.add(cid)
        self.records.pop(cid, None)

    def rebuilt_cohorts(self) -> List[str]:
        return [c for c in self.rebuilt_ids if c not in self.failed_ids]

    def combine_plan(self, projects: set, gone: set, build_failed: set):
        """(projects whose combined files are written this run, manifest entries
        of the combined files kept as they are)."""
        from . import incremental
        self.gone = sorted(gone)
        if not self.enabled or self.why_full:
            return set(projects), {}
        touched = {layout.project_of(c) for c in
                   set(self.rebuilt_ids) | set(gone) | set(build_failed) | self.failed_ids}
        old = self.old.get("combined") or {}
        kept_entries: Dict[str, dict] = {}
        write = set()
        for project in sorted(set(projects)):
            if project in touched:
                write.add(project)
                continue
            mine = {}
            for kind in layout.REACH_KINDS:
                name = layout.key(self.final_dir, layout.combined_file(self.final_dir, kind, project))
                rec = old.get(name)
                has_cohort_files = bool(layout._cohort_files(self.final_dir, kind, project))
                if not has_cohort_files and rec is None:
                    continue
                if rec is None or incremental.stamp(self.final_dir / name) != rec.get("stamp"):
                    mine = None
                    break
                mine[name] = rec
            if mine is None:
                write.add(project)
            else:
                for name, rec in mine.items():
                    kept_entries[name] = incremental.normalized(rec["entry"])
                    self.kept_combined[name] = rec
        return write, kept_entries

    def combined_reuse(self) -> Optional[Dict[str, dict]]:
        if not self.enabled or self.why_full:
            return None
        return self.old.get("combined") or {}

    def finish(self, manifest: dict, combined_records: Dict[str, dict]) -> None:
        """Write the ledger (only now, with every file in place) and the
        manifest's "incremental" record."""
        from . import incremental
        if not self.enabled:
            return
        for cid in self.rebuilt_cohorts():
            rec = self.records.get(cid)
            if rec is None:
                continue
            label = cohort_label(cid)
            rec["files"] = {}
            for kind in layout.REACH_KINDS:
                f = layout.cohort_file(self.final_dir, kind, cid, label)
                if f.is_file():
                    rec["files"][layout.key(self.final_dir, f)] = incremental.stamp(f)
        # A combined file that failed is not in combined_records (combine_all
        # leaves it out), so it is written whole next time.
        combined = dict(self.kept_combined)
        combined.update(combined_records)
        ledger = {"format": incremental.LEDGER_FORMAT, "code": self.code,
                  "saved_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                  "cohorts": {c: r for c, r in self.records.items()
                              if c not in self.failed_ids and r.get("files")},
                  "combined": combined}
        rewritten = {}
        for cid in self.rebuilt_cohorts():
            old = (self.old.get("cohorts") or {}).get(cid)
            fp = self.fps.get(cid, {})
            why = incremental.changed_parts(old and old.get("parts"), fp.get("parts", {}))
            if old and old.get("fingerprint") == fp.get("fingerprint"):
                why = ["its files on disk are not the ones the ledger recorded"]
            rewritten[cid] = dict({"why": self.why_full and [self.why_full] or why},
                                  **incremental.video_changes(old and old.get("videos"),
                                                              fp.get("videos", {})))
        manifest["incremental"] = {
            "mode": "full" if self.why_full else "incremental",
            "why_full": self.why_full,
            "at": self.started,
            "cohorts_rewritten": rewritten,
            "cohorts_kept": {c: self.records[c].get("written_at") for c in self.kept_ids},
            "cohorts_failed": sorted(self.failed_ids),
            "cohorts_removed": list(self.gone),
            "combined_rewritten": sorted(combined_records),
            "combined_kept": sorted(self.kept_combined),
            "combined_front_kept": {k: v.get("kept_cohorts", 0) for k, v in combined_records.items()
                                    if v.get("kept_cohorts")},
        }
        try:
            incremental.save_ledger(self.final_dir, ledger)
        except OSError as e:
            manifest["problems"].append(
                "the export ledger could not be saved (%s: %s); the next refresh rebuilds "
                "every file" % (type(e).__name__, e))
            problem = incremental.drop_ledger(self.final_dir)
            if problem:
                manifest["problems"].append(problem)
                manifest.setdefault("failures", []).append(problem)


def export(snapshot_dir: Path, out_dir: Path, cohorts: Optional[List[str]] = None,
           manifest: Optional[dict] = None, analysis_only: bool = False,
           staging_dir: Optional[Path] = None, incremental: bool = False,
           rebuild_all: bool = False) -> dict:
    """Write the per-reach ODC files for ``cohorts`` (all with reaches if None).

    staging_dir: write every file there first and copy the finished set into
    out_dir at the end. Writing a table straight to a network share runs at a
    small fraction of local-disk speed; copying a finished file is far cheaper
    than formatting it onto the share a number at a time.

    incremental: rewrite only the cohorts whose inputs changed, keeping the
    rest (exporters.incremental; the nightly refresh and the Where Is My Data
    button use it). rebuild_all: with incremental, ignore the ledger and
    rebuild everything (then write a new ledger).
    """
    from .. import study_facts
    from ..config import lab_name
    manifest = manifest if manifest is not None else {"files": {}, "problems": []}
    t = load_snapshot(snapshot_dir)
    if t["records"] is None:
        msg = ("ODC_reaches: the snapshot has no animal_records table yet (it appears after the "
               "next tracking-sheet import and snapshot); sheet columns are missing from these files")
        manifest["problems"].append(msg)
        # Follows from the snapshot alone: see "from_inputs" below.
        manifest.setdefault("from_inputs", []).append(msg)
    wanted = [c.upper() for c in cohorts] if cohorts else cohorts_in(t["reach"])
    analysis_dict, sessions_dict = {}, {}
    # The two document kinds whose columns can follow a cohort's own sheet:
    # merged in file order, with who-has-what recorded (see _merge_dictionary).
    shared = {kind: {"order": [], "rows": {}, "seen_in": {}, "conflicts": []}
              for kind in SHARED_DICTIONARIES}
    final_dir = Path(out_dir)
    write_dir = Path(staging_dir) if staging_dir else final_dir
    write_dir.mkdir(parents=True, exist_ok=True)

    # The exclusion checks run once, against the whole snapshot, before any cohort
    # is written. If they cannot run, the analysis files are NOT written: shipping
    # them unfiltered would put entries already known to be wrong in front of the
    # people analysing them, which is the one outcome the checks exist to prevent.
    try:
        from ..config import get as _cfg, mousereach_pipeline_root
        exclusions = export_exclusions.compute(
            snapshot_dir, mousereach_pipeline_root(), _cfg("cohort_sheets_dir"))
    except Exception as e:
        msg = ("analysis exclusions could not be computed (%s: %s) -- the analysis "
               "files were not written rather than written unfiltered" % (type(e).__name__, e))
        manifest["problems"].append(msg)
        # "failures": problems that mean a file is missing or stale -- the
        # export is incomplete (exporters.current), not merely annotated.
        manifest.setdefault("failures", []).append(msg)
        return manifest
    exclusion_report = []
    built_empty, build_failed = set(), set()
    # INCREMENTAL (exporters.incremental says why and how): a cohort whose
    # fingerprint and files on disk are exactly what the ledger recorded is
    # KEPT -- its files stay, and what it contributes to the manifest, the
    # shared dictionaries and the exclusions report is replayed from the
    # ledger, so the result is the same as rebuilding it. Only a whole export
    # (no ``cohorts``) is incremental; a named-cohort export rebuilds what it
    # names, as before.
    inc = _Incremental(final_dir, t, wanted, exclusions, lab_name(),
                       enabled=incremental and not cohorts and not analysis_only,
                       rebuild_all=rebuild_all)
    for cid in wanted:
        try:
            label = cohort_label(cid)
            # The name of each document in the manifest is its path inside the
            # exports folder (CNT/CNT_05/ODC_reaches_CNT_05.csv).
            name = lambda kind: layout.key(final_dir, layout.cohort_file(final_dir, kind, cid, label))
            kept = inc.kept(cid)
            if kept is not None:
                entry = kept["entry"]
                exclusions.notes.extend(kept["notes"])
            else:
                df, rows = build_cohort(cid, t["reach"], t["subjects"], t["records"], t["pellets"],
                                        study_facts.facts, lab_name(),
                                        video_pellets=t.get("video_pellets"))
                if df.empty:
                    manifest["files"][name("ODC_reaches")] = {"rows": 0, "note": "no reaches"}
                    built_empty.add(cid)
                    continue
                notes_before = len(exclusions.notes)
                entry, label = write_cohort(write_dir, cid, df, rows, exclusions=exclusions,
                                            analysis_only=analysis_only)
                del df
                # As it reads back from the ledger: a rebuilt cohort and a kept
                # one then contribute exactly the same values (incremental.normalized).
                entry = inc.rebuilt(cid, entry, exclusions.notes[notes_before:])
            exclusion_report.extend(entry.pop("_exclusion_report", []) or [])
            for key, store in (("_analysis_dictionary", analysis_dict),
                               ("_sessions_dictionary", sessions_dict)):
                for row in entry.pop(key, []) or []:
                    store.setdefault(row["VariableName"], row)
            for kind in SHARED_DICTIONARIES:
                s = shared[kind]
                _merge_dictionary(s["order"], s["rows"], entry.pop("_%s_dictionary" % kind, []) or [],
                                  label, s["seen_in"], s["conflicts"])
            if not analysis_only:
                entry["dictionary"] = SHARED_DICTIONARIES["complete"]
            manifest["files"][name("ODC_reaches")] = entry
            # Each document under its own name. The counts above describe them
            # too, but a reader of the manifest (and the Where Is My Data tab)
            # looks for a file by its name, and a report that is not listed by
            # name is a report nobody knows is there (2026-10-16).
            if not analysis_only:
                manifest["files"][name("reaches_summary")] = {
                    "rows": entry["rows"], "columns": entry["summary_columns"],
                    "dictionary": SHARED_DICTIONARIES["summary"]}
            manifest["files"][name("reaches_analysis")] = {
                "rows": entry["analysis_rows"], "columns": entry["analysis_columns"],
                "dictionary": "reaches_analysis_DATA_DICTIONARY.csv",
                "note": "entries with an identified problem are left out; see analysis_EXCLUSIONS.csv"}
            if entry["session_rows"]:
                manifest["files"][name("sessions_analysis")] = {
                    "rows": entry["session_rows"], "columns": entry["session_columns"],
                    "dictionary": "sessions_analysis_DATA_DICTIONARY.csv"}
        except Exception as e:
            msg = "ODC_reaches_%s: %s: %s" % (cid, type(e).__name__, e)
            manifest["problems"].append(msg)
            manifest.setdefault("failures", []).append(msg)
            build_failed.add(cid)
            inc.failed(cid)

    # One dictionary for every analysis file of each kind. Their columns do not
    # vary by cohort, so thirteen copies under thirteen names would be thirteen
    # things to keep in step -- and the first time they fell out of step, nothing
    # would say so. A cohort missing a measurement simply has no column for a row
    # that is described here, which is the harmless direction: an upload requires
    # every column to have an entry, not every entry to have a column.
    for rows_by_name, stem in ((analysis_dict, "reaches_analysis"),
                               (sessions_dict, "sessions_analysis")):
        if rows_by_name:
            dd.write_rows(list(rows_by_name.values()),
                          write_dir / ("%s_DATA_DICTIONARY.csv" % stem))
            manifest["files"]["%s_DATA_DICTIONARY.csv" % stem] = {
                "columns": len(rows_by_name),
                "note": "shared by every cohort's %s file" % stem.split("_")[0]}

    # The same for the complete record and the shareable summary.
    superseded = sorted(p.name for p in final_dir.glob("*_DATA_DICTIONARY.csv")
                        if _PER_COHORT_DICTIONARY.match(p.name)
                        and p.name not in SHARED_DICTIONARIES.values())
    for kind, name in SHARED_DICTIONARIES.items():
        s = shared[kind]
        if not s["order"]:
            continue
        if cohorts:
            # A partial export writes only some cohorts, but the dictionary
            # describes every cohort's file: keep each variable the previous
            # dictionaries (shared, or the old per-cohort ones) describe that
            # this run did not meet, or another cohort's file goes undocumented.
            sources = [final_dir / name] + [
                final_dir / n for n in superseded
                if n.startswith("ODC_reaches_") == (kind == "complete")]
            for src in sources:
                if src.exists():
                    for row in pd.read_csv(src, dtype=str, keep_default_na=False).to_dict("records"):
                        if row.get("VariableName") and row["VariableName"] not in s["rows"]:
                            s["rows"][row["VariableName"]] = row
                            s["order"].append(row["VariableName"])
        dd.write_rows([s["rows"][n] for n in s["order"]], write_dir / name)
        written = sorted({c for labels in s["seen_in"].values() for c in labels})
        partial = {n: labels for n, labels in s["seen_in"].items() if len(labels) < len(written)}
        manifest["files"][name] = {
            "columns": len(s["order"]),
            "note": "shared by every cohort's %s file" % (
                "ODC_reaches" if kind == "complete" else "reaches summary"),
            "variables_not_in_every_cohort": partial,
        }
        if partial:
            # A NOTE, not a problem: nothing is missing or wrong -- the
            # dictionary describes every column of every cohort's file. Listed
            # as a problem it kept the exports' skip signature from ever being
            # saved (exporters.current), so every refresh rewrote everything.
            manifest.setdefault("notes", []).append(
                "%s: %d variable(s) are columns in only some cohorts' files, because those "
                "cohorts' tracking sheets differ; the dictionary describes all of them, and "
                "this manifest entry lists which cohorts have each" % (name, len(partial)))
        for c in s["conflicts"]:
            manifest["problems"].append("%s: %s" % (name, c))

    # What was left out and why, beside the files it was left out of. A reader of
    # the analysis files is owed this: a missing session should never look like a
    # session that was not filmed.
    report_cols = ["cohort", "left_out", "video_name", "subject_id", "date", "rows",
                   "fields", "reason"]
    report = pd.DataFrame(exclusion_report, columns=report_cols)
    previous = final_dir / "analysis_EXCLUSIONS.csv"
    if cohorts and previous.exists():
        # A partial export must not throw away the other cohorts' entries: keep
        # every row that belongs to a cohort not being written now. A row whose
        # cohort cannot be told (written before the report carried one) is
        # dropped: it is regenerated when its cohort is next exported, and a
        # stale line in this report is worse than a missing one.
        kept = pd.read_csv(previous, dtype=str, keep_default_na=False)
        if "cohort" not in kept.columns:
            # Written before the report carried a cohort: recover it from the
            # animal id where that id still contains one. Where it does not
            # (a decoded id such as F01), the row is stale and goes.
            kept["cohort"] = kept["subject_id"].str.rsplit("_", n=1).str[0]
            kept["cohort"] = kept["cohort"].where(kept["video_name"] != "(whole cohort)",
                                                  kept["subject_id"])
        known = set(cohorts_in(t["reach"]))
        kept = kept[kept["cohort"].isin(known) & ~kept["cohort"].isin(wanted)]
        report = pd.concat([kept[report_cols], report.astype(str)], ignore_index=True)
    # Same rule as every dataset we ship: no comma, quote or odd character in a
    # cell, because this file goes to the same readers as the data.
    report, _changes = csv_safety.make_frame_safe(report.astype(str))
    report.to_csv(write_dir / "analysis_EXCLUSIONS.csv", index=False)
    manifest["files"]["analysis_EXCLUSIONS.csv"] = {
        "rows": int(len(report)),
        "reach_rows_left_out": int(pd.to_numeric(
            report.loc[report["left_out"] == "reach rows", "rows"], errors="coerce").sum()),
        "note": "every entry left out of the analysis files, and why"}
    # A check that could not run is shown as a problem -- whatever it would
    # have left out is in the files. It follows entirely from inputs the
    # exports' skip signature covers (the snapshot, the name-audit record, the
    # sheets folder), so it is also listed under "from_inputs": it does not
    # stop the signature being saved, and it is carried over, word for word,
    # when an unchanged run is skipped (exporters.current).
    for note in exclusions.notes:
        manifest["problems"].append("analysis exclusions: " + note)
        manifest.setdefault("from_inputs", []).append("analysis exclusions: " + note)

    if write_dir != final_dir:
        from .incremental import copy_if_changed
        final_dir.mkdir(parents=True, exist_ok=True)
        # Each file through a temporary name; a small file that already holds
        # exactly these bytes is left alone, so its time does not change and
        # the limited exports cut from it are not remade for nothing.
        for f in sorted(write_dir.rglob("*.csv")):
            copy_if_changed(f, final_dir / f.relative_to(write_dir))

    # A rebuilt cohort's files are exactly the ones this run wrote. A file of a
    # kind it did not write this time -- its sessions file, when every reach of
    # the cohort was left out of the analysis files -- would otherwise stay
    # from an earlier run and claim sessions that are not there. Archived,
    # never deleted. (write_cohort checks for this too, but it looks in the
    # staging folder, where an earlier run's file never is.)
    if not analysis_only:
        for cid in inc.rebuilt_cohorts():
            label = cohort_label(cid)
            stale = [layout.key(final_dir, layout.cohort_file(final_dir, kind, cid, label))
                     for kind in layout.REACH_KINDS
                     if layout.cohort_file(final_dir, kind, cid, label).is_file()
                     and not layout.cohort_file(write_dir, kind, cid, label).is_file()]
            if stale:
                try:
                    archive_old_names(final_dir, stale, "not_written_this_run")
                except OSError as e:
                    msg = ("%s: an old file this run did not rewrite could not be moved to the "
                           "archive (%s: %s)" % (cid, type(e).__name__, e))
                    manifest["problems"].append(msg)
                    manifest.setdefault("failures", []).append(msg)
                    inc.failed(cid)

    # A cohort that has no reaches any more keeps no reach files. WHY: files
    # are written only for cohorts with reaches, so a cohort whose reaches all
    # left the database (renamed, withdrawn, moved to another cohort) kept its
    # last files -- and the combined file below, built from the files on disk,
    # kept counting them. Its reach files are archived (never deleted) before
    # combining. Which cohorts: on a full export, every cohort folder on disk
    # whose cohort is not in the reach table; on a named export (--cohort), the
    # named cohorts that came out empty. A cohort whose build FAILED keeps its
    # files (the failure is reported; the old files are the best there is).
    # Skipped when the reach table is empty: that is a broken snapshot, not a
    # lab with no reaches, and it must not empty the folder.
    combine_projects = {layout.project_of(c) for c in wanted}
    if len(t["reach"]):
        on_disk = layout.cohort_files_on_disk(final_dir, layout.REACH_KINDS)
        gone = set(built_empty)
        if not cohorts:
            gone |= set(on_disk) - set(cohorts_in(t["reach"]))
        gone -= build_failed
        # One cohort at a time, and a failure is a problem, never a crash: a
        # file held open by another program (or a share refusing the move)
        # used to raise out of export(), and the combined files of EVERY
        # project, the dictionaries and the manifest entries were then not
        # written that night. Now that cohort is reported as a failure (so the
        # skip signature is not saved and the next run tries again) and
        # everything else carries on. A cohort only partly moved still has its
        # remaining files counted by the combined file until the retry.
        archived, emptied = [], []
        for cid in sorted(gone & set(on_disk)):
            files = on_disk[cid]
            try:
                archive_old_names(final_dir, files, "cohorts_without_reaches")
            except OSError as e:
                msg = ("the reach files of %s, a cohort that no longer has any reaches, could "
                       "not all be moved to the archive (%s: %s) -- close any program that has "
                       "one open; the next export tries again" % (cid, type(e).__name__, e))
                manifest["problems"].append(msg)
                manifest.setdefault("failures", []).append(msg)
            done = [f for f in files if not (final_dir / f).exists()]
            archived.extend(done)
            if len(done) == len(files):
                emptied.append(cid)
        if archived:
            manifest["archived_cohorts_without_reaches"] = sorted(archived)
        if emptied:
            # "run_notes", not "notes": exporters.current saves "notes" with the
            # skip signature and repeats them on every night the export is
            # skipped, and this is something that happened ONCE.
            manifest.setdefault("run_notes", []).append(
                "%d file(s) of cohort(s) that no longer have any reaches were moved to the "
                "archive: %s" % (len(archived), ", ".join(emptied)))
        combine_projects |= {layout.project_of(c) for c in gone}

    # Each project's combined files, put together from the cohort files now on
    # disk -- so a one-cohort export still yields a combined file holding every
    # cohort (see exporters.layout). Then the per-cohort files the layout before
    # 2026-10-29 left at the top of the folder are archived.
    #
    # Incremental: a project none of whose cohorts was rebuilt, archived or
    # failed keeps its combined files as they are (their manifest entries come
    # from the ledger), and a project that is written again keeps the front of
    # each old combined file up to its first changed cohort (layout.combine).
    combine_projects, kept_combined = inc.combine_plan(combine_projects, gone if len(t["reach"]) else set(),
                                                       build_failed)
    manifest["files"].update(kept_combined)
    combined_records: Dict[str, dict] = {}
    layout.combine_all(final_dir, layout.REACH_KINDS, combine_projects,
                       manifest, staging=(Path(staging_dir) / "_combined") if staging_dir else None,
                       reuse=inc.combined_reuse(), ledger_out=combined_records)
    inc.finish(manifest, combined_records)
    if not analysis_only:
        moved = layout.archive_flat_layout(final_dir)
        if moved:
            manifest["archived_flat_layout_files"] = len(moved)
        regrouped = layout.archive_moved_project_folders(final_dir)
        if regrouped:
            manifest["archived_regrouped_project_folders"] = len(regrouped)

    # Per-cohort dictionaries from before 2026-10-16 are superseded by the shared
    # ones: moved out of the folder (archived, never deleted) once the shared
    # dictionary that replaces them is in place.
    if not analysis_only and all((final_dir / n).exists() for n in SHARED_DICTIONARIES.values()):
        moved = archive_old_names(final_dir, superseded, "per_cohort_dictionaries")
        if moved:
            manifest["archived_per_cohort_dictionaries"] = len(moved)
    return manifest


def _is_live_exports_folder(folder: Path) -> bool:
    """True when ``folder`` is the live current-exports folder (the configured
    ``<mousedb_root>/exports/current``, or the exporter's own default), however
    it was spelled. Unconfigured: only the exporter's default can be it."""
    from ..config import require
    from .current import CURRENT_DIR
    live = []
    try:
        live.append(require("mousedb_root") / "exports" / "current")
    except Exception:
        pass
    if CURRENT_DIR is not None:
        live.append(Path(CURRENT_DIR))

    def same(a: Path, b: Path) -> bool:
        try:
            return Path(a).resolve() == Path(b).resolve()
        except OSError:
            import os
            return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

    return any(same(folder, f) for f in live)


def main(argv=None) -> int:
    import argparse
    import json
    from ..config import require
    ap = argparse.ArgumentParser(prog="mousedb export-odc-reaches", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cohort", action="append", help="Cohort id, e.g. PROJECT_02 (repeatable; default all)")
    ap.add_argument("--out-dir", type=Path, help="Folder to write into (default: the current exports folder)")
    ap.add_argument("--snapshot-dir", type=Path)
    args = ap.parse_args(argv)
    snap = args.snapshot_dir or require("snapshot_dir")
    out = args.out_dir or (require("mousedb_root") / "exports" / "current")
    if args.out_dir is None or _is_live_exports_folder(args.out_dir):
        # Writing into the live exports folder: wait for any other refresh of
        # it to finish first, and keep it out while this runs (the reasons are
        # at exporters.current.EXPORTS_LOCK). Decided by WHERE the files go,
        # not by whether --out-dir was typed: an --out-dir that names the live
        # folder used to skip the lock and write beside a running refresh.
        from .current import (EXIT_LOCK_FOLDER, EXIT_LOCK_TIMEOUT, hold_exports_lock,
                              lock_folder_message, lock_timeout_message)
        from ..task_mutex import LockFolderNotWritable, LockWaitTimeout
        try:
            lock = hold_exports_lock()
        except LockWaitTimeout as e:
            print(lock_timeout_message(e), flush=True)
            return EXIT_LOCK_TIMEOUT
        except LockFolderNotWritable as e:
            print(lock_folder_message(e), flush=True)
            return EXIT_LOCK_FOLDER
        with lock:
            m = export(snap, out, args.cohort)
    else:
        m = export(snap, out, args.cohort)
    for name, info in m["files"].items():
        print("  %-32s %9s rows  %s%s" % (name, info.get("rows", "-"),
                                           ("(%s) " % info["cohort"]) if info.get("cohort") else "",
                                         ("blank: " + ", ".join(info["blank_animal_columns"]))
                                         if info.get("blank_animal_columns") else ""))
    for p in m["problems"]:
        print("  [!] %s" % p)
    print("written to %s" % out)
    return 1 if m["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
