from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mediaflow.domain.settings import GlobalSettings
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
    def from_settings(cls, settings: GlobalSettings) -> SettingsForm:
        return cls.model_validate(
            {
                "language": settings.ui.language,
                "theme": settings.ui.theme,
                "auto_continue": settings.workflow.auto_continue,
                "default_project_directory": settings.ui.default_project_directory,
                "default_import_directory": settings.ui.default_import_directory or "",
                "download_resolution": settings.download.resolution,
                "download_directory": settings.download.output_directory,
                "download_proxy": settings.download.proxy or "",
                "cookie_file": settings.download.cookie_file or "",
                "browser_cookies": settings.download.browser_cookies or "",
                "download_subtitles": settings.download.download_subtitles,
                "subtitle_languages": ",".join(settings.download.subtitle_languages),
                "download_codec": settings.download.codec,
                "asr_engine": settings.asr.engine,
                "asr_cli_path": settings.asr.cli_path or "",
                "asr_model_directory": settings.asr.model_directory or "",
                "asr_model": settings.asr.model,
                "asr_device": settings.asr.device,
                "asr_compute_type": settings.asr.compute_type,
                "asr_language": settings.asr.language,
                "asr_smart_split_limit": settings.asr.smart_split_limit,
                "asr_parallel_chunks": settings.asr.parallel_chunks,
                "gpt_sovits_root": settings.speech_synthesis.gpt_sovits_root or "",
                "gpt_sovits_device": settings.speech_synthesis.device,
                "translation_target_language": settings.translation.target_language,
                "translation_mode": settings.translation.mode,
                "automatic_proxy": settings.preview.automatic_proxy,
                "preview_quality": settings.preview.preview_quality,
                "hdr_preview": settings.preview.hdr_preview,
                "loudness_target": settings.audio.loudness_target_lufs,
                "true_peak": settings.audio.true_peak_db,
                "audio_layout": settings.audio.default_layout,
            }
        )

    def apply_to(self, settings: GlobalSettings) -> GlobalSettings:
        candidate = settings.model_copy(deep=True)
        candidate.ui.language = self.language
        candidate.ui.theme = self.theme
        project_directory = (
            self.default_project_directory.strip()
            or settings.ui.default_project_directory
        )
        candidate.ui.default_project_directory = str(Path(project_directory).expanduser().resolve())
        candidate.ui.default_import_directory = self.default_import_directory.strip() or None
        candidate.workflow.auto_continue = self.auto_continue
        candidate.download.resolution = self.download_resolution
        directory = self.download_directory.strip() or settings.download.output_directory
        candidate.download.output_directory = str(Path(directory).expanduser().resolve())
        candidate.download.proxy = self.download_proxy.strip() or None
        candidate.download.cookie_file = self.cookie_file.strip() or None
        candidate.download.browser_cookies = self.browser_cookies or None
        candidate.download.download_subtitles = self.download_subtitles
        languages = [value.strip() for value in self.subtitle_languages.split(",") if value.strip()]
        candidate.download.subtitle_languages = languages or ["en", "zh"]
        candidate.download.codec = self.download_codec
        candidate.asr.engine = self.asr_engine
        candidate.asr.cli_path = self.asr_cli_path.strip() or None
        candidate.asr.model_directory = self.asr_model_directory.strip() or None
        candidate.asr.model = self.asr_model
        candidate.asr.device = self.asr_device
        candidate.asr.compute_type = self.asr_compute_type
        candidate.asr.language = self.asr_language
        candidate.asr.smart_split_limit = self.asr_smart_split_limit
        candidate.asr.parallel_chunks = self.asr_parallel_chunks
        candidate.speech_synthesis.gpt_sovits_root = self.gpt_sovits_root.strip() or None
        candidate.speech_synthesis.device = self.gpt_sovits_device
        candidate.translation.target_language = self.translation_target_language
        candidate.translation.mode = self.translation_mode
        candidate.preview.automatic_proxy = self.automatic_proxy
        candidate.preview.preview_quality = self.preview_quality
        candidate.preview.hdr_preview = self.hdr_preview
        candidate.audio.loudness_target_lufs = self.loudness_target
        candidate.audio.true_peak_db = self.true_peak
        candidate.audio.default_layout = self.audio_layout
        return candidate


def settings_data(settings: GlobalSettings) -> dict:
    return {
        **SettingsForm.from_settings(settings).model_dump(by_alias=True),
        "windowWidth": settings.ui.window_width,
        "windowHeight": settings.ui.window_height,
        "windowMaximized": settings.ui.window_maximized,
        "leftPanelWidth": settings.ui.left_panel_width,
        "timelineHeight": settings.ui.timeline_height,
        "assetViewMode": settings.ui.asset_view_mode,
        "lastDownloadUrl": settings.download.last_url,
        "subtitleStylePresets": [
            preset.model_dump(mode="json") for preset in settings.subtitle_style_presets
        ],
    }
