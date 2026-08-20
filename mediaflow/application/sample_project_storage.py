from __future__ import annotations

from pathlib import Path
from typing import Protocol


class SampleProjectStorage(Protocol):
    def prepare_source_directory(self, project_dir: Path) -> Path: ...
    def write_generated_image(self, path: Path, content: bytes) -> None: ...
