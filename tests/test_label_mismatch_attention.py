"""A label mismatch held by the reaching pipeline is shown, naming both trays
and asking a person to decide whether the name or the sheet is wrong.

WHY: a video whose results match another tray's hand scores (the sheet or
the video's name has the trays swapped) is held by the pipeline outside every
review queue -- no review can fix it. The Where Is My Data tab and the
cohort's FIX THESE file are where a person learns of it, so the line must say
which two trays and both ways to fix it. mousedb reads the pipeline's lists;
it never imports the pipeline.
"""
import json
import os

import openpyxl

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from mousedb import attention

ITEM = {
    "kind": "label_mismatch", "needs_person": True,
    "video_id": "20250101_ABC0102_P3", "cohort_id": "ABC_01",
    "own_row": {"key": "20250101_ABC0102_P3", "agree": 7, "compared": 19},
    "matched_rows": [{"key": "20250101_ABC0102_P4", "agree": 19, "compared": 19}],
    "partner_back": {"video_id": "20250101_ABC0102_P4", "agree": 18, "compared": 20},
    "answers_kept": [4],
    "says": "pipeline text",
}


def _lists(tmp_path):
    att = tmp_path / "pipe" / "attention"
    att.mkdir(parents=True)
    (att / "HOST_hand_scores.json").write_text(json.dumps({"items": [ITEM]}))
    return tmp_path / "pipe"


def test_the_line_names_both_trays_and_both_fixes(tmp_path):
    [item] = attention.mousereach_items(_lists(tmp_path))
    says = item["says"]
    assert item["cohort_id"] == "ABC_01"
    assert says.startswith("LABEL MISMATCH: video 20250101_ABC0102_P3")
    assert "tray P4" in says and "19/19" in says and "P3's 7/19" in says
    assert "20250101_ABC0102_P4 matches P3's scores (18/20)" in says
    assert "Decide whether the recording's NAME or the SHEET is wrong" in says
    assert "A sheet error is kept as it is and recorded" in says
    assert "Archive/manual_corrections" in says
    assert "swap those rows" not in says and "fix the sheet" not in says.lower()
    assert "pellet(s) 4 are kept" in says


def test_it_reaches_the_fix_these_file(tmp_path):
    [item] = attention.mousereach_items(_lists(tmp_path))
    p = attention.write_fix_these("ABC_01", [item], tmp_path)
    ws = openpyxl.load_workbook(p).active
    assert ws["A5"].value.startswith("LABEL MISMATCH")
    assert ws["B5"].value == "reaching pipeline"


def test_an_item_without_the_fields_keeps_the_pipelines_text(tmp_path):
    bare = dict(ITEM, own_row=None, matched_rows=[])
    assert attention.label_mismatch_says(bare) is None
    att = tmp_path / "pipe" / "attention"
    att.mkdir(parents=True)
    (att / "HOST_hand_scores.json").write_text(json.dumps({"items": [bare]}))
    [item] = attention.mousereach_items(tmp_path / "pipe")
    assert item["says"] == "pipeline text"


def test_the_tab_shows_it_in_red(tmp_path):
    from PyQt5.QtWidgets import QApplication, QLabel
    from types import SimpleNamespace
    app = QApplication.instance() or QApplication([])
    from mousedb.gui.data_status_tab import DataStatusTab
    tab = SimpleNamespace(attention_label=QLabel(), ATTENTION_SHOWN=DataStatusTab.ATTENTION_SHOWN)
    DataStatusTab._show_attention(tab, attention.mousereach_items(_lists(tmp_path)))
    assert not tab.attention_label.isHidden()
    assert "LABEL MISMATCH: video 20250101_ABC0102_P3" in tab.attention_label.text()
    del app
