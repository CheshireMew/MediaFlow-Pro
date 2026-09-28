from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QUrl, Signal, Slot

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import TimelinePresentationScope


class TimelineInterchangeController(ControllerFacet[TimelinePresentationScope]):
    stateChanged = Signal()
    importCompleted = Signal(str)

    def __init__(self, scope: TimelinePresentationScope) -> None:
        super().__init__(scope)
        self._inspection: dict[str, Any] = {}
        self._running = False
        self._importing = False
        self._request_id = 0

    @Property(dict, notify=stateChanged)
    def inspectionData(self) -> dict[str, Any]:
        return dict(self._inspection)

    @Property(bool, notify=stateChanged)
    def running(self) -> bool:
        return self._running

    @Property(bool, notify=stateChanged)
    def importing(self) -> bool:
        return self._importing

    @Slot(str, str)
    @report_ui_errors
    def inspectTimeline(self, source_url: str, frame_rate_text: str) -> None:
        current = self._session.state.binding.current
        if current is None:
            raise RuntimeError("请先打开项目")
        path = self._local_path(source_url)
        frame_rate = self._frame_rate(path, frame_rate_text)
        self._request_id += 1
        request_id = self._request_id
        self._running = True
        self._inspection = {}
        self.stateChanged.emit()
        self._session.background.submit_project_callback(
            "interchange_timeline",
            ("inspect", request_id),
            lambda: self._session._api.execute_read_operation(
                "timeline.interchange.inspect",
                current.project_dir,
                {
                    "sequence_id": self._session.state.binding.active_sequence_id,
                    "timeline_path": str(path),
                    **({"frame_rate": frame_rate} if frame_rate is not None else {}),
                },
            ),
            on_result=lambda result: self._finish_inspection(request_id, result),
            on_error=lambda error: self._fail(request_id, error, importing=False),
        )

    @Slot(str, str, str, "QVariantMap")
    @report_ui_errors
    def importTimeline(
        self,
        source_url: str,
        name: str,
        frame_rate_text: str,
        media_mappings: dict[str, Any],
    ) -> None:
        self._session._require_writable()
        current = self._session.state.binding.require_current()
        path = self._local_path(source_url)
        frame_rate = self._frame_rate(path, frame_rate_text)
        sequence_id = self._session.state.binding.active_sequence_id
        mappings = {
            str(key): str(value)
            for key, value in dict(media_mappings).items()
            if str(key).strip() and str(value).strip()
        }
        self._request_id += 1
        request_id = self._request_id
        self._importing = True
        self.stateChanged.emit()
        self._session.background.submit_project_callback(
            "interchange_timeline",
            ("import", request_id),
            lambda: current.import_interchange_timeline(
                path,
                sequence_id=sequence_id,
                name=name.strip() or None,
                frame_rate=frame_rate,
                media_mappings=mappings,
            ),
            on_result=lambda result: self._finish_import(request_id, current.project_dir, result),
            on_error=lambda error: self._fail(request_id, error, importing=True),
        )

    def _finish_inspection(self, request_id: int, result: object | None) -> None:
        if request_id != self._request_id:
            return
        if not isinstance(result, dict):
            self._fail(request_id, RuntimeError("交换时间线检查返回了无效结果"), importing=False)
            return
        self._inspection = result
        self._running = False
        self.stateChanged.emit()

    def _finish_import(self, request_id: int, project_dir: Path, result: object | None) -> None:
        if request_id != self._request_id:
            return
        self._importing = False
        current = self._session.state.binding.current
        if current is None or current.project_dir != project_dir:
            self.stateChanged.emit()
            return
        if not isinstance(result, tuple) or len(result) != 5:
            self._fail(request_id, RuntimeError("交换时间线导入返回了无效结果"), importing=True)
            return
        sequence = result[1]
        sequence_id = str(getattr(sequence, "id", ""))
        if not sequence_id:
            self._fail(request_id, RuntimeError("交换时间线没有返回新序列"), importing=True)
            return
        self._session.state.binding.active_sequence_id = sequence_id
        self._session.state.binding.timeline = current.timeline(sequence_id)
        self._session.state.selection.clip_ids = []
        self._session.state.selection.compound_id = ""
        self._session.projectors.refresh_active_sequence(refresh_sequences=True)
        self._session.updates.commit(project=True, history=True)
        self.stateChanged.emit()
        self._session._set_status("交换时间线已导入：%1", getattr(sequence, "name", sequence_id))
        self.importCompleted.emit(sequence_id)

    def _fail(self, request_id: int, error: BaseException, *, importing: bool) -> None:
        if request_id != self._request_id:
            return
        self._running = False
        self._importing = False
        self._inspection = {**self._inspection, "error": str(error)}
        self.stateChanged.emit()

    @staticmethod
    def _local_path(source_url: str) -> Path:
        url = QUrl(source_url)
        value = url.toLocalFile() if url.isLocalFile() else source_url
        return Path(value).expanduser().resolve(strict=True)

    @staticmethod
    def _frame_rate(path: Path, value: str) -> float | None:
        text = value.strip()
        if path.suffix.casefold() != ".edl":
            return None
        if not text:
            return None
        frame_rate = float(text)
        if frame_rate <= 0:
            raise ValueError("EDL 帧率必须大于 0")
        return frame_rate
