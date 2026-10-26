"""ASPA manual scores: the wide '1 - ENTER DATA HERE' layout is parsed right.

WHY: no ASPA manual score had ever reached the database (2026-08-28), so
every ASPA cohort was silently absent from the bench-vs-algorithm scan,
the accuracy figures and the ODC export. The parser must read the real
layout -- four tray blocks side by side, 0/1/2 scores, Pillar/Easy/Flat --
and skip what was never scored.
"""
from datetime import datetime

import openpyxl

from mousedb.cohort_tools.import_aspa_scores import parse_workbook, SCORE_SHEET


def _book(tmp_path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = SCORE_SHEET
    header = ["Test Date", "Test Type", "Tray Type", "Test Phase", "Group", "Animal #"]
    for _ in range(4):
        header += ["Tray #"] + [str(i) for i in range(1, 21)] + ["Displaced", "Eaten", "hit"]
    ws.append(header)
    for r in rows:
        ws.append(r)
    p = tmp_path / "J.xlsx"
    wb.save(p)
    return p


def _row(date, tray_type, animal, trays):
    """trays: list of (tray_num, [20 scores or None])"""
    out = [date, "01 - Train", tray_type, "Training", "J", animal]
    for tn, scores in trays:
        out += [tn] + list(scores) + [None, None, None]
    while len(out) < 6 + 4 * 24:
        out += [None] * 24
    return out


def test_four_tray_blocks_and_the_score_vocabulary(tmp_path):
    scored = [2, 1, 0, 2] * 5
    p = _book(tmp_path, [
        _row(datetime(2022, 8, 10), "Pillar", 11, [(1, scored), (2, scored), (3, [None] * 20), (4, scored)]),
    ])
    w = []
    rows = parse_workbook(p, "ASPA_10", w)
    assert w == []
    assert [(r[0], str(r[1]), r[2], r[3]) for r in rows] == [
        ("ASPA_10_11", "2022-08-10", "P", 1),
        ("ASPA_10_11", "2022-08-10", "P", 2),
        ("ASPA_10_11", "2022-08-10", "P", 4),   # tray 3 unscored -> skipped
    ]
    assert rows[0][4] == {i + 1: s for i, s in enumerate(scored)}


def test_tray_number_is_the_block_position_whatever_its_cell_holds(tmp_path):
    """WHY: some workbooks put the animal's label ('H1') or nothing in the
    'Tray #' cell above blocks 2-4; reading that cell as the number dropped
    every such tray, so a cohort reached the database as tray 1 only."""
    scored = [0, 1, 2, 0] * 5
    p = _book(tmp_path, [
        _row(datetime(2022, 5, 25), "Pillar", 1, [(1, scored), ("H1", scored), (None, scored), ("H1", scored)]),
    ])
    w = []
    rows = parse_workbook(p, "ASPA_08", w)
    assert w == []
    assert [r[3] for r in rows] == [1, 2, 3, 4]


def test_a_tray_number_that_disagrees_with_its_block_is_reported_not_guessed(tmp_path):
    scored = [2] * 20
    p = _book(tmp_path, [
        _row(datetime(2022, 5, 25), "Pillar", 1, [(1, scored), (3, scored)]),
    ])
    w = []
    rows = parse_workbook(p, "ASPA_08", w)
    assert [r[3] for r in rows] == [1]
    assert any("tray block 2 is headed tray 3" in x for x in w)


def test_tray_types_map_and_unknown_is_reported(tmp_path):
    p = _book(tmp_path, [
        _row(datetime(2022, 8, 10), "Easy", 1, [(1, [0] * 20)]),
        _row(datetime(2022, 8, 11), "Flat", 1, [(1, [1] * 20)]),
        _row(datetime(2022, 8, 12), "Recessed", 1, [(1, [2] * 20)]),
    ])
    w = []
    rows = parse_workbook(p, "ASPA_10", w)
    assert [r[2] for r in rows] == ["E", "F"]
    assert any("unknown tray type" in x for x in w)


def test_bad_scores_are_reported_not_stored(tmp_path):
    cells = [2] * 19 + [12]
    p = _book(tmp_path, [_row(datetime(2022, 8, 10), "Pillar", 3, [(1, cells)])])
    w = []
    rows = parse_workbook(p, "ASPA_10", w)
    assert 20 not in rows[0][4] and len(rows[0][4]) == 19
    assert any("out of range" in x for x in w)


def test_missing_tab_is_a_warning_not_a_crash(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "Weights"
    p = tmp_path / "I.xlsx"
    wb.save(p)
    w = []
    assert parse_workbook(p, "ASPA_09", w) == []
    assert w and "no '%s' tab" % SCORE_SHEET in w[0]


def test_a_date_cell_holding_only_a_day_is_read_in_the_month_around_it(tmp_path):
    """WHY: typed "30" into a date cell is Excel's 1900-01-30. A whole session
    was filed under 1900 that way: it met none of its videos and gave its
    cohort a start date of 1900."""
    scored = [2] * 20
    p = _book(tmp_path, [
        _row(datetime(2022, 6, 29), "Pillar", 1, [(1, scored)]),
        _row(datetime(1900, 1, 30), "Pillar", 1, [(1, scored)]),
        _row(datetime(1900, 1, 30), "Pillar", 2, [(1, scored)]),
        _row(datetime(2022, 7, 1), "Pillar", 1, [(1, scored)]),
    ])
    w, repairs = [], {}
    rows = parse_workbook(p, "ASPA_07", w, repairs)
    assert [str(r[1]) for r in rows] == ["2022-06-29", "2022-06-30", "2022-06-30", "2022-07-01"]
    assert repairs == {datetime(1900, 1, 30).date(): {datetime(2022, 6, 30).date()}}
    assert any(x.startswith("REPAIRED J.xlsx: a date cell held only the day (30)") for x in w)


def test_a_day_that_fits_no_month_around_it_is_left_out_and_reported(tmp_path):
    scored = [2] * 20
    p = _book(tmp_path, [
        _row(datetime(2022, 6, 29), "Pillar", 1, [(1, scored)]),
        _row(datetime(1900, 1, 12), "Pillar", 1, [(1, scored)]),     # 06-12 / 07-12: outside
        _row(datetime(2022, 7, 1), "Pillar", 1, [(1, scored)]),
    ])
    w = []
    rows = parse_workbook(p, "ASPA_07", w)
    assert [str(r[1]) for r in rows] == ["2022-06-29", "2022-07-01"]
    assert any("could not be read from the rows around it" in x for x in w)


def test_the_import_moves_scores_filed_under_1900_and_corrects_a_1900_start(tmp_path, monkeypatch):
    from datetime import date
    import mousedb.cohort_sheets as cs
    import mousedb.database as dbmod
    import mousedb.animal_records as ar
    from mousedb.cohort_tools import import_aspa_scores as ias
    from mousedb.schema import Cohort, PelletScore, Project, Subject
    scored = [1] * 20
    book = _book(tmp_path, [
        _row(datetime(2022, 6, 29), "Pillar", 1, [(1, scored)]),
        _row(datetime(1900, 1, 30), "Pillar", 1, [(1, scored)]),
        _row(datetime(2022, 7, 1), "Pillar", 1, [(1, scored)]),
    ])
    db = dbmod.init_database(tmp_path / "scratch.db")
    with db.session() as s:
        if not s.query(Project).filter_by(project_code="ASPA").first():
            s.add(Project(project_code="ASPA", project_name="ASPA"))
        s.add(Cohort(cohort_id="ASPA_07", project_code="ASPA", start_date=date(1900, 1, 30)))
        s.add(Subject(subject_id="ASPA_07_01", cohort_id="ASPA_07"))
        for p in range(1, 21):   # what the old import filed
            s.add(PelletScore(subject_id="ASPA_07_01", session_date=date(1900, 1, 30),
                              test_phase="Pillar", tray_type="P", tray_number=1,
                              pellet_number=p, score=1, entered_by="excel_import"))
        s.commit()
    monkeypatch.setattr(cs, "find_aspa_sheet", lambda letter: book)
    monkeypatch.setattr(dbmod, "init_database", lambda *a, **k: db)
    monkeypatch.setattr(ar, "sync_cohort", lambda *a, **k: {"records": 0, "warnings": [], "error": None})
    monkeypatch.setattr(ias, "_append_ledger", lambda e: None, raising=False)
    import mousedb.sheet_sync as ss
    monkeypatch.setattr(ss, "_append_ledger", lambda e: None)

    e = ias.import_cohort("G", apply=True)

    assert e["success"], e
    assert e["start_date_corrected"] == {"from": "1900-01-30", "to": "2022-06-29"}
    with db.session() as s:
        days = {str(d) for (d,) in s.query(PelletScore.session_date).distinct()}
        assert "1900-01-30" not in days and "2022-06-30" in days
        assert s.query(PelletScore).filter_by(session_date=date(2022, 6, 30)).count() == 20
        assert str(s.query(Cohort).filter_by(cohort_id="ASPA_07").one().start_date) == "2022-06-29"
