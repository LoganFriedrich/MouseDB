"""The ready-made tables the data browser offers for the common questions.

WHERE THEY LIVE, AND WHY THERE
------------------------------
In each person's private copy, made when the copy is built -- NOT in the live
database's schema. Reasons:

* They exist for people looking at the data, not for any program. Putting
  them in ``connectome.db`` would mean a schema change, a migration, and every
  importer carrying views it never uses; here they cost nothing to change.
* The copy is rebuilt from scratch for every new snapshot, so a better
  definition reaches everyone the next morning.
* Datasette shows a view or table exactly like a raw table: sortable,
  filterable, faceted, saveable as CSV. A canned query (the other place they
  could go) gives a form but loses facets and column filters.

Two kinds, chosen per question:

* SUMMARIES (``animals``, ``sessions``) are materialised as tables, because
  they total hundreds of thousands of rows: as views every click on a filter
  or facet would total them again (seconds each). The copy never changes, so a
  materialised summary cannot go out of date while it is open.
* PROJECTIONS AND JOINS (``reaches``, ``brain_region_counts``,
  ``brain_group_counts``) are views: they only pick columns or join two small
  tables, so they are as fast as the tables beneath them and add nothing to
  the copy's size.

Every ready-made table keeps the database's own column names, so each column's
description in the browser is the one the data dictionaries already give
(site.py), and a column means the same thing in the browser, the database and
the exports. A table that is not in the snapshot yet (the brain tables before
the first nightly refresh that copies them) just means its ready-made tables
are not made; nothing fails.
"""
from __future__ import annotations

from typing import Iterable, List, Set

# The cohort an animal belongs to: from the subjects table when it knows the
# animal, otherwise from the id itself (PROJECT_CC_SS -> PROJECT_CC). The
# fallback matters because an animal exists from its first mention -- often a
# video -- before any sheet names it.
_COHORT_FROM_ID = "substr(%s, 1, length(%s) - 3)"

# Columns of the per-reach table worth seeing first, in reading order. Only
# those the snapshot actually has are used, so a renamed or retired column
# never breaks the build.
REACH_COLUMNS = [
    "subject_id", "session_date", "test_phase", "phase_group", "tray_type",
    "run_number", "video_name", "segment_num", "reach_num", "reach_id",
    "contact_group", "causal_reach", "outcome", "segment_outcome",
    "segment_contact_group", "outcome_source",
    "start_frame", "apex_frame", "end_frame", "duration_frames",
    "peak_velocity_px_per_frame", "mean_velocity_px_per_frame",
    "velocity_at_apex_mm_per_sec", "trajectory_straightness",
    "trajectory_smoothness", "hand_angle_at_apex_deg", "hand_rotation_total_deg",
    "head_width_at_apex_mm", "nose_to_slit_at_apex_mm", "head_angle_change_deg",
    "attention_score", "mean_likelihood", "frames_low_confidence",
]

READY_MADE = ("animals", "sessions", "reaches", "brain_region_counts",
              "brain_group_counts")


def _columns(con, table: str) -> List[str]:
    return [r[1] for r in con.execute('PRAGMA table_info("%s")' % table)]


def _c(con, table: str, col: str) -> str:
    """``col`` if ``table`` has it, else NULL -- so an optional column missing
    from an older or newer snapshot empties one column instead of failing the
    whole copy."""
    return col if col in _columns(con, table) else "NULL"


def _pick(con, table: str, cols) -> str:
    have = set(_columns(con, table))
    return ", ".join(c if c in have else "NULL AS %s" % c for c in cols)


def _none(*cols: str) -> str:
    """An empty CTE body with these columns, for a table the snapshot lacks."""
    return "SELECT %s WHERE 0" % ", ".join("NULL AS %s" % c for c in cols)


def create(con, tables: Iterable[str]) -> List[str]:
    """Create the ready-made tables the present ``tables`` allow; returns their names."""
    tables: Set[str] = set(tables)
    made = []
    has = tables.__contains__

    if has("reach_data"):
        made += [_reaches(con)]
    if has("reach_data") or has("pellet_scores"):
        made += [_sessions(con, has), _animals(con, has)]
    if has("brain_samples") and has("region_counts"):
        made += [_brain_regions(con, has)]
    if has("brain_samples") and has("elife_region_counts"):
        made += [_brain_groups(con, has)]
    return made


def _cohort_expr(alias: str, has) -> str:
    own = _COHORT_FROM_ID % (alias + ".subject_id", alias + ".subject_id")
    if has("subjects"):
        return ("COALESCE((SELECT s.cohort_id FROM subjects s WHERE s.subject_id = "
                "%s.subject_id), %s)" % (alias, own))
    return own


def _reaches(con) -> str:
    have = set(_columns(con, "reach_data"))
    cols = [c for c in REACH_COLUMNS if c in have]
    sel = ", ".join('r."%s"' % c for c in cols)
    if "cohort_id" in have:           # added to the copy by build.ADDED_COHORT
        sel = sel.replace('r."subject_id"', 'r."subject_id", r."cohort_id"', 1)
    else:
        sel = sel.replace('r."subject_id"', 'r."subject_id", %s AS cohort_id'
                          % (_COHORT_FROM_ID % ("r.subject_id", "r.subject_id")), 1)
    con.execute("CREATE VIEW reaches AS SELECT %s FROM reach_data r" % sel)
    return "reaches"


def _sessions(con, has) -> str:
    hand = ("SELECT subject_id, date(session_date) AS session_date, tray_type, "
            "MAX(%s) AS test_phase, COUNT(DISTINCT %s) AS trays_hand_scored, "
            "COUNT(*) AS pellets_hand_scored, SUM(score = 2) AS hand_retrieved, "
            "SUM(score = 1) AS hand_displaced, SUM(score = 0) AS hand_untouched "
            "FROM pellet_scores GROUP BY 1, 2, 3" % (_c(con, "pellet_scores", "test_phase"),
                                                     _c(con, "pellet_scores", "tray_number"))
            ) if has("pellet_scores") else _none(
        "subject_id", "session_date", "tray_type", "test_phase", "trays_hand_scored",
        "pellets_hand_scored", "hand_retrieved", "hand_displaced", "hand_untouched")
    algo = ("SELECT subject_id, date(session_date) AS session_date, tray_type, "
            "COUNT(*) AS pellets_in_videos, SUM(outcome = 'retrieved') AS algo_retrieved, "
            "SUM(outcome IN ('displaced_sa', 'displaced_outside')) AS algo_displaced, "
            "SUM(outcome IN ('untouched', 'missed')) AS algo_untouched "
            "FROM video_pellets GROUP BY 1, 2, 3") if has("video_pellets") else _none(
        "subject_id", "session_date", "tray_type", "pellets_in_videos", "algo_retrieved",
        "algo_displaced", "algo_untouched")
    reach = ("SELECT subject_id, date(session_date) AS session_date, tray_type, "
             "MAX(%s) AS test_phase, COUNT(DISTINCT video_name) AS videos, "
             "COUNT(*) AS reaches FROM reach_data GROUP BY 1, 2, 3"
             % _c(con, "reach_data", "test_phase")) if has("reach_data") else _none(
        "subject_id", "session_date", "tray_type", "test_phase", "videos", "reaches")
    weight = ("SELECT subject_id, date(date) AS session_date, MAX(%s) AS weight_grams, "
              "MAX(%s) AS weight_percent FROM weights GROUP BY 1, 2"
              % (_c(con, "weights", "weight_grams"), _c(con, "weights", "weight_percent"))
              ) if has("weights") else _none("subject_id", "session_date", "weight_grams",
                                             "weight_percent")
    con.execute("""
        CREATE TABLE sessions AS
        WITH hand AS (%s), algo AS (%s), reach AS (%s), weight AS (%s),
        keys AS (SELECT subject_id, session_date, tray_type FROM hand
                 UNION SELECT subject_id, session_date, tray_type FROM algo
                 UNION SELECT subject_id, session_date, tray_type FROM reach)
        SELECT k.subject_id, %s AS cohort_id, k.session_date,
               COALESCE(h.test_phase, r.test_phase) AS test_phase, k.tray_type,
               w.weight_grams, w.weight_percent,
               h.trays_hand_scored, h.pellets_hand_scored,
               h.hand_retrieved, h.hand_displaced, h.hand_untouched,
               r.videos, a.pellets_in_videos,
               a.algo_retrieved, a.algo_displaced, a.algo_untouched, r.reaches
        FROM keys k
        LEFT JOIN hand h ON h.subject_id = k.subject_id AND h.session_date = k.session_date
                         AND h.tray_type = k.tray_type
        LEFT JOIN algo a ON a.subject_id = k.subject_id AND a.session_date = k.session_date
                         AND a.tray_type = k.tray_type
        LEFT JOIN reach r ON r.subject_id = k.subject_id AND r.session_date = k.session_date
                          AND r.tray_type = k.tray_type
        LEFT JOIN weight w ON w.subject_id = k.subject_id AND w.session_date = k.session_date
        ORDER BY k.subject_id, k.session_date, k.tray_type
    """ % (hand, algo, reach, weight, _cohort_expr("k", has)))
    con.execute("CREATE INDEX ix_sessions_subject ON sessions(subject_id)")
    con.execute("CREATE INDEX ix_sessions_cohort ON sessions(cohort_id)")
    return "sessions"


def _animals(con, has) -> str:
    ids = " UNION ".join("SELECT subject_id FROM %s" % t for t in
                         ("subjects", "pellet_scores", "reach_data") if has(t))
    subj = ("SELECT %s FROM subjects" % _pick(con, "subjects", (
        "subject_id", "sex", "date_of_birth", "date_of_death", "is_active"))
            if has("subjects") else _none("subject_id", "sex", "date_of_birth",
                                          "date_of_death", "is_active"))
    proj = ("SELECT %s FROM cohorts" % _pick(con, "cohorts", ("cohort_id", "project_code"))
            if has("cohorts")
            else _none("cohort_id", "project_code"))
    injury = ("SELECT subject_id, MIN(surgery_date) AS injury_date, "
              "MAX(%s) AS injury_force_kdyn FROM surgeries "
              "WHERE surgery_type = 'contusion' GROUP BY 1" % _c(con, "surgeries", "force_kdyn")
              if has("surgeries")
              else _none("subject_id", "injury_date", "injury_force_kdyn"))
    con.execute("""
        CREATE TABLE animals AS
        WITH ids AS (%s), subj AS (%s), proj AS (%s), injury AS (%s),
        days AS (SELECT subject_id, MIN(session_date) AS first_session,
                        MAX(session_date) AS last_session,
                        COUNT(DISTINCT session_date) AS testing_days,
                        SUM(pellets_hand_scored) AS pellets_hand_scored,
                        SUM(videos) AS videos, SUM(reaches) AS reaches
                 FROM sessions GROUP BY 1)
        SELECT i.subject_id, %s AS cohort_id, p.project_code,
               s.sex, s.date_of_birth, s.date_of_death, s.is_active,
               j.injury_date, j.injury_force_kdyn,
               d.first_session, d.last_session, d.testing_days,
               d.pellets_hand_scored, d.videos, d.reaches
        FROM ids i
        LEFT JOIN subj s ON s.subject_id = i.subject_id
        LEFT JOIN proj p ON p.cohort_id = %s
        LEFT JOIN injury j ON j.subject_id = i.subject_id
        LEFT JOIN days d ON d.subject_id = i.subject_id
        ORDER BY i.subject_id
    """ % (ids, subj, proj, injury, _cohort_expr("i", has), _cohort_expr("i", has)))
    return "animals"


def _brain_regions(con, has) -> str:
    have = set(_columns(con, "region_counts"))
    extra = [c for c in ("region_acronym", "hemisphere", "cell_type", "cell_count",
                         "cell_density", "region_volume_mm3", "is_final") if c in have]
    con.execute("""
        CREATE VIEW brain_region_counts AS
        SELECT b.subject_id, %s AS cohort_id, b.brain_id, b.status,
               b.registration_approved, r.region_name, %s
        FROM region_counts r JOIN brain_samples b ON b.id = r.brain_sample_id
    """ % (_COHORT_FROM_ID % ("b.subject_id", "b.subject_id"),
           ", ".join("r.%s" % c for c in extra)))
    return "brain_region_counts"


def _brain_groups(con, has) -> str:
    have = set(_columns(con, "elife_region_counts"))
    extra = [c for c in ("hemisphere", "cell_count", "constituent_regions", "is_final")
             if c in have]
    con.execute("""
        CREATE VIEW brain_group_counts AS
        SELECT b.subject_id, %s AS cohort_id, b.brain_id, b.status,
               b.registration_approved, g.group_name, %s
        FROM elife_region_counts g JOIN brain_samples b ON b.id = g.brain_sample_id
    """ % (_COHORT_FROM_ID % ("b.subject_id", "b.subject_id"),
           ", ".join("g.%s" % c for c in extra)))
    return "brain_group_counts"
