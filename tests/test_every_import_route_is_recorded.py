"""Every way of importing the tracking sheets is recorded, waits its turn, and
says when it failed.

THE DEFECT. There were several ways to import the sheets and they did not
behave alike. ``mousedb-sheets import`` (the scheduled job, the Tracking Sheets
tab) wrote each cohort's outcome and warnings to the import ledger, waited for
the cross-task database lock, and kept one cohort's failure from stopping the
rest. But ``mousedb import --all`` threw every cohort's errors and warnings
away, wrote no ledger line, never waited for the lock, and exited 0 whatever
happened -- and the GUI's "Update the database" button ran exactly that
command, so its sheets step showed OK over a failed import. ``mousedb import
--file`` went straight to the importer too. The button's brain step ran
``import-brains`` with no target, which only prints a usage line and fails.

Requests were mis-read as well: any ``--cohort X_NN`` was rewritten as
``CNT_NN`` from its last number, so asking for another project's cohort 01, or
for the animal CNT_05_01, imported CNT_01's sheet instead; a cohort with no
sheet was skipped without a word; ``ASPA_99`` imported cohort I's workbook under
the id ``ASPA_None``.

Once ``--file`` went through the recorded import it also REPLACED that
cohort's animal records from the named file -- but a file inside a snapshot or
archive folder still only drew a warning, so an old copy overwrote good
records. ``mousedb-sheets pin`` answered a wrong file with a Python traceback,
and like ``unpin`` and ``mousedb cohort-sheets --cohort`` it kept only a
request's trailing number (``pin CNTS_01`` pinned CNT_01's sheet). A pin whose
file had since vanished was still listed as "pinned" while the newest file was
imported, and ``set-dir`` recorded any folder at all, after which everything
said "no folder configured" -- and a folder typed relative to where the
command ran was recorded as typed, with the same result once a scheduled job
read it from elsewhere. A problem that stopped a whole import was printed
twice, word for word.

THE CONSEQUENCE. The Tracking Sheets tab and the health report read the
ledger, so an import made by the button or by hand was invisible to them; a
failing import looked green to every scheduler and dialog that reads the exit
code; an import that did not wait for the lock could collide with the reach
import ("database is locked"); and a mis-read request wrote, or claimed to
have written, the wrong cohort.

Every importer here is a test double: no workbook is parsed, and no database
-- least of all the lab's -- is opened.
"""
import contextlib
import json
import sys
from pathlib import Path

import pytest

import mousedb.cohort_sheets as cs
import mousedb.sheet_sync as ss
import mousedb.update as up
from mousedb.importers import ExcelImporter as RealExcelImporter


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mutex_holds(monkeypatch):
    """Record each acquisition of the cross-task database mutex instead of
    taking the PRODUCTION lock (it lives in the configured logs folder; a test
    contending with a real scheduled job would hang the suite)."""
    from mousedb import task_mutex
    seen = []

    def fake_hold(*a, **k):
        seen.append(k.get("waiting_for"))
        return contextlib.nullcontext()

    monkeypatch.setattr(task_mutex, "hold", fake_hold)
    return seen


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """A configured sheets folder with cohorts 01 and 05, a private config (no
    real pins, no ASPA folder), and a private ledger."""
    d = tmp_path / "sheets"
    d.mkdir()
    for name in ("Connectome_01_Animal_Tracking.xlsx", "Connectome_05_Animal_Tracking.xlsx"):
        (d / name).write_bytes(b"x")
    monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv(cs.ENV_VAR, str(d))
    monkeypatch.delenv(cs.ASPA_ENV_VAR, raising=False)
    monkeypatch.setattr(ss, "LEDGER", tmp_path / "ledger.jsonl")
    return d


class FakeImporter:
    """Stands in for ExcelImporter. Results are chosen per cohort number; every
    call is recorded. The cohort a file belongs to is decided by the REAL
    importer's own file-name rule, so these tests prove the ledger names the
    cohort the rows would actually be filed under."""
    calls = []
    results = {}

    _extract_cohort_from_filename = RealExcelImporter._extract_cohort_from_filename

    def import_cohort_file(self, path, dry_run=False):
        cohort = self._extract_cohort_from_filename(Path(path).name)
        FakeImporter.calls.append((cohort, Path(path).name, dry_run))
        r = FakeImporter.results.get(cohort)
        if isinstance(r, Exception):
            raise r
        return r or {"success": True, "imported": {"subjects": 1}, "warnings": [], "errors": []}


@pytest.fixture
def importer(monkeypatch):
    """Every route builds its importer through mousedb.importers.ExcelImporter."""
    import mousedb.importers as imp
    FakeImporter.calls = []
    FakeImporter.results = {}
    monkeypatch.setattr(imp, "ExcelImporter", lambda *a, **k: FakeImporter())
    return FakeImporter


@pytest.fixture
def no_database(monkeypatch):
    """`mousedb import` initialises the database first and runs a completeness
    report after. Neither may touch a real database here: initialising is a
    no-op and opening one fails (the report says it could not run)."""
    import mousedb.database as dbmod

    def refuse(*a, **k):
        raise RuntimeError("no database in tests")

    monkeypatch.setattr(dbmod, "init_database", lambda *a, **k: None)
    monkeypatch.setattr(dbmod, "get_db", refuse)


def _ledger():
    p = Path(ss.LEDGER)
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def _run_mousedb(monkeypatch, *argv):
    """Run the `mousedb` command line in-process; return its exit code."""
    from mousedb import cli
    monkeypatch.setattr(sys, "argv", ["mousedb"] + list(argv))
    try:
        cli.main()
    except SystemExit as e:
        return e.code or 0
    return 0


# ---------------------------------------------------------------------------
# the "Update the database" button
# ---------------------------------------------------------------------------

class TestTheUpdateButton:

    def test_its_sheet_step_is_the_recorded_import(self):
        """The button's sheet step must be the import that writes the ledger,
        takes the lock and fails loudly -- not `mousedb.cli import --all`."""
        key, _, cmd = up.STEPS[0]
        assert key == "sheets"
        assert cmd[1:] == ["-m", "mousedb.sheet_sync", "import", "--triggered-by", "update"]

    def test_its_sheet_step_arguments_reach_the_ledger(self, folder, importer):
        """Run the step's own arguments through the command it names: each
        cohort lands in the ledger marked as coming from the update."""
        argv = up.STEPS[0][2][3:]
        assert ss.main(argv) == 0
        assert {(e["cohort_id"], e["triggered_by"]) for e in _ledger()} == {
            ("CNT_01", "update"), ("CNT_05", "update")}

    def test_its_brain_step_names_what_to_import(self):
        """Without --all the brain import prints its usage line and exits 1, so
        the step failed on every press and never imported a brain."""
        cmd = dict((k, c) for k, _, c in up.STEPS)["brains"]
        assert cmd[-2:] == ["import-brains", "--all"]

    def test_its_sheet_step_shows_a_verdict_not_the_last_cohort(self, folder, importer, capsys):
        """The update dialog shows each step's LAST line as what it did."""
        importer.results["CNT_05"] = {"success": False, "imported": {"weights": 3},
                                      "warnings": [], "errors": ["database is locked"]}
        assert ss.main(up.STEPS[0][2][3:]) == 1
        last = [l for l in capsys.readouterr().out.splitlines() if l.strip()][-1]
        assert last.startswith("Sheets:") and "1 FAILED (CNT_05)" in last


# ---------------------------------------------------------------------------
# mousedb import --all
# ---------------------------------------------------------------------------

class TestImportAll:

    def test_each_cohort_is_recorded_under_the_mutex(self, folder, importer, no_database,
                                                     monkeypatch, mutex_holds):
        importer.results["CNT_05"] = {"success": True, "imported": {"subjects": 16},
                                      "warnings": ["row 7: weight blank"], "errors": []}
        assert _run_mousedb(monkeypatch, "import", "--all") == 0
        led = {e["cohort_id"]: e for e in _ledger()}
        assert set(led) == {"CNT_01", "CNT_05"}
        assert led["CNT_05"]["triggered_by"] == "mousedb-import"
        assert led["CNT_05"]["warnings"] == ["row 7: weight blank"]
        assert led["CNT_05"]["warning_count"] == 1
        assert len(mutex_holds) == 2                      # one per cohort

    def test_a_failed_cohort_fails_the_command_and_is_recorded(self, folder, importer,
                                                               no_database, monkeypatch, capsys):
        importer.results["CNT_05"] = {"success": False, "imported": {"weights": 273},
                                      "warnings": [],
                                      "errors": ["NOT NULL constraint failed: pellet_scores.x"]}
        assert _run_mousedb(monkeypatch, "import", "--all") == 1
        out = capsys.readouterr().out
        # The reason is printed, not the counts it would have written.
        assert "FAIL CNT_05" in out and "NOT NULL constraint failed" in out
        led = {e["cohort_id"]: e for e in _ledger()}
        assert led["CNT_05"]["success"] is False and "NOT NULL" in led["CNT_05"]["error"]
        assert led["CNT_01"]["success"] is True

    def test_one_cohort_crashing_does_not_stop_the_others(self, folder, importer,
                                                         no_database, monkeypatch):
        importer.results["CNT_01"] = RuntimeError("workbook exploded")
        assert _run_mousedb(monkeypatch, "import", "--all") == 1
        assert [c for c, _, _ in importer.calls] == ["CNT_01", "CNT_05"]
        led = {e["cohort_id"]: e for e in _ledger()}
        assert "workbook exploded" in led["CNT_01"]["error"] and led["CNT_05"]["success"]

    def test_a_dry_run_writes_nothing_and_waits_for_nothing(self, folder, importer,
                                                            no_database, monkeypatch, mutex_holds):
        assert _run_mousedb(monkeypatch, "import", "--all", "--dry-run") == 0
        assert all(dry for _, _, dry in importer.calls) and len(importer.calls) == 2
        assert _ledger() == [] and mutex_holds == []

    def test_one_sheet_per_cohort_not_every_matching_file(self, folder, importer,
                                                         no_database, monkeypatch):
        """This route used to import EVERY file whose name matched, so a
        cohort with a second workbook was imported twice from two files. Now
        it imports the one the Tracking Sheets tab shows: the pinned file, or
        the newest."""
        (folder / "Connectome_05_Animal_Tracking(other copy).xlsx").write_bytes(b"x")
        cs.pin_cohort_sheet("CNT_05", "Connectome_05_Animal_Tracking.xlsx")
        assert _run_mousedb(monkeypatch, "import", "--all") == 0
        assert sorted(n for _, n, _ in importer.calls) == [
            "Connectome_01_Animal_Tracking.xlsx", "Connectome_05_Animal_Tracking.xlsx"]

    def test_another_folder_can_be_named(self, folder, importer, no_database,
                                         monkeypatch, tmp_path):
        other = tmp_path / "elsewhere"
        other.mkdir()
        (other / "Connectome_03_Animal_Tracking.xlsx").write_bytes(b"x")
        assert _run_mousedb(monkeypatch, "import", "--all", "--directory", str(other)) == 0
        assert [c for c, _, _ in importer.calls] == ["CNT_03"]
        assert _ledger()[0]["sheet_path"] == str(other / "Connectome_03_Animal_Tracking.xlsx")

    def test_a_folder_named_relatively_is_recorded_with_its_full_path(
            self, folder, importer, no_database, monkeypatch, tmp_path):
        other = tmp_path / "elsewhere"
        other.mkdir()
        (other / "Connectome_03_Animal_Tracking.xlsx").write_bytes(b"x")
        monkeypatch.chdir(tmp_path)
        assert _run_mousedb(monkeypatch, "import", "--all", "--directory", "elsewhere") == 0
        assert _ledger()[0]["sheet_path"] == str(other / "Connectome_03_Animal_Tracking.xlsx")

    def test_a_named_folder_that_is_not_there_says_so(self, folder, importer, monkeypatch,
                                                      tmp_path, capsys):
        """It used to print only the CONFIGURED folder's description, which
        says nothing about the folder that was actually named."""
        import mousedb.database as dbmod
        opened = []
        monkeypatch.setattr(dbmod, "init_database", lambda *a, **k: opened.append(1))
        missing = tmp_path / "nowhere"
        assert _run_mousedb(monkeypatch, "import", "--all", "--directory", str(missing)) == 1
        assert "Not a folder: %s" % missing in capsys.readouterr().out
        assert importer.calls == [] and opened == [] and _ledger() == []

    def test_neither_all_nor_file_is_a_sentence_not_a_crash(self, monkeypatch, capsys):
        assert _run_mousedb(monkeypatch, "import") == 1
        assert "--all" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# mousedb import --file
# ---------------------------------------------------------------------------

class TestImportOneFile:

    def test_it_is_recorded_under_the_cohort_its_rows_land_in(self, folder, importer,
                                                              no_database, monkeypatch,
                                                              tmp_path, mutex_holds):
        f = tmp_path / "Connectome_05_Animal_Tracking.xlsx"
        f.write_bytes(b"x")
        assert _run_mousedb(monkeypatch, "import", "--file", str(f)) == 0
        (e,) = _ledger()
        assert e["cohort_id"] == "CNT_05" and e["sheet_path"] == str(f)
        assert e["triggered_by"] == "mousedb-import --file"
        assert len(mutex_holds) == 1

    def test_a_failure_exits_nonzero_and_is_recorded(self, folder, importer, no_database,
                                                     monkeypatch, tmp_path):
        f = tmp_path / "Connectome_05_Animal_Tracking.xlsx"
        f.write_bytes(b"x")
        importer.results["CNT_05"] = {"success": False, "imported": {}, "warnings": [],
                                      "errors": ["Failed to open Excel file: corrupt"]}
        assert _run_mousedb(monkeypatch, "import", "--file", str(f)) == 1
        assert _ledger()[-1]["success"] is False

    def test_a_file_that_names_no_cohort_is_refused_before_it_is_read(
            self, folder, importer, no_database, monkeypatch, tmp_path, capsys):
        f = tmp_path / "Pellet Summaries.xlsx"
        f.write_bytes(b"x")
        assert _run_mousedb(monkeypatch, "import", "--file", str(f)) == 1
        assert importer.calls == []
        assert "does not say which cohort" in capsys.readouterr().out
        assert _ledger() == []        # no cohort to file it under

    def test_a_dry_run_writes_no_ledger(self, folder, importer, no_database,
                                        monkeypatch, tmp_path, mutex_holds):
        f = tmp_path / "Connectome_05_Animal_Tracking.xlsx"
        f.write_bytes(b"x")
        assert _run_mousedb(monkeypatch, "import", "--file", str(f), "--dry-run") == 0
        assert importer.calls == [("CNT_05", f.name, True)]
        assert _ledger() == [] and mutex_holds == []

    def test_a_file_named_relatively_is_recorded_with_its_full_path(
            self, folder, importer, no_database, monkeypatch, tmp_path):
        """The ledger line says where the workbook was. A name typed relative
        to where the command ran used to be recorded as typed, which says
        nothing once read from anywhere else."""
        f = _workbook(tmp_path / "here" / "Connectome_05_Animal_Tracking.xlsx")
        monkeypatch.chdir(f.parent)
        assert _run_mousedb(monkeypatch, "import", "--file", f.name) == 0
        (e,) = _ledger()
        assert e["sheet_path"] == str(f)


# ---------------------------------------------------------------------------
# which cohort a request names
# ---------------------------------------------------------------------------

class TestARequestNamesOnlyItsOwnCohort:

    @pytest.mark.parametrize("asked", ["CNTS_01", "ENCR_01", "CNT_05_01", "J", "cohort one"])
    def test_a_request_is_never_rewritten_into_another_cohort(self, folder, importer, asked):
        """Each of these used to become CNT_01 (or crash): its last number was
        taken as a CNT cohort. None of them may import anything."""
        r = ss.import_cohorts([asked], triggered_by="test")
        assert importer.calls == []
        (c,) = r["cohorts"]
        assert c["success"] is False and "nothing was imported" in c["error"]
        assert not ss.import_succeeded(r)

    def test_another_projects_cohort_says_why(self, folder, importer):
        (c,) = ss.import_cohorts(["CNTS_01"])["cohorts"]
        assert c["cohort_id"] == "CNTS_01" and "another project's sheet" in c["error"]

    def test_a_cohort_with_no_sheet_is_a_failure_not_silence(self, folder, importer):
        (c,) = ss.import_cohorts(["CNT_09"])["cohorts"]
        assert c["cohort_id"] == "CNT_09" and c["success"] is False
        assert "cohorts with a sheet: CNT_01, CNT_05" in c["error"]
        assert importer.calls == []

    def test_a_request_that_matches_nothing_does_not_import_everything(self, folder, importer):
        """An empty filter must stay a filter: refusing the only cohort asked
        for leaves nothing selected, never "no filter, import all"."""
        ss.import_cohorts(["CNTS_01", "CNT_09"])
        assert importer.calls == []

    def test_unmatched_requests_are_not_written_to_the_ledger(self, folder, importer):
        """No sheet was read, so no cohort's import history changed."""
        ss.import_cohorts(["CNTS_01", "CNT_09"], triggered_by="test")
        assert _ledger() == []

    @pytest.mark.parametrize("asked", ["CNT_05", "cnt05", "CNT05", "05", "5"])
    def test_the_ways_of_naming_a_cohort_still_work(self, folder, importer, asked):
        r = ss.import_cohorts([asked])
        assert [c for c, _, _ in importer.calls] == ["CNT_05"]
        assert ss.import_succeeded(r)

    def test_an_aspa_id_that_names_no_letter_is_refused(self, folder, importer, monkeypatch):
        """'ASPA_99' fell back to its last character, which the sheet lookup
        read as cohort 9 (I): cohort I's workbook was imported as 'ASPA_None'."""
        import mousedb.cohort_tools.import_aspa_scores as ias
        called = []
        monkeypatch.setattr(ias, "import_cohort", lambda *a, **k: called.append(a) or {})
        (c,) = ss.import_cohorts(["ASPA_99"])["cohorts"]
        assert called == [] and c["success"] is False and "not an ASPA cohort id" in c["error"]

    @pytest.mark.parametrize("asked", ["ASPA_J", "aspa_j", "ASPAJ"])
    def test_an_aspa_cohort_named_by_its_letter_still_imports(self, folder, importer,
                                                              monkeypatch, asked):
        """The lab names these cohorts by letter, and the Tracking Sheets tab
        labels a letter cohort whose status lookup failed 'ASPA_J'. The old
        last-character fallback imported J for these; refusing a trailing DIGIT
        (ASPA_99 above) must not also refuse a trailing letter."""
        import mousedb.cohort_tools.import_aspa_scores as ias
        called = []
        monkeypatch.setattr(ias, "import_cohort", lambda letter, apply=False: called.append(letter) or {
            "cohort_id": "ASPA_10", "success": True, "warnings": []})
        (c,) = ss.import_cohorts([asked], dry_run=True)["cohorts"]
        assert called == ["J"] and c["success"] is True

    def test_an_aspa_import_waits_for_the_database_mutex(self, folder, importer,
                                                         monkeypatch, mutex_holds):
        import mousedb.cohort_tools.import_aspa_scores as ias
        monkeypatch.setattr(ias, "import_cohort", lambda letter, apply=False: {
            "cohort_id": "ASPA_10", "success": True, "warnings": ["w1", "w2"]})
        r = ss.import_cohorts(["ASPA_10"], triggered_by="test")
        assert len(mutex_holds) == 1
        assert r["cohorts"][0]["warning_count"] == 2

    def test_an_aspa_mutex_timeout_is_recorded_not_raised(self, folder, importer, monkeypatch):
        from mousedb import task_mutex

        def never(*a, **k):
            raise RuntimeError("could not acquire the database-task lock after 1500s")

        monkeypatch.setattr(task_mutex, "hold", never)
        (c,) = ss.import_cohorts(["ASPA_10"], triggered_by="test")["cohorts"]
        assert c["success"] is False and "database-task lock" in c["error"]
        assert _ledger()[-1]["cohort_id"] == "ASPA_10"


# ---------------------------------------------------------------------------
# what a person sees
# ---------------------------------------------------------------------------

class TestWarningsAreCountedAndShown:

    def test_each_cohort_says_how_many_warnings_it_raised(self, folder, importer, capsys):
        importer.results["CNT_05"] = {"success": True, "imported": {"subjects": 16},
                                      "warnings": ["a", "b", "c", "d", "e"], "errors": []}
        assert ss.main(["import"]) == 0
        out = capsys.readouterr().out.splitlines()
        line01 = [l for l in out if l.startswith("OK   CNT_01")][0]
        line05 = [l for l in out if l.startswith("OK   CNT_05")][0]
        assert line01.endswith("[0 warnings]") and line05.endswith("[5 warnings]")
        assert "       - a" in out and "       - d" not in out      # first few shown
        assert any("... and 2 more" in l for l in out)
        assert out[-1] == "Sheets: 2 of 2 cohorts imported, 0 failed, 5 warnings"

    def test_the_ledger_keeps_a_cut_list_but_the_whole_count(self, folder, importer):
        many = ["warning %d" % i for i in range(ss.LEDGER_WARNINGS_KEPT + 30)]
        importer.results["CNT_05"] = {"success": True, "imported": {}, "warnings": many,
                                      "errors": []}
        r = ss.import_cohorts(["CNT_05"], triggered_by="test")
        assert r["cohorts"][0]["warnings"] == many           # the result keeps them all
        (e,) = _ledger()
        assert e["warning_count"] == len(many)
        assert len(e["warnings"]) == ss.LEDGER_WARNINGS_KEPT + 1
        assert "30 more warnings" in e["warnings"][-1]

    def test_what_is_printed_is_plain_ascii(self):
        """A cell's text can carry characters a Windows console cannot encode;
        printing one would crash the command after the import had landed."""
        lines = []
        ss.print_import_result({"dry_run": False, "cohorts": [{
            "cohort_id": "CNT_05", "sheet_name": "s.xlsx", "success": True,
            "imported": {"subjects": 1}, "warnings": ["dose 5 \u00b5L \u2192 rejected"]}]},
            log=lines.append)
        assert all(l.isascii() for l in lines)
        assert any("\\u2192" in l for l in lines)            # escaped, not dropped


class TestNothingImportedIsAFailure:

    def test_an_unconfigured_folder_exits_nonzero(self, tmp_path, monkeypatch, capsys):
        """Zero cohorts, zero failures used to read as success: the scheduled
        job and the update button went green while no sheet was read."""
        monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.delenv(cs.ENV_VAR, raising=False)
        monkeypatch.setattr(ss, "LEDGER", tmp_path / "ledger.jsonl")
        assert ss.main(["import"]) == 1
        assert "NOTHING IMPORTED" in capsys.readouterr().out

    def test_the_problem_is_said_once_in_the_closing_line(self):
        """It used to be printed on a '[!]' line and then again, word for
        word, in the closing line. The closing line is the one the update
        dialog shows, so that is where it stays."""
        lines = []
        ok = ss.print_import_result({"cohorts": [], "problem": "no folder here"},
                                    log=lines.append)
        assert not ok
        assert [l for l in lines if "no folder here" in l] == [lines[-1]]
        assert lines[-1].startswith("Sheets: NOTHING IMPORTED")

    def test_a_snapshot_folder_is_refused_on_every_route(self, tmp_path, monkeypatch, importer):
        d = tmp_path / "_archive" / "old_sheets"
        d.mkdir(parents=True)
        (d / "Connectome_05_Animal_Tracking.xlsx").write_bytes(b"x")
        monkeypatch.setattr(cs, "CONFIG_PATH", tmp_path / "config.json")
        monkeypatch.setattr(ss, "LEDGER", tmp_path / "ledger.jsonl")
        r = ss.import_cohorts(None, sheets_dir=d)
        assert "snapshot or archive" in r["problem"] and importer.calls == []
        assert not ss.import_succeeded(r)


# ---------------------------------------------------------------------------
# mousedb import --file: a file the folder route would never read is refused
# ---------------------------------------------------------------------------

@pytest.fixture
def database_opened(monkeypatch):
    """Like no_database, but records every attempt to initialise the database,
    so a test can prove a refused import never got that far."""
    import mousedb.database as dbmod
    calls = []

    def refuse(*a, **k):
        raise RuntimeError("no database in tests")

    monkeypatch.setattr(dbmod, "init_database", lambda *a, **k: calls.append("init"))
    monkeypatch.setattr(dbmod, "get_db", refuse)
    return calls


def _workbook(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


class TestAStaleOrWrongFileIsRefused:

    @pytest.mark.parametrize("snapshot", ["Archive", "_archive/old_cohort_scripts",
                                          "generated", "backup"])
    def test_a_copy_in_a_snapshot_folder_is_refused_before_anything_is_opened(
            self, folder, importer, database_opened, monkeypatch, tmp_path, capsys, snapshot):
        """It used to WARN and import anyway -- and this route replaces the
        cohort's animal records, so the old copy overwrote the good ones."""
        f = _workbook(tmp_path / snapshot / "Connectome_05_Animal_Tracking.xlsx")
        assert _run_mousedb(monkeypatch, "import", "--file", str(f)) == 1
        out = capsys.readouterr().out
        assert importer.calls == [] and _ledger() == []
        assert database_opened == []                  # refused before the database
        assert "REFUSED" in out and "'%s'" % snapshot.split("/")[0] in out
        # What to run instead names the live sheet the folder holds.
        assert "mousedb-sheets import --cohort CNT_05" in out
        assert "Connectome_05_Animal_Tracking.xlsx, from the sheets folder" in out

    def test_a_bare_file_name_typed_inside_a_snapshot_folder_is_refused(
            self, folder, importer, database_opened, monkeypatch, tmp_path):
        """A path is judged by its folder names, and a bare name has none of
        its own: standing inside the archive folder used to slip past."""
        d = tmp_path / "archive"
        _workbook(d / "Connectome_05_Animal_Tracking.xlsx")
        monkeypatch.chdir(d)
        assert _run_mousedb(monkeypatch, "import", "--file",
                            "Connectome_05_Animal_Tracking.xlsx") == 1
        assert importer.calls == [] and database_opened == []

    def test_a_path_that_climbs_out_of_an_archive_folder_is_not_refused(
            self, folder, importer, database_opened, monkeypatch, tmp_path):
        (tmp_path / "archive").mkdir()
        _workbook(tmp_path / "live" / "Connectome_05_Animal_Tracking.xlsx")
        f = tmp_path / "archive" / ".." / "live" / "Connectome_05_Animal_Tracking.xlsx"
        assert _run_mousedb(monkeypatch, "import", "--file", str(f)) == 0
        assert [c for c, _, _ in importer.calls] == ["CNT_05"]

    @pytest.mark.parametrize("name", [
        "Copy of Connectome_05_Animal_Tracking.xlsx",
        "Connectome_05_Animal_Tracking_ODC.xlsx",
        "Connectome_05_Animal_Tracking1_summary_matrix.xlsx",
        "~$Connectome_05_Animal_Tracking.xlsx",
    ])
    def test_a_name_the_sheet_rule_refuses_is_not_imported(
            self, folder, importer, database_opened, monkeypatch, tmp_path, capsys, name):
        """The importer's own file-name rule finds cohort 05 in every one of
        these, so naming one used to import it -- and an export carries none
        of the sheet's animal tabs, so it would have emptied CNT_05's animal
        records."""
        f = _workbook(tmp_path / "elsewhere" / name)
        assert _run_mousedb(monkeypatch, "import", "--file", str(f)) == 1
        assert importer.calls == [] and _ledger() == [] and database_opened == []
        assert "not read as a tracking sheet" in capsys.readouterr().out

    def test_the_refusal_is_plain_ascii(self, folder, importer, database_opened,
                                        monkeypatch, tmp_path, capsys):
        f = _workbook(tmp_path / "Kopie \u00e4" / "archive" / "Connectome_05_Animal_Tracking.xlsx")
        assert _run_mousedb(monkeypatch, "import", "--file", str(f)) == 1
        out = capsys.readouterr().out
        assert out.isascii() and "\\xe4" in out          # escaped, not dropped

    def test_the_import_function_itself_refuses_for_every_caller(self, folder, importer,
                                                                 tmp_path):
        f = _workbook(tmp_path / "_archive" / "Connectome_05_Animal_Tracking.xlsx")
        r = ss.import_sheet_file(f, triggered_by="test")
        assert r["problem"].startswith("REFUSED") and r["cohorts"] == []
        assert not ss.import_succeeded(r)
        assert importer.calls == [] and _ledger() == []

    def test_the_live_sheet_named_directly_still_imports(self, folder, importer,
                                                         database_opened, monkeypatch):
        assert _run_mousedb(monkeypatch, "import", "--file",
                            str(folder / "Connectome_05_Animal_Tracking.xlsx")) == 0
        assert [c for c, _, _ in importer.calls] == ["CNT_05"]
        assert database_opened == ["init"]

    def test_a_snapshot_folder_named_relatively_is_refused_too(
            self, folder, importer, database_opened, monkeypatch, tmp_path, capsys):
        """--all --directory judged the folder by the text typed, so a relative
        name given inside an archive folder was read as live."""
        d = tmp_path / "_archive"
        _workbook(d / "sheets" / "Connectome_03_Animal_Tracking.xlsx")
        monkeypatch.chdir(d)
        assert _run_mousedb(monkeypatch, "import", "--all", "--directory", "sheets") == 1
        assert importer.calls == [] and database_opened == []
        assert "REFUSED" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# mousedb-sheets pin / unpin / set-dir, and mousedb cohort-sheets --cohort
# ---------------------------------------------------------------------------

def _lines(text):
    return [l for l in text.splitlines() if l.strip()]


class TestPinningSaysWhyInOneLine:

    @pytest.mark.parametrize("name", [
        "Copy of Connectome_05_Animal_Tracking.xlsx",      # in the folder, refused by name
        "Connectome_01_Animal_Tracking.xlsx",              # another cohort's sheet
        "Connectome_05_Animal_Tracking(2).xlsx",           # right shape, not there
        "Connectome_05_Animal_Tracking_ODC.xlsx",          # a tool's export
    ])
    def test_a_file_that_is_not_the_cohorts_sheet_is_refused_in_one_line(
            self, folder, capsys, name):
        """The guard in cohort_sheets raises ValueError for these; the command
        used to let it out as a traceback."""
        (folder / "Copy of Connectome_05_Animal_Tracking.xlsx").write_bytes(b"x")
        assert ss.main(["pin", "CNT_05", name]) == 1
        cap = capsys.readouterr()
        (line,) = _lines(cap.out)
        assert line.startswith("[FAIL] cannot pin") and line.endswith("nothing was pinned")
        assert "Traceback" not in cap.out + cap.err
        assert cs.pinned_sheet("CNT_05") is None

    def test_the_guards_own_valueerror_is_caught_too(self, folder, capsys, monkeypatch):
        def guard(cohort, filename):
            raise ValueError("cannot pin %r as the sheet for cohort 05: some reason" % filename)

        monkeypatch.setattr(ss, "pin_cohort_sheet", guard)
        assert ss.main(["pin", "CNT_05", "Connectome_05_Animal_Tracking.xlsx"]) == 1
        (line,) = _lines(capsys.readouterr().out)
        assert "some reason" in line and line.startswith("[FAIL]")

    @pytest.mark.parametrize("asked", ["CNTS_05", "CNT_05_01", "J"])
    def test_a_request_is_never_rewritten_into_a_cnt_cohort(self, folder, capsys, asked):
        """Only the trailing number used to be kept: 'pin CNTS_05 <file>'
        pinned CNT_05's sheet."""
        assert ss.main(["pin", asked, "Connectome_05_Animal_Tracking.xlsx"]) == 1
        assert len(_lines(capsys.readouterr().out)) == 1
        assert cs.pinned_sheet("CNT_05") is None

    def test_unpinning_another_projects_id_leaves_the_real_pin(self, folder):
        cs.pin_cohort_sheet("CNT_05", "Connectome_05_Animal_Tracking.xlsx")
        assert ss.main(["unpin", "CNTS_05"]) == 1
        assert cs.pinned_sheet("CNT_05") == "Connectome_05_Animal_Tracking.xlsx"

    def test_a_real_file_is_pinned_and_unpinned(self, folder, capsys):
        assert ss.main(["pin", "5", "Connectome_05_Animal_Tracking.xlsx"]) == 0
        assert cs.pinned_sheet("CNT_05") == "Connectome_05_Animal_Tracking.xlsx"
        assert "pinned CNT_05 -> Connectome_05_Animal_Tracking.xlsx" in capsys.readouterr().out
        assert ss.main(["unpin", "CNT_05"]) == 0
        assert cs.pinned_sheet("CNT_05") is None

    def test_a_pin_whose_file_is_gone_is_announced_once_not_trusted(self, folder, capsys):
        """find_cohort_sheet ignores a pin that names no file and takes the
        newest; the status listing used to go on saying 'pinned' and 'not
        ambiguous'. Now the row shows the open question, and the broken pin
        is named once, on one line under that row."""
        gone = folder / "Connectome_05_Animal_Tracking(2).xlsx"
        gone.write_bytes(b"x")
        (folder / "Connectome_05_Animal_Tracking(3).xlsx").write_bytes(b"x")
        cs.pin_cohort_sheet("CNT_05", gone.name)
        gone.unlink()
        c = ss.cohort_status("05")
        assert c["pinned"] is None and c["ambiguous"]
        assert gone.name in c["broken_pin"]
        ss._print_status(ss.status())
        out = capsys.readouterr().out
        assert len([l for l in out.splitlines() if gone.name in l]) == 1


class TestTheSheetsFolderSetting:

    @staticmethod
    def _recorded():
        p = Path(cs.CONFIG_PATH)
        if not p.exists():
            return None
        return json.loads(p.read_text(encoding="utf-8")).get("cohort_sheets_dir")

    def test_a_folder_that_is_not_there_is_refused(self, folder, tmp_path, capsys):
        assert ss.main(["set-dir", str(tmp_path / "nowhere")]) == 1
        assert "[FAIL]" in capsys.readouterr().out and self._recorded() is None

    def test_a_folder_with_no_sheet_is_refused_and_its_refused_files_listed(
            self, folder, tmp_path, capsys):
        d = tmp_path / "copies"
        _workbook(d / "Copy of Connectome_05_Animal_Tracking.xlsx")
        assert ss.main(["set-dir", str(d)]) == 1
        out = capsys.readouterr().out
        assert "holds no tracking sheet" in out and "Copy of Connectome_05" in out
        assert self._recorded() is None

    def test_a_snapshot_folder_is_refused(self, folder, tmp_path):
        d = tmp_path / "Archive"
        _workbook(d / "Connectome_05_Animal_Tracking.xlsx")
        assert ss.main(["set-dir", str(d)]) == 1
        assert self._recorded() is None

    def test_the_live_folder_is_recorded(self, folder, tmp_path):
        d = tmp_path / "live"
        _workbook(d / "Connectome_05_Animal_Tracking.xlsx")
        assert ss.main(["set-dir", str(d)]) == 0
        assert self._recorded() == str(d)

    @pytest.mark.parametrize("route", ["set-dir", "cohort-sheets --set"])
    def test_a_folder_typed_relatively_is_recorded_in_full(self, folder, tmp_path,
                                                           monkeypatch, route):
        """A relative name passed every test where it was typed, was saved as
        typed, and the scheduled job and the GUI -- run from elsewhere -- then
        found no such folder and said 'not configured'."""
        d = tmp_path / "live"
        _workbook(d / "Connectome_05_Animal_Tracking.xlsx")
        monkeypatch.chdir(tmp_path)
        if route == "set-dir":
            assert ss.main(["set-dir", "live"]) == 0
        else:
            assert _run_mousedb(monkeypatch, "cohort-sheets", "--set", "live") == 0
        assert self._recorded() == str(d)


class TestTheStatusListing:

    def test_every_line_is_plain_ascii_the_folder_line_included(self, capsys):
        """The folder line and the problem line were printed raw: one
        non-ASCII letter in the synced folder's path crashed the listing on a
        Windows console before a single row was shown."""
        ss._print_status({"cnt_sheets_dir": "C:\\Labor \u00e4\\sheets",
                          "problem": "nothing in C:\\Labor \u00e4\\sheets", "cohorts": []})
        out = capsys.readouterr().out
        assert out.isascii() and out.count("\\xe4") == 2


class TestShowingOneCohortsSheet:

    def test_another_projects_cohort_is_not_shown_as_cnt(self, folder, monkeypatch, capsys):
        """`mousedb cohort-sheets --cohort CNTS_01` used to print CNT_01's sheet
        under the name CNTS_01."""
        assert _run_mousedb(monkeypatch, "cohort-sheets", "--cohort", "CNTS_01") == 1
        out = capsys.readouterr().out
        assert "another project's sheet" in out
        assert not any(l.startswith(("CNTS_01: ", "CNT_01: ")) for l in out.splitlines())

    def test_a_cohort_number_shows_that_cohorts_sheet(self, folder, monkeypatch, capsys):
        assert _run_mousedb(monkeypatch, "cohort-sheets", "--cohort", "5") == 0
        assert ("CNT_05: " + str(folder / "Connectome_05_Animal_Tracking.xlsx")
                in capsys.readouterr().out)
