"""mousedb-quick-export and mousedb-reorganize answer --help and fail
clearly.

WHY: both used to ignore --help and start working (reading files, prompting
interactively), and quick-export met a missing generated/ folder with a
pandas traceback that said nothing about what the command expects or where
it looked.
"""
import builtins

import pandas as pd
import pytest

from mousedb.exporters import quick_export
from mousedb.utils import reorganize


# -- quick-export ----------------------------------------------------------

def test_quick_export_help_says_what_it_reads_and_writes(capsys):
    with pytest.raises(SystemExit) as ei:
        quick_export.main(["--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "generated" in out and "all_cohorts_pellet_level.csv" in out
    assert "unified_behavioral_data.xlsx" in out and "--dir" in out


def test_quick_export_missing_generated_folder_is_a_clear_exit_1(tmp_path, capsys):
    assert quick_export.main(["--dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert out.startswith("[FAIL]") and "generated" in out and "--dir" in out


def test_quick_export_names_the_missing_input_file(tmp_path, capsys):
    gen = tmp_path / "generated"
    gen.mkdir()
    (gen / "all_cohorts_pellet_level.csv").write_text("Animal\n")
    assert quick_export.main(["--dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "[FAIL]" in out and "all_cohorts_tray_summaries.csv" in out
    assert "all_cohorts_pellet_level.csv" not in out.split("missing input")[1]


def test_quick_export_default_dir_is_the_working_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert quick_export.main([]) == 1
    assert str(tmp_path / "generated") in capsys.readouterr().out


# -- reorganize ------------------------------------------------------------

def _never_prompt(*args):
    pytest.fail("must not prompt")


def test_reorganize_help_does_not_prompt(monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", _never_prompt)
    with pytest.raises(SystemExit) as ei:
        reorganize.main(["--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "--dir" in out and "reorganized_data_long_format.csv" in out


def test_reorganize_empty_dir_exits_1_without_prompting(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(builtins, "input", _never_prompt)
    assert reorganize.main(["--dir", str(tmp_path)]) == 1
    assert "No Excel files found" in capsys.readouterr().out


def test_reorganize_dir_is_where_it_reads_and_writes(tmp_path, monkeypatch):
    wide = pd.DataFrame({
        "Date": ["2026-06-01", "2026-06-01"], "Animal": ["CNT_05_01", "CNT_05_02"],
        "Sex": ["M", "F"], "Weight": [25.0, 22.5], "Tray Type/Number": ["P1", "P2"],
        **{i: [2, 1] for i in range(1, 21)},
    })
    wide.to_excel(tmp_path / "scores.xlsx", sheet_name="Pillar", index=False)
    monkeypatch.setattr(builtins, "input", lambda *a: "all")

    assert reorganize.main(["--dir", str(tmp_path)]) == 0
    out = pd.read_csv(tmp_path / "reorganized_data_long_format.csv")
    assert len(out) == 2 * 20
    assert set(out["Source_File"]) == {"scores.xlsx"}
    assert set(out["Source_Sheet"]) == {"Pillar"}
    assert sorted(out["Pellet_Number"].unique()) == list(range(1, 21))
