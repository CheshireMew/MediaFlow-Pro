from __future__ import annotations

from pathlib import Path

from mediaflow.domain.settings import DesktopSettings, ServiceSettings

from .base import SessionCoordinator


class SettingsPersistence(SessionCoordinator):
    def commit(
        self,
        candidate: ServiceSettings | DesktopSettings,
        status_source: str = "",
        *status_arguments: object,
    ) -> None:
        if isinstance(candidate, ServiceSettings):
            self._session._api.replace_service_settings(candidate)
            self._session.service_settings = self._session._api.service_settings
            if self._session.binding.current:
                self._session.binding.current.update_settings(self._session.service_settings)
                self._session.events.workflowChanged.emit()
        else:
            self._session._api.replace_desktop_settings(candidate)
            self._session.desktop_settings = self._session._api.desktop_settings
        self._session.projectors.workspace.refresh_settings_models()
        self._session.events.settingsChanged.emit()
        self._session.events.selectionChanged.emit()
        if status_source:
            self._session._set_status(status_source, *status_arguments)

    def remember_default_project_directory(
        self,
        directory: Path,
        status_source: str = "",
        *status_arguments: object,
    ) -> None:
        selected = str(directory.expanduser().resolve())
        if self._session.service_settings.default_project_directory == selected:
            return
        candidate = self._session.service_settings.model_copy(deep=True)
        candidate.default_project_directory = selected
        self.commit(candidate, status_source, *status_arguments)
