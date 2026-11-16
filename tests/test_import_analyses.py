"""mousedb import-analyses -- mirroring MouseBrain's analysis registry.

WHY: the registry is MouseBrain's own record of what it produced and with
which method; mousedb only mirrors it. These tests pin the contract that
makes the mirror trustworthy: a first run copies everything and writes the
ledger + manifest with the right provenance counts, a second run copies
nothing, a file that disappears upstream is archived (never deleted), a
pre-existing identical destination is left alone, and --dry-run writes
nothing at all. Also: `mousedb import-brains` resolves its default folder
through config (a ConfigError with the command to run, not a NameError),
and eLife group counts are read from MouseBrain's own elife_counts.csv when
mousebrain is not importable -- with an explicit error when nothing is.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import pytest

from mousedb import import_analyses as ia

APPROVED = "hash-approved"


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return path


def _entry(analysis: str, sample: str, current: bool, method_hash: str) -> dict:
    e = {
        "sample": sample, "animal": "A1", "region": "R1", "category": "detection",
        "results": {"n_nuclei": 3, "n_positive": 1},
        "method_params": {"detection": "threshold"},
        "method_hash": method_hash,
        "source_files": {"nd2": "X:\\somewhere\\%s.nd2" % sample},
        "outputs": {"measurements": "exports\\%s\\%s\\measurements.csv" % (analysis, sample),
                    "figure": "figures\\%s\\A1\\R1\\%s.png" % (analysis, sample)},
        "registered_at": "2026-01-01T00:00:00", "hostname": "somehost", "is_current": current,
    }
    if not current:
        e["invalidated_at"] = "2026-02-01T00:00:00"
    return e


def _make_analysis(reg_root: Path, analysis: str, stale: bool) -> None:
    """Two entries: one current (stale or not), one invalidated. Four files:
    registry.json, a measurements csv, a figure, the log."""
    reg = {
        "analysis_name": analysis, "version": 1,
        "approved_method": {"detection": "threshold"}, "approved_method_hash": APPROVED,
        "entries": {
            "S1": _entry(analysis, "S1", True, "hash-old" if stale else APPROVED),
            "S2": _entry(analysis, "S2", False, APPROVED),
        },
        "last_updated": "2026-02-01T00:00:00",
    }
    _write(reg_root / "exports" / analysis / "registry.json", json.dumps(reg, indent=1))
    _write(reg_root / "exports" / analysis / "S1" / "measurements.csv", "id,area\n1,10\n")
    _write(reg_root / "figures" / analysis / "A1" / "R1" / "S1.png", b"\x89PNG fake")
    _write(reg_root / "logs" / ("%s.log" % analysis), "2026-01-01\tsomehost\tregister\tS1\n")


FILES_PER_ANALYSIS = 4


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """A fake MouseBrain registry (two analyses) and an empty mousedb root,
    wired through the environment: mousedb.config reads env first. The
    importer calls config.require() at call time, so the package-level
    constants captured at import are irrelevant here."""
    pipeline = tmp_path / "pipeline"
    reg = pipeline / "Registry"
    _make_analysis(reg, "Alpha_Detection", stale=True)
    _make_analysis(reg, "Beta_ROI", stale=False)
    db = tmp_path / "db"
    monkeypatch.setenv("MOUSEDB_ROOT", str(db))
    monkeypatch.setenv("MOUSEBRAIN_PIPELINE_ROOT", str(pipeline))
    return reg, db


def _quiet(*_):
    pass


class TestMirror:
    def test_first_run_mirrors_everything_and_writes_ledger_and_manifest(self, roots):
        reg, db = roots
        res = ia.run(log=_quiet)
        assert res.analyses == 2
        assert res.scanned == 2 * FILES_PER_ANALYSIS
        assert res.copied == 2 * FILES_PER_ANALYSIS and res.skipped == 0
        assert res.archived == 0 and res.errors == 0
        # same relative paths, same modification times
        for rel in ("exports/Alpha_Detection/registry.json",
                    "exports/Alpha_Detection/S1/measurements.csv",
                    "figures/Beta_ROI/A1/R1/S1.png", "logs/Beta_ROI.log"):
            src, dst = reg / rel, db / rel
            assert dst.is_file(), rel
            assert dst.read_bytes() == src.read_bytes()
            assert int(dst.stat().st_mtime) == int(src.stat().st_mtime), rel
        ledger = json.loads((db / "logs" / "analysis_imports.json").read_text())
        assert len(ledger) == 2 * FILES_PER_ANALYSIS
        assert all("/" in k and "\\" not in k for k in ledger)          # forward-slash keys
        assert all(set(v) == {"hash", "stamp"} for v in ledger.values())
        m = json.loads((db / "exports" / "ANALYSES_MANIFEST.json").read_text())
        rows = {r["analysis_name"]: r for r in m["analyses"]}
        assert set(rows) == {"Alpha_Detection", "Beta_ROI"}
        for name, stale in (("Alpha_Detection", 1), ("Beta_ROI", 0)):
            r = rows[name]
            assert r["entries"] == 2 and r["current"] == 1 and r["invalidated"] == 1
            assert r["stale_vs_approved"] == stale
            assert r["approved_method_hash"] == APPROVED
            assert r["last_updated"] == "2026-02-01T00:00:00"
            assert r["files_copied"] == FILES_PER_ANALYSIS
            assert r["files_skipped"] == 0 and r["files_archived"] == 0
            assert r["imported_at"] and r["source_root"] == str(reg)
            for k in ia.MANIFEST_FIELDS:
                assert k in r, k
        assert res.manifest == m["analyses"]

    def test_second_run_skips_everything(self, roots):
        reg, db = roots
        ia.run(log=_quiet)
        res = ia.run(log=_quiet)
        assert res.copied == 0 and res.archived == 0 and res.errors == 0
        assert res.skipped == 2 * FILES_PER_ANALYSIS
        m = json.loads((db / "exports" / "ANALYSES_MANIFEST.json").read_text())
        for r in m["analyses"]:
            assert r["files_copied"] == 0 and r["files_skipped"] == FILES_PER_ANALYSIS

    def test_a_touched_but_unchanged_source_is_confirmed_by_hash_and_skipped(self, roots):
        reg, db = roots
        ia.run(log=_quiet)
        f = reg / "logs" / "Alpha_Detection.log"
        os.utime(f, (time.time() + 100, time.time() + 100))   # new mtime, same content
        res = ia.run(log=_quiet)
        assert res.copied == 0 and res.skipped == 2 * FILES_PER_ANALYSIS
        ledger = json.loads((db / "logs" / "analysis_imports.json").read_text())
        st = f.stat()
        assert ledger["logs/Alpha_Detection.log"]["stamp"] == "%d:%d" % (st.st_size, int(st.st_mtime))

    def test_a_changed_source_is_copied_again(self, roots):
        reg, db = roots
        ia.run(log=_quiet)
        f = reg / "exports" / "Beta_ROI" / "S1" / "measurements.csv"
        f.write_text("id,area\n1,10\n2,20\n", encoding="utf-8")
        os.utime(f, (time.time() + 100, time.time() + 100))
        res = ia.run(log=_quiet)
        assert res.copied == 1 and res.skipped == 2 * FILES_PER_ANALYSIS - 1
        assert (db / "exports" / "Beta_ROI" / "S1" / "measurements.csv").read_text() == f.read_text()

    def test_a_pre_existing_identical_destination_is_skipped_on_the_first_run(self, roots):
        reg, db = roots
        # the tree is already there (an earlier copy), with the same mtimes: nothing to copy
        import shutil
        for rel in ("exports/Alpha_Detection/S1/measurements.csv", "logs/Alpha_Detection.log"):
            (db / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(reg / rel), str(db / rel))
        res = ia.run(log=_quiet)
        assert res.skipped == 2 and res.copied == 2 * FILES_PER_ANALYSIS - 2
        ledger = json.loads((db / "logs" / "analysis_imports.json").read_text())
        assert "exports/Alpha_Detection/S1/measurements.csv" in ledger   # recorded even though not copied

    def test_removed_source_file_is_archived_never_deleted(self, roots):
        reg, db = roots
        ia.run(log=_quiet)
        gone = reg / "figures" / "Alpha_Detection" / "A1" / "R1" / "S1.png"
        payload = gone.read_bytes()
        gone.unlink()
        res = ia.run(log=_quiet)
        assert res.archived == 1 and res.copied == 0 and res.errors == 0
        assert not (db / "figures" / "Alpha_Detection" / "A1" / "R1" / "S1.png").exists()
        archived = list((db / "_archived" / "analyses").glob("*/figures/Alpha_Detection/A1/R1/S1.png"))
        assert len(archived) == 1 and archived[0].read_bytes() == payload
        ledger = json.loads((db / "logs" / "analysis_imports.json").read_text())
        assert "figures/Alpha_Detection/A1/R1/S1.png" not in ledger
        m = json.loads((db / "exports" / "ANALYSES_MANIFEST.json").read_text())
        rows = {r["analysis_name"]: r for r in m["analyses"]}
        assert rows["Alpha_Detection"]["files_archived"] == 1
        assert rows["Beta_ROI"]["files_archived"] == 0

    def test_dry_run_writes_nothing(self, roots):
        reg, db = roots
        res = ia.run(dry_run=True, log=_quiet)
        assert res.copied == 2 * FILES_PER_ANALYSIS and res.errors == 0
        assert not db.exists() or not any(db.iterdir())
        assert not (db / "logs" / "analysis_imports.json").exists()
        assert not (db / "exports" / "ANALYSES_MANIFEST.json").exists()

    def test_dry_run_after_a_removal_counts_but_does_not_move(self, roots):
        reg, db = roots
        ia.run(log=_quiet)
        (reg / "logs" / "Beta_ROI.log").unlink()
        before = (db / "logs" / "analysis_imports.json").read_text()
        res = ia.run(dry_run=True, log=_quiet)
        assert res.archived == 1
        assert (db / "logs" / "Beta_ROI.log").is_file()
        assert not (db / "_archived").exists()
        assert (db / "logs" / "analysis_imports.json").read_text() == before

    def test_all_ignores_the_ledger_but_still_skips_identical_files(self, roots):
        reg, db = roots
        ia.run(log=_quiet)
        res = ia.run(all_files=True, log=_quiet)
        assert res.copied == 0 and res.skipped == 2 * FILES_PER_ANALYSIS

    def test_limit_caps_copies_not_the_listing(self, roots):
        reg, db = roots
        res = ia.run(limit=3, log=_quiet)
        assert res.copied == 3 and res.scanned == 2 * FILES_PER_ANALYSIS

    def test_unreadable_registry_is_an_error_but_files_still_mirror(self, roots):
        reg, db = roots
        (reg / "exports" / "Beta_ROI" / "registry.json").write_text("{not json", encoding="utf-8")
        res = ia.run(log=_quiet)
        assert res.errors == 1 and res.copied == 2 * FILES_PER_ANALYSIS
        rows = {r["analysis_name"]: r for r in res.manifest}
        assert rows["Beta_ROI"]["entries"] is None and rows["Beta_ROI"]["problems"]
        assert rows["Alpha_Detection"]["entries"] == 2




class TestNoArchiveOnAShortListing:
    """WHY: a mirrored file is archived when the ledger has it and this run's
    listing of the source does not. A listing that came back short -- one
    registry.json not seen once, a folder that could not be opened, a share
    that answered with nothing -- used to move every mirrored file of the
    analysis (or of all of them) to the archive. Archiving needs proof that
    the files are gone: the analysis listed completely, or its folder gone."""

    def _mirrored(self, db, rel):
        return (db / rel).is_file()

    def test_a_folder_without_its_registry_keeps_its_mirror_and_is_an_error(self, roots):
        reg, db = roots
        ia.run(log=_quiet)
        (reg / "exports" / "Alpha_Detection" / "registry.json").unlink()
        res = ia.run(log=_quiet)
        assert res.archived == 0
        assert res.errors == 1
        assert any("Alpha_Detection" in m and "registry.json" in m for m in res.messages)
        for rel in ("exports/Alpha_Detection/registry.json",
                    "exports/Alpha_Detection/S1/measurements.csv",
                    "figures/Alpha_Detection/A1/R1/S1.png", "logs/Alpha_Detection.log"):
            assert self._mirrored(db, rel), rel
        assert not (db / "_archived").exists()
        ledger = json.loads((db / "logs" / "analysis_imports.json").read_text())
        assert "exports/Alpha_Detection/registry.json" in ledger     # kept for a later run
        rows = {r["analysis_name"]: r for r in res.manifest}
        assert rows["Alpha_Detection"]["problems"]
        assert ia.main([]) == 1                                       # the job fails, visibly

    def test_once_the_registry_is_back_nothing_was_lost(self, roots):
        reg, db = roots
        ia.run(log=_quiet)
        regfile = reg / "exports" / "Alpha_Detection" / "registry.json"
        content = regfile.read_bytes()
        regfile.unlink()
        ia.run(log=_quiet)
        regfile.write_bytes(content)
        res = ia.run(log=_quiet)
        assert res.errors == 0 and res.archived == 0

    def test_an_empty_listing_with_a_full_ledger_archives_nothing(self, roots):
        reg, db = roots
        ia.run(log=_quiet)
        import shutil
        for a in ("Alpha_Detection", "Beta_ROI"):
            shutil.rmtree(str(reg / "exports" / a))
        res = ia.run(log=_quiet)
        assert res.archived == 0 and res.errors == 1
        assert any("nothing was archived" in m for m in res.messages)
        assert self._mirrored(db, "exports/Beta_ROI/S1/measurements.csv")
        assert not (db / "_archived").exists()

    def test_a_withdrawn_analysis_is_archived_when_another_is_still_listed(self, roots):
        reg, db = roots
        ia.run(log=_quiet)
        import shutil
        shutil.rmtree(str(reg / "exports" / "Alpha_Detection"))
        shutil.rmtree(str(reg / "figures" / "Alpha_Detection"))
        (reg / "logs" / "Alpha_Detection.log").unlink()
        res = ia.run(log=_quiet)
        assert res.errors == 0 and res.archived == FILES_PER_ANALYSIS
        assert not self._mirrored(db, "exports/Alpha_Detection/registry.json")
        assert self._mirrored(db, "exports/Beta_ROI/registry.json")

    def test_a_folder_that_cannot_be_listed_keeps_its_mirror(self, roots, monkeypatch):
        reg, db = roots
        ia.run(log=_quiet)
        real_scandir = os.scandir
        blocked = os.path.normcase(str(reg / "figures" / "Alpha_Detection" / "A1"))

        def scandir(path="."):
            if os.path.normcase(str(path)) == blocked:
                raise PermissionError(13, "Access is denied", str(path))
            return real_scandir(path)
        monkeypatch.setattr(os, "scandir", scandir)
        # Up to Python 3.10 pathlib lists folders (glob, rglob, iterdir)
        # through _NormalAccessor.scandir, a reference to os.scandir taken
        # when pathlib was imported. Patch that route too, so a listing done
        # with pathlib meets the same blocked folder; otherwise the test
        # would pass for code that never sees the error.
        import pathlib
        accessor = getattr(pathlib, "_NormalAccessor", None)
        if accessor is not None and hasattr(accessor, "scandir"):
            monkeypatch.setattr(accessor, "scandir", staticmethod(scandir))
        res = ia.run(log=_quiet)
        assert res.archived == 0 and res.errors == 1
        assert self._mirrored(db, "figures/Alpha_Detection/A1/R1/S1.png")
        rows = {r["analysis_name"]: r for r in res.manifest}
        assert rows["Beta_ROI"]["files_skipped"] == FILES_PER_ANALYSIS   # the others still run


class TestLedgerAndManifestAreSafe:
    """WHY: the ledger is the only record of what was mirrored. It used to be
    written with a plain overwrite and read as EMPTY when it could not be
    parsed -- so a run killed mid-write left half a ledger, the next run
    treated nothing as mirrored before, and a file MouseBrain withdrew
    afterwards was never archived (nothing in an empty ledger is "gone")."""

    def test_an_unreadable_ledger_stops_the_run_and_is_left_alone(self, roots, capsys):
        reg, db = roots
        ia.run(log=_quiet)
        ledger = db / "logs" / "analysis_imports.json"
        ledger.write_text('{"exports/Alpha_Detection/registry.json": {"ha', encoding="utf-8")
        withdrawn = reg / "logs" / "Beta_ROI.log"
        withdrawn.unlink()
        assert ia.main([]) == 1
        out = capsys.readouterr().out
        assert "[FAIL]" in out and "analysis_imports.json" in out and "rename" in out
        # nothing was rewritten: the damaged file is still there for a person
        # to look at, and the mirror was not touched
        assert ledger.read_text(encoding="utf-8").endswith('{"ha')
        assert (db / "logs" / "Beta_ROI.log").is_file()

    def test_a_missing_ledger_is_a_first_run(self, roots):
        reg, db = roots
        res = ia.run(log=_quiet)
        assert res.errors == 0 and res.copied == 2 * FILES_PER_ANALYSIS

    @pytest.mark.parametrize("killed", ["analysis_imports.json", "ANALYSES_MANIFEST.json"])
    def test_a_write_killed_part_way_leaves_the_previous_file_whole(self, roots, monkeypatch, killed):
        reg, db = roots
        ia.run(log=_quiet)
        ledger = db / "logs" / "analysis_imports.json"
        manifest = db / "exports" / "ANALYSES_MANIFEST.json"
        before_ledger, before_manifest = ledger.read_bytes(), manifest.read_bytes()
        (reg / "logs" / "Beta_ROI.log").unlink()        # this run would change both files

        import builtins
        import io
        real_open = io.open
        targets = (killed,)

        class _Killed(Exception):
            pass

        class _HalfWriter:
            """Writes the first half of what it is given, then 'is killed'."""
            def __init__(self, f):
                self._f = f
            def write(self, text):
                self._f.write(text[: max(1, len(text) // 2)])
                self._f.flush()
                raise _Killed()
            def __getattr__(self, name):
                return getattr(self._f, name)
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                self._f.close()

        def fake_open(file, mode="r", *a, **k):
            f = real_open(file, mode, *a, **k)
            name = os.path.basename(str(file))
            if "w" in mode and any(t in name for t in targets):
                return _HalfWriter(f)
            return f

        monkeypatch.setattr(io, "open", fake_open)
        monkeypatch.setattr(builtins, "open", fake_open)
        # Path.write_text does not go through io.open/builtins.open on every
        # Python: up to 3.10 pathlib opens through _NormalAccessor.open, a
        # reference to io.open taken when pathlib was imported. Patch that
        # route too, so a plain write_text is "killed" as well and the test
        # shows what a non-atomic write leaves behind.
        import pathlib
        accessor = getattr(pathlib, "_NormalAccessor", None)
        if accessor is not None and hasattr(accessor, "open"):
            monkeypatch.setattr(accessor, "open", staticmethod(fake_open))
        with pytest.raises(_Killed):
            ia.run(log=_quiet)
        monkeypatch.undo()
        path, before = ((ledger, before_ledger) if killed == ledger.name
                        else (manifest, before_manifest))
        assert path.read_bytes() == before            # the old file, whole
        json.loads(path.read_text(encoding="utf-8"))



class TestOneMirrorAtATime:
    """WHY: the nightly task and the "Update the database now" button on the
    Where Is My Data tab (mousedb update) both start the mirror, and nothing kept two runs apart: each reads the ledger at the
    start and rewrites it at the end, so the second dropped the first's
    entries. A run now takes its own lock; one that cannot get it in time
    says so and changes nothing."""

    @pytest.fixture
    def held(self, roots, monkeypatch):
        from mousedb import task_mutex
        monkeypatch.setenv(task_mutex.WAIT_ENV, "0.002")     # about 0.1 s
        # the lock the docs name: <mousedb_root>/logs/.import-analyses.task.lock
        reg, db = roots
        m = task_mutex.TaskMutex("import-analyses", base_dir=db / "logs")
        assert m.try_acquire()
        yield m
        m.release()

    def test_a_second_run_waits_then_stops_without_changing_anything(self, roots, held, capsys):
        reg, db = roots
        assert ia.main([]) == 1
        out = capsys.readouterr().out
        assert "[FAIL] import-analyses not run" in out
        assert not (db / "exports" / "Alpha_Detection").exists()
        assert not (db / "logs" / "analysis_imports.json").exists()

    def test_a_dry_run_takes_no_lock(self, roots, held):
        assert ia.main(["--dry-run"]) == 0

    def test_the_lock_is_given_back(self, roots):
        reg, db = roots
        assert ia.main([]) == 0
        assert ia.main([]) == 0
        assert not (db / "logs" / ".import-analyses.task.lock").exists()


class TestCli:
    def test_main_dry_run_returns_zero_and_json(self, roots, capsys):
        reg, db = roots
        assert ia.main(["--dry-run", "--json"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["copied"] == 2 * FILES_PER_ANALYSIS and out["analyses"] == 2
        assert not (db / "exports").exists()

    def test_main_reports_a_missing_registry(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("MOUSEDB_ROOT", str(tmp_path / "db"))
        monkeypatch.setenv("MOUSEBRAIN_PIPELINE_ROOT", str(tmp_path / "nowhere"))
        assert ia.main(["--dry-run"]) == 1
        assert "[FAIL]" in capsys.readouterr().out

    def test_mousedb_subcommand_is_wired(self):
        from mousedb import cli
        assert callable(cli.cmd_import_analyses)
        src = Path(cli.__file__).read_text(encoding="utf-8")
        assert "'import-analyses'" in src


class TestHelpers:
    def test_analysis_of_key(self):
        assert ia.analysis_of_key("exports/A/S1/m.csv") == "A"
        assert ia.analysis_of_key("figures/A/x/y.png") == "A"
        assert ia.analysis_of_key("logs/A.log") == "A"
        assert ia.analysis_of_key("exports/ANALYSES_MANIFEST.json") is None

    def test_summarize_registry_counts(self):
        reg = {"analysis_name": "X", "approved_method_hash": "h", "last_updated": "t",
               "entries": {"a": {"is_current": True, "method_hash": "h"},
                           "b": {"is_current": True, "method_hash": "old"},
                           "c": {"is_current": False, "method_hash": "old"}}}
        s = ia.summarize_registry(reg)
        assert s == {"analysis_name": "X", "entries": 3, "current": 2, "invalidated": 1,
                     "last_updated": "t", "approved_method_hash": "h",
                     "approved_method_recorded_at": None, "stale_vs_approved": 1,
                     "no_approved_method": False, "set_aside_manifests": 0}

    def test_an_analysis_with_no_approved_method_is_not_called_stale(self):
        """Before: an empty approved hash made every current entry 'stale'."""
        for empty in (None, ""):
            reg = {"analysis_name": "X", "approved_method_hash": empty,
                   "entries": {"a": {"is_current": True, "method_hash": "h1"},
                               "b": {"is_current": True, "method_hash": "h2"}}}
            s = ia.summarize_registry(reg)
            assert s.get("stale_vs_approved") is None
            assert s.get("no_approved_method") is True
            assert s["current"] == 2

    def test_set_aside_manifests_are_counted(self):
        reg = {"analysis_name": "X", "approved_method_hash": "h", "entries": {},
               "set_aside_manifests": [{"file": "registry.json.set_aside_1"},
                                       {"file": "registry.json.set_aside_2"}]}
        assert ia.summarize_registry(reg).get("set_aside_manifests") == 2


class TestDataStatusLines:
    def test_manifest_rows_and_lines(self, tmp_path):
        from mousedb import data_status as ds
        p = tmp_path / "ANALYSES_MANIFEST.json"
        assert ds.read_analyses_manifest(p) == []
        p.write_text(json.dumps({"analyses": [
            {"analysis_name": "A", "entries": 6, "current": 5, "stale_vs_approved": 2,
             "invalidated": 1, "approved_method_hash": "h1", "imported_at": "2026-08-28T10:00:00"}]}), encoding="utf-8")
        rows = ds.read_analyses_manifest(p)
        assert rows and rows[0]["analysis_name"] == "A"
        line = ds.analysis_lines(rows)[0]
        assert line == ("Analysis A: 5 current, 2 stale vs approved (approved method as recorded "
                        "at an unknown date (registry written before the date was kept)), "
                        "1 invalidated, imported 2026-08-28T10:00:00")

    def test_lines_say_no_approved_method_and_name_set_aside_manifests(self):
        from mousedb import data_status as ds
        # Both rows were read this run (``entries`` set): a row whose registry
        # was not read says so instead (TestHeldAnalysisShowsItsReason).
        rows = [{"analysis_name": "B", "entries": 3, "current": 3, "stale_vs_approved": None,
                 "no_approved_method": True, "invalidated": 0, "imported_at": "t"},
                {"analysis_name": "C", "entries": 1, "current": 1, "stale_vs_approved": 0,
                 "no_approved_method": False, "set_aside_manifests": 1,
                 "invalidated": 0, "imported_at": "t"}]
        b, c = ds.analysis_lines(rows)
        assert b == "Analysis B: 3 current, no approved method, 0 invalidated, imported t"
        assert "? stale" not in b
        assert c.startswith("Analysis C: 1 current, 0 stale vs approved (approved method as recorded "
                            "at an unknown date (registry written before the date was kept)), "
                            "0 invalidated, imported t [!] 1 damaged manifest set aside")


class TestApprovedMethodDateIsShown:
    """WHY: "stale vs approved" is judged against the approved method recorded
    in MouseBrain's registry.json, which is only as new as MouseBrain's last
    write of it. After the approved method changed, the count read "0 stale"
    with nothing to show it was judged against the old one. The manifest
    carries the date the header was recorded, and every line shows it."""

    def test_the_recorded_date_reaches_the_manifest_and_the_status_line(self, roots):
        reg, db = roots
        f = reg / "exports" / "Alpha_Detection" / "registry.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        data["approved_method_recorded_at"] = "2026-03-01T09:30:12.123456"
        f.write_text(json.dumps(data), encoding="utf-8")
        lines = []
        ia.run(log=lines.append)
        m = json.loads((db / "exports" / "ANALYSES_MANIFEST.json").read_text())
        rows = {r["analysis_name"]: r for r in m["analyses"]}
        assert rows["Alpha_Detection"]["approved_method_recorded_at"] == "2026-03-01T09:30:12.123456"
        assert rows["Beta_ROI"]["approved_method_recorded_at"] is None
        from mousedb import data_status as ds
        status = ds.analysis_lines(ds.read_analyses_manifest(db / "exports" / "ANALYSES_MANIFEST.json"))
        alpha = [s for s in status if s.startswith("Analysis Alpha_Detection:")][0]
        assert "1 stale vs approved (approved method as recorded 2026-03-01 09:30)" in alpha
        beta = [s for s in status if s.startswith("Analysis Beta_ROI:")][0]
        assert "approved method as recorded at an unknown date" in beta
        assert any("Alpha_Detection" in s and "as recorded 2026-03-01 09:30" in s for s in lines)

    def test_no_approved_method_is_not_counted_as_stale(self, roots):
        reg, db = roots
        f = reg / "exports" / "Alpha_Detection" / "registry.json"
        data = json.loads(f.read_text(encoding="utf-8"))
        data["approved_method_hash"] = None
        f.write_text(json.dumps(data), encoding="utf-8")
        res = ia.run(log=_quiet)
        rows = {r["analysis_name"]: r for r in res.manifest}
        assert rows["Alpha_Detection"]["stale_vs_approved"] is None
        from mousedb import data_status as ds
        line = ds.analysis_lines([rows["Alpha_Detection"]])[0]
        assert "no approved method" in line and "stale" not in line


class TestHeldAnalysisShowsItsReason:
    """WHY: an analysis whose registry.json was not read this run (missing,
    unlistable, unreadable) gets a manifest row with no counts. Its status
    line used to fall through to "approved method as recorded at an unknown
    date (registry written before the date was kept)", blaming an old
    MouseBrain, while the real reason sat only in the manifest's problems,
    which the Where Is My Data tab never showed."""

    def test_a_row_with_problems_names_the_reason(self):
        from mousedb import data_status as ds
        row = ia._new_row("Gamma_Test", "2026-01-02T03:04:05", Path("/registry"))
        row["problems"] = ["Gamma_Test: exports/Gamma_Test has no registry.json; nothing of it "
                           "was copied or archived -- the mirror is kept as it was",
                           "second problem"]
        line = ds.analysis_lines([row])[0]
        assert "unknown date" not in line
        assert "? stale vs approved (registry not read this run)" in line
        assert line.endswith(" -- [FAIL] Gamma_Test: exports/Gamma_Test has no registry.json; "
                             "nothing of it was copied or archived -- the mirror is kept as it "
                             "was (and 1 more problem(s) in ANALYSES_MANIFEST.json)")

    def test_a_folder_without_its_registry_shows_why_on_the_status_line(self, roots):
        reg, db = roots
        ia.run(log=_quiet)
        (reg / "exports" / "Beta_ROI" / "registry.json").rename(
            reg / "exports" / "Beta_ROI" / "registry.json.rebuilding")
        ia.run(log=_quiet)
        from mousedb import data_status as ds
        status = ds.analysis_lines(ds.read_analyses_manifest(db / "exports" / "ANALYSES_MANIFEST.json"))
        beta = [s for s in status if s.startswith("Analysis Beta_ROI:")]
        assert beta, status
        assert "[FAIL]" in beta[0] and "has no registry.json" in beta[0]
        assert "registry not read this run" in beta[0] and "unknown date" not in beta[0]
        alpha = [s for s in status if s.startswith("Analysis Alpha_Detection:")][0]
        assert "[FAIL]" not in alpha


class TestImportBrains:
    def test_without_summary_dir_and_without_config_raises_config_error(self, monkeypatch):
        """cli.py used to reference a module constant that no longer existed:
        NameError on every run without --summary-dir. The default must come
        from config, and the failure must say what to set."""
        from mousedb import cli, config
        import mousedb.database as dbmod

        def _unset(key):
            raise config.ConfigError("mousedb does not know '%s' on this machine" % key)
        monkeypatch.setattr(config, "require", _unset)
        monkeypatch.setattr(dbmod, "init_database",
                            lambda *a, **k: pytest.fail("database opened before the folder was resolved"))
        args = argparse.Namespace(summary_dir=None, all=True, csv=None, calibration=None,
                                  brain=None, update=False, dry_run=True)
        with pytest.raises(config.ConfigError):
            cli.cmd_import_brains(args)

    def test_a_real_import_waits_for_the_database_task_mutex(self, monkeypatch, tmp_path):
        """WHY: import-brains commits to connectome.db but did not take the
        mutex the reach import and snapshot share; three hourly runs in a row
        failed "database is locked" (2026-09-15). A dry run touches nothing and
        must not wait."""
        import contextlib
        from mousedb import cli, task_mutex
        held, bodies = [], []
        monkeypatch.setattr(task_mutex, "hold",
                            lambda *a, **k: held.append(True) or contextlib.nullcontext())
        monkeypatch.setattr(cli, "_import_brains_body",
                            lambda args, summary_dir: bodies.append(args.dry_run))
        base = dict(summary_dir=str(tmp_path), all=True, csv=None, calibration=None,
                    brain=None, update=False)
        cli.cmd_import_brains(argparse.Namespace(dry_run=False, **base))
        assert held == [True] and bodies == [False]
        cli.cmd_import_brains(argparse.Namespace(dry_run=True, **base))
        assert held == [True] and bodies == [False, True]


class TestElifeFromSummaryCsv:
    HEADER = ("brain,run_date,brain_id,subject,cohort,total_cells,total_left,total_right,"
              "group_Red_Nucleus,group_Parabrachial___Pedunculopontine,group_Unmapped,"
              "group_left_Red_Nucleus,group_right_Red_Nucleus,group_left_Unmapped,group_right_Unmapped\n")

    def _csv(self, tmp_path):
        return _write(tmp_path / "elife_counts.csv", self.HEADER +
                      "349_CNT_01_02/349_CNT_01_02_1p625x_z4,2026-02-21T18:47:20,349,CNT_01_02/349,CNT_01,"
                      "9314,4812,4502,954,26,0,661,293,,\n"
                      "357_CNT_02_08/357_CNT_02_08_1p625x_z4,2026-02-21T12:53:28,357,CNT_02_08/357,CNT_02,"
                      "4208,1998,2210,273,74,16,144,129,16,0\n")

    def test_rows_match_the_brain_and_decode_group_names(self, tmp_path):
        from mousedb.importers import read_elife_summary_rows
        rows = read_elife_summary_rows(self._csv(tmp_path), "357_CNT_02_08_1p625x_z4")
        assert rows["Red Nucleus"] == {"both": 273, "left": 144, "right": 129}
        assert rows["Parabrachial / Pedunculopontine"] == {"both": 74}
        assert rows["[Unmapped]"] == {"both": 16, "left": 16, "right": 0}
        rows = read_elife_summary_rows(self._csv(tmp_path), "349_CNT_01_02_1p625x_z4")
        assert rows["Red Nucleus"] == {"both": 954, "left": 661, "right": 293}
        assert rows["[Unmapped]"] == {"both": 0}      # blank hemisphere cells are absent, not 0

    def test_unknown_brain_is_a_lookup_error(self, tmp_path):
        from mousedb.importers import read_elife_summary_rows
        with pytest.raises(LookupError):
            read_elife_summary_rows(self._csv(tmp_path), "999_CNT_09_09_1p625x_z4")

    def test_import_reads_the_csv_when_mousebrain_is_absent(self, tmp_path, monkeypatch):
        import datetime
        from mousedb import database as dbmod
        from mousedb.importers import BrainGlobeImporter
        from mousedb.schema import BrainSample, ElifeRegionCount, Project, Cohort, Subject
        monkeypatch.setitem(sys.modules, "mousebrain", None)                 # import -> ImportError
        monkeypatch.setitem(sys.modules, "mousebrain.region_mapping", None)
        monkeypatch.setattr(dbmod, "DEFAULT_LOG_PATH", None)                 # logs stay under tmp
        db = dbmod.Database(tmp_path / "t.db")
        db.init_db()
        with db.session() as s:
            # foreign keys are enforced: the brain sample needs its subject chain
            if not s.query(Project).filter_by(project_code="CNT").first():
                s.add(Project(project_code="CNT", project_name="test project"))
            s.add(Cohort(cohort_id="CNT_02", project_code="CNT", start_date=datetime.date(2026, 1, 1)))
            s.add(Subject(subject_id="CNT_02_08", cohort_id="CNT_02"))
        with db.session() as s:
            s.add(BrainSample(subject_id="CNT_02_08", brain_id="357_CNT_02_08_1p625x_z4", brain_number=357))
        with db.session() as s:
            bs_id = s.query(BrainSample).one().id
        imp = BrainGlobeImporter(db)
        res = imp.import_elife_counts(brain_sample_id=bs_id, region_counts_dict={"RN": 273},
                                      is_final=True, brain_id="357_CNT_02_08_1p625x_z4",
                                      elife_summary_csv=self._csv(tmp_path))
        assert res["success"], res["errors"]
        assert res["imported"]["elife_region_counts"] == 3 + 1 + 3     # RN both/l/r, Parabrachial both, Unmapped both/l/r
        with db.session() as s:
            got = {(r.group_name, r.hemisphere): r.cell_count for r in s.query(ElifeRegionCount).all()}
        assert got[("Red Nucleus", "both")] == 273 and got[("Red Nucleus", "left")] == 144
        assert got[("Parabrachial / Pedunculopontine", "both")] == 74
        assert got[("[Unmapped]", "right")] == 0

    def test_nothing_possible_is_an_explicit_error_not_a_silent_skip(self, tmp_path, monkeypatch):
        from mousedb.importers import BrainGlobeImporter
        monkeypatch.setitem(sys.modules, "mousebrain", None)
        monkeypatch.setitem(sys.modules, "mousebrain.region_mapping", None)
        imp = BrainGlobeImporter(db=object())   # the error path never opens a session
        res = imp.import_elife_counts(brain_sample_id=1, region_counts_dict={"RN": 1},
                                      brain_id="357_CNT_02_08_1p625x_z4",
                                      elife_summary_csv=tmp_path / "missing.csv")
        assert not res["success"]
        assert any("NOT imported" in e and "elife_counts.csv" in e for e in res["errors"])
        res = imp.import_elife_counts(brain_sample_id=1, region_counts_dict={"RN": 1})
        assert not res["success"] and any("NOT imported" in e for e in res["errors"])


class TestImportBrainsExitCode:
    """A brain import that did not land must exit non-zero and END its output
    with a [FAIL] line. WHY: the scheduler judges a job by its exit code and the
    task check reads lines that START with [FAIL]; a refused brain used to print
    "[FAIL]" mid-line and the job exited 0, so the scheduler showed success
    over brains that never reached the database."""

    class _Importer:
        """Brain "B1" imports, "B2" is refused, "junk" has no parsable id."""
        def __init__(self, db, calibration_ok=True):
            self.calibration_ok = calibration_ok

        def import_calibration_runs(self, csv_path, dry_run=False):
            ok = self.calibration_ok
            return {"success": ok, "imported": {}, "errors": [] if ok else ["bad calibration"],
                    "warnings": []}

        def _extract_brain_from_path(self, p):
            name = Path(p).name
            return None if name.startswith("junk") else name.split("_counts")[0]

        def import_region_counts(self, csv_path, brain_id=None, is_final=True, dry_run=False):
            ok = brain_id != "B2"
            return {"success": ok, "imported": {"region_counts": 3 if ok else 0},
                    "errors": [] if ok else ["cohort not in the database"], "warnings": []}

    def _setup(self, tmp_path, monkeypatch, calibration_ok=True):
        import sqlalchemy
        from mousedb import cli
        import mousedb.database as dbmod
        import mousedb.importers as imod

        class _Db:
            engine = sqlalchemy.create_engine("sqlite://")
        monkeypatch.setattr(dbmod, "init_database", lambda *a, **k: _Db())
        monkeypatch.setattr(imod, "BrainGlobeImporter",
                            lambda db: self._Importer(db, calibration_ok))
        monkeypatch.setattr(cli, "_import_elife_from_csv", lambda *a, **k: (0, []))
        for name in ("B1_counts.csv", "B2_counts.csv", "junk_counts.csv", "calibration_runs.csv"):
            _write(tmp_path / name, "region,count\nroot,1\n")
        return cli

    def _args(self, tmp_path, **kw):
        base = dict(summary_dir=str(tmp_path), all=True, csv=None, calibration=None,
                    brain=None, update=False, dry_run=True)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_a_refused_brain_fails_the_run_with_a_last_line_fail(self, tmp_path, monkeypatch, capsys):
        cli = self._setup(tmp_path, monkeypatch)
        rc = cli._import_brains_body(self._args(tmp_path), tmp_path)
        lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
        assert rc == 1
        assert lines[-1].startswith("[FAIL]")
        assert "B2" in lines[-1] and "junk_counts.csv" in lines[-1]
        assert "B1" not in lines[-1].replace("B1_", "")

    def test_a_failed_calibration_import_fails_the_run(self, tmp_path, monkeypatch, capsys):
        cli = self._setup(tmp_path, monkeypatch, calibration_ok=False)
        for name in ("B2_counts.csv", "junk_counts.csv"):
            (tmp_path / name).unlink()
        rc = cli._import_brains_body(self._args(tmp_path), tmp_path)
        last = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()][-1]
        assert rc == 1 and last.startswith("[FAIL]") and "calibration" in last

    def test_a_clean_run_exits_zero(self, tmp_path, monkeypatch, capsys):
        cli = self._setup(tmp_path, monkeypatch)
        for name in ("B2_counts.csv", "junk_counts.csv"):
            (tmp_path / name).unlink()
        assert not cli._import_brains_body(self._args(tmp_path), tmp_path)

    def test_calibration_only_and_single_csv_fail_loudly(self, tmp_path, monkeypatch):
        cli = self._setup(tmp_path, monkeypatch, calibration_ok=False)
        rc = cli._import_brains_body(
            self._args(tmp_path, all=False, calibration=str(tmp_path / "calibration_runs.csv")),
            tmp_path)
        assert rc == 1
        rc = cli._import_brains_body(
            self._args(tmp_path, all=False, csv=str(tmp_path / "B2_counts.csv")), tmp_path)
        assert rc == 1

    def test_the_command_line_exits_with_what_the_command_returned(self, monkeypatch):
        """cli.main used to drop a command's return value, so every
        ``return 1`` in a command still exited 0."""
        from mousedb import cli
        monkeypatch.setattr(cli, "cmd_status", lambda args: 1)
        monkeypatch.setattr(sys, "argv", ["mousedb", "status"])
        with pytest.raises(SystemExit) as e:
            cli.main()
        assert e.value.code == 1

    def test_a_command_that_returns_nothing_still_exits_zero(self, monkeypatch):
        from mousedb import cli
        monkeypatch.setattr(cli, "cmd_status", lambda args: None)
        monkeypatch.setattr(sys, "argv", ["mousedb", "status"])
        cli.main()          # no SystemExit: success stays success

    def test_an_unreachable_summary_folder_fails_with_a_fail_line(self, tmp_path, monkeypatch, capsys):
        cli = self._setup(tmp_path, monkeypatch)
        rc = cli._import_brains_body(self._args(tmp_path), tmp_path / "missing")
        last = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()][-1]
        assert rc == 1 and last.startswith("[FAIL]")
