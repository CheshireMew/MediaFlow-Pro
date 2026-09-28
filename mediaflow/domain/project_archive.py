from __future__ import annotations

from pydantic import Field

from .model_base import DomainModel, now_ms


class ProjectArchiveFile(DomainModel):
    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)


class ProjectArchiveManifest(DomainModel):
    format: str = "mediaflow-project-archive"
    version: int = 1
    project_id: str = Field(min_length=1)
    project_name: str = Field(min_length=1)
    project_schema_version: int = Field(gt=0)
    content_revision: int = Field(ge=0)
    created_at: int = Field(default_factory=now_ms)
    files: list[ProjectArchiveFile] = Field(min_length=1)


class ProjectArchiveResult(DomainModel):
    destination: str = Field(min_length=1)
    manifest_path: str = Field(min_length=1)
    file_count: int = Field(gt=0)
    total_bytes: int = Field(ge=0)
    project_id: str = Field(min_length=1)
    content_revision: int = Field(ge=0)
