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
import csv
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

def _datasets():
    from mousedb.config import export_path
    out = export_path()
    if out is None or not (Path(out) / "current").exists():
        return []
    current = Path(out) / "current"
    files = sorted(current.glob("*.csv")) + sorted((current / "Example").glob("*.csv"))
    return [f for f in files if not f.name.endswith("DATA_DICTIONARY.csv")]


def _dictionaries():
    from mousedb.config import export_path
    out = export_path()
    if out is None or not (Path(out) / "current").exists():
        return []
    current = Path(out) / "current"
    return (sorted(current.glob("*DATA_DICTIONARY.csv"))
            + sorted((current / "Example").glob("*DATA_DICTIONARY.csv")))


def _offenders(path, allow_comma):
    """{column: (count, example, hazards)} -- read with a strict CSV reader."""
    found = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        for row in reader:
            for column, value in row.items():
                problems = cs.unsafe(value, allow_comma)
                if problems:
                    count, example, _ = found.get(column, (0, str(value)[:60], ()))
                    found[column] = (count + 1, example, problems)
    return found


@pytest.mark.parametrize("path", _datasets(), ids=lambda p: p.name)
def test_no_data_cell_can_break_a_reader(path):
    bad = _offenders(path, allow_comma=False)
    assert not bad, "%s: %s" % (path.name, "; ".join(
        "%s (%d cells, %s) e.g. %r" % (c, n, "/".join(w), eg)
        for c, (n, eg, w) in bad.items()))


@pytest.mark.parametrize("path", _dictionaries(), ids=lambda p: p.name)
def test_no_dictionary_cell_can_break_a_reader(path):
    bad = _offenders(path, allow_comma=True)
    assert not bad, "%s: %s" % (path.name, "; ".join(
        "%s (%d cells, %s) e.g. %r" % (c, n, "/".join(w), eg)
        for c, (n, eg, w) in bad.items()))


@pytest.mark.parametrize("path", _datasets() + _dictionaries(), ids=lambda p: p.name)
def test_no_file_starts_with_a_byte_order_mark(path):
    """A BOM attaches itself to the first column's name and hides there."""
    with path.open("rb") as handle:
        assert handle.read(3) != b"\xef\xbb\xbf", path.name


def test_the_write_path_applies_the_rule():
    """A claim about the code, so it holds for files not written yet."""
    import inspect

    from mousedb.exporters import odc_reaches
    assert "make_frame_safe" in inspect.getsource(odc_reaches._write)
    assert inspect.getsource(odc_reaches.write_cohort).count("make_frame_safe") >= 2
