from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from .models import DomainModel, new_id


class WorkflowSettings(DomainModel):
    auto_continue: bool = False
    confirm_download: bool = True
    confirm_proxy: bool = True
    confirm_transcribe: bool = True
    confirm_translate: bool = True
    confirm_highlight: bool = True
    confirm_export: bool = True


class DownloadSettings(DomainModel):
    proxy: str | None = None
    cookie_file: str | None = None
    browser_cookies: Literal["chrome", "edge"] | None = None
    resolution: str = "best"


class AsrSettings(DomainModel):
    model: str = "large-v3-turbo"
    device: Literal["auto", "cuda", "cpu"] = "auto"
    compute_type: str = "float16"
    language: str = "auto"
    auto_trim_silence: bool = True
    smart_split_limit: int = 42


class TranslationSettings(DomainModel):
    target_language: Literal["", "zh_CN", "en", "ja", "zh_TW", "ko", "es"] = ""


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
    window_width: int = 1600
    window_height: int = 980
    left_panel_width: int = 288
    inspector_width: int = 320
    timeline_height: int = 330
    default_import_directory: str | None = None
    recent_project_paths: list[str] = Field(default_factory=list)

    @field_validator(
        "window_width",
        "window_height",
        "left_panel_width",
        "inspector_width",
        "timeline_height",
    )
    @classmethod
    def positive_dimension(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("UI dimensions must be positive")
        return value


class GlobalSettings(DomainModel):
    schema_version: int = 3
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)
    download: DownloadSettings = Field(default_factory=DownloadSettings)
    asr: AsrSettings = Field(default_factory=AsrSettings)
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    preview: PreviewSettings = Field(default_factory=PreviewSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    llm_providers: list[LlmProviderSettings] = Field(default_factory=list)
    ui: UiSettings = Field(default_factory=UiSettings)
