from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject

from mediaflow.desktop.component_contexts import (
    AssetProjectorContext,
    AudioProjectorContext,
    BackgroundRequestsContext,
    BackgroundResultRouterContext,
    HighlightProjectorContext,
    PresentationRefreshContext,
    PreviewGraphCoordinatorContext,
    ProjectLifecycleContext,
    RuntimeToolOperationsContext,
    SettingsPersistenceContext,
    SubtitleProjectorContext,
    TaskOperationsContext,
    TaskProjectorContext,
    TimelineAssetOperationsContext,
    TimelineProjectorContext,
    TimelineSubtitleProjectorContext,
    WorkspaceProjectorContext,
)
from mediaflow.desktop.controllers.timeline_snap_coordinator import TimelineSnappingCoordinator
from mediaflow.desktop.coordinators.background_requests import BackgroundRequests
from mediaflow.desktop.coordinators.background_result_router import BackgroundResultRouter
from mediaflow.desktop.coordinators.preview_graph import PreviewGraphCoordinator
from mediaflow.desktop.coordinators.project_lifecycle import ProjectLifecycle
from mediaflow.desktop.coordinators.runtime_tools import RuntimeToolOperations
from mediaflow.desktop.coordinators.settings_persistence import SettingsPersistence
from mediaflow.desktop.coordinators.task_events import TaskOperations
from mediaflow.desktop.coordinators.timeline_assets import TimelineAssetOperations
from mediaflow.desktop.presenters.asset_projector import AssetProjector
from mediaflow.desktop.presenters.audio_projector import AudioProjector
from mediaflow.desktop.presenters.highlight_projector import HighlightProjector
from mediaflow.desktop.presenters.projectors import PresentationProjectors
from mediaflow.desktop.presenters.subtitle_projector import SubtitleProjector
from mediaflow.desktop.presenters.task_projector import TaskProjector
from mediaflow.desktop.presenters.timeline_projector import TimelineProjector
from mediaflow.desktop.presenters.timeline_subtitle_projector import TimelineSubtitleProjector
from mediaflow.desktop.presenters.workspace_projector import WorkspaceProjector
from mediaflow.desktop.session_state import DesktopSessionState, SessionModels
from mediaflow.desktop.session_updates import SessionUpdates
from mediaflow.service.desktop_application_proxy import DesktopEditorApplication


@dataclass(frozen=True, slots=True)
class DesktopSessionActions:
    set_download_plan: Callable[..., None]
    set_status: Callable[..., None]
    apply_workflow_update: Callable[..., None]
    finish_sequence_in_out_edit: Callable[..., None]
    require_writable: Callable[..., None]
    snap_tolerance_frames: Callable[..., int]


@dataclass(frozen=True, slots=True)
class DesktopSessionComponents:
    background: BackgroundRequests
    runtime_tools: RuntimeToolOperations
    settings_persistence: SettingsPersistence
    tasks: TaskOperations
    timeline_assets: TimelineAssetOperations
    preview: PreviewGraphCoordinator
    projectors: PresentationProjectors
    lifecycle: ProjectLifecycle


def compose_desktop_components(
    *,
    parent: QObject,
    state: DesktopSessionState,
    models: SessionModels,
    updates: SessionUpdates,
    api: DesktopEditorApplication,
    snapping: TimelineSnappingCoordinator,
    actions: DesktopSessionActions,
) -> DesktopSessionComponents:
    """Wire peer operations here; consumers receive only their named capabilities."""

    background_results = BackgroundResultRouter(
        BackgroundResultRouterContext(
            set_download_plan=actions.set_download_plan,
            set_status=actions.set_status,
            apply_asset_thumbnails=lambda *args, **kwargs: projectors.assets.apply_asset_thumbnails(
                *args, **kwargs
            ),
            request_asset_thumbnails=lambda *args, **kwargs: projectors.assets.request_asset_thumbnails(
                *args, **kwargs
            ),
            refresh_download_entries=lambda *args, **kwargs: projectors.tasks.refresh_download_entries(
                *args, **kwargs
            ),
            refresh_clip_rows=lambda *args, **kwargs: projectors.timeline.refresh_clip_rows(*args, **kwargs),
            apply_recent_projects=lambda *args, **kwargs: projectors.workspace.apply_recent_projects(
                *args, **kwargs
            ),
            apply_runtime_tool_status=lambda *args, **kwargs: projectors.workspace.apply_runtime_tool_status(
                *args, **kwargs
            ),
            refresh_recent_projects=lambda *args, **kwargs: projectors.workspace.refresh_recent_projects(
                *args, **kwargs
            ),
            state=state,
            updates=updates,
        )
    )
    background = BackgroundRequests(
        BackgroundRequestsContext(
            parent=parent,
            state=state,
            dispatch_result=background_results.dispatch,
        )
    )
    settings_persistence = SettingsPersistence(
        SettingsPersistenceContext(
            parent=parent,
            read_desktop_settings=lambda: api.desktop_settings,
            replace_desktop_settings=api.replace_desktop_settings,
            replace_service_settings=api.replace_service_settings,
            read_service_settings=lambda: api.service_settings,
            set_status=actions.set_status,
            refresh_settings_models=lambda *args, **kwargs: projectors.workspace.refresh_settings_models(
                *args, **kwargs
            ),
            state=state,
            updates=updates,
        )
    )
    runtime_tools = RuntimeToolOperations(
        RuntimeToolOperationsContext(
            parent=parent,
            run_runtime_tool=api.run_runtime_tool,
            set_status=actions.set_status,
            refresh_installed_asr_models=lambda *args,
            **kwargs: projectors.workspace.refresh_installed_asr_models(*args, **kwargs),
            refresh_runtime_tool_status=lambda *args,
            **kwargs: projectors.workspace.refresh_runtime_tool_status(*args, **kwargs),
            commit=lambda *args, **kwargs: settings_persistence.commit(*args, **kwargs),
            state=state,
            updates=updates,
        )
    )
    tasks = TaskOperations(
        TaskOperationsContext(
            parent=parent,
            apply_workflow_update=actions.apply_workflow_update,
            finish_sequence_in_out_edit=actions.finish_sequence_in_out_edit,
            require_writable=actions.require_writable,
            set_download_plan=actions.set_download_plan,
            set_status=actions.set_status,
            reconcile_task_events=lambda *args, **kwargs: lifecycle.reconcile_task_events(*args, **kwargs),
            schedule_preview=lambda *args, **kwargs: preview.schedule(*args, **kwargs),
            refresh_assets=lambda *args, **kwargs: projectors.assets.refresh_assets(*args, **kwargs),
            refresh_highlights=lambda *args, **kwargs: projectors.highlights.refresh_highlights(
                *args, **kwargs
            ),
            refresh_documents=lambda *args, **kwargs: projectors.subtitles.refresh_documents(*args, **kwargs),
            refresh_tasks=lambda *args, **kwargs: projectors.tasks.refresh_tasks(*args, **kwargs),
            refresh_timeline=lambda *args, **kwargs: projectors.timeline.refresh_timeline(*args, **kwargs),
            refresh_preview_subtitles=lambda *args,
            **kwargs: projectors.timeline_subtitles.refresh_preview_subtitles(*args, **kwargs),
            refresh_recent_projects=lambda *args, **kwargs: projectors.workspace.refresh_recent_projects(
                *args, **kwargs
            ),
            state=state,
            finish_import_drop=lambda *args, **kwargs: timeline_assets.finish_import_drop(*args, **kwargs),
            updates=updates,
        )
    )
    timeline_assets = TimelineAssetOperations(
        TimelineAssetOperationsContext(
            parent=parent,
            require_writable=actions.require_writable,
            set_status=actions.set_status,
            snap_tolerance_frames=actions.snap_tolerance_frames,
            snap_targets=snapping.targets,
            refresh_assets=lambda *args, **kwargs: projectors.assets.refresh_assets(*args, **kwargs),
            refresh_documents=lambda *args, **kwargs: projectors.subtitles.refresh_documents(*args, **kwargs),
            refresh_tasks=lambda *args, **kwargs: projectors.tasks.refresh_tasks(*args, **kwargs),
            refresh_timeline=lambda *args, **kwargs: projectors.timeline.refresh_timeline(*args, **kwargs),
            refresh_preview_subtitles=lambda *args,
            **kwargs: projectors.timeline_subtitles.refresh_preview_subtitles(*args, **kwargs),
            state=state,
            start=lambda *args, **kwargs: tasks.start(*args, **kwargs),
            updates=updates,
        )
    )
    preview = PreviewGraphCoordinator(
        PreviewGraphCoordinatorContext(
            parent=parent,
            write_preview_snapshot=api.write_preview_snapshot,
            preview_executor=background.preview_executor,
            submit_background=lambda *args, **kwargs: background.submit(*args, **kwargs),
            state=state,
            create=lambda *args, **kwargs: tasks.create(*args, **kwargs),
            updates=updates,
        )
    )
    workspace = WorkspaceProjector(
        WorkspaceProjectorContext(
            discover_encoder_policy_options=api.discover_encoder_policy_options,
            installed_asr_models=api.installed_asr_models,
            recent_projects=api.recent_projects,
            runtime_tool_status=api.runtime_tool_status,
            submit_background=lambda *args, **kwargs: background.submit(*args, **kwargs),
            models=models,
            state=state,
            updates=updates,
        )
    )
    assets = AssetProjector(
        AssetProjectorContext(
            asset_thumbnail_paths=api.asset_thumbnail_paths,
            submit_background=lambda *args, **kwargs: background.submit(*args, **kwargs),
            models=models,
            refresh_timeline=lambda *args, **kwargs: projectors.timeline.refresh_timeline(*args, **kwargs),
            state=state,
        )
    )
    audio = AudioProjector(
        AudioProjectorContext(
            submit_background=lambda *args, **kwargs: background.submit(*args, **kwargs),
            models=models,
            state=state,
            updates=updates,
        )
    )
    highlights = HighlightProjector(
        HighlightProjectorContext(
            models=models,
            state=state,
        )
    )
    subtitles = SubtitleProjector(
        SubtitleProjectorContext(
            models=models,
            state=state,
        )
    )
    task_projection = TaskProjector(
        TaskProjectorContext(
            models=models,
            state=state,
            updates=updates,
        )
    )
    timeline = TimelineProjector(
        TimelineProjectorContext(
            invalidate_snapping=snapping.invalidate,
            update_snapping_clips=snapping.update_clips,
            models=models,
            schedule_preview=lambda *args, **kwargs: preview.schedule(*args, **kwargs),
            invalidate_audio_metrics=lambda *args, **kwargs: projectors.audio.invalidate_audio_metrics(
                *args, **kwargs
            ),
            state=state,
            updates=updates,
        )
    )
    timeline_subtitles = TimelineSubtitleProjector(
        TimelineSubtitleProjectorContext(
            models=models,
            state=state,
        )
    )
    projectors = PresentationProjectors(
        context=PresentationRefreshContext(
            updates=updates,
            invalidate_active_workflow=tasks.invalidate_active_workflow,
            refresh_active_workflow=tasks.refresh_active_workflow,
            rebuild_snapping=snapping.rebuild,
            schedule_preview=preview.schedule,
        ),
        workspace=workspace,
        assets=assets,
        timeline=timeline,
        timeline_subtitles=timeline_subtitles,
        tasks=task_projection,
        subtitles=subtitles,
        highlights=highlights,
        audio=audio,
    )
    lifecycle = ProjectLifecycle(
        ProjectLifecycleContext(
            parent=parent,
            cancel_timeline_filmstrip_requests=api.cancel_timeline_filmstrip_requests,
            cancel_preview_snapshot_requests=api.cancel_preview_snapshot_requests,
            create_project=api.create_project,
            set_status=actions.set_status,
            submit_background=lambda *args, **kwargs: background.submit(*args, **kwargs),
            models=models,
            schedule_preview=lambda *args, **kwargs: preview.schedule(*args, **kwargs),
            stop_preview=lambda *args, **kwargs: preview.stop(*args, **kwargs),
            refresh_project=lambda *args, **kwargs: projectors.refresh_project(*args, **kwargs),
            refresh_recent_projects=lambda *args, **kwargs: projectors.workspace.refresh_recent_projects(
                *args, **kwargs
            ),
            commit=lambda *args, **kwargs: settings_persistence.commit(*args, **kwargs),
            state=state,
            publish=lambda *args, **kwargs: tasks.publish(*args, **kwargs),
            reconcile_committed_results=lambda *args, **kwargs: tasks.reconcile_committed_results(
                *args, **kwargs
            ),
            reset_delivery_state=lambda *args, **kwargs: tasks.reset_delivery_state(*args, **kwargs),
            update_reconciliation_state=lambda *args, **kwargs: tasks.update_reconciliation_state(
                *args, **kwargs
            ),
            updates=updates,
        )
    )
    return DesktopSessionComponents(
        background=background,
        runtime_tools=runtime_tools,
        settings_persistence=settings_persistence,
        tasks=tasks,
        timeline_assets=timeline_assets,
        preview=preview,
        projectors=projectors,
        lifecycle=lifecycle,
    )
