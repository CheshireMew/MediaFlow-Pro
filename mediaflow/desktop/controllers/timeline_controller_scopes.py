from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mediaflow.desktop.coordinators import BackgroundRequests, TaskOperations, TimelineAssetOperations
from mediaflow.desktop.coordinators.preview_graph import PreviewGraphCoordinator
from mediaflow.desktop.session_state import (
    AsyncRequestState,
    PresentationState,
    ProjectBinding,
    SelectionState,
    TaskViewState,
)
from mediaflow.desktop.timeline_list_models import (
    ClipListModel,
    CompoundClipListModel,
    TimelineClipViewportModel,
    TimelineMarkerListModel,
    TimelineRangeListModel,
    TrackListModel,
    TransitionListModel,
)
from mediaflow.domain.settings import ServiceSettings
from mediaflow.service.desktop_application_proxy import DesktopEditorApplication

from .controller_context import ControllerScope


@dataclass(frozen=True, slots=True)
class TimelineAnalysisScope(ControllerScope):
    project_binding: ProjectBinding
    read_selection: Callable[[], SelectionState]
    task_view_state: TaskViewState
    _require_writable: Callable[[], None]
    _set_status: Callable[..., None]
    tasks: TaskOperations


@dataclass(frozen=True, slots=True)
class TimelineClipScope(ControllerScope):
    refresh_known_clip_membership: Callable[..., None]
    refresh_clip_structure: Callable[..., None]
    invalidate_audio_metrics: Callable[..., None]
    refresh_clip_rows: Callable[..., None]
    refresh_timeline: Callable[..., None]
    project_binding: ProjectBinding
    read_selection: Callable[[], SelectionState]
    clips_model: ClipListModel
    _require_writable: Callable[[], None]
    _set_status: Callable[..., None]
    _snap_timeline_frame: Callable[..., int]
    _snap_tolerance_frames: Callable[[float], int]
    _timeline_snap_targets: Callable[..., list[int]]
    preview: PreviewGraphCoordinator
    timeline_assets: TimelineAssetOperations


@dataclass(frozen=True, slots=True)
class TimelineColorScope(ControllerScope):
    project_binding: ProjectBinding
    _api: DesktopEditorApplication
    _set_status: Callable[..., None]
    background: BackgroundRequests


@dataclass(frozen=True, slots=True)
class TimelineEffectsScope(ControllerScope):
    refresh_timeline: Callable[..., None]
    project_binding: ProjectBinding
    read_selection: Callable[[], SelectionState]
    _require_writable: Callable[[], None]
    _set_status: Callable[..., None]
    preview: PreviewGraphCoordinator


@dataclass(frozen=True, slots=True)
class TimelineInterchangeScope(ControllerScope):
    refresh_active_sequence: Callable[..., None]
    project_binding: ProjectBinding
    read_selection: Callable[[], SelectionState]
    _api: DesktopEditorApplication
    _require_writable: Callable[[], None]
    _set_status: Callable[..., None]
    background: BackgroundRequests


@dataclass(frozen=True, slots=True)
class TimelineKeyframeScope(ControllerScope):
    refresh_clip_rows: Callable[..., None]
    project_binding: ProjectBinding
    read_selection: Callable[[], SelectionState]
    _require_writable: Callable[[], None]
    _set_status: Callable[..., None]
    preview: PreviewGraphCoordinator


@dataclass(frozen=True, slots=True)
class TimelineMaskScope(ControllerScope):
    refresh_clip_rows: Callable[..., None]
    project_binding: ProjectBinding
    read_selection: Callable[[], SelectionState]
    _require_writable: Callable[[], None]
    _set_status: Callable[..., None]
    preview: PreviewGraphCoordinator
    tasks: TaskOperations


@dataclass(frozen=True, slots=True)
class TimelineMulticamScope(ControllerScope):
    refresh_timeline: Callable[..., None]
    project_binding: ProjectBinding
    read_selection: Callable[[], SelectionState]
    _api: DesktopEditorApplication
    _require_writable: Callable[[], None]
    _set_status: Callable[..., None]
    background: BackgroundRequests


@dataclass(frozen=True, slots=True)
class TimelineReviewScope(ControllerScope):
    refresh_timeline: Callable[..., None]
    project_binding: ProjectBinding
    read_selection: Callable[[], SelectionState]
    _api: DesktopEditorApplication
    _require_writable: Callable[[], None]
    _set_status: Callable[..., None]
    background: BackgroundRequests


@dataclass(frozen=True, slots=True)
class TimelineStructureScope(ControllerScope):
    refresh_preview_subtitles: Callable[..., None]
    refresh_documents: Callable[..., None]
    refresh_sequences: Callable[..., None]
    refresh_timeline: Callable[..., None]
    refresh_active_sequence: Callable[..., None]
    refresh_assets: Callable[..., None]
    project_binding: ProjectBinding
    read_selection: Callable[[], SelectionState]
    _finish_sequence_in_out_edit: Callable[..., None]
    _require_writable: Callable[[], None]
    _set_status: Callable[..., None]
    preview: PreviewGraphCoordinator
    timeline_assets: TimelineAssetOperations


@dataclass(frozen=True, slots=True)
class TimelineViewScope(ControllerScope):
    project_binding: ProjectBinding
    presentation_state: PresentationState
    request_state: AsyncRequestState
    read_selection: Callable[[], SelectionState]
    read_service_settings: Callable[[], ServiceSettings]
    clips_model: ClipListModel
    compound_clips_model: CompoundClipListModel
    markers_model: TimelineMarkerListModel
    ranges_model: TimelineRangeListModel
    tracks_model: TrackListModel
    transitions_model: TransitionListModel
    task_view_state: TaskViewState
    _api: DesktopEditorApplication
    _updated_selection: Callable[..., list[str]]
    background: BackgroundRequests
    preview: PreviewGraphCoordinator


@dataclass(frozen=True, slots=True)
class TimelineViewportScope(ControllerScope):
    read_selection: Callable[[], SelectionState]
    visible_clips_model: TimelineClipViewportModel


@dataclass(frozen=True, slots=True)
class TimelineVoiceoverScope(ControllerScope):
    refresh_timeline: Callable[..., None]
    refresh_assets: Callable[..., None]
    project_binding: ProjectBinding
    _require_writable: Callable[[], None]
    _set_status: Callable[..., None]
    preview: PreviewGraphCoordinator
