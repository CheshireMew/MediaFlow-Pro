from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .events import EventHub


class DeferredService:
    """Materialize one focused service on its first public operation."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._instance: Any | None = None
        self._lock = threading.RLock()

    def get(self) -> Any:
        instance = self._instance
        if instance is not None:
            return instance
        with self._lock:
            instance = self._instance
            if instance is None:
                instance = self._factory()
                self._instance = instance
        return instance

    def peek(self) -> Any | None:
        return self._instance

    def __getattr__(self, name: str) -> Any:
        return getattr(self.get(), name)


class DeferredServiceAttribute:
    """Expose a deferred concrete service without adding composition-root methods."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __get__(self, instance, owner=None) -> Any:
        if instance is None:
            return self
        deferred = getattr(instance._services, self.name)
        return deferred.get()


class DeferredEditorServices:
    def __init__(self, application, events: EventHub) -> None:
        def create_registry():
            from .session_registry import ProjectSessionRegistry

            return ProjectSessionRegistry(application, events)

        self.registry = DeferredService(create_registry)

        def create_automation():
            from .automation_sessions import ProjectAutomationOperations

            return ProjectAutomationOperations(self.registry.get())

        self.automation = DeferredService(create_automation)

        def create_desktop():
            from .desktop_sessions import DesktopProjectOperations

            return DesktopProjectOperations(self.registry.get())

        self.desktop = DeferredService(create_desktop)

    def update_project_settings(self) -> None:
        registry = self.registry.peek()
        if registry is not None:
            registry.update_project_settings()

    def registry_status(self) -> dict[str, Any]:
        registry = self.registry.peek()
        if registry is None:
            return {
                "project_session_count": 0,
                "desktop_client_count": 0,
                "active_task_count": 0,
                "active_tasks": [],
            }
        return registry.service_status()

    def cancel_all_tasks(self) -> int:
        registry = self.registry.peek()
        return 0 if registry is None else registry.cancel_all_tasks()

    def close(self) -> None:
        registry = self.registry.peek()
        if registry is not None:
            registry.close()


def service_status(services: DeferredEditorServices, runtime) -> dict[str, Any]:
    return {
        **services.registry_status(),
        "active_runtime_operation": runtime.active_operation or None,
    }


def prepare_shutdown(
    services: DeferredEditorServices,
    runtime,
    *,
    force: bool,
) -> dict[str, Any]:
    status = service_status(services, runtime)
    if (status["active_task_count"] or status["active_runtime_operation"]) and not force:
        task_ids = ", ".join(item["task_id"] for item in status["active_tasks"])
        active_description = task_ids or str(status["active_runtime_operation"])
        raise RuntimeError(
            f"Editor Service has active work; retry with force=true to cancel it: {active_description}"
        )
    runtime_cancelled = False
    cancelled = 0
    if force:
        runtime_cancelled = runtime.cancel_runtime_tool()["cancel_requested"]
        cancelled = services.cancel_all_tasks()
    return {
        "stopping": True,
        "force": force,
        "cancelled_task_count": cancelled,
        "cancelled_runtime_operation": runtime_cancelled,
    }
