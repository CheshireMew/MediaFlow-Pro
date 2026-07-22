from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from .exports import SubtitleStyle
from .model_base import DomainModel, new_id
from .translation import (
    TranslationMode,
    validate_translation_language,
    validate_translation_mode,
)

APPLICATION_ROOT_ENVIRONMENT_VARIABLE = "MEDIAFLOW_APP_ROOT"
PROJECT_DIRECTORY_NAME = "Project"
MEDIA_DIRECTORY_NAME = "WorkSpace"


def application_root() -> Path:
    configured = os.environ.get(APPLICATION_ROOT_ENVIRONMENT_VARIABLE)
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def default_project_root() -> str:
    return str((application_root() / PROJECT_DIRECTORY_NAME).resolve())


def default_media_root() -> str:
    return str((application_root() / MEDIA_DIRECTORY_NAME).resolve())


class WorkflowSettings(DomainModel):
    auto_continue: bool = False
    confirm_download: bool = True
    confirm_proxy: bool = True
    confirm_transcribe: bool = True
    confirm_translate: bool = True
    confirm_highlight: bool = True
    confirm_export: bool = True


class DownloadSettings(DomainModel):
    last_url: str = ""
    output_directory: str = Field(default_factory=default_media_root)
    proxy: str | None = None
    cookie_file: str | None = None
    browser_cookies: Literal["chrome", "edge"] | None = None
    resolution: str = "best"
    download_subtitles: bool = False
    subtitle_languages: list[str] = Field(default_factory=lambda: ["en", "zh"])
    codec: Literal["best", "avc"] = "avc"


class AsrSettings(DomainModel):
    engine: Literal["builtin", "faster_whisper_cli"] = "builtin"
    cli_path: str | None = None
    model: str = "large-v3-turbo"
    device: Literal["auto", "cuda", "cpu"] = "auto"
    compute_type: str = "float16"
    language: str = "auto"
    smart_split_limit: int = 42


class GlossaryTermSettings(DomainModel):
    id: str = Field(default_factory=new_id)
    source: str
    target: str
    note: str = ""
    category: str = "general"

    @field_validator("source", "target")
    @classmethod
    def non_empty_term(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Glossary source and target cannot be empty")
        return value


class TranslationSettings(DomainModel):
    target_language: str = "zh_CN"
    mode: TranslationMode = "standard"
    glossary_terms: list[GlossaryTermSettings] = Field(default_factory=list)

    @field_validator("target_language")
    @classmethod
    def supported_target_language(cls, value: str) -> str:
        return validate_translation_language(value)

    @field_validator("mode")
    @classmethod
    def supported_mode(cls, value: str) -> TranslationMode:
        return validate_translation_mode(value)


class PreviewSettings(DomainModel):
    automatic_proxy: bool = True
    preview_quality: Literal["auto", "source", "proxy"] = "auto"
    hdr_preview: bool = True
    dropped_frame_proxy_threshold: int = 3


class AudioSettings(DomainModel):
    sample_rate: int = 48_000
    default_layout: Literal["mono", "stereo", "5.1"] = "stereo"
    loudness_target_lufs: float = -14.0
    true_peak_db: float = -1.0


class SubtitleStylePresetSettings(DomainModel):
    id: str = Field(default_factory=new_id)
    name: str
    style: SubtitleStyle

    @field_validator("name")
    @classmethod
    def non_empty_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Subtitle style preset name cannot be empty")
        return value


class LlmProviderSettings(DomainModel):
    id: str = Field(default_factory=new_id)
    name: str
    base_url: str
    api_key: str = ""
    model: str
    enabled: bool = True


class UiSettings(DomainModel):
    language: Literal["zh_CN", "en", "ja"] = "zh_CN"
    theme: Literal["dark", "high_contrast"] = "dark"
    asset_view_mode: Literal["list", "thumbnails", "large_thumbnails"] = "list"
    window_width: int = 1600
    window_height: int = 980
    left_panel_width: int = 360
    timeline_height: int = 330
    default_project_directory: str = Field(default_factory=default_project_root)
    default_import_directory: str | None = None
    recent_project_paths: list[str] = Field(default_factory=list)

    @field_validator(
        "window_width",
        "window_height",
        "left_panel_width",
        "timeline_height",
    )
    @classmethod
    def positive_dimension(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("UI dimensions must be positive")
        return value


class GlobalSettings(DomainModel):
    schema_version: int = 13
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)
    download: DownloadSettings = Field(default_factory=DownloadSettings)
    asr: AsrSettings = Field(default_factory=AsrSettings)
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    preview: PreviewSettings = Field(default_factory=PreviewSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    subtitle_style_presets: list[SubtitleStylePresetSettings] = Field(default_factory=list)
    llm_providers: list[LlmProviderSettings] = Field(default_factory=list)
    active_llm_provider_id: str | None = None
    ui: UiSettings = Field(default_factory=UiSettings)
