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
        # The import history's location is configuration too; with none, the
        # listing still has to say what is missing rather than stop.
        monkeypatch.setattr(ss, "LEDGER", tmp_path / "ledger.jsonl")
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


@pytest.fixture
def invented(tmp_path, monkeypatch):
    """A sheets folder holding one invented cohort's sheet (CNT_77)."""
    d = tmp_path / "sheets77"
    d.mkdir()
    (d / "Connectome_77_Animal_Tracking.xlsx").write_bytes(b"x")
    monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv(cs.ENV_VAR, str(d))
    monkeypatch.setattr(ss, "LEDGER", tmp_path / "ledger.jsonl")
    return d


def test_a_refused_table_keeps_its_tabs_animal_details_and_says_so(invented, monkeypatch):
    """WHY: the importer refused to sync the surgeries (the sheet read as
    broken), yet the animal details from the same surgery tabs were replaced
    from that very read. They now stay as they were, reported with the
    refusals."""
    import mousedb.animal_records as ar
    import mousedb.importers as imp

    class Refuses:
        db = object()

        def import_cohort_file(self, *a, **k):
            return {"success": True, "imported": {}, "warnings": [], "errors": [],
                    "sheet_sync": {"refused": ["NOT SYNCED surgeries: ..."],
                                   "refused_tables": ["surgeries"]}}

    asked = []

    def fake_sync(db, cid, sheet, kind, dry_run=False, allow_large_removal=False,
                  keep_tabs=None):
        asked.append(dict(keep_tabs or {}))
        return {"records": 3, "warnings": [], "error": None,
                "kept": [{"tab": t, "why": w} for t, w in (keep_tabs or {}).items()]}

    monkeypatch.setattr(imp, "ExcelImporter", lambda *a, **k: Refuses())
    monkeypatch.setattr(ar, "sync_cohort", fake_sync)
    r = ss.import_cohorts(["CNT_77"])
    assert set(asked[0]) == {"4_Contusion_Injury_Details", "5_SC_Injection_Details"}
    refusals = ss.sync_refused(r["cohorts"][0])
    assert any("animal details '4_Contusion_Injury_Details'" in x for x in refusals)
    assert any("animal details '5_SC_Injection_Details'" in x for x in refusals)
    assert not ss.import_succeeded(r)


def test_several_files_and_no_pin_import_add_only_until_pinned(invented, monkeypatch):
    """WHY: the newest of several matching files was imported, and the import
    then changed and removed rows to match it -- on a guess."""
    import os, time
    import mousedb.animal_records as ar
    import mousedb.importers as imp
    rival = invented / "Connectome_77_Animal_Tracking (2).xlsx"
    rival.write_bytes(b"y")
    later = time.time() + 3600
    os.utime(rival, (later, later))
    built = []

    class Records:
        db = object()
        sync_blocked = None

        def import_cohort_file(self, *a, **k):
            built.append(self)
            return {"success": True, "imported": {}, "warnings": [], "errors": []}

    copied = []
    monkeypatch.setattr(imp, "ExcelImporter", lambda *a, **k: Records())
    monkeypatch.setattr(ar, "sync_cohort", lambda *a, **k: copied.append(a) or {
        "records": 1, "warnings": [], "error": None, "kept": []})

    (c,) = ss.import_cohorts(["CNT_77"])["cohorts"]
    assert built[-1].sync_blocked and "2 files match" in built[-1].sync_blocked
    assert copied == []                                   # animal details left alone
    assert "pin" in c[ss.LEFT_UNCHANGED]
    # Not counted as the cohort's scores import: the file was a guess, and the
    # reaching pipeline would file a video whose session it lacks without the
    # comparison. Its ledger line says so and the hand-scores file does not
    # move the cohort's last scores import.
    from mousedb import hand_scores_file as hsf
    assert c["scores_saved"] is False
    assert ss._read_ledger()[-1]["scores_saved"] is False
    assert "CNT_77" not in hsf._last_successful_imports()

    cs.pin_cohort_sheet("CNT_77", rival.name)
    (c,) = ss.import_cohorts(["CNT_77"])["cohorts"]
    assert built[-1].sync_blocked is None and len(copied) == 1
    assert c["scores_saved"] is True and "CNT_77" in hsf._last_successful_imports()


def test_a_tab_not_read_this_time_fails_the_import_and_says_so(invented, monkeypatch, tmp_path):
    """WHY: a tab renamed or cut from the workbook kept its stored animal
    details in silence -- the import said OK and the old values stayed in the
    exports for good. The kept tab is now a refusal, and the import fails."""
    import openpyxl
    import mousedb.importers as imp
    from mousedb.database import Database

    sheet = invented / "Connectome_77_Animal_Tracking.xlsx"

    def workbook(with_metadata):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "0a_Metadata" if with_metadata else "0a_Metadata (old)"
        ws.append(["SubjectID", "Sex"])
        ws.append(["CNT_77_01", "F"])
        wb.save(sheet)

    db = Database(tmp_path / "t.db")

    class Saves:
        def __init__(self, *a, **k):
            self.db = db

        def import_cohort_file(self, *a, **k):
            return {"success": True, "imported": {}, "warnings": [], "errors": []}

    monkeypatch.setattr(imp, "ExcelImporter", Saves)
    workbook(True)
    assert ss.import_succeeded(ss.import_cohorts(["CNT_77"]))
    workbook(False)
    r = ss.import_cohorts(["CNT_77"])
    (refusal,) = ss.sync_refused(r["cohorts"][0])
    assert "animal details '0a_Metadata'" in refusal and "not read" in refusal
    assert not ss.import_succeeded(r)


def test_a_cohort_read_on_a_guess_is_told_to_pin_not_to_look_for_a_broken_sheet():
    lines = []
    ss.print_import_result({"cohorts": [{
        "cohort_id": "CNT_77", "sheet_name": "Connectome_77_Animal_Tracking (2).xlsx",
        "success": True, "imported": {}, "warnings": [], "sync_blocked": "2 files match",
        "sheet_sync": {"refused": ["NOT SYNCED any table: 2 files match"]}}]},
        log=lines.append)
    assert "pin one" in lines[-1] and "read as broken" not in lines[-1]


def test_a_letter_cohorts_kept_odc_tab_fails_the_full_import(invented, monkeypatch, tmp_path):
    """WHY: the full import's pass over the letter cohorts' ODC tabs ignored a
    tab kept as it was, so it printed OK and the run succeeded."""
    import mousedb.animal_records as ar
    import mousedb.database as dbmod
    import mousedb.importers as imp

    class Fake:
        db = None

        def import_cohort_file(self, *a, **k):
            return {"success": True, "imported": {}, "warnings": [], "errors": []}
    monkeypatch.setattr(imp, "ExcelImporter", lambda *a, **k: Fake())
    monkeypatch.setattr(cs, "available_aspa_cohorts", lambda *a, **k: ["Z"])
    monkeypatch.setattr(cs, "find_aspa_sheet", lambda *a, **k: tmp_path / "Z.xlsx")
    monkeypatch.setattr(dbmod, "get_db", lambda *a, **k: "db")
    monkeypatch.setattr(ar, "sync_cohort", lambda *a, **k: {
        "records": 0, "warnings": [], "error": None,
        "kept": [{"tab": "ODC", "why": "the 'ODC' tab read as holding no animal's details"}]})
    r = ss.import_cohorts(None)
    lines = []
    assert ss.print_import_result(r, log=lines.append) is False
    assert any("[!] NOT SYNCED animal details 'ODC'" in x for x in lines)
    assert "1 NOT SYNCED (ASPA_26)" in lines[-1]


def test_the_letter_cohort_score_import_reports_a_kept_odc_tab(monkeypatch, tmp_path, capsys):
    import mousedb.animal_records as ar
    import mousedb.cohort_sheets as sheets
    import mousedb.database as dbmod
    from mousedb.cohort_tools import import_aspa_scores as ias

    book = tmp_path / "Z.xlsx"
    book.write_bytes(b"x")
    monkeypatch.setattr(sheets, "find_aspa_sheet", lambda *a, **k: book)
    monkeypatch.setattr(sheets, "aspa_data_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(dbmod, "DEFAULT_LOG_PATH", None)   # never the real log folder
    scratch = dbmod.init_database(tmp_path / "t.db")
    monkeypatch.setattr(dbmod, "init_database", lambda *a, **k: scratch)
    monkeypatch.setattr(ias, "parse_workbook", lambda *a, **k: [])
    monkeypatch.setattr(ar, "sync_cohort", lambda *a, **k: {
        "records": 0, "warnings": [], "error": None,
        "kept": [{"tab": "ODC", "why": "the 'ODC' tab read as holding no animal's details"}]})
    e = ias.import_cohort("Z", apply=False)
    assert any("animal details 'ODC'" in x for x in ss.sync_refused(e))
    assert ias.main(["--cohort", "Z"]) == 1
    assert "[!] NOT SYNCED animal details 'ODC'" in capsys.readouterr().out


def test_the_and_n_more_line_counts_only_refusals_that_are_warnings():
    """WHY: a kept animal-details tab is listed with the refusals without
    being a warning; subtracting it from the warning count hid one warning
    per kept tab from the "... and N more" line (or dropped the line)."""
    lines = []
    ss.print_import_result({"cohorts": [{
        "cohort_id": "CNT_77", "sheet_name": "Connectome_77_Animal_Tracking.xlsx",
        "success": False, "error": "a tab was kept", "imported": {},
        "warnings": ["NOT SYNCED weights: read as broken", "w1", "w2", "w3"],
        "sheet_sync": {"refused": [
            "NOT SYNCED weights: read as broken",
            "NOT SYNCED animal details '0a_Metadata': not read"]}}]},
        show_warnings=1, log=lines.append)
    more = [x for x in lines if "more (all of them" in x]
    # 4 warnings: one printed as a refusal, one shown, two hidden.
    assert len(more) == 1 and "and 2 more" in more[0]

    lines = []
    ss.print_import_result({"cohorts": [{
        "cohort_id": "CNT_77", "sheet_name": "Connectome_77_Animal_Tracking.xlsx",
        "success": False, "error": "a tab was kept", "imported": {},
        "warnings": ["w1", "w2"],
        "sheet_sync": {"refused": [
            "NOT SYNCED animal details '0a_Metadata': not read"]}}]},
        show_warnings=1, log=lines.append)
    assert any("and 1 more" in x for x in lines)
