from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from mediaflow.domain.enums import (
    ColorMode,
)
from mediaflow.domain.project import ProjectProfile

from .controller_facet import ControllerFacet
from .project_controller import _TimelinePlacement


class WorkspaceController(ControllerFacet):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    statusChanged = Signal()
    tasksChanged = Signal()
    previewGraphChanged = Signal()
    profileConfirmationChanged = Signal()
    settingsChanged = Signal()
    relinkConfirmationChanged = Signal()
    audioMetricsChanged = Signal()
    workflowChanged = Signal()
    downloadPlanChanged = Signal()
    runtimeToolsChanged = Signal()
    waveformDataChanged = Signal(str)
    previewRangeRequested = Signal(int, int)
    errorOccurred = Signal(str)
    errorReferenceChanged = Signal()

    @Property(QObject, constant=True)
    def sequencesModel(self) -> QObject:
        return self._sequence_model

    @Property(QObject, constant=True)
    def recentProjectsModel(self) -> QObject:
        return self._recent_project_model

    @Property("QVariantMap", notify=projectStateChanged)
    def homeSummary(self) -> dict:
        return self._home_summary

    @Property(bool, notify=projectStateChanged)
    def hasProject(self) -> bool:
        return self._documents is not None

    @Property(str, notify=projectStateChanged)
    def projectName(self) -> str:
        return self._documents.get_project().name if self._documents else ""

    @Property(str, notify=projectStateChanged)
    def projectPath(self) -> str:
        return str(self._documents.project_dir) if self._documents else ""

    @Property("QVariantList", notify=projectStateChanged)
    def projectVersions(self) -> list[dict]:
        if not self._project:
            return []
        return [
            {
                "versionId": item.id,
                "name": item.name,
                "snapshotPath": item.snapshot_path,
                "contentRevision": item.content_revision,
                "createdAt": item.created_at,
            }
            for item in self._project.list_versions()
        ]

    @Property(QUrl, notify=settingsChanged)
    def defaultProjectDirectoryUrl(self) -> QUrl:
        directory = self.settings.ui.default_project_directory
        return QUrl.fromLocalFile(directory)

    @Property(str, notify=projectStateChanged)
    def activeSequenceId(self) -> str:
        return self._active_sequence_id

    @Property(bool, notify=projectStateChanged)
    def canArchiveActiveSequence(self) -> bool:
        if not self._documents or not self._active_sequence_id:
            return False
        project = self._documents.get_project()
        return not self._documents.read_only and self._active_sequence_id != project.main_sequence_id

    @Property(str, notify=projectStateChanged)
    def profileLabel(self) -> str:
        if not self._documents or not self._active_sequence_id:
            return ""
        sequence = self._documents.get_sequence(self._active_sequence_id)
        if not sequence.profile_confirmed:
            return "等待首个视频"
        profile = sequence.profile
        fps = profile.fps_numerator / profile.fps_denominator
        return f"{profile.width}×{profile.height}  {fps:.3f} fps".replace(".000", "")

    @Property(bool, notify=projectStateChanged)
    def profileConfirmed(self) -> bool:
        if not self._documents or not self._active_sequence_id:
            return False
        return self._documents.get_sequence(self._active_sequence_id).profile_confirmed

    @Property(str, notify=projectStateChanged)
    def colorMode(self) -> str:
        if not self._documents or not self._active_sequence_id:
            return ""
        return self._documents.get_sequence(self._active_sequence_id).profile.color_mode.value

    @Property(int, notify=projectStateChanged)
    def profileWidth(self) -> int:
        if not self._documents or not self._active_sequence_id:
            return 0
        return self._documents.get_sequence(self._active_sequence_id).profile.width

    @Property(int, notify=projectStateChanged)
    def profileHeight(self) -> int:
        if not self._documents or not self._active_sequence_id:
            return 0
        return self._documents.get_sequence(self._active_sequence_id).profile.height

    @Property(int, notify=projectStateChanged)
    def profileFpsNumerator(self) -> int:
        if not self._documents or not self._active_sequence_id:
            return 0
        return self._documents.get_sequence(self._active_sequence_id).profile.fps_numerator

    @Property(int, notify=projectStateChanged)
    def profileFpsDenominator(self) -> int:
        if not self._documents or not self._active_sequence_id:
            return 1
        return self._documents.get_sequence(self._active_sequence_id).profile.fps_denominator

    @Property(int, notify=projectStateChanged)
    def profileAudioChannels(self) -> int:
        if not self._documents or not self._active_sequence_id:
            return 2
        return self._documents.get_sequence(self._active_sequence_id).profile.audio_channels

    @Property(int, notify=historyChanged)
    def timelineDurationFrames(self) -> int:
        if not self._editor:
            return 0
        return self._editor.state.duration_frames

    @Property(bool, notify=historyChanged)
    def hasSequenceInOut(self) -> bool:
        return bool(self._editor and self._editor.state.sequence.in_out)

    @Property(int, notify=historyChanged)
    def sequenceInFrame(self) -> int:
        if not self._editor or self._editor.state.sequence.in_out is None:
            return 0
        return self._editor.state.sequence.in_out.in_frame

    @Property(int, notify=historyChanged)
    def sequenceOutFrame(self) -> int:
        if not self._editor or self._editor.state.sequence.in_out is None:
            return self.timelineDurationFrames
        return self._editor.state.sequence.in_out.out_frame

    @Property(bool, notify=projectStateChanged)
    def readOnly(self) -> bool:
        return bool(self._documents and self._documents.read_only)

    @Property(int, notify=projectStateChanged)
    def offlineAssetCount(self) -> int:
        if not self._documents:
            return 0
        return sum(asset.status.value == "offline" for asset in self._documents.list_assets())

    @Property(str, notify=workflowChanged)
    def projectWorkflowMode(self) -> str:
        if not self._documents:
            return "inherit"
        value = self._documents.get_project().workflow_auto_continue
        return "inherit" if value is None else "auto" if value else "confirm"

    @Property(bool, notify=workflowChanged)
    def workflowPending(self) -> bool:
        return self._active_workflow_run() is not None

    @Property(str, notify=workflowChanged)
    def workflowRunId(self) -> str:
        run = self._active_workflow_run()
        return run.id if run else ""

    @Property(str, notify=workflowChanged)
    def workflowStage(self) -> str:
        run = self._active_workflow_run()
        return run.stage.value if run else ""

    @Property(str, notify=workflowChanged)
    def workflowStatus(self) -> str:
        run = self._active_workflow_run()
        return run.status.value if run else ""

    @Property(str, notify=workflowChanged)
    def workflowMessageCode(self) -> str:
        run = self._active_workflow_run()
        return run.message_code if run else ""

    @Property(str, notify=statusChanged)
    def statusMessage(self) -> str:
        return self._status_message

    @Property(str, notify=errorReferenceChanged)
    def lastErrorId(self) -> str:
        return self._last_error_id

    @Property(str, notify=previewGraphChanged)
    def previewGraphPath(self) -> str:
        return self._preview_graph_path

    @Property(str, constant=True)
    def mltRuntimeRoot(self) -> str:
        return self._api.mlt_runtime_root

    @Property(bool, notify=profileConfirmationChanged)
    def profileConfirmationPending(self) -> bool:
        return bool(self._pending_profile_asset_id)

    @Property(str, notify=profileConfirmationChanged)
    def pendingProfileLabel(self) -> str:
        return self._pending_profile_label

    @Property(QUrl, notify=settingsChanged)
    def defaultImportDirectoryUrl(self) -> QUrl:
        path = self.settings.ui.default_import_directory
        return QUrl.fromLocalFile(path) if path and Path(path).is_dir() else QUrl()

    @Property(bool, notify=relinkConfirmationChanged)
    def relinkConfirmationPending(self) -> bool:
        return bool(self._pending_relink_asset_id)

    @Property(str, notify=relinkConfirmationChanged)
    def pendingRelinkPath(self) -> str:
        return self._pending_relink_path

    @Slot(str)
    def setProjectWorkflowMode(self, mode: str) -> None:
        try:
            self._require_writable()
            values = {"inherit": None, "confirm": False, "auto": True}
            if mode not in values:
                raise ValueError("未知的项目工作流模式")
            self._workflows.set_project_mode(values[mode])
            self.workflowChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str)
    def continueWorkflow(self, run_id: str, target_language: str = "") -> None:
        try:
            self._require_writable()
            self._apply_workflow_update(
                self._workflows.continue_run(
                    run_id,
                    target_language=target_language,
                )
            )
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def cancelWorkflow(self, run_id: str) -> None:
        try:
            self._require_writable()
            self._apply_workflow_update(self._workflows.cancel(run_id))
            self.workflowChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str)
    def createProject(self, parent_url: str, name: str) -> None:
        try:
            parent = self._local_path(parent_url)
            self._create_and_open_project(parent, name)
            self._remember_default_project_directory(parent)
            self._set_status("项目已创建")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def createProjectInDefaultDirectory(self, name: str) -> None:
        try:
            self._create_and_open_project(
                Path(self.settings.ui.default_project_directory),
                name,
                ensure_unique=True,
            )
            self._set_status("项目已创建")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def openProject(self, path_url: str) -> None:
        try:
            path = self._local_path(path_url)
            root = path.parent if path.name == "project.mfp" else path
            if self._project and root.resolve() == self._project.project_dir:
                self._set_status("项目已打开")
                return
            candidate = self._api.open_project(root, writable=True)
            self._replace_project(candidate)
            self._set_status("项目已打开" if not self.readOnly else "项目正被其他窗口使用，已只读打开")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def removeRecentProject(self, path_value: str) -> None:
        try:
            if self._forget_recent_project(self._local_path(path_value)):
                self._set_status("已从最近项目中移除")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def closeProject(self) -> None:
        self._close_current()
        self._projector.refresh_recent_projects()
        self.projectStateChanged.emit()

    @Slot(str)
    def createNamedVersion(self, name: str) -> None:
        try:
            self._require_writable()
            if any(not task.status.is_terminal for task in self._task_view.values()):
                raise RuntimeError("请等待当前任务完成后再创建命名版本")
            record = self._project.create_version(name)
            self.projectStateChanged.emit()
            self._set_status(f"已创建命名版本“{record.name}”")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def restoreNamedVersion(self, version_id: str) -> None:
        try:
            self._require_writable()
            if any(not task.status.is_terminal for task in self._task_view.values()):
                raise RuntimeError("请等待当前任务完成后再恢复命名版本")
            record = self._project.restore_version(version_id)
            sequence_ids = {
                sequence.id for sequence in self._documents.list_sequences()
            }
            if self._active_sequence_id not in sequence_ids:
                self._active_sequence_id = self._documents.get_project().main_sequence_id
            self._editor = self._project.timeline(self._active_sequence_id)
            self._reset_project_selection()
            self._projector.refresh_all()
            self._projector.schedule_preview_graph()
            self.projectStateChanged.emit()
            self.selectionChanged.emit()
            self.historyChanged.emit()
            self._set_status(f"已恢复命名版本“{record.name}”")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def selectSequence(self, sequence_id: str) -> None:
        if not self._documents:
            return
        try:
            self._documents.get_sequence(sequence_id)
            self._active_sequence_id = sequence_id
            self._editor = self._project.timeline(sequence_id)
            self._selected_clip_ids = []
            self._selected_compound_id = ""
            self._projector.refresh_timeline()
            self._projector.refresh_audio_metrics()
            self._projector.refresh_preview_subtitles()
            self.projectStateChanged.emit()
            self.historyChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def createShortSequence(self, name: str) -> None:
        try:
            self._require_writable()
            selected_name = name.strip()
            if not selected_name:
                existing_names = {
                    item.name for item in self._documents.list_sequences(include_archived=True)
                }
                sequence_number = 1
                while f"短视频 {sequence_number}" in existing_names:
                    sequence_number += 1
                selected_name = f"短视频 {sequence_number}"
            sequence = self._documents.create_short_sequence(selected_name)
            self._active_sequence_id = sequence.id
            self._editor = self._project.timeline(sequence.id)
            self._projector.refresh_all()
            self._set_status("短视频序列已创建")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def archiveActiveSequence(self) -> None:
        try:
            self._require_writable()
            project = self._documents.get_project()
            sequence_id = self._active_sequence_id
            if sequence_id == project.main_sequence_id:
                raise ValueError("主序列不能删除")
            self._project.archive_short_sequence(sequence_id)
            self._active_sequence_id = project.main_sequence_id
            self._editor = self._project.timeline(project.main_sequence_id)
            self._selected_clip_ids = []
            self._selected_compound_id = ""
            self._projector.refresh_all()
            self._set_status("短视频序列已移除；可使用撤销恢复")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(bool)
    def resolveProfileAdoption(self, adopt: bool) -> None:
        asset_id = self._pending_profile_asset_id
        placement = self._pending_profile_placement
        self._pending_profile_asset_id = ""
        self._pending_profile_label = ""
        self._pending_profile_placement = _TimelinePlacement()
        self.profileConfirmationChanged.emit()
        if not asset_id:
            return
        try:
            self._require_writable()
            if adopt:
                self._assets.adopt_main_profile_from_video(asset_id)
                self._editor.reload()
                self._projector.refresh_all()
            placed = self._place_asset_on_timeline(
                self._documents.get_asset(asset_id),
                placement,
            )
            if self._pending_asset_batch_placement.start_frame is not None:
                self._pending_asset_batch_placement = replace(
                    self._pending_asset_batch_placement,
                    track_id=placed.track_id,
                    start_frame=placed.end_frame,
                    force_new_track=False,
                )
            self._continue_asset_batch()
        except Exception as error:
            self._pending_asset_batch_ids = []
            self.errorOccurred.emit(str(error))

    @Slot(int)
    def reportPreviewDroppedFrames(self, dropped_frames: int) -> None:
        if (
            dropped_frames < self.settings.preview.dropped_frame_proxy_threshold
            or not self._documents
            or not self._editor
        ):
            return
        asset_ids = {clip.asset_id for clip in self._editor.state.clips}
        for asset_id in asset_ids:
            asset = self._documents.get_asset(asset_id)
            if not asset.proxy_path:
                self._schedule_asset_background(asset, dropped_frames=dropped_frames)

    @Slot(bool)
    def reportHdrPreviewActive(self, active: bool) -> None:
        if self._hdr_preview_active == active:
            return
        self._hdr_preview_active = active
        self._projector.schedule_preview_graph()

    @Slot(int, int, int, int, str, int)
    def updateSequenceProfile(
        self,
        width: int,
        height: int,
        fps_numerator: int,
        fps_denominator: int,
        color_mode: str,
        audio_channels: int,
    ) -> None:
        try:
            self._require_writable()
            mode = ColorMode(color_mode)
            self._editor.set_sequence_profile(
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
            self._projector.refresh_assets()
            self._projector.refresh_sequences()
            self._projector.refresh_timeline()
            self._projector.refresh_documents()
            self._projector.refresh_preview_subtitles()
            self._projector.schedule_preview_graph()
            self.projectStateChanged.emit()
            self.historyChanged.emit()
            self._set_status("序列配置已更新")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def saveProject(self) -> None:
        if not self._documents:
            return
        self._set_status("项目已保存")

    @Slot()
    def shutdown(self) -> None:
        self._shutting_down = True
        self._runtime_tool_cancel.set()
        if self._runtime_tool_thread and self._runtime_tool_thread.is_alive():
            self._runtime_tool_thread.join()
        self._close_current(close_in_background=False)
        self._preview_executor.shutdown(wait=True, cancel_futures=True)
        self._background_executor.shutdown(wait=True, cancel_futures=True)
