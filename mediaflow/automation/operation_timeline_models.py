from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from mediaflow.domain.color_scopes import ColorScopeAnalysis
from mediaflow.domain.enums import (
    MaskShapeKind,
    TrackKind,
    TransitionKind,
    VisualEffectKind,
)
from mediaflow.domain.exports import SubtitleStyle
from mediaflow.domain.interchange import InterchangeTimelineSummary
from mediaflow.domain.keyframes import KeyframeCurve
from mediaflow.domain.masks import MaskGeometry
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.multicam import (
    MulticamAngleSyncSpec,
    MulticamGroup,
    MulticamSyncAnalysis,
)
from mediaflow.domain.portable_timeline import PortableTimelineProfile
from mediaflow.domain.project import Asset, Sequence
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


class InterchangeTimelineArguments(SequenceArguments):
    timeline_path: str = Field(min_length=1)
    frame_rate: float | None = Field(default=None, gt=0)
    media_mappings: dict[str, str] = Field(default_factory=dict)


class InterchangeTimelineImportArguments(InterchangeTimelineArguments):
    name: str | None = Field(default=None, min_length=1, max_length=160)


class TimelineTrackAddArguments(SequenceArguments):
    kind: TrackKind
    name: str | None = None


class MulticamGroupCreateArguments(SequenceArguments):
    name: str = Field(min_length=1, max_length=120)
    angles: list[MulticamAngleSyncSpec] = Field(min_length=2, max_length=16)
    timeline_start: int = Field(ge=0)
    duration: int = Field(gt=0)
    sync_offset: int = Field(default=0, ge=0)
    initial_angle_id: str | None = Field(default=None, min_length=1)
    sync_method: Literal["manual", "timecode", "waveform"] = "manual"
    sync_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    audio_strategy: Literal["follow_video", "master_angle"] = "follow_video"
    master_audio_angle_id: str | None = Field(default=None, min_length=1)


class MulticamSyncAnalyzeArguments(SequenceArguments):
    asset_ids: list[str] = Field(min_length=2, max_length=16)
    method: Literal["timecode", "waveform"]


class MulticamAngleSwitchArguments(SequenceArguments):
    group_id: str = Field(min_length=1)
    angle_id: str = Field(min_length=1)
    timeline_frame: int = Field(ge=0)


class MulticamGroupResult(DomainModel):
    group: MulticamGroup


class MulticamGroupListResult(DomainModel):
    groups: list[MulticamGroup]


MulticamSyncAnalyzeResult = MulticamSyncAnalysis


class ColorScopeAnalyzeArguments(SequenceArguments):
    frame: int = Field(ge=0)
    bins: int = Field(default=64, ge=16, le=128)
    use_proxies: bool = True


ColorScopeAnalyzeResult = ColorScopeAnalysis


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


class TimelineClipTransformKeyframeSetArguments(TimelineClipTransformArguments):
    timeline_offset: int = Field(ge=0)
    curve: KeyframeCurve = Field(default_factory=KeyframeCurve)


class TimelineClipTransformKeyframeRemoveArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    timeline_offset: int = Field(ge=0)


class TimelineClipTransformKeyframeMoveArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    old_timeline_offset: int = Field(ge=0)
    new_timeline_offset: int = Field(ge=0)


class TimelineClipTransformKeyframeRetimeArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    timeline_offsets: list[int] = Field(min_length=1)
    anchor_offset: int = Field(ge=0)
    scale: float = Field(gt=0)


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


class TimelineClipVisualEffectKeyframeSetArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)
    field_id: str = Field(min_length=1)
    timeline_offset: int = Field(ge=0)
    value: float
    curve: KeyframeCurve = Field(default_factory=KeyframeCurve)


class TimelineClipVisualEffectKeyframeRemoveArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)
    field_id: str = Field(min_length=1)
    timeline_offset: int = Field(ge=0)


class TimelineClipVisualEffectKeyframeMoveArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)
    field_id: str = Field(min_length=1)
    old_timeline_offset: int = Field(ge=0)
    new_timeline_offset: int = Field(ge=0)


class TimelineClipVisualEffectKeyframeRetimeArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)
    field_id: str = Field(min_length=1)
    timeline_offsets: list[int] = Field(min_length=1)
    anchor_offset: int = Field(ge=0)
    scale: float = Field(gt=0)


class TimelineClipVisualEffectMaskAssignArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)
    mask_id: str | None = Field(default=None, min_length=1)


class TimelineClipMaskAddArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    kind: MaskShapeKind
    name: str = Field(min_length=1, max_length=120)
    geometry: MaskGeometry
    combine_mode: Literal["replace", "add", "subtract", "intersect"] = "replace"


class TimelineClipMaskUpdateArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    mask_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=120)
    enabled: bool
    inverted: bool
    feather: int = Field(ge=0, le=1000)
    feather_passes: int = Field(ge=1, le=10)
    opacity: float = Field(ge=0.0, le=1.0)
    geometry: MaskGeometry
    combine_mode: Literal["replace", "add", "subtract", "intersect"] = "replace"


class TimelineClipMaskMoveArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    mask_id: str = Field(min_length=1)
    position: int = Field(ge=0)


class TimelineClipMaskRemoveArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    mask_id: str = Field(min_length=1)


class TimelineClipMaskKeyframeSetArguments(SequenceArguments):
    clip_id: str = Field(min_length=1)
    mask_id: str = Field(min_length=1)
    timeline_offset: int = Field(ge=0)
    geometry: MaskGeometry
    curve: KeyframeCurve = Field(default_factory=KeyframeCurve)


class TimelineClipMaskKeyframeRemoveArguments(TimelineClipMaskRemoveArguments):
    timeline_offset: int = Field(ge=0)


class TimelineClipMaskKeyframeMoveArguments(TimelineClipMaskRemoveArguments):
    old_timeline_offset: int = Field(ge=0)
    new_timeline_offset: int = Field(ge=0)


class TimelineClipMaskKeyframeRetimeArguments(TimelineClipMaskRemoveArguments):
    timeline_offsets: list[int] = Field(min_length=1)
    anchor_offset: int = Field(ge=0)
    scale: float = Field(gt=0)


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


InterchangeTimelineInspectResult = InterchangeTimelineSummary


class InterchangeTimelineImportResult(DomainModel):
    summary: InterchangeTimelineSummary
    sequence: Sequence
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
