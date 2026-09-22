"""What to write where there is no value, and why the four words are not one word.

WHY THIS EXISTS
---------------
An ODC-SCI dataset cannot carry empty cells, and the obvious fix -- fill every blank
with the same marker -- destroys real information. In a per-reach export a blank means
at least four different things, and in a real dataset each of them is common:

  * Days_Post_Injury is blank for every session that happened BEFORE the injury. There
    is no number to know. The question does not apply.
  * The per-reach outcome is blank on most rows because only the reach that decided the
    pellet's fate carries one. Every other reach has a perfectly good answer -- it did
    not move the pellet -- that a blank hides.
  * Some columns are blank on every row because no code ever computes them. The
    measurement does not exist, and for several of them a working replacement sits in
    the extended features under another name.
  * Some are blank because the source we would read them from does not record them.

Collapsing those into one marker would tell a reader that a pre-injury session and an
uncomputed column are the same kind of nothing. They are not, and the difference
changes what you may conclude. So each gets its own word, each word is written into the
data dictionary, and code says which one it means at the point it knows.
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd

# The question cannot have an answer for this row.
NOT_APPLICABLE = "Not applicable"
# No code computes this value; the column exists to keep the column set stable.
NOT_MEASURED = "Not measured"
# A source should carry it and does not.
NOT_RECORDED = "Not recorded"
# We looked and could not commit to an answer (an unresolved review, for instance).
UNDETERMINED = "Undetermined"

ALL = (NOT_APPLICABLE, NOT_MEASURED, NOT_RECORDED, UNDETERMINED)

PERMITTED_VALUES = "; ".join(ALL)
PV_DESCRIBED = ("Not applicable=the question cannot apply to this row; "
                "Not measured=no code computes this value; "
                "Not recorded=a source should carry it and does not; "
                "Undetermined=looked at and could not be decided")


def is_blank(value) -> bool:
    """True for the several shapes 'no value' arrives in (None, NaN, NA, empty text)."""
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip() == ""


def fill(series: pd.Series, reason: str) -> pd.Series:
    """Every empty cell of ``series`` replaced by ``reason``.

    Returns text, because a column that mixes numbers with a reason is text whether or
    not we admit it, and admitting it keeps the written file honest about its own type.
    """
    out = series.astype("object").where(~series.map(is_blank), reason)
    return out.astype("object")


def fill_frame(frame: pd.DataFrame, reasons: dict, default: str = NOT_RECORDED) -> pd.DataFrame:
    """Fill every column, using ``reasons[column]`` where given and ``default`` elsewhere.

    The default is deliberately NOT_RECORDED rather than something softer: a column
    nobody classified is one nobody has thought about, and it should read as a gap in
    the record rather than quietly claim the question did not apply.
    """
    out = frame.copy()
    for column in out.columns:
        out[column] = fill(out[column], reasons.get(column, default))
    return out


def all_blank_columns(frame: pd.DataFrame) -> list:
    """Columns with no value on any row -- the ones to classify as never measured."""
    return [c for c in frame.columns if frame[c].map(is_blank).all()]


def dictionary_comment(reason_by_column: dict, column: str) -> str:
    """The sentence to append to a column's dictionary entry about its empty cells."""
    reason = reason_by_column.get(column)
    if not reason:
        return ""
    return "Cells with no value read '%s'." % reason
