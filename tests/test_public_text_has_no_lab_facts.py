"""mousedb is a public repository: update.py and cli.py must not carry one lab's facts.

WHY THIS EXISTS
---------------
Two sentences in these files described the lab that wrote them rather than the
tool: update.py's docstring credited a design decision to a named person, and
the text ``mousedb import --all`` prints when it refuses a snapshot folder
described one lab's archive (which cohort's copy there was a stub, and what the
live sheet held). Both were rewritten as the general reason. A routine edit can
put that kind of sentence back without anyone noticing, so this test fails if:

  * a phrase that was removed comes back (REMOVED_PHRASES below, matched
    exactly after spaces and line breaks are evened out);
  * the person's name that was removed comes back anywhere in either file;
  * a decision is credited to someone by name ("<Name>'s correction", etc.);
  * any line holds a drive-letter path (``Q:\\...`` or ``Q:/...``) or an
    e-mail address;
  * a string the program can print (any string literal: messages, --help
    text, docstrings) names a tracking workbook with a real cohort number,
    ``Connectome_`` followed by two digits. ``Connectome_NN`` -- the name
    pattern itself -- is fine and is what the files should use.

Why the name is stored as a digest: writing it here, in another public file,
would publish it again. The test hashes every word in the two files and looks
for that one digest. It is not secret -- a short first name can be guessed from
its digest -- it just keeps the name out of plain text.

LIMITS -- what passing does NOT mean
------------------------------------
  * Only mousedb/update.py and mousedb/cli.py are checked. Other modules are
    not; they need their own review (see test_cohort_sheets.py for the
    corpus-number guard on cohort_sheets.py).
  * Removed phrases are matched word for word. The same fact written in new
    words passes.
  * Only the one removed name is detected. There is no reliable general test
    for people's names: a rule like "two capitalised words" flags every class,
    product and tool name in the code. The "<Name>'s correction" rule catches
    that one sentence shape, for any name, and nothing else.
  * The path check sees drive letters only. A UNC share (``\\\\server\\share``),
    a POSIX home folder, or a path built at run time from configuration is
    not caught.
  * The ``Connectome_NN`` check reads string literals only, not comments, and
    only that one file-name shape. Counts, file sizes, dates and cadences that
    describe one lab's data are not detected at all -- those need a person.
  * Passing does not make the files clean. The lab-private pre-commit guard
    and a person reading the diff are still the real check.
"""
import ast
import hashlib
import re
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "mousedb"
FILES = [PACKAGE / "update.py", PACKAGE / "cli.py"]

# Each phrase was removed because it described this lab rather than the tool.
# None of them names a person, cohort or path on its own, so they can be
# written here; the general reason each one gave is still in the files.
REMOVED_PHRASES = [
    # the snapshot-folder refusal: one cohort's archived copy described
    "is a stub with an empty subject table",
    "the full scoring record",
    # one lab's own schedule given as if it were the tool's design
    "the scheduled hourly run can call this same command",
    "three hourly runs in a row",
    "failing costs an hour",
    "it then skipped a whole day",
    "task scheduler stayed green",
]

# SHA-256 of the removed person's name, lower case (see the module docstring).
REMOVED_NAME_DIGESTS = {
    "5ba4c5ad7f67ea1a40f3108d9af56d063ec36d3400d810573769eb5c07809af8",
}

# A decision credited to someone by name: "<Name>'s correction". Lower-case
# words and names with an inner capital (MouseBrain's, MouseReach's) do not match.
ATTRIBUTION = re.compile(
    r"\b[A-Z][a-z]+'s (?:correction|decision|call|request|rule|idea|choice|preference)\b")
# A drive letter, not part of a longer word ("https://" does not match).
DRIVE_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
# A real cohort number in a workbook name; the pattern Connectome_NN is fine.
WORKBOOK_WITH_NUMBER = re.compile(r"Connectome_\d{2}")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _even(text: str) -> str:
    """Lower case, every run of spaces and line breaks one space: a phrase
    split across lines, or re-indented, is still found."""
    return re.sub(r"\s+", " ", text).lower()


def _name_digests_found(text: str, digests=REMOVED_NAME_DIGESTS) -> set:
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", text)}
    return {w for w in words
            if hashlib.sha256(w.encode("utf-8")).hexdigest() in digests}


def _string_literals(source: str):
    """Every string the module could print: plain strings, the literal parts
    of f-strings (ast keeps them as Constant nodes), --help text, docstrings."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_removed_phrase_has_come_back(path):
    text = _even(_text(path))
    back = [p for p in REMOVED_PHRASES if _even(p) in text]
    assert not back, "%s again holds removed lab-specific text: %r" % (path.name, back)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_the_removed_name_is_not_back(path):
    # Asserted as a COUNT held in its own variable. WHY: pytest prints the
    # values in a failed assert, so asserting on the found words themselves
    # printed the name this test exists to keep out of view; the message does
    # not repeat it either, for the same reason it is stored as a digest.
    count = len(_name_digests_found(_text(path)))
    assert count == 0, ("%s again names the person removed from its docstring; "
                        "say what was decided and why, not who decided it" % path.name)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_decision_is_credited_to_a_named_person(path):
    hits = [(i, m.group(0)) for i, line in enumerate(_text(path).splitlines(), 1)
            for m in ATTRIBUTION.finditer(line)]
    assert not hits, "%s credits a decision to a person: %r" % (path.name, hits)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_line_holds_a_drive_path_or_an_email_address(path):
    hits = [(i, line.strip()) for i, line in enumerate(_text(path).splitlines(), 1)
            if DRIVE_PATH.search(line) or EMAIL.search(line)]
    assert not hits, ("%s holds a machine path or an address; paths belong in "
                      "~/.mousedb/config.json: %r" % (path.name, hits))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_printed_text_names_no_real_cohort_workbook(path):
    hits = [(line, s) for line, s in _string_literals(_text(path))
            if WORKBOOK_WITH_NUMBER.search(s)]
    assert not hits, ("%s prints a workbook name with a real cohort number; "
                      "write Connectome_NN: %r" % (path.name, hits))


def test_the_screens_catch_what_they_are_meant_to():
    """Each check is first shown to catch a made-up dirty line. WHY: a screen
    that silently matches nothing passes forever, and looks like a clean file."""
    assert _even("IS A STUB with an\n    empty subject table") in _even(
        "x is a stub with an empty subject table y")
    made_up = "placeholdername"
    digest = hashlib.sha256(made_up.encode("utf-8")).hexdigest()
    assert _name_digests_found("Placeholdername's note", {digest}) == {made_up}
    assert ATTRIBUTION.search("as per Somebody's correction, a person")
    assert not ATTRIBUTION.search("MouseBrain's registry and the importer's rule")
    assert DRIVE_PATH.search(r'root = "Q:\data"') and DRIVE_PATH.search("Q:/data")
    assert not DRIVE_PATH.search("see https://example.org and '%s: %s'")
    assert EMAIL.search("someone@example.org") and not EMAIL.search("@dataclass")
    lits = [s for _, s in _string_literals('print(f"read Connectome_07_x.xlsx {n}")')]
    assert any(WORKBOOK_WITH_NUMBER.search(s) for s in lits)
    assert not WORKBOOK_WITH_NUMBER.search("Connectome_NN_Animal_Tracking.xlsx")
