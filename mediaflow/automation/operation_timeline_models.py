from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from mediaflow.domain.enums import (
    TrackKind,
    TransitionKind,
    VisualEffectKind,
)
from mediaflow.domain.exports import SubtitleStyle
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.portable_timeline import PortableTimelineProfile
from mediaflow.domain.project import Asset
from mediaflow.domain.timeline import (
    Clip,
    ClipAddRequest,
    ClipAudio,
    ClipTransform,
    FreezeClipAddRequest,
    TimelineMarker,
    TimelineState,
    Track,
    Transition,
)

from .operation_model_common import SequenceArguments


class PortableTimelineArguments(SequenceArguments):
    timeline_path: str = Field(min_length=1)


class TimelineTrackAddArguments(SequenceArguments):
    kind: TrackKind
    name: str | None = None


class TimelineClipAddArguments(ClipAddRequest):
    sequence_id: str | None = None


class TimelineClipBatchAddArguments(SequenceArguments):
    clips: list[ClipAddRequest] = Field(min_length=1, max_length=1000)


class TimelineFreezeClipAddArguments(FreezeClipAddRequest):
    sequence_id: str | None = None


class TimelineClipMoveArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    timeline_start: int = Field(ge=0)
    track_id: str | None = None


class TimelineClipSplitArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    split_frame: int = Field(gt=0)


class TimelineClipDeleteArguments(SequenceArguments):
    clip_ids: list[str] = Field(min_length=1)
    ripple: bool | None = None


class TimelineTransitionAddArguments(SequenceArguments):
    left_clip_id: str = Field(min_length=1)
    right_clip_id: str = Field(min_length=1)
    kind: TransitionKind
    duration: int = Field(gt=0)


class TimelineTransitionUpdateArguments(SequenceArguments):
    transition_id: str = Field(min_length=1)
    kind: TransitionKind
    duration: int = Field(gt=0)
    parameters: dict[str, JsonValue] | None = None


class TimelineTransitionRemoveArguments(SequenceArguments):
    transition_id: str = Field(min_length=1)


class TimelineMarkerAddArguments(SequenceArguments):
    frame: int = Field(ge=0)
    name: str = ""
    color: str = Field(default="#4ea1ff", pattern="^#[0-9a-fA-F]{6}$")


class TimelineMarkerUpdateArguments(SequenceArguments):
    marker_id: str = Field(min_length=1)
    frame: int = Field(ge=0)
    name: str = ""
    color: str = Field(pattern="^#[0-9a-fA-F]{6}$")


class TimelineMarkerRemoveArguments(SequenceArguments):
    marker_id: str = Field(min_length=1)


class SubtitleTrackStyleUpdateArguments(SequenceArguments):
    track_id: str = Field(min_length=1)
    style: SubtitleStyle


class TimelineClipTransformArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    transform: ClipTransform


class TimelineClipAudioArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    audio: ClipAudio


class TimelineClipReplaceSourceArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)


class TimelineClipVisualEffectAddArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    kind: VisualEffectKind
    resource_asset_id: str | None = Field(default=None, min_length=1)


class TimelineClipVisualEffectUpdateArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)
    enabled: bool
    parameters: dict[str, float]


class TimelineClipVisualEffectMoveArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)
    position: int = Field(ge=0)


class TimelineClipVisualEffectRemoveArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)


class TimelineResult(DomainModel):
    timeline: TimelineState


class PortableTimelineInspectResult(DomainModel):
    timeline_path: str
    timeline_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    project_id: str
    profile: PortableTimelineProfile
    duration_seconds: float = Field(gt=0)
    source_count: int = Field(ge=0)
    track_count: int = Field(gt=0)
    clip_count: int = Field(ge=0)
    marker_count: int = Field(ge=0)
    mediaflow_compatible: Literal[True] = True


class PortableTimelineImportResult(PortableTimelineInspectResult):
    timeline: TimelineState
    source_assets: dict[str, Asset]
    subtitle_document_ids: list[str]


class TrackResult(DomainModel):
    track: Track


class ClipResult(DomainModel):
    clip: Clip


class ClipsResult(DomainModel):
    clips: list[Clip]


class TransitionResult(DomainModel):
    transition: Transition


class MarkerResult(DomainModel):
    marker: TimelineMarker
