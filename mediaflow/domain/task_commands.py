from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from mediaflow.domain.asr import TranscriptionPlan
from mediaflow.domain.downloads import DownloadRequest
from mediaflow.domain.dubbing import DubbingSettings
from mediaflow.domain.enums import ExportFormat, TaskKind, WorkflowStage
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.translation import TranslationMode
from mediaflow.domain.web_exports import (
    WebExportFormat,
    require_web_export_destination,
)

NonEmptyText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
    ),
]


class WorkflowTaskLink(DomainModel):
    run_id: NonEmptyText
    stage: WorkflowStage


class CommandModel(DomainModel):
    workflow: WorkflowTaskLink | None = None

    def validate_for_execution(self) -> None:
        """Reject commands that are valid only as historical task records."""

    @property
    def task_kind(self) -> TaskKind:
        raise NotImplementedError


class ImportAssetCommand(CommandModel):
    command_type: Literal["import_asset"] = "import_asset"
    source_path: NonEmptyText
    purpose: Literal["media", "subtitle", "watermark"] = "media"
    language: NonEmptyText = "auto"
    media_asset_id: NonEmptyText | None = None

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.IMPORT


class GenerateProxyCommand(CommandModel):
    command_type: Literal["generate_proxy"] = "generate_proxy"
    asset_id: NonEmptyText
    reasons: list[NonEmptyText] = Field(default_factory=list)

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.PROXY


class GenerateWaveformCommand(CommandModel):
    command_type: Literal["generate_waveform"] = "generate_waveform"
    asset_id: NonEmptyText

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
    sequence_id: NonEmptyText
    output_path: NonEmptyText
    format: ExportFormat = ExportFormat.H264
    preset: ExportPreset | None = None
    overwrite: bool = False

    def validate_for_execution(self) -> None:
        if self.preset is not None and self.preset.format != self.format:
            raise ValueError("导出预设格式必须与请求的导出格式一致")
        if self.preset is not None:
            self.preset.validate_destination(self.output_path)

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.EXPORT


class SequenceBuildUnit(DomainModel):
    id: NonEmptyText
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=1)

    @model_validator(mode="after")
    def positive_range(self) -> SequenceBuildUnit:
        if self.end_frame <= self.start_frame:
            raise ValueError("Build unit end_frame must be after start_frame")
        return self


class BuildSequenceCommand(CommandModel):
    command_type: Literal["build_sequence"] = "build_sequence"
    sequence_id: NonEmptyText
    units: list[SequenceBuildUnit] = Field(min_length=1)
    output_path: NonEmptyText
    format: ExportFormat = ExportFormat.H264
    preset: ExportPreset | None = None
    overwrite: bool = False

    @model_validator(mode="after")
    def coherent_units(self) -> BuildSequenceCommand:
        ids = [unit.id for unit in self.units]
        if len(set(ids)) != len(ids):
            raise ValueError("Build units must have unique ids")
        if self.units[0].start_frame != 0:
            raise ValueError("Build units must start at frame 0")
        previous_end: int | None = None
        for unit in self.units:
            if previous_end is not None and unit.start_frame != previous_end:
                raise ValueError("Build units must be ordered and contiguous for deterministic assembly")
            previous_end = unit.end_frame
        if self.preset is not None and self.preset.format != self.format:
            raise ValueError("Build preset format must match the requested format")
        if self.preset is not None:
            self.preset.validate_destination(self.output_path)
        if self.format == ExportFormat.AUDIO:
            raise ValueError(
                "Segmented sequence build is for video; audio is rendered once as a continuous master"
            )
        return self

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.EXPORT


class ExportHighlightsCommand(CommandModel):
    command_type: Literal["export_highlights"] = "export_highlights"
    sequence_id: NonEmptyText
    candidate_ids: list[NonEmptyText] = Field(min_length=1)
    output_dir: NonEmptyText
    preset: ExportPreset | None = None
    burn_subtitles: bool = True

    @field_validator("candidate_ids")
    @classmethod
    def unique_candidate_ids(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("批量导出不能包含重复的高光候选")
        return values

    def validate_for_execution(self) -> None:
        if self.preset is not None and self.preset.format == ExportFormat.AUDIO:
            raise ValueError("高光批量导出必须使用视频预设")

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.EXPORT


class RenderWebClipCommand(CommandModel):
    command_type: Literal["render_web_clip"] = "render_web_clip"
    sequence_id: NonEmptyText
    clip_id: NonEmptyText

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.WEB_RENDER


class ExportWebClipCommand(CommandModel):
    command_type: Literal["export_web_clip"] = "export_web_clip"
    sequence_id: NonEmptyText
    clip_id: NonEmptyText
    output_path: NonEmptyText
    format: WebExportFormat
    time_ms: int = Field(default=0, ge=0)
    background: NonEmptyText = "#000000"
    overwrite: bool = False

    def validate_for_execution(self) -> None:
        require_web_export_destination(
            self.output_path,
            self.format,
        )

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.WEB_RENDER


class TranscribeSequenceCommand(CommandModel):
    command_type: Literal["transcribe_sequence"] = "transcribe_sequence"
    plan: TranscriptionPlan

    @property
    def sequence_id(self) -> str:
        return self.plan.sequence_id

    def validate_for_execution(self) -> None:
        if not self.plan.sources or self.plan.recognition_frames <= 0:
            raise ValueError("转录计划没有可识别的源音频区间")

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.TRANSCRIBE


class DiagnosticsBundleCommand(CommandModel):
    command_type: Literal["diagnostics_bundle"] = "diagnostics_bundle"
    output_path: NonEmptyText
    task_ids: list[NonEmptyText] = Field(default_factory=list)
    overwrite: bool = False

    @field_validator("task_ids")
    @classmethod
    def unique_task_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Diagnostic task identifiers must be unique")
        return values

    def validate_for_execution(self) -> None:
        output = Path(self.output_path)
        if not output.is_absolute():
            raise ValueError("诊断包输出路径必须是绝对路径")
        if output.suffix.lower() != ".zip":
            raise ValueError("诊断包输出路径必须使用 .zip 扩展名")

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.DIAGNOSTICS


class TranslateDocumentCommand(CommandModel):
    command_type: Literal["translate_document"] = "translate_document"
    document_id: NonEmptyText
    target_language: NonEmptyText
    mode: TranslationMode = "standard"

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.TRANSLATE


class TranslateSegmentsCommand(CommandModel):
    command_type: Literal["translate_segments"] = "translate_segments"
    document_id: NonEmptyText
    segment_ids: list[NonEmptyText] = Field(min_length=1)
    target_document_id: NonEmptyText | None = None
    target_language: NonEmptyText
    mode: TranslationMode = "standard"

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.TRANSLATE


class AnalyzeHighlightsCommand(CommandModel):
    command_type: Literal["analyze_highlights"] = "analyze_highlights"
    document_id: NonEmptyText

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.HIGHLIGHT


class PrepareDubbingCommand(CommandModel):
    command_type: Literal["prepare_dubbing"] = "prepare_dubbing"
    sequence_id: NonEmptyText
    source_document_id: NonEmptyText
    target_language: NonEmptyText = "zh_CN"
    target_document_id: NonEmptyText | None = None
    settings: DubbingSettings = Field(default_factory=DubbingSettings)

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.DUBBING


class SynthesizeDubbingCommand(CommandModel):
    command_type: Literal["synthesize_dubbing"] = "synthesize_dubbing"
    sequence_id: NonEmptyText
    session_id: NonEmptyText
    utterance_ids: list[NonEmptyText] = Field(default_factory=list)
    regenerate: bool = False

    @field_validator("utterance_ids")
    @classmethod
    def unique_utterance_ids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("Dubbing utterance identifiers must be unique")
        return values

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.DUBBING


class CommitDubbingCommand(CommandModel):
    command_type: Literal["commit_dubbing"] = "commit_dubbing"
    sequence_id: NonEmptyText
    session_id: NonEmptyText
    track_name: NonEmptyText = "中文配音"
    mute_source_dialogue: bool = True

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.DUBBING


class AnalyzeDownloadCommand(CommandModel):
    command_type: Literal["analyze_download"] = "analyze_download"
    url: NonEmptyText

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.ANALYZE


class AnalyzeSequenceBoundsCommand(CommandModel):
    command_type: Literal["analyze_sequence_bounds"] = "analyze_sequence_bounds"
    sequence_id: NonEmptyText
    snapshot_hash: NonEmptyText

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.ANALYZE


class AnalyzeLoudnessCommand(CommandModel):
    command_type: Literal["analyze_loudness"] = "analyze_loudness"
    sequence_id: NonEmptyText

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.ANALYZE


class AnalyzeScenesCommand(CommandModel):
    command_type: Literal["analyze_scenes"] = "analyze_scenes"
    sequence_id: NonEmptyText
    clip_id: NonEmptyText
    threshold: float = Field(default=0.35, ge=0.05, le=0.95)

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.ANALYZE


class TrackSubjectCommand(CommandModel):
    command_type: Literal["track_subject"] = "track_subject"
    sequence_id: NonEmptyText
    clip_id: NonEmptyText
    mode: Literal["auto_reframe", "subject_tracking"]

    @property
    def task_kind(self) -> TaskKind:
        return TaskKind.ANALYZE


type TaskCommand = Annotated[
    ImportAssetCommand
    | GenerateProxyCommand
    | GenerateWaveformCommand
    | DownloadMediaCommand
    | ExportSequenceCommand
    | BuildSequenceCommand
    | ExportHighlightsCommand
    | RenderWebClipCommand
    | ExportWebClipCommand
    | TranscribeSequenceCommand
    | DiagnosticsBundleCommand
    | TranslateDocumentCommand
    | TranslateSegmentsCommand
    | PrepareDubbingCommand
    | SynthesizeDubbingCommand
    | CommitDubbingCommand
    | AnalyzeHighlightsCommand
    | AnalyzeDownloadCommand
    | AnalyzeSequenceBoundsCommand
    | AnalyzeLoudnessCommand
    | AnalyzeScenesCommand
    | TrackSubjectCommand,
    Field(discriminator="command_type"),
]
