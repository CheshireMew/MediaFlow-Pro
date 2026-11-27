from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject

from mediaflow.application.presentation_models import RecentProjectSnapshot
from mediaflow.desktop.session_state import DesktopProject, DesktopSessionState, SessionModels
from mediaflow.desktop.session_updates import SessionUpdates
from mediaflow.domain.settings import DesktopSettings, ServiceSettings
from mediaflow.domain.tasks import Task


@dataclass(frozen=True, slots=True)
class AssetProjectorContext:
    asset_thumbnail_paths: Callable[[Path], dict[str, str]]
    submit_background: Callable[..., Future[object] | None]
    models: SessionModels
    refresh_timeline: Callable[..., None]
    state: DesktopSessionState


@dataclass(frozen=True, slots=True)
class AudioProjectorContext:
    submit_background: Callable[..., Future[object] | None]
    models: SessionModels
    state: DesktopSessionState
    updates: SessionUpdates


@dataclass(frozen=True, slots=True)
class HighlightProjectorContext:
    models: SessionModels
    state: DesktopSessionState


@dataclass(frozen=True, slots=True)
class SubtitleProjectorContext:
    models: SessionModels
    state: DesktopSessionState


@dataclass(frozen=True, slots=True)
class TaskProjectorContext:
    models: SessionModels
    state: DesktopSessionState
    updates: SessionUpdates


@dataclass(frozen=True, slots=True)
class TimelineProjectorContext:
    invalidate_snapping: Callable[..., None]
    update_snapping_clips: Callable[..., None]
    models: SessionModels
    schedule_preview: Callable[..., None]
    invalidate_audio_metrics: Callable[..., None]
    state: DesktopSessionState
    updates: SessionUpdates


@dataclass(frozen=True, slots=True)
class TimelineSubtitleProjectorContext:
    models: SessionModels
    state: DesktopSessionState


@dataclass(frozen=True, slots=True)
class WorkspaceProjectorContext:
    discover_encoder_policy_options: Callable[..., list[dict[str, Any]]]
    installed_asr_models: Callable[..., frozenset[str]]
    recent_projects: Callable[..., RecentProjectSnapshot]
    runtime_tool_status: Callable[..., dict[str, Any]]
    submit_background: Callable[..., Future[object] | None]
    models: SessionModels
    state: DesktopSessionState
    updates: SessionUpdates


@dataclass(frozen=True, slots=True)
class BackgroundRequestsContext:
    parent: QObject
    state: DesktopSessionState
    dispatch_result: Callable[..., bool]


@dataclass(frozen=True, slots=True)
class BackgroundResultRouterContext:
    set_download_plan: Callable[..., None]
    set_status: Callable[..., None]
    apply_asset_thumbnails: Callable[..., None]
    request_asset_thumbnails: Callable[..., None]
    refresh_download_entries: Callable[..., None]
    refresh_clip_rows: Callable[..., None]
    apply_recent_projects: Callable[..., None]
    apply_runtime_tool_status: Callable[..., None]
    refresh_recent_projects: Callable[..., None]
    state: DesktopSessionState
    updates: SessionUpdates


@dataclass(frozen=True, slots=True)
class PreviewGraphCoordinatorContext:
    parent: QObject
    write_preview_snapshot: Callable[..., Path]
    preview_executor: ThreadPoolExecutor
    submit_background: Callable[..., Future[object] | None]
    state: DesktopSessionState
    create: Callable[..., Task]
    updates: SessionUpdates


@dataclass(frozen=True, slots=True)
class ProjectLifecycleContext:
    parent: QObject
    cancel_timeline_filmstrip_requests: Callable[..., None]
    cancel_preview_snapshot_requests: Callable[..., None]
    create_project: Callable[..., DesktopProject]
    set_status: Callable[..., None]
    submit_background: Callable[..., Future[object] | None]
    models: SessionModels
    schedule_preview: Callable[..., None]
    stop_preview: Callable[..., None]
    refresh_project: Callable[..., None]
    refresh_recent_projects: Callable[..., None]
    commit: Callable[..., None]
    state: DesktopSessionState
    publish: Callable[..., None]
    reconcile_committed_results: Callable[..., None]
    reset_delivery_state: Callable[..., None]
    update_reconciliation_state: Callable[..., None]
    updates: SessionUpdates


@dataclass(frozen=True, slots=True)
class RuntimeToolOperationsContext:
    parent: QObject
    run_runtime_tool: Callable[..., dict[str, Any]]
    set_status: Callable[..., None]
    refresh_installed_asr_models: Callable[..., None]
    refresh_runtime_tool_status: Callable[..., None]
    commit: Callable[..., None]
    state: DesktopSessionState
    updates: SessionUpdates


@dataclass(frozen=True, slots=True)
class SettingsPersistenceContext:
    parent: QObject
    read_desktop_settings: Callable[[], DesktopSettings]
    replace_desktop_settings: Callable[..., None]
    replace_service_settings: Callable[..., None]
    read_service_settings: Callable[[], ServiceSettings]
    set_status: Callable[..., None]
    refresh_settings_models: Callable[..., None]
    state: DesktopSessionState
    updates: SessionUpdates


@dataclass(frozen=True, slots=True)
class TaskOperationsContext:
    parent: QObject
    apply_workflow_update: Callable[..., None]
    finish_sequence_in_out_edit: Callable[..., None]
    require_writable: Callable[..., None]
    set_download_plan: Callable[..., None]
    set_status: Callable[..., None]
    reconcile_task_events: Callable[..., None]
    schedule_preview: Callable[..., None]
    refresh_assets: Callable[..., None]
    refresh_highlights: Callable[..., None]
    refresh_documents: Callable[..., None]
    refresh_tasks: Callable[..., None]
    refresh_timeline: Callable[..., None]
    refresh_preview_subtitles: Callable[..., None]
    refresh_recent_projects: Callable[..., None]
    state: DesktopSessionState
    finish_import_drop: Callable[..., None]
    updates: SessionUpdates


@dataclass(frozen=True, slots=True)
class TimelineAssetOperationsContext:
    parent: QObject
    require_writable: Callable[..., None]
    set_status: Callable[..., None]
    snap_tolerance_frames: Callable[..., int]
    snap_targets: Callable[..., list[int]]
    refresh_assets: Callable[..., None]
    refresh_documents: Callable[..., None]
    refresh_tasks: Callable[..., None]
    refresh_timeline: Callable[..., None]
    refresh_preview_subtitles: Callable[..., None]
    state: DesktopSessionState
    start: Callable[..., Task | None]
    updates: SessionUpdates


@dataclass(frozen=True, slots=True)
class PresentationRefreshContext:
    updates: SessionUpdates
    invalidate_active_workflow: Callable[[], None]
    refresh_active_workflow: Callable[..., object]
    rebuild_snapping: Callable[[], None]
    schedule_preview: Callable[..., None]


@dataclass(frozen=True, slots=True)
class TimelineSnappingContext:
    state: DesktopSessionState
    models: SessionModels
