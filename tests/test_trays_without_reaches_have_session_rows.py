"""Every filed tray has a session row -- also a tray at which the animal never reached.

Owner, 2026-11-24: "No reaches means no REACH rows. Session rows should still
exist in the session files." The sessions document was built from reach rows
only, so a tray with no reach (often THE result after an injury) vanished from
it, with its attention score and its pellet outcomes. Now the import records
every results file as one row of video_sessions, and the exporter gives each
filed tray with no reach one session-only row that only the sessions document
keeps: 0 reaches, every session number that does not depend on reaches filled.
"""
import contextlib
import json
import sqlite3

import pandas as pd

from mousedb import import_reaches as ir
from mousedb import task_mutex
from mousedb.database import Database
from mousedb.exporters import odc_reaches as ox

SID = "PROJA_01_01"
DAY = "2025-07-10"
REACHED = "20250710_PROJA0101_P1"
NO_REACH = "20250710_PROJA0101_P2"
EASY = "20250710_PROJA0101_E1"


# ------------------------------------------------------------------ import

def _features(folder, video, reached=(), easy=False, att=None):
    segs = []
    n = 1 if easy else 20
    for s in range(1, n + 1):
        reaches = ([{"reach_id": s, "reach_num": 1, "start_frame": 10 * s, "end_frame": 10 * s + 5,
                     "causal_reach": True, "is_first_reach": True, "is_last_reach": True,
                     "n_reaches_in_segment": 1, "flagged_for_review": False,
                     "duration_frames": 5}]
                   if s in reached else [])
        segs.append({"segment_num": s, "outcome": None if easy else "untouched",
                     "reaches": reaches,
                     "attention_score": (att[s - 1] if att else float(s))})
    path = folder / (video + "_features.json")
    path.write_text(json.dumps({"video_name": video, "extractor_version": "9.9.9",
                                "segments": segs}), encoding="utf-8")
    return path


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_db()
    conn = sqlite3.connect(str(db.db_path))
    with conn:
        ir.ensure_columns(conn)
    return conn


def test_a_tray_with_no_reach_gets_a_session_row_with_its_attention(tmp_path):
    conn = _db(tmp_path)
    ir.import_file(conn, _features(tmp_path, NO_REACH))
    assert conn.execute("SELECT COUNT(*) FROM reach_data").fetchone()[0] == 0
    row = conn.execute("SELECT subject_id, tray_type, run_number, n_segments, n_reaches, "
                       "attention_score, attention_segments FROM video_sessions "
                       "WHERE video_name = ?", (NO_REACH,)).fetchone()
    # attention: the mean over EVERY segment (1..20 -> 10.5), reached for or not
    assert row == (SID, "P", 2, 20, 0, 10.5, 20)


def test_an_easy_tray_with_no_reach_gets_a_session_row_and_no_pellet_rows(tmp_path):
    conn = _db(tmp_path)
    ir.import_file(conn, _features(tmp_path, EASY, easy=True, att=[61.5]))
    assert conn.execute("SELECT COUNT(*) FROM video_pellets").fetchone()[0] == 0
    assert conn.execute("SELECT tray_type, n_reaches, attention_score FROM video_sessions"
                        ).fetchone() == ("E", 0, 61.5)


def test_the_backfill_fills_session_rows(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    root = tmp_path / "pipe"
    folder = root / "Analyzed" / "Project"
    folder.mkdir(parents=True)
    ir.import_file(conn, _features(folder, NO_REACH))
    ir.import_file(conn, _features(folder, REACHED, reached=(3,)))
    conn.execute("DELETE FROM video_sessions")
    conn.commit()
    monkeypatch.setattr(task_mutex, "hold", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(ir, "require", lambda key: tmp_path / "t.db" if key == "db_path" else root)
    out = ir.backfill_video_pellets(log=lambda *_: None)
    assert out["sessions"] == 2 and out["sessions_without_reaches"] == 1
    assert dict(conn.execute("SELECT video_name, n_reaches FROM video_sessions")) == {
        NO_REACH: 0, REACHED: 1}


def test_a_withdrawn_video_loses_its_session_row_too(tmp_path):
    conn = _db(tmp_path)
    ir.import_file(conn, _features(tmp_path, NO_REACH))
    root = tmp_path / "pipe"
    queue = root / "Processing" / "Review" / "triage" / "Pending" / NO_REACH
    queue.mkdir(parents=True)
    _features(queue, NO_REACH)
    v, n, _ = ir.withdraw_videos_back_in_review(conn, root, {}, {}, tmp_path / "bk",
                                                log=lambda *_: None)
    assert v == 1
    assert conn.execute("SELECT COUNT(*) FROM video_sessions").fetchone()[0] == 0
    assert (tmp_path / "bk" / (NO_REACH + "_video_sessions.csv")).is_file()


# ------------------------------------------------------------------ export

def _reach_rows():
    rows = []
    for seg in (1, 2):
        rows.append({"subject_id": SID, "video_name": REACHED, "session_date": DAY,
                     "tray_type": "P", "run_number": 1, "segment_num": seg, "reach_id": seg,
                     "reach_num": 1, "outcome": "retrieved", "causal_reach": 1,
                     "segment_outcome": "retrieved", "start_frame": 10 * seg,
                     "end_frame": 10 * seg + 5, "attention_score": 4.0,
                     "test_phase": "Pillar_Training", "phase_group": "Training",
                     "session_flag": None, "session_flag_reason": None,
                     "session_flag_by": None, "session_flag_at": None,
                     "extended_features": "{}"})
    return pd.DataFrame(rows)


def _video_sessions():
    return pd.DataFrame([
        {"video_name": v, "subject_id": SID, "session_date": DAY, "tray_type": t, "run_number": r,
         "n_segments": n, "n_reaches": k, "attention_score": a, "attention_segments": n,
         "extractor_version": "9.9.9", "session_flag": None, "session_flag_reason": None,
         "session_flag_by": None, "session_flag_at": None, "source_file": v + "_features.json",
         "imported_at": "x"}
        for v, t, r, n, k, a in ((REACHED, "P", 1, 20, 2, 3.0), (NO_REACH, "P", 2, 20, 0, 1.25),
                                 (EASY, "E", 1, 1, 0, 61.5))])


def _video_pellets():
    rows = []
    for v, run in ((REACHED, 1), (NO_REACH, 2)):
        for s in range(1, 21):
            rows.append({"video_name": v, "segment_num": s, "subject_id": SID, "session_date": DAY,
                         "tray_type": "P", "run_number": run,
                         "outcome": "retrieved" if (v == REACHED and s <= 2) else "untouched"})
    return pd.DataFrame(rows)


def _hand_scores():
    rows = []
    for tt, tn in (("P", 1), ("P", 2), ("E", 1)):
        for pn in range(1, 21):
            rows.append({"subject_id": SID, "session_date": DAY, "tray_type": tt,
                         "tray_number": tn, "pellet_number": pn,
                         "score": 2 if (tt, tn, pn) in (("P", 1, 1), ("P", 1, 2), ("P", 2, 5)) else 0,
                         "test_phase": "Pillar_Training", "phase_group": "Training"})
    return pd.DataFrame(rows)


def _build(vs=True):
    subjects = pd.DataFrame([{"subject_id": SID, "sex": "F", "date_of_birth": None}])
    return ox.build_cohort("PROJA_01", _reach_rows(), subjects, None, _hand_scores(),
                           lambda s: {}, "lab-x", video_pellets=_video_pellets(),
                           video_sessions=_video_sessions() if vs else None)


def _written(tmp_path, vs=True):
    df, rows = _build(vs)
    entry, _ = ox.write_cohort(tmp_path, "PROJA_01", df, rows)
    files = {p.name: p for p in tmp_path.rglob("*.csv")}
    read = lambda n: pd.read_csv(files[n], dtype=str, keep_default_na=False)
    return entry, read


def test_the_sessions_file_has_every_filed_tray_and_the_reach_files_only_reaches(tmp_path):
    entry, read = _written(tmp_path)
    sessions = read("sessions_PROJA_01_analysis.csv").set_index("video_name")
    assert set(sessions.index) == {REACHED, NO_REACH, EASY}
    assert entry["session_rows_without_reaches"] == 2
    for name in ("ODC_reaches_PROJA_01.csv", "reaches_PROJA_01_summary.csv",
                 "reaches_PROJA_01_analysis.csv"):
        f = read(name)
        col = "video_name" if "video_name" in f.columns else "session_video"
        assert set(f[col]) == {REACHED}, name                # no reaches -> no reach rows
        assert len(f) == 2, name
    analysis = read("reaches_PROJA_01_analysis.csv")
    assert list(analysis["entry_index"]) == ["1", "2"]


def test_a_pillar_tray_with_no_reach_has_every_session_number(tmp_path):
    _, read = _written(tmp_path)
    s = read("sessions_PROJA_01_analysis.csv").set_index("video_name").loc[NO_REACH]
    assert s["session_reaches_ai"] == "0"
    assert s["session_attention_ai"] == "1.25"
    assert s["session_pellets_ai"] == "20" and s["session_untouched_ai"] == "20"
    assert s["session_retrieved_ai"] == "0" and s["session_percent_retrieved_ai"] == "0"
    assert s["session_pellets_hand"] == "20" and s["session_retrieved_hand"] == "1"
    assert s["session_percent_retrieved_hand"] == "5"
    assert s["session_pellets_presented"] == "20"
    assert s["session_pellets_reached_for"] == "0"
    assert s["session_reaches_that_decided_a_pellet"] == "0"
    assert s["session_reaches_after_the_pellet_was_gone"] == "0"
    assert s["test_type"] == "Pillar_Training" and s["tray_id"] == "P2"
    assert s["session_flag"] == "No"
    # nothing in a session row is blank because the tray had no reach
    assert "NA" not in set(s.drop(["days_post_injury", "session_flag_reason", "session_flag_by",
                                   "session_flag_at", "injury_type", "injury_level",
                                   "study_group", "strain", "survived_all_surgeries",
                                   "excluded_in_origin_study"], errors="ignore")
                      .drop(_reach_derived(s.index)))


def test_an_easy_tray_with_no_reach_has_its_attention_and_completes_the_day(tmp_path):
    _, read = _written(tmp_path)
    sessions = read("sessions_PROJA_01_analysis.csv").set_index("video_name")
    e = sessions.loc[EASY]
    assert e["session_reaches_ai"] == "0" and e["session_attention_ai"] == "61.5"
    assert e["session_pellets_ai"] == "NA"                 # easy tray: not counted, as on any
    assert e["session_pellets_reached_for"] == "NA"
    # every tray of the day is filed, so the day is complete and counts 2 reaches
    assert set(sessions["day_complete"]) == {"yes"}
    assert set(sessions["day_reaches_ai"]) == {"2"}
    reaches = read("reaches_PROJA_01_analysis.csv")
    assert set(reaches["day_reaches_ai"]) == {"2"} and set(reaches["day_complete"]) == {"yes"}
    # the reached tray too: the mean over every segment (video_sessions), not
    # over its reached segments (owner decision 2026-11-24)
    assert float(sessions.loc[REACHED, "session_attention_ai"]) == 3.0


def test_a_tray_with_reaches_gets_the_all_segment_mean_on_every_file(tmp_path):
    # The reached segments score 4.0; the tray's 20 segments average 3.0. Every
    # file shows the tray's mean, never the reached-segment mean.
    _, read = _written(tmp_path)
    reaches = read("reaches_PROJA_01_analysis.csv")
    assert {float(v) for v in reaches["session_attention_ai"]} == {3.0}
    sessions = read("sessions_PROJA_01_analysis.csv").set_index("video_name")
    assert float(sessions.loc[REACHED, "session_attention_ai"]) == 3.0
    odc = read("ODC_reaches_PROJA_01.csv")
    col = [c for c in odc.columns if c.lower() == "session_attention_ai"]
    assert col and {float(v) for v in odc[col[0]]} == {3.0}


def test_without_the_session_table_a_reached_tray_falls_back_to_its_reached_segments(tmp_path):
    _, read = _written(tmp_path, vs=False)
    sessions = read("sessions_PROJA_01_analysis.csv").set_index("video_name")
    assert float(sessions.loc[REACHED, "session_attention_ai"]) == 4.0


def test_the_segment_attention_is_published_as_segment_attention(tmp_path):
    entry, read = _written(tmp_path)
    for name in ("reaches_PROJA_01_analysis.csv", "ODC_reaches_PROJA_01.csv"):
        f = read(name)
        lower = {c.lower(): c for c in f.columns}
        assert "segment_attention" in lower, name
        assert {float(v) for v in f[lower["segment_attention"]]} == {4.0}, name
        assert "reach_attention" not in lower and "attention_score" not in lower, name
    texts = {}
    for key in ("_analysis_dictionary", "_complete_dictionary"):
        for r in entry[key]:
            if str(r.get("VariableName", "")).lower() == "segment_attention":
                texts[key] = " ".join(str(v) for v in r.values())
    assert set(texts) == {"_analysis_dictionary", "_complete_dictionary"}
    for name, t in texts.items():
        assert "SEGMENT value" in t, name


def test_without_the_session_table_an_easy_tray_with_no_reach_leaves_the_day_incomplete(tmp_path):
    _, read = _written(tmp_path, vs=False)
    sessions = read("sessions_PROJA_01_analysis.csv").set_index("video_name")
    assert set(sessions.index) == {REACHED, NO_REACH}       # pillar from video_pellets
    assert sessions.loc[NO_REACH, "session_attention_ai"] == "NA"
    assert set(sessions["day_complete"]) == {"no"}


def test_the_dictionary_says_what_a_tray_with_no_reach_holds(tmp_path):
    entry, _ = _written(tmp_path)
    named = {r["VariableName"]: r for r in entry["_sessions_dictionary"]}
    assert "On the row of a tray with no reach" in named["session_reaches_ai"]["Comments"]
    assert "0." in named["session_reaches_ai"]["Comments"]
    assert "does not depend on reaches" in named["session_retrieved_hand"]["Comments"]
    assert "every segment" in named["session_attention_ai"]["Comments"]


def test_an_excluded_tray_with_no_reach_is_reported_as_a_session_row(tmp_path):
    from mousedb.exporters import export_exclusions as xe
    ex = xe.Exclusions()
    ex.drop(NO_REACH, "identity: test")
    df, rows = _build()
    entry, _ = ox.write_cohort(tmp_path, "PROJA_01", df, rows, exclusions=ex)
    rep = [r for r in entry["_exclusion_report"] if r["video_name"] == NO_REACH]
    assert rep and rep[0]["left_out"].startswith("session row") and rep[0]["rows"] == 0
    sessions = pd.read_csv(next(tmp_path.rglob("sessions_*_analysis.csv")), dtype=str)
    assert NO_REACH not in set(sessions["video_name"])


def test_the_incremental_export_notices_a_new_session_row(tmp_path):
    from mousedb.exporters import incremental
    fps = []
    for vs in (_video_sessions().iloc[:1], _video_sessions()):
        t = {"reach": _reach_rows(), "subjects": None, "records": None, "pellets": None,
             "video_pellets": None, "video_sessions": vs}
        fps.append(incremental.cohort_fingerprints(t, ["PROJA_01"], None, tmp_path, "lab-x"))
    assert fps[0]["PROJA_01"]["fingerprint"] != fps[1]["PROJA_01"]["fingerprint"]


def test_the_per_reach_files_are_byte_for_byte_what_they_were_without_session_rows(tmp_path):
    """Session-only rows never change a per-reach file: not a value, not how a
    number is written (an integer column must not turn into 1.0 because the
    rows that were set aside for the sessions file had no value in it)."""
    subjects = pd.DataFrame([{"subject_id": SID, "sex": "F", "date_of_birth": None}])
    hand = _hand_scores()
    hand = hand[(hand["tray_type"] == "P") & (hand["tray_number"] == 1)]
    reach = _reach_rows()
    reach["reach_id"] = reach["reach_id"].astype("int64")
    texts = []
    vp_all = _video_pellets()
    only_reached = (_video_sessions().iloc[:1], vp_all)   # as before: the no-reach tray only in video_pellets
    for i, (vs, vp) in enumerate((only_reached, (_video_sessions(), vp_all))):
        out = tmp_path / str(i)
        df, rows = ox.build_cohort("PROJA_01", reach, subjects, None, hand, lambda s: {},
                                   "lab-x", video_pellets=vp, video_sessions=vs)
        ox.write_cohort(out, "PROJA_01", df, rows)
        texts.append({p.name: p.read_bytes() for p in out.rglob("*.csv")
                      if not p.name.startswith("sessions_")})
    assert texts[0].keys() == texts[1].keys() and len(texts[0]) == 3
    for name in texts[0]:
        assert texts[0][name] == texts[1][name], name


def _reach_derived(columns):
    """The session measurements computed FROM reaches (exporters.session_metrics):
    NA on a tray with no reach by rule, and the inputs these test rows do not
    carry (video length, recording status, weight)."""
    from mousedb.exporters import session_metrics as sm
    kin = [c for c in columns if c.startswith(("session_median_reach_", "session_mean_reach_",
                                               "session_sem_reach_", "session_n_reach_"))]
    absent_inputs = ["session_duration_sec", "session_reaches_per_minute",
                     "day_recording_status", "day_recording_issue_reason",
                     "day_body_weight_g", "day_body_weight_percent_of_free_feeding",
                     "session_percent_retrieved_of_contacted_ai",
                     "session_median_latency_to_first_reach_sec",
                     "session_mean_inter_reach_interval_sec",
                     "session_mean_pellet_position_idealness",
                     "session_displaced_in_scoring_area_ai", "session_displaced_outside_ai",
                     "session_moved_not_by_reach_ai"]
    return [c for c in list(sm.FROM_REACHES) + kin + absent_inputs if c in columns]
