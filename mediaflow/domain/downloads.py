from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from mediaflow.domain.model_base import DomainModel


class DownloadEntry(DomainModel):
    """One selectable media item and the exact yt-dlp selection needed for it."""

    index: int = Field(ge=1)
    media_id: str = ""
    title: str
    page_url: str
    download_url: str
    selector: int | None = Field(default=None, ge=1)
    duration: float = Field(default=0.0, ge=0.0)
    uploader: str = ""
    thumbnail: str = ""
    available: bool = True
    unavailable_reason: str = ""

    @model_validator(mode="after")
    def validate_download_target(self) -> DownloadEntry:
        if self.available and (not self.page_url.strip() or not self.download_url.strip()):
            raise ValueError("Available download entries require page and download URLs")
        if not self.available and not self.unavailable_reason.strip():
            raise ValueError("Unavailable download entries require a reason")
        return self


class DownloadPlan(DomainModel):
    """Canonical output of URL analysis consumed by every download surface."""

    source_url: str
    kind: Literal["single", "collection"]
    media_id: str = ""
    title: str
    extractor: str
    thumbnail: str = ""
    duration: float = Field(default=0.0, ge=0.0)
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    fps: float = Field(default=0.0, ge=0.0)
    available_heights: list[int] = Field(default_factory=list)
    collection_title: str = ""
    entries: list[DownloadEntry]

    @field_validator("available_heights")
    @classmethod
    def normalize_available_heights(cls, values: list[int]) -> list[int]:
        if any(height <= 0 for height in values):
            raise ValueError("Available download heights must be positive")
        return sorted(set(values), reverse=True)

    @model_validator(mode="after")
    def validate_plan(self) -> DownloadPlan:
        if not self.source_url.strip():
            raise ValueError("Download plans require a source URL")
        if not self.entries:
            raise ValueError("Download plans require at least one entry")
        if self.kind == "single":
            if len(self.entries) != 1:
                raise ValueError("Single download plans require exactly one entry")
            if self.collection_title:
                raise ValueError("Single download plans cannot have a collection title")
        elif not self.collection_title.strip():
            raise ValueError("Collection download plans require a collection title")
        indices = [entry.index for entry in self.entries]
        if len(indices) != len(set(indices)):
            raise ValueError("Download entry indices must be unique")
        return self


class DownloadRequest(DomainModel):
    """Persistable command submitted to the download task handler."""

    entry: DownloadEntry
    collection_title: str = ""
    resolution: str = "best"
    codec: Literal["best", "avc"] = "avc"
    download_subtitles: bool = False
    subtitle_languages: list[str] = Field(default_factory=lambda: ["en", "zh"])
    filename_prefix: str = ""
    output_directory: str

    @field_validator("output_directory")
    @classmethod
    def normalize_output_directory(cls, value: str) -> str:
        selected = value.strip()
        if not selected:
            raise ValueError("Download output directory cannot be empty")
        directory = Path(selected).expanduser()
        if not directory.is_absolute():
            raise ValueError("Download output directory must be absolute")
        return str(directory.resolve())

    @model_validator(mode="after")
    def validate_request(self) -> DownloadRequest:
        if not self.entry.available:
            raise ValueError("Unavailable download entries cannot be submitted")
        if not self.resolution.strip():
            raise ValueError("Download resolution cannot be empty")
        return self
