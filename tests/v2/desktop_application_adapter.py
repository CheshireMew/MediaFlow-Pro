from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mediaflow.composition import EditorApplication
from mediaflow.domain.settings import DesktopSettings, ServiceSettings
from mediaflow.infrastructure.settings_repository import DesktopSettingsRepository


class DesktopPresentationProject:
    """Presentation-test double for the remote project surface only."""

    def __init__(self, project: Any):
        self._project = project
        self._subscription_serial = 0
        self._project_subscribers: dict[int, Callable[[Any], None]] = {}
        self._workspace_subscribers: dict[int, Callable[[Any], None]] = {}

    @property
    def actor_id(self) -> str:
        return "desktop-presentation-test"

    def subscribe_project_events(
        self,
        handler: Callable[[Any], None],
        *,
        include_snapshot: bool = False,
    ) -> int:
        del include_snapshot
        self._subscription_serial += 1
        self._project_subscribers[self._subscription_serial] = handler
        return self._subscription_serial

    def unsubscribe_project_events(self, token: int) -> None:
        self._project_subscribers.pop(token, None)

    def subscribe_workspace_events(self, handler: Callable[[Any], None]) -> int:
        self._subscription_serial += 1
        self._workspace_subscribers[self._subscription_serial] = handler
        return self._subscription_serial

    def unsubscribe_workspace_events(self, token: int) -> None:
        self._workspace_subscribers.pop(token, None)

    def begin_draft(self, path: str) -> None:
        del path

    def end_draft(self, path: str) -> None:
        del path

    def resolve_pending_conflict(self, resolution: str) -> None:
        raise RuntimeError(
            f"Presentation test projects do not create collaboration conflicts: {resolution}"
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._project, name)


class DesktopPresentationApplication:
    """Test-only desktop boundary for focused presentation tests.

    Real desktop/service integration tests use ``DesktopEditorApplication``.
    This adapter keeps QML unit setup small without restoring an in-process
    composition path in production desktop code.
    """

    def __init__(self, application: EditorApplication):
        self._application = application
        self._desktop_settings_repository = DesktopSettingsRepository()
        self._desktop_settings = self._desktop_settings_repository.load()

    @property
    def service_settings(self) -> ServiceSettings:
        return self._application.service_settings

    @property
    def desktop_settings(self) -> DesktopSettings:
        return self._desktop_settings

    def replace_service_settings(self, settings: ServiceSettings) -> None:
        self._application.replace_service_settings(settings)

    def replace_desktop_settings(self, settings: DesktopSettings) -> None:
        self._desktop_settings_repository.save(settings)
        self._desktop_settings = settings.model_copy(deep=True)

    @staticmethod
    def close_client_transport() -> None:
        return

    @staticmethod
    def adapt_project(project: Any) -> DesktopPresentationProject:
        if isinstance(project, DesktopPresentationProject):
            return project
        return DesktopPresentationProject(project)

    def create_project(self, *args: Any, **kwargs: Any) -> DesktopPresentationProject:
        return self.adapt_project(self._application.create_project(*args, **kwargs))

    def open_project(self, *args: Any, **kwargs: Any) -> DesktopPresentationProject:
        return self.adapt_project(self._application.open_project(*args, **kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._application, name)
