"""Calibration runs that name no brain are reported once, by name and count.

WHY (2026-10-22): the tracker records every calibration run, including runs on
training cubes and test data. The nightly brain import warned once per such row
("Could not parse brain ID: training_data", ...), every night, which buried any
real problem in the task log.
"""
import pandas as pd
import pytest

from mousedb import database as dbmod
from mousedb.importers import BrainGlobeImporter


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A scratch database; audit log files stay under tmp_path."""
    monkeypatch.setattr(dbmod, "DEFAULT_LOG_PATH", None)
    d = dbmod.Database(tmp_path / "scratch.db")
    d.init_db()
    return d


def test_non_brain_runs_are_one_warning_naming_each_once(db, tmp_path):
    csv = tmp_path / "calibration_runs.csv"
    pd.DataFrame({"brain": ["training_data", "training_data", "TEST_VERIFICATION_BRAIN_001"],
                  "exp_id": ["a", "b", "c"]}).to_csv(csv, index=False)
    imp = BrainGlobeImporter(db)
    imp.import_calibration_runs(csv)
    lines = [w for w in imp.warnings if "name no brain" in w]
    assert len(lines) == 1
    assert "3 calibration run(s)" in lines[0]
    assert "training_data (2)" in lines[0] and "TEST_VERIFICATION_BRAIN_001 (1)" in lines[0]
    assert not [w for w in imp.warnings if w.startswith("Could not parse brain ID")]
