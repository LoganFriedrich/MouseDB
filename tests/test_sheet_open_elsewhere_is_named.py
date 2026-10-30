"""A workbook another program has open is reported by that program's name.

2026-10-29: the nightly import said only "[Errno 13] Permission denied" for a
workbook that was open in Excel on the same computer. The message must say
what to close.
"""
import sys

import pytest

from mousedb import file_holders


@pytest.mark.skipif(sys.platform != "win32", reason="Windows locks files; elsewhere the answer is []")
def test_a_file_held_open_exclusively_is_named(tmp_path):
    import ctypes
    from ctypes import wintypes as W
    f = tmp_path / "Connectome_09_Animal_Tracking.xlsx"
    f.write_bytes(b"x")
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = W.HANDLE
    handle = k.CreateFileW(str(f), 0x80000000, 0, None, 3, 0x80, None)   # read, share nothing
    try:
        with pytest.raises(PermissionError):
            open(f, "rb").read()
        held = file_holders.holders(f)
        assert held and "process" in held[0]
    finally:
        k.CloseHandle(handle)
    assert file_holders.holders(f) == []


def test_the_importer_names_the_program_instead_of_errno_13(tmp_path, monkeypatch):
    import pandas as pd
    from mousedb import importers
    f = tmp_path / "Connectome_09_Animal_Tracking.xlsx"
    f.write_bytes(b"x")

    def locked(*a, **k):
        raise PermissionError(13, "Permission denied", str(f))
    monkeypatch.setattr(pd, "ExcelFile", locked)
    monkeypatch.setattr(file_holders, "holders", lambda p: ["Microsoft Excel (process 42)"])
    imp = importers.ExcelImporter(db=object())     # never reached: the open fails first
    imp.import_cohort_file(f, dry_run=True)
    assert any("open in Microsoft Excel (process 42)" in e for e in imp.errors), imp.errors
