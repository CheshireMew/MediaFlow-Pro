from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from .model_base import DomainModel, new_id


class MulticamAngle(DomainModel):
    id: str = Field(default_factory=new_id)
    asset_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    source_in: int = Field(ge=0)

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Multicam angle name cannot be empty")
        return normalized


class MulticamCut(DomainModel):
    frame: int = Field(ge=0)
    angle_id: str = Field(min_length=1)


class MulticamGroup(DomainModel):
    id: str = Field(default_factory=new_id)
    sequence_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    program_track_id: str = Field(min_length=1)
    timeline_start: int = Field(ge=0)
    duration: int = Field(gt=0)
    sync_offset: int = Field(ge=0)
    sync_method: Literal["manual", "timecode", "waveform"] = "manual"
    sync_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    audio_strategy: Literal["follow_video", "master_angle"] = "follow_video"
    master_audio_angle_id: str | None = None
    program_audio_track_id: str | None = None
    angles: list[MulticamAngle] = Field(min_length=2, max_length=16)
    cuts: list[MulticamCut] = Field(min_length=1)
    program_clip_ids: list[str] = Field(min_length=1)
    program_audio_clip_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_program(self) -> MulticamGroup:
        angle_ids = [item.id for item in self.angles]
        known_angles = set(angle_ids)
        if len(angle_ids) != len(known_angles):
            raise ValueError("Multicam angles must have unique identifiers")
        if len({item.asset_id for item in self.angles}) != len(self.angles):
            raise ValueError("A multicam group cannot reuse the same source asset")
        frames = [item.frame for item in self.cuts]
        if frames != sorted(set(frames)) or frames[0] != 0:
            raise ValueError("Multicam cuts must be unique, ordered, and begin at frame zero")
        if any(item.frame >= self.duration for item in self.cuts):
            raise ValueError("Multicam cuts must stay inside the program duration")
        if any(item.angle_id not in known_angles for item in self.cuts):
            raise ValueError("Multicam cut references an unknown angle")
        if len(self.program_clip_ids) != len(self.cuts):
            raise ValueError("Every multicam cut must own one program clip")
        if len(self.program_clip_ids) != len(set(self.program_clip_ids)):
            raise ValueError("Multicam program clip identifiers must be unique")
        if self.sync_offset >= self.duration:
            raise ValueError("Multicam sync offset must stay inside the program")
        if self.audio_strategy == "master_angle":
            if self.master_audio_angle_id not in known_angles:
                raise ValueError("Master audio strategy requires a known master angle")
            if self.program_audio_track_id is None or len(self.program_audio_clip_ids) != 1:
                raise ValueError("Master audio strategy requires one continuous program audio clip")
        elif self.master_audio_angle_id is not None:
            raise ValueError("Follow-video audio cannot carry a master angle")
        if self.program_audio_track_id is None and self.program_audio_clip_ids:
            raise ValueError("Program audio clips require an audio track")
        if self.audio_strategy == "follow_video" and self.program_audio_track_id is not None:
            if len(self.program_audio_clip_ids) != len(self.cuts):
                raise ValueError("Follow-video audio requires one audio clip per camera cut")
        return self


class MulticamAngleSyncSpec(DomainModel):
    id: str = Field(default_factory=new_id)
    asset_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    sync_frame: int = Field(ge=0)


class MulticamSyncAnalysis(DomainModel):
    method: Literal["timecode", "waveform"]
    reference_asset_id: str = Field(min_length=1)
    angles: list[MulticamAngleSyncSpec] = Field(min_length=2, max_length=16)
    confidence: float = Field(ge=0.0, le=1.0)
    offset_span_frames: int = Field(ge=0)
