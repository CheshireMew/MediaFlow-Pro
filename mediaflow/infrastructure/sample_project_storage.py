from __future__ import annotations

from pathlib import Path

from mediaflow.atomic_file import atomic_write_bytes


class LocalSampleProjectStorage:
    def prepare_source_directory(self, project_dir: Path) -> Path:
        source_dir = project_dir / "sources"
        source_dir.mkdir(parents=True, exist_ok=True)
        return source_dir

    def write_generated_image(self, path: Path, content: bytes) -> None:
        atomic_write_bytes(path, content)
