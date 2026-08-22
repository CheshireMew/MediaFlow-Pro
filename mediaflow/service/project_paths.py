from __future__ import annotations

from pathlib import Path


def project_path(value: str | None) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ValueError("project is required")
    path = Path(text).expanduser().resolve()
    return path.parent if path.name == "project.mfp" else path
