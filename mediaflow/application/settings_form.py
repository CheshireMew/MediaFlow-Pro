from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mediaflow.domain.settings import DesktopSettings, ServiceSettings
from mediaflow.domain.translation import TranslationMode


class SettingsForm(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    language: Literal["zh_CN", "en", "ja"]
    theme: Literal["dark", "high_contrast"]
    auto_continue: bool = Field(alias="autoContinue")
    default_project_directory: str = Field(alias="defaultProjectDirectory")
    default_import_directory: str = Field(alias="defaultImportDirectory")
    download_resolution: str = Field(alias="downloadResolution")
    download_directory: str = Field(alias="downloadDirectory")
    download_proxy: str = Field(alias="downloadProxy")
    cookie_file: str = Field(alias="cookieFile")
    browser_cookies: Literal["", "chrome", "edge"] = Field(alias="browserCookies")
    download_subtitles: bool = Field(alias="downloadSubtitles")
    subtitle_languages: str = Field(alias="subtitleLanguages")
    download_codec: Literal["best", "avc"] = Field(alias="downloadCodec")
    asr_engine: Literal["builtin", "faster_whisper_cli"] = Field(alias="asrEngine")
    asr_cli_path: str = Field(alias="asrCliPath")
    asr_model_directory: str = Field(alias="asrModelDirectory")
    asr_model: str = Field(alias="asrModel")
    asr_device: Literal["auto", "cuda", "cpu"] = Field(alias="asrDevice")
    asr_compute_type: str = Field(alias="asrComputeType")
    asr_language: str = Field(alias="asrLanguage")
    asr_smart_split_limit: int = Field(alias="asrSmartSplitLimit", ge=1, le=200)
    asr_parallel_chunks: int = Field(alias="asrParallelChunks", ge=0, le=4)
    gpt_sovits_root: str = Field(alias="gptSoVitsRoot")
    gpt_sovits_device: Literal["auto", "cuda", "cpu"] = Field(alias="gptSoVitsDevice")
    translation_target_language: str = Field(alias="translationTargetLanguage")
    translation_mode: TranslationMode = Field(alias="translationMode")
    automatic_proxy: bool = Field(alias="automaticProxy")
    preview_quality: Literal["auto", "source", "proxy"] = Field(alias="previewQuality")
    hdr_preview: bool = Field(alias="hdrPreview")
    loudness_target: float = Field(alias="loudnessTarget")
    true_peak: float = Field(alias="truePeak")
    audio_layout: Literal["mono", "stereo", "5.1"] = Field(alias="audioLayout")

    @classmethod
    def from_settings(
        cls,
        service: ServiceSettings,
        desktop: DesktopSettings,
    ) -> SettingsForm:
        return cls.model_validate(
            {
                "language": desktop.ui.language,
                "theme": desktop.ui.theme,
                "auto_continue": service.workflow.auto_continue,
                "default_project_directory": service.default_project_directory,
                "default_import_directory": desktop.ui.default_import_directory or "",
                "download_resolution": service.download.resolution,
                "download_directory": service.download.output_directory,
                "download_proxy": service.download.proxy or "",
                "cookie_file": service.download.cookie_file or "",
                "browser_cookies": service.download.browser_cookies or "",
                "download_subtitles": service.download.download_subtitles,
                "subtitle_languages": ",".join(service.download.subtitle_languages),
                "download_codec": service.download.codec,
                "asr_engine": service.asr.engine,
                "asr_cli_path": service.asr.cli_path or "",
                "asr_model_directory": service.asr.model_directory or "",
                "asr_model": service.asr.model,
                "asr_device": service.asr.device,
                "asr_compute_type": service.asr.compute_type,
                "asr_language": service.asr.language,
                "asr_smart_split_limit": service.asr.smart_split_limit,
                "asr_parallel_chunks": service.asr.parallel_chunks,
                "gpt_sovits_root": service.speech_synthesis.gpt_sovits_root or "",
                "gpt_sovits_device": service.speech_synthesis.device,
                "translation_target_language": service.translation.target_language,
                "translation_mode": service.translation.mode,
                "automatic_proxy": service.preview.automatic_proxy,
                "preview_quality": service.preview.preview_quality,
                "hdr_preview": service.preview.hdr_preview,
                "loudness_target": service.audio.loudness_target_lufs,
                "true_peak": service.audio.true_peak_db,
                "audio_layout": service.audio.default_layout,
            }
        )

    def apply_to(
        self,
        service: ServiceSettings,
        desktop: DesktopSettings,
    ) -> tuple[ServiceSettings, DesktopSettings]:
        service_candidate = service.model_copy(deep=True)
        desktop_candidate = desktop.model_copy(deep=True)
        desktop_candidate.ui.language = self.language
        desktop_candidate.ui.theme = self.theme
        project_directory = (
            self.default_project_directory.strip()
            or service.default_project_directory
        )
        service_candidate.default_project_directory = str(
            Path(project_directory).expanduser().resolve()
        )
        desktop_candidate.ui.default_import_directory = (
            self.default_import_directory.strip() or None
        )
        service_candidate.workflow.auto_continue = self.auto_continue
        service_candidate.download.resolution = self.download_resolution
        directory = self.download_directory.strip() or service.download.output_directory
        service_candidate.download.output_directory = str(
            Path(directory).expanduser().resolve()
        )
        service_candidate.download.proxy = self.download_proxy.strip() or None
        service_candidate.download.cookie_file = self.cookie_file.strip() or None
        service_candidate.download.browser_cookies = self.browser_cookies or None
        service_candidate.download.download_subtitles = self.download_subtitles
        languages = [value.strip() for value in self.subtitle_languages.split(",") if value.strip()]
        service_candidate.download.subtitle_languages = languages or ["en", "zh"]
        service_candidate.download.codec = self.download_codec
        service_candidate.asr.engine = self.asr_engine
        service_candidate.asr.cli_path = self.asr_cli_path.strip() or None
        service_candidate.asr.model_directory = self.asr_model_directory.strip() or None
        service_candidate.asr.model = self.asr_model
        service_candidate.asr.device = self.asr_device
        service_candidate.asr.compute_type = self.asr_compute_type
        service_candidate.asr.language = self.asr_language
        service_candidate.asr.smart_split_limit = self.asr_smart_split_limit
        service_candidate.asr.parallel_chunks = self.asr_parallel_chunks
        service_candidate.speech_synthesis.gpt_sovits_root = (
            self.gpt_sovits_root.strip() or None
        )
        service_candidate.speech_synthesis.device = self.gpt_sovits_device
        service_candidate.translation.target_language = self.translation_target_language
        service_candidate.translation.mode = self.translation_mode
        service_candidate.preview.automatic_proxy = self.automatic_proxy
        service_candidate.preview.preview_quality = self.preview_quality
        service_candidate.preview.hdr_preview = self.hdr_preview
        service_candidate.audio.loudness_target_lufs = self.loudness_target
        service_candidate.audio.true_peak_db = self.true_peak
        service_candidate.audio.default_layout = self.audio_layout
        return service_candidate, desktop_candidate


def settings_data(service: ServiceSettings, desktop: DesktopSettings) -> dict:
    return {
        **SettingsForm.from_settings(service, desktop).model_dump(by_alias=True),
        "windowWidth": desktop.ui.window_width,
        "windowHeight": desktop.ui.window_height,
        "windowMaximized": desktop.ui.window_maximized,
        "workspaceLayoutPreset": desktop.ui.workspace_layout_preset,
        "workspaceLayouts": desktop.ui.workspace_layouts.model_dump(
            mode="json", by_alias=True
        ),
        "workspaceTourCompleted": desktop.ui.workspace_tour_completed,
        "assetViewMode": desktop.ui.asset_view_mode,
        "lastDownloadUrl": service.download.last_url,
        "subtitleStylePresets": [
            preset.model_dump(mode="json") for preset in service.subtitle_style_presets
        ],
    }
