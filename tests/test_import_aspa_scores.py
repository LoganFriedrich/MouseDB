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
