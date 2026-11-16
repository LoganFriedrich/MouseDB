"""A check written outside tests/ that borrows this suite's helpers never sees
this computer's configuration.

WHY: pytest loads tests/conftest.py (which hides the configuration) only for
test files inside tests/. A scenario check placed elsewhere that did
``from tests.test_the_sheet_wins import ...`` ran a synthetic sheet import
with the computer's REAL configuration, and the import's last step rewrote the
live hand-scores file (tests/__init__.py has the details). Importing the tests
package now hides the configuration first.

The check runs in a child process with a home folder of its own whose
settings name a throwaway folder as the data root, standing in for a
configured computer; nothing real is read or written.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

CHILD = r"""
import tests.test_the_sheet_wins  # what a borrowed-helper scenario check does first
from mousedb import config, hand_scores_file
print("ROOT=%s" % config.get("mousedb_root"))
try:
    print("OUT=%s" % hand_scores_file.out_dir())
except Exception as e:
    print("OUT=refused %s" % type(e).__name__)
"""


def test_importing_the_suite_helpers_hides_the_configured_data_root(tmp_path):
    home = tmp_path / "home"
    (home / ".mousedb").mkdir(parents=True)
    stand_in_root = tmp_path / "configured_root"
    (home / ".mousedb" / "config.json").write_text(
        json.dumps({"mousedb_root": str(stand_in_root)}), encoding="utf-8")

    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("MOUSEDB_", "MOUSEREACH_"))}
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONPATH=str(REPO),
               MOUSEDB_MACHINE_CONFIG=str(tmp_path / "no_machine_settings.json"))
    # Run from a folder outside the repo, as a scenario check elsewhere would.
    out = subprocess.run([sys.executable, "-c", CHILD], cwd=str(tmp_path), env=env,
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    lines = dict(l.split("=", 1) for l in out.stdout.splitlines() if "=" in l)
    # The stand-in configuration was hidden: no data root, so the hand-scores
    # file has nowhere to be written.
    assert lines["ROOT"] == "None"
    assert lines["OUT"].startswith("refused")
    assert not stand_in_root.exists()
