"""The cohort tracking sheets must be findable, current, and not baked into source.

The tracking sheet is a cohort's source of record. Getting the location wrong is
not a minor inconvenience: `mousedb import` had a hardcoded default pointing at a
directory that had since moved, so it found nothing and reported that as though
there were nothing to import. Anyone going looking then found the snapshots under
`Databases/_archive/` -- where the cohort 05 copy is a 92 KB stub with an empty
subject table, while the live sheet is 359 KB and carries 2,234 rows of tray
scoring, weights, injury and injection details. Reading the stale one leads
straight to the wrong conclusion about what has been recorded for a cohort.

Three properties, pinned here:
  - nothing lab-specific is in the source (this repository is public),
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
    """A directory of cohort sheets, configured and reachable."""
    d = tmp_path / "Animal_Cohorts"
    d.mkdir()
    for n in ("00", "01", "05"):
        (d / f"Connectome_{n}_Animal_Tracking.xlsx").write_bytes(b"x")
    monkeypatch.setenv(cs.ENV_VAR, str(d))
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
                     "G:\\", "Y:/LAB_ROOT", "user_data"):
            assert leak not in text, f"lab-specific string in source: {leak!r}"

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
        assert cs.available_cohorts() == ["00", "01", "05"]


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
