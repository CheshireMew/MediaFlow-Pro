from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from mediaflow.automation import diagnostics_operations as diagnostics
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
HistoryMode = Literal["reversible", "non_undoable"]
IdempotencyPolicy = Literal["none", "optional"]
OperationHandler = Callable[[OperationContext], Any]


@dataclass(frozen=True, slots=True)
class OperationDefinition:
    arguments_model: type[DomainModel]
    result_model: type[DomainModel]
    project_access: ProjectAccess
    execution_mode: ExecutionMode
    history_mode: HistoryMode
    required_capabilities: tuple[str, ...]
    handler: OperationHandler

    @property
    def idempotency(self) -> IdempotencyPolicy:
        return "optional" if self.project_access in {"create", "write"} else "none"

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

    def write_set(self, operation: str, arguments: dict[str, Any]) -> list[str]:
        if self.project_access not in {"create", "write"}:
            return []
        return _operation_write_set(operation, arguments)

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
    reversible: bool = False,
    capabilities: tuple[str, ...] = ("project-editing",),
) -> OperationDefinition:
    return OperationDefinition(
        arguments_model=arguments_model,
        result_model=result_model,
        project_access=project_access,
        execution_mode="task" if task_backed else "atomic",
        history_mode="reversible" if reversible else "non_undoable",
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
    reversible: bool = False,
    capabilities: tuple[str, ...] = ("project-editing",),
) -> OperationDefinition:
    return _operation(
        arguments_model,
        result_model,
        "write",
        handler,
        task_backed=task_backed,
        reversible=reversible,
        capabilities=capabilities,
    )


WEB = ("project-editing", "editable-web-media")


def _path_value(value: Any, fallback: str = "main") -> str:
    text = str(value or fallback)
    return text.replace("~", "~0").replace("/", "~1")


def _field_paths(root: str, values: Any) -> list[str]:
    if isinstance(values, DomainModel):
        values = values.model_dump(mode="python", exclude_unset=True)
    if not isinstance(values, dict) or not values:
        return [root]
    return [f"{root}/{_path_value(name)}" for name in sorted(values)]


def _operation_write_set(operation: str, arguments: dict[str, Any]) -> list[str]:
    sequence = _path_value(arguments.get("sequence_id"))
    if operation == "project.create":
        return ["/project"]
    if operation in {"project.upgrade", "project.version.restore"}:
        return ["/project"]
    if operation == "project.version.create":
        return ["/project/versions"]
    if operation.startswith("timeline.transition."):
        transition_id = arguments.get("transition_id") or "new"
        return [f"/sequences/{sequence}/transitions/{_path_value(transition_id)}"]
    if operation.startswith("timeline.marker."):
        marker_id = arguments.get("marker_id") or "new"
        return [f"/sequences/{sequence}/markers/{_path_value(marker_id)}"]
    if operation == "asset.import":
        return ["/assets"]
    if operation == "sequence.short.create":
        return ["/sequences"]
    if operation == "timeline.portable.import":
        return [
            "/assets",
            f"/sequences/{sequence}",
            f"/sequences/{sequence}/subtitles",
        ]
    if operation == "timeline.track.add":
        return [f"/sequences/{sequence}/tracks"]
    if operation == "subtitle.track.style.update":
        return [f"/sequences/{sequence}/tracks/{_path_value(arguments.get('track_id'))}/subtitle-style"]
    if operation in {"timeline.clip.add", "timeline.clip.batch.add", "timeline.clip.copy"}:
        return [f"/sequences/{sequence}/clips"]
    if operation == "timeline.clip.freeze.add":
        return [f"/sequences/{sequence}/clips"]
    if operation.startswith("timeline.clip."):
        clip_ids = arguments.get("clip_ids") or [arguments.get("clip_id")]
        roots = [f"/sequences/{sequence}/clips/{_path_value(clip_id)}" for clip_id in clip_ids if clip_id]
        if operation in {"timeline.clip.delete", "timeline.clip.split"}:
            return roots
        field = {
            "timeline.clip.move": "placement",
            "timeline.clip.transform": "transform",
            "timeline.clip.audio": "audio",
            "timeline.clip.source.replace": "asset_id",
        }.get(operation)
        if field:
            return [f"{root}/{field}" for root in roots]
        if ".effect." in operation:
            effect_id = arguments.get("effect_id")
            suffix = f"/{_path_value(effect_id)}" if effect_id else ""
            return [f"{root}/effects{suffix}" for root in roots]
        return roots or [f"/sequences/{sequence}/clips"]
    if operation == "subtitle.segment.update":
        root = (
            f"/subtitles/documents/{_path_value(arguments.get('document_id'))}"
            f"/segments/{_path_value(arguments.get('segment_id'))}"
        )
        fields = {
            name: value for name, value in arguments.items() if name not in {"document_id", "segment_id"}
        }
        return _field_paths(root, fields)
    if operation == "transcript.edit.apply":
        return [f"/sequences/{sequence}/transcript"]
    if operation == "transcript.sequence.transcribe":
        return [f"/tasks/transcript-sequence:{sequence}"]
    if operation == "diagnostics.bundle.create":
        return ["/tasks/diagnostics-bundle"]
    if operation == "audio.bus.update":
        root = f"/audio/buses/{_path_value(arguments.get('bus_id'))}"
        return _field_paths(root, arguments.get("changes"))
    if operation == "audio.effect.save":
        effect = arguments.get("effect")
        if isinstance(effect, DomainModel):
            effect_id = effect.model_dump().get("id")
        elif isinstance(effect, dict):
            effect_id = effect.get("id")
        else:
            effect_id = None
        return [f"/audio/effects/{_path_value(effect_id, 'new')}"]
    if operation == "audio.effect.remove":
        return [f"/audio/effects/{_path_value(arguments.get('effect_id'))}"]
    if operation.startswith("task."):
        return [f"/tasks/{_path_value(arguments.get('task_id'), 'new')}"]
    if operation.startswith(("preview.", "export.")):
        return [f"/tasks/{operation.replace('.', '-')}:{sequence}"]
    if operation == "web.import":
        return ["/assets/web"]
    if operation.startswith("web.clip."):
        root = f"/web/clips/{_path_value(arguments.get('clip_id'))}"
        web_suffix = {
            "web.clip.variant.select": "variant",
            "web.clip.theme.update": "theme",
            "web.clip.data.update": "data",
            "web.clip.data.snapshot": "data",
            "web.clip.lock.update": "locks",
            "web.clip.parameter.lock.update": "parameter-locks",
        }.get(operation)
        if web_suffix:
            return [f"{root}/{web_suffix}"]
        if "keyframe" in operation:
            return [f"{root}/keyframes/{_path_value(arguments.get('path'))}"]
        if "parameter" in operation:
            return [f"{root}/parameters/{_path_value(arguments.get('path'))}"]
        return [root]
    if operation.startswith("web.batch."):
        return ["/web/batches"]
    if operation == "web.asset.rebind.commit":
        return [f"/assets/{_path_value(arguments.get('asset_id'))}/web-package"]
    return [f"/operations/{operation.replace('.', '/')}"]


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
    "project.changes.list": _read(
        models.ProjectChangesListArguments,
        models.ProjectChangesListResult,
        project.list_changes,
        capabilities=("project-editing", "asynchronous-project-handoff"),
    ),
    "project.handoff.inspect": _read(
        models.ProjectHandoffInspectArguments,
        models.ProjectHandoffInspectResult,
        project.inspect_handoff,
        capabilities=("project-editing", "asynchronous-project-handoff"),
    ),
    "diagnostics.bundle.create": _write(
        models.DiagnosticsBundleArguments,
        models.TaskReceiptResult,
        diagnostics.create_bundle,
        task_backed=True,
    ),
    "asset.list": _read(
        models.EmptyArguments,
        models.AssetListResult,
        project.list_assets,
    ),
    "asset.import": _write(
        models.AssetImportArguments,
        models.TaskReceiptResult,
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
    "timeline.portable.inspect": _read(
        models.PortableTimelineArguments,
        models.PortableTimelineInspectResult,
        timeline.inspect_portable_timeline,
        capabilities=("project-editing", "portable-timeline-import"),
    ),
    "timeline.portable.import": _write(
        models.PortableTimelineArguments,
        models.PortableTimelineImportResult,
        timeline.import_portable_timeline,
        capabilities=(
            "project-editing",
            "portable-timeline-import",
            "ffmpeg",
            "ffprobe",
        ),
    ),
    "timeline.track.add": _write(
        models.TimelineTrackAddArguments,
        models.TrackResult,
        timeline.add_track,
        reversible=True,
    ),
    "timeline.clip.add": _write(
        models.TimelineClipAddArguments,
        models.ClipResult,
        timeline.add_clip,
        reversible=True,
    ),
    "timeline.clip.batch.add": _write(
        models.TimelineClipBatchAddArguments,
        models.ClipsResult,
        timeline.add_clips,
        reversible=True,
    ),
    "timeline.clip.move": _write(
        models.TimelineClipMoveArguments,
        models.ClipResult,
        timeline.move_clip,
        reversible=True,
    ),
    "timeline.clip.copy": _write(
        models.TimelineClipMoveArguments,
        models.ClipResult,
        timeline.copy_clip,
        reversible=True,
    ),
    "timeline.clip.split": _write(
        models.TimelineClipSplitArguments,
        models.ClipsResult,
        timeline.split_clip,
        reversible=True,
    ),
    "timeline.clip.delete": _write(
        models.TimelineClipDeleteArguments,
        models.TimelineResult,
        timeline.delete_clips,
        reversible=True,
    ),
    "timeline.clip.freeze.add": _write(
        models.TimelineFreezeClipAddArguments,
        models.ClipResult,
        timeline.add_freeze_clip,
        reversible=True,
        capabilities=("project-editing", "native-freeze-clips"),
    ),
    "timeline.transition.add": _write(
        models.TimelineTransitionAddArguments,
        models.TransitionResult,
        timeline.add_transition,
        reversible=True,
    ),
    "timeline.transition.update": _write(
        models.TimelineTransitionUpdateArguments,
        models.TransitionResult,
        timeline.update_transition,
        reversible=True,
    ),
    "timeline.transition.remove": _write(
        models.TimelineTransitionRemoveArguments,
        models.RemovedResult,
        timeline.remove_transition,
        reversible=True,
    ),
    "timeline.marker.add": _write(
        models.TimelineMarkerAddArguments,
        models.MarkerResult,
        timeline.add_marker,
        reversible=True,
        capabilities=("project-editing", "semantic-timeline-markers"),
    ),
    "timeline.marker.update": _write(
        models.TimelineMarkerUpdateArguments,
        models.MarkerResult,
        timeline.update_marker,
        reversible=True,
        capabilities=("project-editing", "semantic-timeline-markers"),
    ),
    "timeline.marker.remove": _write(
        models.TimelineMarkerRemoveArguments,
        models.RemovedResult,
        timeline.remove_marker,
        reversible=True,
        capabilities=("project-editing", "semantic-timeline-markers"),
    ),
    "subtitle.track.style.update": _write(
        models.SubtitleTrackStyleUpdateArguments,
        models.TrackResult,
        timeline.update_subtitle_track_style,
        reversible=True,
    ),
    "timeline.clip.transform": _write(
        models.TimelineClipTransformArguments,
        models.ClipResult,
        timeline.transform_clip,
        reversible=True,
    ),
    "timeline.clip.audio": _write(
        models.TimelineClipAudioArguments,
        models.ClipResult,
        timeline.update_clip_audio,
        reversible=True,
    ),
    "timeline.clip.source.replace": _write(
        models.TimelineClipReplaceSourceArguments,
        models.ClipResult,
        timeline.replace_clip_source,
        reversible=True,
    ),
    "timeline.clip.effect.add": _write(
        models.TimelineClipVisualEffectAddArguments,
        models.ClipResult,
        timeline.add_clip_visual_effect,
        reversible=True,
    ),
    "timeline.clip.effect.update": _write(
        models.TimelineClipVisualEffectUpdateArguments,
        models.ClipResult,
        timeline.update_clip_visual_effect,
        reversible=True,
    ),
    "timeline.clip.effect.move": _write(
        models.TimelineClipVisualEffectMoveArguments,
        models.ClipResult,
        timeline.move_clip_visual_effect,
        reversible=True,
    ),
    "timeline.clip.effect.remove": _write(
        models.TimelineClipVisualEffectRemoveArguments,
        models.ClipResult,
        timeline.remove_clip_visual_effect,
        reversible=True,
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
        reversible=True,
    ),
    "transcript.get": _read(
        models.TranscriptGetArguments,
        models.TranscriptResult,
        language_audio.get_transcript,
    ),
    "transcript.sequence.transcribe": _write(
        models.TranscriptSequenceTranscribeArguments,
        models.TaskReceiptResult,
        language_audio.transcribe_sequence,
        task_backed=True,
        capabilities=("project-editing", "faster-whisper-xxl"),
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
        reversible=True,
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
        models.TaskReceiptResult,
        timeline.export_sequence,
        task_backed=True,
        capabilities=("project-editing", "mlt", "ffmpeg", "ffprobe"),
    ),
    "export.sequence.build": _write(
        models.BuildSequenceArguments,
        models.TaskReceiptResult,
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
    "task.get": _read(
        models.TaskStatusArguments,
        models.TaskStatusResult,
        tasks.get_task,
    ),
    "task.cancel": _write(
        models.TaskStatusArguments,
        models.TaskStatusResult,
        tasks.cancel_task,
    ),
    "task.wait": _read(
        models.TaskWaitArguments,
        models.TaskStatusResult,
        tasks.wait_for_task,
    ),
    "task.start": _write(
        models.TaskStartArguments,
        models.TaskReceiptResult,
        tasks.start_task,
        task_backed=True,
    ),
    "task.resume": _write(
        models.TaskResumeArguments,
        models.TaskReceiptResult,
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
        models.TaskReceiptResult,
        web.render_web_clip,
        task_backed=True,
        capabilities=(*WEB, "chromium", "ffmpeg", "ffprobe"),
    ),
    "web.clip.export": _write(
        models.WebClipExportArguments,
        models.TaskReceiptResult,
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
