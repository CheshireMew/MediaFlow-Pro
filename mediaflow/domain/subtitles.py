from __future__ import annotations

from pydantic import Field, model_validator

from .model_base import DomainModel, new_id, now_ms


class SubtitleSegment(DomainModel):
    id: str = Field(default_factory=new_id)
    document_id: str
    source_segment_id: str | None = None
    start_frame: int
    end_frame: int
    text: str
    speaker: str | None = None
    confidence: float | None = None

    @model_validator(mode="after")
    def validate_times(self) -> SubtitleSegment:
        if self.start_frame < 0 or self.end_frame <= self.start_frame:
            raise ValueError("Subtitle segment must have a positive frame range")
        return self


class SubtitleDocument(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    asset_id: str
    media_asset_id: str | None = None
    language: str
    source_document_id: str | None = None
    is_source: bool = True
    created_at: int = Field(default_factory=now_ms)


class SubtitlePlacement(DomainModel):
    id: str = Field(default_factory=new_id)
    track_id: str
    segment_id: str
    clip_id: str | None = None
    start_frame: int
    end_frame: int
    text_override: str | None = None
