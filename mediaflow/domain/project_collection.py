from __future__ import annotations

from pydantic import Field

from .enums import AssetStatus
from .model_base import DomainModel, new_id, now_ms
from .project import Asset


class ProjectCollectionCandidate(DomainModel):
    asset_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    managed: bool
    status: AssetStatus
    size_bytes: int = Field(ge=0)
    collectable: bool
    reason: str = ""


class ProjectCollectionItem(DomainModel):
    asset_id: str = Field(min_length=1)
    original_path: str = Field(min_length=1)
    original_managed: bool
    collected_path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)


class ProjectCollectionRecord(DomainModel):
    id: str = Field(default_factory=new_id)
    created_at: int = Field(default_factory=now_ms)
    items: list[ProjectCollectionItem] = Field(min_length=1)

    @property
    def total_bytes(self) -> int:
        return sum(item.size_bytes for item in self.items)


class ProjectCollectionPreview(DomainModel):
    candidates: list[ProjectCollectionCandidate]
    collectable_count: int = Field(ge=0)
    total_bytes: int = Field(ge=0)


class ProjectCollectionResult(DomainModel):
    record: ProjectCollectionRecord
    assets: list[Asset]
    using_collected_files: bool
