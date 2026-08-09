from __future__ import annotations

from fractions import Fraction
from typing import Any, Literal

from pydantic import Field, computed_field, field_validator, model_validator

from .enums import AssetKind, ClipMediaKind, TrackKind, TransitionKind
from .exports import SubtitleStyle
from .model_base import DomainModel, new_id
from .project import Sequence
from .visual_effects import ClipVisualEffect
from .web_media import WebClipState


def default_clip_media_kind(asset_kind: AssetKind, *, has_audio: bool) -> ClipMediaKind:
    if asset_kind in {AssetKind.VIDEO, AssetKind.WEB}:
        return ClipMediaKind.LINKED_AV if has_audio else ClipMediaKind.VIDEO_ONLY
    if asset_kind == AssetKind.AUDIO:
        return ClipMediaKind.AUDIO_ONLY
    if asset_kind == AssetKind.IMAGE:
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
    subtitle_style: SubtitleStyle | None = None

    @model_validator(mode="after")
    def coherent_subtitle_style(self) -> Track:
        if self.kind != TrackKind.SUBTITLE and self.subtitle_style is not None:
            raise ValueError("Only subtitle tracks can carry a subtitle style")
        return self


class ClipAddRequest(DomainModel):
    track_id: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    timeline_start: int = Field(ge=0)
    source_in: int = Field(ge=0)
    duration: int = Field(gt=0)
    speed_numerator: int = 1
    speed_denominator: int = Field(default=1, gt=0)
    pitch_compensation: bool = True


class FreezeClipAddRequest(DomainModel):
    track_id: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    timeline_start: int = Field(ge=0)
    source_frame: int = Field(ge=0)
    duration: int = Field(gt=0)


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
    source_frame: int | None = Field(default=None, ge=0)
    timeline_offset: int | None = Field(default=None, ge=0)
    transform: ClipTransform
    source: Literal["manual", "auto_reframe", "subject_tracking"] = "manual"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def one_time_anchor(self) -> ClipTransformKeyframe:
        if (self.source_frame is None) == (self.timeline_offset is None):
            raise ValueError(
                "A clip transform keyframe must use exactly one source-frame or timeline-offset anchor"
            )
        return self


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
    freeze_source_frame: int | None = Field(default=None, ge=0)
    transform: ClipTransform = Field(default_factory=ClipTransform)
    transform_keyframes: list[ClipTransformKeyframe] = Field(default_factory=list)
    audio: ClipAudio = Field(default_factory=ClipAudio)
    visual_effects: list[ClipVisualEffect] = Field(default_factory=list)

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
        if self.freeze_source_frame is not None:
            if self.media_kind != ClipMediaKind.VIDEO_ONLY:
                raise ValueError("Freeze clips must be video-only")
            if self.speed_numerator != 1 or self.speed_denominator != 1:
                raise ValueError("Freeze clips cannot change playback speed")
        anchor_modes = {
            "timeline" if item.timeline_offset is not None else "source" for item in self.transform_keyframes
        }
        if len(anchor_modes) > 1:
            raise ValueError("Clip transform keyframes cannot mix time anchor modes")
        frames = [
            item.timeline_offset if item.timeline_offset is not None else item.source_frame
            for item in self.transform_keyframes
        ]
        resolved_frames = [int(frame) for frame in frames if frame is not None]
        if len(resolved_frames) != len(frames) or resolved_frames != sorted(set(resolved_frames)):
            raise ValueError("Clip transform keyframes must have unique ordered anchors")
        if any(
            item.timeline_offset is not None and item.timeline_offset >= self.duration
            for item in self.transform_keyframes
        ):
            raise ValueError("Timeline-anchored transform keyframes must stay inside the clip")
        effect_positions = [item.position for item in self.visual_effects]
        if effect_positions != list(range(len(self.visual_effects))):
            raise ValueError("Clip visual effect positions must be contiguous and ordered")
        if len({item.id for item in self.visual_effects}) != len(self.visual_effects):
            raise ValueError("Clip visual effects must have unique identifiers")
        return self

    @computed_field
    @property
    def timeline_end(self) -> int:
        return self.timeline_start + self.duration

    def validate_source_range(
        self,
        asset_kind: AssetKind,
        source_duration_frames: int,
    ) -> None:
        """Validate the exact source interval consumed by this timeline clip.

        Still images and editable web media intentionally have an unbounded
        presentation duration. Timed media with unknown metadata is accepted
        until probing completes, but every known duration is enforced here.
        """
        if self.freeze_source_frame is not None:
            if asset_kind not in {AssetKind.VIDEO, AssetKind.WEB}:
                raise ValueError("Freeze clips require a video or rendered web source")
            if source_duration_frames > 0 and self.freeze_source_frame >= source_duration_frames:
                raise ValueError("Freeze frame is outside the source asset")
            return
        maximum_duration = self.maximum_timeline_duration(
            asset_kind,
            source_duration_frames,
        )
        if maximum_duration is None:
            return
        if self.duration > maximum_duration:
            direction = "reverse" if self.speed_numerator < 0 else "forward"
            raise ValueError(
                f"Clip source range exceeds the {source_duration_frames}-frame asset "
                f"while playing {direction}"
            )

    def maximum_timeline_duration(
        self,
        asset_kind: AssetKind,
        source_duration_frames: int,
    ) -> int | None:
        """Return the exact largest timeline duration available from the source.

        Images and editable web media are presentation sources and therefore
        unbounded. A non-positive timed-media duration remains unknown until
        probing completes. Every known timed source uses this same calculation
        for validation and frame-clock migration.
        """

        if (
            self.freeze_source_frame is not None
            or asset_kind in {AssetKind.IMAGE, AssetKind.WEB}
            or source_duration_frames <= 0
        ):
            return None
        if self.source_in >= source_duration_frames:
            return 0
        available_source_frames = (
            source_duration_frames - self.source_in if self.speed_numerator > 0 else self.source_in + 1
        )
        speed = Fraction(abs(self.speed_numerator), self.speed_denominator)
        return available_source_frames * speed.denominator // speed.numerator


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

    def effective_tracks(self, kind: TrackKind) -> list[Track]:
        """Return the enabled tracks of one kind after applying kind-local solo."""
        candidates = [
            track
            for track in sorted(self.tracks, key=lambda item: (item.position, item.id))
            if track.kind == kind and track.enabled
        ]
        soloed = [track for track in candidates if track.solo]
        return soloed or candidates


class TimelineRevisionConflict(RuntimeError):
    """A timeline mutation was based on a sequence revision that is no longer current."""

    def __init__(self, sequence_id: str, *, expected: int, actual: int):
        self.sequence_id = sequence_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Timeline changed while editing sequence {sequence_id}: "
            f"expected revision {expected}, current revision {actual}"
        )


class TimelineMergeConflict(RuntimeError):
    """Two concurrent mutations changed the same timeline entity."""

    def __init__(self, entity: str, entity_id: str):
        self.entity = entity
        self.entity_id = entity_id
        super().__init__(f"Concurrent timeline edits conflict on {entity} {entity_id}")
