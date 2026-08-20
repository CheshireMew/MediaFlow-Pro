from __future__ import annotations

from typing import Any

from mediaflow.composition import EditorApplication

from .automation_sessions import ProjectAutomationOperations
from .desktop_sessions import DesktopProjectOperations
from .events import EventHub
from .runtime_sessions import ApplicationRuntimeOperations
from .session_registry import ProjectSessionRegistry


class EditorServiceOperations:
    """Composition root for editor-service project, automation, and runtime operations."""

    def __init__(self, application: EditorApplication, events: EventHub):
        self.registry = ProjectSessionRegistry(application, events)
        self.automation = ProjectAutomationOperations(self.registry)
        self.desktop = DesktopProjectOperations(self.registry)
        self.runtime = ApplicationRuntimeOperations(self.registry)

    def service_status(self) -> dict[str, Any]:
        return {
            **self.registry.service_status(),
            "active_runtime_operation": self.runtime.active_operation or None,
        }

    def prepare_shutdown(self, *, force: bool) -> dict[str, Any]:
        status = self.service_status()
        if (status["active_task_count"] or status["active_runtime_operation"]) and not force:
            task_ids = ", ".join(item["task_id"] for item in status["active_tasks"])
            active_description = task_ids or str(status["active_runtime_operation"])
            raise RuntimeError(
                f"Editor Service has active work; retry with force=true to cancel it: {active_description}"
            )
        runtime_cancelled = False
        cancelled = 0
        if force:
            runtime_cancelled = self.runtime.cancel_runtime_tool()["cancel_requested"]
            cancelled = self.registry.cancel_all_tasks()
        return {
            "stopping": True,
            "force": force,
            "cancelled_task_count": cancelled,
            "cancelled_runtime_operation": runtime_cancelled,
        }

    def close(self) -> None:
        self.registry.close()
