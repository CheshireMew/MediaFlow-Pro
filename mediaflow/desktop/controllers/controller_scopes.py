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
from mediaflow.desktop.presenters import PresentationProjectors
from mediaflow.desktop.session_events import SessionEvents
from mediaflow.desktop.session_state import DesktopSessionState, SessionModels
from mediaflow.desktop.session_updates import SessionUpdates
from mediaflow.service.client import EditorServiceRpcError
from mediaflow.service.desktop_application_proxy import DesktopEditorApplication

if TYPE_CHECKING:
    from mediaflow.desktop.controllers.project_controller import ProjectSession

Operation = Callable[..., Any]


class _SupportValues(TypedDict):
    parent: QObject
    updates: SessionUpdates
    _present_collaboration_conflict: Callable[[EditorServiceRpcError], None]


@dataclass(frozen=True, slots=True)
class ControllerScope:
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
class SettingsControllerScope(ControllerScope):
    state: DesktopSessionState
    models: SessionModels
    projectors: PresentationProjectors
    runtime_tools: RuntimeToolOperations
    settings_persistence: SettingsPersistence
    _api: DesktopEditorApplication
    _local_path: Callable[[str], Path]
    _set_status: Operation


@dataclass(frozen=True, slots=True)
class MediaControllerScope(ControllerScope):
    state: DesktopSessionState
    models: SessionModels
    projectors: PresentationProjectors
    timeline_assets: TimelineAssetOperations
    settings_persistence: SettingsPersistence
    _api: DesktopEditorApplication
    _local_path: Callable[[str], Path]
    _require_writable: Callable[[], None]
    _set_status: Operation
    _updated_selection: Operation


@dataclass(frozen=True, slots=True)
class TimelinePresentationScope(ControllerScope):
    state: DesktopSessionState
    events: SessionEvents
    models: SessionModels
    background: BackgroundRequests
    projectors: PresentationProjectors
    timeline_assets: TimelineAssetOperations
    tasks: TaskOperations
    _api: DesktopEditorApplication
    _finish_sequence_in_out_edit: Operation
    _require_writable: Callable[[], None]
    _set_status: Operation
    _snap_tolerance_frames: Operation
    _timeline_snap_targets: Operation
    _updated_selection: Operation


@dataclass(frozen=True, slots=True)
class SubtitlePresentationScope(ControllerScope):
    state: DesktopSessionState
    events: SessionEvents
    models: SessionModels
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
        state=session.state,
        background=session.background,
        lifecycle=session.lifecycle,
        projectors=session.projectors,
        settings_persistence=session.settings_persistence,
        _api=session._api,
        _local_path=session._local_path,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
    )


def workspace_sequence_scope(session: ProjectSession) -> WorkspaceSequenceScope:
    return WorkspaceSequenceScope(
        **_support(session),
        state=session.state,
        timeline_assets=session.timeline_assets,
        projectors=session.projectors,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
    )


def workspace_workflow_scope(session: ProjectSession) -> WorkspaceWorkflowScope:
    return WorkspaceWorkflowScope(
        **_support(session),
        state=session.state,
        _apply_workflow_update=session._apply_workflow_update,
        _require_writable=session._require_writable,
    )


def workspace_playback_scope(session: ProjectSession) -> WorkspacePlaybackScope:
    return WorkspacePlaybackScope(
        **_support(session),
        events=session.events,
        state=session.state,
    )


def settings_scope(session: ProjectSession) -> SettingsControllerScope:
    return SettingsControllerScope(
        **_support(session),
        state=session.state,
        models=session.models,
        projectors=session.projectors,
        runtime_tools=session.runtime_tools,
        settings_persistence=session.settings_persistence,
        _api=session._api,
        _local_path=session._local_path,
        _set_status=session._set_status,
    )


def media_scope(session: ProjectSession) -> MediaControllerScope:
    return MediaControllerScope(
        **_support(session),
        state=session.state,
        models=session.models,
        projectors=session.projectors,
        timeline_assets=session.timeline_assets,
        settings_persistence=session.settings_persistence,
        _api=session._api,
        _local_path=session._local_path,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        _updated_selection=session._updated_selection,
    )


def timeline_scope(session: ProjectSession) -> TimelinePresentationScope:
    return TimelinePresentationScope(
        **_support(session),
        state=session.state,
        events=session.events,
        models=session.models,
        background=session.background,
        projectors=session.projectors,
        timeline_assets=session.timeline_assets,
        tasks=session.tasks,
        _api=session._api,
        _finish_sequence_in_out_edit=session._finish_sequence_in_out_edit,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        _snap_tolerance_frames=session._snap_tolerance_frames,
        _timeline_snap_targets=session._timeline_snap_targets,
        _updated_selection=session._updated_selection,
    )


def subtitle_scope(session: ProjectSession) -> SubtitlePresentationScope:
    return SubtitlePresentationScope(
        **_support(session),
        state=session.state,
        events=session.events,
        models=session.models,
        projectors=session.projectors,
        settings_persistence=session.settings_persistence,
        tasks=session.tasks,
        _finish_subtitle_edit=session._finish_subtitle_edit,
        _local_path=session._local_path,
        _require_subtitle_document=session._require_subtitle_document,
        _require_writable=session._require_writable,
        _set_status=session._set_status,
        _snap_tolerance_frames=session._snap_tolerance_frames,
        _timeline_snap_targets=session._timeline_snap_targets,
        _updated_selection=session._updated_selection,
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
    )
