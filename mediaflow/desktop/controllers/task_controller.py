from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from mediaflow.domain.enums import TaskStatus

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import TaskControllerScope


class TaskController(ControllerFacet[TaskControllerScope]):
    taskCenterRequested = Signal()
    tasksChanged = Signal()
    errorOccurred = Signal(str)

    @Property(QObject, constant=True)
    def tasksModel(self) -> QObject:
        return self._session.models.tasks

    @Property(int, notify=tasksChanged)
    def activeTaskCount(self) -> int:
        return sum(task.status.is_active for task in self._session.state.tasks.items.values())

    @Property(int, notify=tasksChanged)
    def inFlightTaskCount(self) -> int:
        return sum(task.status.is_in_flight for task in self._session.state.tasks.items.values())

    @Property(int, notify=tasksChanged)
    def pausedTaskCount(self) -> int:
        return sum(task.status == TaskStatus.PAUSED for task in self._session.state.tasks.items.values())

    @Property(int, notify=tasksChanged)
    def terminalTaskCount(self) -> int:
        return sum(task.status.is_terminal for task in self._session.state.tasks.items.values())

    @Slot(str, str, result="QVariantMap")
    def latestTask(self, kind: str, context_id: str = "") -> dict:
        return self._latest_matching_task(
            lambda row: row.get("kind") == kind,
            context_id,
        )

    @Slot(str, str, result="QVariantMap")
    def latestCommandTask(self, command_type: str, context_id: str = "") -> dict:
        return self._latest_matching_task(
            lambda row: row.get("commandType") == command_type,
            context_id,
        )

    @Slot(str, result="QVariantMap")
    def latestMediaTask(self, asset_id: str = "") -> dict:
        task = self._latest_matching_task(
            lambda row: row.get("kind") in {"import", "proxy", "waveform"},
            asset_id,
        )
        return {} if task.get("status") == "completed" else task

    def _latest_matching_task(
        self,
        predicate: Callable[[dict], bool],
        context_id: str,
    ) -> dict:
        matches: list[dict] = []
        for index in range(self._session.models.tasks.rowCount()):
            row = self._session.models.tasks.get(index)
            if not predicate(row):
                continue
            if (
                context_id
                and context_id not in (row.get("inputAssetIds") or [])
                and context_id != row.get("contextId")
            ):
                continue
            matches.append(row)
        return next(
            (row for row in matches if row.get("status") in {"pending", "running", "paused"}),
            matches[0] if matches else {},
        )

    @Slot(str)
    @report_ui_errors
    def pauseTask(self, task_id: str) -> None:
        self._session._require_writable()
        self._session.state.binding.require_current().pause_task(task_id)
        self._session._set_status("已请求暂停任务")

    @Slot(str)
    @report_ui_errors
    def resumeTask(self, task_id: str) -> None:
        self._session._require_writable()
        self._session.state.binding.require_current().resume_task(task_id)
        self._session.projectors.tasks.refresh_tasks()

    @Slot(str)
    @report_ui_errors
    def cancelTask(self, task_id: str) -> None:
        self._session._require_writable()
        self._session.state.binding.require_current().cancel_task(task_id)
        self._session._set_status("已请求取消任务")

    @Slot(str)
    @report_ui_errors
    def retryTask(self, task_id: str) -> None:
        self._session._require_writable()
        self._session.state.binding.require_current().retry_task(task_id)
        self._session._set_status("已重新创建任务")
        self._session.projectors.tasks.refresh_tasks()

    @Slot(str)
    @report_ui_errors
    def removeTask(self, task_id: str) -> None:
        self._session._require_writable()
        self._session.state.binding.require_current().delete_task(task_id)
        self._session._set_status("已移除任务记录，任务产物仍保留")
        self._session.projectors.tasks.refresh_tasks()

    @Slot()
    @report_ui_errors
    def pauseAllTasks(self) -> None:
        self._session._require_writable()
        count = self._session.state.binding.require_current().pause_all_tasks()
        self._session._set_status("已请求暂停 %1 个任务", count)

    @Slot()
    @report_ui_errors
    def cancelAllTasks(self) -> None:
        self._session._require_writable()
        count = self._session.state.binding.require_current().cancel_all_tasks()
        self._session._set_status("已请求取消 %1 个任务", count)

    @Slot()
    @report_ui_errors
    def clearTaskHistory(self) -> None:
        self._session._require_writable()
        count = self._session.state.binding.require_current().clear_task_history()
        self._session._set_status("已清理 %1 条任务记录，任务产物仍保留", count)
        self._session.projectors.tasks.refresh_tasks()

    @Slot(str)
    def copyErrorDetails(self, error: str) -> None:
        value = error.strip()
        if not value:
            return
        QGuiApplication.clipboard().setText(value)
        self._session._set_status("错误详情已复制")

    @Slot(str)
    @report_ui_errors
    def openArtifact(self, path_value: str) -> None:
        path = Path(path_value)
        if not path.is_absolute():
            if not self._session.state.binding.current:
                raise RuntimeError("当前没有打开的项目")
            path = self._session.state.binding.require_current().project_dir / path
        path = path.resolve(strict=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            raise RuntimeError(f"无法打开产物：{path}")

    @Slot()
    def openTaskCenter(self) -> None:
        self.taskCenterRequested.emit()
