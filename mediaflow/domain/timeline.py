from __future__ import annotations

from fractions import Fraction
from typing import Any, Literal

from pydantic import Field, computed_field, field_validator, model_validator

from .enums import AssetKind, ClipMediaKind, TrackKind, TransitionKind
from .model_base import DomainModel, new_id
from .project import Sequence
from .web_media import WebClipState


def default_clip_media_kind(asset_kind: AssetKind, *, has_audio: bool) -> ClipMediaKind:
    if asset_kind == AssetKind.VIDEO:
        return ClipMediaKind.LINKED_AV if has_audio else ClipMediaKind.VIDEO_ONLY
    if asset_kind == AssetKind.AUDIO:
        return ClipMediaKind.AUDIO_ONLY
    if asset_kind in {AssetKind.IMAGE, AssetKind.WEB}:
        return ClipMediaKind.VIDEO_ONLY
    raise ValueError("Subtitle assets are placed as subtitle documents, not clips")


def compatible_track_kinds(media_kind: ClipMediaKind) -> tuple[TrackKind, ...]:
    if media_kind == ClipMediaKind.AUDIO_ONLY:
        return (TrackKind.AUDIO,)
    return (TrackKind.VIDEO,)


class TimelineMarker(DomainModel):
    id: str = Field(default_factory=new_id)
    sequence_id: str
    frame: int
    name: str = ""
    color: str = "#4ea1ff"

    @field_validator("frame")
    @classmethod
    def non_negative_frame(cls, value: int) -> int:
        if value < 0:
            raise ValueError("Marker frame cannot be negative")
        return value


class TimelineRange(DomainModel):
    id: str = Field(default_factory=new_id)
    sequence_id: str
    start_frame: int
    end_frame: int
    name: str = ""
    color: str = "#4ea1ff"

    @model_validator(mode="after")
    def positive_range(self) -> TimelineRange:
        if self.start_frame < 0 or self.end_frame <= self.start_frame:
            raise ValueError("Timeline range must have a positive frame span")
        return self


class Track(DomainModel):
    id: str = Field(default_factory=new_id)
    sequence_id: str
    name: str
    kind: TrackKind
    position: int
    enabled: bool = True
    locked: bool = False
    muted: bool = False
    solo: bool = False
    audio_bus_id: str | None = None
    linked_audio_track_id: str | None = None
    primary_dialogue: bool = False


class ClipTransform(DomainModel):
    x: float = 0.0
    y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0
    crop_left: float = 0.0
    crop_top: float = 0.0
    crop_right: float = 0.0
    crop_bottom: float = 0.0
    opacity: float = 1.0

    @field_validator("crop_left", "crop_top", "crop_right", "crop_bottom")
    @classmethod
    def crop_fraction(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("Crop values must be normalized between 0 and 1")
        return value

    @field_validator("opacity")
    @classmethod
    def opacity_fraction(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("Opacity must be normalized between 0 and 1")
        return value


class ClipAudio(DomainModel):
    gain_db: float = 0.0
    pan: float = 0.0
    fade_in_frames: int = 0
    fade_out_frames: int = 0

    @field_validator("pan")
    @classmethod
    def normalized_pan(cls, value: float) -> float:
        if not -1.0 <= value <= 1.0:
            raise ValueError("Pan must be between -1 and 1")
        return value


class ClipTransformKeyframe(DomainModel):
    source_frame: int = Field(ge=0)
    transform: ClipTransform
    source: Literal["manual", "auto_reframe", "subject_tracking"] = "manual"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class Clip(DomainModel):
    id: str = Field(default_factory=new_id)
    track_id: str
    asset_id: str
    timeline_start: int
    source_in: int
    duration: int
    media_kind: ClipMediaKind
    speed_numerator: int = 1
    speed_denominator: int = 1
    pitch_compensation: bool = True
    transform: ClipTransform = Field(default_factory=ClipTransform)
    transform_keyframes: list[ClipTransformKeyframe] = Field(default_factory=list)
    audio: ClipAudio = Field(default_factory=ClipAudio)

    @model_validator(mode="after")
    def validate_clip(self) -> Clip:
        if self.timeline_start < 0 or self.source_in < 0:
            raise ValueError("Clip frame positions cannot be negative")
        if self.duration <= 0:
            raise ValueError("Clip duration must be positive")
        if self.speed_numerator == 0 or self.speed_denominator <= 0:
            raise ValueError("Clip speed must be non-zero with a positive denominator")
        speed = abs(Fraction(self.speed_numerator, self.speed_denominator))
        if speed < Fraction(1, 4) or speed > 4:
            raise ValueError("Clip speed must be between 0.25x and 4x")
        frames = [item.source_frame for item in self.transform_keyframes]
        if frames != sorted(set(frames)):
            raise ValueError("Clip transform keyframes must have unique ordered source frames")
        return self

    @computed_field
    @property
    def timeline_end(self) -> int:
        return self.timeline_start + self.duration


class Transition(DomainModel):
    id: str = Field(default_factory=new_id)
    track_id: str
    left_clip_id: str
    right_clip_id: str
    kind: TransitionKind
    duration: int
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("duration")
    @classmethod
    def positive_duration(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Transition duration must be positive")
        return value


class CompoundClip(DomainModel):
    """One persisted editorial unit backed by adjacent source clips."""

    id: str = Field(default_factory=new_id)
    sequence_id: str
    name: str = "复合片段"
    clip_ids: list[str]

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value: str) -> str:
        return value.strip() or "复合片段"

    @model_validator(mode="after")
    def valid_members(self) -> CompoundClip:
        if len(self.clip_ids) < 2:
            raise ValueError("A compound clip must contain at least two clips")
        if len(set(self.clip_ids)) != len(self.clip_ids):
            raise ValueError("A compound clip cannot contain the same clip twice")
        return self


class TimelineState(DomainModel):
    sequence: Sequence
    tracks: list[Track] = Field(default_factory=list)
    clips: list[Clip] = Field(default_factory=list)
    compounds: list[CompoundClip] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    markers: list[TimelineMarker] = Field(default_factory=list)
    ranges: list[TimelineRange] = Field(default_factory=list)
    web_states: dict[str, WebClipState] = Field(default_factory=dict)

    @property
    def duration_frames(self) -> int:
        """Exclusive media end frame for preview, editing, and export boundaries."""
        return max((clip.timeline_end for clip in self.clips), default=0)

    def clips_for_track(self, track_id: str) -> list[Clip]:
        return sorted(
            (clip for clip in self.clips if clip.track_id == track_id),
            key=lambda clip: (clip.timeline_start, clip.id),
        )
