from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal, Slot

from mediaflow.domain.progress import OperationProgress

from .base import SessionCoordinator

logger = logging.getLogger(__name__)


class _RuntimeToolBridge(QObject):
    eventReceived = Signal(object)


class RuntimeToolOperations(SessionCoordinator):
    def __init__(self, session):
        super().__init__(session)
        self._bridge = _RuntimeToolBridge(session)
        self._bridge.eventReceived.connect(self._on_event)

    def start(self, operation: str) -> None:
        if self._session.runtime_state.thread and self._session.runtime_state.thread.is_alive():
            self._session.events.errorOccurred.emit("已有运行时工具操作正在执行")
            return
        self._session.runtime_state.cancel.clear()
        self._session.runtime_state.status = {
            **self._session.runtime_state.status,
            "busy": True,
            "progressMode": "indeterminate",
            "progressValue": 0.0,
            "message": "starting",
            "operation": operation,
        }
        self._session.events.runtimeToolsChanged.emit()

        def check_cancelled() -> None:
            if self._session.runtime_state.cancel.is_set():
                raise RuntimeError("运行时工具操作已取消")

        def report(progress: OperationProgress) -> None:
            self._bridge.eventReceived.emit(
                {
                    "type": "progress",
                    "progress": progress.model_dump(
                        mode="json",
                        exclude_computed_fields=True,
                    ),
                    "operation": operation,
                }
            )

        def run() -> None:
            try:
                result = self._session._api.run_runtime_tool(
                    operation,
                    progress=report,
                    check_cancelled=check_cancelled,
                )
                self._bridge.eventReceived.emit(
                    {"type": "completed", "operation": operation, "result": result}
                )
            except Exception as error:
                self._bridge.eventReceived.emit(
                    {
                        "type": "cancelled" if self._session.runtime_state.cancel.is_set() else "failed",
                        "operation": operation,
                        "error": str(error),
                    }
                )

        self._session.runtime_state.thread = threading.Thread(
            target=run,
            name=f"mediaflow-runtime-{operation}",
            daemon=True,
        )
        self._session.runtime_state.thread.start()

    @Slot(object)
    def _on_event(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "progress":
            progress = OperationProgress.model_validate(event.get("progress"))
            self._session.runtime_state.status = {
                **self._session.runtime_state.status,
                "progressMode": progress.mode,
                "progressValue": progress.percent or 0.0,
                "message": progress.message_code,
            }
            self._session.events.runtimeToolsChanged.emit()
            return
        operation = str(event.get("operation") or "")
        if event_type == "completed" and operation == "install_asr_cli":
            candidate = self._session.settings.model_copy(deep=True)
            candidate.asr.cli_path = str(event.get("result") or "") or None
            self._session._commit_settings(candidate)
        if event_type == "completed" and operation == "inspect":
            self._session.runtime_state.status = {
                **self._session.runtime_state.status,
                **dict(event.get("result") or {}),
            }
        self._session.projectors.workspace.refresh_runtime_tool_status(preserve_cuda=True)
        if event_type == "failed":
            self._session.events.errorOccurred.emit(str(event.get("error") or "运行时工具操作失败"))
        elif event_type == "cancelled":
            self._session._set_status("运行时工具操作已取消")
        else:
            self._session._set_status("运行时工具操作已完成")
