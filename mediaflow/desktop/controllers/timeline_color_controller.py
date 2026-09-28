from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, Signal, Slot

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import TimelinePresentationScope


class TimelineColorController(ControllerFacet[TimelinePresentationScope]):
    scopeChanged = Signal()

    def __init__(self, scope: TimelinePresentationScope) -> None:
        super().__init__(scope)
        self._scope_data: dict[str, Any] = {}
        self._running = False
        self._request_id = 0

    @Property(dict, notify=scopeChanged)
    def scopeData(self) -> dict[str, Any]:
        return dict(self._scope_data)

    @Property(bool, notify=scopeChanged)
    def running(self) -> bool:
        return self._running

    @Slot(int)
    @report_ui_errors
    def analyzeFrame(self, frame: int) -> None:
        current = self._session.state.binding.current
        if current is None:
            raise RuntimeError("请先打开项目")
        state = self._session.state.binding.require_timeline().state
        if not 0 <= frame < state.duration_frames:
            raise ValueError("当前播放头没有可分析的画面")
        self._request_id += 1
        request_id = self._request_id
        self._running = True
        self.scopeChanged.emit()
        self._session.background.submit_project_callback(
            "color_scopes",
            request_id,
            lambda: self._session._api.execute_read_operation(
                "color.scope.analyze",
                current.project_dir,
                {
                    "sequence_id": self._session.state.binding.active_sequence_id,
                    "frame": frame,
                    "bins": 64,
                    "use_proxies": True,
                },
            ),
            on_result=lambda result: self._finish(request_id, result),
            on_error=lambda error: self._fail(request_id, error),
        )

    def _finish(self, request_id: int, result: object | None) -> None:
        if request_id != self._request_id:
            return
        if not isinstance(result, dict):
            self._fail(request_id, RuntimeError("示波器返回了无效结果"))
            return
        self._scope_data = result
        self._running = False
        self.scopeChanged.emit()
        self._session._set_status("视频示波器已更新")

    def _fail(self, request_id: int, error: BaseException) -> None:
        if request_id != self._request_id:
            return
        self._running = False
        self._scope_data = {"error": str(error)}
        self.scopeChanged.emit()
