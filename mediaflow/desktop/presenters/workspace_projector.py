from __future__ import annotations

from PySide6.QtCore import QUrl

from .base import Projector


class WorkspaceProjector(Projector):
    def refresh_runtime_tool_status(self, *, preserve_cuda: bool = True) -> None:
        cuda = {
            key: self._session.runtime_state.status.get(key, "")
            for key in ("cudaStatus", "cudaSummary", "gpuName", "driverVersion")
        }
        self._session.runtime_state.status = {
            **self._session._api.runtime_tool_status(),
            **(cuda if preserve_cuda else {}),
            "busy": False,
            "progressMode": "indeterminate",
            "progressValue": 0.0,
            "message": "",
            "operation": "",
        }
        self._session.events.runtimeToolsChanged.emit()

    def refresh_recent_projects(self) -> None:
        self._session.requests.recent_id += 1
        request_id = self._session.requests.recent_id
        paths = list(self._session.desktop_settings.ui.recent_project_paths)
        self._session.background.submit(
            "recent_projects",
            request_id,
            lambda: self._session._api.recent_projects(paths),
        )

    def apply_recent_projects(self, snapshot) -> None:
        self._session.presentation.home_summary = snapshot.totals
        items = []
        for item in snapshot.items:
            cover_path = item.get("coverPath", "")
            row = {key: value for key, value in item.items() if key != "coverPath"}
            row["coverUrl"] = QUrl.fromLocalFile(cover_path).toString() if cover_path else ""
            items.append(row)
        self._session.models.recent_projects.set_items(items)
        self._session.events.projectStateChanged.emit()

    def discover_encoder_policies(self) -> None:
        self._session.requests.encoder_id += 1
        request_id = self._session.requests.encoder_id
        self._session.background.submit(
            "encoder_policies",
            request_id,
            self._session._api.discover_encoder_policy_options,
        )

    def refresh_settings_models(self) -> None:
        active_id = self._session.service_settings.active_llm_provider_id
        self._session.models.llm_providers.set_items(
            [
                {
                    "providerId": provider.id,
                    "name": provider.name,
                    "baseUrl": provider.base_url,
                    "apiKey": provider.api_key,
                    "model": provider.model,
                    "enabled": provider.enabled,
                    "active": provider.id == active_id,
                }
                for provider in self._session.service_settings.llm_providers
            ]
        )
        provider_ids = {
            item.id for item in self._session.service_settings.llm_providers
        }
        if self._session.selection.llm_provider_id not in provider_ids:
            self._session.selection.llm_provider_id = ""
        self._session.models.glossary.set_items(
            [
                {
                    "termId": term.id,
                    "source": term.source,
                    "target": term.target,
                    "note": term.note,
                    "category": term.category,
                }
                for term in self._session.service_settings.translation.glossary_terms
            ]
        )
        term_ids = {
            item.id
            for item in self._session.service_settings.translation.glossary_terms
        }
        if self._session.selection.glossary_term_id not in term_ids:
            self._session.selection.glossary_term_id = ""
