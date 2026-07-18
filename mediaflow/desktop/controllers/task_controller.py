from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from mediaflow.desktop.download_selection import parse_download_entry_selection
from mediaflow.domain.downloads import DownloadRequest
from mediaflow.domain.enums import TaskKind
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.task_commands import DownloadMediaCommand

from .controller_facet import ControllerFacet


class TaskController(ControllerFacet):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    statusChanged = Signal()
    taskDrawerChanged = Signal()
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
    def downloadEntriesModel(self) -> QObject:
        return self._download_entry_model

    @Property(QObject, constant=True)
    def tasksModel(self) -> QObject:
        return self._task_model

    @Property(int, notify=tasksChanged)
    def activeTaskCount(self) -> int:
        return sum(task.status.is_active for task in self._task_view.values())

    @Property(int, notify=tasksChanged)
    def terminalTaskCount(self) -> int:
        return sum(task.status.is_terminal for task in self._task_view.values())

    @Property(bool, notify=downloadPlanChanged)
    def downloadPlanReady(self) -> bool:
        return self._download_plan is not None

    @Property(bool, notify=downloadPlanChanged)
    def downloadAnalysisBusy(self) -> bool:
        return self._download_analysis_busy

    @Property("QVariantMap", notify=downloadPlanChanged)
    def downloadPlanData(self) -> dict:
        if self._download_plan is None:
            return {}
        data = self._download_plan.model_dump(mode="json")
        data["entryCount"] = len(self._download_plan.entries)
        data["availableEntryCount"] = sum(entry.available for entry in self._download_plan.entries)
        return data

    @Property(bool, notify=taskDrawerChanged)
    def taskDrawerOpen(self) -> bool:
        return self._task_drawer_open

    def _active_download_tasks(self):
        return [
            task
            for task in self._task_view.values()
            if task.kind == TaskKind.DOWNLOAD and task.status.is_active
        ]

    @Property(bool, notify=tasksChanged)
    def downloadProgressVisible(self) -> bool:
        return bool(self._active_download_tasks())

    @Property(float, notify=tasksChanged)
    def downloadProgress(self) -> float:
        tasks = self._active_download_tasks()
        return sum(task.progress for task in tasks) / len(tasks) if tasks else 0.0

    @Property(int, notify=tasksChanged)
    def activeDownloadCount(self) -> int:
        return len(self._active_download_tasks())

    @Property(str, notify=tasksChanged)
    def activeDownloadTitle(self) -> str:
        tasks = self._active_download_tasks()
        if len(tasks) != 1 or not isinstance(tasks[0].command, DownloadMediaCommand):
            return ""
        return tasks[0].command.request.entry.title

    @Slot(str)
    def analyzeDownloadUrl(self, url: str) -> None:
        normalized_url = url.strip()
        if not normalized_url:
            self.errorOccurred.emit("请输入视频链接")
            return
        try:
            self._remember_download_url(normalized_url)
        except Exception as error:
            self.errorOccurred.emit(f"保存最近视频地址失败：{error}")
        self._download_analysis_request_id += 1
        request_id = self._download_analysis_request_id
        self._download_analysis_busy = True
        self._download_plan = None
        self._download_entry_selection = set()
        self._projector.refresh_download_entries()
        self.downloadPlanChanged.emit()
        self._submit_background(
            "download_plan",
            request_id,
            lambda: self._api.analyze_download_url(normalized_url),
        )

    @Slot(str, str, bool, str, str)
    def submitDownloadPlan(
        self,
        resolution: str,
        entry_selection: str,
        download_subtitles: bool,
        codec: str,
        filename: str,
    ) -> None:
        try:
            self._require_writable()
            requests = self._build_download_requests(
                resolution,
                entry_selection,
                download_subtitles,
                codec,
                filename,
            )
            self._remember_download_preferences(resolution, download_subtitles, codec)
            self._start_download_workflow(requests)
            self._clear_download_plan()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str, str, str, bool, str, str)
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
        try:
            plan = self._download_plan
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
            project_parent = self._local_path(parent_url)
            self._create_and_open_project(
                project_parent,
                project_name,
                profile=profile,
                ensure_unique=True,
            )
            self._remember_default_project_directory(project_parent)
            self._remember_download_preferences(resolution, download_subtitles, codec)
            self._start_download_workflow(requests)
            self._clear_download_plan()
            self._set_status("项目已创建，正在下载视频")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    def _build_download_requests(
        self,
        resolution: str,
        entry_selection: str,
        download_subtitles: bool,
        codec: str,
        filename: str,
    ) -> list[DownloadRequest]:
        plan = self._download_plan
        if plan is None:
            raise RuntimeError("下载分析结果已失效，请重新分析链接")
        selected_items = entry_selection.strip()
        available_entries = {entry.index: entry for entry in plan.entries if entry.available}
        if plan.kind == "collection" and not selected_items:
            if not self._download_entry_selection:
                raise ValueError("请至少选择一个可用的下载项目")
            selected_items = ",".join(str(index) for index in sorted(self._download_entry_selection))
        selected_entry_indices = (
            parse_download_entry_selection(selected_items, set(available_entries))
            if plan.kind == "collection"
            else [plan.entries[0].index]
        )
        return [
            DownloadRequest(
                entry=available_entries[index],
                collection_title=(plan.collection_title if plan.kind == "collection" else ""),
                resolution=resolution or self.settings.download.resolution,
                codec=codec,
                download_subtitles=download_subtitles,
                subtitle_languages=self.settings.download.subtitle_languages,
                filename_prefix=filename.strip(),
                output_directory=self.settings.download.output_directory,
            )
            for index in selected_entry_indices
        ]

    def _remember_download_url(self, url: str) -> None:
        if self.settings.download.last_url == url:
            return
        candidate = self.settings.model_copy(deep=True)
        candidate.download.last_url = url
        self._commit_settings(candidate)

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
            self.settings.download.resolution == selected_resolution
            and self.settings.download.download_subtitles == download_subtitles
            and self.settings.download.codec == codec
        ):
            return
        candidate = self.settings.model_copy(deep=True)
        candidate.download.resolution = selected_resolution
        candidate.download.download_subtitles = download_subtitles
        candidate.download.codec = codec
        self._commit_settings(candidate)

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
        self._download_plan = None
        self._download_entry_selection = set()
        self._projector.refresh_download_entries()
        self.downloadPlanChanged.emit()

    @Slot()
    def dismissDownloadPlan(self) -> None:
        self._clear_download_plan()

    @Slot(int, bool)
    def setDownloadEntrySelected(self, entry_index: int, selected: bool) -> None:
        plan_entries = self._download_plan.entries if self._download_plan else []
        entries = {entry.index: entry for entry in plan_entries}
        if entry_index not in entries or not entries[entry_index].available:
            return
        if selected:
            self._download_entry_selection.add(entry_index)
        else:
            self._download_entry_selection.discard(entry_index)
        self._projector.refresh_download_entries()

    @Slot(bool)
    def selectAllDownloadEntries(self, selected: bool) -> None:
        self._download_entry_selection = (
            {
                entry.index
                for entry in (self._download_plan.entries if self._download_plan else [])
                if entry.available
            }
            if selected
            else set()
        )
        self._projector.refresh_download_entries()

    @Slot(str)
    def pauseTask(self, task_id: str) -> None:
        try:
            if not self._tasks:
                raise RuntimeError("当前没有打开的项目")
            self._tasks.pause(task_id)
            self._set_status("已请求暂停任务")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def resumeTask(self, task_id: str) -> None:
        try:
            if not self._tasks:
                raise RuntimeError("当前没有打开的项目")
            self._tasks.resume(task_id)
            self._projector.refresh_tasks()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def cancelTask(self, task_id: str) -> None:
        try:
            if not self._tasks:
                raise RuntimeError("当前没有打开的项目")
            self._tasks.cancel(task_id)
            self._set_status("已请求取消任务")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def retryTask(self, task_id: str) -> None:
        try:
            if not self._tasks:
                raise RuntimeError("当前没有打开的项目")
            self._tasks.retry(task_id)
            self._set_status("已重新创建任务")
            self._projector.refresh_tasks()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def removeTask(self, task_id: str) -> None:
        try:
            if not self._tasks:
                raise RuntimeError("当前没有打开的项目")
            self._tasks.delete(task_id)
            self._set_status("已移除任务记录，任务产物仍保留")
            self._projector.refresh_tasks()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def pauseAllTasks(self) -> None:
        try:
            if not self._tasks:
                raise RuntimeError("当前没有打开的项目")
            count = self._tasks.pause_all()
            self._set_status(f"已请求暂停 {count} 个任务")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def cancelAllTasks(self) -> None:
        try:
            if not self._tasks:
                raise RuntimeError("当前没有打开的项目")
            count = self._tasks.cancel_all()
            self._set_status(f"已请求取消 {count} 个任务")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def clearTaskHistory(self) -> None:
        try:
            if not self._tasks:
                raise RuntimeError("当前没有打开的项目")
            count = self._tasks.clear_history()
            self._set_status(f"已清理 {count} 条任务记录，任务产物仍保留")
            self._projector.refresh_tasks()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def openArtifact(self, path_value: str) -> None:
        try:
            path = Path(path_value)
            if not path.is_absolute():
                if not self._documents:
                    raise RuntimeError("当前没有打开的项目")
                path = self._documents.project_dir / path
            path = path.resolve(strict=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                raise RuntimeError(f"无法打开产物：{path}")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def toggleTaskDrawer(self) -> None:
        self._task_drawer_open = not self._task_drawer_open
        self.taskDrawerChanged.emit()
