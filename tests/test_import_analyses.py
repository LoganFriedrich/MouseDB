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
                     "last_updated": "t", "approved_method_hash": "h", "stale_vs_approved": 1}


class TestDataStatusLines:
    def test_manifest_rows_and_lines(self, tmp_path):
        from mousedb import data_status as ds
        p = tmp_path / "ANALYSES_MANIFEST.json"
        assert ds.read_analyses_manifest(p) == []
        p.write_text(json.dumps({"analyses": [
            {"analysis_name": "A", "current": 5, "stale_vs_approved": 2, "invalidated": 1,
             "imported_at": "2026-08-28T10:00:00"}]}), encoding="utf-8")
        rows = ds.read_analyses_manifest(p)
        assert rows and rows[0]["analysis_name"] == "A"
        line = ds.analysis_lines(rows)[0]
        assert line == "Analysis A: 5 current, 2 stale vs approved, 1 invalidated, imported 2026-08-28T10:00:00"


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
