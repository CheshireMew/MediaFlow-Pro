from __future__ import annotations

import json

from PySide6.QtCore import Property, QObject, Signal, Slot

from mediaflow.application.settings_form import SettingsForm, settings_data
from mediaflow.desktop.presentation_asr import (
    asr_language_options,
    asr_model_options,
    asr_parallel_options,
)
from mediaflow.desktop.presentation_subtitles import built_in_subtitle_style_presets
from mediaflow.desktop.settings_draft import SettingsDraft
from mediaflow.domain.exports import SubtitleStyle
from mediaflow.domain.settings import SubtitleStylePresetSettings

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import SettingsFormControllerScope


class SettingsController(ControllerFacet[SettingsFormControllerScope]):
    settingsChanged = Signal()
    errorOccurred = Signal(str)

    def __init__(self, session: SettingsFormControllerScope):
        super().__init__(session)
        self._startup_language = session.state.desktop_settings.ui.language
        self._settings_draft = SettingsDraft(
            self,
            read_current=self._current_settings_form,
            commit=self._commit_settings_form,
            report_error=self._session.updates.report_error,
        )
        self.settingsChanged.connect(self._settings_draft.refresh)

    @Property(QObject, constant=True)
    def settingsDraft(self) -> QObject:
        return self._settings_draft

    @Property(list, constant=True)
    def builtInSubtitleStylePresets(self) -> list[dict]:
        return built_in_subtitle_style_presets()

    @Property(dict, notify=settingsChanged)
    def settingsData(self) -> dict:
        return settings_data(
            self._session.state.service_settings,
            self._session.state.desktop_settings,
        )

    @Property(list, notify=settingsChanged)
    def asrModelOptions(self) -> list[dict]:
        return asr_model_options(
            self._session.state.service_settings.asr.model,
            installed_models=self._installed_asr_models(),
        )

    @Property(list, notify=settingsChanged)
    def asrLanguageOptions(self) -> list[dict]:
        return asr_language_options(self._session.state.service_settings.asr.language)

    @Property(list, constant=True)
    def asrParallelOptions(self) -> list[dict]:
        return asr_parallel_options()

    @Property(bool, notify=settingsChanged)
    def languageRestartRequired(self) -> bool:
        return self._session.state.desktop_settings.ui.language != self._startup_language

    @Property(str, notify=settingsChanged)
    def defaultTranslationLanguage(self) -> str:
        return self._session.state.service_settings.translation.target_language

    def _current_settings_form(self) -> SettingsForm:
        return SettingsForm.from_settings(
            self._session.state.service_settings,
            self._session.state.desktop_settings,
        )

    def _commit_settings_form(self, form: SettingsForm) -> None:
        service_candidate, desktop_candidate = form.apply_to(
            self._session.state.service_settings,
            self._session.state.desktop_settings,
        )
        self._session.settings_persistence.commit_pair(
            service_candidate,
            desktop_candidate,
            "设置已保存；界面语言将在下次启动时生效",
        )
        self._session.projectors.workspace.refresh_runtime_tool_status()

    def _installed_asr_models(self) -> frozenset[str]:
        return self._session._api.installed_asr_models()

    @Slot(str, str)
    @report_ui_errors
    def saveSubtitleStylePreset(self, name: str, style_json: str) -> None:
        value = name.strip()
        if not value:
            raise ValueError("请输入字幕样式预设名称")
        if any(
            item.name.casefold() == value.casefold()
            for item in self._session.state.service_settings.subtitle_style_presets
        ):
            raise ValueError("同名字幕样式预设已存在")
        style = SubtitleStyle.model_validate(json.loads(style_json))
        candidate = self._session.state.service_settings.model_copy(deep=True)
        candidate.subtitle_style_presets = [
            *candidate.subtitle_style_presets,
            SubtitleStylePresetSettings(name=value, style=style),
        ]
        self._session.settings_persistence.commit(
            candidate,
            "已保存字幕样式预设：%1",
            value,
        )

    @Slot(str)
    @report_ui_errors
    def removeSubtitleStylePreset(self, preset_id: str) -> None:
        candidate = self._session.state.service_settings.model_copy(deep=True)
        before = len(candidate.subtitle_style_presets)
        candidate.subtitle_style_presets = [
            item
            for item in self._session.state.service_settings.subtitle_style_presets
            if item.id != preset_id
        ]
        if len(candidate.subtitle_style_presets) == before:
            raise KeyError(preset_id)
        self._session.settings_persistence.commit(candidate, "字幕样式预设已移除")
