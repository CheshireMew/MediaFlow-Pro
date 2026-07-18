from __future__ import annotations

from pathlib import Path


def resolve_project_artifact(project_dir: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project_dir / path


def project_artifact_reference(project_dir: Path, value: str | Path) -> str:
    path = Path(value).resolve()
    return path.relative_to(project_dir.resolve()).as_posix()
