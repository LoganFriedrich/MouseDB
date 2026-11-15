"""A per-reach skip record that cannot be removed is reported, not swallowed.

WHY: after a per-reach export that changed files without saving a new skip
record, the old record is removed so a later night cannot skip the rebuild on
its strength. That delete was wrapped in "ignore any error": when another
program held the file open, it failed every night and nothing said so.
"""
import json
from pathlib import Path

from mousedb.exporters import current, odc_reaches


def test_a_refused_delete_of_the_skip_record_is_a_problem(tmp_path, monkeypatch):
    out = tmp_path / "current"
    out.mkdir()
    snap = tmp_path / "snap"
    snap.mkdir()
    sig_file = out / ".odc_reaches_signature.json"
    sig_file.write_text(json.dumps({"signature": {"old": True}, "files": {}}), encoding="utf-8")

    def export(snapshot_dir, out_dir, manifest, staging_dir=None):
        # A run with a problem that does not follow from its inputs: no new
        # skip record may be saved, and the old one must go.
        manifest["problems"].append("ODC_reaches_PROJ_01: OSError: could not write")
        manifest.setdefault("failures", []).append("ODC_reaches_PROJ_01: could not write")
        return manifest

    monkeypatch.setattr(odc_reaches, "export", export)
    monkeypatch.setattr(current, "odc_reaches_signature", lambda snapshot_dir: {"new": True})
    real_unlink = Path.unlink

    def held_open(self, *a, **kw):
        if self.name == sig_file.name:
            raise PermissionError(13, "The process cannot access the file", str(self))
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", held_open)
    manifest = {"files": {}, "problems": [], "notes": []}
    current._odc_reaches_csvs(snap, out, manifest)
    said = [p for p in manifest["problems"] if sig_file.name in p]
    assert said, manifest["problems"]
    assert "could not be removed" in said[0]
