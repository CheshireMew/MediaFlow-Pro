from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_CONTRACT = ROOT / "runtime.lock.json"


@dataclass(frozen=True, slots=True)
class RuntimeContract:
    shotcut_version: str
    melt_version: str
    ffmpeg_version: str
    qt_version: str

    def shotcut_directory(self, tools_root: Path) -> Path:
        return (
            tools_root
            / "deps"
            / f"shotcut-{self.shotcut_version}"
            / "Shotcut"
        )


def load_runtime_contract(
    path: str | Path = DEFAULT_RUNTIME_CONTRACT,
) -> RuntimeContract:
    source = Path(path).resolve()
    document: Any = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("Runtime contract schema is not supported")
    windows = document.get("windows")
    if not isinstance(windows, dict):
        raise ValueError("Runtime contract windows section is missing")
    shotcut = windows.get("shotcut")
    qt = windows.get("qt")
    if not isinstance(shotcut, dict) or not isinstance(qt, dict):
        raise ValueError("Runtime contract tool sections are missing")
    values = {
        "shotcut_version": shotcut.get("version"),
        "melt_version": shotcut.get("melt_version"),
        "ffmpeg_version": shotcut.get("ffmpeg_version"),
        "qt_version": qt.get("version"),
    }
    missing = [name for name, value in values.items() if not str(value or "")]
    if missing:
        raise ValueError(
            "Runtime contract fields are missing: " + ", ".join(missing)
        )
    return RuntimeContract(
        **{name: str(value) for name, value in values.items()}
    )
