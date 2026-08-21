from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import mediaflow.automation.operation_models as models
from mediaflow.application.project_mutation_planning import plan_automation_project_mutation
from mediaflow.automation.operation_context import OperationContext
from mediaflow.domain.collaboration import ProjectMutationPlan
from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.runtime_capabilities import CAPABILITY_IDS
from mediaflow.domain.speech import (
    SpeechSynthesisResult,
    SpeechSynthesizeArguments,
    SpeechTranscribeArguments,
    SpeechTranscriptionResult,
)

if TYPE_CHECKING:
    from mediaflow.composition import EditorProject

ProjectAccess = Literal["none", "create", "read", "write"]
ExecutionMode = Literal["atomic", "task"]
HistoryMode = Literal["reversible", "non_undoable"]
IdempotencyPolicy = Literal["none", "optional"]
OperationHandler = Callable[[OperationContext], Any]


class _LazyOperationModule:
    """Keep operation implementations out of client and desktop processes."""

    __slots__ = ("_module_name",)

    def __init__(self, module_name: str) -> None:
        self._module_name = module_name

    def __getattr__(self, handler_name: str) -> OperationHandler:
        module_name = self._module_name

        def invoke(context: OperationContext) -> Any:
            module = importlib.import_module(module_name)
            handler: OperationHandler = getattr(module, handler_name)
            return handler(context)

        invoke.__name__ = handler_name
        invoke.__qualname__ = f"{module_name}.{handler_name}"
        return invoke


diagnostics = _LazyOperationModule("mediaflow.automation.diagnostics_operations")
dubbing = _LazyOperationModule("mediaflow.automation.dubbing_operations")
language_audio = _LazyOperationModule("mediaflow.automation.language_audio_operations")
media_quality = _LazyOperationModule("mediaflow.automation.media_quality_operations")
project = _LazyOperationModule("mediaflow.automation.project_operations")
resources = _LazyOperationModule("mediaflow.automation.resource_operations")
runtime = _LazyOperationModule("mediaflow.automation.runtime_operations")
speech = _LazyOperationModule("mediaflow.automation.speech_operations")
tasks = _LazyOperationModule("mediaflow.automation.task_operations")
timeline = _LazyOperationModule("mediaflow.automation.timeline_operations")
web = _LazyOperationModule("mediaflow.automation.web_operations")


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

    def mutation_plan(
        self,
        operation: str,
        arguments: dict[str, Any],
        project: EditorProject,
    ) -> ProjectMutationPlan:
        if self.project_access not in {"create", "write"}:
            return ProjectMutationPlan.scoped([])
        return plan_automation_project_mutation(
            operation,
            arguments,
            default_sequence_id=project.get_project().main_sequence_id,
            project=project,
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


OPERATIONS: dict[str, OperationDefinition] = {
    "resource.catalog.search": _operation(
        models.MediaResourceSearchArguments,
        models.MediaResourceSearchResult,
        "none",
        resources.search_catalog,
        capabilities=(),
    ),
    "runtime.inspect": _operation(
        models.EmptyArguments,
        models.RuntimeInspectionResult,
        "none",
        runtime.inspect_runtime,
        capabilities=(),
    ),
    "speech.transcribe": _operation(
        SpeechTranscribeArguments,
        SpeechTranscriptionResult,
        "none",
        speech.transcribe,
        capabilities=("faster-whisper-xxl",),
    ),
    "speech.synthesize": _operation(
        SpeechSynthesizeArguments,
        SpeechSynthesisResult,
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
    "project.context.inspect": _read(
        models.ProjectContextInspectArguments,
        models.ProjectContextInspectResult,
        project.inspect_context,
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
    "dubbing.session.list": _read(
        models.DubbingListArguments,
        models.DubbingSessionListResult,
        dubbing.list_sessions,
    ),
    "dubbing.session.get": _read(
        models.DubbingSessionArguments,
        models.DubbingSessionResult,
        dubbing.get_session,
    ),
    "dubbing.prepare": _write(
        models.DubbingPrepareArguments,
        models.TaskReceiptResult,
        dubbing.prepare,
        task_backed=True,
        capabilities=(
            "project-editing",
            "speaker-diarization",
            "mlt",
            "ffmpeg",
            "ffprobe",
        ),
    ),
    "dubbing.synthesize": _write(
        models.DubbingSynthesizeArguments,
        models.TaskReceiptResult,
        dubbing.synthesize,
        task_backed=True,
        capabilities=("project-editing", "gpt-sovits-v2pro", "ffmpeg"),
    ),
    "dubbing.commit": _write(
        models.DubbingCommitArguments,
        models.TaskReceiptResult,
        dubbing.commit,
        task_backed=True,
        capabilities=("project-editing", "ffprobe"),
    ),
    "dubbing.speaker.update": _write(
        models.DubbingSpeakerUpdateArguments,
        models.DubbingSessionResult,
        dubbing.update_speaker,
    ),
    "dubbing.reference.update": _write(
        models.DubbingReferenceUpdateArguments,
        models.DubbingSessionResult,
        dubbing.update_reference,
    ),
    "dubbing.utterance.update": _write(
        models.DubbingUtteranceUpdateArguments,
        models.DubbingSessionResult,
        dubbing.update_utterance,
    ),
    "transcript.get": _read(
        models.TranscriptGetArguments,
        models.TranscriptResult,
        language_audio.get_transcript,
    ),
    "script.inspect": _read(
        models.TranscriptGetArguments,
        models.ScriptInspectResult,
        language_audio.inspect_script,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "script.segment.update": _write(
        models.ScriptSegmentUpdateArguments,
        models.SubtitleSegmentResult,
        language_audio.update_script_segment,
        reversible=True,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "script.segment.split": _write(
        models.ScriptSegmentSplitArguments,
        models.ScriptSegmentSplitResult,
        language_audio.split_script_segment,
        reversible=True,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "script.segment.merge": _write(
        models.ScriptSegmentMergeArguments,
        models.SubtitleSegmentResult,
        language_audio.merge_script_segments,
        reversible=True,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "script.segment.move": _write(
        models.ScriptSegmentMoveArguments,
        models.ScriptTimelineEditResult,
        language_audio.move_script_segment,
        reversible=True,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "script.gap.close": _write(
        models.ScriptGapCloseArguments,
        models.ScriptTimelineEditResult,
        language_audio.close_script_gap,
        reversible=True,
        capabilities=("project-editing", "transcript-edit-plans"),
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
    "preview.frames.render": _read(
        models.PreviewFramesRenderArguments,
        models.PreviewFramesRenderResult,
        timeline.render_preview_frames,
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
