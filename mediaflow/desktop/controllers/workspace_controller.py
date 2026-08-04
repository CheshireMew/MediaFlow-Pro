from __future__ import annotations

import logging
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from mediaflow.desktop.presentation_catalogs import workspace_mode_catalog
from mediaflow.desktop.session_state import TimelinePlacement
from mediaflow.domain.enums import (
    ColorMode,
)
from mediaflow.domain.project import ProjectProfile

from .controller_facet import ControllerFacet, report_ui_errors

logger = logging.getLogger(__name__)


class WorkspaceController(ControllerFacet):
    sampleTourRequested = Signal()
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    statusChanged = Signal()
    previewGraphChanged = Signal()
    profileConfirmationChanged = Signal()
    settingsChanged = Signal()
    relinkConfirmationChanged = Signal()
    workflowChanged = Signal()
    downloadPlanChanged = Signal()
    previewRangeRequested = Signal(int, int)
    errorOccurred = Signal(str)
    errorReferenceChanged = Signal()
    collaborationConflictChanged = Signal()
    remoteSeekRequested = Signal(int)
    remotePlayRequested = Signal(int)
    remotePauseRequested = Signal()
    remoteStopRequested = Signal()

    def __init__(self, session):
        super().__init__(session)
        session.events.workspaceCommandReceived.connect(self._apply_workspace_command)

    @Slot(object)
    def _apply_workspace_command(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        command = str(event.get("command") or "")
        arguments = event.get("arguments")
        values = arguments if isinstance(arguments, dict) else {}
        if command == "playhead.seek":
            self.remoteSeekRequested.emit(int(values["frame"]))
        elif command == "playback.play":
            self.remotePlayRequested.emit(int(values["frame"]))
        elif command == "playback.pause":
            self.remotePauseRequested.emit()
        elif command == "playback.stop":
            self.remoteStopRequested.emit()

    @Property("QVariantList", constant=True)
    def workspaceModes(self) -> list[dict[str, str]]:
        return workspace_mode_catalog()

    @Property(QObject, constant=True)
    def sequencesModel(self) -> QObject:
        return self._session.models.sequences

    @Property(QObject, constant=True)
    def recentProjectsModel(self) -> QObject:
        return self._session.models.recent_projects

    @Property("QVariantMap", notify=projectStateChanged)
    def homeSummary(self) -> dict:
        return self._session.presentation.home_summary

    @Property(bool, notify=projectStateChanged)
    def hasProject(self) -> bool:
        return self._session.binding.current is not None

    @Property(str, notify=projectStateChanged)
    def projectName(self) -> str:
        return self._session.binding.current.get_project().name if self._session.binding.current else ""

    @Property(str, notify=projectStateChanged)
    def projectPath(self) -> str:
        return str(self._session.binding.current.project_dir) if self._session.binding.current else ""

    @Property("QVariantList", notify=projectStateChanged)
    def projectVersions(self) -> list[dict]:
        if not self._session.binding.current:
            return []
        return [
            {
                "versionId": item.id,
                "name": item.name,
                "snapshotPath": item.snapshot_path,
                "contentRevision": item.content_revision,
                "createdAt": item.created_at,
            }
            for item in self._session.binding.current.list_versions()
        ]

    @Property(QUrl, notify=settingsChanged)
    def defaultProjectDirectoryUrl(self) -> QUrl:
        directory = self._session.service_settings.default_project_directory
        return QUrl.fromLocalFile(directory)

    @Property(str, notify=projectStateChanged)
    def activeSequenceId(self) -> str:
        return self._session.binding.active_sequence_id

    @Property(bool, notify=projectStateChanged)
    def canArchiveActiveSequence(self) -> bool:
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            return False
        project = self._session.binding.current.get_project()
        return (
            not self._session.binding.current.read_only
            and self._session.binding.active_sequence_id != project.main_sequence_id
        )

    @Property(str, notify=projectStateChanged)
    def profileLabel(self) -> str:
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            return ""
        sequence = self._session.binding.current.get_sequence(self._session.binding.active_sequence_id)
        if not sequence.profile_confirmed:
            return "等待首个视频"
        profile = sequence.profile
        fps = profile.fps_numerator / profile.fps_denominator
        return f"{profile.width}×{profile.height}  {fps:.3f} fps".replace(".000", "")

    @Property(bool, notify=projectStateChanged)
    def profileConfirmed(self) -> bool:
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            return False
        return self._session.binding.current.get_sequence(
            self._session.binding.active_sequence_id
        ).profile_confirmed

    @Property(str, notify=projectStateChanged)
    def colorMode(self) -> str:
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            return ""
        return self._session.binding.current.get_sequence(
            self._session.binding.active_sequence_id
        ).profile.color_mode.value

    @Property(int, notify=projectStateChanged)
    def profileWidth(self) -> int:
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            return 0
        return self._session.binding.current.get_sequence(
            self._session.binding.active_sequence_id
        ).profile.width

    @Property(int, notify=projectStateChanged)
    def profileHeight(self) -> int:
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            return 0
        return self._session.binding.current.get_sequence(
            self._session.binding.active_sequence_id
        ).profile.height

    @Property(int, notify=projectStateChanged)
    def profileFpsNumerator(self) -> int:
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            return 0
        return self._session.binding.current.get_sequence(
            self._session.binding.active_sequence_id
        ).profile.fps_numerator

    @Property(int, notify=projectStateChanged)
    def profileFpsDenominator(self) -> int:
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            return 1
        return self._session.binding.current.get_sequence(
            self._session.binding.active_sequence_id
        ).profile.fps_denominator

    @Property(int, notify=projectStateChanged)
    def profileAudioChannels(self) -> int:
        if not self._session.binding.current or not self._session.binding.active_sequence_id:
            return 2
        return self._session.binding.current.get_sequence(
            self._session.binding.active_sequence_id
        ).profile.audio_channels

    @Property(int, notify=historyChanged)
    def timelineDurationFrames(self) -> int:
        if not self._session.binding.timeline:
            return 0
        return self._session.binding.timeline.state.duration_frames

    @Property(bool, notify=historyChanged)
    def hasSequenceInOut(self) -> bool:
        return bool(self._session.binding.timeline and self._session.binding.timeline.state.sequence.in_out)

    @Property(int, notify=historyChanged)
    def sequenceInFrame(self) -> int:
        if not self._session.binding.timeline or self._session.binding.timeline.state.sequence.in_out is None:
            return 0
        return self._session.binding.timeline.state.sequence.in_out.in_frame

    @Property(int, notify=historyChanged)
    def sequenceOutFrame(self) -> int:
        if not self._session.binding.timeline or self._session.binding.timeline.state.sequence.in_out is None:
            return self.timelineDurationFrames
        return self._session.binding.timeline.state.sequence.in_out.out_frame

    @Property(bool, notify=projectStateChanged)
    def readOnly(self) -> bool:
        return bool(self._session.binding.current and self._session.binding.current.read_only)

    @Property(bool, notify=projectStateChanged)
    def projectClosing(self) -> bool:
        return self._session.requests.project_close_future is not None

    @Property(bool, notify=projectStateChanged)
    def projectReleasePending(self) -> bool:
        return self._session.requests.closing_project is not None

    @Property(bool, notify=projectStateChanged)
    def projectCloseFailed(self) -> bool:
        return bool(
            self._session.requests.closing_project is not None
            and self._session.requests.project_close_future is None
            and self._session.requests.closing_project_error
        )

    @Property(str, notify=projectStateChanged)
    def closingProjectPath(self) -> str:
        project = self._session.requests.closing_project
        return "" if project is None else str(project.project_dir)

    @Property(str, notify=projectStateChanged)
    def projectCloseError(self) -> str:
        return self._session.requests.closing_project_error

    @Property("QVariantMap", notify=projectStateChanged)
    def actionCapabilities(self) -> dict:
        project = self._session.binding.current
        release_pending = self._session.requests.closing_project is not None
        closing = self._session.requests.project_close_future is not None
        close_failed = bool(
            release_pending
            and not closing
            and self._session.requests.closing_project_error
        )
        writable = bool(project and not project.read_only)
        return {
            "canEdit": writable,
            "canImport": writable,
            "canStartTasks": writable,
            "canManageTasks": writable,
            "canManageWorkflow": writable,
            "canOpenProject": not release_pending,
            "canCreateProject": not release_pending,
            "canCloseProject": bool(project) and not release_pending,
            "canRetryProjectClose": close_failed,
            "projectClosing": closing,
            "projectReleasePending": release_pending,
        }

    @Property(int, notify=projectStateChanged)
    def offlineAssetCount(self) -> int:
        if not self._session.binding.current:
            return 0
        return sum(asset.status.value == "offline" for asset in self._session.binding.current.list_assets())

    @Property(bool, notify=workflowChanged)
    def workflowPending(self) -> bool:
        return self._session.tasks.active_workflow() is not None

    @Property(str, notify=workflowChanged)
    def workflowRunId(self) -> str:
        run = self._session.tasks.active_workflow()
        return run.id if run else ""

    @Property(str, notify=workflowChanged)
    def workflowStage(self) -> str:
        run = self._session.tasks.active_workflow()
        return run.stage.value if run else ""

    @Property(str, notify=workflowChanged)
    def workflowStatus(self) -> str:
        run = self._session.tasks.active_workflow()
        return run.status.value if run else ""

    @Property(str, notify=workflowChanged)
    def workflowMessageCode(self) -> str:
        run = self._session.tasks.active_workflow()
        return run.message_code if run else ""

    @Property(str, notify=statusChanged)
    def statusMessage(self) -> str:
        return self._session.presentation.status_message

    @Property(str, notify=errorReferenceChanged)
    def lastErrorId(self) -> str:
        return self._session.presentation.last_error_id

    @Property("QVariantMap", notify=collaborationConflictChanged)
    def collaborationConflict(self) -> dict:
        return self._session.presentation.collaboration_conflict

    @Property(bool, notify=collaborationConflictChanged)
    def collaborationConflictPending(self) -> bool:
        return bool(self._session.presentation.collaboration_conflict)

    @Slot(str)
    @report_ui_errors
    def resolveCollaborationConflict(self, resolution: str) -> None:
        self._session.lifecycle.resolve_collaboration_conflict(resolution)
        self._session.presentation.collaboration_conflict = {}
        self._session.events.collaborationConflictChanged.emit()

    @Property(str, notify=previewGraphChanged)
    def previewGraphPath(self) -> str:
        return self._session.presentation.preview_graph_path

    @Property(str, constant=True)
    def mltRuntimeRoot(self) -> str:
        return self._session._api.mlt_runtime_root

    @Property(str, constant=True)
    def mltLibraryPath(self) -> str:
        return self._session._api.mlt_library_path

    @Property(str, constant=True)
    def mltRepositoryPath(self) -> str:
        return self._session._api.mlt_preview_repository_path

    @Property(str, constant=True)
    def mltDataPath(self) -> str:
        return self._session._api.mlt_data_path

    @Property(bool, notify=profileConfirmationChanged)
    def profileConfirmationPending(self) -> bool:
        return bool(self._session.asset_state.pending_profile_asset_id)

    @Property(str, notify=profileConfirmationChanged)
    def pendingProfileLabel(self) -> str:
        return self._session.asset_state.pending_profile_label

    @Property(QUrl, notify=settingsChanged)
    def defaultImportDirectoryUrl(self) -> QUrl:
        path = self._session.desktop_settings.ui.default_import_directory
        return QUrl.fromLocalFile(path) if path and Path(path).is_dir() else QUrl()

    @Property(bool, notify=relinkConfirmationChanged)
    def relinkConfirmationPending(self) -> bool:
        return bool(self._session.asset_state.pending_relink_asset_id)

    @Property(str, notify=relinkConfirmationChanged)
    def pendingRelinkPath(self) -> str:
        return self._session.asset_state.pending_relink_path

    @Slot(str, str)
    @report_ui_errors
    def continueWorkflow(self, run_id: str, target_language: str = "") -> None:
        self._session._require_writable()
        self._session._apply_workflow_update(
            self._session.binding.current.continue_workflow(
                run_id,
                target_language=target_language,
            )
        )

    @Slot(str)
    @report_ui_errors
    def skipWorkflow(self, run_id: str) -> None:
        self._session._require_writable()
        self._session._apply_workflow_update(
            self._session.binding.current.skip_workflow(run_id)
        )

    @Slot(str)
    @report_ui_errors
    def cancelWorkflow(self, run_id: str) -> None:
        self._session._require_writable()
        self._session._apply_workflow_update(self._session.binding.current.cancel_workflow(run_id))
        self._session.events.workflowChanged.emit()

    @Slot(str, str)
    @report_ui_errors
    def createProject(self, parent_url: str, name: str) -> None:
        self._require_project_open_available()
        parent = self._session._local_path(parent_url)
        self._session.lifecycle.create_and_open(parent, name)
        self._session.settings_persistence.remember_default_project_directory(parent)
        self._session._set_status("项目已创建")

    @Slot(str)
    @report_ui_errors
    def createProjectInDefaultDirectory(self, name: str) -> None:
        self._require_project_open_available()
        self._session.lifecycle.create_and_open(
            Path(self._session.service_settings.default_project_directory),
            name,
            ensure_unique=True,
        )
        self._session._set_status("项目已创建")

    @Slot()
    @report_ui_errors
    def createSampleProject(self) -> None:
        self._require_project_open_available()
        self._session.lifecycle.create_sample_and_open(
            Path(self._session.service_settings.default_project_directory)
        )
        self._session._set_status("示例项目已创建；跟随引导认识主要区域")
        self.sampleTourRequested.emit()

    @Slot()
    def showWorkspaceTour(self) -> None:
        if self.hasProject:
            self.sampleTourRequested.emit()

    @Slot(str)
    @report_ui_errors
    def openProject(self, path_url: str) -> None:
        self._require_project_open_available()
        path = self._session._local_path(path_url)
        root = path.parent if path.name == "project.mfp" else path
        if self._session.binding.current and root.resolve() == self._session.binding.current.project_dir:
            self._session._set_status("项目已打开")
            return
        candidate = self._session._api.open_project(root, writable=True)
        self._session.lifecycle.replace(candidate)
        self._session._set_status("项目已打开" if not self.readOnly else "项目正被其他窗口使用，已只读打开")

    @Slot(str)
    @report_ui_errors
    def removeRecentProject(self, path_value: str) -> None:
        if self._session.lifecycle.forget_recent(self._session._local_path(path_value)):
            self._session._set_status("已从最近项目中移除")

    @Slot()
    def closeProject(self) -> None:
        if not self.actionCapabilities["canCloseProject"]:
            return
        self._session.lifecycle.close()
        self._session.projectors.workspace.refresh_recent_projects()
        self._session.events.projectStateChanged.emit()

    @Slot()
    @report_ui_errors
    def retryProjectClose(self) -> None:
        if not self.actionCapabilities["canRetryProjectClose"]:
            return
        self._session.lifecycle.retry_close()

    def _require_project_open_available(self) -> None:
        if self._session.requests.closing_project is not None:
            raise RuntimeError("正在释放上一个项目，请稍候再打开或创建项目")

    @Slot(str)
    @report_ui_errors
    def createNamedVersion(self, name: str) -> None:
        self._session._require_writable()
        if any(not task.status.is_terminal for task in self._session.task_state.items.values()):
            raise RuntimeError("请等待当前任务完成后再创建命名版本")
        record = self._session.binding.current.create_version(name)
        self._session.events.projectStateChanged.emit()
        self._session._set_status(f"已创建命名版本“{record.name}”")

    @Slot(str)
    @report_ui_errors
    def restoreNamedVersion(self, version_id: str) -> None:
        self._session._require_writable()
        if any(not task.status.is_terminal for task in self._session.task_state.items.values()):
            raise RuntimeError("请等待当前任务完成后再恢复命名版本")
        record = self._session.binding.current.restore_version(version_id)
        sequence_ids = {sequence.id for sequence in self._session.binding.current.list_sequences()}
        if self._session.binding.active_sequence_id not in sequence_ids:
            self._session.binding.active_sequence_id = (
                self._session.binding.current.get_project().main_sequence_id
            )
        self._session.binding.timeline = self._session.binding.current.timeline(
            self._session.binding.active_sequence_id
        )
        self._session.lifecycle.reset_interaction()
        self._session.projectors.refresh_project()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.events.projectStateChanged.emit()
        self._session.events.selectionChanged.emit()
        self._session.events.historyChanged.emit()
        self._session._set_status(f"已恢复命名版本“{record.name}”")

    @Slot(str)
    @report_ui_errors
    def selectSequence(self, sequence_id: str) -> None:
        if not self._session.binding.current:
            return
        self._session.binding.current.get_sequence(sequence_id)
        self._session.binding.active_sequence_id = sequence_id
        self._session.binding.timeline = self._session.binding.current.timeline(sequence_id)
        self._session.selection.clip_ids = []
        self._session.selection.compound_id = ""
        self._session.projectors.refresh_active_sequence()

    @Slot(str)
    @report_ui_errors
    def createShortSequence(self, name: str) -> None:
        self._session._require_writable()
        selected_name = name.strip()
        if not selected_name:
            existing_names = {
                item.name for item in self._session.binding.current.list_sequences(include_archived=True)
            }
            sequence_number = 1
            while f"短视频 {sequence_number}" in existing_names:
                sequence_number += 1
            selected_name = f"短视频 {sequence_number}"
        sequence = self._session.binding.current.create_short_sequence(selected_name)
        self._session.binding.active_sequence_id = sequence.id
        self._session.binding.timeline = self._session.binding.current.timeline(sequence.id)
        self._session.projectors.refresh_active_sequence(refresh_sequences=True)
        self._session._set_status("短视频序列已创建")

    @Slot()
    @report_ui_errors
    def archiveActiveSequence(self) -> None:
        self._session._require_writable()
        project = self._session.binding.current.get_project()
        sequence_id = self._session.binding.active_sequence_id
        if sequence_id == project.main_sequence_id:
            raise ValueError("主序列不能删除")
        self._session.binding.current.archive_short_sequence(sequence_id)
        self._session.binding.active_sequence_id = project.main_sequence_id
        self._session.binding.timeline = self._session.binding.current.timeline(project.main_sequence_id)
        self._session.selection.clip_ids = []
        self._session.selection.compound_id = ""
        self._session.projectors.refresh_active_sequence(refresh_sequences=True)
        self._session._set_status("短视频序列已移除；可使用撤销恢复")

    @Slot(bool)
    def resolveProfileAdoption(self, adopt: bool) -> None:
        asset_id = self._session.asset_state.pending_profile_asset_id
        placement = self._session.asset_state.pending_profile_placement
        self._session.asset_state.pending_profile_asset_id = ""
        self._session.asset_state.pending_profile_label = ""
        self._session.asset_state.pending_profile_placement = TimelinePlacement()
        self._session.events.profileConfirmationChanged.emit()
        if not asset_id:
            return
        try:
            self._session._require_writable()
            if adopt:
                self._session.binding.current.adopt_main_profile_from_video(asset_id)
                self._session.binding.timeline.reload()
                self._session.projectors.timeline.refresh_sequences()
                self._session.events.projectStateChanged.emit()
            placed = self._session.timeline_assets.place_on_timeline(
                self._session.binding.current.get_asset(asset_id),
                placement,
            )
            if self._session.asset_state.pending_batch_placement.start_frame is not None:
                self._session.asset_state.pending_batch_placement = replace(
                    self._session.asset_state.pending_batch_placement,
                    track_id=placed.track_id,
                    start_frame=placed.end_frame,
                    force_new_track=False,
                )
            self._session.timeline_assets.continue_batch()
        except Exception as error:
            self._session.asset_state.pending_batch_ids = []
            self._session.events.errorOccurred.emit(str(error))

    @Slot(int)
    def reportPreviewDroppedFrames(self, dropped_frames: int) -> None:
        if (
            dropped_frames
            < self._session.service_settings.preview.dropped_frame_proxy_threshold
            or not self._session.binding.current
            or not self._session.binding.timeline
            or self._session.binding.current.read_only
        ):
            return
        asset_ids = {clip.asset_id for clip in self._session.binding.timeline.state.clips}
        for asset_id in asset_ids:
            asset = self._session.binding.current.get_asset(asset_id)
            if not asset.proxy_path:
                self._session.timeline_assets.schedule_background(asset, dropped_frames=dropped_frames)

    @Slot(bool)
    def reportHdrPreviewActive(self, active: bool) -> None:
        if self._session.presentation.hdr_preview_active == active:
            return
        self._session.presentation.hdr_preview_active = active
        self._session.projectors.timeline.schedule_preview_graph()

    @Slot(int, int, int, int, str, int)
    @report_ui_errors
    def updateSequenceProfile(
        self,
        width: int,
        height: int,
        fps_numerator: int,
        fps_denominator: int,
        color_mode: str,
        audio_channels: int,
    ) -> None:
        self._session._require_writable()
        mode = ColorMode(color_mode)
        self._session.binding.timeline.set_sequence_profile(
            ProjectProfile(
                width=width,
                height=height,
                fps_numerator=fps_numerator,
                fps_denominator=fps_denominator,
                color_mode=mode,
                bit_depth=10 if mode == ColorMode.HDR10_BT2020_PQ else 8,
                audio_channels=audio_channels,
            )
        )
        self._session.projectors.assets.refresh_assets()
        self._session.projectors.timeline.refresh_sequences()
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.subtitles.refresh_documents()
        self._session.projectors.timeline.refresh_preview_subtitles()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.events.projectStateChanged.emit()
        self._session.events.historyChanged.emit()
        self._session._set_status("序列配置已更新")

    @Slot()
    def saveProject(self) -> None:
        if not self._session.binding.current:
            return
        self._session._set_status("项目已保存")

    @Slot()
    def shutdown(self) -> None:
        self._session.requests.shutting_down = True
        self._session.runtime_state.cancel.set()
        if self._session.runtime_state.thread and self._session.runtime_state.thread.is_alive():
            self._session.runtime_state.thread.join(timeout=5)
        # Project-scoped readers can still be compiling previews, thumbnails,
        # waveforms, or loudness identities here. Drain them before releasing
        # the desktop project session; otherwise the service may clean a web
        # render cache while a reader is fingerprinting that same file.
        self._session.background.shutdown_project_requests()
        self._session.lifecycle.shutdown()
        close_future = self._session.requests.project_close_future
        if close_future is not None:
            try:
                close_future.result(timeout=15)
            except FutureTimeoutError:
                logger.error(
                    "Timed out while waiting for the closing project to release resources"
                )
            except Exception as error:
                logger.exception("Failed while closing the previous project")
                self._session.requests.project_close_future = None
                self._session.requests.closing_project_error = str(error)
                pending_project = self._session.requests.closing_project
                if pending_project is not None:
                    try:
                        pending_project.close(timeout=15)
                    except Exception:
                        logger.exception(
                            "Failed to release the previous project on shutdown retry"
                        )
                    else:
                        self._session.requests.closing_project = None
                        self._session.requests.closing_project_error = ""
                        self._session.requests.project_close_id += 1
            else:
                self._session.requests.project_close_future = None
                self._session.requests.closing_project = None
                self._session.requests.closing_project_error = ""
                self._session.requests.project_close_id += 1
        elif self._session.requests.closing_project is not None:
            try:
                self._session.requests.closing_project.close(timeout=15)
            except Exception:
                logger.exception(
                    "Failed to release the pending project on shutdown"
                )
            else:
                self._session.requests.closing_project = None
                self._session.requests.closing_project_error = ""
                self._session.requests.project_close_id += 1
        self._session._api.close_client_transport()
        self._session.background.shutdown_application_requests()
