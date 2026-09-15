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


@pytest.fixture(autouse=True)
def mutex_holds(monkeypatch):
    """Record every acquisition of the cross-task database mutex instead of
    taking the PRODUCTION lock (it lives under the configured logs folder, and a
    unit test contending with a real hourly task hangs the suite -- 2026-09-02)."""
    import contextlib
    from mousedb import task_mutex
    seen = []

    def fake_hold(*a, **k):
        seen.append(k.get("waiting_for"))
        return contextlib.nullcontext()

    monkeypatch.setattr(task_mutex, "hold", fake_hold)
    return seen


@pytest.fixture
def folder(tmp_path, monkeypatch):
    d = tmp_path / "sheets"
    d.mkdir()
    (d / "Connectome_05_Animal_Tracking.xlsx").write_bytes(b"x")
    monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv(cs.ENV_VAR, str(d))
    monkeypatch.setattr(ss, "LEDGER", tmp_path / "ledger.jsonl")
    return d


def test_full_import_refreshes_letter_cohort_odc_records(folder, monkeypatch, mutex_holds, tmp_path):
    """WHY: frozen letter cohorts' animal details live in their ODC tab; without this
    pass the per-reach export's animal columns stayed blank for them (2026-09-15)."""
    import mousedb.importers as imp
    import mousedb.animal_records as ar
    import mousedb.database as dbmod

    class Fake:
        db = None
        def import_cohort_file(self, *a, **k):
            return {"success": True, "imported": {}, "warnings": [], "errors": []}
    monkeypatch.setattr(imp, "ExcelImporter", lambda *a, **k: Fake())
    monkeypatch.setattr(cs, "available_aspa_cohorts", lambda *a, **k: ["K"])
    monkeypatch.setattr(cs, "find_aspa_sheet", lambda *a, **k: tmp_path / "K.xlsx")
    monkeypatch.setattr(dbmod, "get_db", lambda *a, **k: "db")
    calls = []
    monkeypatch.setattr(ar, "sync_cohort", lambda db, cid, sheet, kind, dry_run=False:
                        calls.append((cid, kind, dry_run)) or {"records": 5, "warnings": [], "error": None})
    r = ss.import_cohorts(None)
    assert calls == [("ASPA_11", "odc", False)]
    assert r["aspa_animal_records"][0]["records"] == 5
    assert ss.import_cohorts(["CNT_05"]).get("aspa_animal_records") is None   # only on a full import


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

    def test_a_real_import_takes_the_database_mutex_per_cohort(self, folder, monkeypatch, mutex_holds):
        """WHY: this import commits to connectome.db but did not take the mutex
        the reach import and the snapshot share, so on 2026-09-15 four of six
        cohorts failed "database is locked" while another task held the
        database. One acquisition per cohort (not around the whole loop), and
        none on a dry run, which touches nothing."""
        class Fake:
            def import_cohort_file(self, *a, **k):
                return {"success": True, "imported": {"subjects": 1}, "warnings": [], "errors": []}
        import mousedb.importers as imp
        monkeypatch.setattr(imp, "ExcelImporter", lambda *a, **k: Fake())
        ss.import_cohorts(["CNT_05"], dry_run=True)
        assert mutex_holds == []
        r = ss.import_cohorts(["CNT_05"], triggered_by="test")
        assert r["cohorts"][0]["success"]
        assert len(mutex_holds) == 1

    def test_a_mutex_timeout_fails_that_cohort_visibly(self, folder, monkeypatch):
        """A lock that never frees must not crash the run or pass silently: the
        cohort is recorded as failed with the reason, like any other failure."""
        from mousedb import task_mutex

        def never(*a, **k):
            raise RuntimeError("could not acquire the database-task lock after 1500s")

        monkeypatch.setattr(task_mutex, "hold", never)

        class Fake:
            def import_cohort_file(self, *a, **k):
                pytest.fail("imported without holding the database mutex")
        import mousedb.importers as imp
        monkeypatch.setattr(imp, "ExcelImporter", lambda *a, **k: Fake())
        r = ss.import_cohorts(["CNT_05"], triggered_by="test")
        c = r["cohorts"][0]
        assert c["success"] is False and "database-task lock" in c["error"]
        assert ss.cohort_status("05")["state"] == "last_import_failed"
