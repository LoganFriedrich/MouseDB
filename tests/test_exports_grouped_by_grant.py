"""A grant's projects sit together in the exports.

WHY (2026-10-30): one grant funds two projects (two wings of one study), each
with its own cohorts. Exported flat, the second project's folder sat beside the
first as if unrelated. With the grant set in the lab settings the exports are
<GRANT>/<PROJECT>/<COHORT>/; a project in no grant stays <PROJECT>/<COHORT>/;
and the folder a project used to have is moved to the archive once its
replacement is written, never left looking current.
"""
import json

import pytest

from mousedb import grants
from mousedb.exporters import layout


@pytest.fixture
def lab(tmp_path, monkeypatch):
    cfg = tmp_path / "mousedb_lab_config.json"
    cfg.write_text(json.dumps({"grants": {"GrantA": ["PROJA", "PROJAB"]}}), encoding="utf-8")
    monkeypatch.setenv("MOUSEDB_LAB_CONFIG", str(cfg))
    return tmp_path


def test_a_grants_projects_are_filed_inside_it(lab):
    root = lab / "current"
    assert layout.cohort_dir(root, "PROJAB_02", "PROJAB_02") == root / "GrantA" / "PROJAB" / "PROJAB_02"
    assert layout.combined_file(root, "reaches_analysis", "PROJA").parent == root / "GrantA" / "PROJA"
    assert layout.cohort_dir(root, "PROJC_01", "PROJC_01") == root / "PROJC" / "PROJC_01"


def test_locate_finds_a_cohort_inside_its_grant(lab):
    root = lab / "current"
    f = layout.cohort_file(root, "reaches_analysis", "PROJAB_02", "PROJAB_02")
    f.parent.mkdir(parents=True)
    f.write_text("x")
    assert layout.locate("reaches_analysis", "PROJAB_02", root) == f


def test_the_old_project_folder_is_archived_once_the_new_one_exists(lab):
    root = lab / "exports" / "current"
    old = root / "PROJA" / "PROJA_05"
    old.mkdir(parents=True)
    (old / "a.csv").write_text("old")
    assert layout.archive_moved_project_folders(root) == []          # nothing new yet: kept
    new = root / "GrantA" / "PROJA" / "PROJA_05"
    new.mkdir(parents=True)
    (new / "a.csv").write_text("new")
    moved = layout.archive_moved_project_folders(root)
    assert len(moved) == 1 and not (root / "PROJA").exists()
    assert (lab / "_archived" / "exports_current").is_dir()          # moved, never deleted


def test_a_project_under_two_grants_is_refused(lab):
    (lab / "mousedb_lab_config.json").write_text(json.dumps(
        {"grants": {"GrantA": ["PROJA"], "GrantB": ["PROJA"]}}), encoding="utf-8")
    with pytest.raises(ValueError):
        grants.grants()


def test_setting_a_grant_keeps_the_rest_of_the_settings(lab):
    cfg = lab / "mousedb_lab_config.json"
    cfg.write_text(json.dumps({"sheet_projects": {"PROJA": {"sheet_name": "A_{NN}.xlsx"}}}))
    grants.set_grant("GrantA", ["proja", "projab"])
    data = json.loads(cfg.read_text())
    assert data["grants"] == {"GrantA": ["PROJA", "PROJAB"]}
    assert data["sheet_projects"]["PROJA"]["sheet_name"] == "A_{NN}.xlsx"
    with pytest.raises(ValueError):
        grants.set_grant("GrantB", ["PROJA"])                         # already GrantA's
    with pytest.raises(ValueError):
        grants.set_grant("../x", ["PROJC"])                           # not a folder name
