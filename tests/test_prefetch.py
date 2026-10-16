"""The database file is read once and held open while a scan runs.

WHY: over a network share a cold SQLite table scan runs at a fraction of a
megabyte per second; the same scan runs at tens of MB/s once the file is in
the operating system's cache, and that cache is dropped when the last handle
on the file closes. So the warm-up must (a) read the whole file and (b) keep
it open until the job inside the block is done (2026-10-16).
"""
import os
import sys

import pytest

from mousedb import prefetch


def _file(tmp_path, size=3 * 1024 * 1024 + 123):
    p = tmp_path / "connectome.db"
    p.write_bytes(os.urandom(size))
    return p


def test_reads_the_whole_file_and_says_so(tmp_path):
    p = _file(tmp_path)
    said = []
    with prefetch.warmed(p, log=said.append, chunk=1024 * 1024):
        pass
    assert len(said) == 1 and "connectome.db" in said[0] and "3 MB" in said[0]


@pytest.mark.skipif(sys.platform != "win32", reason="an open handle blocks delete only on Windows")
def test_the_file_stays_open_for_the_whole_block(tmp_path):
    p = _file(tmp_path)
    with prefetch.warmed(p):
        with pytest.raises(PermissionError):
            os.remove(p)                # held open: the cache behind it survives
    os.remove(p)                        # released at the end of the block


def test_missing_file_and_disabled_prefetch_do_nothing(tmp_path, monkeypatch):
    said = []
    with prefetch.warmed(tmp_path / "absent.db", log=said.append):
        pass
    assert said == []
    monkeypatch.setenv("MOUSEDB_PREFETCH", "0")
    with prefetch.warmed(_file(tmp_path), log=said.append):
        pass
    assert said == []
