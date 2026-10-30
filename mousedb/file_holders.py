"""Which program has a file open -- so a "permission denied" can say what to close.

WHY (2026-10-29): the nightly sheet import failed on one cohort's workbook with
"[Errno 13] Permission denied". The cause was ordinary -- the workbook was open
in Excel on the same computer, and Excel locks a synced workbook while it is
open -- but the message gave a person nothing to act on. Windows' Restart
Manager (the part of Windows that says "close these programs" before an
install) can name the program holding a file, without administrator rights.

Windows only. Anywhere else, or if Windows cannot say, the answer is an empty
list and the caller keeps its original message: this never turns one failure
into two.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List


def holders(path) -> List[str]:
    """Programs that have ``path`` open, as "Microsoft Excel (process 1234)"."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes as W

        rm = ctypes.WinDLL("rstrtmgr")

        class _Unique(ctypes.Structure):
            _fields_ = [("pid", W.DWORD), ("start", W.FILETIME)]

        class _Info(ctypes.Structure):
            _fields_ = [("process", _Unique), ("app", W.WCHAR * 256), ("service", W.WCHAR * 64),
                        ("type", ctypes.c_int), ("status", W.ULONG), ("session", W.DWORD),
                        ("restartable", W.BOOL)]

        session, key = W.DWORD(), ctypes.create_unicode_buffer(64)
        if rm.RmStartSession(ctypes.byref(session), 0, key) != 0:
            return []
        try:
            files = (W.LPCWSTR * 1)(str(Path(path)))
            if rm.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
                return []
            needed, count, reason = W.UINT(), W.UINT(16), W.DWORD()
            found = (_Info * 16)()
            if rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), found,
                            ctypes.byref(reason)) != 0:
                return []
            return ["%s (process %d)" % (found[i].app or "a program", found[i].process.pid)
                    for i in range(count.value)]
        finally:
            rm.RmEndSession(session)
    except Exception:
        return []
