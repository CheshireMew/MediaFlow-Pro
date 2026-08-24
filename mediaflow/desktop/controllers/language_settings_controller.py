from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from mediaflow.desktop.llm_provider_catalog import llm_provider_presets
from mediaflow.domain.settings import GlossaryTermSettings, LlmProviderSettings

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import LanguageSettingsControllerScope


class LanguageSettingsController(ControllerFacet[LanguageSettingsControllerScope]):
    selectionChanged = Signal()
    settingsChanged = Signal()
    errorOccurred = Signal(str)

    @Property(QObject, constant=True)
    def glossaryTermsModel(self) -> QObject:
        return self._session.models.glossary

    @Property(QObject, constant=True)
    def llmProvidersModel(self) -> QObject:
        return self._session.models.llm_providers

    @Property(list, constant=True)
    def llmProviderPresets(self) -> list[dict]:
        return llm_provider_presets()

    @Property(str, notify=selectionChanged)
    def selectedGlossaryTermId(self) -> str:
        return self._session.state.selection.glossary_term_id

    @Property(dict, notify=selectionChanged)
    def selectedGlossaryTermData(self) -> dict:
        row = self._session.models.glossary.findRow("termId", self._session.state.selection.glossary_term_id)
        return self._session.models.glossary.get(row)

    @Property(str, notify=selectionChanged)
    def selectedLlmProviderId(self) -> str:
        return self._session.state.selection.llm_provider_id

    @Property(dict, notify=selectionChanged)
    def selectedLlmProviderData(self) -> dict:
        row = self._session.models.llm_providers.findRow(
            "providerId", self._session.state.selection.llm_provider_id
        )
        return self._session.models.llm_providers.get(row)

    @Slot(str)
    def selectLlmProvider(self, provider_id: str) -> None:
        self._session.state.selection.llm_provider_id = provider_id
        self._session.updates.commit(selection=True)

    @Slot(str, str, str, str, str, bool)
    @report_ui_errors
    def saveLlmProvider(
        self,
        provider_id: str,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        enabled: bool,
    ) -> None:
        normalized_name = name.strip() or "默认 LLM"
        normalized_url = base_url.strip()
        normalized_model = model.strip()
        if not normalized_url or not normalized_model:
            raise ValueError("LLM Base URL 和模型名称需要同时填写")
        providers = list(self._session.state.service_settings.llm_providers)
        if provider_id:
            try:
                index = next(index for index, item in enumerate(providers) if item.id == provider_id)
            except StopIteration as error:
                raise KeyError(provider_id) from error
            provider = providers[index].model_copy(
                update={
                    "name": normalized_name,
                    "base_url": normalized_url,
                    "api_key": api_key,
                    "model": normalized_model,
                    "enabled": enabled,
                }
            )
            providers[index] = provider
        else:
            provider = LlmProviderSettings(
                name=normalized_name,
                base_url=normalized_url,
                api_key=api_key,
                model=normalized_model,
                enabled=enabled,
            )
            providers.append(provider)
        candidate = self._session.state.service_settings.model_copy(deep=True)
        candidate.llm_providers = providers
        if not candidate.active_llm_provider_id or not any(
            item.id == candidate.active_llm_provider_id and item.enabled for item in providers
        ):
            candidate.active_llm_provider_id = (
                provider.id
                if provider.enabled
                else next(
                    (item.id for item in providers if item.enabled),
                    None,
                )
            )
        self._session.state.selection.llm_provider_id = provider.id
        self._session.settings_persistence.commit(candidate, "LLM 提供商已保存")

    @Slot(str)
    @report_ui_errors
    def removeLlmProvider(self, provider_id: str) -> None:
        providers = [
            item for item in self._session.state.service_settings.llm_providers if item.id != provider_id
        ]
        if len(providers) == len(self._session.state.service_settings.llm_providers):
            raise KeyError(provider_id)
        candidate = self._session.state.service_settings.model_copy(deep=True)
        candidate.llm_providers = providers
        if candidate.active_llm_provider_id == provider_id:
            candidate.active_llm_provider_id = next(
                (item.id for item in providers if item.enabled),
                None,
            )
        self._session.state.selection.llm_provider_id = ""
        self._session.settings_persistence.commit(candidate, "LLM 提供商已移除")

    @Slot(str)
    @report_ui_errors
    def setActiveLlmProvider(self, provider_id: str) -> None:
        provider = next(
            item for item in self._session.state.service_settings.llm_providers if item.id == provider_id
        )
        if not provider.enabled:
            raise ValueError("启用提供商后才能设为当前提供商")
        candidate = self._session.state.service_settings.model_copy(deep=True)
        candidate.active_llm_provider_id = provider.id
        self._session.state.selection.llm_provider_id = provider.id
        self._session.settings_persistence.commit(candidate, "当前 LLM 提供商已切换")

    @Slot(str)
    @report_ui_errors(message="LLM 连接测试失败：{error}")
    def testLlmProvider(self, provider_id: str) -> None:
        provider = next(
            item for item in self._session.state.service_settings.llm_providers if item.id == provider_id
        )
        self._session._api.test_llm_provider(provider)
        self._session._set_status("%1 连接测试成功", provider.name)

    @Slot(str)
    def selectGlossaryTerm(self, term_id: str) -> None:
        self._session.state.selection.glossary_term_id = term_id
        self._session.updates.commit(selection=True)

    @Slot(str, str, str, str, str)
    @report_ui_errors
    def saveGlossaryTerm(
        self,
        term_id: str,
        source: str,
        target: str,
        note: str,
        category: str,
    ) -> None:
        terms = list(self._session.state.service_settings.translation.glossary_terms)
        values = {
            "source": source,
            "target": target,
            "note": note.strip(),
            "category": category.strip() or "general",
        }
        if term_id:
            try:
                index = next(index for index, item in enumerate(terms) if item.id == term_id)
            except StopIteration as error:
                raise KeyError(term_id) from error
            term = GlossaryTermSettings.model_validate({**terms[index].model_dump(mode="python"), **values})
            terms[index] = term
        else:
            term = GlossaryTermSettings(**values)
            terms.append(term)
        candidate = self._session.state.service_settings.model_copy(deep=True)
        candidate.translation.glossary_terms = terms
        self._session.state.selection.glossary_term_id = term.id
        self._session.settings_persistence.commit(candidate, "术语已保存")

    @Slot(str)
    @report_ui_errors
    def removeGlossaryTerm(self, term_id: str) -> None:
        terms = [
            item
            for item in self._session.state.service_settings.translation.glossary_terms
            if item.id != term_id
        ]
        if len(terms) == len(self._session.state.service_settings.translation.glossary_terms):
            raise KeyError(term_id)
        candidate = self._session.state.service_settings.model_copy(deep=True)
        candidate.translation.glossary_terms = terms
        self._session.state.selection.glossary_term_id = ""
        self._session.settings_persistence.commit(candidate, "术语已移除")
