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
    default_media_root,
)

from .controller_facet import ControllerFacet


class SettingsController(ControllerFacet):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    statusChanged = Signal()
    tasksChanged = Signal()
    previewGraphChanged = Signal()
    profileConfirmationChanged = Signal()
    settingsChanged = Signal()
    relinkConfirmationChanged = Signal()
    audioMetricsChanged = Signal()
    workflowChanged = Signal()
    downloadPlanChanged = Signal()
    runtimeToolsChanged = Signal()
    waveformDataChanged = Signal(str)
    previewRangeRequested = Signal(int, int)
    errorOccurred = Signal(str)
    errorReferenceChanged = Signal()

    @Property(QObject, constant=True)
    def glossaryTermsModel(self) -> QObject:
        return self._glossary_model

    @Property(QObject, constant=True)
    def llmProvidersModel(self) -> QObject:
        return self._llm_provider_model

    @Property("QVariantList", constant=True)
    def builtInSubtitleStylePresets(self) -> list[dict]:
        return built_in_subtitle_style_presets()

    @Property("QVariantList", constant=True)
    def llmProviderPresets(self) -> list[dict]:
        return llm_provider_presets()

    @Property(str, notify=selectionChanged)
    def selectedGlossaryTermId(self) -> str:
        return self._selected_glossary_term_id

    @Property("QVariantMap", notify=selectionChanged)
    def selectedGlossaryTermData(self) -> dict:
        row = self._glossary_model.findRow("termId", self._selected_glossary_term_id)
        return self._glossary_model.get(row)

    @Property(str, notify=selectionChanged)
    def selectedLlmProviderId(self) -> str:
        return self._selected_llm_provider_id

    @Property("QVariantMap", notify=selectionChanged)
    def selectedLlmProviderData(self) -> dict:
        row = self._llm_provider_model.findRow("providerId", self._selected_llm_provider_id)
        return self._llm_provider_model.get(row)

    @Property("QVariantMap", notify=settingsChanged)
    def settingsData(self) -> dict:
        return settings_data(self.settings)

    @Property("QVariantList", notify=settingsChanged)
    def asrModelOptions(self) -> list[dict]:
        return asr_model_options(
            self.settings.asr.model,
            installed_models=self._installed_asr_models(),
        )

    @Property("QVariantList", notify=settingsChanged)
    def asrLanguageOptions(self) -> list[dict]:
        return asr_language_options(self.settings.asr.language)

    @Property("QVariantList", constant=True)
    def asrParallelOptions(self) -> list[dict]:
        return asr_parallel_options()

    @Property("QVariantMap", notify=runtimeToolsChanged)
    def runtimeToolStatus(self) -> dict:
        return self._runtime_tool_status

    @Property(str, notify=settingsChanged)
    def defaultTranslationLanguage(self) -> str:
        return self.settings.translation.target_language

    @Property(str, constant=True)
    def builtInMediaDirectory(self) -> str:
        return default_media_root()

    @Property("QVariantMap", notify=settingsChanged)
    def managedCookieStatus(self) -> dict:
        return self._cookie_status

    @Slot("QVariantMap")
    def saveSettings(self, values: dict) -> None:
        try:
            candidate = SettingsForm.model_validate(values).apply_to(self.settings)
            self._commit_settings(candidate)
            self._projector.refresh_runtime_tool_status()
            self._set_status("设置已保存；界面语言将在下次启动时生效")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    def _installed_asr_models(self) -> frozenset[str]:
        root = self._api.runtime_paths.runtime_dir / "models" / "faster-whisper"
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
        configured = self.settings.asr.model.strip()
        if configured and Path(configured).expanduser().is_dir():
            installed.add(configured)
        return frozenset(installed)

    @Slot(str)
    def setDefaultDownloadDirectory(self, value: str) -> None:
        try:
            if not value.strip():
                raise ValueError("媒体默认保存目录不能为空")
            selected = str(self._local_path(value))
            if self.settings.download.output_directory == selected:
                return
            candidate = self.settings.model_copy(deep=True)
            candidate.download.output_directory = selected
            self._commit_settings(candidate, "默认下载目录已更新")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def resetDefaultDownloadDirectory(self) -> None:
        self.setDefaultDownloadDirectory(default_media_root())

    @Slot(str)
    def setDefaultProjectDirectory(self, value: str) -> None:
        try:
            if not value.strip():
                return
            self._remember_default_project_directory(
                self._local_path(value),
                "默认项目保存目录已更新",
            )
            self.downloadPlanChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def setLastDownloadUrl(self, value: str) -> None:
        normalized = value.strip()
        if self.settings.download.last_url == normalized:
            return
        try:
            candidate = self.settings.model_copy(deep=True)
            candidate.download.last_url = normalized
            self._commit_settings(candidate)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str)
    def saveSubtitleStylePreset(self, name: str, style_json: str) -> None:
        try:
            value = name.strip()
            if not value:
                raise ValueError("请输入字幕样式预设名称")
            if any(item.name.casefold() == value.casefold() for item in self.settings.subtitle_style_presets):
                raise ValueError("同名字幕样式预设已存在")
            style = SubtitleStyle.model_validate(json.loads(style_json))
            candidate = self.settings.model_copy(deep=True)
            candidate.subtitle_style_presets = [
                *candidate.subtitle_style_presets,
                SubtitleStylePresetSettings(name=value, style=style),
            ]
            self._commit_settings(candidate, f"已保存字幕样式预设：{value}")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def removeSubtitleStylePreset(self, preset_id: str) -> None:
        try:
            candidate = self.settings.model_copy(deep=True)
            before = len(candidate.subtitle_style_presets)
            candidate.subtitle_style_presets = [
                item for item in self.settings.subtitle_style_presets if item.id != preset_id
            ]
            if len(candidate.subtitle_style_presets) == before:
                raise KeyError(preset_id)
            self._commit_settings(candidate, "字幕样式预设已移除")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def inspectRuntimeTools(self) -> None:
        self._start_runtime_tool_operation("inspect")

    @Slot()
    def updateYtDlp(self) -> None:
        self._start_runtime_tool_operation("update_ytdlp")

    @Slot()
    def installAsrCli(self) -> None:
        self._start_runtime_tool_operation("install_asr_cli")

    @Slot()
    def prewarmAsrCli(self) -> None:
        self._start_runtime_tool_operation("prewarm_asr_cli")

    @Slot()
    def cancelRuntimeToolOperation(self) -> None:
        if self._runtime_tool_thread and self._runtime_tool_thread.is_alive():
            self._runtime_tool_cancel.set()
            self._set_status("已请求取消运行时工具操作")

    @Slot(str)
    def selectLlmProvider(self, provider_id: str) -> None:
        self._selected_llm_provider_id = provider_id
        self.selectionChanged.emit()

    @Slot(str, str, str, str, str, bool)
    def saveLlmProvider(
        self,
        provider_id: str,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        enabled: bool,
    ) -> None:
        try:
            normalized_name = name.strip() or "默认 LLM"
            normalized_url = base_url.strip()
            normalized_model = model.strip()
            if not normalized_url or not normalized_model:
                raise ValueError("LLM Base URL 和模型名称需要同时填写")
            providers = list(self.settings.llm_providers)
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
            candidate = self.settings.model_copy(deep=True)
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
            self._selected_llm_provider_id = provider.id
            self._commit_settings(candidate, "LLM 提供商已保存")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def removeLlmProvider(self, provider_id: str) -> None:
        try:
            providers = [item for item in self.settings.llm_providers if item.id != provider_id]
            if len(providers) == len(self.settings.llm_providers):
                raise KeyError(provider_id)
            candidate = self.settings.model_copy(deep=True)
            candidate.llm_providers = providers
            if candidate.active_llm_provider_id == provider_id:
                candidate.active_llm_provider_id = next(
                    (item.id for item in providers if item.enabled),
                    None,
                )
            self._selected_llm_provider_id = ""
            self._commit_settings(candidate, "LLM 提供商已移除")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def setActiveLlmProvider(self, provider_id: str) -> None:
        try:
            provider = next(item for item in self.settings.llm_providers if item.id == provider_id)
            if not provider.enabled:
                raise ValueError("启用提供商后才能设为当前提供商")
            candidate = self.settings.model_copy(deep=True)
            candidate.active_llm_provider_id = provider.id
            self._selected_llm_provider_id = provider.id
            self._commit_settings(candidate, "当前 LLM 提供商已切换")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def testLlmProvider(self, provider_id: str) -> None:
        try:
            provider = next(item for item in self.settings.llm_providers if item.id == provider_id)
            self._api.test_llm_provider(provider)
            self._set_status(f"{provider.name} 连接测试成功")
        except Exception as error:
            self.errorOccurred.emit(f"LLM 连接测试失败：{error}")

    @Slot(str)
    def selectGlossaryTerm(self, term_id: str) -> None:
        self._selected_glossary_term_id = term_id
        self.selectionChanged.emit()

    @Slot(str, str, str, str, str)
    def saveGlossaryTerm(
        self,
        term_id: str,
        source: str,
        target: str,
        note: str,
        category: str,
    ) -> None:
        try:
            terms = list(self.settings.translation.glossary_terms)
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
                term = GlossaryTermSettings.model_validate(
                    {**terms[index].model_dump(mode="python"), **values}
                )
                terms[index] = term
            else:
                term = GlossaryTermSettings(**values)
                terms.append(term)
            candidate = self.settings.model_copy(deep=True)
            candidate.translation.glossary_terms = terms
            self._selected_glossary_term_id = term.id
            self._commit_settings(candidate, "术语已保存")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def removeGlossaryTerm(self, term_id: str) -> None:
        try:
            terms = [item for item in self.settings.translation.glossary_terms if item.id != term_id]
            if len(terms) == len(self.settings.translation.glossary_terms):
                raise KeyError(term_id)
            candidate = self.settings.model_copy(deep=True)
            candidate.translation.glossary_terms = terms
            self._selected_glossary_term_id = ""
            self._commit_settings(candidate, "术语已移除")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def inspectManagedCookies(self, domain: str) -> None:
        try:
            self._cookie_status = self._api.cookies.status(domain)
            self.settingsChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str)
    def saveManagedCookies(self, domain: str, json_text: str) -> None:
        try:
            payload = json.loads(json_text)
            cookies = payload.get("cookies") if isinstance(payload, dict) else payload
            if not isinstance(cookies, list) or not all(isinstance(item, dict) for item in cookies):
                raise ValueError("Cookie JSON 必须是对象数组或包含 cookies 数组的对象")
            path = self._api.cookies.save(domain, cookies)
            self._cookie_status = self._api.cookies.status(domain)
            self.settingsChanged.emit()
            self._set_status(f"Cookie 已保存到 {path}")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def clearManagedCookies(self, domain: str) -> None:
        try:
            removed = self._api.cookies.clear(domain)
            self._cookie_status = self._api.cookies.status(domain)
            self.settingsChanged.emit()
            self._set_status("Cookie 已清除" if removed else "该域名没有已保存的 Cookie")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int, int)
    def saveWindowSize(self, width: int, height: int) -> None:
        try:
            candidate = self.settings.model_copy(deep=True)
            candidate.ui.window_width = max(1180, int(width))
            candidate.ui.window_height = max(720, int(height))
            self._commit_settings(candidate)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int, int)
    def savePanelLayout(self, left: int, timeline: int) -> None:
        try:
            candidate = self.settings.model_copy(deep=True)
            candidate.ui.left_panel_width = max(340, min(640, int(left)))
            candidate.ui.timeline_height = max(210, min(640, int(timeline)))
            self._commit_settings(candidate)
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def setAssetViewMode(self, mode: str) -> None:
        if mode not in {"list", "thumbnails", "large_thumbnails"}:
            self.errorOccurred.emit(f"未知素材视图模式：{mode}")
            return
        try:
            candidate = self.settings.model_copy(deep=True)
            candidate.ui.asset_view_mode = mode
            self._commit_settings(candidate)
        except Exception as error:
            self.errorOccurred.emit(str(error))
