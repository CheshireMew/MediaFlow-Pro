from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mediaflow.composition import EditorApplication

from .deferred_editor_services import (
    DeferredEditorServices,
    DeferredServiceAttribute,
    prepare_shutdown,
    service_status,
)
from .events import EventHub
from .runtime_sessions import ApplicationRuntimeOperations


class EditorServiceOperations:
    """Composition root for editor-service project, automation, and runtime operations."""

    registry = DeferredServiceAttribute("registry")
    automation = DeferredServiceAttribute("automation")
    desktop = DeferredServiceAttribute("desktop")

    def __init__(self, application: EditorApplication, events: EventHub):
        self._application = application
        self._events = events
        self._services = DeferredEditorServices(application, events)
        self.runtime = ApplicationRuntimeOperations(
            application,
            events,
            update_project_settings=self._services.update_project_settings,
        )

    def service_status(self) -> dict[str, Any]:
        return service_status(self._services, self.runtime)

    def prepare_shutdown(self, *, force: bool) -> dict[str, Any]:
        return prepare_shutdown(self._services, self.runtime, force=force)

    def close(self) -> None:
        self._services.close()
