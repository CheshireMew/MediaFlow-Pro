from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CollectedAssetCopy:
    asset_id: str
    path: Path
    size_bytes: int
    sha256: str


class ProjectCollectionStorage(Protocol):
    def content_size(self, source: Path) -> int: ...

    def collect(
        self,
        collection_id: str,
        sources: list[tuple[str, Path]],
    ) -> list[CollectedAssetCopy]: ...

    def verify(self, path: Path, expected_sha256: str) -> bool: ...

    def archive_uncommitted(self, collection_id: str) -> Path | None: ...
