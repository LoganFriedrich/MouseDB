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
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Dict, Iterable


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
