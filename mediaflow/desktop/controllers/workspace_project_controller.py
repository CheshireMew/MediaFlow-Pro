from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path

from PySide6.QtCore import QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import WorkspaceProjectScope
from .workspace_capabilities import workspace_action_capabilities

logger = logging.getLogger(__name__)


class WorkspaceProjectController(ControllerFacet[WorkspaceProjectScope]):
    """Project lifecycle, named versions, and application shutdown commands."""

    sampleTourRequested = Signal()

    @Slot(str)
    @report_ui_errors
    def resolveCollaborationConflict(self, resolution: str) -> None:
        self._session.lifecycle.resolve_collaboration_conflict(resolution)
        self._session.state.presentation.collaboration_conflict = {}
        self._session.updates.commit(collaboration_conflict=True)

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
            Path(self._session.state.service_settings.default_project_directory),
            name,
            ensure_unique=True,
        )
        self._session._set_status("项目已创建")

    @Slot()
    @report_ui_errors
    def createSampleProject(self) -> None:
        self._require_project_open_available()
        self._session.lifecycle.create_sample_and_open(
            Path(self._session.state.service_settings.default_project_directory)
        )
        self._session._set_status("示例项目已创建；跟随引导认识主要区域")
        self.sampleTourRequested.emit()

    @Slot()
    def showWorkspaceTour(self) -> None:
        if self._session.state.binding.current is not None:
            self.sampleTourRequested.emit()

    @Slot(str)
    @report_ui_errors
    def openProject(self, path_url: str) -> None:
        self._require_project_open_available()
        path = self._session._local_path(path_url)
        root = path.parent if path.name == "project.mfp" else path
        if (
            self._session.state.binding.current
            and root.resolve() == self._session.state.binding.require_current().project_dir
        ):
            self._session._set_status("项目已打开")
            return
        candidate = self._session._api.open_project(root, writable=True)
        self._session.lifecycle.replace(candidate)
        if self._session.state.binding.require_current().read_only:
            self._session._set_status("项目正被其他窗口使用，已只读打开")
        else:
            self._session._set_status("项目已打开")

    @Slot(str)
    @report_ui_errors
    def removeRecentProject(self, path_value: str) -> None:
        if self._session.lifecycle.forget_recent(self._session._local_path(path_value)):
            self._session._set_status("已从最近项目中移除")

    @Slot()
    def closeProject(self) -> None:
        if not workspace_action_capabilities(self._session.state)["canCloseProject"]:
            return
        self._session.lifecycle.close()
        self._session.projectors.workspace.refresh_recent_projects()
        self._session.updates.commit(project=True)

    @Slot(str)
    @report_ui_errors
    def renameProject(self, name: str) -> None:
        self._session._require_writable()
        project = self._session.state.binding.require_current().rename_project(name)
        self._session.updates.commit(project=True, history=True)
        self._session._set_status("项目已重命名为“%1”", project.name)

    @Slot()
    @report_ui_errors
    def revealProjectFolder(self) -> None:
        if self._session.state.binding.current is None:
            raise RuntimeError("请先打开一个项目")
        project_dir = self._session.state.binding.require_current().project_dir
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(project_dir))):
            raise RuntimeError("无法在文件管理器中打开项目目录")

    @Slot()
    @report_ui_errors
    def copyProjectPath(self) -> None:
        if self._session.state.binding.current is None:
            raise RuntimeError("请先打开一个项目")
        project_dir = self._session.state.binding.require_current().project_dir
        QGuiApplication.clipboard().setText(str(project_dir))
        self._session._set_status("项目路径已复制")

    @Slot()
    @report_ui_errors
    def retryProjectClose(self) -> None:
        if not workspace_action_capabilities(self._session.state)["canRetryProjectClose"]:
            return
        self._session.lifecycle.retry_close()

    def _require_project_open_available(self) -> None:
        if self._session.state.requests.closing_project is not None:
            raise RuntimeError("正在释放上一个项目，请稍候再打开或创建项目")

    @Slot(str)
    @report_ui_errors
    def createNamedVersion(self, name: str) -> None:
        self._session._require_writable()
        if any(not task.status.is_terminal for task in self._session.state.tasks.items.values()):
            raise RuntimeError("请等待当前任务完成后再创建命名版本")
        record = self._session.state.binding.require_current().create_version(name)
        self._session.updates.commit(project=True)
        self._session._set_status("已创建命名版本“%1”", record.name)

    @Slot(str)
    @report_ui_errors
    def restoreNamedVersion(self, version_id: str) -> None:
        self._session._require_writable()
        if any(not task.status.is_terminal for task in self._session.state.tasks.items.values()):
            raise RuntimeError("请等待当前任务完成后再恢复命名版本")
        record = self._session.state.binding.require_current().restore_version(version_id)
        sequence_ids = {
            sequence.id for sequence in self._session.state.binding.require_current().list_sequences()
        }
        if self._session.state.binding.active_sequence_id not in sequence_ids:
            self._session.state.binding.active_sequence_id = (
                self._session.state.binding.require_current().get_project().main_sequence_id
            )
        self._session.state.binding.timeline = self._session.state.binding.require_current().timeline(
            self._session.state.binding.active_sequence_id
        )
        self._session.lifecycle.reset_interaction()
        self._session.projectors.refresh_project()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.updates.commit(project=True, selection=True, history=True)
        self._session._set_status("已恢复命名版本“%1”", record.name)

    @Slot()
    def shutdown(self) -> None:
        self._session.state.requests.shutting_down = True
        self._session.state.runtime_state.cancel.set()
        first_error: BaseException | None = None

        def finish(label: str, operation: Callable[[], object]) -> None:
            nonlocal first_error
            try:
                operation()
            except BaseException as error:
                logger.exception("Desktop shutdown could not finish %s", label)
                if first_error is None:
                    first_error = error

        finish("timeline filmstrip cancellation", self._session.lifecycle.cancel_filmstrip)
        if self._session.state.runtime_state.thread and self._session.state.runtime_state.thread.is_alive():
            self._session.state.runtime_state.thread.join(timeout=5)
        finish("project background requests", self._session.background.shutdown_project_requests)
        finish("project lifecycle", self._session.lifecycle.shutdown)
        finish("pending project close", self._finish_pending_project_close)
        finish("service transport", self._session._api.close_client_transport)
        finish("application background requests", self._session.background.shutdown_application_requests)
        if first_error is not None:
            raise first_error

    def _finish_pending_project_close(self) -> None:
        close_future = self._session.state.requests.project_close_future
        if close_future is not None:
            try:
                close_future.result(timeout=15)
            except FutureTimeoutError:
                logger.error("Timed out while waiting for the closing project to release resources")
                return
            except Exception as error:
                logger.exception("Failed while closing the previous project")
                self._session.state.requests.project_close_future = None
                self._session.state.requests.closing_project_error = str(error)
                self._retry_pending_project_close()
                return
            self._clear_pending_project_close()
        elif self._session.state.requests.closing_project is not None:
            self._retry_pending_project_close()

    def _retry_pending_project_close(self) -> None:
        pending_project = self._session.state.requests.closing_project
        if pending_project is None:
            return
        try:
            pending_project.close(timeout=15)
        except Exception:
            logger.exception("Failed to release the pending project on shutdown")
        else:
            self._clear_pending_project_close()

    def _clear_pending_project_close(self) -> None:
        self._session.state.requests.project_close_future = None
        self._session.state.requests.closing_project = None
        self._session.state.requests.closing_project_error = ""
        self._session.state.requests.project_close_id += 1
