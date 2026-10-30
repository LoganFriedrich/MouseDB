"""Only a file NAMED as a cohort's tracking sheet may be read as one.

THE DEFECT. The tracking-sheet name pattern was matched with an unanchored
search, so any file whose name merely CONTAINED "Connectome_NN_Animal_Tracking"
was a candidate for that cohort: "Copy of Connectome_05_Animal_Tracking.xlsx",
an Excel lock file "~$Connectome_05_...", the "._Connectome_05_..." shadow file a
Mac leaves on a network share, a note another tool writes beside the sheets.
Files this package's own tools write next to a sheet ("<sheet>_ODC.xlsx",
"<sheet>_summary_matrix.xlsx", ...) start with the sheet's name and were
candidates too.

THE CONSEQUENCE. With no pin, the NEWEST candidate is the one imported -- by the
scheduled import and by the GUI alike. A stray file is almost always newer than the
sheet it sits beside, so it silently became the cohort's record: the import read
a copy, a derived export or a lock file instead of the workbook people edit.

WHAT IS PINNED HERE.
  - every legitimate sheet name still counts (suffixes after the name are real:
    "...Tracking1.xlsx", "...Tracking(<note>).xlsx", a "(2)" copy);
  - a stray file, however new, never displaces the real sheet -- including a
    tool's export of a sheet whose name already has a suffix
    ("...Tracking1_ODC.xlsx");
  - a refused file is not hidden: it is listed, with a reason a person can act
    on, printed in ASCII whatever characters its name holds;
  - a refused name cannot be pinned, because a pin on it would be silently
    ignored; nor can a name that is not in the folder;
  - a BROKEN pin (the pinned file renamed, moved or deleted) is announced,
    naming the file being read instead, and is never reported as "pinned".
"""
import json
import os
import time
from pathlib import Path

import pytest

import mousedb.cohort_sheets as cs
import mousedb.sheet_sync as ss

REAL = "Connectome_05_Animal_Tracking.xlsx"

# Names that are NOT cohort 05's sheet, each for a reason the module must know.
REFUSED = [
    "Copy of Connectome_05_Animal_Tracking.xlsx",          # a person's copy
    "~$Connectome_05_Animal_Tracking.xlsx",                # Excel lock file
    "._Connectome_05_Animal_Tracking.xlsx",                # Mac shadow file on a share
    "!! Connectome_05_Animal_Tracking - FIX THESE.xlsx",   # a note named after the sheet
    "Connectome_05_Animal_Tracking_ODC.xlsx",              # odc_builder default output
    "Connectome_05_Animal_Tracking_summary_matrix.xlsx",   # summary_matrix default output
    "Connectome_05_Animal_Tracking_updated.xlsx",          # update_sheets output
    "Connectome_05_Animal_Tracking_fixed(1).xlsx",         # older make_sheets --fix output
]


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """A configured sheets folder holding cohort 05's real sheet, and a private
    config file so no test can read or write this machine's real pins."""
    d = tmp_path / "sheets"
    d.mkdir()
    (d / REAL).write_bytes(b"real")
    monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv(cs.ENV_VAR, str(d))
    monkeypatch.setattr(ss, "LEDGER", tmp_path / "ledger.jsonl")
    return d


def _newer(path: Path, by: float = 3600) -> Path:
    """Make ``path`` look edited after everything else -- the case that matters,
    because with no pin the newest candidate is the one imported."""
    t = time.time() + by
    os.utime(path, (t, t))
    return path


class TestLegitimateSheetsStillCount:
    """Anchoring must not lose a real sheet. Each of these shapes is in use."""

    @pytest.mark.parametrize("name,cohort", [
        ("Connectome_05_Animal_Tracking.xlsx", "CNT_05"),
        ("Connectome_04_Animal_Tracking1.xlsx", "CNT_04"),               # trailing digit
        ("Connectome_00_Animal_Tracking(room20260101).xlsx", "CNT_00"),   # a note in brackets
        ("Connectome_05_Animal_Tracking (2).xlsx", "CNT_05"),             # a "(2)" copy
        ("Connectome_5_Animal_Tracking.xlsx", "CNT_05"),                  # one-digit cohort
        ("connectome_05_animal_tracking.XLSX", "CNT_05"),                 # case differs
        ("Connectome_05_Animal_Tracking-OTHERPC.xlsx", "CNT_05"),         # sync conflict copy
    ])
    def test_it_is_a_sheet(self, name, cohort):
        assert cs._sheet_cohort(name) == cohort

    def test_every_variant_is_a_candidate_for_a_person_to_choose(self, folder):
        """Variants stay VISIBLE: when more than one file claims a cohort the
        status says "N files match -- choose" and a person pins the real one.
        A sync conflict copy is deliberately left in this group -- it may hold
        the newer edits, so only a person can say which file is the record."""
        for name in ("Connectome_05_Animal_Tracking1.xlsx",
                     "Connectome_05_Animal_Tracking(room20260101).xlsx",
                     "Connectome_05_Animal_Tracking-OTHERPC.xlsx"):
            (folder / name).write_bytes(b"v")
        names = {p.name for p in cs.cohort_sheet_candidates("CNT_05")}
        assert names == {REAL, "Connectome_05_Animal_Tracking1.xlsx",
                         "Connectome_05_Animal_Tracking(room20260101).xlsx",
                         "Connectome_05_Animal_Tracking-OTHERPC.xlsx"}


class TestStrayFilesAreNotSheets:

    @pytest.mark.parametrize("name", REFUSED)
    def test_the_name_is_refused(self, name):
        assert cs._sheet_cohort(name) is None

    @pytest.mark.parametrize("name", [
        "!! CNT_05 - FIX THESE.xlsx",            # a per-cohort note a tool may write beside the sheets
        "Summaries.xlsx",
        "OtherProject_01_Animal_Tracking.xlsx",   # another project's tracking sheet
    ])
    def test_other_workbooks_in_the_folder_are_not_sheets(self, name):
        assert cs._sheet_cohort(name) is None

    @pytest.mark.parametrize("name", REFUSED)
    def test_a_newer_stray_file_never_displaces_the_real_sheet(self, folder, name):
        """The exact failure: the stray file is the newest, and the newest wins."""
        (folder / name).write_bytes(b"stray")
        _newer(folder / name)
        assert [p.name for p in cs.cohort_sheet_candidates("CNT_05")] == [REAL]
        assert cs.find_cohort_sheet("CNT_05").name == REAL

    @pytest.mark.parametrize("name", REFUSED)
    def test_a_fetched_working_copy_is_of_the_real_sheet(self, folder, tmp_path, name):
        (folder / name).write_bytes(b"stray")
        _newer(folder / name)
        got = cs.fetch_cohort_sheet("CNT_05", tmp_path / "work")
        assert got.read_bytes() == b"real"

    def test_a_cohort_known_only_from_a_stray_file_is_not_a_cohort(self, folder):
        (folder / "Copy of Connectome_09_Animal_Tracking.xlsx").write_bytes(b"x")
        (folder / "~$Connectome_08_Animal_Tracking.xlsx").write_bytes(b"x")
        assert cs.available_cohorts() == ["CNT_05"]
        assert cs.find_cohort_sheet("CNT_09") is None

    def test_a_folder_holding_only_stray_files_is_not_the_sheets_folder(
            self, tmp_path, monkeypatch):
        """cohort_sheets_dir accepts a folder only if it holds a sheet. A lock
        file left behind, or a copy, is not one -- otherwise a folder with no
        workbook in it would be configured as the source of record."""
        d = tmp_path / "strays"
        d.mkdir()
        (d / "~$Connectome_05_Animal_Tracking.xlsx").write_bytes(b"x")
        (d / "Copy of Connectome_05_Animal_Tracking.xlsx").write_bytes(b"x")
        monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setenv(cs.ENV_VAR, str(d))
        assert cs.cohort_sheets_dir() is None
        assert d not in cs.discover(extra_roots=[d])

    def test_every_lookup_goes_through_the_anchored_rule(self):
        """The defect was a ``.search`` on the sheet pattern. Every caller now
        asks _sheet_verdict, so one rule decides; a reintroduced bare search
        would quietly bring the stray files back."""
        import re
        text = Path(cs.__file__).read_text(encoding="utf-8")
        # The rule lives in sheet_projects now: one anchored pattern per
        # project, and only its display-only "resembles" search is unanchored.
        assert "_projects.match(name)" in text
        from mousedb import sheet_projects
        for p in sheet_projects.definitions().values():
            assert p.matcher().pattern.startswith("^")

    @pytest.mark.parametrize("sheet", [
        "Connectome_05_Animal_Tracking1.xlsx",
        "Connectome_05_Animal_Tracking(room20260101).xlsx",
        "Connectome_05_Animal_Tracking (2).xlsx",
    ])
    @pytest.mark.parametrize("marker", ["_ODC", "_summary_matrix", "_updated", "_fixed"])
    def test_a_tool_output_of_a_suffixed_sheet_never_displaces_it(self, folder, sheet, marker):
        """The tools APPEND their marker to the source's full stem
        (``src.stem + "_ODC.xlsx"``). For a sheet that already carries a suffix
        the marker therefore sits mid-tail ("...Tracking1_ODC.xlsx"), and a check
        on the START of the tail missed it: the export, written last, became the
        newest candidate and the unpinned import read it instead of the sheet."""
        (folder / REAL).unlink()
        (folder / sheet).write_bytes(b"real")
        derived = folder / (Path(sheet).stem + marker + ".xlsx")
        derived.write_bytes(b"derived")
        _newer(derived)
        assert cs._sheet_cohort(derived.name) is None
        assert [p.name for p in cs.cohort_sheet_candidates("CNT_05")] == [sheet]
        assert cs.find_cohort_sheet("CNT_05").name == sheet
        assert derived.name in dict(cs.refused_sheet_files())

    @pytest.mark.parametrize("module,suffix", [
        ("odc_builder", '"_ODC.xlsx"'),
        ("summary_matrix", '"_summary_matrix.xlsx"'),
        ("update_sheets", "_updated.xlsx"),
    ])
    def test_the_refused_derived_names_are_the_ones_the_tools_write(self, module, suffix):
        """The derived-output list is tied to the tools that produce it. If a
        tool's default output name changes, this fails so the list is updated
        rather than the new name slipping through as a candidate."""
        import importlib.util
        spec = importlib.util.find_spec("mousedb.cohort_tools.%s" % module)
        assert suffix in Path(spec.origin).read_text(encoding="utf-8")


class TestRefusalsAreShownNotHidden:
    """Refusing a file is a decision. If a real sheet were ever saved under a
    refused name its cohort would stop importing; the refusal must be visible
    in what a person reads, with what to do about it."""

    def test_each_refused_file_is_listed_with_a_reason(self, folder):
        for name in REFUSED:
            (folder / name).write_bytes(b"x")
        (folder / "Summaries.xlsx").write_bytes(b"x")   # unrelated: not listed
        listed = dict(cs.refused_sheet_files())
        assert set(listed) == set(REFUSED)
        assert all(reason for reason in listed.values())

    def test_the_reasons_tell_a_person_what_the_file_is(self, folder):
        for name in ("~$Connectome_05_Animal_Tracking.xlsx",
                     "Copy of Connectome_05_Animal_Tracking.xlsx",
                     "Connectome_05_Animal_Tracking_ODC.xlsx"):
            (folder / name).write_bytes(b"x")
        listed = dict(cs.refused_sheet_files())
        assert "lock file" in listed["~$Connectome_05_Animal_Tracking.xlsx"]
        assert "rename" in listed["Copy of Connectome_05_Animal_Tracking.xlsx"]
        assert "mousedb tool" in listed["Connectome_05_Animal_Tracking_ODC.xlsx"]

    def test_the_folder_summary_prints_them_in_plain_ascii(self, folder):
        """describe() is what `mousedb cohort-sheets` prints. ASCII only:
        a Windows console crashes on anything else."""
        (folder / "Copy of Connectome_05_Animal_Tracking.xlsx").write_bytes(b"x")
        text = cs.describe()
        assert "NOT read: Copy of Connectome_05_Animal_Tracking.xlsx" in text
        text.encode("ascii")

    def test_a_clean_folder_reports_no_refusals(self, folder):
        assert cs.refused_sheet_files() == []
        assert "NOT read" not in cs.describe()

    def test_a_refused_name_with_non_ascii_characters_is_printed_escaped(self, folder):
        """File names come from disk and can hold any character (a dash a word
        processor turned into an en dash, an accented name). describe() is
        printed to a console that crashes on non-ASCII, so the name is escaped
        -- still recognisable, never a crash."""
        name = "Copy of Connectome_05_Animal_Tracking \u2013 old.xlsx"
        (folder / name).write_bytes(b"x")
        assert name in dict(cs.refused_sheet_files())   # the raw name, for a GUI
        text = cs.describe()
        text.encode("ascii")
        assert "NOT read: Copy of Connectome_05_Animal_Tracking \\u2013 old.xlsx" in text


class TestAPinCannotNameARefusedFile:
    """find_cohort_sheet honours a pin only if it names one of the cohort's
    candidates. A pin on any other name used to be stored, shown as "pinned"
    in every status listing, and then silently ignored -- the newest file was
    still the one imported. Now that some names are refused as sheets, the
    names a person might type into ``mousedb-sheets pin`` include ones that
    can never be honoured, so the pin itself must say no."""

    @pytest.mark.parametrize("name", REFUSED + ["Connectome_06_Animal_Tracking.xlsx",
                                                "Summaries.xlsx"])
    def test_pinning_a_name_that_is_not_the_cohorts_sheet_is_refused(self, folder, name):
        with pytest.raises(ValueError) as e:
            cs.pin_cohort_sheet("CNT_05", name)
        assert "cannot pin" in str(e.value)
        assert cs.pinned_sheet("CNT_05") is None   # nothing was recorded
        str(e.value).encode("ascii")

    def test_a_real_variant_can_still_be_pinned_and_wins(self, folder):
        (folder / "Connectome_05_Animal_Tracking1.xlsx").write_bytes(b"v")
        _newer(folder / "Connectome_05_Animal_Tracking1.xlsx")
        cs.pin_cohort_sheet("CNT_05", REAL)
        assert cs.find_cohort_sheet("CNT_05").name == REAL
        cs.pin_cohort_sheet("CNT_05", None)
        assert cs.pinned_sheet("CNT_05") is None


class TestAPinMustNameAFileThatIsThere:
    """A correctly shaped name that is not in the folder -- a typo of a real
    variant, a file since renamed -- would be a broken pin from the moment it
    was made: stored, shown, and ignored. So the pin says no, and lists the
    files the cohort does have so the person can pick one."""

    def test_a_name_not_in_the_folder_is_refused_with_the_files_listed(self, folder):
        with pytest.raises(ValueError) as e:
            cs.pin_cohort_sheet("CNT_05", "Connectome_05_Animal_Tracking9.xlsx")
        msg = str(e.value)
        assert "no file of that name" in msg and REAL in msg
        msg.encode("ascii")
        assert cs._stored_pin("CNT_05") is None          # nothing was recorded

    def test_a_case_difference_is_stored_as_the_real_name(self, folder):
        """Windows does not care about letter case; an exact-name comparison
        does. Storing the file's real name keeps the pin in force."""
        cs.pin_cohort_sheet("CNT_05", REAL.lower())
        assert cs._stored_pin("CNT_05") == REAL
        assert cs.pinned_sheet("CNT_05") == REAL


def _pin_directly(key: str, name: str) -> None:
    """Record a pin straight into the config, the way an older mousedb could
    have recorded it -- before a pin was checked when it was made."""
    cfg = json.loads(cs.CONFIG_PATH.read_text()) if cs.CONFIG_PATH.exists() else {}
    cfg.setdefault("pinned_sheets", {})[key] = name
    cs.CONFIG_PATH.write_text(json.dumps(cfg))


class TestABrokenPinIsAnnounced:
    """THE DEFECT. find_cohort_sheet honours a pin only while it names one of
    the cohort's files. When the pinned file was renamed, moved or deleted, it
    fell back to the newest file WITHOUT notice: the status went on reporting
    the old name as pinned, the Tracking Sheets tab marked the file being read
    "[pinned]" although nobody had chosen it, and a cohort with several files
    was not flagged as a choice to make. The import read a file no person had
    picked while every display said one had.

    The fallback stays (the cohort keeps importing), but broken_pins names the
    broken pin, the file read instead and what to do; `mousedb cohort-sheets`
    prints it once; and a broken pin is kept apart from the refused-files list,
    whose names callers never import."""

    PINNED = "Connectome_05_Animal_Tracking1.xlsx"
    RENAMED = "Connectome_05_Animal_Tracking2.xlsx"

    def _pin_then_lose(self, folder, how):
        (folder / self.PINNED).write_bytes(b"pinned")
        cs.pin_cohort_sheet("CNT_05", self.PINNED)
        assert cs.broken_pins() == []                      # in force while it is there
        if how == "rename":
            (folder / self.PINNED).rename(folder / self.RENAMED)
            _newer(folder / self.RENAMED)
        else:
            (folder / self.PINNED).unlink()

    def test_a_renamed_pinned_file_is_reported_with_the_file_read_instead(self, folder):
        self._pin_then_lose(folder, "rename")
        reading = cs.find_cohort_sheet("CNT_05").name
        assert reading == self.RENAMED          # still imports: the newest, as if unpinned
        [b] = cs.broken_pins()
        assert (b["cohort_id"], b["pinned"], b["reading"]) == ("CNT_05", self.PINNED, reading)
        assert "renamed, moved or deleted" in b["why"]
        assert "CNT_05 is reading %s instead" % reading in b["why"]
        assert "This is the sheet" in b["why"] and "mousedb-sheets pin CNT_05" in b["why"]

    def test_the_pin_record_is_kept_so_a_display_can_tell(self, folder):
        """pinned_sheet returns the record as stored, broken or not: a status
        display compares it with the cohort's files, and must never call a
        broken pin "pinned". End to end through sheet_sync's status."""
        self._pin_then_lose(folder, "rename")
        assert cs.pinned_sheet("CNT_05") == self.PINNED
        c = ss.cohort_status("05")
        assert c["sheet"] == self.RENAMED
        assert c["pinned"] != self.PINNED

    def test_a_deleted_pinned_file_with_one_file_left(self, folder):
        self._pin_then_lose(folder, "delete")
        [b] = cs.broken_pins()
        assert b["reading"] == REAL and "CNT_05 is reading %s instead" % REAL in b["why"]

    def test_a_cohort_left_with_no_file_says_so(self, folder):
        (folder / "Connectome_06_Animal_Tracking.xlsx").write_bytes(b"x")   # still a sheets folder
        cs.pin_cohort_sheet("CNT_05", REAL)
        (folder / REAL).unlink()
        assert cs.find_cohort_sheet("CNT_05") is None
        [b] = cs.broken_pins()
        assert b["reading"] is None and "it has no sheet to read" in b["why"]
        # A cohort with no file has no row in the Tracking Sheets tab, so the
        # instruction must not be "select its row": put the file back or unpin.
        assert "This is the sheet" not in b["why"]
        assert "put the workbook back" in b["why"] and "mousedb-sheets unpin CNT_05" in b["why"]

    def test_the_import_reads_the_newest_file_meanwhile(self, folder, monkeypatch):
        """Reported, not blocking: a broken pin does not stop the cohort's
        import -- it reads exactly what an unpinned cohort would."""
        import mousedb.importers as imp
        opened = []

        class Recorder:
            db = None   # no database: sheet_sync then skips animal records

            def import_cohort_file(self, path, dry_run=False):
                opened.append(Path(path).name)
                return {"success": True, "imported": {}, "warnings": [], "errors": []}

        monkeypatch.setattr(imp, "ExcelImporter", lambda *a, **k: Recorder())
        self._pin_then_lose(folder, "rename")
        ss.import_cohorts(["CNT_05"], dry_run=True)
        assert opened == [self.RENAMED]

    def test_mousedb_cohort_sheets_prints_it_once(self, folder):
        self._pin_then_lose(folder, "rename")
        text = cs.describe()
        assert "PIN BROKEN: CNT_05 is pinned to %s" % self.PINNED in text
        assert text.count(self.PINNED) == 1
        assert "NOT read" not in text             # a pin is not a refused file
        text.encode("ascii")

    def test_a_broken_pin_never_makes_a_real_file_refused(self, folder):
        """Callers treat refused_sheet_files as "never import this file". A
        pin recorded (before pins were checked) on ANOTHER cohort's real sheet
        is broken for its own cohort, but must not stop that sheet's import."""
        other = "Connectome_06_Animal_Tracking.xlsx"
        (folder / other).write_bytes(b"x")
        _pin_directly("CNT_05", other)
        assert cs.refused_sheet_files() == []
        assert "CNT_06" in cs.available_cohorts() and cs.find_cohort_sheet("CNT_06").name == other
        [b] = cs.broken_pins()
        assert b["pinned"] == other and "is not named as cohort CNT_05's sheet" in b["why"]
        assert cs.find_cohort_sheet("CNT_05").name == REAL

    def test_a_pin_recorded_on_a_now_refused_file_says_why(self, folder):
        """Pins were not checked before the naming rule existed. One recorded
        on a file the rule now refuses is broken, and says the refusal."""
        name = "Copy of Connectome_05_Animal_Tracking.xlsx"
        (folder / name).write_bytes(b"x")
        _newer(folder / name)
        _pin_directly("CNT_05", name)
        [b] = cs.broken_pins()
        assert "is in the folder but is not read as a sheet" in b["why"] and "rename it" in b["why"]
        assert b["reading"] == REAL
        assert cs.find_cohort_sheet("CNT_05").name == REAL

    def test_a_pin_in_force_is_not_announced(self, folder):
        (folder / self.PINNED).write_bytes(b"v")
        _newer(folder / self.PINNED)
        cs.pin_cohort_sheet("CNT_05", REAL)
        assert cs.find_cohort_sheet("CNT_05").name == REAL
        assert cs.broken_pins() == []
        assert "PIN BROKEN" not in cs.describe()

    def test_a_non_ascii_pinned_name_is_printed_escaped(self, folder):
        name = "Connectome_05_Animal_Tracking \u2013 old.xlsx"
        (folder / name).write_bytes(b"x")
        cs.pin_cohort_sheet("CNT_05", name)
        (folder / name).unlink()
        assert cs.broken_pins()[0]["pinned"] == name          # the raw name, for a GUI
        text = cs.describe()
        text.encode("ascii")
        assert "PIN BROKEN: CNT_05 is pinned to Connectome_05_Animal_Tracking \\u2013 old.xlsx" in text

    def test_a_non_ascii_folder_path_is_printed_escaped(self, tmp_path, monkeypatch):
        """Not only file names: the folder path describe() prints, and the
        config path (it holds the user's home folder name), come from the
        machine and can hold any character too. One accented letter in a
        folder name used to make `mousedb cohort-sheets` crash on a console."""
        d = tmp_path / "Donn\u00e9es"
        d.mkdir()
        (d / REAL).write_bytes(b"real")
        monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setenv(cs.ENV_VAR, str(d))
        text = cs.describe()
        text.encode("ascii")
        assert "Donn\\xe9es" in text


class TestTheImportReadsTheRealSheet:
    """End to end through sheet_sync, the path the scheduled import and the
    GUI's Import buttons both take: status and import must name the real sheet."""

    def test_status_is_not_confused_by_a_newer_copy(self, folder):
        (folder / "Copy of Connectome_05_Animal_Tracking.xlsx").write_bytes(b"x")
        _newer(folder / "Copy of Connectome_05_Animal_Tracking.xlsx")
        c = ss.cohort_status("05")
        assert c["sheet"] == REAL
        assert [x["name"] for x in c["candidates"]] == [REAL]
        assert not c["ambiguous"]

    def test_the_import_opens_the_real_sheet(self, folder, monkeypatch):
        import mousedb.importers as imp
        opened = []

        class Recorder:
            db = None   # no database: sheet_sync then skips animal records

            def import_cohort_file(self, path, dry_run=False):
                opened.append(Path(path).name)
                return {"success": True, "imported": {}, "warnings": [], "errors": []}

        monkeypatch.setattr(imp, "ExcelImporter", lambda *a, **k: Recorder())
        for name in REFUSED:
            (folder / name).write_bytes(b"x")
            _newer(folder / name)
        r = ss.import_cohorts(["CNT_05"], dry_run=True)
        assert opened == [REAL]
        assert r["cohorts"][0]["sheet_name"] == REAL
