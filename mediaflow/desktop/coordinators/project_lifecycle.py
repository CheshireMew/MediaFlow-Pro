from __future__ import annotations

import copy
import logging
import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QGuiApplication

from mediaflow.desktop.component_contexts import ProjectLifecycleContext
from mediaflow.desktop.session_state import (
    DesktopProject,
    ProjectInteractionSnapshot,
    TimelinePlacement,
)
from mediaflow.domain.collaboration import ProjectChangeEvent, project_write_paths_overlap
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.storage_names import (
    PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT,
    PROJECT_ROOT_PATH_UTF16_LIMIT,
    safe_child_path,
)

from .base import SessionCoordinator

logger = logging.getLogger(__name__)
PROJECT_CLOSE_TIMEOUT_SECONDS = 15.0


class ProjectLifecycle(SessionCoordinator[ProjectLifecycleContext]):
    projectEventReceived = Signal(object)

    def __init__(self, context: ProjectLifecycleContext):
        super().__init__(context)
        self._active_draft_path = ""
        self._deferred_events: list[ProjectChangeEvent] = []
        self._application: QGuiApplication | None = None
        self._unbind_handlers: list[Callable[[], None]] = []
        self.projectEventReceived.connect(self._on_project_event)
        application = QGuiApplication.instance()
        if isinstance(application, QGuiApplication):
            self._application = application
            application.focusObjectChanged.connect(self._on_focus_object_changed)

    def replace(self, candidate: DesktopProject) -> None:
        if self._context.state.requests.closing_project is not None:
            candidate.close()
            raise RuntimeError("正在释放上一个项目，请稍候再打开项目")
        try:
            project_document = candidate.get_project()
            candidate.timeline(project_document.main_sequence_id)
            candidate.list_tasks()
        except Exception:
            candidate.close()
            raise
        previous = self._context.state.binding.current
        previous_subscription = self._context.state.binding.task_subscription_token
        previous_project_subscription = self._context.state.binding.project_subscription_token
        previous_workspace_subscription = self._context.state.binding.workspace_subscription_token
        preserved_selection = self._capture_interaction()
        try:
            self._prepare_unbind()
        except BaseException:
            candidate.close()
            raise
        try:
            self._bind(candidate)
        except Exception:
            if (
                self._context.state.binding.current
                and self._context.state.binding.task_subscription_token is not None
            ):
                self._context.state.binding.require_current().unsubscribe_task_events(
                    self._context.state.binding.task_subscription_token
                )
            if (
                self._context.state.binding.current
                and self._context.state.binding.project_subscription_token is not None
            ):
                self._unsubscribe_project_events(
                    self._context.state.binding.current,
                    self._context.state.binding.project_subscription_token,
                )
            if (
                self._context.state.binding.current
                and self._context.state.binding.workspace_subscription_token is not None
            ):
                self._context.state.binding.require_current().unsubscribe_workspace_events(
                    self._context.state.binding.workspace_subscription_token
                )
            candidate.close()
            if previous is None:
                self._context.state.binding.current = None
                self.close(close_in_background=False)
            else:
                if previous_subscription is not None:
                    previous.unsubscribe_task_events(previous_subscription)
                if previous_project_subscription is not None:
                    self._unsubscribe_project_events(
                        previous,
                        previous_project_subscription,
                    )
                if previous_workspace_subscription is not None:
                    previous.unsubscribe_workspace_events(previous_workspace_subscription)
                self._restore_interaction(preserved_selection)
                self._bind(previous, reset_selection=False)
            raise
        if previous is not None:
            if previous_subscription is not None:
                previous.unsubscribe_task_events(previous_subscription)
            if previous_project_subscription is not None:
                self._unsubscribe_project_events(
                    previous,
                    previous_project_subscription,
                )
            if previous_workspace_subscription is not None:
                previous.unsubscribe_workspace_events(previous_workspace_subscription)
            self._dispose(previous, close_in_background=True)
        self.remember_recent(candidate.project_dir)

    def create_and_open(
        self,
        parent: Path,
        name: str,
        *,
        profile: ProjectProfile | None = None,
        ensure_unique: bool = False,
    ) -> None:
        root, display_name = self._creation_target(
            parent,
            name,
            ensure_unique=ensure_unique,
        )
        candidate = self._context.create_project(root, display_name, profile)
        self.replace(candidate)

    def create_sample_and_open(self, parent: Path) -> None:
        root, display_name = self._creation_target(
            parent,
            "MediaFlow 示例项目",
            ensure_unique=True,
        )
        candidate = self._context.create_project(root, display_name)
        try:
            candidate.populate_sample_project()
        except BaseException:
            candidate.close()
            raise
        self.replace(candidate)

    @staticmethod
    def _creation_target(
        parent: Path,
        name: str,
        *,
        ensure_unique: bool = False,
    ) -> tuple[Path, str]:
        display_name = name.strip()
        if not display_name:
            sequence = 1
            while True:
                display_name = f"未命名项目 {sequence}"
                root = safe_child_path(
                    parent,
                    display_name,
                    max_path_utf16_units=PROJECT_ROOT_PATH_UTF16_LIMIT,
                    max_component_utf16_units=(PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT),
                )
                if not root.exists():
                    return root, display_name
                sequence += 1
        root = safe_child_path(
            parent,
            display_name,
            max_path_utf16_units=PROJECT_ROOT_PATH_UTF16_LIMIT,
            max_component_utf16_units=PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT,
        )
        if not ensure_unique:
            return root, display_name
        suffix = 2
        while root.exists():
            collision_suffix = f" ({suffix})"
            root = safe_child_path(
                parent,
                display_name,
                suffix=collision_suffix,
                max_path_utf16_units=PROJECT_ROOT_PATH_UTF16_LIMIT,
                max_component_utf16_units=(PROJECT_DIRECTORY_COMPONENT_UTF16_LIMIT),
            )
            suffix += 1
        if suffix > 2:
            display_name = f"{display_name} ({suffix - 1})"
        return root, display_name

    def _bind(self, project: DesktopProject, *, reset_selection: bool = True) -> None:
        self._context.state.binding.generation += 1
        generation = self._context.state.binding.generation
        if reset_selection:
            self.reset_interaction()
        self._context.state.binding.current = project
        current = self._context.state.binding.require_current().get_project()
        self._context.state.binding.project_id = current.id
        self._context.state.binding.active_sequence_id = current.main_sequence_id
        self._context.state.binding.timeline = project.timeline(
            self._context.state.binding.active_sequence_id
        )
        self._context.reset_delivery_state()
        initial_tasks, self._context.state.tasks.cursor = (
            self._context.state.binding.require_current().task_snapshot()
        )
        self._context.state.tasks.items = {task.id: task for task in initial_tasks}
        self._context.state.tasks.revisions = {task.id: task.revision for task in initial_tasks}
        self._context.state.binding.task_subscription_token = (
            self._context.state.binding.require_current().subscribe_task_events(
                lambda event: self._context.publish((generation, event)),
                include_snapshot=False,
            )
        )
        self._context.state.binding.project_subscription_token = project.subscribe_project_events(
            lambda event: self.projectEventReceived.emit((generation, event)),
            include_snapshot=False,
        )
        self._context.state.binding.workspace_subscription_token = project.subscribe_workspace_events(
            self._context.updates.receive_workspace_command
        )
        self.reconcile_task_events()
        self._context.refresh_project()

    @Slot(object)
    def _on_project_event(self, envelope: object) -> None:
        if not isinstance(envelope, tuple) or len(envelope) != 2:
            return
        generation, event = envelope
        if (
            generation != self._context.state.binding.generation
            or not isinstance(event, ProjectChangeEvent)
            or self._context.state.binding.current is None
            or self._context.state.requests.shutting_down
        ):
            return
        draft_paths = current.draft_paths if (current := self._context.state.binding.current) else ()
        if any(
            project_write_paths_overlap(draft, path) for draft in draft_paths for path in event.write_set
        ):
            self._deferred_events.append(event)
            self._context.set_status("外部修改与当前输入冲突，已保护未提交内容")
            return
        self._apply_project_event(event)

    def _apply_project_event(self, event: ProjectChangeEvent) -> None:
        current = self._context.state.binding.current
        if current is None:
            return
        try:
            current.reload_external_changes()
            available_sequences = {item.id for item in current.list_sequences()}
            if self._context.state.binding.active_sequence_id not in available_sequences:
                self._context.state.binding.active_sequence_id = current.get_project().main_sequence_id
            self._context.state.binding.timeline = current.timeline(
                self._context.state.binding.active_sequence_id
            )
            self._context.refresh_project()
            self._context.updates.commit(selection=True)
            self._context.updates.commit(history=True)
            self._context.schedule_preview()
            if event.actor.kind != "system":
                self._context.set_status(
                    "已实时同步 %1 的修改",
                    event.actor.name or event.actor.kind,
                )
        except Exception as error:
            self._context.updates.report_error(f"无法投影项目修改：{error}")

    @Slot(object)
    def _on_focus_object_changed(self, focus: object) -> None:
        next_path = ""
        property_reader = getattr(focus, "property", None)
        if callable(property_reader):
            next_path = str(property_reader("collaborationPath") or "").rstrip("/")
        if next_path == self._active_draft_path:
            return
        self._active_draft_path = next_path
        self._flush_deferred_events()

    def _flush_deferred_events(self) -> None:
        if not self._deferred_events:
            return
        applicable: list[ProjectChangeEvent] = []
        retained: list[ProjectChangeEvent] = []
        for event in self._deferred_events:
            current = self._context.state.binding.current
            if current is not None and any(
                project_write_paths_overlap(draft, path)
                for draft in current.draft_paths for path in event.write_set
            ):
                retained.append(event)
            else:
                applicable.append(event)
        self._deferred_events = retained
        if applicable:
            self._apply_project_event(applicable[-1])

    def resolve_collaboration_conflict(self, resolution: str) -> None:
        current = self._context.state.binding.current
        if current is None:
            raise RuntimeError("当前没有打开的项目")
        focus = QGuiApplication.focusObject()
        set_focus = getattr(focus, "setFocus", None)
        if callable(set_focus):
            set_focus(False)
        if self._active_draft_path:
            current.end_draft(self._active_draft_path)
        self._active_draft_path = ""
        current.resolve_pending_conflict(resolution)
        current.reload_external_changes()
        if self._deferred_events:
            latest = self._deferred_events[-1]
            self._deferred_events.clear()
            self._apply_project_event(latest)
        else:
            self._context.state.binding.timeline = current.timeline(
                self._context.state.binding.active_sequence_id
            )
            self._context.refresh_project()
            self._context.updates.commit(selection=True)
            self._context.updates.commit(history=True)
            self._context.schedule_preview()
        if resolution == "keep_local":
            self._context.set_status("已保留你的修改")
        else:
            self._context.set_status("已采用最新项目内容")

    def replay_task_events(self) -> None:
        if not self._context.state.binding.current:
            return
        for event in self._context.state.binding.require_current().task_events_after(
            self._context.state.tasks.cursor
        ):
            self._context.publish((self._context.state.binding.generation, event))

    def reconcile_task_events(self) -> None:
        self._context.reconcile_committed_results()
        self.replay_task_events()
        self._context.update_reconciliation_state()

    def _capture_interaction(self) -> ProjectInteractionSnapshot:
        return ProjectInteractionSnapshot(
            selection=copy.deepcopy(self._context.state.selection),
            pending_profile_asset_id=self._context.state.assets.pending_profile_asset_id,
            pending_profile_label=self._context.state.assets.pending_profile_label,
            pending_profile_placement=self._context.state.assets.pending_profile_placement,
            pending_batch_ids=copy.deepcopy(self._context.state.assets.pending_batch_ids),
            pending_batch_placement=self._context.state.assets.pending_batch_placement,
            pending_import_tasks=copy.deepcopy(self._context.state.assets.pending_import_tasks),
            pending_import_batches=copy.deepcopy(self._context.state.assets.pending_import_batches),
            pending_relink_asset_id=self._context.state.assets.pending_relink_asset_id,
            pending_relink_path=self._context.state.assets.pending_relink_path,
            download_plan=copy.deepcopy(self._context.state.download.plan),
            selected_download_entries=set(self._context.state.download.selected_entries),
            pending_preview_range=self._context.state.presentation.pending_preview_range,
        )

    def _restore_interaction(self, values: ProjectInteractionSnapshot) -> None:
        self._context.state.selection = copy.deepcopy(values.selection)
        self._context.state.assets.pending_profile_asset_id = values.pending_profile_asset_id
        self._context.state.assets.pending_profile_label = values.pending_profile_label
        self._context.state.assets.pending_profile_placement = values.pending_profile_placement
        self._context.state.assets.pending_batch_ids = copy.deepcopy(values.pending_batch_ids)
        self._context.state.assets.pending_batch_placement = values.pending_batch_placement
        self._context.state.assets.pending_import_tasks = copy.deepcopy(values.pending_import_tasks)
        self._context.state.assets.pending_import_batches = copy.deepcopy(values.pending_import_batches)
        self._context.state.assets.pending_relink_asset_id = values.pending_relink_asset_id
        self._context.state.assets.pending_relink_path = values.pending_relink_path
        self._context.state.download.plan = copy.deepcopy(values.download_plan)
        self._context.state.download.selected_entries = set(values.selected_download_entries)
        self._context.state.presentation.pending_preview_range = values.pending_preview_range

    def reset_interaction(self) -> None:
        self._context.state.selection.asset_ids = []
        self._context.state.selection.clip_ids = []
        self._context.state.selection.compound_id = ""
        self._context.state.selection.document_id = ""
        self._context.state.selection.subtitle_segment_ids = []
        self._context.state.selection.subtitle_placement_id = ""
        self._context.state.selection.highlight_id = ""
        self._context.state.selection.audio_bus_id = ""
        self._context.state.selection.audio_effect_id = ""
        self._context.state.selection.transition_id = ""
        self._context.state.selection.marker_id = ""
        self._context.state.selection.range_id = ""
        self._context.state.selection.watermark_asset_id = ""
        self._context.state.selection.range_in_frame = None
        self._context.state.assets.pending_profile_asset_id = ""
        self._context.state.assets.pending_profile_label = ""
        self._context.state.assets.pending_profile_placement = TimelinePlacement()
        self._context.state.assets.pending_batch_ids = []
        self._context.state.assets.pending_batch_placement = TimelinePlacement()
        self._context.state.assets.pending_import_tasks = {}
        self._context.state.assets.pending_import_batches = {}
        self._context.state.assets.pending_relink_asset_id = ""
        self._context.state.assets.pending_relink_path = ""
        self._context.state.download.plan = None
        self._context.state.download.selected_entries = set()
        self._context.state.presentation.pending_preview_range = None

    def remember_recent(self, project_dir: Path) -> None:
        project_path = str(project_dir.expanduser().resolve())
        project_key = self._recent_key(project_path)
        candidate = self._context.state.desktop_settings.model_copy(deep=True)
        candidate.ui.recent_project_paths = [
            project_path,
            *(path for path in candidate.ui.recent_project_paths if self._recent_key(path) != project_key),
        ][:10]
        try:
            self._context.commit(candidate)
        except Exception as error:
            self._context.updates.report_error(f"项目已打开，但无法更新最近项目记录：{error}")
        self._context.refresh_recent_projects()

    def forget_recent(self, project_dir: Path) -> bool:
        project_key = self._recent_key(project_dir)
        remaining = [
            path
            for path in self._context.state.desktop_settings.ui.recent_project_paths
            if self._recent_key(path) != project_key
        ]
        if len(remaining) == len(self._context.state.desktop_settings.ui.recent_project_paths):
            return False
        candidate = self._context.state.desktop_settings.model_copy(deep=True)
        candidate.ui.recent_project_paths = remaining
        self._context.commit(candidate)
        self._context.refresh_recent_projects()
        return True

    @staticmethod
    def _recent_key(path: str | Path) -> str:
        return os.path.normcase(str(Path(path).expanduser().resolve()))

    def close(self, *, close_in_background: bool = True) -> None:
        self._prepare_unbind()
        self._context.state.binding.generation += 1
        if (
            self._context.state.binding.current
            and self._context.state.binding.task_subscription_token is not None
        ):
            self._context.state.binding.require_current().unsubscribe_task_events(
                self._context.state.binding.task_subscription_token
            )
        self._context.state.binding.task_subscription_token = None
        if (
            self._context.state.binding.current
            and self._context.state.binding.project_subscription_token is not None
        ):
            self._unsubscribe_project_events(
                self._context.state.binding.current,
                self._context.state.binding.project_subscription_token,
            )
        self._context.state.binding.project_subscription_token = None
        if (
            self._context.state.binding.current
            and self._context.state.binding.workspace_subscription_token is not None
        ):
            self._context.state.binding.require_current().unsubscribe_workspace_events(
                self._context.state.binding.workspace_subscription_token
            )
        self._context.state.binding.workspace_subscription_token = None
        self._active_draft_path = ""
        self._deferred_events.clear()
        self._context.state.tasks.cursor = 0
        closing_project = self._context.state.binding.current
        self._context.state.binding.current = None
        self._context.state.binding.project_id = ""
        self._context.state.tasks.revisions = {}
        self._context.state.tasks.items = {}
        self._context.state.binding.timeline = None
        self._context.state.binding.active_sequence_id = ""
        self._context.state.selection.asset_ids = []
        self._context.state.selection.clip_ids = []
        self._context.state.selection.compound_id = ""
        self._context.state.selection.document_id = ""
        self._context.state.selection.subtitle_segment_ids = []
        self._context.state.selection.subtitle_placement_id = ""
        self._context.state.selection.highlight_id = ""
        self._context.state.selection.audio_bus_id = ""
        self._context.state.selection.audio_effect_id = ""
        self._context.state.selection.transition_id = ""
        self._context.state.selection.marker_id = ""
        self._context.state.selection.range_id = ""
        self._context.state.selection.range_in_frame = None
        self._context.state.download.plan = None
        self._context.state.download.selected_entries = set()
        self._context.models.download_entries.set_items([])
        self._context.updates.commit(download_plan=True)
        self._context.stop_preview()
        self._context.state.presentation.preview_graph_path = ""
        self._context.state.presentation.preview_status = "empty"
        self._context.state.presentation.preview_error = ""
        self._context.state.presentation.filmstrip_frames.clear()
        self.cancel_filmstrip(closing_project)
        self.cancel_preview(closing_project)
        self._context.state.presentation.hdr_preview_active = False
        self._context.state.presentation.preview_subtitles = []
        self._context.state.presentation.preview_subtitles_by_track = {}
        self._context.state.assets.waveform_cache.clear()
        self._context.state.assets.waveform_pending.clear()
        self._context.state.assets.thumbnail_paths.clear()
        self._context.state.assets.thumbnail_pending_request = None
        self._context.state.assets.thumbnail_refresh_requested = False
        self._context.state.presentation.audio_metrics = {}
        if self._context.state.assets.pending_profile_asset_id:
            self._context.state.assets.pending_profile_asset_id = ""
            self._context.state.assets.pending_profile_label = ""
            self._context.state.assets.pending_profile_placement = TimelinePlacement()
            self._context.updates.commit(profile_confirmation=True)
        self._context.state.assets.pending_batch_ids = []
        self._context.state.assets.pending_batch_placement = TimelinePlacement()
        self._context.state.assets.pending_import_tasks = {}
        self._context.state.assets.pending_import_batches = {}
        if self._context.state.assets.pending_relink_asset_id:
            self._context.state.assets.pending_relink_asset_id = ""
            self._context.state.assets.pending_relink_path = ""
            self._context.updates.commit(relink_confirmation=True)
        self._context.models.assets.set_items([])
        self._context.models.sequences.set_items([])
        self._context.models.tracks.set_items([])
        self._context.models.clips.set_items([])
        self._context.models.compound_clips.set_items([])
        self._context.models.transitions.set_items([])
        self._context.models.markers.set_items([])
        self._context.models.ranges.set_items([])
        self._context.models.tasks.set_items([])
        self._context.models.documents.set_items([])
        self._context.models.segments.set_items([])
        self._context.models.subtitle_placements.set_items([])
        self._context.models.highlights.set_items([])
        self._context.models.audio_buses.set_items([])
        self._context.models.audio_effects.set_items([])
        self._context.models.audio_effect_parameters.set_items([])
        self._context.updates.commit(audio_metrics=True)
        self._context.updates.commit(workflow=True)
        self._context.updates.commit(preview_graph=True)
        if closing_project:
            self._dispose(
                closing_project,
                close_in_background=close_in_background,
            )

    def cancel_filmstrip(self, project: DesktopProject | None = None) -> None:
        self._context.state.requests.filmstrip_id += 1
        target = project or self._context.state.binding.current
        future = self._context.state.requests.filmstrip_future
        if future is not None:
            future.cancel()
            self._context.state.requests.filmstrip_future = None
        if target is not None:
            self._context.cancel_timeline_filmstrip_requests(
                target.project_dir,
                request_owner=target.actor_id,
                request_generation=self._context.state.requests.filmstrip_id,
            )

    def cancel_preview(self, project: DesktopProject | None = None) -> None:
        self._context.stop_preview()
        self._context.state.requests.preview_id += 1
        future = self._context.state.requests.preview_future
        if future is not None:
            future.cancel()
            self._context.state.requests.preview_future = None
        target = project or self._context.state.binding.current
        if target is not None:
            self._context.cancel_preview_snapshot_requests(
                target.project_dir, request_owner=target.actor_id,
                request_generation=self._context.state.requests.preview_id,
            )

    def add_unbind_handler(self, handler: Callable[[], None]) -> None:
        self._unbind_handlers.append(handler)

    def _prepare_unbind(self) -> None:
        for handler in self._unbind_handlers:
            handler()

    def shutdown(self) -> None:
        try:
            self.close(close_in_background=False)
        finally:
            application = self._application
            self._application = None
            if application is not None:
                try:
                    application.focusObjectChanged.disconnect(self._on_focus_object_changed)
                except (RuntimeError, TypeError):
                    pass

    def _dispose(
        self,
        project: DesktopProject,
        *,
        close_in_background: bool,
    ) -> None:
        if close_in_background and not self._context.state.requests.shutting_down:
            pending_project = self._context.state.requests.closing_project
            if pending_project is not None and pending_project is not project:
                raise RuntimeError("已有项目资源等待释放")
            if self._context.state.requests.project_close_future is not None:
                raise RuntimeError("项目资源仍在释放中")
            self._context.state.requests.project_close_id += 1
            close_id = self._context.state.requests.project_close_id
            project_path = str(project.project_dir)
            self._context.state.requests.closing_project = project
            self._context.state.requests.closing_project_error = ""
            self._context.updates.commit(project=True)
            self._context.set_status("正在关闭项目并释放文件…")
            self._context.state.requests.project_close_future = self._context.submit_background(
                "project_close",
                (close_id, project_path),
                lambda: project.close(
                    timeout=PROJECT_CLOSE_TIMEOUT_SECONDS,
                ),
            )
        else:
            project.close()

    @staticmethod
    def _unsubscribe_project_events(project: DesktopProject, token: int) -> None:
        project.unsubscribe_project_events(token)

    def retry_close(self) -> None:
        project = self._context.state.requests.closing_project
        if project is None:
            return
        if self._context.state.requests.project_close_future is not None:
            raise RuntimeError("项目资源仍在释放中")
        self._dispose(project, close_in_background=True)
