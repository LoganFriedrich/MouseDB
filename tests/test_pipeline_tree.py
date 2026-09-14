"""Superseded and scratch copies are never read as live results.

Analyzed holds files with the SAME names as a video's live results in places
that are not live: the superseded-output archive, pose-only DLC Model
folders, collage folders, the folder template, UNKNOWN, and scratch or
retired copies. The reach import deletes a video's rows before inserting the
file's rows, so reading one of those would replace live reach data with an
older generation's. These tests pin the pruning rule and the import's use of
it.
"""
from pathlib import Path

from mousedb.import_reaches import find_features_files
from mousedb.pipeline_tree import is_skipped_dir, iter_files

STEM = "20250101_CNT0101_P1"


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}", encoding="utf-8")
    return p


def _tree(root: Path) -> Path:
    """One live copy, plus the same name everywhere it must NOT be read."""
    live = _touch(root / "Analyzed" / "Connectome" / "CNT01" / f"{STEM}_features.json")
    for decoy in (
        "Analyzed/Archive/DLC Model 4.0/seg2.2.4_reach8.1.0_out6.1.0_asn2.1.0",
        "Analyzed/Archive/superseded_processing_root_3.1",
        "Analyzed/Connectome/DLC Model 4/CNT01",
        "Analyzed/Connectome/CNT01/Multi-Animal",
        "Analyzed/Folder Template",
        "Analyzed/UNKNOWN",
        "Analyzed/Connectome/CNT01/_retired",
        "Analyzed/.inflight/host",
    ):
        _touch(root / decoy / f"{STEM}_features.json")
    return live


class TestTheRule:

    def test_skipped_names(self):
        for name in ("Archive", "DLC Model 3", "DLC Model 4", "Multi-Animal",
                     "Folder Template", "UNKNOWN", "_leftovers", ".claims"):
            assert is_skipped_dir(name), name

    def test_real_folders_are_not_skipped(self):
        for name in ("Connectome", "CNT01", "ASPA", "triage", "20250101_CNT0101_P1"):
            assert not is_skipped_dir(name), name

    def test_only_the_live_copy_is_found(self, tmp_path):
        live = _tree(tmp_path)
        assert list(iter_files(tmp_path / "Analyzed", "*_features.json")) == [live]

    def test_the_root_itself_is_walked_whatever_its_name(self, tmp_path):
        f = _touch(tmp_path / "_leftovers" / f"{STEM}_features.json")
        assert list(iter_files(tmp_path / "_leftovers", "*_features.json")) == [f]

    def test_missing_root_yields_nothing(self, tmp_path):
        assert list(iter_files(tmp_path / "nope", "*")) == []
        assert list(iter_files(None, "*")) == []


class TestTheReachImport:

    def test_archived_copies_are_not_imported(self, tmp_path):
        live = _tree(tmp_path)
        assert find_features_files(tmp_path) == [live]

    def test_processing_bundles_are_found_but_not_retired_copies(self, tmp_path):
        live = _tree(tmp_path)
        bundle = _touch(tmp_path / "Processing" / "Review" / "triage" / STEM / f"{STEM}_features.json")
        _touch(tmp_path / "Processing" / "Review" / "_Problematic" / STEM / f"{STEM}_features.json")
        _touch(tmp_path / "Processing" / "_leftovers_2026-09-14" / "DLC_Complete" / f"{STEM}_features.json")
        assert find_features_files(tmp_path, include_processing=True) == sorted([live, bundle])
        assert find_features_files(tmp_path) == [live]


class TestTheFinishedCheck:

    def test_a_manifest_only_in_the_archive_is_not_in_analyzed(self, tmp_path, monkeypatch):
        """A video held in review whose superseded manifest was archived must
        not pass as finished."""
        import mousedb.analyzable as an
        live, held = STEM, "20250101_CNT0102_P1"
        _touch(tmp_path / "Analyzed" / "Connectome" / "CNT01" / f"{live}_processing_manifest.json")
        _touch(tmp_path / "Analyzed" / "Archive" / "DLC Model 4.0" / "seg2.2.4_reach8.1.0_out6.1.0_asn2.1.0"
               / f"{held}_processing_manifest.json")
        row = {"pipeline_versions": {"segmenter": "2.0"}, "dlc_scorer": "NEW"}
        monkeypatch.setattr(an, "_analyzed", lambda: tmp_path / "Analyzed")
        monkeypatch.setattr(an, "_index_rows", lambda db_path=None: {live: row, held: row})
        ok, rejected = an.finished_videos_with_reasons({"dlc_scorer": "NEW", "segmenter": "2.0"})
        assert ok == {live}
        assert rejected[held].startswith("not in the Analyzed folder")


class TestAspaVideoDates:

    def test_an_archived_copy_adds_no_session_date(self, tmp_path, monkeypatch):
        import mousedb.cohort_sheets as cs
        import mousedb.cohort_tools.register_aspa as ra
        _touch(tmp_path / "Analyzed" / "ASPA" / "ASPA" / "20220811_ASPA1011_P3_features.json")
        _touch(tmp_path / "Analyzed" / "Archive" / "DLC Model 3.1" / "seg2.1.0_reach5.3.0_out2.4.4_asnNA"
               / "20220901_ASPA1011_P3_features.json")
        monkeypatch.setattr(cs, "aspa_cohort_number", lambda letter: "10")
        monkeypatch.setattr(ra, "_video_roots", lambda: [tmp_path / "Analyzed"])
        assert ra.videos_for("J") == {11: ["20220811"]}
