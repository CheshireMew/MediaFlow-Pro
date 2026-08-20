from __future__ import annotations

import threading
from typing import Any

from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.settings import ServiceSettings

from .codec import decode_transport, encode_transport
from .events import ServiceEvent
from .session_registry import ProjectSessionRegistry


class ApplicationRuntimeOperations:
    def __init__(self, registry: ProjectSessionRegistry):
        self.registry = registry
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
        return encode_transport(self.registry.application.service_settings)

    def desktop_runtime_descriptor(self) -> Any:
        return self.registry.application.runtime.desktop_descriptor().model_dump(mode="json")

    def replace_application_settings(self, value: Any) -> Any:
        settings = decode_transport(value)
        if not isinstance(settings, ServiceSettings):
            raise ValueError("settings must be ServiceSettings")
        self.registry.application.replace_service_settings(settings)
        self.registry.update_project_settings()
        return encode_transport(self.registry.application.service_settings)

    def cookie_command(self, command: str, args_value: Any) -> Any:
        if command not in {"status", "save", "clear"}:
            raise ValueError(f"Unknown managed-cookie command: {command}")
        args = decode_transport(args_value)
        if not isinstance(args, list):
            raise ValueError("Managed-cookie args must decode to an array")
        return encode_transport(getattr(self.registry.application.cookies, command)(*args))

    def execute_application_command(
        self,
        command: str,
        args_value: Any,
        kwargs_value: Any,
    ) -> Any:
        allowed = {
            "analyze_download_url",
            "asset_thumbnail_paths",
            "cancel_timeline_filmstrip_requests",
            "timeline_filmstrip_paths",
            "default_media_directory",
            "discover_encoder_policy_options",
            "installed_asr_models",
            "recent_projects",
            "cancel_runtime_tool",
            "runtime_tool_status",
            "run_runtime_tool",
            "test_llm_provider",
            "write_asset_preview_snapshot",
            "write_preview_snapshot",
        }
        if command not in allowed:
            raise ValueError(f"Unknown desktop application command: {command}")
        args = decode_transport(args_value)
        kwargs = decode_transport(kwargs_value)
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise ValueError("Application command args and kwargs must decode correctly")
        if command == "default_media_directory":
            if args or kwargs:
                raise ValueError("default_media_directory does not accept arguments")
            return encode_transport(self.registry.application.default_media_directory)
        if command == "cancel_runtime_tool":
            if args or kwargs:
                raise ValueError("cancel_runtime_tool does not accept arguments")
            return encode_transport(self.cancel_runtime_tool())
        if command == "run_runtime_tool":
            return encode_transport(self._run_runtime_tool(args, kwargs))
        return encode_transport(getattr(self.registry.application, command)(*args, **kwargs))

    def _run_runtime_tool(self, args: list[Any], kwargs: dict[str, Any]) -> object:
        if len(args) != 1 or not isinstance(args[0], str) or not args[0].strip():
            raise ValueError("run_runtime_tool requires one operation argument")
        if set(kwargs) - {"arguments"}:
            raise ValueError("run_runtime_tool only accepts the arguments keyword")
        arguments = kwargs.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("run_runtime_tool arguments must be an object")
        operation = args[0].strip()
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
            result = self.registry.application.run_runtime_tool(
                operation,
                arguments=arguments,
                progress=report,
                check_cancelled=check_cancelled,
            )
            check_cancelled()
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
        self.registry.events.publish_from_worker(ServiceEvent("runtime.changed", payload))
