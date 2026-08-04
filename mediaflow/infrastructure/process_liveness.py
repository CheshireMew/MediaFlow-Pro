from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Any, cast

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_INVALID_PARAMETER = 87
_STILL_ACTIVE = 259
_WINDOWS_CTYPES = cast(Any, ctypes)


def process_is_alive(pid: int) -> bool:
    """Return whether a process is alive without sending it a signal on Windows.

    An access or query failure is treated as alive so lifecycle cleanup remains
    conservative when the operating system cannot prove that the owner exited.
    """

    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_process_is_alive(pid: int) -> bool:
    kernel32 = _WINDOWS_CTYPES.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    open_process.restype = wintypes.HANDLE
    get_exit_code_process = kernel32.GetExitCodeProcess
    get_exit_code_process.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    get_exit_code_process.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(
        _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not handle:
        return _WINDOWS_CTYPES.get_last_error() != _ERROR_INVALID_PARAMETER
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code_process(
            handle,
            ctypes.byref(exit_code),
        ):
            return True
        return exit_code.value == _STILL_ACTIVE
    finally:
        close_handle(handle)
