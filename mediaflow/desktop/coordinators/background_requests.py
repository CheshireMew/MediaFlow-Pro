from __future__ import annotations

import logging
import threading
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, wait

from PySide6.QtCore import QObject, Signal, Slot

from .base import SessionCoordinator

logger = logging.getLogger(__name__)
PROJECT_REQUEST_SHUTDOWN_TIMEOUT_SECONDS = 15.0

_PROJECT_REQUEST_KINDS = frozenset(
    {
        "asset_thumbnails",
        "audio_metrics",
        "timeline_filmstrip",
        "project_close",
        "waveform",
    }
)


class _BackgroundBridge(QObject):
    resultReceived = Signal(object)


class BackgroundRequests(SessionCoordinator):
    def __init__(self, session):
        super().__init__(session)
        self._application_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="mediaflow-desktop-io",
        )
        self._project_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="mediaflow-project-io",
        )
        self.preview_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="mediaflow-preview-compile",
        )
        self._project_futures: set[Future] = set()
        self._project_futures_lock = threading.Lock()
        self._bridge = _BackgroundBridge(session)
        self._bridge.resultReceived.connect(self._on_result)

    def submit(
        self,
        kind: str,
        request_id: object,
        operation,
        *,
        executor: ThreadPoolExecutor | None = None,
        publish_result: bool = True,
    ) -> Future | None:
        if self._session.requests.shutting_down:
            return None
        worker = executor or (
            self._project_executor if kind in _PROJECT_REQUEST_KINDS else self._application_executor
        )
        future = worker.submit(operation)
        if worker is self._project_executor or worker is self.preview_executor:
            with self._project_futures_lock:
                self._project_futures.add(future)
            future.add_done_callback(self._forget_project_future)
        if publish_result:
            future.add_done_callback(
                lambda completed: self._publish_result(
                    kind,
                    request_id,
                    completed,
                )
            )
        return future

    def _forget_project_future(self, future: Future) -> None:
        with self._project_futures_lock:
            self._project_futures.discard(future)

    def _publish_result(
        self,
        kind: str,
        request_id: object,
        completed: Future,
    ) -> None:
        try:
            result = completed.result()
        except Exception as error:
            payload = (kind, request_id, None, error)
        else:
            payload = (kind, request_id, result, None)
        if not self._session.requests.shutting_down:
            self._bridge.resultReceived.emit(payload)

    def shutdown_project_requests(
        self,
        *,
        timeout: float = PROJECT_REQUEST_SHUTDOWN_TIMEOUT_SECONDS,
    ) -> None:
        if timeout < 0:
            raise ValueError("Project request shutdown timeout cannot be negative")
        # Project readers must finish before the project session is released;
        # they may own SQLite readers or fingerprint service-owned cache files.
        self.preview_executor.shutdown(wait=False, cancel_futures=True)
        self._project_executor.shutdown(wait=False, cancel_futures=True)
        with self._project_futures_lock:
            pending = tuple(self._project_futures)
        if pending:
            _done, unfinished = wait(pending, timeout=timeout)
            if unfinished:
                raise TimeoutError(
                    f"Timed out waiting for {len(unfinished)} project background request(s)"
                )
        self.preview_executor.shutdown(wait=True, cancel_futures=True)
        self._project_executor.shutdown(wait=True, cancel_futures=True)

    def shutdown_application_requests(self) -> None:
        # The service transport is closed before this call, which interrupts
        # pending network operations such as download inspection.
        self._application_executor.shutdown(wait=True, cancel_futures=True)

    def raise_if_shutting_down(self) -> None:
        if self._session.requests.shutting_down:
            raise CancelledError("Desktop background requests are shutting down")

    @Slot(object)
    def _on_result(self, payload: object) -> None:
        if self._session.requests.shutting_down:
            return
        try:
            kind, request_id, result, error = payload
        except (TypeError, ValueError):
            return
        if kind == "recent_projects":
            if request_id != self._session.requests.recent_id:
                return
            if error:
                self._session.events.errorOccurred.emit(f"读取最近项目失败：{error}")
            else:
                self._session.projectors.workspace.apply_recent_projects(result)
            return
        if kind == "encoder_policies":
            if request_id != self._session.requests.encoder_id:
                return
            if error:
                self._session.events.errorOccurred.emit(f"检测编码器失败：{error}")
            else:
                self._session.presentation.encoder_policy_options = list(result)
                self._session.events.settingsChanged.emit()
            return
        if kind == "download_plan":
            if request_id != self._session.download_state.request_id:
                return
            self._session.download_state.busy = False
            if error:
                self._session.download_state.plan = None
                self._session.download_state.selected_entries = set()
                self._session.projectors.tasks.refresh_download_entries()
                self._session.events.downloadPlanChanged.emit()
                self._session.events.errorOccurred.emit(f"读取视频信息失败：{error}")
            else:
                self._session._set_download_plan(result)
            return
        if kind == "waveform":
            self._session.asset_state.waveform_pending.discard(request_id)
            generation, asset_id, path_value = request_id
            if generation != self._session.binding.generation:
                return
            if error:
                logger.warning(
                    "Failed to preload waveform (asset=%s, path=%s): %s",
                    asset_id,
                    path_value,
                    error,
                )
                return
            modified, waveform = result
            self._session.asset_state.waveform_cache[asset_id] = (path_value, modified, waveform)
            self._session.events.waveformDataChanged.emit(asset_id)
            return
        if kind == "asset_thumbnails":
            if request_id != self._session.asset_state.thumbnail_pending_request:
                return
            self._session.asset_state.thumbnail_pending_request = None
            generation, _thumbnail_request_id, _project_path = request_id
            if generation != self._session.binding.generation:
                return
            if error:
                logger.warning("Failed to prepare asset thumbnails: %s", error)
            else:
                self._session.projectors.assets.apply_asset_thumbnails(result)
            if self._session.asset_state.thumbnail_refresh_requested:
                self._session.asset_state.thumbnail_refresh_requested = False
                assets = self._session.binding.current.list_assets() if self._session.binding.current else []
                self._session.projectors.assets.request_asset_thumbnails(assets)
            return
        if kind == "audio_metrics":
            generation, metrics_request_id, sequence_id = request_id
            if (
                generation != self._session.binding.generation
                or metrics_request_id != self._session.requests.audio_metrics_id
                or sequence_id != self._session.binding.active_sequence_id
            ):
                return
            if error:
                logger.warning(
                    "Failed to read loudness metrics (sequence=%s): %s",
                    sequence_id,
                    error,
                )
                metrics = {}
            else:
                metrics = dict(result)
            if metrics != self._session.presentation.audio_metrics:
                self._session.presentation.audio_metrics = metrics
            self._session.events.audioMetricsChanged.emit()
            return
        if kind == "timeline_filmstrip":
            generation, filmstrip_id, sequence_id = request_id
            if (
                generation != self._session.binding.generation
                or filmstrip_id != self._session.requests.filmstrip_id
                or sequence_id != self._session.binding.active_sequence_id
            ):
                return
            self._session.requests.filmstrip_future = None
            if error:
                logger.warning("Failed to prepare timeline filmstrip: %s", error)
                return
            grouped: dict[str, list[dict]] = {}
            for item in result:
                grouped.setdefault(str(item["clipId"]), []).append(dict(item))
            self._session.presentation.filmstrip_frames = grouped
            self._session.projectors.timeline.refresh_timeline(defer_clip_updates=True)
            return
        if kind == "project_close":
            close_id, project_path = request_id
            if close_id != self._session.requests.project_close_id:
                return
            self._session.requests.project_close_future = None
            self._session.projectors.workspace.refresh_recent_projects()
            if error:
                self._session.requests.closing_project_error = str(error)
                self._session.events.errorOccurred.emit(f"关闭项目时释放资源失败：{error}")
            else:
                self._session.requests.closing_project = None
                self._session.requests.closing_project_error = ""
                self._session._set_status("项目已关闭：%1", project_path)
            self._session.events.projectStateChanged.emit()
            return
        if kind != "preview":
            return
        generation, preview_request_id, sequence_id = request_id
        if (
            generation != self._session.binding.generation
            or preview_request_id != self._session.requests.preview_id
            or sequence_id != self._session.binding.active_sequence_id
        ):
            return
        if error:
            self._session.events.errorOccurred.emit(f"预览图编译失败：{error}")
            return
        self._session.presentation.preview_graph_path = str(result)
        self._session.events.previewGraphChanged.emit()
        if self._session.presentation.pending_preview_range is not None:
            start_frame, end_frame = self._session.presentation.pending_preview_range
            self._session.presentation.pending_preview_range = None
            self._session.events.previewRangeRequested.emit(start_frame, end_frame)
