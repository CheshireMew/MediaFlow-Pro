from __future__ import annotations

from pydantic import Field, model_validator

from .model_base import DomainModel, new_id


class HighlightCandidate(DomainModel):
    id: str = Field(default_factory=new_id)
    project_id: str
    asset_id: str
    document_id: str | None = None
    sequence_id: str | None = None
    start_frame: int
    end_frame: int
    title: str
    reason: str = ""
    score: float = 0.0
    selected: bool = True

    @model_validator(mode="after")
    def validate_range(self) -> HighlightCandidate:
        if self.start_frame < 0 or self.end_frame <= self.start_frame:
            raise ValueError("Highlight candidate must have a positive frame range")
        if not self.title.strip():
            raise ValueError("Highlight candidate title cannot be empty")
        return self
