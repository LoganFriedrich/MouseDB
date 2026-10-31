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

Also here: ``readable_workbook``, which reads a workbook that Excel has open.
WHY (2026-10-31): a person keeping a sheet open must not stop the import, and
closing it is not their job. Excel holds the file so that only a reader who also
tolerates it being renamed or deleted may open it. Python's open() does not offer
that, so it gets "Permission denied"; asking Windows directly, with every sharing
option on, reads the file and changes nothing.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import List, Union


def _read_sharing_everything(path) -> bytes:
    """The whole file, opened read-only and allowing every other use of it."""
    import ctypes
    import msvcrt
    import os
    from ctypes import wintypes as W

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = W.HANDLE
    k.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, W.LPVOID, W.DWORD, W.DWORD, W.HANDLE]
    GENERIC_READ, SHARE_READ_WRITE_DELETE, OPEN_EXISTING, NORMAL = 0x80000000, 7, 3, 0x80
    handle = k.CreateFileW(str(path), GENERIC_READ, SHARE_READ_WRITE_DELETE, None,
                           OPEN_EXISTING, NORMAL, None)
    if handle == W.HANDLE(-1).value:
        raise PermissionError(13, "Permission denied", str(path))
    with os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDONLY), "rb") as f:
        return f.read()


def readable_workbook(path) -> Union[Path, io.BytesIO]:
    """Something pandas or openpyxl can open: the path, or the file's saved
    contents when another program has it open.

    Only what was last SAVED can be read: changes on someone's screen are not in
    the file, and arrive with the first import after they save.

    Raises PermissionError only if the file truly cannot be read, so the caller's
    message naming the program still applies then.
    """
    path = Path(path)
    try:
        with open(path, "rb"):
            return path
    except PermissionError:
        if sys.platform != "win32":
            raise
    contents = _read_sharing_everything(path)
    print("  %s is open in another program: read its last saved version" % path.name)
    return io.BytesIO(contents)


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
