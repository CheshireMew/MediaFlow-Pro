from __future__ import annotations

from typing import Any

from mediaflow.domain.downloads import DownloadRequest
from mediaflow.domain.enums import ExportFormat, TaskKind, WorkflowStage
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.settings import default_media_root
from mediaflow.domain.task_commands import (
    AnalyzeDownloadCommand,
    AnalyzeHighlightsCommand,
    AnalyzeLoudnessCommand,
    AnalyzeSequenceBoundsCommand,
    DownloadMediaCommand,
    ExportHighlightsCommand,
    ExportSequenceCommand,
    GenerateProxyCommand,
    GenerateWaveformCommand,
    ImportAssetCommand,
    TaskCommand,
    TranscribeSequenceCommand,
    TranslateDocumentCommand,
    TranslateSegmentsCommand,
    WorkflowTaskLink,
)
from mediaflow.domain.translation import validate_translation_mode


def legacy_task_command(
    kind: str,
    parameters: dict[str, Any],
    *,
    sequence_id: str | None = None,
) -> TaskCommand:
    values = dict(parameters)
    workflow = _workflow_link(values)
    task_kind = TaskKind(kind)
    if task_kind == TaskKind.IMPORT:
        return ImportAssetCommand(
            source_path=str(values["source_path"]),
            purpose=str(values.get("purpose") or "media"),
            language=str(values.get("language") or "auto"),
            media_asset_id=values.get("media_asset_id") or None,
            workflow=workflow,
        )
    if task_kind == TaskKind.PROXY:
        return GenerateProxyCommand(
            asset_id=str(values["asset_id"]),
            reasons=[str(value) for value in values.get("reasons") or []],
            workflow=workflow,
        )
    if task_kind == TaskKind.WAVEFORM:
        return GenerateWaveformCommand(asset_id=str(values["asset_id"]), workflow=workflow)
    if task_kind == TaskKind.DOWNLOAD:
        request = dict(values["request"])
        if not str(request.get("output_directory") or "").strip():
            request["output_directory"] = default_media_root()
        return DownloadMediaCommand(
            request=DownloadRequest.model_validate(request),
            workflow=workflow,
        )
    if task_kind == TaskKind.EXPORT:
        preset = ExportPreset.model_validate(values["preset"]) if values.get("preset") else None
        if values.get("candidate_ids"):
            return ExportHighlightsCommand(
                sequence_id=str(values["sequence_id"]),
                candidate_ids=[str(value) for value in values["candidate_ids"]],
                output_dir=str(values["output_dir"]),
                preset=preset,
                burn_subtitles=bool(values.get("burn_subtitles", True)),
                workflow=workflow,
            )
        return ExportSequenceCommand(
            sequence_id=str(values["sequence_id"]),
            output_path=str(values["output_path"]),
            format=ExportFormat(str(values.get("format") or ExportFormat.H264.value)),
            preset=preset,
            workflow=workflow,
        )
    if task_kind == TaskKind.TRANSCRIBE:
        target_sequence_id = str(sequence_id or values.get("sequence_id") or "")
        if not target_sequence_id:
            raise ValueError("Persisted transcription task has no sequence")
        return TranscribeSequenceCommand(
            sequence_id=target_sequence_id,
            workflow=workflow,
        )
    if task_kind == TaskKind.TRANSLATE:
        common = {
            "document_id": str(values["document_id"]),
            "target_language": str(values["target_language"]),
            "mode": validate_translation_mode(str(values.get("mode") or "standard")),
            "workflow": workflow,
        }
        if values.get("segment_ids"):
            return TranslateSegmentsCommand(
                segment_ids=[str(value) for value in values["segment_ids"]],
                **common,
            )
        return TranslateDocumentCommand(**common)
    if task_kind == TaskKind.HIGHLIGHT:
        return AnalyzeHighlightsCommand(
            document_id=str(values["document_id"]),
            workflow=workflow,
        )
    if task_kind == TaskKind.ANALYZE:
        analysis = str(values.get("analysis") or "")
        if analysis == "download_url":
            return AnalyzeDownloadCommand(url=str(values["url"]), workflow=workflow)
        if analysis == "sequence_bounds":
            return AnalyzeSequenceBoundsCommand(
                sequence_id=str(values["sequence_id"]),
                snapshot_hash=str(values["snapshot_hash"]),
                workflow=workflow,
            )
        if analysis == "loudness":
            return AnalyzeLoudnessCommand(
                sequence_id=str(values["sequence_id"]),
                workflow=workflow,
            )
        raise ValueError(f"Unknown persisted analysis task: {analysis}")
    raise ValueError(f"Unsupported persisted task kind: {task_kind.value}")


def _workflow_link(values: dict[str, Any]) -> WorkflowTaskLink | None:
    run_id = str(values.pop("workflow_run_id", "") or "")
    stage = str(values.pop("workflow_stage", "") or "")
    if not run_id and not stage:
        return None
    if not run_id or not stage:
        raise ValueError("Persisted task has an incomplete workflow link")
    return WorkflowTaskLink(run_id=run_id, stage=WorkflowStage(stage))
