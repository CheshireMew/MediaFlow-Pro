from __future__ import annotations

from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from mediaflow.desktop.download_selection import parse_download_entry_selection
from mediaflow.domain.downloads import DownloadRequest
from mediaflow.domain.enums import TaskKind, TaskStatus
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.task_commands import DownloadMediaCommand

from .controller_facet import ControllerFacet, report_ui_errors


class TaskController(ControllerFacet):
    taskCenterRequested = Signal()
    tasksChanged = Signal()
    downloadPlanChanged = Signal()
    errorOccurred = Signal(str)

    @Property(QObject, constant=True)
    def downloadEntriesModel(self) -> QObject:
        return self._session.models.download_entries

    @Property(QObject, constant=True)
    def tasksModel(self) -> QObject:
        return self._session.models.tasks

    @Property(int, notify=tasksChanged)
    def activeTaskCount(self) -> int:
        return sum(task.status.is_active for task in self._session.task_state.items.values())

    @Property(int, notify=tasksChanged)
    def inFlightTaskCount(self) -> int:
        return sum(task.status.is_in_flight for task in self._session.task_state.items.values())

    @Property(int, notify=tasksChanged)
    def pausedTaskCount(self) -> int:
        return sum(task.status == TaskStatus.PAUSED for task in self._session.task_state.items.values())

    @Property(int, notify=tasksChanged)
    def terminalTaskCount(self) -> int:
        return sum(task.status.is_terminal for task in self._session.task_state.items.values())

    @Property(bool, notify=downloadPlanChanged)
    def downloadPlanReady(self) -> bool:
        return self._session.download_state.plan is not None

    @Property(bool, notify=downloadPlanChanged)
    def downloadAnalysisBusy(self) -> bool:
        return self._session.download_state.busy

    @Property("QVariantMap", notify=downloadPlanChanged)
    def downloadPlanData(self) -> dict:
        if self._session.download_state.plan is None:
            return {}
        data = self._session.download_state.plan.model_dump(mode="json")
        data["entryCount"] = len(self._session.download_state.plan.entries)
        data["availableEntryCount"] = sum(
            entry.available for entry in self._session.download_state.plan.entries
        )
        return data

    def _active_download_tasks(self):
        return [
            task
            for task in self._session.task_state.items.values()
            if task.kind == TaskKind.DOWNLOAD and task.status.is_active
        ]

    @Property(bool, notify=tasksChanged)
    def downloadProgressVisible(self) -> bool:
        return bool(self._active_download_tasks())

    @Property(float, notify=tasksChanged)
    def downloadProgress(self) -> float:
        tasks = self._active_download_tasks()
        if not tasks or not all(
            task.progress.mode == "determinate" and task.progress.unit == "bytes" for task in tasks
        ):
            return 0.0
        completed = sum(task.progress.completed or 0.0 for task in tasks)
        total = sum(task.progress.total or 0.0 for task in tasks)
        return completed / total * 100.0 if total > 0 else 0.0

    @Property(bool, notify=tasksChanged)
    def downloadProgressDeterminate(self) -> bool:
        tasks = self._active_download_tasks()
        return bool(tasks) and all(
            task.progress.mode == "determinate" and task.progress.unit == "bytes" for task in tasks
        )

    @Property(int, notify=tasksChanged)
    def activeDownloadCount(self) -> int:
        return len(self._active_download_tasks())

    @Property(str, notify=tasksChanged)
    def activeDownloadTitle(self) -> str:
        tasks = self._active_download_tasks()
        if len(tasks) != 1 or not isinstance(tasks[0].command, DownloadMediaCommand):
            return ""
        return tasks[0].command.request.entry.title

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
    def analyzeDownloadUrl(self, url: str) -> None:
        normalized_url = url.strip()
        if not normalized_url:
            self._session.events.errorOccurred.emit("请输入视频链接")
            return
        try:
            self._remember_download_url(normalized_url)
        except Exception as error:
            self._session.events.errorOccurred.emit(f"保存最近视频地址失败：{error}")
        self._session.download_state.request_id += 1
        request_id = self._session.download_state.request_id
        self._session.download_state.busy = True
        self._session.download_state.plan = None
        self._session.download_state.selected_entries = set()
        self._session.projectors.tasks.refresh_download_entries()
        self._session.events.downloadPlanChanged.emit()
        self._session.background.submit(
            "download_plan",
            request_id,
            lambda: self._session._api.analyze_download_url(
                normalized_url,
                check_cancelled=self._session.background.raise_if_shutting_down,
            ),
        )

    @Slot(str, str, bool, str, str)
    @report_ui_errors
    def submitDownloadPlan(
        self,
        resolution: str,
        entry_selection: str,
        download_subtitles: bool,
        codec: str,
        filename: str,
    ) -> None:
        self._session._require_writable()
        requests = self._build_download_requests(
            resolution,
            entry_selection,
            download_subtitles,
            codec,
            filename,
        )
        self._remember_download_preferences(resolution, download_subtitles, codec)
        self._session._start_download_workflow(requests)
        self._clear_download_plan()

    @Slot(str, str, str, str, bool, str, str)
    @report_ui_errors
    def createProjectAndDownload(
        self,
        parent_url: str,
        project_name: str,
        resolution: str,
        entry_selection: str,
        download_subtitles: bool,
        codec: str,
        filename: str,
    ) -> None:
        plan = self._session.download_state.plan
        if plan is None:
            raise RuntimeError("下载分析结果已失效，请重新分析链接")
        requests = self._build_download_requests(
            resolution,
            entry_selection,
            download_subtitles,
            codec,
            filename,
        )
        profile = self._download_project_profile(plan, resolution)
        project_parent = self._session._local_path(parent_url)
        self._session.lifecycle.create_and_open(
            project_parent,
            project_name,
            profile=profile,
            ensure_unique=True,
        )
        self._session._remember_default_project_directory(project_parent)
        self._remember_download_preferences(resolution, download_subtitles, codec)
        self._session._start_download_workflow(requests)
        self._clear_download_plan()
        self._session._set_status("项目已创建，正在下载视频")

    def _build_download_requests(
        self,
        resolution: str,
        entry_selection: str,
        download_subtitles: bool,
        codec: str,
        filename: str,
    ) -> list[DownloadRequest]:
        plan = self._session.download_state.plan
        if plan is None:
            raise RuntimeError("下载分析结果已失效，请重新分析链接")
        selected_items = entry_selection.strip()
        available_entries = {entry.index: entry for entry in plan.entries if entry.available}
        if plan.kind == "collection" and not selected_items:
            if not self._session.download_state.selected_entries:
                raise ValueError("请至少选择一个可用的下载项目")
            selected_items = ",".join(
                str(index) for index in sorted(self._session.download_state.selected_entries)
            )
        selected_entry_indices = (
            parse_download_entry_selection(selected_items, set(available_entries))
            if plan.kind == "collection"
            else [plan.entries[0].index]
        )
        return [
            DownloadRequest(
                entry=available_entries[index],
                collection_title=(plan.collection_title if plan.kind == "collection" else ""),
                resolution=resolution or self._session.settings.download.resolution,
                codec=codec,
                download_subtitles=download_subtitles,
                subtitle_languages=self._session.settings.download.subtitle_languages,
                filename_prefix=filename.strip(),
                output_directory=self._session.settings.download.output_directory,
            )
            for index in selected_entry_indices
        ]

    def _remember_download_url(self, url: str) -> None:
        if self._session.settings.download.last_url == url:
            return
        candidate = self._session.settings.model_copy(deep=True)
        candidate.download.last_url = url
        self._session._commit_settings(candidate)

    def _remember_download_preferences(
        self,
        resolution: str,
        download_subtitles: bool,
        codec: str,
    ) -> None:
        selected_resolution = resolution.strip() or "best"
        if codec not in {"best", "avc"}:
            raise ValueError("下载编码设置无效")
        if (
            self._session.settings.download.resolution == selected_resolution
            and self._session.settings.download.download_subtitles == download_subtitles
            and self._session.settings.download.codec == codec
        ):
            return
        candidate = self._session.settings.model_copy(deep=True)
        candidate.download.resolution = selected_resolution
        candidate.download.download_subtitles = download_subtitles
        candidate.download.codec = codec
        self._session._commit_settings(candidate)

    @staticmethod
    def _download_project_profile(plan, resolution: str) -> ProjectProfile | None:
        if plan.width <= 0 or plan.height <= 0 or resolution == "audio":
            return None
        target_height = plan.height
        height_aliases = {"4k": 2160, "2k": 1440}
        requested_height = height_aliases.get(resolution)
        if requested_height is None and resolution.endswith("p"):
            try:
                requested_height = int(resolution[:-1])
            except ValueError:
                requested_height = None
        if requested_height:
            target_height = min(plan.height, requested_height)
        scale = target_height / plan.height
        target_width = max(2, round(plan.width * scale / 2) * 2)
        target_height = max(2, round(target_height / 2) * 2)
        frame_rate = Fraction(str(plan.fps)).limit_denominator(1001) if plan.fps > 0 else Fraction(30, 1)
        return ProjectProfile(
            width=target_width,
            height=target_height,
            fps_numerator=frame_rate.numerator,
            fps_denominator=frame_rate.denominator,
        )

    def _clear_download_plan(self) -> None:
        self._session.download_state.plan = None
        self._session.download_state.selected_entries = set()
        self._session.projectors.tasks.refresh_download_entries()
        self._session.events.downloadPlanChanged.emit()

    @Slot()
    def dismissDownloadPlan(self) -> None:
        self._clear_download_plan()

    @Slot(int, bool)
    def setDownloadEntrySelected(self, entry_index: int, selected: bool) -> None:
        plan_entries = self._session.download_state.plan.entries if self._session.download_state.plan else []
        entries = {entry.index: entry for entry in plan_entries}
        if entry_index not in entries or not entries[entry_index].available:
            return
        if selected:
            self._session.download_state.selected_entries.add(entry_index)
        else:
            self._session.download_state.selected_entries.discard(entry_index)
        self._session.projectors.tasks.refresh_download_entries()

    @Slot(bool)
    def selectAllDownloadEntries(self, selected: bool) -> None:
        self._session.download_state.selected_entries = (
            {
                entry.index
                for entry in (
                    self._session.download_state.plan.entries if self._session.download_state.plan else []
                )
                if entry.available
            }
            if selected
            else set()
        )
        self._session.projectors.tasks.refresh_download_entries()

    @Slot(str)
    @report_ui_errors
    def pauseTask(self, task_id: str) -> None:
        self._session._require_writable()
        self._session.binding.current.pause_task(task_id)
        self._session._set_status("已请求暂停任务")

    @Slot(str)
    @report_ui_errors
    def resumeTask(self, task_id: str) -> None:
        self._session._require_writable()
        self._session.binding.current.resume_task(task_id)
        self._session.projectors.tasks.refresh_tasks()

    @Slot(str)
    @report_ui_errors
    def cancelTask(self, task_id: str) -> None:
        self._session._require_writable()
        self._session.binding.current.cancel_task(task_id)
        self._session._set_status("已请求取消任务")

    @Slot(str)
    @report_ui_errors
    def retryTask(self, task_id: str) -> None:
        self._session._require_writable()
        self._session.binding.current.retry_task(task_id)
        self._session._set_status("已重新创建任务")
        self._session.projectors.tasks.refresh_tasks()

    @Slot(str)
    @report_ui_errors
    def removeTask(self, task_id: str) -> None:
        self._session._require_writable()
        self._session.binding.current.delete_task(task_id)
        self._session._set_status("已移除任务记录，任务产物仍保留")
        self._session.projectors.tasks.refresh_tasks()

    @Slot()
    @report_ui_errors
    def pauseAllTasks(self) -> None:
        self._session._require_writable()
        count = self._session.binding.current.pause_all_tasks()
        self._session._set_status(f"已请求暂停 {count} 个任务")

    @Slot()
    @report_ui_errors
    def cancelAllTasks(self) -> None:
        self._session._require_writable()
        count = self._session.binding.current.cancel_all_tasks()
        self._session._set_status(f"已请求取消 {count} 个任务")

    @Slot()
    @report_ui_errors
    def clearTaskHistory(self) -> None:
        self._session._require_writable()
        count = self._session.binding.current.clear_task_history()
        self._session._set_status(f"已清理 {count} 条任务记录，任务产物仍保留")
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
            if not self._session.binding.current:
                raise RuntimeError("当前没有打开的项目")
            path = self._session.binding.current.project_dir / path
        path = path.resolve(strict=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            raise RuntimeError(f"无法打开产物：{path}")

    @Slot()
    def openTaskCenter(self) -> None:
        self.taskCenterRequested.emit()
