from __future__ import annotations

from typing import Any

from mediaflow.composition import EditorApplication
from mediaflow.domain.settings import DesktopSettings, ServiceSettings
from mediaflow.infrastructure.settings_repository import DesktopSettingsRepository


class LocalDesktopEditorApplication:
    """Desktop composition root for in-process development and test sessions."""

    def __init__(self, service: EditorApplication):
        self._service = service
        self._desktop_settings_repository = DesktopSettingsRepository()
        self._desktop_settings = self._desktop_settings_repository.load()

    @property
    def service_settings(self) -> ServiceSettings:
        return self._service.service_settings

    @property
    def desktop_settings(self) -> DesktopSettings:
        return self._desktop_settings

    def replace_service_settings(self, settings: ServiceSettings) -> None:
        self._service.replace_service_settings(settings)

    def replace_desktop_settings(self, settings: DesktopSettings) -> None:
        self._desktop_settings_repository.save(settings)
        self._desktop_settings = settings.model_copy(deep=True)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._service, name)
