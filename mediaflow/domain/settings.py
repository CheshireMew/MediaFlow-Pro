from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from .exports import SubtitleStyle
from .model_base import DomainModel, new_id
from .translation import (
    TranslationMode,
    validate_translation_language,
    validate_translation_mode,
)


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
    output_directory: str = ""
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
    model_directory: str | None = None
    model: str = "large-v3-turbo"
    device: Literal["auto", "cuda", "cpu"] = "auto"
    compute_type: str = "float16"
    language: str = "auto"
    smart_split_limit: int = 42
    parallel_chunks: int = Field(default=0, ge=0, le=4)


class SpeechSynthesisSettings(DomainModel):
    gpt_sovits_root: str | None = None
    device: Literal["auto", "cuda", "cpu"] = "auto"
    startup_timeout_seconds: int = Field(default=300, ge=30, le=900)


class SpeakerDiarizationSettings(DomainModel):
    backend: Literal["transcript_clustering", "community_1"] = (
        "transcript_clustering"
    )
    clustering_python_executable: str | None = None
    embedding_model_path: str | None = None
    clustering_num_threads: int = Field(default=4, ge=1, le=32)
    clustering_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    python_executable: str | None = None
    model: str = "pyannote/speaker-diarization-community-1"
    hugging_face_token: str = ""
    device: Literal["auto", "cuda", "cpu"] = "auto"
    timeout_seconds: int = Field(default=7200, ge=60, le=86400)

    @field_validator("model")
    @classmethod
    def required_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Speaker diarization model cannot be empty")
        return normalized

    @field_validator("hugging_face_token")
    @classmethod
    def normalized_token(cls, value: str) -> str:
        return value.strip()


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


class ResourceLibrarySettings(DomainModel):
    catalog_paths: list[str] = Field(default_factory=list)

    @field_validator("catalog_paths")
    @classmethod
    def normalized_unique_paths(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Resource catalog paths must be unique")
        return normalized


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


WorkspaceLayoutPreset = Literal["standard", "media", "vertical"]
AssetViewMode = Literal["list", "thumbnails", "large_thumbnails"]


class WorkspacePanelLayoutSettings(DomainModel):
    left_panel_width: int = 520
    inspector_panel_width: int = 400
    timeline_height: int = 330
    tool_panel_visible: bool = True
    inspector_panel_visible: bool = True
    timeline_visible: bool = True

    @field_validator("left_panel_width", "inspector_panel_width", "timeline_height")
    @classmethod
    def positive_dimension(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("Workspace panel dimensions must be positive")
        return value


class WorkspaceLayoutsSettings(DomainModel):
    standard: WorkspacePanelLayoutSettings = Field(
        default_factory=WorkspacePanelLayoutSettings
    )
    media: WorkspacePanelLayoutSettings = Field(
        default_factory=lambda: WorkspacePanelLayoutSettings(
            left_panel_width=560,
            inspector_panel_width=360,
            timeline_height=300,
        )
    )
    vertical: WorkspacePanelLayoutSettings = Field(
        default_factory=lambda: WorkspacePanelLayoutSettings(
            left_panel_width=420,
            inspector_panel_width=360,
            timeline_height=280,
            tool_panel_visible=False,
        )
    )


class UiSettings(DomainModel):
    language: Literal["zh_CN", "en", "ja"] = "zh_CN"
    theme: Literal["dark", "high_contrast"] = "dark"
    asset_view_mode: AssetViewMode = "list"
    window_width: int = 1600
    window_height: int = 980
    window_maximized: bool = False
    workspace_layout_preset: WorkspaceLayoutPreset = "standard"
    workspace_layouts: WorkspaceLayoutsSettings = Field(
        default_factory=WorkspaceLayoutsSettings
    )
    workspace_tour_completed: bool = False
    default_import_directory: str | None = None
    recent_project_paths: list[str] = Field(default_factory=list)
    favorite_resource_keys: list[str] = Field(default_factory=list)

    @field_validator(
        "window_width",
        "window_height",
    )
    @classmethod
    def positive_dimension(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("UI dimensions must be positive")
        return value

    @field_validator("favorite_resource_keys")
    @classmethod
    def normalized_unique_resource_keys(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Favorite media resource keys must be unique")
        return normalized


class SettingsDocument(DomainModel):
    schema_version: int = 1


class ServiceSettings(SettingsDocument):
    default_project_directory: str = ""
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)
    download: DownloadSettings = Field(default_factory=DownloadSettings)
    asr: AsrSettings = Field(default_factory=AsrSettings)
    speech_synthesis: SpeechSynthesisSettings = Field(default_factory=SpeechSynthesisSettings)
    speaker_diarization: SpeakerDiarizationSettings = Field(
        default_factory=SpeakerDiarizationSettings
    )
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    preview: PreviewSettings = Field(default_factory=PreviewSettings)
    resource_library: ResourceLibrarySettings = Field(default_factory=ResourceLibrarySettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    subtitle_style_presets: list[SubtitleStylePresetSettings] = Field(default_factory=list)
    llm_providers: list[LlmProviderSettings] = Field(default_factory=list)
    active_llm_provider_id: str | None = None


class DesktopSettings(SettingsDocument):
    ui: UiSettings = Field(default_factory=UiSettings)
