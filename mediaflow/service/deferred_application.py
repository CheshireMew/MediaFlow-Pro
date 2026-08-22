from __future__ import annotations

import threading
from typing import Any

from mediaflow.domain.settings import ServiceSettings
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.settings_repository import ServiceSettingsRepository


class DeferredEditorApplication:
    """Serve the desktop bootstrap before loading the full editing runtime."""

    def __init__(self) -> None:
        self.runtime = RuntimeContext.discover()
        self._settings_repository = ServiceSettingsRepository()
        self._service_settings = self._settings_repository.load()
        self._settings_repository.prepare_storage(self._service_settings)
        self._application: Any | None = None
        self._materialize_lock = threading.RLock()

    @property
    def service_settings(self) -> ServiceSettings:
        application = self._application
        return application.service_settings if application is not None else self._service_settings

    @property
    def materialized(self) -> bool:
        return self._application is not None

    def runtime_tool_status(self) -> dict:
        from mediaflow.infrastructure.runtime_tools import RuntimeToolService

        return RuntimeToolService(self.service_settings, self.runtime.paths).status()

    @staticmethod
    def bootstrap_runtime_tool_status() -> dict[str, Any]:
        return {
            "components": {},
            "ytDlpVersion": "",
            "speakerClustering": {
                "ready": False,
                "version": "",
                "python": "",
                "model": "",
                "reason": "正在读取运行环境",
            },
            "cudaStatus": "unchecked",
            "cudaSummary": "尚未检测 CUDA",
            "gpuName": "",
            "driverVersion": "",
        }

    def save_service_settings(self) -> None:
        application = self._application
        if application is not None:
            application.save_service_settings()
            return
        self._settings_repository.save(self._service_settings)

    def replace_service_settings(self, settings: ServiceSettings) -> None:
        application = self._application
        if application is not None:
            application.replace_service_settings(settings)
            self._service_settings = application.service_settings
            return
        self._settings_repository.save(settings)
        self._service_settings = self._settings_repository.normalize(settings)
        self._settings_repository.prepare_storage(self._service_settings)

    def _materialize(self):
        application = self._application
        if application is not None:
            return application
        with self._materialize_lock:
            application = self._application
            if application is None:
                from mediaflow.composition import EditorApplication

                application = EditorApplication(runtime=self.runtime)
                self._application = application
                self._service_settings = application.service_settings
        return application

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._materialize(), name)
