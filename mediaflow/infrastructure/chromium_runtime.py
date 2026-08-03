from __future__ import annotations

import os
import shutil
from pathlib import Path

from mediaflow.environment import CHROMIUM_EXECUTABLE_VARIABLE, configured_path


def _system_candidates() -> list[Path]:
    candidates: list[Path] = []
    playwright_root = configured_path("PLAYWRIGHT_BROWSERS_PATH")
    if playwright_root is not None and playwright_root.is_dir():
        candidates.extend(
            sorted(
                playwright_root.glob("chromium-*/chrome-win*/chrome.exe"),
                reverse=True,
            )
        )
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(variable, "").strip()
        if not value:
            continue
        root = Path(value).expanduser()
        candidates.extend(
            (
                root / "Google/Chrome/Application/chrome.exe",
                root / "Microsoft/Edge/Application/msedge.exe",
            )
        )
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(
            Path(local_app_data).expanduser()
            / "Google/Chrome/Application/chrome.exe"
        )
    for command in ("chrome", "msedge", "chromium"):
        executable = shutil.which(command)
        if executable:
            candidates.append(Path(executable))
    return candidates


def discover_chromium_executable() -> Path | None:
    configured = configured_path(CHROMIUM_EXECUTABLE_VARIABLE)
    if configured is not None:
        return configured if configured.is_file() else None
    executable = next((path for path in _system_candidates() if path.is_file()), None)
    return executable.resolve() if executable is not None else None


def find_chromium_executable() -> Path:
    executable = discover_chromium_executable()
    if executable is None:
        raise FileNotFoundError(
            "Chrome, Edge, or Playwright Chromium is required for editable web media "
            "rendering. Set MEDIAFLOW_CHROMIUM in .env to select an executable."
        )
    return executable
