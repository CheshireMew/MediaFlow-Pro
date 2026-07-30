from __future__ import annotations

import os

_FALLBACK_AVAILABLE_MEMORY_BYTES = 4 * 1024**3


def available_physical_memory_bytes() -> int:
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return max(1, int(status.available_physical))
    try:
        sysconf = getattr(os, "sys" + "conf")
        return max(1, int(sysconf("SC_AVPHYS_PAGES") * sysconf("SC_PAGE_SIZE")))
    except (AttributeError, OSError, TypeError, ValueError):
        return _FALLBACK_AVAILABLE_MEMORY_BYTES
