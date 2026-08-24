from __future__ import annotations

import copy
import logging
import os
from pathlib import Path

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QGuiApplication

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


class ProjectLifecycle(SessionCoordinator):
    projectEventReceived = Signal(object)

    def __init__(self, session):
        super().__init__(session)
        self._active_draft_path = ""
        self._deferred_events: list[ProjectChangeEvent] = []
        self._application: QGuiApplication | None = None
        self.projectEventReceived.connect(self._on_project_event)
        application = QGuiApplication.instance()
        if isinstance(application, QGuiApplication):
            self._application = application
            application.focusObjectChanged.connect(self._on_focus_object_changed)

    def replace(self, candidate: DesktopProject) -> None:
        if self._session.state.requests.closing_project is not None:
            candidate.close()
            raise RuntimeError("正在释放上一个项目，请稍候再打开项目")
        try:
            project_document = candidate.get_project()
            candidate.timeline(project_document.main_sequence_id)
            candidate.list_tasks()
        except Exception:
            candidate.close()
            raise
        previous = self._session.state.binding.current
        previous_subscription = self._session.state.binding.task_subscription_token
        previous_project_subscription = self._session.state.binding.project_subscription_token
        previous_workspace_subscription = self._session.state.binding.workspace_subscription_token
        preserved_selection = self._capture_interaction()
        try:
            self._bind(candidate)
        except Exception:
            if (
                self._session.state.binding.current
                and self._session.state.binding.task_subscription_token is not None
            ):
                self._session.state.binding.require_current().unsubscribe_task_events(
                    self._session.state.binding.task_subscription_token
                )
            if (
                self._session.state.binding.current
                and self._session.state.binding.project_subscription_token is not None
            ):
                self._unsubscribe_project_events(
                    self._session.state.binding.current,
                    self._session.state.binding.project_subscription_token,
                )
            if (
                self._session.state.binding.current
                and self._session.state.binding.workspace_subscription_token is not None
            ):
                self._session.state.binding.require_current().unsubscribe_workspace_events(
                    self._session.state.binding.workspace_subscription_token
                )
            candidate.close()
            if previous is None:
                self._session.state.binding.current = None
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
        candidate = self._session._api.create_project(root, display_name, profile)
        self.replace(candidate)

    def create_sample_and_open(self, parent: Path) -> None:
        root, display_name = self._creation_target(
            parent,
            "MediaFlow 示例项目",
            ensure_unique=True,
        )
        candidate = self._session._api.create_project(root, display_name)
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
        self._session.state.binding.generation += 1
        generation = self._session.state.binding.generation
        if reset_selection:
            self.reset_interaction()
        self._session.state.binding.current = project
        current = self._session.state.binding.require_current().get_project()
        self._session.state.binding.project_id = current.id
        self._session.state.binding.active_sequence_id = current.main_sequence_id
        self._session.state.binding.timeline = project.timeline(
            self._session.state.binding.active_sequence_id
        )
        self._session.tasks.reset_delivery_state()
        initial_tasks, self._session.state.tasks.cursor = (
            self._session.state.binding.require_current().task_snapshot()
        )
        self._session.state.tasks.items = {task.id: task for task in initial_tasks}
        self._session.state.tasks.revisions = {task.id: task.revision for task in initial_tasks}
        self._session.state.binding.task_subscription_token = (
            self._session.state.binding.require_current().subscribe_task_events(
                lambda event: self._session.tasks.publish((generation, event)),
                include_snapshot=False,
            )
        )
        self._session.state.binding.project_subscription_token = project.subscribe_project_events(
            lambda event: self.projectEventReceived.emit((generation, event)),
            include_snapshot=False,
        )
        self._session.state.binding.workspace_subscription_token = project.subscribe_workspace_events(
            self._session.updates.receive_workspace_command
        )
        self.reconcile_task_events()
        self._session.projectors.refresh_project()

    @Slot(object)
    def _on_project_event(self, envelope: object) -> None:
        if not isinstance(envelope, tuple) or len(envelope) != 2:
            return
        generation, event = envelope
        if (
            generation != self._session.state.binding.generation
            or not isinstance(event, ProjectChangeEvent)
            or self._session.state.binding.current is None
            or self._session.state.requests.shutting_down
        ):
            return
        if event.actor.id == self._session.state.binding.require_current().actor_id:
            # The command response has already projected this desktop client's
            # write. Replaying its journal event can erase a pending deferred
            # dataChanged notification without producing a replacement.
            return
        if self._active_draft_path and any(
            project_write_paths_overlap(self._active_draft_path, path) for path in event.write_set
        ):
            self._deferred_events.append(event)
            self._session._set_status("外部修改与当前输入冲突，已保护未提交内容")
            return
        self._apply_project_event(event)

    def _apply_project_event(self, event: ProjectChangeEvent) -> None:
        current = self._session.state.binding.current
        if current is None:
            return
        try:
            current.reload_external_changes()
            available_sequences = {item.id for item in current.list_sequences()}
            if self._session.state.binding.active_sequence_id not in available_sequences:
                self._session.state.binding.active_sequence_id = current.get_project().main_sequence_id
            self._session.state.binding.timeline = current.timeline(
                self._session.state.binding.active_sequence_id
            )
            self._session.projectors.refresh_project()
            self._session.updates.commit(selection=True)
            self._session.updates.commit(history=True)
            self._session.projectors.timeline.schedule_preview_graph()
            self._session._set_status(
                "已实时同步 %1 的修改",
                event.actor.name or event.actor.kind,
            )
        except Exception as error:
            self._session.updates.report_error(f"无法投影项目修改：{error}")

    @Slot(object)
    def _on_focus_object_changed(self, focus: object) -> None:
        next_path = ""
        property_reader = getattr(focus, "property", None)
        if callable(property_reader):
            next_path = str(property_reader("collaborationPath") or "").rstrip("/")
        if next_path == self._active_draft_path:
            return
        current = self._session.state.binding.current
        if current is not None and self._active_draft_path:
            current.end_draft(self._active_draft_path)
        self._active_draft_path = next_path
        if current is not None and next_path:
            current.begin_draft(next_path)
        self._flush_deferred_events()

    def _flush_deferred_events(self) -> None:
        if not self._deferred_events:
            return
        applicable: list[ProjectChangeEvent] = []
        retained: list[ProjectChangeEvent] = []
        for event in self._deferred_events:
            if self._active_draft_path and any(
                project_write_paths_overlap(self._active_draft_path, path) for path in event.write_set
            ):
                retained.append(event)
            else:
                applicable.append(event)
        self._deferred_events = retained
        if applicable:
            self._apply_project_event(applicable[-1])

    def resolve_collaboration_conflict(self, resolution: str) -> None:
        current = self._session.state.binding.current
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
            self._session.state.binding.timeline = current.timeline(
                self._session.state.binding.active_sequence_id
            )
            self._session.projectors.refresh_project()
            self._session.updates.commit(selection=True)
            self._session.updates.commit(history=True)
            self._session.projectors.timeline.schedule_preview_graph()
        if resolution == "keep_local":
            self._session._set_status("已保留你的修改")
        else:
            self._session._set_status("已采用最新项目内容")

    def replay_task_events(self) -> None:
        if not self._session.state.binding.current:
            return
        for event in self._session.state.binding.require_current().task_events_after(
            self._session.state.tasks.cursor
        ):
            self._session.tasks.publish((self._session.state.binding.generation, event))

    def reconcile_task_events(self) -> None:
        self._session.tasks.reconcile_committed_results()
        self.replay_task_events()

    def _capture_interaction(self) -> ProjectInteractionSnapshot:
        return ProjectInteractionSnapshot(
            selection=copy.deepcopy(self._session.state.selection),
            pending_profile_asset_id=self._session.state.assets.pending_profile_asset_id,
            pending_profile_label=self._session.state.assets.pending_profile_label,
            pending_profile_placement=self._session.state.assets.pending_profile_placement,
            pending_batch_ids=copy.deepcopy(self._session.state.assets.pending_batch_ids),
            pending_batch_placement=self._session.state.assets.pending_batch_placement,
            pending_import_tasks=copy.deepcopy(self._session.state.assets.pending_import_tasks),
            pending_import_batches=copy.deepcopy(self._session.state.assets.pending_import_batches),
            pending_relink_asset_id=self._session.state.assets.pending_relink_asset_id,
            pending_relink_path=self._session.state.assets.pending_relink_path,
            download_plan=copy.deepcopy(self._session.state.download.plan),
            selected_download_entries=set(self._session.state.download.selected_entries),
            pending_preview_range=self._session.state.presentation.pending_preview_range,
        )

    def _restore_interaction(self, values: ProjectInteractionSnapshot) -> None:
        self._session.state.selection = copy.deepcopy(values.selection)
        self._session.state.assets.pending_profile_asset_id = values.pending_profile_asset_id
        self._session.state.assets.pending_profile_label = values.pending_profile_label
        self._session.state.assets.pending_profile_placement = values.pending_profile_placement
        self._session.state.assets.pending_batch_ids = copy.deepcopy(values.pending_batch_ids)
        self._session.state.assets.pending_batch_placement = values.pending_batch_placement
        self._session.state.assets.pending_import_tasks = copy.deepcopy(values.pending_import_tasks)
        self._session.state.assets.pending_import_batches = copy.deepcopy(values.pending_import_batches)
        self._session.state.assets.pending_relink_asset_id = values.pending_relink_asset_id
        self._session.state.assets.pending_relink_path = values.pending_relink_path
        self._session.state.download.plan = copy.deepcopy(values.download_plan)
        self._session.state.download.selected_entries = set(values.selected_download_entries)
        self._session.state.presentation.pending_preview_range = values.pending_preview_range

    def reset_interaction(self) -> None:
        self._session.state.selection.asset_ids = []
        self._session.state.selection.clip_ids = []
        self._session.state.selection.compound_id = ""
        self._session.state.selection.document_id = ""
        self._session.state.selection.subtitle_segment_ids = []
        self._session.state.selection.subtitle_placement_id = ""
        self._session.state.selection.highlight_id = ""
        self._session.state.selection.audio_bus_id = ""
        self._session.state.selection.audio_effect_id = ""
        self._session.state.selection.transition_id = ""
        self._session.state.selection.marker_id = ""
        self._session.state.selection.range_id = ""
        self._session.state.selection.watermark_asset_id = ""
        self._session.state.selection.range_in_frame = None
        self._session.state.assets.pending_profile_asset_id = ""
        self._session.state.assets.pending_profile_label = ""
        self._session.state.assets.pending_profile_placement = TimelinePlacement()
        self._session.state.assets.pending_batch_ids = []
        self._session.state.assets.pending_batch_placement = TimelinePlacement()
        self._session.state.assets.pending_import_tasks = {}
        self._session.state.assets.pending_import_batches = {}
        self._session.state.assets.pending_relink_asset_id = ""
        self._session.state.assets.pending_relink_path = ""
        self._session.state.download.plan = None
        self._session.state.download.selected_entries = set()
        self._session.state.presentation.pending_preview_range = None

    def remember_recent(self, project_dir: Path) -> None:
        project_path = str(project_dir.expanduser().resolve())
        project_key = self._recent_key(project_path)
        candidate = self._session.state.desktop_settings.model_copy(deep=True)
        candidate.ui.recent_project_paths = [
            project_path,
            *(path for path in candidate.ui.recent_project_paths if self._recent_key(path) != project_key),
        ][:10]
        try:
            self._session.settings_persistence.commit(candidate)
        except Exception as error:
            self._session.updates.report_error(f"项目已打开，但无法更新最近项目记录：{error}")
        self._session.projectors.workspace.refresh_recent_projects()

    def forget_recent(self, project_dir: Path) -> bool:
        project_key = self._recent_key(project_dir)
        remaining = [
            path
            for path in self._session.state.desktop_settings.ui.recent_project_paths
            if self._recent_key(path) != project_key
        ]
        if len(remaining) == len(self._session.state.desktop_settings.ui.recent_project_paths):
            return False
        candidate = self._session.state.desktop_settings.model_copy(deep=True)
        candidate.ui.recent_project_paths = remaining
        self._session.settings_persistence.commit(candidate)
        self._session.projectors.workspace.refresh_recent_projects()
        return True

    @staticmethod
    def _recent_key(path: str | Path) -> str:
        return os.path.normcase(str(Path(path).expanduser().resolve()))

    def close(self, *, close_in_background: bool = True) -> None:
        self._session.state.binding.generation += 1
        if (
            self._session.state.binding.current
            and self._session.state.binding.task_subscription_token is not None
        ):
            self._session.state.binding.require_current().unsubscribe_task_events(
                self._session.state.binding.task_subscription_token
            )
        self._session.state.binding.task_subscription_token = None
        if (
            self._session.state.binding.current
            and self._session.state.binding.project_subscription_token is not None
        ):
            self._unsubscribe_project_events(
                self._session.state.binding.current,
                self._session.state.binding.project_subscription_token,
            )
        self._session.state.binding.project_subscription_token = None
        if (
            self._session.state.binding.current
            and self._session.state.binding.workspace_subscription_token is not None
        ):
            self._session.state.binding.require_current().unsubscribe_workspace_events(
                self._session.state.binding.workspace_subscription_token
            )
        self._session.state.binding.workspace_subscription_token = None
        self._active_draft_path = ""
        self._deferred_events.clear()
        self._session.state.tasks.cursor = 0
        closing_project = self._session.state.binding.current
        self._session.state.binding.current = None
        self._session.state.binding.project_id = ""
        self._session.state.tasks.revisions = {}
        self._session.state.tasks.items = {}
        self._session.state.binding.timeline = None
        self._session.state.binding.active_sequence_id = ""
        self._session.state.selection.asset_ids = []
        self._session.state.selection.clip_ids = []
        self._session.state.selection.compound_id = ""
        self._session.state.selection.document_id = ""
        self._session.state.selection.subtitle_segment_ids = []
        self._session.state.selection.subtitle_placement_id = ""
        self._session.state.selection.highlight_id = ""
        self._session.state.selection.audio_bus_id = ""
        self._session.state.selection.audio_effect_id = ""
        self._session.state.selection.transition_id = ""
        self._session.state.selection.marker_id = ""
        self._session.state.selection.range_id = ""
        self._session.state.selection.range_in_frame = None
        self._session.state.download.plan = None
        self._session.state.download.selected_entries = set()
        self._session.models.download_entries.set_items([])
        self._session.updates.commit(download_plan=True)
        self._session.projectors.timeline.stop_preview()
        self._session.state.presentation.preview_graph_path = ""
        self._session.state.presentation.filmstrip_frames.clear()
        self.cancel_filmstrip(closing_project)
        self._session.state.presentation.hdr_preview_active = False
        self._session.state.presentation.preview_subtitles = []
        self._session.state.presentation.preview_subtitles_by_track = {}
        self._session.state.assets.waveform_cache.clear()
        self._session.state.assets.waveform_pending.clear()
        self._session.state.assets.thumbnail_paths.clear()
        self._session.state.assets.thumbnail_pending_request = None
        self._session.state.assets.thumbnail_refresh_requested = False
        self._session.state.presentation.audio_metrics = {}
        if self._session.state.assets.pending_profile_asset_id:
            self._session.state.assets.pending_profile_asset_id = ""
            self._session.state.assets.pending_profile_label = ""
            self._session.state.assets.pending_profile_placement = TimelinePlacement()
            self._session.updates.commit(profile_confirmation=True)
        self._session.state.assets.pending_batch_ids = []
        self._session.state.assets.pending_batch_placement = TimelinePlacement()
        self._session.state.assets.pending_import_tasks = {}
        self._session.state.assets.pending_import_batches = {}
        if self._session.state.assets.pending_relink_asset_id:
            self._session.state.assets.pending_relink_asset_id = ""
            self._session.state.assets.pending_relink_path = ""
            self._session.updates.commit(relink_confirmation=True)
        self._session.models.assets.set_items([])
        self._session.models.sequences.set_items([])
        self._session.models.tracks.set_items([])
        self._session.models.clips.set_items([])
        self._session.models.compound_clips.set_items([])
        self._session.models.transitions.set_items([])
        self._session.models.markers.set_items([])
        self._session.models.ranges.set_items([])
        self._session.models.tasks.set_items([])
        self._session.models.documents.set_items([])
        self._session.models.segments.set_items([])
        self._session.models.subtitle_placements.set_items([])
        self._session.models.highlights.set_items([])
        self._session.models.audio_buses.set_items([])
        self._session.models.audio_effects.set_items([])
        self._session.models.audio_effect_parameters.set_items([])
        self._session.updates.commit(audio_metrics=True)
        self._session.updates.commit(workflow=True)
        self._session.updates.commit(preview_graph=True)
        if closing_project:
            self._dispose(
                closing_project,
                close_in_background=close_in_background,
            )

    def cancel_filmstrip(self, project: DesktopProject | None = None) -> None:
        self._session.state.requests.filmstrip_id += 1
        target = project or self._session.state.binding.current
        future = self._session.state.requests.filmstrip_future
        if future is not None:
            future.cancel()
            self._session.state.requests.filmstrip_future = None
        if target is not None:
            self._session._api.cancel_timeline_filmstrip_requests(
                target.project_dir,
                request_owner=target.actor_id,
                request_generation=self._session.state.requests.filmstrip_id,
            )

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
        if close_in_background and not self._session.state.requests.shutting_down:
            pending_project = self._session.state.requests.closing_project
            if pending_project is not None and pending_project is not project:
                raise RuntimeError("已有项目资源等待释放")
            if self._session.state.requests.project_close_future is not None:
                raise RuntimeError("项目资源仍在释放中")
            self._session.state.requests.project_close_id += 1
            close_id = self._session.state.requests.project_close_id
            project_path = str(project.project_dir)
            self._session.state.requests.closing_project = project
            self._session.state.requests.closing_project_error = ""
            self._session.updates.commit(project=True)
            self._session._set_status("正在关闭项目并释放文件…")
            self._session.state.requests.project_close_future = self._session.background.submit(
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
        project = self._session.state.requests.closing_project
        if project is None:
            return
        if self._session.state.requests.project_close_future is not None:
            raise RuntimeError("项目资源仍在释放中")
        self._dispose(project, close_in_background=True)
