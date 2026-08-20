from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from mediaflow.domain.downloads import DownloadPlan
from mediaflow.project_presentation import RecentProjectSnapshot

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


@dataclass(frozen=True, slots=True)
class BackgroundResult:
    kind: str
    request_id: object
    result: object | None
    error: BaseException | None


def _require_recent_projects(value: object | None) -> RecentProjectSnapshot:
    if not isinstance(value, RecentProjectSnapshot):
        raise TypeError("Recent-project request returned an invalid snapshot")
    return value


def _require_dict_rows(value: object | None, label: str) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, dict) for item in value):
        raise TypeError(f"{label} request returned invalid rows")
    return [{str(key): item for key, item in row.items()} for row in value]


def _require_string_map(value: object | None, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise TypeError(f"{label} request returned an invalid string map")
    return dict(value)


def _require_object_map(value: object | None, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TypeError(f"{label} request returned an invalid object map")
    return {str(key): item for key, item in value.items()}


def _require_int_int_str_request(value: object, label: str) -> tuple[int, int, str]:
    if (
        not isinstance(value, tuple)
        or len(value) != 3
        or not isinstance(value[0], int)
        or not isinstance(value[1], int)
        or not isinstance(value[2], str)
    ):
        raise TypeError(f"{label} request identity is invalid")
    return value


def _require_int_str_str_request(value: object, label: str) -> tuple[int, str, str]:
    if (
        not isinstance(value, tuple)
        or len(value) != 3
        or not isinstance(value[0], int)
        or not isinstance(value[1], str)
        or not isinstance(value[2], str)
    ):
        raise TypeError(f"{label} request identity is invalid")
    return value


def _require_int_str_request(value: object, label: str) -> tuple[int, str]:
    if (
        not isinstance(value, tuple)
        or len(value) != 2
        or not isinstance(value[0], int)
        or not isinstance(value[1], str)
    ):
        raise TypeError(f"{label} request identity is invalid")
    return value


def _require_waveform(value: object | None) -> tuple[int, dict[str, object]]:
    if (
        not isinstance(value, tuple)
        or len(value) != 2
        or not isinstance(value[0], int)
        or not isinstance(value[1], dict)
        or not all(isinstance(key, str) for key in value[1])
    ):
        raise TypeError("Waveform request returned an invalid payload")
    return value[0], {str(key): item for key, item in value[1].items()}


def _require_path(value: object | None, label: str) -> Path:
    if not isinstance(value, Path):
        raise TypeError(f"{label} request returned an invalid path")
    return value


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
        self._project_futures: set[Future[object]] = set()
        self._project_futures_lock = threading.Lock()
        self._bridge = _BackgroundBridge(session)
        self._bridge.resultReceived.connect(self._on_result)

    def submit(
        self,
        kind: str,
        request_id: object,
        operation: Callable[[], object],
        *,
        executor: ThreadPoolExecutor | None = None,
        publish_result: bool = True,
    ) -> Future[object] | None:
        if self._session.state.requests.shutting_down:
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

    def _forget_project_future(self, future: Future[object]) -> None:
        with self._project_futures_lock:
            self._project_futures.discard(future)

    def _publish_result(
        self,
        kind: str,
        request_id: object,
        completed: Future[object],
    ) -> None:
        try:
            result = completed.result()
        except Exception as error:
            payload = BackgroundResult(kind, request_id, None, error)
        else:
            payload = BackgroundResult(kind, request_id, result, None)
        if not self._session.state.requests.shutting_down:
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
                raise TimeoutError(f"Timed out waiting for {len(unfinished)} project background request(s)")
        self.preview_executor.shutdown(wait=True, cancel_futures=True)
        self._project_executor.shutdown(wait=True, cancel_futures=True)

    def shutdown_application_requests(self) -> None:
        # The service transport is closed before this call, which interrupts
        # pending network operations such as download inspection.
        self._application_executor.shutdown(wait=True, cancel_futures=True)

    def raise_if_shutting_down(self) -> None:
        if self._session.state.requests.shutting_down:
            raise CancelledError("Desktop background requests are shutting down")

    @Slot(object)
    def _on_result(self, payload: object) -> None:
        if self._session.state.requests.shutting_down:
            return
        if not isinstance(payload, BackgroundResult):
            return
        kind = payload.kind
        request_id = payload.request_id
        result = payload.result
        error = payload.error
        if kind == "recent_projects":
            if request_id != self._session.state.requests.recent_id:
                return
            if error:
                self._session.updates.report_error(f"读取最近项目失败：{error}")
            else:
                self._session.projectors.workspace.apply_recent_projects(_require_recent_projects(result))
            return
        if kind == "encoder_policies":
            if request_id != self._session.state.requests.encoder_id:
                return
            if error:
                self._session.updates.report_error(f"检测编码器失败：{error}")
            else:
                self._session.state.presentation.encoder_policy_options = _require_dict_rows(
                    result,
                    "Encoder-policy",
                )
                self._session.updates.commit(settings=True)
            return
        if kind == "download_plan":
            if request_id != self._session.state.download.request_id:
                return
            self._session.state.download.busy = False
            if error:
                self._session.state.download.plan = None
                self._session.state.download.selected_entries = set()
                self._session.projectors.tasks.refresh_download_entries()
                self._session.updates.commit(download_plan=True)
                self._session.updates.report_error(f"读取媒体信息失败：{error}")
            else:
                if not isinstance(result, DownloadPlan):
                    raise TypeError("Download-plan request returned an invalid plan")
                self._session._set_download_plan(result)
            return
        if kind == "waveform":
            generation, asset_id, path_value = _require_int_str_str_request(
                request_id,
                "Waveform",
            )
            self._session.state.assets.waveform_pending.discard((generation, asset_id, path_value))
            if generation != self._session.state.binding.generation:
                return
            if error:
                logger.warning(
                    "Failed to preload waveform (asset=%s, path=%s): %s",
                    asset_id,
                    path_value,
                    error,
                )
                return
            modified, waveform = _require_waveform(result)
            self._session.state.assets.waveform_cache[asset_id] = (path_value, modified, waveform)
            self._session.updates.commit(waveform_asset_id=asset_id)
            return
        if kind == "asset_thumbnails":
            if request_id != self._session.state.assets.thumbnail_pending_request:
                return
            self._session.state.assets.thumbnail_pending_request = None
            generation, _thumbnail_request_id, _project_path = _require_int_int_str_request(
                request_id,
                "Asset-thumbnail",
            )
            if generation != self._session.state.binding.generation:
                return
            if error:
                logger.warning("Failed to prepare asset thumbnails: %s", error)
            else:
                self._session.projectors.assets.apply_asset_thumbnails(
                    _require_string_map(result, "Asset-thumbnail")
                )
            if self._session.state.assets.thumbnail_refresh_requested:
                self._session.state.assets.thumbnail_refresh_requested = False
                assets = (
                    self._session.state.binding.require_current().list_assets()
                    if self._session.state.binding.current
                    else []
                )
                self._session.projectors.assets.request_asset_thumbnails(assets)
            return
        if kind == "audio_metrics":
            generation, metrics_request_id, sequence_id = _require_int_int_str_request(
                request_id,
                "Audio-metrics",
            )
            if (
                generation != self._session.state.binding.generation
                or metrics_request_id != self._session.state.requests.audio_metrics_id
                or sequence_id != self._session.state.binding.active_sequence_id
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
                metrics = _require_object_map(result, "Audio-metrics")
            if metrics != self._session.state.presentation.audio_metrics:
                self._session.state.presentation.audio_metrics = metrics
            self._session.updates.commit(audio_metrics=True)
            return
        if kind == "timeline_filmstrip":
            generation, filmstrip_id, sequence_id = _require_int_int_str_request(
                request_id,
                "Timeline-filmstrip",
            )
            if (
                generation != self._session.state.binding.generation
                or filmstrip_id != self._session.state.requests.filmstrip_id
                or sequence_id != self._session.state.binding.active_sequence_id
            ):
                return
            self._session.state.requests.filmstrip_future = None
            if error:
                logger.warning("Failed to prepare timeline filmstrip: %s", error)
                return
            grouped: dict[str, list[dict]] = {}
            for item in _require_dict_rows(result, "Timeline-filmstrip"):
                grouped.setdefault(str(item["clipId"]), []).append(dict(item))
            self._session.state.presentation.filmstrip_frames = grouped
            self._session.projectors.timeline.refresh_timeline(defer_clip_updates=True)
            return
        if kind == "project_close":
            close_id, project_path = _require_int_str_request(
                request_id,
                "Project-close",
            )
            if close_id != self._session.state.requests.project_close_id:
                return
            self._session.state.requests.project_close_future = None
            self._session.projectors.workspace.refresh_recent_projects()
            if error:
                self._session.state.requests.closing_project_error = str(error)
                self._session.updates.report_error(f"关闭项目时释放资源失败：{error}")
            else:
                self._session.state.requests.closing_project = None
                self._session.state.requests.closing_project_error = ""
                self._session._set_status("项目已关闭：%1", project_path)
            self._session.updates.commit(project=True)
            return
        if kind != "preview":
            return
        generation, preview_request_id, sequence_id = _require_int_int_str_request(
            request_id,
            "Preview",
        )
        if (
            generation != self._session.state.binding.generation
            or preview_request_id != self._session.state.requests.preview_id
            or sequence_id != self._session.state.binding.active_sequence_id
        ):
            return
        if error:
            self._session.updates.report_error(f"预览图编译失败：{error}")
            return
        self._session.state.presentation.preview_graph_path = str(_require_path(result, "Preview"))
        self._session.updates.commit(preview_graph=True)
        if self._session.state.presentation.pending_preview_range is not None:
            start_frame, end_frame = self._session.state.presentation.pending_preview_range
            self._session.state.presentation.pending_preview_range = None
            self._session.updates.request_preview_range(start_frame, end_frame)
