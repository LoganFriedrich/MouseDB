"""Each project names its own tracking-sheet file pattern, and every lookup uses it.

2026-10-29: a second project's cohort sheets sat in the sheets folder, named
by their own pattern, and were never recognised -- so never imported, and
their hand scores never reached the reaching pipeline -- because one
project's sheet name was written into the code.
"""
import json

import pytest

from mousedb import cohort_sheets, sheet_projects, sheet_sync
from mousedb.importers import ExcelImporter

A = "ProjectA_{NN}_Animal_Tracking.xlsx"
AB = "Silencing_(PROJAB)_{NN}_Animal_Tracking.xlsx"     # parentheses, as real names have


@pytest.fixture
def two_projects(tmp_path, monkeypatch):
    cfg = tmp_path / "mousedb_lab_config.json"
    cfg.write_text(json.dumps({"sheet_projects": {
        "PROJA": {"sheet_name": A}, "PROJAB": {"sheet_name": AB}}}), encoding="utf-8")
    monkeypatch.setenv(sheet_projects.ENV_VAR, str(cfg))
    sheets = tmp_path / "sheets"
    sheets.mkdir()
    for name in ("ProjectA_01_Animal_Tracking.xlsx", "ProjectA_02_Animal_Tracking.xlsx",
                 "Silencing_(PROJAB)_02_Animal_Tracking.xlsx"):
        (sheets / name).write_bytes(b"x")
    return sheets


def test_both_projects_cohorts_are_found_by_their_own_names(two_projects):
    assert sorted(cohort_sheets.available_cohorts(two_projects)) == ["PROJAB_02", "PROJA_01", "PROJA_02"]


def test_the_same_cohort_number_in_two_projects_is_two_cohorts(two_projects):
    a = cohort_sheets.find_cohort_sheet("PROJA_02", sheets_dir=two_projects)
    ab = cohort_sheets.find_cohort_sheet("PROJAB_02", sheets_dir=two_projects)
    assert a.name == "ProjectA_02_Animal_Tracking.xlsx"
    assert ab.name == "Silencing_(PROJAB)_02_Animal_Tracking.xlsx"
    assert cohort_sheets.find_cohort_sheet("PROJAB_01", sheets_dir=two_projects) is None


def test_a_bare_number_is_refused_when_two_projects_have_sheets(two_projects):
    cohort, why = sheet_sync.requested_cohort_id("02")
    assert cohort is None and "PROJA" in why and "PROJAB" in why
    assert sheet_sync.requested_cohort_id("projab02") == ("PROJAB_02", None)


def test_a_project_with_no_sheet_name_is_refused_by_name(two_projects):
    cohort, why = sheet_sync.requested_cohort_id("PROJC_01")
    assert cohort == "PROJC_01" and "no sheet name is set for project PROJC" in why


def test_the_importer_files_each_workbook_under_its_own_project(two_projects):
    pick = ExcelImporter.__dict__["_extract_cohort_from_filename"]
    assert pick(None, "Silencing_(PROJAB)_02_Animal_Tracking.xlsx") == "PROJAB_02"
    assert pick(None, "ProjectA_02_Animal_Tracking.xlsx") == "PROJA_02"
    assert pick(None, "PROJAB_02 notes.xlsx") == "PROJAB_02"        # longest code first
    assert pick(None, "PROJA_021.xlsx") is None                     # not cohort 02 of anything
    assert pick(None, "Other_02_Animal_Tracking.xlsx") is None


def test_a_name_two_patterns_fit_is_refused_not_guessed(tmp_path, monkeypatch):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"sheet_projects": {
        "PROJA": {"sheet_name": "Track_{NN}.xlsx"}, "PROJB": {"sheet_name": "Track_{NN}_B.xlsx"}}}))
    monkeypatch.setenv(sheet_projects.ENV_VAR, str(cfg))
    cohort, reason = cohort_sheets._sheet_verdict("Track_03_B.xlsx")
    assert cohort is None and "more than one project" in reason


def test_with_no_project_set_nothing_is_a_sheet_and_it_says_why(tmp_path, monkeypatch):
    monkeypatch.setenv(sheet_projects.ENV_VAR, str(tmp_path / "absent.json"))
    (tmp_path / "ProjectA_01_Animal_Tracking.xlsx").write_bytes(b"x")
    assert cohort_sheets.available_cohorts(tmp_path) == []
    assert "no project's sheet name is set" in sheet_projects.describe()
    assert "mousedb sheet-projects --set" in sheet_projects.describe()


@pytest.mark.parametrize("bad", ["Tracking.xlsx", "{NN}_Tracking.xlsx", "T_{NN}_{NN}.xlsx", "T_{NN}.csv"])
def test_a_pattern_that_cannot_name_one_cohort_is_refused(tmp_path, monkeypatch, bad):
    monkeypatch.setenv(sheet_projects.ENV_VAR, str(tmp_path / "c.json"))
    with pytest.raises(ValueError):
        sheet_projects.set_project("PROJA", bad)
    assert not (tmp_path / "c.json").exists()


def test_set_project_keeps_the_rest_of_the_settings_file(tmp_path, monkeypatch):
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"other_setting": 1, "sheet_projects": {"PROJA": {"sheet_name": A}}}))
    monkeypatch.setenv(sheet_projects.ENV_VAR, str(cfg))
    sheet_projects.set_project("projab", AB, template="sheet_templates/PROJAB.xlsx")
    data = json.loads(cfg.read_text())
    assert data["other_setting"] == 1
    assert data["sheet_projects"]["PROJA"]["sheet_name"] == A
    assert data["sheet_projects"]["PROJAB"] == {"sheet_name": AB, "template": "sheet_templates/PROJAB.xlsx"}
    assert sheet_projects.definitions()["PROJAB"].template == tmp_path / "sheet_templates" / "PROJAB.xlsx"


def test_a_pin_for_one_project_never_answers_for_the_other(two_projects, tmp_path, monkeypatch):
    monkeypatch.setattr(cohort_sheets, "CONFIG_PATH", tmp_path / "user_config.json")
    cohort_sheets.pin_cohort_sheet("PROJA_02", "ProjectA_02_Animal_Tracking.xlsx", sheets_dir=two_projects)
    assert cohort_sheets.pinned_sheet("PROJA_02") == "ProjectA_02_Animal_Tracking.xlsx"
    assert cohort_sheets.pinned_sheet("PROJAB_02") is None
    with pytest.raises(ValueError):
        cohort_sheets.pin_cohort_sheet("PROJAB_02", "ProjectA_02_Animal_Tracking.xlsx",
                                       sheets_dir=two_projects)
