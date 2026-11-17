"""How much memory this computer has left, for clear messages when a step runs out of it.

Windows hands out memory up to a fixed ceiling (RAM plus the page file). Once other programs have used it,
a request for even a few hundred MB fails, however much the step "should" need. The page file lives on C:,
so a nearly full C: stops Windows from making it bigger.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
from ctypes import wintypes


class _Status(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def available_mb() -> tuple[int, int] | None:
    """(free RAM, memory Windows will still hand out) in MB, or None where this cannot be read."""
    if sys.platform != "win32":
        return None
    status = _Status()
    status.dwLength = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullAvailPhys // 1048576), int(status.ullAvailPageFile // 1048576)


def system_drive_free_gb() -> float | None:
    try:
        return shutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\").free / 1024**3
    except OSError:
        return None


def low_memory_message(what: str) -> str:
    """A plain explanation, with what to do, for `what` running out of memory."""
    left = available_mb()
    text = f"There is not enough free memory for {what}"
    if left is not None:
        text += f": Windows has only {left[1]:,} MB left to give (free RAM {left[0]:,} MB)"
    text += ". Close other programs (web browsers use a lot) and press Retry; the job carries on from where it stopped."
    free = system_drive_free_gb()
    if free is not None and free < 2:
        text += (f" Drive C: has only {free:.1f} GB free, which stops Windows from enlarging its page file; "
                 "freeing space on C: gives it room.")
    return text
