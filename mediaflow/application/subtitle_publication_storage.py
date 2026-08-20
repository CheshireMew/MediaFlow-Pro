from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SubtitleOutputSnapshot:
    path: Path
    existed: bool
    content: bytes


class SubtitlePublicationStorage(Protocol):
    def list_files(self, root: Path, pattern: str) -> tuple[Path, ...]: ...

    def exists(self, path: Path) -> bool: ...

    def move(self, source: Path, destination: Path) -> None: ...

    def snapshot(self, output: Path) -> SubtitleOutputSnapshot: ...

    def changed(self, snapshot: SubtitleOutputSnapshot) -> bool: ...

    def publish(self, output: Path, content: bytes) -> bool: ...

    def rollback(
        self,
        snapshot: SubtitleOutputSnapshot,
        *,
        project_dir: Path,
        document_id: str,
        error: BaseException,
    ) -> None: ...
