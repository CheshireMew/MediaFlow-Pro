from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from mediaflow.domain.downloads import DownloadRequest
from mediaflow.domain.enums import ExportFormat, TaskKind, WorkflowStage
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.translation import TranslationMode


class WorkflowTaskLink(DomainModel):
    run_id: str
    stage: WorkflowStage


class CommandModel(DomainModel):
    workflow: WorkflowTaskLink | None = None

    @property
    def task_kind(self) -> TaskKind:
        raise NotImplementedError


class ImportAssetCommand(CommandModel):
    command_type: Literal["import_asset"] = "import_asset"
    source_path: str
    purpose: Literal["media", "subtitle", "watermark"] = "media"
    language: str = "auto"
    media_asset_id: str | None = None

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.IMPORT


class GenerateProxyCommand(CommandModel):
    command_type: Literal["generate_proxy"] = "generate_proxy"
    asset_id: str
    reasons: list[str] = Field(default_factory=list)

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.PROXY


class GenerateWaveformCommand(CommandModel):
    command_type: Literal["generate_waveform"] = "generate_waveform"
    asset_id: str

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.WAVEFORM


class DownloadMediaCommand(CommandModel):
    command_type: Literal["download_media"] = "download_media"
    request: DownloadRequest

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.DOWNLOAD


class ExportSequenceCommand(CommandModel):
    command_type: Literal["export_sequence"] = "export_sequence"
    sequence_id: str
    output_path: str
    format: ExportFormat = ExportFormat.H264
    preset: ExportPreset | None = None

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.EXPORT


class ExportHighlightsCommand(CommandModel):
    command_type: Literal["export_highlights"] = "export_highlights"
    sequence_id: str
    candidate_ids: list[str]
    output_dir: str
    preset: ExportPreset | None = None
    burn_subtitles: bool = True

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.EXPORT


class TranscribeAssetCommand(CommandModel):
    command_type: Literal["transcribe_asset"] = "transcribe_asset"
    asset_id: str

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.TRANSCRIBE


class TranscribeRegionCommand(CommandModel):
    command_type: Literal["transcribe_region"] = "transcribe_region"
    asset_id: str
    start_frame: int
    end_frame: int
    document_id: str | None = None
    translate_after: bool = False
    mode: TranslationMode = "standard"
    target_language: str = ""

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.TRANSCRIBE


class TranslateDocumentCommand(CommandModel):
    command_type: Literal["translate_document"] = "translate_document"
    document_id: str
    target_language: str
    mode: TranslationMode = "standard"

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.TRANSLATE


class TranslateSegmentsCommand(CommandModel):
    command_type: Literal["translate_segments"] = "translate_segments"
    document_id: str
    segment_ids: list[str]
    target_language: str
    mode: TranslationMode = "standard"

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.TRANSLATE


class AnalyzeHighlightsCommand(CommandModel):
    command_type: Literal["analyze_highlights"] = "analyze_highlights"
    document_id: str

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.HIGHLIGHT


class AnalyzeDownloadCommand(CommandModel):
    command_type: Literal["analyze_download"] = "analyze_download"
    url: str

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.ANALYZE


class AnalyzeSequenceBoundsCommand(CommandModel):
    command_type: Literal["analyze_sequence_bounds"] = "analyze_sequence_bounds"
    sequence_id: str
    snapshot_hash: str

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.ANALYZE


class AnalyzeLoudnessCommand(CommandModel):
    command_type: Literal["analyze_loudness"] = "analyze_loudness"
    sequence_id: str

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.ANALYZE


type TaskCommand = Annotated[
    ImportAssetCommand
    | GenerateProxyCommand
    | GenerateWaveformCommand
    | DownloadMediaCommand
    | ExportSequenceCommand
    | ExportHighlightsCommand
    | TranscribeAssetCommand
    | TranscribeRegionCommand
    | TranslateDocumentCommand
    | TranslateSegmentsCommand
    | AnalyzeHighlightsCommand
    | AnalyzeDownloadCommand
    | AnalyzeSequenceBoundsCommand
    | AnalyzeLoudnessCommand,
    Field(discriminator="command_type"),
]
