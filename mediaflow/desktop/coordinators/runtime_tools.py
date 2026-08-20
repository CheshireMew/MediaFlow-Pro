from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal, Slot

from mediaflow.desktop.presentation_tasks import task_message_label
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.runtime_capabilities import RuntimeComponentInstallResult

from .base import SessionCoordinator

logger = logging.getLogger(__name__)


class _RuntimeToolBridge(QObject):
    eventReceived = Signal(object)


class RuntimeToolOperations(SessionCoordinator):
    def __init__(self, session):
        super().__init__(session)
        self._bridge = _RuntimeToolBridge(session)
        self._bridge.eventReceived.connect(self._on_event)

    def start(self, operation: str, arguments: dict | None = None) -> None:
        if self._session.state.runtime_state.thread and self._session.state.runtime_state.thread.is_alive():
            self._session.updates.report_error("已有运行时工具操作正在执行")
            return
        self._session.state.runtime_state.cancel.clear()
        self._session.state.runtime_state.status = {
            **self._session.state.runtime_state.status,
            "busy": True,
            "progressMode": "indeterminate",
            "progressValue": 0.0,
            "message": "starting",
            "operation": operation,
        }
        self._session.updates.commit(runtime_tools=True)

        def check_cancelled() -> None:
            if self._session.state.runtime_state.cancel.is_set():
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
                    arguments=arguments,
                    progress=report,
                    check_cancelled=check_cancelled,
                )
                self._bridge.eventReceived.emit(
                    {"type": "completed", "operation": operation, "result": result}
                )
            except Exception as error:
                self._bridge.eventReceived.emit(
                    {
                        "type": "cancelled"
                        if self._session.state.runtime_state.cancel.is_set()
                        else "failed",
                        "operation": operation,
                        "error": str(error),
                    }
                )

        self._session.state.runtime_state.thread = threading.Thread(
            target=run,
            name=f"mediaflow-runtime-{operation}",
            daemon=True,
        )
        self._session.state.runtime_state.thread.start()

    @Slot(object)
    def _on_event(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "progress":
            progress = OperationProgress.model_validate(event.get("progress"))
            self._session.state.runtime_state.status = {
                **self._session.state.runtime_state.status,
                "progressMode": progress.mode,
                "progressValue": progress.percent or 0.0,
                "message": task_message_label(progress.message_code),
            }
            self._session.updates.commit(runtime_tools=True)
            return
        operation = str(event.get("operation") or "")
        if event_type == "completed" and operation == "install_components":
            installed = dict(event.get("result") or {})
            candidate = self._session.state.service_settings.model_copy(deep=True)
            if xxl_result := installed.get("faster-whisper-xxl"):
                installation = RuntimeComponentInstallResult.model_validate(xxl_result)
                candidate.asr.cli_path = installation.entrypoint
            if gpt_result := installed.get("gpt-sovits-v2pro"):
                installation = RuntimeComponentInstallResult.model_validate(gpt_result)
                candidate.speech_synthesis.gpt_sovits_root = installation.root
            self._session.settings_persistence.commit(candidate)
        if event_type == "completed" and operation == "install_speaker_clustering":
            installed = dict(event.get("result") or {})
            candidate = self._session.state.service_settings.model_copy(deep=True)
            candidate.speaker_diarization.backend = "transcript_clustering"
            candidate.speaker_diarization.clustering_python_executable = str(installed["python"])
            candidate.speaker_diarization.embedding_model_path = str(installed["model"])
            self._session.settings_persistence.commit(candidate)
        if event_type == "completed" and operation == "inspect":
            self._session.state.runtime_state.status = {
                **self._session.state.runtime_state.status,
                **dict(event.get("result") or {}),
            }
        self._session.projectors.workspace.refresh_runtime_tool_status(preserve_cuda=True)
        if event_type == "failed":
            self._session.updates.report_error(str(event.get("error") or "运行时工具操作失败"))
        elif event_type == "cancelled":
            self._session._set_status("运行时工具操作已取消")
        else:
            self._session._set_status("运行时工具操作已完成")
