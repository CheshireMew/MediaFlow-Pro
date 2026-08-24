from __future__ import annotations

from fractions import Fraction
from typing import cast

from PySide6.QtCore import Property, QObject, Signal, Slot

from mediaflow.desktop.download_selection import parse_download_entry_selection
from mediaflow.domain.downloads import DownloadCodec, DownloadRequest
from mediaflow.domain.enums import TaskKind
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.task_commands import DownloadMediaCommand

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import DownloadControllerScope


class DownloadController(ControllerFacet[DownloadControllerScope]):
    tasksChanged = Signal()
    downloadPlanChanged = Signal()
    errorOccurred = Signal(str)

    @Property(QObject, constant=True)
    def downloadEntriesModel(self) -> QObject:
        return self._session.models.download_entries

    @Property(bool, notify=downloadPlanChanged)
    def downloadPlanReady(self) -> bool:
        return self._session.state.download.plan is not None

    @Property(bool, notify=downloadPlanChanged)
    def downloadAnalysisBusy(self) -> bool:
        return self._session.state.download.busy

    @Property(dict, notify=downloadPlanChanged)
    def downloadPlanData(self) -> dict:
        if self._session.state.download.plan is None:
            return {}
        data = self._session.state.download.plan.model_dump(mode="json")
        data["entryCount"] = len(self._session.state.download.plan.entries)
        data["availableEntryCount"] = sum(
            entry.available for entry in self._session.state.download.plan.entries
        )
        return data

    def _active_download_tasks(self):
        return [
            task
            for task in self._session.state.tasks.items.values()
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

    @Slot(str)
    def analyzeDownloadUrl(self, url: str) -> None:
        normalized_url = url.strip()
        if not normalized_url:
            self._session.updates.report_error("请输入媒体链接")
            return
        try:
            self._remember_download_url(normalized_url)
        except Exception as error:
            self._session.updates.report_error(f"保存最近媒体地址失败：{error}")
        self._session.state.download.request_id += 1
        request_id = self._session.state.download.request_id
        self._session.state.download.busy = True
        self._session.state.download.plan = None
        self._session.state.download.selected_entries = set()
        self._session.projectors.tasks.refresh_download_entries()
        self._session.updates.commit(download_plan=True)
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
        plan = self._session.state.download.plan
        if plan is None:
            raise RuntimeError("下载分析结果已失效，请重新分析链接")
        requests = self._build_download_requests(
            resolution,
            entry_selection,
            download_subtitles,
            codec,
            filename,
        )
        if plan.media_kind != "audio":
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
        plan = self._session.state.download.plan
        if plan is None:
            raise RuntimeError("下载分析结果已失效，请重新分析链接")
        requests = self._build_download_requests(
            resolution,
            entry_selection,
            download_subtitles,
            codec,
            filename,
        )
        profile = self._download_project_profile(plan, requests[0].resolution)
        project_parent = self._session._local_path(parent_url)
        self._session.lifecycle.create_and_open(
            project_parent,
            project_name,
            profile=profile,
            ensure_unique=True,
        )
        self._session.settings_persistence.remember_default_project_directory(project_parent)
        if plan.media_kind != "audio":
            self._remember_download_preferences(resolution, download_subtitles, codec)
        self._session._start_download_workflow(requests)
        self._clear_download_plan()
        if plan.media_kind == "audio":
            self._session._set_status("项目已创建，正在下载音频")
        else:
            self._session._set_status("项目已创建，正在下载视频")

    def _build_download_requests(
        self,
        resolution: str,
        entry_selection: str,
        download_subtitles: bool,
        codec: str,
        filename: str,
    ) -> list[DownloadRequest]:
        plan = self._session.state.download.plan
        if plan is None:
            raise RuntimeError("下载分析结果已失效，请重新分析链接")
        selected_items = entry_selection.strip()
        available_entries = {entry.index: entry for entry in plan.entries if entry.available}
        if plan.kind == "collection" and not selected_items:
            if not self._session.state.download.selected_entries:
                raise ValueError("请至少选择一个可用的下载项目")
            selected_items = ",".join(
                str(index) for index in sorted(self._session.state.download.selected_entries)
            )
        selected_entry_indices = (
            parse_download_entry_selection(selected_items, set(available_entries))
            if plan.kind == "collection"
            else [plan.entries[0].index]
        )
        selected_resolution = (
            "audio"
            if plan.media_kind == "audio"
            else resolution or self._session.state.service_settings.download.resolution
        )
        if codec not in {"best", "avc"}:
            raise ValueError("下载编码设置无效")
        selected_codec = cast(DownloadCodec, codec)
        return [
            DownloadRequest(
                entry=available_entries[index],
                collection_title=(plan.collection_title if plan.kind == "collection" else ""),
                resolution=selected_resolution,
                codec=selected_codec,
                download_subtitles=download_subtitles if plan.media_kind == "video" else False,
                subtitle_languages=self._session.state.service_settings.download.subtitle_languages,
                filename_prefix=filename.strip(),
                output_directory=self._session.state.service_settings.download.output_directory,
            )
            for index in selected_entry_indices
        ]

    def _remember_download_url(self, url: str) -> None:
        if self._session.state.service_settings.download.last_url == url:
            return
        candidate = self._session.state.service_settings.model_copy(deep=True)
        candidate.download.last_url = url
        self._session.settings_persistence.commit(candidate)

    def _remember_download_preferences(
        self,
        resolution: str,
        download_subtitles: bool,
        codec: str,
    ) -> None:
        selected_resolution = resolution.strip() or "best"
        if codec not in {"best", "avc"}:
            raise ValueError("下载编码设置无效")
        selected_codec = cast(DownloadCodec, codec)
        if (
            self._session.state.service_settings.download.resolution == selected_resolution
            and self._session.state.service_settings.download.download_subtitles == download_subtitles
            and self._session.state.service_settings.download.codec == codec
        ):
            return
        candidate = self._session.state.service_settings.model_copy(deep=True)
        candidate.download.resolution = selected_resolution
        candidate.download.download_subtitles = download_subtitles
        candidate.download.codec = selected_codec
        self._session.settings_persistence.commit(candidate)

    @staticmethod
    def _download_project_profile(plan, resolution: str) -> ProjectProfile | None:
        if plan.media_kind == "audio" or plan.width <= 0 or plan.height <= 0 or resolution == "audio":
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
        self._session.state.download.plan = None
        self._session.state.download.selected_entries = set()
        self._session.projectors.tasks.refresh_download_entries()
        self._session.updates.commit(download_plan=True)

    @Slot()
    def dismissDownloadPlan(self) -> None:
        self._clear_download_plan()

    @Slot(int, bool)
    def setDownloadEntrySelected(self, entry_index: int, selected: bool) -> None:
        plan_entries = self._session.state.download.plan.entries if self._session.state.download.plan else []
        entries = {entry.index: entry for entry in plan_entries}
        if entry_index not in entries or not entries[entry_index].available:
            return
        if selected:
            self._session.state.download.selected_entries.add(entry_index)
        else:
            self._session.state.download.selected_entries.discard(entry_index)
        self._session.projectors.tasks.refresh_download_entries()

    @Slot(bool)
    def selectAllDownloadEntries(self, selected: bool) -> None:
        self._session.state.download.selected_entries = (
            {
                entry.index
                for entry in (
                    self._session.state.download.plan.entries if self._session.state.download.plan else []
                )
                if entry.available
            }
            if selected
            else set()
        )
        self._session.projectors.tasks.refresh_download_entries()
