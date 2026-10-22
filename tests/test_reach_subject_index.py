"""reach_data carries an index on (subject_id, session_date).

WHY (2026-10-22): the phase re-labelling after every reach import selects one
cohort's rows by subject_id. With no index SQLite read the whole table once per
touched cohort -- hours over the share (2026-10-15/16). A new database gets the
index from schema.py; an existing one from import_reaches.ensure_columns.
"""
import sqlite3

from mousedb import import_reaches


def test_ensure_columns_adds_the_index_and_the_relabel_query_uses_it(tmp_path):
    db = tmp_path / "c.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE subjects(subject_id TEXT PRIMARY KEY, cohort_id TEXT);"
        "CREATE TABLE reach_data(id INTEGER PRIMARY KEY, subject_id TEXT, video_name TEXT,"
        " session_date TEXT, reach_id INT);")
    import_reaches.ensure_columns(con)
    import_reaches.ensure_columns(con)          # idempotent
    names = {r[1] for r in con.execute("PRAGMA index_list(reach_data)")}
    assert "ix_reach_data_subject_session" in names
    plan = " ".join(r[3] for r in con.execute(
        "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM reach_data WHERE subject_id IN "
        "(SELECT subject_id FROM subjects WHERE cohort_id = 'X')"))
    assert "USING INDEX ix_reach_data_subject_session" in plan or "USING COVERING INDEX ix_reach_data_subject_session" in plan


def test_a_new_database_gets_it_from_the_schema(tmp_path):
    from sqlalchemy import create_engine
    from mousedb.schema import Base
    eng = create_engine("sqlite:///%s" % (tmp_path / "n.db"))
    Base.metadata.create_all(eng)
    con = sqlite3.connect(tmp_path / "n.db")
    assert "ix_reach_data_subject_session" in {r[1] for r in con.execute("PRAGMA index_list(reach_data)")}
