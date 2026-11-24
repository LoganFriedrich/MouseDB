"""Session-level measurements of the sessions document beyond the pellet counts.

WHY THESE EXIST
---------------
The sessions document said how many pellets an animal got on a tray, from the
algorithm and from the hand scores. A reader asking anything else about the
session -- how heavy the animal was that day, how long the tray ran, how soon
and how often it reached, whether it succeeded on the first try, what its
reaches looked like, where the pellets it knocked went, whether the pellets
were well placed, whether a recording of that day failed -- had to go back to
the reach file, or to data that never left the pipeline, and work it out
themselves, each in their own way. These are those answers, worked out once,
here, by one written definition each (every column's dictionary row repeats it).

They are computed where the sessions document is built (the main export,
odc_reaches.write_cohort), from the same rows and tables as everything else in
it -- never in a file derived from it. The per-reach documents never carry them.

WHAT THEY ARE AND WHERE EACH COMES FROM
---------------------------------------
 1 body weight on the day    the weights table (the value the reach rows carry);
                             PER_SESSION in analysis_sessions carries it
 2 day pillar reaches        the reach rows of this document: pillar trays of the
                             animal-day (an earlier program's "Total Swipes")
 3 timing                    the results file, imported by import-reaches: the
                             video's length (total frames / frame rate) and, per
                             pellet, the latency to its first reach and the mean
                             gap between its reaches
 4 success given contact     the session's retrieved and displaced counts
 5 first-attempt success     the reach rows: first reach at a pellet that retrieved it
 6 reach kinematics          the reach rows: for EVERY per-reach kinematic measure
                             of the reach file, the session median, mean, standard
                             error of the mean and n
 7 displaced split           video_pellets: displaced and stayed in the scoring
                             area, or displaced out of it
 8 moved not by a reach      video_pellets: a person saw something other than a
                             reach move the pellet
 9 pellet placement          video_pellets: mean of each pellet's position idealness
10 recording failure         session_recording_status: a recording of that
                             animal-day failed

THE TWO RULES ABOUT ABSENT VALUES (written NA)
----------------------------------------------
* A tray with NO reach (it has a session row, but no reach rows): every measure
  computed FROM reaches is NA -- the latency, the gap between reaches, both
  first-attempt columns and every kinematics column (n included). There is no
  reach to measure; a 0 would be a claim. Every other measure is filled exactly
  as on any tray. Reaches per minute is 0 there, not NA: it is the reach count
  (session_reaches_ai, a real 0) over the session's known length.
* An easy or flat training tray (E, F): every measure about a pellet from the
  ALGORITHM is NA, as the existing algorithm pellet counts are (trays.py says
  why: its pellets are not tracked and its video is not cut per pellet). The
  hand-scored success given contact is filled where hand scores exist, as the
  other hand columns are. Weight, video length, reaches per minute, the day's
  recording status and the reach kinematics do not ask about a pellet and are
  filled; the day's pillar reaches is NA on such a row (it counts pillar trays).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import pandas as pd

# The per-pellet outcomes, as the results file writes them.
DISPLACED_IN_AREA = "displaced_sa"
DISPLACED_OUTSIDE = "displaced_outside"
NOT_BY_A_REACH = ("abnormal", "abnormal_exception")
RECORDING_FAILED = "recording_failed"
NO_ISSUE = "no_issue_recorded"
PILLAR = "P"
STATS = ("median", "mean", "sem", "n")

# Every column this module adds that is not a kinematics column, in file order.
COLUMNS: List[str] = [
    "day_pillar_reaches_ai",
    "session_duration_sec", "session_reaches_per_minute",
    "session_median_latency_to_first_reach_sec", "session_mean_inter_reach_interval_sec",
    "session_percent_retrieved_of_contacted_ai", "session_percent_retrieved_of_contacted_hand",
    "session_first_attempt_retrieved_ai", "session_percent_first_attempt_success_ai",
    "session_displaced_in_scoring_area_ai", "session_displaced_outside_ai",
    "session_moved_not_by_reach_ai", "session_mean_pellet_position_idealness",
    "day_recording_status", "day_recording_issue_reason",
]
TEXT = ("day_recording_status", "day_recording_issue_reason")
# Computed from reaches: NA on the row of a tray with no reach.
FROM_REACHES = ("session_median_latency_to_first_reach_sec",
                "session_mean_inter_reach_interval_sec",
                "session_first_attempt_retrieved_ai", "session_percent_first_attempt_success_ai")


def kinematic_column(stat: str, published: str) -> str:
    """session_<stat>_<the reach file's column>, e.g. session_median_reach_duration_frames."""
    return "session_%s_%s" % (stat, published)


# ------------------------------------------------------------------ which measures

def kinematic_measures(columns) -> List[Tuple[str, str]]:
    """[(internal name, published reach-file name)] of every per-reach kinematic
    measure among ``columns`` (the analysis frame's internal names).

    THE RULE: every per-reach measurement the reach file publishes as reach_<name>
    (analysis_reaches.measurement_columns -- listed the other way round, so a
    measure the extractor adds later is summarised here without anyone adding
    it) that is a number describing the movement, which leaves out:
      * text columns (how an anchor was placed, which paw point led),
      * frame numbers (a name ending in _frame: WHERE in the video the reach
        happened -- its start, apex, end or contact frame -- not how it moved;
        durations, counted in frames, end in _frames and are kept),
      * tracking confidence (a name containing 'confidence': how sure the pose
        tracking was, not a property of the reach),
      * the kinematics version (a label).
    """
    from . import analysis_reaches as ar
    from . import levels
    measured = ar.measurement_columns(list(columns))
    names = levels.with_reach_prefix(levels.ANALYSIS, measured)
    out = []
    for c in measured:
        pub = names.get(c, c)
        low = str(c).lower()
        if (not pub.startswith(levels.REACH) or c in ar.TEXT_COLUMNS
                or low.endswith("_frame") or "confidence" in low
                or low == "kinematics_version"):
            continue
        out.append((c, pub))
    return out


# ------------------------------------------------------------------ inputs

def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _day(v) -> str:
    return "" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v)[:10]


def prepare(cohort_id: str, video_sessions: Optional[pd.DataFrame] = None,
            video_pellets: Optional[pd.DataFrame] = None,
            recording_status: Optional[pd.DataFrame] = None) -> dict:
    """What these measures need that the reach rows do not carry, for one cohort.

    {"videos": {video: {...}}, "recording": {(subject, day): [...]} or None}

    "recording" is None when the snapshot has no recording-status table at all:
    the day's status is then unknown (NA), never "no issue". A table that is
    absent (an older snapshot) leaves the columns it feeds NA.
    """
    prefix = cohort_id + "_"

    def mine(df):
        if df is None or df.empty or "subject_id" not in df.columns:
            return None
        return df[df["subject_id"].astype(str).str.startswith(prefix)]

    videos: Dict[str, dict] = {}
    vs = mine(video_sessions)
    if vs is not None:
        for r in vs.to_dict("records"):
            frames, fps = _num(r.get("total_frames")), _num(r.get("frame_rate"))
            videos.setdefault(str(r["video_name"]), {})["duration_sec"] = (
                frames / fps if frames is not None and fps else None)
    vp = mine(video_pellets)
    if vp is not None and "video_name" in vp.columns:
        for v, g in vp.groupby("video_name"):
            d = videos.setdefault(str(v), {})
            o = g["outcome"].astype(str) if "outcome" in g.columns else pd.Series(dtype=str)
            d["displaced_in_area"] = int((o == DISPLACED_IN_AREA).sum())
            d["displaced_outside"] = int((o == DISPLACED_OUTSIDE).sum())
            d["moved_not_by_reach"] = int(o.isin(NOT_BY_A_REACH).sum())
            d.update(pellet_timing(g))
    out = {"videos": videos, "recording": None}
    if recording_status is not None:
        rec: Dict[tuple, list] = {}
        rs = mine(recording_status)
        if rs is not None:
            for r in rs.to_dict("records"):
                if str(r.get("status") or "") != RECORDING_FAILED:
                    continue
                rec.setdefault((str(r["subject_id"]), _day(r.get("session_date"))), []).append(
                    (str(r.get("tray_type") or "?"), _num(r.get("tray_number")),
                     str(r.get("reason") or "recording failed"),
                     str(r.get("collage_name") or "?")))
        out["recording"] = rec
    return out


def pellet_timing(g: pd.DataFrame) -> dict:
    """One tray's latency, gap between reaches and placement, from its pellet rows.

    latency: the MEDIAN, over the pellets that got a reach, of each pellet's time
    from its first frame to the start of its first reach (results-file field
    time_to_first_reach_sec). A median, so one pellet ignored for most of its
    stretch does not stand for the typical pellet.

    gap: the MEAN over every gap between consecutive reaches at the same pellet
    (end of one reach to the start of the next). The results file gives that mean
    per pellet (mean_inter_reach_interval_sec); recombined as
    sum(mean_i x (reaches_i - 1)) / sum(reaches_i - 1), which is exactly the mean
    over all the tray's gaps, so a pellet with ten reaches weighs its nine gaps.
    None when no pellet had two reaches.

    idealness: the mean over the tray's pellets that have a value.
    """
    out = {"latency": None, "gap": None, "idealness": None}
    if "time_to_first_reach_sec" in g.columns:
        lat = pd.to_numeric(g["time_to_first_reach_sec"], errors="coerce").dropna()
        out["latency"] = float(lat.median()) if len(lat) else None
    if {"mean_inter_reach_interval_sec", "n_reaches"} <= set(g.columns):
        m = pd.to_numeric(g["mean_inter_reach_interval_sec"], errors="coerce")
        k = pd.to_numeric(g["n_reaches"], errors="coerce") - 1
        ok = m.notna() & (k > 0)
        if ok.any():
            out["gap"] = float((m[ok] * k[ok]).sum() / k[ok].sum())
    if "pellet_position_idealness" in g.columns:
        ideal = pd.to_numeric(g["pellet_position_idealness"], errors="coerce").dropna()
        out["idealness"] = float(ideal.mean()) if len(ideal) else None
    return out


# ------------------------------------------------------------------ per session

def _numbers(frame: pd.DataFrame, column: str) -> pd.Series:
    from .analysis_reaches import MISSING
    if column not in frame.columns:
        return pd.Series(dtype="float64", index=frame.index)
    return pd.to_numeric(frame[column].replace(MISSING, None), errors="coerce")


def describe(values: pd.Series) -> Dict[str, Optional[float]]:
    """median, mean, standard error of the mean (sample SD / sqrt(n)) and n, over
    the values present. sem is None when n < 2 (one value has no spread); median
    and mean are None when n is 0."""
    v = pd.to_numeric(values, errors="coerce").dropna().astype(float)
    n = int(len(v))
    if n == 0:
        return {"median": None, "mean": None, "sem": None, "n": 0}
    return {"median": float(v.median()), "mean": float(v.mean()),
            "sem": (float(v.std(ddof=1)) / math.sqrt(n)) if n >= 2 else None, "n": n}


def day_pillar_reach_counts(frame: pd.DataFrame, session_only=()) -> Dict[tuple, int]:
    """{(subject, day): pillar reach rows of that animal-day in this document}."""
    if frame.empty or not {"tray_type", "subject_id", "test_date", "video_name"} <= set(frame.columns):
        return {}
    r = frame[~frame["video_name"].astype(str).isin(set(session_only))]
    r = r[r["tray_type"].astype(str).str.strip().str.upper() == PILLAR]
    return {(str(s), _day(d)): int(n) for (s, d), n in
            r.groupby(["subject_id", "test_date"]).size().items()}


def kinematics(frame: pd.DataFrame, measures: List[Tuple[str, str]],
               session_only=()) -> pd.DataFrame:
    """One row per video: for each measure, median, mean, SEM and n over the
    video's reach rows that have a value. Every reach of the session counts
    (also one made after its pellet was gone, and every reach of an easy or flat
    tray): the population is the session's reaches, whose number is
    session_reaches_ai. A video with reaches but no value for a measure has n 0
    and the other three empty; a tray with no reach gets no row here (all NA)."""
    reach = frame[~frame["video_name"].astype(str).isin(set(session_only))]
    if reach.empty or not measures:
        return pd.DataFrame()
    cols = [m for m, _p in measures]
    num = pd.DataFrame({m: _numbers(reach, m) for m in cols}, index=reach.index)
    g = num.groupby(reach["video_name"].astype(str))
    med, mean, sd, n = g.median(), g.mean(), g.std(ddof=1), g.count()
    sem = (sd / n.pow(0.5)).where(n >= 2)
    out = {}
    for m, pub in measures:
        for stat, table in (("median", med), ("mean", mean), ("sem", sem), ("n", n)):
            out[kinematic_column(stat, pub)] = table[m]
    result = pd.DataFrame(out)
    result.index.name = "video_name"
    return result


def for_session(group: pd.DataFrame, row: dict, no_reach: bool, easy_flat: bool,
                sources: dict, day_pillar_reaches: Dict[tuple, int]) -> dict:
    """The non-kinematics measures for one session (one video). ``row`` is the
    session row built so far: its counts are read from it, so the two cannot
    disagree."""
    video = str(group["video_name"].iloc[0])
    subject = str(group["subject_id"].iloc[0])
    day = _day(group["test_date"].iloc[0]) if "test_date" in group.columns else ""
    v = (sources.get("videos") or {}).get(video, {})
    out: Dict[str, object] = {c: None for c in COLUMNS}

    # 2 the day's pillar reaches (NA on an easy or flat tray's row)
    if not easy_flat:
        out["day_pillar_reaches_ai"] = day_pillar_reaches.get((subject, day), 0)

    # 3 timing
    n_reaches = row.get("reaches_in_video") or 0
    dur = v.get("duration_sec")
    out["session_duration_sec"] = dur
    if dur:
        out["session_reaches_per_minute"] = n_reaches / (dur / 60.0)
    if not no_reach and not easy_flat:
        out["session_median_latency_to_first_reach_sec"] = v.get("latency")
        out["session_mean_inter_reach_interval_sec"] = v.get("gap")

    # 4 success given contact, from each scoring stream
    for stream, ret_col, disp_col in (("ai", "video_retrieved", "video_displaced"),
                                      ("hand", "manual_retrieved", "manual_displaced")):
        if stream == "ai" and easy_flat:
            continue
        ret, disp = _num(row.get(ret_col)), _num(row.get(disp_col))
        if ret is not None and disp is not None and (ret + disp) > 0:
            out["session_percent_retrieved_of_contacted_%s" % stream] = round(
                100.0 * ret / (ret + disp), 4)

    # 5 first-attempt success
    # (NA when the reach rows do not say which reach came first at its pellet)
    first_known = _numbers(group, "is_first_reach_at_pellet")
    if not no_reach and not easy_flat and first_known.notna().any():
        first = first_known == 1
        won = (group["reach_outcome"].astype(str).str.strip().str.lower() == "retrieved"
               if "reach_outcome" in group.columns else pd.Series(False, index=group.index))
        n_first = int((first & won).sum())
        out["session_first_attempt_retrieved_ai"] = n_first
        reached = _num(row.get("pellets_reached_for"))
        if reached:
            out["session_percent_first_attempt_success_ai"] = round(100.0 * n_first / reached, 4)

    # 7, 8, 9 pellet outcomes and placement, from the algorithm (NA on easy/flat)
    if not easy_flat:
        out["session_displaced_in_scoring_area_ai"] = v.get("displaced_in_area")
        out["session_displaced_outside_ai"] = v.get("displaced_outside")
        out["session_moved_not_by_reach_ai"] = v.get("moved_not_by_reach")
        out["session_mean_pellet_position_idealness"] = v.get("idealness")

    # 10 recording failure on the animal-day
    rec = sources.get("recording")
    if rec is not None:
        failed = rec.get((subject, day)) or []
        if failed:
            out["day_recording_status"] = RECORDING_FAILED
            out["day_recording_issue_reason"] = "; ".join(
                "tray %s%s: %s (recording %s)" % (t, "" if n is None else int(n), why, name)
                for t, n, why, name in sorted(failed, key=lambda x: (x[0], x[1] or 0)))
        else:
            out["day_recording_status"] = NO_ISSUE
    return out


# ------------------------------------------------------------------ dictionary

_NO_REACH_NA = " NA on the row of a tray with no reach: there is no reach to measure."
_EF_NA = (" NA on an easy or flat training tray: the algorithm does not analyse its "
          "pellets (they are not tracked and the video is not cut per pellet).")

# What each column holds on the row of a tray with no reach (analysis_sessions
# appends it to the column's Comments).
NO_REACH_RULES = {c: "NA: computed from reaches, and there are none." for c in FROM_REACHES}
NO_REACH_RULES["session_reaches_per_minute"] = (
    "0 when the session's length is known: no reach over a known length is a real 0.")
NO_REACH_KINEMATICS = "NA (all four columns of the measure, n included): there is no reach."


def dictionary_rows(row) -> List[dict]:
    """One dictionary row per column of COLUMNS. ``row`` = data_dictionary._row."""
    return [
        row("day_pillar_reaches_ai", "Pillar reaches on the day",
            "How many reaches this animal made on this test day over all its pillar (P) "
            "trays together: the reach rows of pillar trays with the same subject_id and "
            "test_date in the per-reach file. The count an earlier reaching program called "
            "the day's Total Swipes. Not session_reaches_ai, which counts one tray, and not "
            "day_reaches_ai, which counts every tray type. A tray left out of the export "
            "(analysis_EXCLUSIONS.csv) or not yet filed is not counted, so on such a day it "
            "is an undercount. NA on an easy or flat tray's row (it counts pillar trays). "
            "0 on a pillar day with no reach.", "count", "integer"),
        row("session_duration_sec", "Session (video) length",
            "Length of this session's video in seconds: its total frames divided by its "
            "frame rate, both from the pipeline's results file. The whole video (every "
            "pellet plus the time before the first and after the last), not only the "
            "pellet stretches. NA when the results file does not give them.",
            "seconds", "number"),
        row("session_reaches_per_minute", "Reaches per minute",
            "session_reaches_ai divided by session_duration_sec / 60. 0 on a tray with no "
            "reach (no reach over a known length is a real 0). NA when the length is "
            "unknown.", "reaches per minute", "number"),
        row("session_median_latency_to_first_reach_sec", "Median latency to the first reach",
            "For each pellet that got at least one reach: the time from the pellet's first "
            "frame (the start of its stretch of video) to the start of its first reach "
            "(results-file field time_to_first_reach_sec). This column is the MEDIAN over "
            "those pellets; a pellet never reached for has no latency and is not counted "
            "(session_pellets_reached_for says how many were). A median, so one pellet "
            "ignored for most of its stretch does not stand for the typical pellet."
            + _NO_REACH_NA + _EF_NA, "seconds", "number"),
        row("session_mean_inter_reach_interval_sec", "Mean interval between reaches",
            "The mean, over every pair of consecutive reaches at the same pellet in this "
            "session, of the time from the end of one reach to the start of the next. The "
            "results file gives that mean per pellet (mean_inter_reach_interval_sec); they "
            "are recombined weighted by each pellet's number of gaps, which equals the mean "
            "over all the session's gaps. A gap between reaches at different pellets is not "
            "counted (the tray moved between them). NA when no pellet got two reaches."
            + _NO_REACH_NA + _EF_NA, "seconds", "number"),
        row("session_percent_retrieved_of_contacted_ai",
            "Percent retrieved of pellets contacted (algorithm)",
            "Success given contact: session_retrieved_ai / (session_retrieved_ai + "
            "session_displaced_ai) x 100 -- of the pellets the animal moved, the share it "
            "ate. NA when it moved none (no answer, not 0)." + _EF_NA,
            "percent", "number", "", "", "0", "100"),
        row("session_percent_retrieved_of_contacted_hand",
            "Percent retrieved of pellets contacted (hand scored)",
            "Success given contact from the hand scores: session_retrieved_hand / "
            "(session_retrieved_hand + session_displaced_hand) x 100. NA when no pellet was "
            "contacted or the tray has no hand score. Filled on every tray type that has "
            "hand scores, as the other hand columns are.",
            "percent", "number", "", "", "0", "100"),
        row("session_first_attempt_retrieved_ai", "Pellets retrieved on the first attempt",
            "How many pellets were retrieved by the FIRST reach made at them: the reach rows "
            "with reach_is_first_in_segment 1 and reach_outcome Retrieved (the first reach "
            "is the one credited with the pellet, and the pellet was eaten)."
            + _NO_REACH_NA + _EF_NA, "count", "integer"),
        row("session_percent_first_attempt_success_ai", "First-attempt success (percent)",
            "session_first_attempt_retrieved_ai / session_pellets_reached_for x 100: of the "
            "pellets the animal reached for, the share it retrieved with its first reach. "
            "Out of the pellets attempted, not the 20 presented (which would also count "
            "pellets it ignored)." + _NO_REACH_NA + _EF_NA,
            "percent", "number", "", "", "0", "100"),
        row("session_displaced_in_scoring_area_ai", "Displaced, stayed in the scoring area",
            "Pellets the algorithm scored displaced that stayed in the scoring area (outcome "
            "displaced_sa). With session_displaced_outside_ai it adds up to "
            "session_displaced_ai." + _EF_NA, "count", "integer"),
        row("session_displaced_outside_ai", "Displaced out of the scoring area",
            "Pellets the algorithm scored displaced that left the scoring area (outcome "
            "displaced_outside: knocked out of reach)." + _EF_NA, "count", "integer"),
        row("session_moved_not_by_reach_ai", "Pellets moved by something other than a reach",
            "Pellets a person reviewing the video saw moved by something other than a reach "
            "(outcome abnormal or abnormal_exception). No reach moved them, so they are "
            "counted in session_untouched_ai too: this says how many of the untouched "
            "pellets are these." + _EF_NA, "count", "integer"),
        row("session_mean_pellet_position_idealness", "Mean pellet placement",
            "The mean, over the tray's pellets that have a value, of each pellet's position "
            "idealness (0-1, how well the pellet sat relative to the slit; results-file field "
            "pellet_position_idealness, the value the per-reach file carries as "
            "segment_pellet_position_idealness). A badly placed pellet is harder to get, so "
            "this is a covariate for success." + _EF_NA, "", "number", "", "", "0", "1"),
        row("day_recording_status", "Recording status of the day",
            "Whether a video recording of one of this animal's trays on this day FAILED: the "
            "reaching pipeline found it empty, a fraction of a second long, unreadable or "
            "broken, set it aside and never analysed it, so that tray has no row here. "
            "'no_issue_recorded' says only that no failure is on record. From the database "
            "table session_recording_status (the source of the ODC_days file's "
            "Recording_Status). NA when the snapshot has no such table.",
            "", "text", "recording_failed; no_issue_recorded",
            "recording_failed=a recording of this animal-day failed; "
            "no_issue_recorded=no recording failure is on record for the day"),
        row("day_recording_issue_reason", "Recording issue of the day",
            "For day_recording_status 'recording_failed': which tray's recording failed, why, "
            "and the recording's name. NA otherwise.", "", "text"),
    ]


def kinematic_dictionary_rows(row, measures: List[Tuple[str, str]],
                              reach_rows: Dict[str, dict]) -> List[dict]:
    """Four rows per measure. ``reach_rows`` = the reach file's dictionary rows by
    INTERNAL name: each summary column names, and takes its unit from, the
    per-reach column it summarises."""
    which = (" Over every reach of this session that has a value for the measure (the "
             "matching session_n_ column says how many): reaches made after the pellet "
             "was already gone are included (session_reaches_after_the_pellet_was_gone "
             "says how many there are), and on an easy or flat tray every reach counts. "
             "The session's total number of reaches, the total N, is session_reaches_ai. "
             "Computed from the values of the per-reach file's column %s, as written there"
             " (its dictionary row defines the measure). NA on the row of a tray with no "
             "reach.")
    rows = []
    for internal, pub in measures:
        r = reach_rows.get(internal) or {}
        title = str(r.get("Title") or pub)
        unit = str(r.get("Unit_of_Measure") or "")
        w = which % pub
        mean_col, n_col = kinematic_column("mean", pub), kinematic_column("n", pub)
        rows += [
            row(kinematic_column("median", pub), "Session median: %s" % title,
                "The median of %s over this session's reaches." % pub + w, unit, "number"),
            row(mean_col, "Session mean: %s" % title,
                "The mean of %s over this session's reaches." % pub + w, unit, "number"),
            row(kinematic_column("sem", pub), "Standard error of the session mean: %s" % title,
                "The standard error of %s: the sample standard deviation (n - 1 in the "
                "denominator) of the same values divided by the square root of n (n = %s). "
                "NA when n < 2." % (mean_col, n_col) + w, unit, "number"),
            row(n_col, "Reaches in the session summary: %s" % title,
                "How many reaches the median, mean and standard error of %s are over: this "
                "session's reaches that have a value for it; 0 when none has." % pub + w,
                "reaches", "integer"),
        ]
    return rows
