from __future__ import annotations

from pathlib import Path

from mediaflow.desktop.component_contexts import SettingsPersistenceContext
from mediaflow.domain.settings import DesktopSettings, ServiceSettings

from .base import SessionCoordinator


class SettingsPersistence(SessionCoordinator[SettingsPersistenceContext]):
    def commit(
        self,
        candidate: ServiceSettings | DesktopSettings,
        status_source: str = "",
        *status_arguments: object,
    ) -> None:
        if isinstance(candidate, ServiceSettings):
            self._context.replace_service_settings(candidate)
            self._context.state.service_settings = self._context.read_service_settings()
            if self._context.state.binding.current:
                self._context.state.binding.require_current().update_settings(
                    self._context.state.service_settings
                )
                self._context.updates.commit(workflow=True)
        else:
            self._context.replace_desktop_settings(candidate)
            self._context.state.desktop_settings = self._context.read_desktop_settings()
        self._context.refresh_settings_models()
        self._context.updates.commit(settings=True)
        self._context.updates.commit(selection=True)
        if status_source:
            self._context.set_status(status_source, *status_arguments)

    def commit_pair(
        self,
        service_candidate: ServiceSettings,
        desktop_candidate: DesktopSettings,
        status_source: str = "",
        *status_arguments: object,
    ) -> None:
        """Persist one validated settings form and notify presentation once."""

        self._context.replace_service_settings(service_candidate)
        self._context.state.service_settings = self._context.read_service_settings()
        if self._context.state.binding.current:
            self._context.state.binding.require_current().update_settings(
                self._context.state.service_settings
            )
        self._context.replace_desktop_settings(desktop_candidate)
        self._context.state.desktop_settings = self._context.read_desktop_settings()
        self._context.refresh_settings_models()
        self._context.updates.commit(
            settings=True,
            selection=True,
            workflow=self._context.state.binding.current is not None,
        )
        if status_source:
            self._context.set_status(status_source, *status_arguments)

    def remember_default_project_directory(
        self,
        directory: Path,
        status_source: str = "",
        *status_arguments: object,
    ) -> None:
        selected = str(directory.expanduser().resolve())
        if self._context.state.service_settings.default_project_directory == selected:
            return
        candidate = self._context.state.service_settings.model_copy(deep=True)
        candidate.default_project_directory = selected
        self.commit(candidate, status_source, *status_arguments)
