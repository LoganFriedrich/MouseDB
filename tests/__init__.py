"""The test suite's package.

Importing anything from it first runs the suite's machine isolation
(tests/conftest.py, part 2: every configuration source of this computer is
hidden before mousedb is imported).

WHY here and not only in conftest.py: pytest loads tests/conftest.py only for
test files inside tests/. A test file written somewhere else -- a quick
scenario check that borrows this suite's helpers with
``from tests.test_the_sheet_wins import ...`` -- used to run with this
computer's REAL configuration. Its synthetic import then ended, as every
import does, by rewriting the hand-scores file and the FIX THESE files at the
configured (live) locations, while its import history pointed at a test file:
the live hand-scores file lost every real cohort's import time. Importing the
conftest here hides the configuration for any such caller too, so the live
locations are simply unknown to it and every live write fails instead of
landing. (The per-test guard that fails a test touching a live folder still
runs only inside the suite.)
"""
from . import conftest  # noqa: F401  (imported for its side effect: see above)
