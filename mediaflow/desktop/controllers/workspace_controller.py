from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from mediaflow.desktop.presentation_workspace import workspace_mode_catalog

from .controller_facet import ControllerFacet
from .controller_scopes import WorkspaceViewScope
from .workspace_capabilities import workspace_action_capabilities


class WorkspaceViewController(ControllerFacet[WorkspaceViewScope]):
    projectStateChanged = Signal()
    historyChanged = Signal()
    statusChanged = Signal()
    previewGraphChanged = Signal()
    profileConfirmationChanged = Signal()
    settingsChanged = Signal()
    relinkConfirmationChanged = Signal()
    workflowChanged = Signal()
    previewRangeRequested = Signal(int, int)
    errorOccurred = Signal(str)
    errorReferenceChanged = Signal()
    errorHistoryChanged = Signal()
    collaborationConflictChanged = Signal()

    @Property(list, constant=True)
    def workspaceModes(self) -> list[dict[str, object]]:
        return workspace_mode_catalog()

    @Property(QObject, constant=True)
    def sequencesModel(self) -> QObject:
        return self._session.models.sequences

    @Property(QObject, constant=True)
    def recentProjectsModel(self) -> QObject:
        return self._session.models.recent_projects

    @Property(dict, notify=projectStateChanged)
    def homeSummary(self) -> dict:
        return self._session.state.presentation.home_summary

    @Property(bool, notify=projectStateChanged)
    def hasProject(self) -> bool:
        return self._session.state.binding.current is not None

    @Property(str, notify=projectStateChanged)
    def projectName(self) -> str:
        return (
            self._session.state.binding.require_current().get_project().name
            if self._session.state.binding.current
            else ""
        )

    @Property(str, notify=projectStateChanged)
    def projectPath(self) -> str:
        return (
            str(self._session.state.binding.require_current().project_dir)
            if self._session.state.binding.current
            else ""
        )

    @Property(list, notify=projectStateChanged)
    def projectVersions(self) -> list[dict]:
        if not self._session.state.binding.current:
            return []
        return [
            {
                "versionId": item.id,
                "name": item.name,
                "snapshotPath": item.snapshot_path,
                "contentRevision": item.content_revision,
                "createdAt": item.created_at,
            }
            for item in self._session.state.binding.require_current().list_versions()
        ]

    @Property(QUrl, notify=settingsChanged)
    def defaultProjectDirectoryUrl(self) -> QUrl:
        directory = self._session.state.service_settings.default_project_directory
        return QUrl.fromLocalFile(directory)

    @Property(str, notify=projectStateChanged)
    def activeSequenceId(self) -> str:
        return self._session.state.binding.active_sequence_id

    @Property(bool, notify=projectStateChanged)
    def canArchiveActiveSequence(self) -> bool:
        if not self._session.state.binding.current or not self._session.state.binding.active_sequence_id:
            return False
        project = self._session.state.binding.require_current().get_project()
        return (
            not self._session.state.binding.require_current().read_only
            and self._session.state.binding.active_sequence_id != project.main_sequence_id
        )

    @Property(str, notify=projectStateChanged)
    def profileLabel(self) -> str:
        if not self._session.state.binding.current or not self._session.state.binding.active_sequence_id:
            return ""
        sequence = self._session.state.binding.require_current().get_sequence(
            self._session.state.binding.active_sequence_id
        )
        if not sequence.profile_confirmed:
            return "等待首个视频"
        profile = sequence.profile
        fps = profile.fps_numerator / profile.fps_denominator
        return f"{profile.width}×{profile.height}  {fps:.3f} fps".replace(".000", "")

    @Property(bool, notify=projectStateChanged)
    def profileConfirmed(self) -> bool:
        if not self._session.state.binding.current or not self._session.state.binding.active_sequence_id:
            return False
        return (
            self._session.state.binding.require_current()
            .get_sequence(self._session.state.binding.active_sequence_id)
            .profile_confirmed
        )

    @Property(str, notify=projectStateChanged)
    def colorMode(self) -> str:
        if not self._session.state.binding.current or not self._session.state.binding.active_sequence_id:
            return ""
        return (
            self._session.state.binding.require_current()
            .get_sequence(self._session.state.binding.active_sequence_id)
            .profile.color_mode.value
        )

    @Property(int, notify=projectStateChanged)
    def profileWidth(self) -> int:
        if not self._session.state.binding.current or not self._session.state.binding.active_sequence_id:
            return 0
        return (
            self._session.state.binding.require_current()
            .get_sequence(self._session.state.binding.active_sequence_id)
            .profile.width
        )

    @Property(int, notify=projectStateChanged)
    def profileHeight(self) -> int:
        if not self._session.state.binding.current or not self._session.state.binding.active_sequence_id:
            return 0
        return (
            self._session.state.binding.require_current()
            .get_sequence(self._session.state.binding.active_sequence_id)
            .profile.height
        )

    @Property(int, notify=projectStateChanged)
    def profileFpsNumerator(self) -> int:
        if not self._session.state.binding.current or not self._session.state.binding.active_sequence_id:
            return 0
        return (
            self._session.state.binding.require_current()
            .get_sequence(self._session.state.binding.active_sequence_id)
            .profile.fps_numerator
        )

    @Property(int, notify=projectStateChanged)
    def profileFpsDenominator(self) -> int:
        if not self._session.state.binding.current or not self._session.state.binding.active_sequence_id:
            return 1
        return (
            self._session.state.binding.require_current()
            .get_sequence(self._session.state.binding.active_sequence_id)
            .profile.fps_denominator
        )

    @Property(int, notify=projectStateChanged)
    def profileAudioChannels(self) -> int:
        if not self._session.state.binding.current or not self._session.state.binding.active_sequence_id:
            return 2
        return (
            self._session.state.binding.require_current()
            .get_sequence(self._session.state.binding.active_sequence_id)
            .profile.audio_channels
        )

    @Property(int, notify=historyChanged)
    def timelineDurationFrames(self) -> int:
        if not self._session.state.binding.timeline:
            return 0
        return self._session.state.binding.require_timeline().state.duration_frames

    @Property(bool, notify=historyChanged)
    def hasSequenceInOut(self) -> bool:
        return bool(
            self._session.state.binding.timeline
            and self._session.state.binding.require_timeline().state.sequence.in_out
        )

    @Property(int, notify=historyChanged)
    def sequenceInFrame(self) -> int:
        if (
            not self._session.state.binding.timeline
            or self._session.state.binding.require_timeline().state.sequence.in_out is None
        ):
            return 0
        return self._session.state.binding.require_timeline().state.sequence.in_out.in_frame

    @Property(int, notify=historyChanged)
    def sequenceOutFrame(self) -> int:
        if (
            not self._session.state.binding.timeline
            or self._session.state.binding.require_timeline().state.sequence.in_out is None
        ):
            return self._session.state.binding.require_timeline().state.duration_frames
        return self._session.state.binding.require_timeline().state.sequence.in_out.out_frame

    @Property(bool, notify=projectStateChanged)
    def readOnly(self) -> bool:
        return bool(
            self._session.state.binding.current and self._session.state.binding.require_current().read_only
        )

    @Property(bool, notify=projectStateChanged)
    def projectClosing(self) -> bool:
        return self._session.state.requests.project_close_future is not None

    @Property(bool, notify=projectStateChanged)
    def projectReleasePending(self) -> bool:
        return self._session.state.requests.closing_project is not None

    @Property(bool, notify=projectStateChanged)
    def projectCloseFailed(self) -> bool:
        return bool(
            self._session.state.requests.closing_project is not None
            and self._session.state.requests.project_close_future is None
            and self._session.state.requests.closing_project_error
        )

    @Property(str, notify=projectStateChanged)
    def closingProjectPath(self) -> str:
        project = self._session.state.requests.closing_project
        return "" if project is None else str(project.project_dir)

    @Property(str, notify=projectStateChanged)
    def projectCloseError(self) -> str:
        return self._session.state.requests.closing_project_error

    @Property(dict, notify=projectStateChanged)
    def actionCapabilities(self) -> dict:
        return workspace_action_capabilities(self._session.state)

    @Property(int, notify=projectStateChanged)
    def offlineAssetCount(self) -> int:
        if not self._session.state.binding.current:
            return 0
        return sum(
            asset.status.value == "offline"
            for asset in self._session.state.binding.require_current().list_assets()
        )

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
        return self._session.state.presentation.status_message

    @Property(str, notify=errorReferenceChanged)
    def lastErrorId(self) -> str:
        return self._session.state.presentation.last_error_id

    @Property(list, notify=errorHistoryChanged)
    def recentErrors(self) -> list[dict]:
        return list(self._session.state.presentation.recent_errors)

    @Slot()
    def clearErrorHistory(self) -> None:
        self._session.state.presentation.recent_errors.clear()
        self._session.updates.commit(error_history=True)

    @Property(dict, notify=collaborationConflictChanged)
    def collaborationConflict(self) -> dict:
        return self._session.state.presentation.collaboration_conflict

    @Property(bool, notify=collaborationConflictChanged)
    def collaborationConflictPending(self) -> bool:
        return bool(self._session.state.presentation.collaboration_conflict)

    @Property(str, notify=previewGraphChanged)
    def previewGraphPath(self) -> str:
        return self._session.state.presentation.preview_graph_path

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
        return bool(self._session.state.assets.pending_profile_asset_id)

    @Property(str, notify=profileConfirmationChanged)
    def pendingProfileLabel(self) -> str:
        return self._session.state.assets.pending_profile_label

    @Property(QUrl, notify=settingsChanged)
    def defaultImportDirectoryUrl(self) -> QUrl:
        path = self._session.state.desktop_settings.ui.default_import_directory
        return QUrl.fromLocalFile(path) if path and Path(path).is_dir() else QUrl()

    @Property(bool, notify=relinkConfirmationChanged)
    def relinkConfirmationPending(self) -> bool:
        return bool(self._session.state.assets.pending_relink_asset_id)

    @Property(str, notify=relinkConfirmationChanged)
    def pendingRelinkPath(self) -> str:
        return self._session.state.assets.pending_relink_path
