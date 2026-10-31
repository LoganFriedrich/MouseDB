"""A workbook someone has open is imported anyway, from what was last saved.

2026-10-31: the nightly import failed on a cohort sheet for as long as it stayed
open in Excel, and asked a person to close it. They should not have to. Measured
on the real open workbook: Python's open() is refused and a read that allows
every other use of the file succeeds. The handle below reproduces that state.
"""
import sys

import openpyxl
import pandas as pd
import pytest

from mousedb import file_holders

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows locks files")


def _workbook(tmp_path):
    f = tmp_path / "Connectome_09_Animal_Tracking.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "1_Weight"
    wb.active["A1"] = "saved value"
    wb.save(f)
    return f


def _held_like_excel(f, share):
    import ctypes
    from ctypes import wintypes as W
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = W.HANDLE
    k.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.DWORD, W.HANDLE]
    k.CloseHandle.argtypes = [W.HANDLE]
    GENERIC_READ, DELETE = 0x80000000, 0x10000
    return k, k.CreateFileW(str(f), GENERIC_READ | DELETE, share, None, 3, 0x80, None)


def test_a_file_nobody_holds_is_read_from_its_path(tmp_path):
    f = _workbook(tmp_path)
    assert file_holders.readable_workbook(f) == f


@windows_only
def test_a_workbook_held_like_excel_holds_it_can_be_read(tmp_path, capsys):
    f = _workbook(tmp_path)
    k, handle = _held_like_excel(f, share=7)
    try:
        with pytest.raises(PermissionError):
            open(f, "rb").read()
        source = file_holders.readable_workbook(f)
        assert pd.ExcelFile(source).sheet_names == ["1_Weight"]
        source.seek(0)
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
        assert wb["1_Weight"]["A1"].value == "saved value"
        wb.close()
    finally:
        k.CloseHandle(handle)
    assert "last saved version" in capsys.readouterr().out


@windows_only
def test_a_file_nothing_can_read_still_raises_for_the_caller_to_name(tmp_path):
    f = _workbook(tmp_path)
    k, handle = _held_like_excel(f, share=0)
    try:
        with pytest.raises(PermissionError):
            file_holders.readable_workbook(f)
    finally:
        k.CloseHandle(handle)
