from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class FileAllocation:
    identity: tuple[int, int]
    logical_bytes: int
    allocated_bytes: int
    links: int


def _windows_path(path: str | Path) -> str:
    # Win32 needs an absolute extended path, without reopening every ancestor
    # to resolve it again for each file in an inventory scan.
    value = os.path.abspath(path)
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


@lru_cache(maxsize=1)
def _kernel() -> Any:
    from ctypes import wintypes

    library = ctypes.WinDLL("kernel32", use_last_error=True)
    library.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    library.GetCompressedFileSizeW.restype = wintypes.DWORD
    library.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    library.GetVolumePathNameW.restype = wintypes.BOOL
    library.GetDiskFreeSpaceW.argtypes = [wintypes.LPCWSTR, *([ctypes.POINTER(wintypes.DWORD)] * 4)]
    library.GetDiskFreeSpaceW.restype = wintypes.BOOL
    return library


@lru_cache(maxsize=64)
def _windows_allocation_unit(volume: str) -> int:
    from ctypes import wintypes

    values = [wintypes.DWORD() for _ in range(4)]
    if not _kernel().GetDiskFreeSpaceW(volume, *(ctypes.byref(value) for value in values)):
        raise ctypes.WinError(ctypes.get_last_error())
    unit = values[0].value * values[1].value
    if unit <= 0:
        raise OSError(f"Filesystem allocation unit is unknown: {volume}")
    return unit


def allocation_unit(path: str | Path) -> int:
    selected = Path(path).expanduser().resolve()
    while not selected.exists() and selected.parent != selected:
        selected = selected.parent
    if sys.platform == "win32":
        volume = ctypes.create_unicode_buffer(32768)
        if not _kernel().GetVolumePathNameW(_windows_path(selected), volume, len(volume)):
            raise ctypes.WinError(ctypes.get_last_error())
        return _windows_allocation_unit(volume.value)
    values = os.statvfs(selected)
    unit = values.f_frsize or values.f_bsize
    if unit <= 0:
        raise OSError(f"Filesystem allocation unit is unknown: {selected}")
    return unit


def file_allocation(
    path: str | Path,
    stat_result: os.stat_result | None = None,
    *,
    allocation_unit_bytes: int | None = None,
) -> FileAllocation:
    """Measure file data allocation, with stable filesystem identity (never a content hash).

    Windows reports sparse/compressed storage with GetCompressedFileSizeW; rounding
    to the actual volume cluster size is a conservative bound for ordinary files.
    Shared/reflink extents may be counted more than once, never inferred as free.
    https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-getcompressedfilesizew
    """
    selected = os.fspath(path)
    before = stat_result or os.stat(selected, follow_symlinks=False)
    for _attempt in range(2):
        if not before.st_ino or before.st_nlink <= 0:
            raise OSError(f"Stable filesystem object identity is unknown: {selected}")
        if os.name == "nt":
            from ctypes import wintypes

            high = wintypes.DWORD()
            ctypes.set_last_error(0)
            low = _kernel().GetCompressedFileSizeW(_windows_path(selected), ctypes.byref(high))
            error = ctypes.get_last_error()
            if low == 0xFFFFFFFF and error:
                raise ctypes.WinError(error)
            used = (high.value << 32) | low
            unit = allocation_unit_bytes or allocation_unit(selected)
            allocated = ((used + unit - 1) // unit) * unit
        else:
            blocks = getattr(before, "st_blocks", None)
            if blocks is None or blocks < 0:
                raise OSError(f"Filesystem allocation measurement is unavailable: {selected}")
            allocated = int(blocks) * 512
        after = os.stat(selected, follow_symlinks=False)
        if (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino):
            if os.name == "nt" and not (getattr(after, "st_file_attributes", 0) & (0x800 | 0x200)):
                allocated = max(allocated, ((after.st_size + unit - 1) // unit) * unit)
            return FileAllocation(
                identity=(after.st_dev, after.st_ino),
                logical_bytes=after.st_size,
                allocated_bytes=allocated,
                links=after.st_nlink,
            )
        before = after
    raise OSError(f"Filesystem object changed while measuring allocation: {selected}")


def allocated_growth_bound(path: str | Path, logical_peak_bytes: int, *, files: int = 1) -> int:
    if logical_peak_bytes < 0 or files < 0:
        raise ValueError("Storage allocation estimates must be non-negative")
    if logical_peak_bytes == 0:
        return 0
    unit = allocation_unit(path)
    return ((logical_peak_bytes + unit - 1) // unit + max(0, files - 1)) * unit
