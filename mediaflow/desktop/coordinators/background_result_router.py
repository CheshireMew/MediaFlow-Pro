from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mediaflow.application.presentation_models import RecentProjectSnapshot
from mediaflow.domain.downloads import DownloadPlan

logger = logging.getLogger(__name__)


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


class BackgroundResultRouter:
    """Routes typed background results to one focused state transition per request kind."""

    def __init__(self, session: Any):
        self._session = session
        self._handlers: dict[str, Callable[[BackgroundResult], None]] = {
            "recent_projects": self._handle_recent_projects,
            "encoder_policies": self._handle_encoder_policies,
            "runtime_status": self._handle_runtime_status,
            "download_plan": self._handle_download_plan,
            "waveform": self._handle_waveform,
            "asset_thumbnails": self._handle_asset_thumbnails,
            "audio_metrics": self._handle_audio_metrics,
            "timeline_filmstrip": self._handle_timeline_filmstrip,
            "project_close": self._handle_project_close,
            "preview": self._handle_preview,
        }

    @property
    def supported_kinds(self) -> frozenset[str]:
        return frozenset(self._handlers)

    def dispatch(self, payload: BackgroundResult) -> bool:
        handler = self._handlers.get(payload.kind)
        if handler is None:
            return False
        handler(payload)
        return True

    def _handle_recent_projects(self, payload: BackgroundResult) -> None:
        if payload.request_id != self._session.state.requests.recent_id:
            return
        if payload.error:
            self._session.updates.report_error(f"读取最近项目失败：{payload.error}")
        else:
            self._session.projectors.workspace.apply_recent_projects(_require_recent_projects(payload.result))

    def _handle_encoder_policies(self, payload: BackgroundResult) -> None:
        if payload.request_id != self._session.state.requests.encoder_id:
            return
        if payload.error:
            self._session.updates.report_error(f"检测编码器失败：{payload.error}")
        else:
            self._session.state.presentation.encoder_policy_options = _require_dict_rows(
                payload.result,
                "Encoder-policy",
            )
            self._session.updates.commit(settings=True)

    def _handle_runtime_status(self, payload: BackgroundResult) -> None:
        if payload.error:
            logger.warning("Failed to read runtime status: %s", payload.error)
            return
        self._session.projectors.workspace.apply_runtime_tool_status(
            _require_object_map(payload.result, "Runtime-status"),
            preserve_cuda=False,
        )

    def _handle_download_plan(self, payload: BackgroundResult) -> None:
        if payload.request_id != self._session.state.download.request_id:
            return
        self._session.state.download.busy = False
        if payload.error:
            self._session.state.download.plan = None
            self._session.state.download.selected_entries = set()
            self._session.projectors.tasks.refresh_download_entries()
            self._session.updates.commit(download_plan=True)
            self._session.updates.report_error(f"读取媒体信息失败：{payload.error}")
            return
        if not isinstance(payload.result, DownloadPlan):
            raise TypeError("Download-plan request returned an invalid plan")
        self._session._set_download_plan(payload.result)

    def _handle_waveform(self, payload: BackgroundResult) -> None:
        generation, asset_id, path_value = _require_int_str_str_request(
            payload.request_id,
            "Waveform",
        )
        self._session.state.assets.waveform_pending.discard((generation, asset_id, path_value))
        if generation != self._session.state.binding.generation:
            return
        if payload.error:
            logger.warning(
                "Failed to preload waveform (asset=%s, path=%s): %s",
                asset_id,
                path_value,
                payload.error,
            )
            return
        modified, waveform = _require_waveform(payload.result)
        self._session.state.assets.waveform_cache[asset_id] = (path_value, modified, waveform)
        self._session.updates.commit(waveform_asset_id=asset_id)

    def _handle_asset_thumbnails(self, payload: BackgroundResult) -> None:
        if payload.request_id != self._session.state.assets.thumbnail_pending_request:
            return
        self._session.state.assets.thumbnail_pending_request = None
        generation, _thumbnail_request_id, _project_path = _require_int_int_str_request(
            payload.request_id,
            "Asset-thumbnail",
        )
        if generation != self._session.state.binding.generation:
            return
        if payload.error:
            logger.warning("Failed to prepare asset thumbnails: %s", payload.error)
        else:
            self._session.projectors.assets.apply_asset_thumbnails(
                _require_string_map(payload.result, "Asset-thumbnail")
            )
        if self._session.state.assets.thumbnail_refresh_requested:
            self._session.state.assets.thumbnail_refresh_requested = False
            assets = (
                self._session.state.binding.require_current().list_assets()
                if self._session.state.binding.current
                else []
            )
            self._session.projectors.assets.request_asset_thumbnails(assets)

    def _handle_audio_metrics(self, payload: BackgroundResult) -> None:
        generation, metrics_request_id, sequence_id = _require_int_int_str_request(
            payload.request_id,
            "Audio-metrics",
        )
        if (
            generation != self._session.state.binding.generation
            or metrics_request_id != self._session.state.requests.audio_metrics_id
            or sequence_id != self._session.state.binding.active_sequence_id
        ):
            return
        if payload.error:
            logger.warning(
                "Failed to read loudness metrics (sequence=%s): %s",
                sequence_id,
                payload.error,
            )
            metrics = {}
        else:
            metrics = _require_object_map(payload.result, "Audio-metrics")
        if metrics != self._session.state.presentation.audio_metrics:
            self._session.state.presentation.audio_metrics = metrics
        self._session.updates.commit(audio_metrics=True)

    def _handle_timeline_filmstrip(self, payload: BackgroundResult) -> None:
        generation, filmstrip_id, sequence_id = _require_int_int_str_request(
            payload.request_id,
            "Timeline-filmstrip",
        )
        if (
            generation != self._session.state.binding.generation
            or filmstrip_id != self._session.state.requests.filmstrip_id
            or sequence_id != self._session.state.binding.active_sequence_id
        ):
            return
        self._session.state.requests.filmstrip_future = None
        if payload.error:
            logger.warning("Failed to prepare timeline filmstrip: %s", payload.error)
            return
        grouped: dict[str, list[dict]] = {}
        for item in _require_dict_rows(payload.result, "Timeline-filmstrip"):
            grouped.setdefault(str(item["clipId"]), []).append(dict(item))
        changed_clip_ids = set(self._session.state.presentation.filmstrip_frames).union(grouped)
        self._session.state.presentation.filmstrip_frames = grouped
        self._session.projectors.timeline.refresh_clip_rows(
            list(changed_clip_ids),
            defer_updates=True,
            refresh_relations=False,
            schedule_preview=False,
        )

    def _handle_project_close(self, payload: BackgroundResult) -> None:
        close_id, project_path = _require_int_str_request(
            payload.request_id,
            "Project-close",
        )
        if close_id != self._session.state.requests.project_close_id:
            return
        self._session.state.requests.project_close_future = None
        self._session.projectors.workspace.refresh_recent_projects()
        if payload.error:
            self._session.state.requests.closing_project_error = str(payload.error)
            self._session.updates.report_error(f"关闭项目时释放资源失败：{payload.error}")
        else:
            self._session.state.requests.closing_project = None
            self._session.state.requests.closing_project_error = ""
            self._session._set_status("项目已关闭：%1", project_path)
        self._session.updates.commit(project=True)

    def _handle_preview(self, payload: BackgroundResult) -> None:
        generation, preview_request_id, sequence_id = _require_int_int_str_request(
            payload.request_id,
            "Preview",
        )
        if (
            generation != self._session.state.binding.generation
            or preview_request_id != self._session.state.requests.preview_id
            or sequence_id != self._session.state.binding.active_sequence_id
        ):
            return
        if payload.error:
            self._session.updates.report_error(f"预览图编译失败：{payload.error}")
            return
        self._session.state.presentation.preview_graph_path = str(_require_path(payload.result, "Preview"))
        self._session.updates.commit(preview_graph=True)
        if self._session.state.presentation.pending_preview_range is not None:
            start_frame, end_frame = self._session.state.presentation.pending_preview_range
            self._session.state.presentation.pending_preview_range = None
            self._session.updates.request_preview_range(start_frame, end_frame)
