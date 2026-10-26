"""Recognise a year typed wrong in a column of session dates.

WHY
---
A tracking sheet is typed by hand, and a session typed as last year (a
2025 for 2026 at the turn of a month) is filed a whole year away from the
rest of its cohort. That one wrong digit does more than strand the
session: phases are derived from the gaps between a cohort's sessions
(mousedb.phases), so a session a year early reads as a long break after
it and every real session after that break is labelled as if it came
after an injury. The scores also no longer meet the videos of that day,
so the hand-score check cannot find them.

THE RULE
--------
In one tab's date column, take the year most rows carry. A date in any
other year is a year typo when, with only its year changed to that one,
it falls inside the span of the rows that do carry it (between their
earliest and latest date). Nothing else is ever changed: a date that
would land outside that span is left alone, so a cohort that runs over
New Year keeps its December and January sessions as typed.

The correction is reported every time the sheet is read, so the person
who keeps the sheet sees it until the sheet itself is corrected.

A WEEKEND DATE INSIDE A DAY'S BLOCK (block_date_repairs)
--------------------------------------------------------
Some workbooks do not type their dates: each row's date cell is a formula
such as ``=A419+1``, "the date a fixed number of rows up, plus one". That
holds only while every day's block has the same number of rows. Cut a row
(an animal leaves the cohort) or leave one blank, and the formulas below it
count from the wrong row: CNT_04's rows for animals 02-04, tray 1, sit in the
2025-10-28 block but compute to 2025-10-25, a Saturday. Their scores were
filed under a day nothing was recorded, and the videos of the 28th found no
hand scores. The rule: a run of rows dated on a WEEKEND (recording never
happens then -- Logan, 2026-10-22), sitting between rows that all carry one
and the same weekday date, is read as that weekday -- provided none of its
animal/tray pairs is already on that day. A weekday date out of place (a
make-up session typed among the regular rows) is never touched, and neither
is a whole block on a weekend: with no neighbours to read it from, the day
cannot be lined up (Logan: "if you can't line up one day who cares").
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Dict, Iterable, List, Sequence, Tuple


def year_typo_repairs(dates: Iterable[date]) -> Dict[date, date]:
    """{date as typed: date it was meant to be} for every year typo in one
    column of session dates (one entry per row, repeats included: the year
    most ROWS carry is the cohort's year). Empty when there is none."""
    dates = [d for d in dates if d is not None]
    if not dates:
        return {}
    year, _ = Counter(d.year for d in dates).most_common(1)[0]
    in_year = [d for d in dates if d.year == year]
    first, last = min(in_year), max(in_year)
    repairs = {}
    for d in set(dates):
        if d.year == year:
            continue
        try:
            meant = d.replace(year=year)
        except ValueError:          # 29 February in a year without one
            continue
        if first <= meant <= last:
            repairs[d] = meant
    return repairs


def block_date_repairs(rows: Sequence[Tuple]) -> Dict[int, date]:
    """{row index: date it was meant to be} for every weekend-dated run inside a
    day's block (see the module docstring). ``rows`` are (animal, date,
    tray_type, tray_number) in SHEET ORDER; the index is into that list.
    Empty when there is none."""
    runs: List[Tuple[int, int, date]] = []          # (first index, last index, date)
    for i, r in enumerate(rows):
        d = r[1]
        if runs and runs[-1][2] == d:
            runs[-1] = (runs[-1][0], i, d)
        else:
            runs.append((i, i, d))
    repairs: Dict[int, date] = {}
    for k in range(1, len(runs) - 1):
        first, last, d = runs[k]
        before, after = runs[k - 1][2], runs[k + 1][2]
        if d is None or before is None or before != after:
            continue
        if d.weekday() < 5 or before.weekday() >= 5:
            continue
        on_that_day = {(r[0], r[2], r[3]) for r in rows if r[1] == before}
        if any((rows[i][0], rows[i][2], rows[i][3]) in on_that_day for i in range(first, last + 1)):
            continue
        for i in range(first, last + 1):
            repairs[i] = before
    return repairs
