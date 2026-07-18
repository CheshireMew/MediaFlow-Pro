from __future__ import annotations

from pydantic import Field, model_validator

from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.project import SequenceInOut


class SequenceBoundaryAnalysis(DomainModel):
    sequence_id: str
    snapshot_hash: str
    duration_frames: int = Field(ge=1)
    suggested: SequenceInOut
    speech_in_frame: int | None = Field(default=None, ge=0)
    speech_out_frame: int | None = Field(default=None, ge=1)
    black_in_frame: int | None = Field(default=None, ge=0)
    black_out_frame: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def bounds_fit_duration(self) -> SequenceBoundaryAnalysis:
        values = [
            self.suggested.in_frame,
            self.suggested.out_frame,
            self.speech_in_frame,
            self.speech_out_frame,
            self.black_in_frame,
            self.black_out_frame,
        ]
        if any(value is not None and value > self.duration_frames for value in values):
            raise ValueError("Sequence boundary analysis exceeds the sequence duration")
        return self
