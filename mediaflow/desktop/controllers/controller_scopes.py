from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from PySide6.QtCore import QObject

from mediaflow.desktop.coordinators import (
    BackgroundRequests,
    ProjectLifecycle,
    RuntimeToolOperations,
    SettingsPersistence,
    TaskOperations,
    TimelineAssetOperations,
)
from mediaflow.desktop.coordinators.preview_graph import PreviewGraphCoordinator
from mediaflow.desktop.presenters import PresentationProjectors
from mediaflow.desktop.session_events import SessionEvents
from mediaflow.desktop.session_state import DesktopSessionState, SessionModels
from mediaflow.desktop.session_updates import SessionUpdates
from mediaflow.service.client import EditorServiceRpcError
from mediaflow.service.desktop_application_proxy import DesktopEditorApplication

if TYPE_CHECKING:
    from mediaflow.desktop.controllers.project_controller import ProjectSession
from .controller_context import ControllerScope
from .timeline_controller_scopes import (
    TimelineAnalysisScope,
    TimelineClipScope,
    TimelineColorScope,
    TimelineEffectsScope,
    TimelineInterchangeScope,
    TimelineKeyframeScope,
    TimelineMaskScope,
    TimelineMulticamScope,
    TimelineReviewScope,
    TimelineStructureScope,
    TimelineViewportScope,
    TimelineViewScope,
    TimelineVoiceoverScope,
)

Operation = Callable[..., Any]


class _SupportValues(TypedDict):
    parent: QObject
    updates: SessionUpdates
    _present_collaboration_conflict: Callable[[EditorServiceRpcError], None]


@dataclass(frozen=True, slots=True)
class WorkspaceViewScope(ControllerScope):
    state: DesktopSessionState
    models: SessionModels
    tasks: TaskOperations
    _api: DesktopEditorApplication


@dataclass(frozen=True, slots=True)
class WorkspaceProjectScope(ControllerScope):
    tasks: TaskOperations
    preview: PreviewGraphCoordinator
    state: DesktopSessionState
    background: BackgroundRequests
    lifecycle: ProjectLifecycle
    projectors: PresentationProjectors
    settings_persistence: SettingsPersistence
    _api: DesktopEditorApplication
    _local_path: Callable[[str], Path]
    _require_writable: Callable[[], None]
    _set_status: Operation


@dataclass(frozen=True, slots=True)
class WorkspaceSequenceScope(ControllerScope):
    preview: PreviewGraphCoordinator
    state: DesktopSessionState
    timeline_assets: TimelineAssetOperations
    projectors: PresentationProjectors
    _require_writable: Callable[[], None]
    _set_status: Operation


@dataclass(frozen=True, slots=True)
class WorkspaceWorkflowScope(ControllerScope):
    state: DesktopSessionState
    _apply_workflow_update: Operation
    _require_writable: Callable[[], None]


@dataclass(frozen=True, slots=True)
class WorkspacePlaybackScope(ControllerScope):
    events: SessionEvents
    state: DesktopSessionState


@dataclass(frozen=True, slots=True)
class SettingsFormControllerScope(ControllerScope):
    state: DesktopSessionState
    models: SessionModels
    projectors: PresentationProjectors
    settings_persistence: SettingsPersistence
    _api: DesktopEditorApplication


@dataclass(frozen=True, slots=True)
class RuntimeSettingsControllerScope(ControllerScope):
    state: DesktopSessionState
    runtime_tools: RuntimeToolOperations
    _api: DesktopEditorApplication
    _set_status: Operation


@dataclass(frozen=True, slots=True)
class LanguageSettingsControllerScope(ControllerScope):
    state: DesktopSessionState
    models: SessionModels
    background: BackgroundRequests
    settings_persistence: SettingsPersistence
    _api: DesktopEditorApplication
    _set_status: Operation


@dataclass(frozen=True, slots=True)
class DownloadSettingsControllerScope(ControllerScope):
    state: DesktopSessionState
    settings_persistence: SettingsPersistence
    _api: DesktopEditorApplication
    _local_path: Callable[[str], Path]
    _set_status: Operation


@dataclass(frozen=True, slots=True)
class WorkspaceSettingsControllerScope(ControllerScope):
    state: DesktopSessionState
    settings_persistence: SettingsPersistence


@dataclass(frozen=True, slots=True)
class MediaControllerScope(ControllerScope):
    preview: PreviewGraphCoordinator
    state: DesktopSessionState
    models: SessionModels
    background: BackgroundRequests
    projectors: PresentationProjectors
    timeline_assets: TimelineAssetOperations
    settings_persistence: SettingsPersistence
    _api: DesktopEditorApplication
    _local_path: Callable[[str], Path]
    _require_writable: Callable[[], None]
    _set_status: Operation
    _updated_selection: Operation


@dataclass(frozen=True, slots=True)
class SubtitlePresentationScope(ControllerScope):
    preview: PreviewGraphCoordinator
    state: DesktopSessionState
    events: SessionEvents
    models: SessionModels
    background: BackgroundRequests
    projectors: PresentationProjectors
    settings_persistence: SettingsPersistence
    tasks: TaskOperations
    _finish_subtitle_edit: Operation
    _local_path: Callable[[str], Path]
    _require_subtitle_document: Callable[[], None]
    _require_writable: Callable[[], None]
    _set_status: Operation
    _snap_tolerance_frames: Operation
    _timeline_snap_targets: Operation
    _updated_selection: Operation


@dataclass(frozen=True, slots=True)
class CreativeControllerScope(ControllerScope):
    preview: PreviewGraphCoordinator
    state: DesktopSessionState
    models: SessionModels
    projectors: PresentationProjectors
    settings_persistence: SettingsPersistence
    tasks: TaskOperations
    _local_path: Callable[[str], Path]
    _require_writable: Callable[[], None]
    _set_status: Operation


@dataclass(frozen=True, slots=True)
class AutomationControllerScope(ControllerScope):
    state: DesktopSessionState
    tasks: TaskOperations
    _local_path: Callable[[str], Path]
    _require_exportable_sequence: Callable[[], None]
    _require_writable: Callable[[], None]
    _set_status: Operation


@dataclass(frozen=True, slots=True)
class ExportControllerScope(ControllerScope):
    state: DesktopSessionState
    tasks: TaskOperations
    _api: DesktopEditorApplication
    _active_sequence_has_renderable_content: Callable[[], bool]
    _local_path: Callable[[str], Path]
    _require_exportable_sequence: Callable[[], None]
    _set_status: Operation


@dataclass(frozen=True, slots=True)
class TaskControllerScope(ControllerScope):
    state: DesktopSessionState
    models: SessionModels
    projectors: PresentationProjectors
    _require_writable: Callable[[], None]
    _set_status: Operation


@dataclass(frozen=True, slots=True)
class DownloadControllerScope(ControllerScope):
    state: DesktopSessionState
    models: SessionModels
    background: BackgroundRequests
    lifecycle: ProjectLifecycle
    projectors: PresentationProjectors
    settings_persistence: SettingsPersistence
    _api: DesktopEditorApplication
    _local_path: Callable[[str], Path]
    _require_writable: Callable[[], None]
    _set_status: Operation
    _start_download_workflow: Operation


@dataclass(frozen=True, slots=True)
class WebControllerScope(ControllerScope):
    preview: PreviewGraphCoordinator
    state: DesktopSessionState
    events: SessionEvents
    models: SessionModels
    projectors: PresentationProjectors
    tasks: TaskOperations
    _require_writable: Callable[[], None]


def _support(session: ProjectSession) -> _SupportValues:
    return {
        "parent": session,
        "updates": session.updates,
        "_present_collaboration_conflict": session._present_collaboration_conflict,
    }


def workspace_view_scope(session: ProjectSession) -> WorkspaceViewScope:
    return WorkspaceViewScope(
        **_support(session),
        state=session.state,
        models=session.models,
        tasks=session.tasks,
        _api=session._api,
    )


def workspace_project_scope(session: ProjectSession) -> WorkspaceProjectScope:
    return WorkspaceProjectScope(
        **_support(session),
        tasks=session.tasks,
        state=session.state,
        background=session.background,
        lifecycle=session.lifecycle,
        projectors=session.projectors,
        settings_persistence=session.settings_persistence,
        _api=session._api,
        _local_path=session._local_path,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        preview=session.preview,
    )


def workspace_sequence_scope(session: ProjectSession) -> WorkspaceSequenceScope:
    return WorkspaceSequenceScope(
        **_support(session),
        state=session.state,
        timeline_assets=session.timeline_assets,
        projectors=session.projectors,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        preview=session.preview,
    )


def workspace_workflow_scope(session: ProjectSession) -> WorkspaceWorkflowScope:
    return WorkspaceWorkflowScope(
        **_support(session),
        state=session.state,
        _apply_workflow_update=session._apply_workflow_update,
        _require_writable=session._require_writable,
    )


def workspace_playback_scope(session: ProjectSession) -> WorkspacePlaybackScope:
    return WorkspacePlaybackScope(**_support(session), events=session.events, state=session.state)


def settings_scope(session: ProjectSession) -> SettingsFormControllerScope:
    return SettingsFormControllerScope(
        **_support(session),
        state=session.state,
        models=session.models,
        projectors=session.projectors,
        settings_persistence=session.settings_persistence,
        _api=session._api,
    )


def runtime_settings_scope(session: ProjectSession) -> RuntimeSettingsControllerScope:
    return RuntimeSettingsControllerScope(
        **_support(session),
        state=session.state,
        runtime_tools=session.runtime_tools,
        _api=session._api,
        _set_status=session._set_status,
    )


def language_settings_scope(session: ProjectSession) -> LanguageSettingsControllerScope:
    return LanguageSettingsControllerScope(
        **_support(session),
        state=session.state,
        models=session.models,
        background=session.background,
        settings_persistence=session.settings_persistence,
        _api=session._api,
        _set_status=session._set_status,
    )


def download_settings_scope(session: ProjectSession) -> DownloadSettingsControllerScope:
    return DownloadSettingsControllerScope(
        **_support(session),
        state=session.state,
        settings_persistence=session.settings_persistence,
        _api=session._api,
        _local_path=session._local_path,
        _set_status=session._set_status,
    )


def workspace_settings_scope(session: ProjectSession) -> WorkspaceSettingsControllerScope:
    return WorkspaceSettingsControllerScope(
        **_support(session), state=session.state, settings_persistence=session.settings_persistence
    )


def media_scope(session: ProjectSession) -> MediaControllerScope:
    return MediaControllerScope(
        **_support(session),
        state=session.state,
        models=session.models,
        background=session.background,
        projectors=session.projectors,
        timeline_assets=session.timeline_assets,
        settings_persistence=session.settings_persistence,
        _api=session._api,
        _local_path=session._local_path,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        _updated_selection=session._updated_selection,
        preview=session.preview,
    )


def subtitle_scope(session: ProjectSession) -> SubtitlePresentationScope:
    return SubtitlePresentationScope(
        **_support(session),
        state=session.state,
        events=session.events,
        models=session.models,
        background=session.background,
        projectors=session.projectors,
        settings_persistence=session.settings_persistence,
        tasks=session.tasks,
        _finish_subtitle_edit=session._finish_subtitle_edit,
        _local_path=session._local_path,
        _require_subtitle_document=session._require_subtitle_document,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        _snap_tolerance_frames=session._snap_tolerance_frames,
        _timeline_snap_targets=session._timeline_snapping.targets,
        _updated_selection=session._updated_selection,
        preview=session.preview,
    )


def creative_scope(session: ProjectSession) -> CreativeControllerScope:
    return CreativeControllerScope(
        **_support(session),
        state=session.state,
        models=session.models,
        projectors=session.projectors,
        settings_persistence=session.settings_persistence,
        tasks=session.tasks,
        _local_path=session._local_path,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        preview=session.preview,
    )


def automation_scope(session: ProjectSession) -> AutomationControllerScope:
    return AutomationControllerScope(
        **_support(session),
        state=session.state,
        tasks=session.tasks,
        _local_path=session._local_path,
        _require_exportable_sequence=session._require_exportable_sequence,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
    )


def export_scope(session: ProjectSession) -> ExportControllerScope:
    return ExportControllerScope(
        **_support(session),
        state=session.state,
        tasks=session.tasks,
        _api=session._api,
        _active_sequence_has_renderable_content=session._active_sequence_has_renderable_content,
        _local_path=session._local_path,
        _require_exportable_sequence=session._require_exportable_sequence,
        _set_status=session._set_status,
    )


def task_scope(session: ProjectSession) -> TaskControllerScope:
    return TaskControllerScope(
        **_support(session),
        state=session.state,
        models=session.models,
        projectors=session.projectors,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
    )


def download_scope(session: ProjectSession) -> DownloadControllerScope:
    return DownloadControllerScope(
        **_support(session),
        state=session.state,
        models=session.models,
        background=session.background,
        lifecycle=session.lifecycle,
        projectors=session.projectors,
        settings_persistence=session.settings_persistence,
        _api=session._api,
        _local_path=session._local_path,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        _start_download_workflow=session._start_download_workflow,
    )


def web_scope(session: ProjectSession) -> WebControllerScope:
    return WebControllerScope(
        **_support(session),
        state=session.state,
        events=session.events,
        models=session.models,
        projectors=session.projectors,
        tasks=session.tasks,
        _require_writable=session._require_writable,
        preview=session.preview,
    )


def timeline_analysis_scope(session: ProjectSession) -> TimelineAnalysisScope:
    return TimelineAnalysisScope(
        **_support(session),
        project_binding=session.state.binding,
        read_selection=lambda: session.state.selection,
        task_view_state=session.state.tasks,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        tasks=session.tasks,
    )


def timeline_clip_scope(session: ProjectSession) -> TimelineClipScope:
    return TimelineClipScope(
        **_support(session),
        refresh_known_clip_membership=session.projectors.timeline.refresh_known_clip_membership,
        refresh_clip_structure=session.projectors.timeline.refresh_clip_structure,
        invalidate_audio_metrics=session.projectors.audio.invalidate_audio_metrics,
        refresh_clip_rows=session.projectors.timeline.refresh_clip_rows,
        refresh_timeline=session.projectors.timeline.refresh_timeline,
        project_binding=session.state.binding,
        read_selection=lambda: session.state.selection,
        clips_model=session.models.clips,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        _snap_timeline_frame=session._timeline_snapping.snap,
        _snap_tolerance_frames=session._snap_tolerance_frames,
        _timeline_snap_targets=session._timeline_snapping.targets,
        preview=session.preview,
        timeline_assets=session.timeline_assets,
    )


def timeline_color_scope(session: ProjectSession) -> TimelineColorScope:
    return TimelineColorScope(
        **_support(session),
        project_binding=session.state.binding,
        _api=session._api,
        _set_status=session._set_status,
        background=session.background,
    )


def timeline_effects_scope(session: ProjectSession) -> TimelineEffectsScope:
    return TimelineEffectsScope(
        **_support(session),
        refresh_timeline=session.projectors.timeline.refresh_timeline,
        project_binding=session.state.binding,
        read_selection=lambda: session.state.selection,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        preview=session.preview,
    )


def timeline_interchange_scope(session: ProjectSession) -> TimelineInterchangeScope:
    return TimelineInterchangeScope(
        **_support(session),
        refresh_active_sequence=session.projectors.refresh_active_sequence,
        project_binding=session.state.binding,
        read_selection=lambda: session.state.selection,
        _api=session._api,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        background=session.background,
    )


def timeline_keyframe_scope(session: ProjectSession) -> TimelineKeyframeScope:
    return TimelineKeyframeScope(
        **_support(session),
        refresh_clip_rows=session.projectors.timeline.refresh_clip_rows,
        project_binding=session.state.binding,
        read_selection=lambda: session.state.selection,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        preview=session.preview,
    )


def timeline_mask_scope(session: ProjectSession) -> TimelineMaskScope:
    return TimelineMaskScope(
        **_support(session),
        refresh_clip_rows=session.projectors.timeline.refresh_clip_rows,
        project_binding=session.state.binding,
        read_selection=lambda: session.state.selection,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        preview=session.preview,
        tasks=session.tasks,
    )


def timeline_multicam_scope(session: ProjectSession) -> TimelineMulticamScope:
    return TimelineMulticamScope(
        **_support(session),
        refresh_timeline=session.projectors.timeline.refresh_timeline,
        project_binding=session.state.binding,
        read_selection=lambda: session.state.selection,
        _api=session._api,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        background=session.background,
    )


def timeline_review_scope(session: ProjectSession) -> TimelineReviewScope:
    return TimelineReviewScope(
        **_support(session),
        refresh_timeline=session.projectors.timeline.refresh_timeline,
        project_binding=session.state.binding,
        read_selection=lambda: session.state.selection,
        _api=session._api,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        background=session.background,
    )


def timeline_structure_scope(session: ProjectSession) -> TimelineStructureScope:
    return TimelineStructureScope(
        **_support(session),
        refresh_preview_subtitles=session.projectors.timeline_subtitles.refresh_preview_subtitles,
        refresh_documents=session.projectors.subtitles.refresh_documents,
        refresh_sequences=session.projectors.timeline.refresh_sequences,
        refresh_timeline=session.projectors.timeline.refresh_timeline,
        refresh_active_sequence=session.projectors.refresh_active_sequence,
        refresh_assets=session.projectors.assets.refresh_assets,
        project_binding=session.state.binding,
        read_selection=lambda: session.state.selection,
        _finish_sequence_in_out_edit=session._finish_sequence_in_out_edit,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        preview=session.preview,
        timeline_assets=session.timeline_assets,
    )


def timeline_view_scope(session: ProjectSession) -> TimelineViewScope:
    return TimelineViewScope(
        **_support(session),
        project_binding=session.state.binding,
        presentation_state=session.state.presentation,
        request_state=session.state.requests,
        read_selection=lambda: session.state.selection,
        read_service_settings=lambda: session.state.service_settings,
        clips_model=session.models.clips,
        compound_clips_model=session.models.compound_clips,
        markers_model=session.models.markers,
        ranges_model=session.models.ranges,
        tracks_model=session.models.tracks,
        transitions_model=session.models.transitions,
        task_view_state=session.state.tasks,
        _api=session._api,
        _updated_selection=session._updated_selection,
        background=session.background,
        preview=session.preview,
    )


def timeline_viewport_scope(session: ProjectSession) -> TimelineViewportScope:
    return TimelineViewportScope(
        **_support(session),
        read_selection=lambda: session.state.selection,
        visible_clips_model=session.models.visible_clips,
    )


def timeline_voiceover_scope(session: ProjectSession) -> TimelineVoiceoverScope:
    return TimelineVoiceoverScope(
        **_support(session),
        refresh_timeline=session.projectors.timeline.refresh_timeline,
        refresh_assets=session.projectors.assets.refresh_assets,
        project_binding=session.state.binding,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        preview=session.preview,
    )
