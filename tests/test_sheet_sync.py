"""sheet_sync: status, pinning and the import ledger.

WHY: nothing used to record when a sheet was imported or whether it worked,
so 'is the database current?' had no answer and a failing import was
invisible. These tests pin the status verdicts and the it's-this-one pin.
"""
import json
from pathlib import Path

import pytest

import mousedb.cohort_sheets as cs
import mousedb.sheet_sync as ss


@pytest.fixture
def folder(tmp_path, monkeypatch):
    d = tmp_path / "sheets"
    d.mkdir()
    (d / "Connectome_05_Animal_Tracking.xlsx").write_bytes(b"x")
    monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv(cs.ENV_VAR, str(d))
    monkeypatch.setattr(ss, "LEDGER", tmp_path / "ledger.jsonl")
    return d


class TestStatus:
    def test_never_imported(self, folder):
        c = ss.cohort_status("05")
        assert c["state"] == "never_imported"
        assert c["sheet"] == "Connectome_05_Animal_Tracking.xlsx"
        assert not c["ambiguous"]

    def test_up_to_date_then_sheet_newer(self, folder):
        sheet = folder / "Connectome_05_Animal_Tracking.xlsx"
        ss._append_ledger({"cohort_id": "CNT_05", "success": True,
                           "sheet_name": sheet.name,
                           "sheet_mtime": ss._iso(sheet.stat().st_mtime),
                           "finished": "2026-08-28T10:00:00"})
        assert ss.cohort_status("05")["state"] == "up_to_date"
        # someone edits the sheet later
        import os, time
        later = time.time() + 3600
        os.utime(sheet, (later, later))
        assert ss.cohort_status("05")["state"] == "sheet_newer"

    def test_failed_import_is_shown_with_its_reason(self, folder):
        ss._append_ledger({"cohort_id": "CNT_05", "success": False,
                           "error": "NOT NULL constraint failed: pellet_scores.test_phase"})
        c = ss.cohort_status("05")
        assert c["state"] == "last_import_failed"
        assert "NOT NULL" in c["why"]

    def test_unconfigured_is_a_stated_problem(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.delenv(cs.ENV_VAR, raising=False)
        st = ss.status()
        assert not st["configured"] and st["problem"]


class TestPinning:
    def test_two_files_is_ambiguous_until_pinned(self, folder):
        (folder / "Connectome_05_Animal_Tracking (2).xlsx").write_bytes(b"y")
        c = ss.cohort_status("05")
        assert c["ambiguous"] and len(c["candidates"]) == 2
        cs.pin_cohort_sheet("CNT_05", "Connectome_05_Animal_Tracking.xlsx")
        c = ss.cohort_status("05")
        assert not c["ambiguous"]
        assert c["pinned"] == "Connectome_05_Animal_Tracking.xlsx"
        assert c["sheet"] == "Connectome_05_Animal_Tracking.xlsx"
        cs.pin_cohort_sheet("CNT_05", None)
        assert ss.cohort_status("05")["ambiguous"]

    def test_pin_survives_a_newer_rival(self, folder):
        import os, time
        rival = folder / "Connectome_05_Animal_Tracking1.xlsx"
        rival.write_bytes(b"z")
        later = time.time() + 3600
        os.utime(rival, (later, later))
        assert cs.find_cohort_sheet("CNT_05").name == rival.name  # newest wins
        cs.pin_cohort_sheet("CNT_05", "Connectome_05_Animal_Tracking.xlsx")
        assert cs.find_cohort_sheet("CNT_05").name == "Connectome_05_Animal_Tracking.xlsx"


class TestImportLedger:
    def test_every_outcome_is_recorded_even_a_crash(self, folder, monkeypatch):
        class Boom:
            def import_cohort_file(self, *a, **k):
                raise RuntimeError("workbook exploded")
        import mousedb.importers as imp
        monkeypatch.setattr(imp, "ExcelImporter", lambda *a, **k: Boom())
        r = ss.import_cohorts(["CNT_05"], triggered_by="test")
        c = r["cohorts"][0]
        assert c["success"] is False and "workbook exploded" in c["error"]
        entries = [json.loads(l) for l in Path(ss.LEDGER).read_text().splitlines()]
        assert entries[-1]["cohort_id"] == "CNT_05" and entries[-1]["triggered_by"] == "test"
        assert ss.cohort_status("05")["state"] == "last_import_failed"

    def test_dry_run_writes_no_ledger(self, folder, monkeypatch):
        class Fake:
            def import_cohort_file(self, *a, **k):
                return {"success": True, "imported": {"subjects": 1}, "warnings": [], "errors": []}
        import mousedb.importers as imp
        monkeypatch.setattr(imp, "ExcelImporter", lambda *a, **k: Fake())
        r = ss.import_cohorts(["CNT_05"], dry_run=True)
        assert r["cohorts"][0]["success"]
        assert not Path(ss.LEDGER).exists()
