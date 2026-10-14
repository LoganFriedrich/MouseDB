"""What each tracking-sheet column MEANS, supplied by the laboratory.

WHY THIS EXISTS
---------------
A data dictionary's ``Description`` is supposed to tell a reader what the field
is. For every column copied out of a tracking sheet it said something else
entirely -- where the value came from:

    "Copied as written from the 'Injury_device' column of the 'ODC' tab of this
     cohort's tracking sheet. Text; dates as YYYY-MM-DD."

That is provenance. It is worth recording, and it now goes in ``Comments``,
where provenance belongs. It tells a reader nothing about what the field means,
and a dictionary full of it satisfies an upload while leaving every column
undefined.

The definitions cannot be shipped in this package. They describe one
laboratory's spreadsheet -- its tabs, its column names, the meaning its own
practice gives them -- and another lab's sheet has different columns meaning
different things. So this module is the MECHANISM; the definitions live in the
lab's own file, next to its study facts.

WHEN THERE IS NO DEFINITION
---------------------------
The row says so, in the Description, naming the file to edit. That is
deliberate: the alternative is text that reads like a definition and is not,
which is how the dictionary got into this state. A gap that announces itself is
fixable; one that looks finished is not.

THE FILE
--------
``~/.mousedb/sheet_field_definitions.json``, or ``MOUSEDB_FIELD_DEFINITIONS``::

    {
      "ODC::Injury_device": "The device used to produce the injury ...",
      "*::Survived":        "Whether the animal survived this surgery ...",
      "_units": {"ODC::Surgery_weight": "g"}
    }

A key is ``<tab>::<field>``. ``*::<field>`` applies to a field of that name on
ANY tab, so a column the lab repeats across tabs -- Analgesic, Survived,
Surgery_Date -- is defined once. An exact tab match always wins over ``*``.

``_units`` is optional and fills the dictionary's Unit_of_Measure for columns
that have one, since a number without its unit is not usable by anyone.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

ENV_VAR = "MOUSEDB_FIELD_DEFINITIONS"
FILENAME = "sheet_field_definitions.json"
UNITS_KEY = "_units"
ANY_TAB = "*"

# Said in the Description itself, not hidden in a comment, and naming the file to
# edit. A dictionary row that admits it is undefined is useful; one that reads
# like a definition and is not, is worse than an empty cell.
UNDEFINED = ("NO DEFINITION YET for this column. Its meaning is a property of "
             "this laboratory's own tracking sheet, so it has to be written down "
             # SINGLE quotes, not double: this text lands in a CSV cell, and a
             # double quote inside a quoted CSV field is the construct
             # hand-written parsers get wrong most often.
             "by the laboratory: add '%s' to %s. Until then, only the "
             "column's provenance is known -- see Comments.")


def definitions_path() -> Path:
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override)
    return Path.home() / ".mousedb" / FILENAME


def read_all() -> Dict[str, str]:
    """The lab's definitions, or {} when none have been written.

    Never raises: a dictionary must still be produced when this file is missing
    or malformed, with every row honestly marked undefined.
    """
    path = definitions_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def key_for(tab: str, field: str) -> str:
    return "%s::%s" % (tab, field)


def lookup(tab: str, field: str, table: Optional[Dict[str, str]] = None) -> str:
    """The definition for one column: exact tab first, then any-tab, else ''."""
    table = read_all() if table is None else table
    for candidate in (key_for(tab, field), key_for(ANY_TAB, field)):
        value = table.get(candidate)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def unit_for(tab: str, field: str, table: Optional[Dict[str, str]] = None) -> str:
    """The unit for one column, same lookup order. '' when it has none."""
    table = read_all() if table is None else table
    units = table.get(UNITS_KEY) or {}
    if not isinstance(units, dict):
        return ""
    for candidate in (key_for(tab, field), key_for(ANY_TAB, field)):
        value = units.get(candidate)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def undefined_text(tab: str, field: str) -> str:
    return UNDEFINED % (key_for(tab, field), definitions_path())


def missing_definitions(columns) -> list:
    """Every (tab, field) in use that the lab has not defined, sorted.

    So a person can be told what is left, rather than finding out one dictionary
    row at a time.
    """
    table = read_all()
    gaps = {(tab, field) for tab, field in columns if not lookup(tab, field, table)}
    return sorted(gaps)


def set_definition(tab: str, field: str, text: Optional[str]) -> Path:
    """Write one definition. Passing None removes it."""
    path = definitions_path()
    table = read_all()
    key = key_for(tab, field)
    if text is None or not str(text).strip():
        table.pop(key, None)
    else:
        table[key] = str(text).strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(table, indent=1, sort_keys=True), encoding="utf-8")
    return path
