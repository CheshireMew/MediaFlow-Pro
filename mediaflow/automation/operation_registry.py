from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from mediaflow.automation import language_audio_operations as language_audio
from mediaflow.automation import media_quality_operations as media_quality
from mediaflow.automation import operation_models as models
from mediaflow.automation import project_operations as project
from mediaflow.automation import runtime_operations as runtime
from mediaflow.automation import speech_operations as speech
from mediaflow.automation import task_operations as tasks
from mediaflow.automation import timeline_operations as timeline
from mediaflow.automation import web_operations as web
from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.runtime_capabilities import CAPABILITY_IDS

ProjectAccess = Literal["none", "create", "read", "write"]
ExecutionMode = Literal["atomic", "task"]
IdempotencyPolicy = Literal["none", "optional"]
OperationHandler = Callable[[OperationContext], Any]


@dataclass(frozen=True, slots=True)
class OperationDefinition:
    arguments_model: type[DomainModel]
    result_model: type[DomainModel]
    project_access: ProjectAccess
    execution_mode: ExecutionMode
    required_capabilities: tuple[str, ...]
    handler: OperationHandler

    @property
    def idempotency(self) -> IdempotencyPolicy:
        return (
            "optional"
            if self.project_access in {"create", "write"}
            else "none"
        )

    def validate_arguments(self, values: dict[str, Any]) -> dict[str, Any]:
        return self.arguments_model.model_validate(values).model_dump(
            mode="python",
            exclude_unset=True,
            exclude_computed_fields=True,
        )

    def validate_result(self, values: Any) -> dict[str, Any]:
        return self.result_model.model_validate(values).model_dump(
            mode="json",
            exclude_computed_fields=True,
        )

    def __post_init__(self) -> None:
        unknown = set(self.required_capabilities) - CAPABILITY_IDS
        if unknown:
            raise ValueError(f"Unknown operation capabilities: {sorted(unknown)}")


def _operation(
    arguments_model: type[DomainModel],
    result_model: type[DomainModel],
    project_access: ProjectAccess,
    handler: OperationHandler,
    *,
    task_backed: bool = False,
    capabilities: tuple[str, ...] = ("project-editing",),
) -> OperationDefinition:
    return OperationDefinition(
        arguments_model=arguments_model,
        result_model=result_model,
        project_access=project_access,
        execution_mode="task" if task_backed else "atomic",
        required_capabilities=capabilities,
        handler=handler,
    )


def _read(
    arguments_model: type[DomainModel],
    result_model: type[DomainModel],
    handler: OperationHandler,
    *,
    capabilities: tuple[str, ...] = ("project-editing",),
) -> OperationDefinition:
    return _operation(
        arguments_model,
        result_model,
        "read",
        handler,
        capabilities=capabilities,
    )


def _write(
    arguments_model: type[DomainModel],
    result_model: type[DomainModel],
    handler: OperationHandler,
    *,
    task_backed: bool = False,
    capabilities: tuple[str, ...] = ("project-editing",),
) -> OperationDefinition:
    return _operation(
        arguments_model,
        result_model,
        "write",
        handler,
        task_backed=task_backed,
        capabilities=capabilities,
    )


WEB = ("project-editing", "editable-web-media")

OPERATIONS: dict[str, OperationDefinition] = {
    "runtime.inspect": _operation(
        models.EmptyArguments,
        models.RuntimeInspectionResult,
        "none",
        runtime.inspect_runtime,
        capabilities=(),
    ),
    "speech.transcribe": _operation(
        models.SpeechTranscribeArguments,
        models.SpeechTranscriptionResult,
        "none",
        speech.transcribe,
        capabilities=("faster-whisper-xxl",),
    ),
    "speech.synthesize": _operation(
        models.SpeechSynthesizeArguments,
        models.SpeechSynthesisResult,
        "none",
        speech.synthesize,
        capabilities=("gpt-sovits-v2pro",),
    ),
    "quality.reference.compare": _operation(
        models.ReferenceComparisonArguments,
        models.ReferenceComparisonOperationResult,
        "none",
        media_quality.compare_reference,
        capabilities=("reference-video-comparison", "ffmpeg", "ffprobe"),
    ),
    "project.create": _operation(
        models.ProjectCreateArguments,
        models.ProjectSnapshotResult,
        "create",
        project.create_project,
    ),
    "project.inspect": _read(
        models.EmptyArguments,
        models.ProjectSnapshotResult,
        project.inspect_project,
    ),
    "project.upgrade": _write(
        models.EmptyArguments,
        models.ProjectUpgradeResult,
        project.upgrade_project,
    ),
    "project.version.list": _read(
        models.EmptyArguments,
        models.ProjectVersionListResult,
        project.list_versions,
    ),
    "project.version.create": _write(
        models.ProjectVersionCreateArguments,
        models.ProjectVersionResult,
        project.create_version,
    ),
    "project.version.restore": _write(
        models.ProjectVersionRestoreArguments,
        models.ProjectVersionRestoreResult,
        project.restore_version,
    ),
    "asset.list": _read(
        models.EmptyArguments,
        models.AssetListResult,
        project.list_assets,
    ),
    "asset.import": _write(
        models.AssetImportArguments,
        models.AssetImportResult,
        project.import_asset,
        task_backed=True,
        capabilities=("project-editing", "ffprobe", "ffmpeg"),
    ),
    "sequence.short.create": _write(
        models.SequenceShortCreateArguments,
        models.SequenceResult,
        project.create_short_sequence,
    ),
    "timeline.get": _read(
        models.SequenceArguments,
        models.TimelineResult,
        timeline.get_timeline,
    ),
    "timeline.track.add": _write(
        models.TimelineTrackAddArguments,
        models.TrackResult,
        timeline.add_track,
    ),
    "timeline.clip.add": _write(
        models.TimelineClipAddArguments,
        models.ClipResult,
        timeline.add_clip,
    ),
    "timeline.clip.batch.add": _write(
        models.TimelineClipBatchAddArguments,
        models.ClipsResult,
        timeline.add_clips,
    ),
    "timeline.clip.move": _write(
        models.TimelineClipMoveArguments,
        models.ClipResult,
        timeline.move_clip,
    ),
    "timeline.clip.copy": _write(
        models.TimelineClipMoveArguments,
        models.ClipResult,
        timeline.copy_clip,
    ),
    "timeline.clip.split": _write(
        models.TimelineClipSplitArguments,
        models.ClipsResult,
        timeline.split_clip,
    ),
    "timeline.clip.delete": _write(
        models.TimelineClipDeleteArguments,
        models.TimelineResult,
        timeline.delete_clips,
    ),
    "timeline.clip.transform": _write(
        models.TimelineClipTransformArguments,
        models.ClipResult,
        timeline.transform_clip,
    ),
    "timeline.clip.audio": _write(
        models.TimelineClipAudioArguments,
        models.ClipResult,
        timeline.update_clip_audio,
    ),
    "timeline.clip.source.replace": _write(
        models.TimelineClipReplaceSourceArguments,
        models.ClipResult,
        timeline.replace_clip_source,
    ),
    "timeline.clip.effect.add": _write(
        models.TimelineClipVisualEffectAddArguments,
        models.ClipResult,
        timeline.add_clip_visual_effect,
    ),
    "timeline.clip.effect.update": _write(
        models.TimelineClipVisualEffectUpdateArguments,
        models.ClipResult,
        timeline.update_clip_visual_effect,
    ),
    "timeline.clip.effect.move": _write(
        models.TimelineClipVisualEffectMoveArguments,
        models.ClipResult,
        timeline.move_clip_visual_effect,
    ),
    "timeline.clip.effect.remove": _write(
        models.TimelineClipVisualEffectRemoveArguments,
        models.ClipResult,
        timeline.remove_clip_visual_effect,
    ),
    "timeline.undo": _write(
        models.SequenceArguments,
        models.TimelineResult,
        timeline.undo,
    ),
    "timeline.redo": _write(
        models.SequenceArguments,
        models.TimelineResult,
        timeline.redo,
    ),
    "subtitle.list": _read(
        models.SequenceArguments,
        models.SubtitleListResult,
        language_audio.list_subtitles,
    ),
    "subtitle.segment.update": _write(
        models.SubtitleSegmentUpdateArguments,
        models.SubtitleSegmentResult,
        language_audio.update_subtitle_segment,
    ),
    "transcript.get": _read(
        models.TranscriptGetArguments,
        models.TranscriptResult,
        language_audio.get_transcript,
    ),
    "transcript.edit.preview": _read(
        models.TranscriptEditPreviewArguments,
        models.TranscriptEditPlanResult,
        language_audio.preview_transcript_edit,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "transcript.edit.apply": _write(
        models.TranscriptEditApplyArguments,
        models.TranscriptEditResultDocument,
        language_audio.apply_transcript_edit,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "audio.inspect": _read(
        models.SequenceArguments,
        models.AudioInspectResult,
        language_audio.inspect_audio,
    ),
    "audio.bus.update": _write(
        models.AudioBusUpdateArguments,
        models.AudioBusResult,
        language_audio.update_audio_bus,
    ),
    "audio.effect.save": _write(
        models.AudioEffectSaveArguments,
        models.AudioEffectResult,
        language_audio.save_audio_effect,
    ),
    "audio.effect.remove": _write(
        models.AudioEffectRemoveArguments,
        models.RemovedResult,
        language_audio.remove_audio_effect,
    ),
    "preview.render": _write(
        models.PreviewRenderArguments,
        models.PreviewRenderResult,
        timeline.render_preview,
    ),
    "export.sequence": _write(
        models.ExportSequenceArguments,
        models.TaskCompletionResult,
        timeline.export_sequence,
        task_backed=True,
        capabilities=("project-editing", "mlt", "ffmpeg", "ffprobe"),
    ),
    "export.sequence.build": _write(
        models.BuildSequenceArguments,
        models.TaskCompletionResult,
        timeline.build_sequence,
        task_backed=True,
        capabilities=("project-editing", "mlt", "ffmpeg", "ffprobe"),
    ),
    "export.fcpxml": _write(
        models.ExportFcpxmlArguments,
        models.FcpxmlExportResult,
        timeline.export_fcpxml,
        capabilities=(
            "project-editing",
            "fcpxml-export",
            "chromium",
            "ffmpeg",
            "ffprobe",
        ),
    ),
    "task.list": _read(
        models.EmptyArguments,
        models.TaskListResult,
        tasks.list_tasks,
    ),
    "task.status": _read(
        models.TaskStatusArguments,
        models.TaskStatusResult,
        tasks.get_task,
    ),
    "task.start": _write(
        models.TaskStartArguments,
        models.TaskCompletionResult,
        tasks.start_task,
        task_backed=True,
    ),
    "task.resume": _write(
        models.TaskResumeArguments,
        models.TaskCompletionResult,
        tasks.resume_task,
        task_backed=True,
    ),
    "web.import": _write(
        models.WebImportArguments,
        models.WebImportResult,
        web.import_web,
        capabilities=(*WEB, "chromium"),
    ),
    "web.inspect": _read(
        models.WebInspectArguments,
        models.WebInspectResult,
        web.inspect_web,
        capabilities=WEB,
    ),
    "web.clip.get": _read(
        models.WebClipGetArguments,
        models.WebClipStateResult,
        web.get_web_clip,
        capabilities=WEB,
    ),
    "web.clip.edit.describe": _read(
        models.WebClipEditDescribeArguments,
        models.WebEditDocumentResult,
        web.describe_web_clip_editing,
        capabilities=WEB,
    ),
    "web.clip.update": _write(
        models.WebClipUpdateArguments,
        models.WebClipStateResult,
        web.update_web_clip,
        capabilities=WEB,
    ),
    "web.clip.diff": _read(
        models.WebClipUpdateArguments,
        models.WebStateDiffResult,
        web.diff_web_clip,
        capabilities=WEB,
    ),
    "web.clip.variant.select": _write(
        models.WebClipVariantSelectArguments,
        models.WebClipStateResult,
        web.select_variant,
        capabilities=(*WEB, "web-responsive-variants"),
    ),
    "web.clip.keyframe.set": _write(
        models.WebClipKeyframeSetArguments,
        models.WebClipStateResult,
        web.set_keyframe,
        capabilities=(*WEB, "web-keyframes"),
    ),
    "web.clip.keyframe.remove": _write(
        models.WebClipKeyframeRemoveArguments,
        models.WebClipStateResult,
        web.remove_keyframe,
        capabilities=(*WEB, "web-keyframes"),
    ),
    "web.clip.parameter.update": _write(
        models.WebParameterUpdateArguments,
        models.WebClipStateResult,
        web.update_parameter,
        capabilities=(*WEB, "web-parameters"),
    ),
    "web.clip.parameter.keyframe.set": _write(
        models.WebParameterKeyframeSetArguments,
        models.WebClipStateResult,
        web.set_parameter_keyframe,
        capabilities=(*WEB, "web-parameters", "web-keyframes"),
    ),
    "web.clip.parameter.keyframe.remove": _write(
        models.WebParameterKeyframeRemoveArguments,
        models.WebClipStateResult,
        web.remove_parameter_keyframe,
        capabilities=(*WEB, "web-parameters", "web-keyframes"),
    ),
    "web.clip.parameter.lock.update": _write(
        models.WebParameterLockUpdateArguments,
        models.WebClipStateResult,
        web.update_parameter_lock,
        capabilities=(*WEB, "web-parameters", "web-field-locks"),
    ),
    "web.clip.theme.update": _write(
        models.WebThemeUpdateArguments,
        models.WebClipStateResult,
        web.update_theme,
        capabilities=(*WEB, "web-themes"),
    ),
    "web.clip.data.update": _write(
        models.WebDataUpdateArguments,
        models.WebClipStateResult,
        web.update_data,
        capabilities=(*WEB, "web-data-snapshots"),
    ),
    "web.clip.data.snapshot": _write(
        models.WebDataSnapshotArguments,
        models.WebClipStateResult,
        web.snapshot_data,
        capabilities=(*WEB, "web-data-snapshots"),
    ),
    "web.clip.lock.update": _write(
        models.WebFieldLockUpdateArguments,
        models.WebClipStateResult,
        web.update_locks,
        capabilities=(*WEB, "web-field-locks"),
    ),
    "web.clip.render": _write(
        models.WebClipRenderArguments,
        models.TaskCompletionResult,
        web.render_web_clip,
        task_backed=True,
        capabilities=(*WEB, "chromium", "ffmpeg", "ffprobe"),
    ),
    "web.clip.export": _write(
        models.WebClipExportArguments,
        models.TaskCompletionResult,
        web.export_web_clip,
        task_backed=True,
        capabilities=(
            *WEB,
            "web-multi-format-export",
            "chromium",
            "ffmpeg",
            "ffprobe",
        ),
    ),
    "web.batch.create": _write(
        models.WebBatchCreateArguments,
        models.WebBatchResult,
        web.create_batch,
        capabilities=(*WEB, "web-batch-variants"),
    ),
    "web.asset.rebind.plan": _read(
        models.WebAssetRebindPlanArguments,
        models.WebRebindPlanResult,
        web.plan_rebind_asset,
        capabilities=(*WEB, "web-template-rebinding", "chromium"),
    ),
    "web.asset.rebind.commit": _write(
        models.WebAssetRebindCommitArguments,
        models.WebRebindCommitResult,
        web.commit_rebind_asset,
        capabilities=(*WEB, "web-template-rebinding", "chromium"),
    ),
}
