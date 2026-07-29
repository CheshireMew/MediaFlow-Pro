from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot

from mediaflow.application.settings_form import SettingsForm, settings_data
from mediaflow.desktop.presentation_catalogs import (
    asr_language_options,
    asr_model_options,
    asr_parallel_options,
    built_in_subtitle_style_presets,
    llm_provider_presets,
)
from mediaflow.domain.exports import SubtitleStyle
from mediaflow.domain.settings import (
    GlossaryTermSettings,
    LlmProviderSettings,
    SubtitleStylePresetSettings,
)

from .controller_facet import ControllerFacet, report_ui_errors


class SettingsController(ControllerFacet):
    selectionChanged = Signal()
    settingsChanged = Signal()
    downloadPlanChanged = Signal()
    runtimeToolsChanged = Signal()
    errorOccurred = Signal(str)

    @Property(QObject, constant=True)
    def glossaryTermsModel(self) -> QObject:
        return self._session.models.glossary

    @Property(QObject, constant=True)
    def llmProvidersModel(self) -> QObject:
        return self._session.models.llm_providers

    @Property("QVariantList", constant=True)
    def builtInSubtitleStylePresets(self) -> list[dict]:
        return built_in_subtitle_style_presets()

    @Property("QVariantList", constant=True)
    def llmProviderPresets(self) -> list[dict]:
        return llm_provider_presets()

    @Property(str, notify=selectionChanged)
    def selectedGlossaryTermId(self) -> str:
        return self._session.selection.glossary_term_id

    @Property("QVariantMap", notify=selectionChanged)
    def selectedGlossaryTermData(self) -> dict:
        row = self._session.models.glossary.findRow("termId", self._session.selection.glossary_term_id)
        return self._session.models.glossary.get(row)

    @Property(str, notify=selectionChanged)
    def selectedLlmProviderId(self) -> str:
        return self._session.selection.llm_provider_id

    @Property("QVariantMap", notify=selectionChanged)
    def selectedLlmProviderData(self) -> dict:
        row = self._session.models.llm_providers.findRow(
            "providerId", self._session.selection.llm_provider_id
        )
        return self._session.models.llm_providers.get(row)

    @Property("QVariantMap", notify=settingsChanged)
    def settingsData(self) -> dict:
        return settings_data(self._session.settings)

    @Property("QVariantList", notify=settingsChanged)
    def asrModelOptions(self) -> list[dict]:
        return asr_model_options(
            self._session.settings.asr.model,
            installed_models=self._installed_asr_models(),
        )

    @Property("QVariantList", notify=settingsChanged)
    def asrLanguageOptions(self) -> list[dict]:
        return asr_language_options(self._session.settings.asr.language)

    @Property("QVariantList", constant=True)
    def asrParallelOptions(self) -> list[dict]:
        return asr_parallel_options()

    @Property("QVariantMap", notify=runtimeToolsChanged)
    def runtimeToolStatus(self) -> dict:
        return self._session.runtime_state.status

    @Property(str, notify=settingsChanged)
    def defaultTranslationLanguage(self) -> str:
        return self._session.settings.translation.target_language

    @Property(str, constant=True)
    def builtInMediaDirectory(self) -> str:
        return self._session._api.default_media_directory

    @Property("QVariantMap", notify=settingsChanged)
    def managedCookieStatus(self) -> dict:
        return self._session.download_state.cookie_status

    @Slot("QVariantMap", "QVariantMap")
    @report_ui_errors
    def saveSettings(
        self,
        values: dict,
        baseline_values: dict,
    ) -> None:
        submitted = SettingsForm.model_validate(values)
        baseline = SettingsForm.model_validate(
            baseline_values
        )
        current_values = SettingsForm.from_settings(
            self._session.settings
        ).model_dump(mode="json", by_alias=True)
        submitted_values = submitted.model_dump(
            mode="json",
            by_alias=True,
        )
        baseline_data = baseline.model_dump(
            mode="json",
            by_alias=True,
        )
        for key, value in submitted_values.items():
            if value != baseline_data[key]:
                current_values[key] = value
        candidate = SettingsForm.model_validate(
            current_values
        ).apply_to(self._session.settings)
        self._session._commit_settings(candidate)
        self._session.projectors.workspace.refresh_runtime_tool_status()
        self._session._set_status("设置已保存；界面语言将在下次启动时生效")

    def _installed_asr_models(self) -> frozenset[str]:
        root = self._session._api.runtime_paths.runtime_dir / "models" / "faster-whisper"
        if not root.is_dir():
            return frozenset()
        installed: set[str] = set()
        prefix = "models--Systran--faster-whisper-"
        for candidate in root.iterdir():
            if not candidate.is_dir():
                continue
            model = candidate.name.removeprefix(prefix)
            if model == candidate.name:
                model = candidate.name
            direct_model = candidate / "model.bin"
            snapshots = candidate / "snapshots"
            has_snapshot = snapshots.is_dir() and any(
                (snapshot / "model.bin").is_file() for snapshot in snapshots.iterdir()
            )
            if direct_model.is_file() or has_snapshot:
                installed.add(model)
        configured = self._session.settings.asr.model.strip()
        if configured and Path(configured).expanduser().is_dir():
            installed.add(configured)
        return frozenset(installed)

    @Slot(str)
    @report_ui_errors
    def setDefaultDownloadDirectory(self, value: str) -> None:
        if not value.strip():
            raise ValueError("媒体默认保存目录不能为空")
        selected = str(self._session._local_path(value))
        if self._session.settings.download.output_directory == selected:
            return
        candidate = self._session.settings.model_copy(deep=True)
        candidate.download.output_directory = selected
        self._session._commit_settings(candidate, "默认下载目录已更新")

    @Slot()
    def resetDefaultDownloadDirectory(self) -> None:
        self.setDefaultDownloadDirectory(self._session._api.default_media_directory)

    @Slot(str)
    @report_ui_errors
    def setDefaultProjectDirectory(self, value: str) -> None:
        if not value.strip():
            return
        self._session._remember_default_project_directory(
            self._session._local_path(value),
            "默认项目保存目录已更新",
        )
        self._session.events.downloadPlanChanged.emit()

    @Slot(str)
    @report_ui_errors
    def setLastDownloadUrl(self, value: str) -> None:
        normalized = value.strip()
        if self._session.settings.download.last_url == normalized:
            return
        candidate = self._session.settings.model_copy(deep=True)
        candidate.download.last_url = normalized
        self._session._commit_settings(candidate)

    @Slot(str, str)
    @report_ui_errors
    def saveSubtitleStylePreset(self, name: str, style_json: str) -> None:
        value = name.strip()
        if not value:
            raise ValueError("请输入字幕样式预设名称")
        if any(
            item.name.casefold() == value.casefold() for item in self._session.settings.subtitle_style_presets
        ):
            raise ValueError("同名字幕样式预设已存在")
        style = SubtitleStyle.model_validate(json.loads(style_json))
        candidate = self._session.settings.model_copy(deep=True)
        candidate.subtitle_style_presets = [
            *candidate.subtitle_style_presets,
            SubtitleStylePresetSettings(name=value, style=style),
        ]
        self._session._commit_settings(candidate, f"已保存字幕样式预设：{value}")

    @Slot(str)
    @report_ui_errors
    def removeSubtitleStylePreset(self, preset_id: str) -> None:
        candidate = self._session.settings.model_copy(deep=True)
        before = len(candidate.subtitle_style_presets)
        candidate.subtitle_style_presets = [
            item for item in self._session.settings.subtitle_style_presets if item.id != preset_id
        ]
        if len(candidate.subtitle_style_presets) == before:
            raise KeyError(preset_id)
        self._session._commit_settings(candidate, "字幕样式预设已移除")

    @Slot()
    def inspectRuntimeTools(self) -> None:
        self._session.runtime_tools.start("inspect")

    @Slot()
    def updateYtDlp(self) -> None:
        self._session.runtime_tools.start("update_ytdlp")

    @Slot()
    def installAsrCli(self) -> None:
        self._session.runtime_tools.start("install_asr_cli")

    @Slot()
    def prewarmAsrCli(self) -> None:
        self._session.runtime_tools.start("prewarm_asr_cli")

    @Slot()
    def cancelRuntimeToolOperation(self) -> None:
        if self._session.runtime_state.thread and self._session.runtime_state.thread.is_alive():
            self._session.runtime_state.cancel.set()
            self._session._set_status("已请求取消运行时工具操作")

    @Slot(str)
    def selectLlmProvider(self, provider_id: str) -> None:
        self._session.selection.llm_provider_id = provider_id
        self._session.events.selectionChanged.emit()

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
        providers = list(self._session.settings.llm_providers)
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
        candidate = self._session.settings.model_copy(deep=True)
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
        self._session.selection.llm_provider_id = provider.id
        self._session._commit_settings(candidate, "LLM 提供商已保存")

    @Slot(str)
    @report_ui_errors
    def removeLlmProvider(self, provider_id: str) -> None:
        providers = [item for item in self._session.settings.llm_providers if item.id != provider_id]
        if len(providers) == len(self._session.settings.llm_providers):
            raise KeyError(provider_id)
        candidate = self._session.settings.model_copy(deep=True)
        candidate.llm_providers = providers
        if candidate.active_llm_provider_id == provider_id:
            candidate.active_llm_provider_id = next(
                (item.id for item in providers if item.enabled),
                None,
            )
        self._session.selection.llm_provider_id = ""
        self._session._commit_settings(candidate, "LLM 提供商已移除")

    @Slot(str)
    @report_ui_errors
    def setActiveLlmProvider(self, provider_id: str) -> None:
        provider = next(item for item in self._session.settings.llm_providers if item.id == provider_id)
        if not provider.enabled:
            raise ValueError("启用提供商后才能设为当前提供商")
        candidate = self._session.settings.model_copy(deep=True)
        candidate.active_llm_provider_id = provider.id
        self._session.selection.llm_provider_id = provider.id
        self._session._commit_settings(candidate, "当前 LLM 提供商已切换")

    @Slot(str)
    @report_ui_errors(message="LLM 连接测试失败：{error}")
    def testLlmProvider(self, provider_id: str) -> None:
        provider = next(item for item in self._session.settings.llm_providers if item.id == provider_id)
        self._session._api.test_llm_provider(provider)
        self._session._set_status(f"{provider.name} 连接测试成功")

    @Slot(str)
    def selectGlossaryTerm(self, term_id: str) -> None:
        self._session.selection.glossary_term_id = term_id
        self._session.events.selectionChanged.emit()

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
        terms = list(self._session.settings.translation.glossary_terms)
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
        candidate = self._session.settings.model_copy(deep=True)
        candidate.translation.glossary_terms = terms
        self._session.selection.glossary_term_id = term.id
        self._session._commit_settings(candidate, "术语已保存")

    @Slot(str)
    @report_ui_errors
    def removeGlossaryTerm(self, term_id: str) -> None:
        terms = [item for item in self._session.settings.translation.glossary_terms if item.id != term_id]
        if len(terms) == len(self._session.settings.translation.glossary_terms):
            raise KeyError(term_id)
        candidate = self._session.settings.model_copy(deep=True)
        candidate.translation.glossary_terms = terms
        self._session.selection.glossary_term_id = ""
        self._session._commit_settings(candidate, "术语已移除")

    @Slot(str)
    @report_ui_errors
    def inspectManagedCookies(self, domain: str) -> None:
        self._session.download_state.cookie_status = self._session._api.cookies.status(domain)
        self._session.events.settingsChanged.emit()

    @Slot(str, str)
    @report_ui_errors
    def saveManagedCookies(self, domain: str, json_text: str) -> None:
        payload = json.loads(json_text)
        cookies = payload.get("cookies") if isinstance(payload, dict) else payload
        if not isinstance(cookies, list) or not all(isinstance(item, dict) for item in cookies):
            raise ValueError("Cookie JSON 必须是对象数组或包含 cookies 数组的对象")
        path = self._session._api.cookies.save(domain, cookies)
        self._session.download_state.cookie_status = self._session._api.cookies.status(domain)
        self._session.events.settingsChanged.emit()
        self._session._set_status(f"Cookie 已保存到 {path}")

    @Slot(str)
    @report_ui_errors
    def clearManagedCookies(self, domain: str) -> None:
        removed = self._session._api.cookies.clear(domain)
        self._session.download_state.cookie_status = self._session._api.cookies.status(domain)
        self._session.events.settingsChanged.emit()
        self._session._set_status("Cookie 已清除" if removed else "该域名没有已保存的 Cookie")

    @Slot(int, int, bool)
    @report_ui_errors
    def saveWindowState(self, width: int, height: int, maximized: bool) -> None:
        candidate = self._session.settings.model_copy(deep=True)
        candidate.ui.window_width = max(1, int(width))
        candidate.ui.window_height = max(1, int(height))
        candidate.ui.window_maximized = bool(maximized)
        self._session._commit_settings(candidate)

    @Slot(int, int)
    @report_ui_errors
    def savePanelLayout(self, left: int, timeline: int) -> None:
        candidate = self._session.settings.model_copy(deep=True)
        candidate.ui.left_panel_width = max(340, min(640, int(left)))
        candidate.ui.timeline_height = max(210, min(640, int(timeline)))
        self._session._commit_settings(candidate)

    @Slot(str)
    @report_ui_errors
    def setAssetViewMode(self, mode: str) -> None:
        if mode not in {"list", "thumbnails", "large_thumbnails"}:
            self._session.events.errorOccurred.emit(f"未知素材视图模式：{mode}")
            return
        candidate = self._session.settings.model_copy(deep=True)
        candidate.ui.asset_view_mode = mode
        self._session._commit_settings(candidate)
