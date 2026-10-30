"""The cohort tracking sheets must be findable, current, and not baked into source.

The tracking sheet is a cohort's source of record. Getting the location wrong is
not a minor inconvenience: `mousedb import` had a hardcoded default pointing at a
directory that had since moved, so it found nothing and reported that as though
there were nothing to import. Anyone going looking then found old snapshot copies
in an archive folder -- a fraction of the live sheet's size, with an empty
subject table and none of the scoring, weights, injury or injection details
recorded since. Reading the stale one leads straight to the wrong conclusion
about what has been recorded for a cohort.

Three properties, pinned here:
  - nothing lab-specific is in the source (this repository is public) -- no
    path, and no numbers describing one lab's own sheets,
  - a snapshot folder is never accepted as a source,
  - an unconfigured machine says so instead of guessing.
"""

import json
import os
from pathlib import Path

import pytest

from mousedb import cohort_sheets as cs


@pytest.fixture
def sheets(tmp_path, monkeypatch):
    """A directory of cohort sheets, configured and reachable -- with a private
    config file. WHY: lookups honour pins, which live in the config file; left
    pointing at this machine's real one, a pin a person made there (say on
    cohort 05) would change which file these tests find."""
    d = tmp_path / "Animal_Cohorts"
    d.mkdir()
    for n in ("00", "01", "05"):
        (d / f"Connectome_{n}_Animal_Tracking.xlsx").write_bytes(b"x")
    monkeypatch.setenv(cs.ENV_VAR, str(d))
    monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "config.json")
    return d


@pytest.fixture
def unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv(cs.ENV_VAR, raising=False)
    monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "config.json")
    return tmp_path


class TestNothingLabSpecificInSource:
    """This repository is public. A synced-folder path carries a username, a
    drive letter, and an organisation's internal folder names."""

    def test_the_module_names_no_organisation_or_drive(self):
        text = Path(cs.__file__).read_text(encoding="utf-8")
        for leak in ("OneDrive - ", "Sharepoint", "SharePoint",
                     "G:\\", "Y:/", "Y:\\", "user_data"):
            assert leak not in text, f"lab-specific string in source: {leak!r}"

    @pytest.mark.parametrize("path", [cs.__file__, __file__])
    def test_no_numbers_describing_one_labs_sheets(self, path):
        """The WHY behind 'no built-in default' used to be told with one lab's
        own figures -- file sizes in KB, row and weight counts in the
        thousands, an edit date. Those describe a corpus, not the defect, and
        this repository is public: the reason stays, the figures go. These
        shapes are what such figures look like; neither file needs any."""
        import re
        text = Path(path).read_text(encoding="utf-8")
        for shape, why in ((r"\b\d+(\.\d+)?\s?[KMG]B\b", "a file size"),
                           (r"\b\d{1,3}(,\d{3})+\b", "a count in the thousands"),
                           (r"last edited\s+\d{4}-\d\d-\d\d", "an edit date")):
            hit = re.search(shape, text)
            assert hit is None, f"{why} in {Path(path).name}: {hit.group(0)!r}"

    def test_there_is_no_built_in_fallback_path(self, unconfigured):
        """A wrong default that silently resolves is worse than no default: it
        produces a confident wrong answer."""
        assert cs.cohort_sheets_dir() is None

    def test_unconfigured_explains_itself(self, unconfigured):
        msg = cs.describe()
        assert "No cohort tracking sheets are configured" in msg
        assert "--set" in msg and "--discover" in msg
        assert cs.ENV_VAR in msg


class TestResolution:

    def test_environment_variable_wins(self, sheets):
        assert cs.cohort_sheets_dir() == sheets

    def test_config_file_is_used_when_no_env(self, unconfigured):
        d = unconfigured / "elsewhere"
        d.mkdir()
        (d / "Connectome_02_Animal_Tracking.xlsx").write_bytes(b"x")
        cs.set_cohort_sheets_dir(d)
        assert cs.cohort_sheets_dir() == d
        saved = json.loads(cs.CONFIG_PATH.read_text())
        assert saved["cohort_sheets_dir"] == str(d)

    def test_a_directory_with_no_sheets_is_not_the_directory(self, unconfigured, monkeypatch):
        empty = unconfigured / "empty"
        empty.mkdir()
        monkeypatch.setenv(cs.ENV_VAR, str(empty))
        assert cs.cohort_sheets_dir() is None

    def test_a_missing_directory_does_not_raise(self, unconfigured, monkeypatch):
        monkeypatch.setenv(cs.ENV_VAR, str(unconfigured / "nope"))
        assert cs.cohort_sheets_dir() is None


class TestSnapshotsAreNeverSources:

    @pytest.mark.parametrize("path", [
        "/data/Databases/_archive/old_cohort_scripts/Connectome_05_Animal_Tracking.xlsx",
        "/data/cohorts/generated/Connectome_01_Animal_Tracking.xlsx",
        "/data/cohorts/Archive/Connectome_01_Animal_Tracking.xlsx",
        "/data/backup/Connectome_01_Animal_Tracking.xlsx",
    ])
    def test_snapshot_locations_are_flagged(self, path):
        assert cs.is_stale_source(path)

    def test_a_live_location_is_not_flagged(self, sheets):
        assert not cs.is_stale_source(sheets / "Connectome_05_Animal_Tracking.xlsx")


class TestFindingASheet:

    def test_finds_the_sheet_for_a_cohort(self, sheets):
        p = cs.find_cohort_sheet("CNT_05")
        assert p is not None and p.name == "Connectome_05_Animal_Tracking.xlsx"

    @pytest.mark.parametrize("spelling", ["CNT_05", "CNT05", "05", "5"])
    def test_cohort_can_be_named_several_ways(self, sheets, spelling):
        assert cs.find_cohort_sheet(spelling) is not None

    def test_a_cohort_with_no_sheet_returns_none(self, sheets):
        assert cs.find_cohort_sheet("CNT_09") is None

    def test_the_newest_variant_wins(self, sheets):
        """Cohorts acquire '...Tracking1.xlsx' and dated variants; the one being
        kept up to date is the one to read."""
        import time
        newer = sheets / "Connectome_05_Animal_Tracking1.xlsx"
        newer.write_bytes(b"xx")
        os.utime(newer, (time.time() + 60, time.time() + 60))
        assert cs.find_cohort_sheet("CNT_05") == newer

    def test_excel_lock_files_are_ignored(self, sheets):
        (sheets / "~$Connectome_05_Animal_Tracking.xlsx").write_bytes(b"x")
        assert cs.find_cohort_sheet("CNT_05").name.startswith("Connectome_")

    def test_available_cohorts_lists_what_is_there(self, sheets):
        assert cs.available_cohorts() == ["CNT_00", "CNT_01", "CNT_05"]


class TestFetchingAFreshCopy:

    def test_it_copies_rather_than_reading_in_place(self, sheets, tmp_path):
        """These live in a synced folder: reading in place competes with the
        sync, and opening one for writing can block a colleague."""
        work = tmp_path / "work"
        got = cs.fetch_cohort_sheet("CNT_05", work)
        assert got is not None
        assert got.parent == work
        assert got.read_bytes() == (sheets / "Connectome_05_Animal_Tracking.xlsx").read_bytes()
        assert (sheets / "Connectome_05_Animal_Tracking.xlsx").exists(), "source untouched"

    def test_a_missing_cohort_returns_none(self, sheets, tmp_path):
        assert cs.fetch_cohort_sheet("CNT_09", tmp_path / "work") is None


# ---------------------------------------------------------------------------
# ASPA animal sheets -- the other source-of-record folder
# ---------------------------------------------------------------------------

@pytest.fixture
def aspa(tmp_path, monkeypatch):
    """An ASPA animal-data folder holding every name shape the module meets:
    a bare letter, a letter with a description, an "Opt"-prefixed letter with
    and without one, and workbooks that are not per-cohort sheets. The
    descriptions are placeholders; a real folder's say what each cohort was."""
    d = tmp_path / "Animal Data"
    d.mkdir()
    for name in ("I.xlsx", "J.xlsx", "K - Description 1.xlsx",
                 "G - Description.xlsx", "OptD - Part 1 - description.xlsx",
                 "OptE.xlsx", "1 Overview.xlsx", "ABC1.xlsx",
                 "XYZ1.xlsx"):
        (d / name).write_bytes(b"x")
    monkeypatch.setenv(cs.ASPA_ENV_VAR, str(d))
    return d


class TestAspaCohortLetters:
    """ASPA ids are encoded cohort-number = alphabet position, as a RULE."""

    @pytest.mark.parametrize("given,letter", [
        ("J", "J"), ("10", "J"), (10, "J"), ("ASPA_10", "J"),
        ("ASPA_10_11", "J"), ("ASPA1011", "J"),
        ("I", "I"), ("ASPA0901", "I"), ("D", "D"), ("ASPA0401", "D"),
    ])
    def test_anything_naming_a_cohort_resolves_to_its_letter(self, given, letter):
        assert cs.aspa_letter(given) == letter

    @pytest.mark.parametrize("letter,number", [
        ("D", "04"), ("I", "09"), ("J", "10"), ("M", "13"),
    ])
    def test_letter_to_number_matches_the_recorded_examples(self, letter, number):
        """The rule's worked examples: D01 -> ASPA0401, M04 -> ASPA1304."""
        assert cs.aspa_cohort_number(letter) == number

    def test_it_round_trips(self):
        for n in range(1, 27):
            L = cs.aspa_letter(str(n))
            assert cs.aspa_cohort_number(L) == "%02d" % n

    @pytest.mark.parametrize("junk", ["", "ASPA", "zz", "99", "0"])
    def test_nonsense_returns_none(self, junk):
        assert cs.aspa_letter(junk) is None


class TestFindingAnAspaSheet:

    def test_a_bare_letter_sheet(self, aspa):
        assert cs.find_aspa_sheet("J").name == "J.xlsx"

    def test_a_sheet_with_a_description(self, aspa):
        assert cs.find_aspa_sheet("K").name == "K - Description 1.xlsx"

    def test_an_opt_prefixed_sheet(self, aspa):
        """'Opt' is a prefix, not part of the cohort letter. Matching only a
        bare letter silently lost every cohort whose workbook carries it."""
        assert cs.find_aspa_sheet("D").name == "OptD - Part 1 - description.xlsx"
        assert cs.find_aspa_sheet("E").name == "OptE.xlsx"

    def test_an_encoded_animal_id_finds_its_cohort(self, aspa):
        assert cs.find_aspa_sheet("ASPA1011").name == "J.xlsx"

    def test_multi_letter_names_are_not_cohorts(self, aspa):
        """Multi-letter names and an overview workbook are not per-cohort
        animal sheets."""
        assert set(cs.available_aspa_cohorts()) == {"D", "E", "G", "I", "J", "K"}

    def test_a_cohort_with_no_sheet(self, aspa):
        assert cs.find_aspa_sheet("Z") is None

    def test_unconfigured_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv(cs.ASPA_ENV_VAR, raising=False)
        monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "c.json")
        assert cs.aspa_data_dir() is None
        assert cs.find_aspa_sheet("J") is None

    def test_fetch_copies_and_leaves_the_source(self, aspa, tmp_path):
        got = cs.fetch_aspa_sheet("J", tmp_path / "work")
        assert got is not None and got.parent == tmp_path / "work"
        assert (aspa / "J.xlsx").exists()
