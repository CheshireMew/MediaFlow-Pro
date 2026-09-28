from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from pydantic import JsonValue

from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.settings import ServiceSettings

from .application_commands import application_command
from .codec import decode_transport, encode_transport
from .events import EventHub, ServiceEvent


class ApplicationRuntimeOperations:
    def __init__(
        self,
        application,
        events: EventHub,
        *,
        update_project_settings: Callable[[], None],
    ):
        self.application = application
        self.events = events
        self._update_project_settings = update_project_settings
        self._operation_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._cancel = threading.Event()
        self._operation = ""
        self._revision = 0

    @property
    def active_operation(self) -> str:
        with self._state_lock:
            return self._operation

    def application_settings(self) -> Any:
        return encode_transport(self.application.service_settings)

    def desktop_runtime_descriptor(self) -> Any:
        return self.application.runtime.desktop_descriptor().model_dump(mode="json")

    def desktop_bootstrap(self) -> dict[str, Any]:
        bootstrap_status = getattr(self.application, "bootstrap_runtime_tool_status", None)
        runtime_status = (
            bootstrap_status() if callable(bootstrap_status) else self.application.runtime_tool_status()
        )
        return {
            "runtime_descriptor": self.desktop_runtime_descriptor(),
            "settings": self.application_settings(),
            "runtime_tool_status": encode_transport(runtime_status),
            "default_media_directory": self.application.default_media_directory,
        }

    def replace_application_settings(self, value: Any) -> Any:
        settings = decode_transport(value)
        if not isinstance(settings, ServiceSettings):
            raise ValueError("settings must be ServiceSettings")
        self.application.replace_service_settings(settings)
        self._update_project_settings()
        return encode_transport(self.application.service_settings)

    def execute_application_command(
        self,
        command: str,
        arguments_value: Any,
    ) -> Any:
        definition = application_command(command)
        return encode_transport(definition.invoke(self, decode_transport(arguments_value)))

    def _run_runtime_tool(self, operation: str, arguments: dict[str, JsonValue]) -> object:
        if not self._operation_lock.acquire(blocking=False):
            with self._state_lock:
                active = self._operation
            raise RuntimeError(f"Runtime tool operation is already active: {active}")
        self._cancel.clear()
        with self._state_lock:
            self._operation = operation
        self._publish_runtime_event(
            operation,
            "running",
            progress=OperationProgress.indeterminate("runtime_service_operation").model_dump(
                mode="json",
                exclude_computed_fields=True,
            ),
        )

        def check_cancelled() -> None:
            if self._cancel.is_set():
                raise RuntimeError("Runtime tool operation was cancelled")

        def report(progress: Any) -> None:
            payload = (
                progress.model_dump(mode="json", exclude_computed_fields=True)
                if hasattr(progress, "model_dump")
                else progress
            )
            self._publish_runtime_event(operation, "running", progress=payload)

        try:
            result = self.application.run_runtime_tool(
                operation,
                arguments=arguments,
                progress=report,
                check_cancelled=check_cancelled,
            )
            check_cancelled()
            result = application_command("run_runtime_tool").validate_result(result)
            self._publish_runtime_event(operation, "completed", result=result)
            return result
        except BaseException as error:
            state = "cancelled" if self._cancel.is_set() else "failed"
            self._publish_runtime_event(operation, state, error=str(error))
            raise
        finally:
            with self._state_lock:
                self._operation = ""
            self._operation_lock.release()

    def cancel_runtime_tool(self) -> dict[str, Any]:
        with self._state_lock:
            operation = self._operation
        if not operation:
            return {"cancel_requested": False, "operation": ""}
        self._cancel.set()
        self._publish_runtime_event(operation, "cancel_requested")
        return {"cancel_requested": True, "operation": operation}

    def _publish_runtime_event(
        self,
        operation: str,
        state: str,
        *,
        progress: Any = None,
        result: Any = None,
        error: str = "",
    ) -> None:
        with self._state_lock:
            self._revision += 1
            revision = self._revision
        payload = {
            "runtime_revision": revision,
            "operation": operation,
            "state": state,
        }
        if progress is not None:
            payload["progress"] = progress
        if result is not None:
            payload["result"] = result
        if error:
            payload["error"] = error
        self.events.publish_from_worker(ServiceEvent("runtime.changed", payload))
