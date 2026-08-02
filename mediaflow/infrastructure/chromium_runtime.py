from __future__ import annotations

from pathlib import Path

CHROMIUM_EXECUTABLES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
)


def discover_chromium_executable() -> Path | None:
    executable = next((path for path in CHROMIUM_EXECUTABLES if path.is_file()), None)
    return executable.resolve() if executable is not None else None


def find_chromium_executable() -> Path:
    executable = discover_chromium_executable()
    if executable is None:
        raise FileNotFoundError("Chrome or Edge is required for editable web media rendering")
    return executable
