"""No value we export can break the program that reads the file.

WHY THIS TEST EXISTS
--------------------
A data standard publishes rules about variable names. The CSV format has rules of
its own that nobody publishes alongside them, and they are enforced at the far
end -- by an uploader, a spreadsheet, or a collaborator's script -- which means a
violation is discovered after the submission is assembled.

The tracking sheets contained the classic one: ODC_Injury_location holds
"Pyramid, left", a comma inside a data cell, in 285 cells of one cohort. Our
writer quotes it correctly and every standards-respecting reader handles it. A
reader that splits a line on commas sees an extra column and shifts every column
after it -- and that reader is common enough that the comma is not worth keeping,
because a comma in a data cell is never load-bearing.

So data cells carry no commas, no double quotes, no line breaks, no tabs and
nothing outside plain ASCII, and no cell begins with a character that makes a
spreadsheet evaluate it as a formula. The data dictionary keeps its commas, since
its Description and Comments are English prose and ODC's own template is prose in
the same columns -- it loses the rest.

Nothing is changed quietly: a column whose values were altered says so in its own
dictionary entry.
"""
import codecs
import csv
import re
from pathlib import Path

import pandas as pd
import pytest

from mousedb.exporters import csv_safety as cs


# ------------------------------------------------------------------ the rule itself

def test_a_comma_in_a_data_cell_becomes_a_semicolon():
    """The real value from the sheets, and what it must become."""
    assert cs.safe_cell("Pyramid, left") == "Pyramid; left"


def test_prose_in_the_dictionary_keeps_its_commas():
    text = "Whether the animal came through all of its surgeries, from the sheet."
    assert cs.safe_cell(text, allow_comma=True) == text


def test_a_double_quote_becomes_a_single_quote_everywhere():
    assert cs.safe_cell('add "ODC::x" to the file', allow_comma=True) == \
        "add 'ODC::x' to the file"


def test_a_line_break_or_tab_inside_a_value_becomes_a_space():
    assert cs.safe_cell("two\nlines") == "two lines"
    assert cs.safe_cell("a\tb") == "a b"
    assert cs.safe_cell("carriage\r\nreturn") == "carriage return"


def test_a_unit_symbol_keeps_its_meaning_instead_of_being_dropped():
    """Dropping the micro sign would turn microns into metres."""
    assert cs.safe_cell("displacement (µm)") == "displacement (um)"
    assert cs.safe_cell("area mm²") == "area mm2"
    assert cs.safe_cell("12 ± 3") == "12 +/- 3"
    assert cs.safe_cell("37 °C") == "37 degC"


def test_anything_else_outside_ascii_is_removed_rather_than_guessed_at():
    assert cs.unsafe(cs.safe_cell("café 中")) == ()


def test_a_value_a_spreadsheet_would_treat_as_a_formula_is_defused():
    for raw in ("=SUM(A1:A2)", "+1234", "@name", "-Not a number"):
        assert cs.safe_cell(raw).startswith("'"), raw


def test_a_negative_number_is_left_alone():
    """Defusing formulas must not corrupt the data it is protecting."""
    for raw in ("-1", "-0.5", "-1.2e-3"):
        assert cs.safe_cell(raw) == raw


def test_every_value_the_rule_produces_is_safe():
    awkward = ["Pyramid, left", 'a "quoted" word', "line\nbreak", "=cmd",
               "tab\there", "café", "µm", None, "", 3.5, 7]
    for raw in awkward:
        assert cs.unsafe(cs.safe_cell(raw)) == (), raw


def test_a_changed_column_says_so_in_its_own_dictionary_entry():
    frame = pd.DataFrame({"injury_location": ["Pyramid, left"], "subject": ["CNT_01_01"]})
    safe, changes = cs.make_frame_safe(frame)
    assert list(safe["injury_location"]) == ["Pyramid; left"]
    assert "injury_location" in changes and "subject" not in changes

    rows = [{"VariableName": "injury_location", "Comments": "From the sheet."},
            {"VariableName": "subject", "Comments": ""}]
    noted = cs.note_substitutions(rows, changes)
    assert "commas" in noted[0]["Comments"] and "From the sheet." in noted[0]["Comments"]
    assert noted[1]["Comments"] == ""


# -------------------------------------------------------- the files we actually ship

def _exports():
    from mousedb.config import export_path
    out = export_path()
    if out is None or not (Path(out) / "current").exists():
        return None
    return Path(out) / "current"


def _datasets():
    current = _exports()
    if current is None:
        return []
    files = sorted(current.glob("*.csv")) + sorted((current / "Example").glob("*.csv"))
    return [f for f in files if not f.name.endswith("DATA_DICTIONARY.csv")]


def _dictionaries():
    current = _exports()
    if current is None:
        return []
    return (sorted(current.glob("*DATA_DICTIONARY.csv"))
            + sorted((current / "Example").glob("*DATA_DICTIONARY.csv")))


# The hazards a dataset must not contain, as raw bytes.
#
# WHY BYTES AND NOT CELLS. The first version of this test read every cell of
# every file with a CSV reader: about 1.5 GB over a network share, cell by cell
# in Python, and it ran for more than twenty minutes. A test nobody will wait
# for is a test nobody runs.
#
# The byte scan is not a weaker check -- it is a stronger one, and it is exact.
# A writer only quotes a field that needs quoting, so a dataset containing no
# double-quote byte AT ALL cannot contain a comma inside a value, a quote inside
# a value, or a newline inside a value: any of the three would have forced a
# quote character into the file. One sequential pass over the bytes therefore
# settles all three at once, plus tabs and anything outside ASCII, for every row
# rather than a sample of them.
# What each forbidden byte means, for the failure message.
WHY = {
    0x22: ("a double-quote byte, which means some value had to be quoted -- so a "
           "value contains a comma, a quote or a line break"),
    0x09: "a tab inside the file",
}

# One compiled scan for every hazard at once: a quote, a tab, or any byte above
# plain ASCII. Scanning byte by byte in Python was the first attempt and it was
# unusable -- 1.5 GB is 1.5 billion loop iterations. This hands the same work to
# the regex engine, which does it in C at disk speed.
# The class is built from byte values rather than written as a literal so
# this source file stays pure ASCII, which the project requires of anything
# that can reach a Windows console.
# Assembled from byte values, not written as a literal: 0x5B is '[', 0x22
# the double quote, 0x09 tab, 0x2D the '-' of the range, 0x5D ']'. Written
# this way the source file stays pure ASCII, which this project requires of
# anything that can reach a Windows console.
HAZARD = re.compile(bytes([0x5B, 0x22, 0x09, 0x7F, 0x2D, 0xFF, 0x5D]))
BLOCK = 8 << 20


def _hazardous_bytes(path):
    """(byte, why, offset) for the first hazard in the file, or None.

    The pattern is one byte wide, so reading in blocks cannot split a match and
    no overlap between blocks is needed.
    """
    offset = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(BLOCK)
            if not block:
                return None
            found = HAZARD.search(block)
            if found:
                code = block[found.start()]
                return (code, WHY.get(code, "a byte outside plain ASCII"),
                        offset + found.start())
            offset += len(block)


@pytest.mark.parametrize("path", _datasets(), ids=lambda p: p.name)
def test_no_dataset_contains_a_byte_that_can_break_a_reader(path):
    hazard = _hazardous_bytes(path)
    assert hazard is None, "%s: byte 0x%02X at offset %d -- %s" % (
        path.name, hazard[0], hazard[2], hazard[1])


@pytest.mark.parametrize("path", _dictionaries(), ids=lambda p: p.name)
def test_no_dictionary_cell_can_break_a_reader(path):
    """The dictionaries are small and they are allowed their prose commas, so
    these are checked value by value with a strict CSV reader."""
    bad = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, strict=True):
            for column, value in row.items():
                problems = cs.unsafe(value, allow_comma=True)
                if problems:
                    bad.setdefault(column, (str(value)[:60], problems))
    assert not bad, "%s: %s" % (path.name, "; ".join(
        "%s (%s) e.g. %r" % (c, "/".join(w), eg) for c, (eg, w) in bad.items()))


@pytest.mark.parametrize("path", _datasets() + _dictionaries(), ids=lambda p: p.name)
def test_no_file_starts_with_a_byte_order_mark(path):
    """A BOM attaches itself to the first column's name and hides there."""
    with path.open("rb") as handle:
        assert handle.read(3) != codecs.BOM_UTF8, path.name


def test_the_write_path_applies_the_rule():
    """A claim about the code, so it holds for files not written yet."""
    import inspect

    from mousedb.exporters import odc_reaches
    assert "make_frame_safe" in inspect.getsource(odc_reaches._write)
    assert inspect.getsource(odc_reaches.write_cohort).count("make_frame_safe") >= 2
