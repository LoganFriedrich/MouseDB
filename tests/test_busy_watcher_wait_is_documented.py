"""The wait for a busy watcher database is stated correctly in the operator docs.

WHY: the docs said a locked watcher database was tried "over about a minute
and a half"; the code allows three tries of a 30-second busy timeout with two
20-second pauses -- over two minutes. A person timing a stuck refresh against
the docs would conclude it was hung when it was still waiting.
"""
from pathlib import Path

from mousedb.exporters import export_exclusions as ee

DOC = Path(__file__).resolve().parent.parent / "docs" / "MOUSEREACH_VIDEO_PROCESSING_PROGRESS.md"


def test_the_documented_wait_matches_the_retry_settings():
    longest = ee.COLLAGE_TRIES * ee.COLLAGE_BUSY_TIMEOUT \
        + (ee.COLLAGE_TRIES - 1) * ee.COLLAGE_RETRY_SECONDS
    text = DOC.read_text(encoding="utf-8")
    assert "a minute and a half" not in text
    # "a little over two minutes" per database, read twice per refresh.
    assert 120 < longest <= 150, longest
    assert "a little over two minutes per database" in text
    assert "about four and a half minutes per database" in text
    assert 240 < 2 * longest <= 300
