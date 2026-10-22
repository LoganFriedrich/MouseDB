"""Where Is My Data says how far each study's list of sessions has got.

WHY: a study needs particular sessions spread over many cohorts and
recordings, and the per-cohort table cannot say how far those have got.
MouseReach's coverage report writes <list>.coverage.json beside each list;
this reads every one and gives one line per list. A report that cannot be
read is shown as such, never dropped.
"""
import json

import mousedb.data_status as ds


def test_one_line_per_list(tmp_path):
    folder = tmp_path / ds.SESSION_LISTS_FOLDER
    folder.mkdir()
    (folder / "study_a.txt").write_text("20220101_X01_P1\n", encoding="utf-8")
    (folder / "study_a.coverage.json").write_text(json.dumps({
        "written_at": "2026-10-22T03:41:41", "sessions": 3,
        "by_stage": {"recording waiting to be cut": 2, "analysed": 1}}), encoding="utf-8")
    (folder / "study_b.coverage.json").write_text("{not json", encoding="utf-8")

    rows = ds.read_session_coverage(folder)
    lines = ds.session_list_lines(rows)

    assert [r["list"] for r in rows] == ["study_a", "study_b"]
    assert lines[0] == ("Sessions list study_a: 3 sessions -- 2 recording waiting to be cut, "
                        "1 analysed (report written 2026-10-22T03:41:41)")
    assert lines[1].startswith("Sessions list study_b: report unreadable")


def test_no_folder_means_no_lines(tmp_path):
    assert ds.read_session_coverage(tmp_path / "absent") == []
    assert ds.session_list_lines([]) == []
