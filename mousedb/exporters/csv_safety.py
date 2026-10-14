"""Make a cell safe to put in a CSV that someone else's program will read.

WHY THIS EXISTS
---------------
A CSV has rules of its own that nobody publishes alongside a data standard, and
they are enforced at the far end, by whatever reads the file. The breakages are
always the same handful:

* **A comma inside a value.** Legal CSV -- the value is quoted, and every
  standards-respecting reader handles it. But a reader that splits a line on
  commas sees one extra column, and every column after it is shifted by one. The
  file looks fine until something tries to use it.
* **A double quote inside a quoted value.** Correct CSV doubles it (`""`). This
  is the most commonly mishandled construct in the format, because a hand-written
  parser almost never implements it.
* **A newline inside a value.** Correct CSV quotes it, so one record spans two
  lines. A reader that counts lines then reports the wrong number of rows, and a
  reader that splits on newlines corrupts both halves.
* **A character outside ASCII.** It depends on the encoding being read back the
  way it was written, and on this project's own terminals it is not (a single
  non-ASCII character in a print statement crashes a Windows console).
* **A value starting with `=`, `+`, `-` or `@`.** A spreadsheet treats it as a
  formula. Nobody uploads a CSV without first opening it in a spreadsheet.

None of this is ODC's doing, and none of it is written down where it can be
looked up before an upload fails. So the values are made safe on the way out.

WHAT IS AND IS NOT ALLOWED TO CHANGE
------------------------------------
Data cells get the full treatment, because a comma is never load-bearing in one:
the values are identifiers, categories, numbers and short phrases. A comma
becomes a semicolon, which reads the same way in "Pyramid; left" and cannot break
a reader.

The data dictionary keeps its commas. Its Description and Comments are English
prose and removing commas from prose mangles it -- and the commas there are
necessarily safe, since ODC's own dictionary template is prose in the same
column. The dictionary still loses quotes, newlines and non-ASCII.

NOTHING IS CHANGED SILENTLY. substitutions() reports every column it altered so
the writer can say so in that column's dictionary entry, where a reader of the
column will see it.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# Characters with a plain-ASCII meaning. Replaced by that meaning rather than
# dropped: a measurement in "um" still says microns, where a dropped character
# would leave "m" and quietly change the unit by a factor of a thousand.
ASCII_FOR = {
    "µ": "u", "μ": "u",        # micro sign, Greek mu
    "°": " deg",                     # degree
    "±": "+/-",                      # plus-minus
    "×": "x",                        # multiplication
    "–": "-", "—": "-",         # en dash, em dash
    "‘": "'", "’": "'",         # curly single quotes
    "“": "'", "”": "'",         # curly double quotes
    "…": "...",                      # ellipsis
    " ": " ",                        # non-breaking space
    "→": "->", "←": "<-",       # arrows
    "≤": "<=", "≥": ">=",
    "²": "2", "³": "3",         # superscript 2 and 3 (mm2, mm3)
}

# A comma in a data cell becomes this. A semicolon, because the values that
# contain one are lists or qualified phrases where a semicolon reads the same.
COMMA_REPLACEMENT = ";"

FORMULA_STARTS = ("=", "+", "@")


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except (TypeError, ValueError):
        return False


def safe_cell(value, allow_comma: bool = False) -> str:
    """One value, safe to write into a CSV. Returns a string.

    allow_comma is for prose columns of the data dictionary (see module docstring).
    """
    if value is None:
        return ""
    text = str(value)
    for char, plain in ASCII_FOR.items():
        if char in text:
            text = text.replace(char, plain)
    # Anything still outside printable ASCII is dropped: it has no ASCII meaning
    # to preserve, and keeping it means depending on an encoding round-trip.
    if any(ord(c) > 126 or ord(c) < 32 for c in text):
        text = "".join(c if 32 <= ord(c) <= 126 else " " for c in text)
    text = text.replace('"', "'")
    if not allow_comma:
        text = text.replace(",", COMMA_REPLACEMENT)
    # Newlines and tabs became spaces above; collapse what that leaves behind.
    while "  " in text:
        text = text.replace("  ", " ")
    text = text.strip()
    # A leading = + @ makes a spreadsheet evaluate the cell as a formula. A
    # leading "-" does too, but only when what follows is not a number, and
    # negative numbers must obviously survive.
    if text[:1] in FORMULA_STARTS or (text[:1] == "-" and not _is_number(text)):
        text = "'" + text
    return text


def substitutions(frame, allow_comma: bool = False) -> Dict[str, List[str]]:
    """{column: [what changed]} for a frame, without changing it.

    Used to tell a reader what was done to a column, in that column's own
    dictionary entry. Only text columns can be affected.
    """
    changed: Dict[str, List[str]] = {}
    for column in frame.columns:
        series = frame[column]
        if series.dtype != object:
            continue
        text = series.dropna().astype(str)
        if text.empty:
            continue
        reasons = []
        if not allow_comma and text.str.contains(",", regex=False).any():
            reasons.append("commas in this column's values were replaced with "
                           "semicolons so that the file cannot be split wrongly "
                           "by a reader that splits on commas")
        if text.str.contains('"', regex=False).any():
            reasons.append("double quotes were replaced with single quotes")
        if text.str.contains(r"[\n\r\t]", regex=True).any():
            reasons.append("line breaks and tabs within values were replaced "
                           "with single spaces")
        if text.map(lambda v: any(ord(c) > 126 for c in v)).any():
            reasons.append("characters outside plain ASCII were replaced with "
                           "their ASCII equivalent")
        if reasons:
            changed[column] = reasons
    return changed


def make_frame_safe(frame, allow_comma: bool = False):
    """A copy of the frame with every text value made safe. (frame, changes)"""
    changes = substitutions(frame, allow_comma)
    out = frame.copy()
    for column in out.columns:
        if out[column].dtype == object:
            out[column] = out[column].map(lambda v: safe_cell(v, allow_comma))
    return out, changes


def make_rows_safe(rows: List[dict]) -> List[dict]:
    """Dictionary rows made safe, keeping the commas in their prose."""
    out = []
    for row in rows:
        out.append({k: safe_cell(v, allow_comma=True) for k, v in row.items()})
    return out


def note_substitutions(rows: List[dict], changes: Dict[str, List[str]]) -> List[dict]:
    """Record in each affected column's dictionary entry what was done to it."""
    if not changes:
        return rows
    out = []
    for row in rows:
        name = str(row.get("VariableName", ""))
        if name in changes:
            row = dict(row)
            note = "For CSV safety, " + "; ".join(changes[name]) + "."
            row["Comments"] = (str(row.get("Comments", "")) + " " + note).strip()
        out.append(row)
    return out


def unsafe(value, allow_comma: bool = False) -> Tuple[str, ...]:
    """Which hazards a value still carries. Empty when it is safe."""
    text = "" if value is None else str(value)
    problems = []
    if not allow_comma and "," in text:
        problems.append("comma")
    if '"' in text:
        problems.append("double quote")
    if any(c in text for c in "\n\r\t"):
        problems.append("line break or tab")
    if any(ord(c) > 126 for c in text):
        problems.append("non-ASCII")
    if text[:1] in FORMULA_STARTS or (text[:1] == "-" and not _is_number(text)):
        problems.append("reads as a spreadsheet formula")
    return tuple(problems)
