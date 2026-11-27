from __future__ import annotations

from PySide6.QtCore import QUrl

from mediaflow.application.presentation_models import RecentProjectSnapshot
from mediaflow.desktop.component_contexts import WorkspaceProjectorContext

from .base import Projector


class WorkspaceProjector(Projector[WorkspaceProjectorContext]):
    def refresh_runtime_tool_status(self, *, preserve_cuda: bool = True) -> None:
        self._context.state.requests.runtime_status_id += 1
        request_id = self._context.state.requests.runtime_status_id
        previous = self._context.state.requests.runtime_status_future
        if previous is not None and not previous.done():
            previous.cancel()
        self._context.state.requests.runtime_status_future = self._context.submit_background(
            "runtime_status",
            (request_id, preserve_cuda),
            self._context.runtime_tool_status,
        )

    def refresh_installed_asr_models(self) -> None:
        self._context.state.requests.installed_asr_models_id += 1
        request_id = self._context.state.requests.installed_asr_models_id
        previous = self._context.state.requests.installed_asr_models_future
        if previous is not None and not previous.done():
            previous.cancel()
        self._context.state.requests.installed_asr_models_future = self._context.submit_background(
            "installed_asr_models",
            request_id,
            self._context.installed_asr_models,
        )

    def apply_runtime_tool_status(
        self,
        status: dict,
        *,
        preserve_cuda: bool = True,
    ) -> None:
        cuda = {
            key: self._context.state.runtime_state.status.get(key, "")
            for key in ("cudaStatus", "cudaSummary", "gpuName", "driverVersion")
        }
        self._context.state.runtime_state.status = {
            **status,
            **(cuda if preserve_cuda else {}),
            "busy": False,
            "progressMode": "indeterminate",
            "progressValue": 0.0,
            "message": "",
            "operation": "",
        }
        self._context.updates.commit(runtime_tools=True)

    def refresh_recent_projects(self) -> None:
        self._context.state.requests.recent_id += 1
        request_id = self._context.state.requests.recent_id
        paths = list(self._context.state.desktop_settings.ui.recent_project_paths)
        self._context.submit_background(
            "recent_projects",
            request_id,
            lambda: self._context.recent_projects(paths),
        )

    def apply_recent_projects(self, snapshot: RecentProjectSnapshot) -> None:
        self._context.state.presentation.home_summary = snapshot.totals
        items = []
        for item in snapshot.items:
            cover_path = item.get("coverPath", "")
            row = {key: value for key, value in item.items() if key != "coverPath"}
            row["coverUrl"] = QUrl.fromLocalFile(cover_path).toString() if cover_path else ""
            items.append(row)
        self._context.models.recent_projects.set_items(items)
        self._context.updates.commit(home_summary=True)

    def discover_encoder_policies(self) -> None:
        self._context.state.requests.encoder_id += 1
        request_id = self._context.state.requests.encoder_id
        self._context.submit_background(
            "encoder_policies",
            request_id,
            self._context.discover_encoder_policy_options,
        )

    def refresh_settings_models(self) -> None:
        active_id = self._context.state.service_settings.active_llm_provider_id
        self._context.models.llm_providers.set_items(
            [
                {
                    "providerId": provider.id,
                    "name": provider.name,
                    "baseUrl": provider.base_url,
                    "apiKey": provider.api_key,
                    "model": provider.model,
                    "providerEnabled": provider.enabled,
                    "active": provider.id == active_id,
                }
                for provider in self._context.state.service_settings.llm_providers
            ]
        )
        provider_ids = {item.id for item in self._context.state.service_settings.llm_providers}
        if self._context.state.selection.llm_provider_id not in provider_ids:
            self._context.state.selection.llm_provider_id = ""
        self._context.models.glossary.set_items(
            [
                {
                    "termId": term.id,
                    "source": term.source,
                    "target": term.target,
                    "note": term.note,
                    "category": term.category,
                }
                for term in self._context.state.service_settings.translation.glossary_terms
            ]
        )
        term_ids = {item.id for item in self._context.state.service_settings.translation.glossary_terms}
        if self._context.state.selection.glossary_term_id not in term_ids:
            self._context.state.selection.glossary_term_id = ""
