"""Every output folder's name is in ONE place (mousedb.output_folders).

WHY: the folders are browsed by people, so their names get changed. With a
name written out wherever a path was built, a rename left one writer behind,
still writing into the old folder beside the new one. These tests pin that
every writer and reader resolves its folder through output_folders, that the
guides and help texts name only folders the code uses, and that the analysis
mirror lands in the folder the lab settings name for an analysis.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from mousedb import output_folders as of

REPO = Path(__file__).resolve().parents[1]


def test_the_layout(tmp_path):
    """The names, pinned: a rename is made in output_folders and here, on
    purpose, and nowhere else in the code."""
    r = tmp_path
    assert of.all_cohorts_dir(r) == r / "exports" / "Behavior" / "All_Cohorts"
    assert of.selected_cohorts_dir(r) == r / "exports" / "Behavior" / "Selected_Cohorts"
    assert of.hand_scores_dir(r) == r / "exports" / "Behavior" / "Hand_Scores"
    assert of.sessions_without_video_dir(r) == r / "exports" / "Behavior" / "Sessions_Without_Video"
    assert of.tissue_dir(r) == r / "exports" / "Tissue"
    assert of.video_progress_dir(r) == r / "reports" / "MouseReach_Video_Processing_Progress"
    assert of.shown(of.hand_scores_dir, "x.csv") == "exports/Behavior/Hand_Scores/x.csv"


def test_every_writer_asks_output_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("MOUSEDB_ROOT", str(tmp_path))
    from mousedb import (config, data_status, hand_scores_file, import_analyses,
                         missing_videos, weekly_report)
    from mousedb.exporters import example_export, layout, subsets

    assert config.export_path() == of.exports_dir(tmp_path)
    assert layout.current_root() == of.all_cohorts_dir(tmp_path)
    assert subsets.subsets_root() == of.selected_cohorts_dir(tmp_path)
    assert example_export.default_out_dir().parent == of.all_cohorts_dir(tmp_path)
    assert hand_scores_file.out_dir() == of.hand_scores_dir(tmp_path)
    assert missing_videos.scores_path() == (of.hand_scores_dir(tmp_path)
                                            / hand_scores_file.FILE_NAME)
    assert missing_videos.csv_path().parent == of.sessions_without_video_dir(tmp_path)
    assert missing_videos.csv_shown().startswith(of.shown(of.sessions_without_video_dir))
    assert weekly_report.default_out() == of.video_progress_dir(tmp_path)
    assert import_analyses.manifest_path().parent == of.exports_dir(tmp_path)
    assert import_analyses.mirror_exports_dir(tmp_path, "Some_Analysis") == (
        of.tissue_dir(tmp_path) / "Some_Analysis")
    # The status tab reports the folder the exporter writes.
    assert "all_cohorts_dir" in Path(data_status.__file__).read_text(encoding="utf-8")


def test_replaced_files_go_to_the_roots_archive_only_from_the_usual_folders(tmp_path):
    from mousedb.exporters import subsets
    r = tmp_path / "root"
    assert of.all_cohorts_archive(of.all_cohorts_dir(r)) == r / "_archived" / "exports_current"
    assert of.selected_cohorts_archive(of.selected_cohorts_dir(r)) == (
        r / "_archived" / "exports_subsets")
    # Any other folder (a test's, a one-off --out-dir) archives inside itself.
    assert of.all_cohorts_archive(r / "somewhere" / "else") is None
    assert of.all_cohorts_archive(r / of.ALL_COHORTS) is None
    assert of.selected_cohorts_archive(of.all_cohorts_dir(r)) is None
    assert subsets.archive_root(of.selected_cohorts_dir(r)) == r / "_archived" / "exports_subsets"
    assert subsets.archive_root(r / "mine") == r / "mine" / "_archived"


def test_the_weekly_page_is_named_for_what_it_is(tmp_path, monkeypatch):
    from mousedb import weekly_report
    monkeypatch.setattr(weekly_report, "build", lambda now=None: "<p>page</p>")
    dated = weekly_report.write(tmp_path / "out", now=datetime(2026, 1, 5, 10, 0))
    assert dated.name == "MouseReach video processing progress 2026-01-05.html"
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        "MouseReach video processing progress - LATEST.html",
        "MouseReach video processing progress 2026-01-05.html"]


def test_the_page_the_tab_and_the_guide_carry_the_folders_name():
    """One name for one thing: the folder, its files, the page's heading and
    title, the window's tab and the guide. WHY: a person who opened the
    folder looks for the same words in the window and in the guides."""
    from mousedb import weekly_report
    words = of.VIDEO_PROGRESS_TITLE
    assert words == "MouseReach Video Processing Progress"
    assert of.VIDEO_PROGRESS == words.replace(" ", "_")
    assert of.VIDEO_PROGRESS_FILE_STEM.lower() == words.lower()
    page = weekly_report.build(now=datetime(2026, 1, 5, 10, 0))
    assert "<title>%s 2026-01-05</title>" % words in page
    assert "<h1>%s</h1>" % words in page
    app = (REPO / "mousedb" / "gui" / "app.py").read_text(encoding="utf-8")
    assert '"9. %s"' % words in app
    guide = REPO / "docs" / (words.upper().replace(" ", "_") + ".md")
    assert guide.read_text(encoding="utf-8").startswith("# %s" % words)


# ------------------------------------------------- the guides and help texts

def _texts():
    for f in sorted((REPO / "mousedb").rglob("*.py")) + sorted((REPO / "docs").glob("*.md")) \
            + [REPO / "README.md", REPO / "INSTALL.md"]:
        yield f, f.read_text(encoding="utf-8")


def test_no_guide_or_help_text_names_a_folder_the_code_does_not_use():
    """A text that still names a folder under its old name sends a person to a
    folder that is no longer written."""
    behavior = {of.ALL_COHORTS, of.SELECTED_COHORTS, of.HAND_SCORES, of.SESSIONS_WITHOUT_VIDEO}
    under_exports = {of.BEHAVIOR, of.TISSUE}
    wrong = []
    for f, text in _texts():
        for m in re.finditer(r"exports(?:[\\/]+)%s(?:[\\/]+)([A-Za-z_]+)" % of.BEHAVIOR, text):
            if m.group(1) not in behavior:
                wrong.append("%s: %s" % (f.name, m.group(0)))
        # <mousedb_root>/exports/<Capitalized>/: one of the two groups.
        for m in re.finditer(r"<mousedb_root>(?:[\\/]+)exports(?:[\\/]+)([A-Z][a-z]+)[\\/]", text):
            if m.group(1) not in under_exports:
                wrong.append("%s: %s" % (f.name, m.group(0)))
        for m in re.finditer(r"<mousedb_root>(?:[\\/]+)reports(?:[\\/]+)([^\\/\s`)]+)", text):
            if m.group(1) != of.VIDEO_PROGRESS:
                wrong.append("%s: %s" % (f.name, m.group(0)))
    assert not wrong, wrong


# ------------------------------------------- the analysis mirror's folders

def _lab_settings(root: Path, names) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "mousedb_lab_config.json").write_text(
        json.dumps({"analysis_mirror_folders": names}), encoding="utf-8")


def _registry(reg: Path, analysis: str) -> None:
    for rel, text in (("exports/%s/registry.json" % analysis,
                       json.dumps({"analysis_name": analysis, "version": 1, "entries": {}})),
                      ("exports/%s/S1/measurements.csv" % analysis, "id\n1\n"),
                      ("figures/%s/S1.png" % analysis, "fake"),
                      ("logs/%s.log" % analysis, "log\n")):
        p = reg / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    pipeline, db = tmp_path / "pipeline", tmp_path / "db"
    _registry(pipeline / "Registry", "Check_One")
    _registry(pipeline / "Registry", "Plain")
    monkeypatch.setenv("MOUSEDB_ROOT", str(db))
    monkeypatch.setenv("MOUSEBRAIN_PIPELINE_ROOT", str(pipeline))
    monkeypatch.setenv("MOUSEDB_LAB_CONFIG", str(db / "config" / "mousedb_lab_config.json"))
    return pipeline / "Registry", db


def test_a_key_and_its_mirror_path_are_each_others_reverse(mirror):
    from mousedb import import_analyses as ia
    _reg, db = mirror
    _lab_settings(db, {"Check_One": "Check_Ones"})
    p = ia.mirror_path(db, "exports/Check_One/S1/measurements.csv")
    assert p == of.tissue_dir(db) / "Check_Ones" / "S1" / "measurements.csv"
    assert ia.mirror_key(db, "Check_One", p) == "exports/Check_One/S1/measurements.csv"
    q = ia.mirror_path(db, "exports/Plain/registry.json")
    assert q == of.tissue_dir(db) / "Plain" / "registry.json"
    assert ia.mirror_key(db, "Plain", q) == "exports/Plain/registry.json"
    # Figures and logs keep the path they have at the source.
    for key in ("figures/Check_One/S1.png", "logs/Check_One.log"):
        assert ia.mirror_path(db, key) == db / key
        assert ia.mirror_key(db, "Check_One", db / key) == key


def test_the_mirror_lands_in_the_folder_the_lab_settings_name(mirror):
    from mousedb import import_analyses as ia
    reg, db = mirror
    _lab_settings(db, {"Check_One": "Check_Ones"})
    res = ia.run(log=lambda *_: None)
    assert res.errors == 0 and res.copied == 8
    assert (of.tissue_dir(db) / "Check_Ones" / "S1" / "measurements.csv").is_file()
    assert (of.tissue_dir(db) / "Check_Ones" / "registry.json").is_file()
    assert not (of.tissue_dir(db) / "Check_One").exists()       # no second folder beside it
    assert (of.tissue_dir(db) / "Plain" / "registry.json").is_file()
    assert (db / "figures" / "Check_One" / "S1.png").is_file()
    assert not (db / "exports" / "Check_One").exists()          # nothing at the old place
    assert not (db / "exports" / "Plain").exists()
    # The ledger keeps the source's keys, so it reads the same under any folder name.
    ledger = json.loads((db / "logs" / "analysis_imports.json").read_text())
    assert "exports/Check_One/S1/measurements.csv" in ledger
    again = ia.run(log=lambda *_: None)
    assert again.copied == 0 and again.archived == 0 and again.errors == 0


def test_a_withdrawn_file_is_archived_from_the_named_folder(mirror):
    from mousedb import import_analyses as ia
    reg, db = mirror
    _lab_settings(db, {"Check_One": "Check_Ones"})
    ia.run(log=lambda *_: None)
    (reg / "exports" / "Check_One" / "S1" / "measurements.csv").unlink()
    res = ia.run(log=lambda *_: None)
    assert res.archived == 1 and res.errors == 0
    assert not (of.tissue_dir(db) / "Check_Ones" / "S1" / "measurements.csv").exists()
    assert list((db / "_archived" / "analyses").glob("*/exports/Check_One/S1/measurements.csv"))


@pytest.mark.parametrize("names, says", [
    ({"Check_One": "a/b"}, "not one plain folder name"),
    ({"Check_One": ""}, "not one plain folder name"),
    ({"Check_One": "Same", "Plain": "same"}, "to two analyses"),
    (["Check_One"], "must map analysis names"),
])
def test_a_folder_name_that_cannot_be_used_stops_the_mirror(mirror, names, says):
    from mousedb import import_analyses as ia
    _reg, db = mirror
    _lab_settings(db, names)
    with pytest.raises(ValueError, match=says):
        ia.run(log=lambda *_: None)
    assert not (db / "exports").exists()                        # nothing was copied
    # The command reports it as a stopped run, never as a traceback.
    assert ia.main([]) == 1
    assert says in json.loads((db / "logs" / ia.STOP_RECORD_NAME).read_text())["reason"]
