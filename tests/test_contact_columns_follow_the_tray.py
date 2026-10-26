"""The contact columns: Not applicable on easy/flat for the algorithm; pellet words.

WHY (Logan, 2026-10-26): "the algo shouldn't be analyzing pellet outcomes on easy
and flat trays ... The result there should be 'not applicable' for the algo
pellet/reach outcome for flat and easy trays and should be whatever the manual
says for the manual pellet outcome." And a pellet nobody touched is "untouched";
"missed" is a word for a reach. The database computed 'missed' on every easy/flat
reach and on every undecided pellet, and used 'missed' for untouched pellets.
"""
import sqlite3

import pandas as pd

from mousedb.database import Database
from mousedb.exporters import missing
from mousedb.exporters.current import refresh_current
from mousedb.migrations import CONTACT_COLUMNS, bring_contact_columns_current

OLD_REACH = "CASE WHEN interaction_frame IS NOT NULL THEN 'contacted' ELSE 'missed' END"
OLD_SEGMENT = ("CASE WHEN segment_outcome IN ('retrieved', 'displaced_sa', 'displaced_outside') "
               "THEN 'contacted' ELSE 'missed' END")
OLD_PELLET = "CASE WHEN score = 0 THEN 'missed' ELSE 'contacted' END"


def _old_database(path):
    """The shape the live database had before 2026-10-26."""
    conn = sqlite3.connect(str(path))
    conn.executescript(f"""
        CREATE TABLE reach_data (id INTEGER PRIMARY KEY, tray_type TEXT, interaction_frame INTEGER,
            segment_outcome TEXT,
            contact_group TEXT GENERATED ALWAYS AS ({OLD_REACH}) VIRTUAL,
            segment_contact_group TEXT GENERATED ALWAYS AS ({OLD_SEGMENT}) VIRTUAL);
        CREATE TABLE pellet_scores (id INTEGER PRIMARY KEY, tray_type TEXT, score INTEGER,
            contact_group TEXT GENERATED ALWAYS AS ({OLD_PELLET}) VIRTUAL);
    """)
    conn.executemany("INSERT INTO reach_data (tray_type, interaction_frame, segment_outcome) VALUES (?, ?, ?)", [
        ("F", None, None),                      # 1 flat tray: nothing analysed
        ("E", None, None),                      # 2 easy tray
        ("P", 120, "retrieved"),                # 3 the reach that took it
        ("P", None, "retrieved"),               # 4 another reach at the same pellet
        ("P", None, "untouched"),               # 5 a pellet nothing moved
        ("P", None, "triaged"),                 # 6 not decided
        ("P", None, "abnormal_exception"),      # 7 moved by something other than a reach
    ])
    conn.executemany("INSERT INTO pellet_scores (tray_type, score) VALUES (?, ?)",
                     [("F", 0), ("F", 2), ("P", 0), ("P", 1)])
    conn.commit()
    return conn


def test_an_old_database_is_brought_to_the_current_words(tmp_path):
    conn = _old_database(tmp_path / "old.db")
    with conn:
        changed = bring_contact_columns_current(conn)
    assert changed == ["%s.%s" % (t, c) for t, c, _ in CONTACT_COLUMNS]
    rows = conn.execute("SELECT contact_group, segment_contact_group FROM reach_data ORDER BY id").fetchall()
    assert rows == [(None, None), (None, None),                 # easy/flat: not applicable
                    ("contacted", "contacted"), ("missed", "contacted"),
                    ("missed", "untouched"),                     # a reach misses; a pellet is untouched
                    ("missed", None), ("missed", None)]          # no algorithm answer for the pellet
    hand = [r[0] for r in conn.execute("SELECT contact_group FROM pellet_scores ORDER BY id")]
    assert hand == ["untouched", "contacted", "untouched", "contacted"]   # every tray, as scored
    with conn:
        assert bring_contact_columns_current(conn) == []         # a no-op once current


def test_a_new_database_already_has_the_current_words(tmp_path):
    db = Database(tmp_path / "new.db")
    db.init_db()
    conn = sqlite3.connect(str(db.db_path))
    with conn:
        assert bring_contact_columns_current(conn) == []


def test_reach_data_csv_says_not_applicable_for_the_algorithm_on_easy_and_flat(tmp_path):
    rd = pd.DataFrame({
        "id": [1, 2], "subject_id": ["PROJA_01_01"] * 2,
        "video_name": ["20260601_PROJA0101_F1", "20260602_PROJA0101_P1"],
        "session_date": ["2026-06-01", "2026-06-02"], "tray_type": ["F", "P"],
        "run_number": [1, 1], "segment_num": [1, 1], "reach_id": [1, 1], "reach_num": [1, 1],
        "outcome": [None, "retrieved"], "causal_reach": [0, 1],
        "segment_outcome": [None, "retrieved"], "contact_group": [None, "contacted"],
        "segment_contact_group": [None, "contacted"], "outcome_source": [None, "algo"],
        "extended_features": ["{}"] * 2, "test_phase": ["Rehab Flat", "Pillar"],
    })
    ps = pd.DataFrame({
        "id": [1], "subject_id": ["PROJA_01_01"], "session_date": ["2026-06-01"],
        "test_phase": ["Rehab Flat"], "phase_group": ["Rehab_Flat"], "tray_type": ["F"],
        "tray_number": [1], "pellet_number": [1], "score": [0], "contact_group": ["untouched"],
        "entered_by": ["excel_import"], "entered_at": ["2026-06-02"],
    })
    snap = tmp_path / "snap"
    snap.mkdir()
    rd.to_parquet(snap / "reach_data.parquet", index=False)
    ps.to_parquet(snap / "pellet_scores.parquet", index=False)
    out = tmp_path / "current"
    refresh_current(snap, out, db_ok=False)
    r = pd.read_csv(out / "reach_data.csv", dtype=str, keep_default_na=False)
    flat, pillar = r[r.tray_type == "F"].iloc[0], r[r.tray_type == "P"].iloc[0]
    for column in ("outcome", "causal_reach", "segment_outcome", "contact_group",
                   "segment_contact_group", "outcome_source"):
        assert flat[column] == missing.NOT_APPLICABLE, column
    assert pillar["contact_group"] == "contacted" and pillar["segment_outcome"] == "retrieved"
    m = pd.read_csv(out / "manual_scores.csv", dtype=str)
    assert m.iloc[0]["contact_group"] == "untouched"            # the hand score, kept on a flat tray
