from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import mediaflow.automation.operation_delivery_models as delivery_models
import mediaflow.automation.operation_language_models as language_models
import mediaflow.automation.operation_model_common as common_models
import mediaflow.automation.operation_production_models as production_models
import mediaflow.automation.operation_project_models as project_models
import mediaflow.automation.operation_review_models as review_models
import mediaflow.automation.operation_timeline_models as timeline_models
import mediaflow.automation.operation_voiceover_models as voiceover_models
import mediaflow.automation.operation_web_models as web_models
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
production = _LazyOperationModule("mediaflow.automation.production_operations")
project = _LazyOperationModule("mediaflow.automation.project_operations")
resources = _LazyOperationModule("mediaflow.automation.resource_operations")
review = _LazyOperationModule("mediaflow.automation.review_operations")
runtime = _LazyOperationModule("mediaflow.automation.runtime_operations")
speech = _LazyOperationModule("mediaflow.automation.speech_operations")
tasks = _LazyOperationModule("mediaflow.automation.task_operations")
timeline = _LazyOperationModule("mediaflow.automation.timeline_operations")
voiceover = _LazyOperationModule("mediaflow.automation.voiceover_operations")
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
        project_models.MediaResourceSearchArguments,
        project_models.MediaResourceSearchResult,
        "none",
        resources.search_catalog,
        capabilities=(),
    ),
    "runtime.inspect": _operation(
        common_models.EmptyArguments,
        project_models.RuntimeInspectionResult,
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
        project_models.ReferenceComparisonArguments,
        project_models.ReferenceComparisonOperationResult,
        "none",
        media_quality.compare_reference,
        capabilities=("reference-video-comparison", "ffmpeg", "ffprobe"),
    ),
    "production.bundle.inspect": _operation(
        production_models.ProductionBundleInspectArguments,
        production_models.ProductionBundleInspectResult,
        "none",
        production.inspect_bundle,
        capabilities=(),
    ),
    "project.create": _operation(
        project_models.ProjectCreateArguments,
        project_models.ProjectSnapshotResult,
        "create",
        project.create_project,
    ),
    "project.inspect": _read(
        common_models.EmptyArguments,
        project_models.ProjectSnapshotResult,
        project.inspect_project,
    ),
    "project.upgrade": _write(
        common_models.EmptyArguments,
        project_models.ProjectUpgradeResult,
        project.upgrade_project,
    ),
    "project.version.list": _read(
        common_models.EmptyArguments,
        project_models.ProjectVersionListResult,
        project.list_versions,
    ),
    "project.version.create": _write(
        project_models.ProjectVersionCreateArguments,
        project_models.ProjectVersionResult,
        project.create_version,
    ),
    "project.version.restore": _write(
        project_models.ProjectVersionRestoreArguments,
        project_models.ProjectVersionRestoreResult,
        project.restore_version,
    ),
    "project.changes.list": _read(
        project_models.ProjectChangesListArguments,
        project_models.ProjectChangesListResult,
        project.list_changes,
        capabilities=("project-editing", "asynchronous-project-handoff"),
    ),
    "project.handoff.inspect": _read(
        project_models.ProjectHandoffInspectArguments,
        project_models.ProjectHandoffInspectResult,
        project.inspect_handoff,
        capabilities=("project-editing", "asynchronous-project-handoff"),
    ),
    "project.context.inspect": _read(
        project_models.ProjectContextInspectArguments,
        project_models.ProjectContextInspectResult,
        project.inspect_context,
        capabilities=("project-editing", "asynchronous-project-handoff"),
    ),
    "diagnostics.bundle.create": _write(
        project_models.DiagnosticsBundleArguments,
        delivery_models.TaskReceiptResult,
        diagnostics.create_bundle,
        task_backed=True,
    ),
    "asset.list": _read(
        common_models.EmptyArguments,
        project_models.AssetListResult,
        project.list_assets,
    ),
    "asset.import": _write(
        project_models.AssetImportArguments,
        delivery_models.TaskReceiptResult,
        project.import_asset,
        task_backed=True,
        capabilities=("project-editing", "ffprobe", "ffmpeg"),
    ),
    "project.collection.preview": _read(
        project_models.ProjectCollectionArguments,
        project_models.ProjectCollectionPreviewResult,
        project.preview_project_collection,
        capabilities=("project-editing", "project-collection"),
    ),
    "project.collection.list": _read(
        common_models.EmptyArguments,
        project_models.ProjectCollectionListResult,
        project.list_project_collections,
        capabilities=("project-editing", "project-collection"),
    ),
    "project.collection.apply": _write(
        project_models.ProjectCollectionArguments,
        project_models.ProjectCollectionApplyResult,
        project.collect_project_assets,
        capabilities=("project-editing", "project-collection"),
        reversible=True,
    ),
    "project.collection.state.set": _write(
        project_models.ProjectCollectionStateArguments,
        project_models.ProjectCollectionApplyResult,
        project.set_project_collection_state,
        capabilities=("project-editing", "project-collection"),
        reversible=True,
    ),
    "project.archive.create": _write(
        project_models.ProjectArchiveCreateArguments,
        project_models.ProjectArchiveCreateResult,
        project.create_project_archive,
        capabilities=("project-editing", "project-collection"),
    ),
    "sequence.short.create": _write(
        project_models.SequenceShortCreateArguments,
        project_models.SequenceResult,
        project.create_short_sequence,
    ),
    "sequence.variant.list": _read(
        project_models.SequenceVariantListArguments,
        project_models.SequenceVariantListResult,
        project.list_sequence_variants,
        capabilities=("project-editing", "sequence-delivery-variants"),
    ),
    "sequence.variant.plan": _read(
        project_models.SequenceVariantPlanArguments,
        project_models.SequenceVariantPlanResult,
        project.plan_sequence_variants,
        capabilities=("project-editing", "sequence-delivery-variants"),
    ),
    "sequence.variant.generate": _write(
        project_models.SequenceVariantGenerateArguments,
        project_models.SequenceVariantGenerateResult,
        project.generate_sequence_variants,
        capabilities=("project-editing", "sequence-delivery-variants"),
        reversible=True,
    ),
    "timeline.get": _read(
        common_models.SequenceArguments,
        timeline_models.TimelineResult,
        timeline.get_timeline,
    ),
    "multicam.list": _read(
        common_models.SequenceArguments,
        timeline_models.MulticamGroupListResult,
        timeline.list_multicam_groups,
        capabilities=("project-editing", "multicam-editing"),
    ),
    "multicam.sync.analyze": _read(
        timeline_models.MulticamSyncAnalyzeArguments,
        timeline_models.MulticamSyncAnalyzeResult,
        timeline.analyze_multicam_sync,
        capabilities=("project-editing", "multicam-editing"),
    ),
    "multicam.group.create": _write(
        timeline_models.MulticamGroupCreateArguments,
        timeline_models.MulticamGroupResult,
        timeline.create_multicam_group,
        capabilities=("project-editing", "multicam-editing"),
        reversible=True,
    ),
    "multicam.angle.switch": _write(
        timeline_models.MulticamAngleSwitchArguments,
        timeline_models.MulticamGroupResult,
        timeline.switch_multicam_angle,
        capabilities=("project-editing", "multicam-editing"),
        reversible=True,
    ),
    "color.scope.analyze": _read(
        timeline_models.ColorScopeAnalyzeArguments,
        timeline_models.ColorScopeAnalyzeResult,
        timeline.analyze_color_scopes,
        capabilities=("project-editing", "color-scopes", "mlt"),
    ),
    "timeline.portable.inspect": _read(
        timeline_models.PortableTimelineArguments,
        timeline_models.PortableTimelineInspectResult,
        timeline.inspect_portable_timeline,
        capabilities=("project-editing", "portable-timeline-import"),
    ),
    "timeline.portable.import": _write(
        timeline_models.PortableTimelineArguments,
        timeline_models.PortableTimelineImportResult,
        timeline.import_portable_timeline,
        capabilities=(
            "project-editing",
            "portable-timeline-import",
            "ffmpeg",
            "ffprobe",
        ),
    ),
    "timeline.interchange.inspect": _read(
        timeline_models.InterchangeTimelineArguments,
        timeline_models.InterchangeTimelineInspectResult,
        timeline.inspect_interchange_timeline,
        capabilities=("project-editing", "interchange-import"),
    ),
    "timeline.interchange.import": _write(
        timeline_models.InterchangeTimelineImportArguments,
        timeline_models.InterchangeTimelineImportResult,
        timeline.import_interchange_timeline,
        capabilities=("project-editing", "interchange-import", "ffmpeg", "ffprobe"),
        reversible=True,
    ),
    "voiceover.cue.list": _read(
        voiceover_models.VoiceoverCueListArguments,
        voiceover_models.VoiceoverCueListResult,
        voiceover.list_cues,
        capabilities=("project-editing", "voiceover-adr"),
    ),
    "voiceover.cue.create": _write(
        voiceover_models.VoiceoverCueCreateArguments,
        voiceover_models.VoiceoverCueResult,
        voiceover.create_cue,
        capabilities=("project-editing", "voiceover-adr"),
        reversible=True,
    ),
    "voiceover.cue.update": _write(
        voiceover_models.VoiceoverCueUpdateArguments,
        voiceover_models.VoiceoverCueResult,
        voiceover.update_cue,
        capabilities=("project-editing", "voiceover-adr"),
        reversible=True,
    ),
    "voiceover.cue.archive": _write(
        voiceover_models.VoiceoverCueArchiveArguments,
        voiceover_models.VoiceoverCueResult,
        voiceover.archive_cue,
        capabilities=("project-editing", "voiceover-adr"),
        reversible=True,
    ),
    "voiceover.take.add": _write(
        voiceover_models.VoiceoverTakeAddArguments,
        voiceover_models.VoiceoverTakeResult,
        voiceover.add_take,
        capabilities=("project-editing", "voiceover-adr", "ffprobe"),
        reversible=True,
    ),
    "voiceover.take.update": _write(
        voiceover_models.VoiceoverTakeUpdateArguments,
        voiceover_models.VoiceoverTakeUpdateResult,
        voiceover.update_take,
        capabilities=("project-editing", "voiceover-adr"),
        reversible=True,
    ),
    "voiceover.take.select": _write(
        voiceover_models.VoiceoverTakeSelectArguments,
        voiceover_models.VoiceoverCueResult,
        voiceover.select_take,
        capabilities=("project-editing", "voiceover-adr"),
        reversible=True,
    ),
    "voiceover.take.archive": _write(
        voiceover_models.VoiceoverTakeArchiveArguments,
        voiceover_models.VoiceoverTakeResult,
        voiceover.archive_take,
        capabilities=("project-editing", "voiceover-adr"),
        reversible=True,
    ),
    "voiceover.take.place": _write(
        voiceover_models.VoiceoverTakePlaceArguments,
        voiceover_models.VoiceoverCueResult,
        voiceover.place_take,
        capabilities=("project-editing", "voiceover-adr"),
        reversible=True,
    ),
    "voiceover.latency.list": _read(
        voiceover_models.VoiceoverLatencyCalibrationListArguments,
        voiceover_models.VoiceoverLatencyCalibrationListResult,
        voiceover.list_latency_calibrations,
        capabilities=("project-editing", "voiceover-adr"),
    ),
    "voiceover.latency.get": _read(
        voiceover_models.VoiceoverLatencyCalibrationGetArguments,
        voiceover_models.VoiceoverLatencyCalibrationResult,
        voiceover.get_latency_calibration,
        capabilities=("project-editing", "voiceover-adr"),
    ),
    "voiceover.latency.set": _write(
        voiceover_models.VoiceoverLatencyCalibrationSetArguments,
        voiceover_models.VoiceoverLatencyCalibrationResult,
        voiceover.set_latency_calibration,
        capabilities=("project-editing", "voiceover-adr"),
    ),
    "voiceover.latency.analyze": _write(
        voiceover_models.VoiceoverLatencyCalibrationAnalyzeArguments,
        voiceover_models.VoiceoverLatencyCalibrationResult,
        voiceover.analyze_latency_calibration,
        capabilities=("project-editing", "voiceover-adr"),
    ),
    "timeline.track.add": _write(
        timeline_models.TimelineTrackAddArguments,
        timeline_models.TrackResult,
        timeline.add_track,
        reversible=True,
    ),
    "timeline.clip.add": _write(
        timeline_models.TimelineClipAddArguments,
        timeline_models.ClipResult,
        timeline.add_clip,
        reversible=True,
    ),
    "timeline.clip.batch.add": _write(
        timeline_models.TimelineClipBatchAddArguments,
        timeline_models.ClipsResult,
        timeline.add_clips,
        reversible=True,
    ),
    "timeline.clip.move": _write(
        timeline_models.TimelineClipMoveArguments,
        timeline_models.ClipResult,
        timeline.move_clip,
        reversible=True,
    ),
    "timeline.clip.copy": _write(
        timeline_models.TimelineClipMoveArguments,
        timeline_models.ClipResult,
        timeline.copy_clip,
        reversible=True,
    ),
    "timeline.clip.split": _write(
        timeline_models.TimelineClipSplitArguments,
        timeline_models.ClipsResult,
        timeline.split_clip,
        reversible=True,
    ),
    "timeline.clip.delete": _write(
        timeline_models.TimelineClipDeleteArguments,
        timeline_models.TimelineResult,
        timeline.delete_clips,
        reversible=True,
    ),
    "timeline.clip.freeze.add": _write(
        timeline_models.TimelineFreezeClipAddArguments,
        timeline_models.ClipResult,
        timeline.add_freeze_clip,
        reversible=True,
        capabilities=("project-editing", "native-freeze-clips"),
    ),
    "timeline.transition.add": _write(
        timeline_models.TimelineTransitionAddArguments,
        timeline_models.TransitionResult,
        timeline.add_transition,
        reversible=True,
    ),
    "timeline.transition.update": _write(
        timeline_models.TimelineTransitionUpdateArguments,
        timeline_models.TransitionResult,
        timeline.update_transition,
        reversible=True,
    ),
    "timeline.transition.remove": _write(
        timeline_models.TimelineTransitionRemoveArguments,
        delivery_models.RemovedResult,
        timeline.remove_transition,
        reversible=True,
    ),
    "timeline.marker.add": _write(
        timeline_models.TimelineMarkerAddArguments,
        timeline_models.MarkerResult,
        timeline.add_marker,
        reversible=True,
        capabilities=("project-editing", "semantic-timeline-markers"),
    ),
    "timeline.marker.update": _write(
        timeline_models.TimelineMarkerUpdateArguments,
        timeline_models.MarkerResult,
        timeline.update_marker,
        reversible=True,
        capabilities=("project-editing", "semantic-timeline-markers"),
    ),
    "timeline.marker.remove": _write(
        timeline_models.TimelineMarkerRemoveArguments,
        delivery_models.RemovedResult,
        timeline.remove_marker,
        reversible=True,
        capabilities=("project-editing", "semantic-timeline-markers"),
    ),
    "subtitle.track.style.update": _write(
        timeline_models.SubtitleTrackStyleUpdateArguments,
        timeline_models.TrackResult,
        timeline.update_subtitle_track_style,
        reversible=True,
    ),
    "timeline.clip.transform": _write(
        timeline_models.TimelineClipTransformArguments,
        timeline_models.ClipResult,
        timeline.transform_clip,
        reversible=True,
    ),
    "timeline.clip.transform.keyframe.set": _write(
        timeline_models.TimelineClipTransformKeyframeSetArguments,
        timeline_models.ClipResult,
        timeline.set_clip_transform_keyframe,
        reversible=True,
    ),
    "timeline.clip.transform.keyframe.remove": _write(
        timeline_models.TimelineClipTransformKeyframeRemoveArguments,
        timeline_models.ClipResult,
        timeline.remove_clip_transform_keyframe,
        reversible=True,
    ),
    "timeline.clip.transform.keyframe.move": _write(
        timeline_models.TimelineClipTransformKeyframeMoveArguments,
        timeline_models.ClipResult,
        timeline.move_clip_transform_keyframe,
        reversible=True,
    ),
    "timeline.clip.transform.keyframe.retime": _write(
        timeline_models.TimelineClipTransformKeyframeRetimeArguments,
        timeline_models.ClipResult,
        timeline.retime_clip_transform_keyframes,
        reversible=True,
    ),
    "timeline.clip.audio": _write(
        timeline_models.TimelineClipAudioArguments,
        timeline_models.ClipResult,
        timeline.update_clip_audio,
        reversible=True,
    ),
    "timeline.clip.source.replace": _write(
        timeline_models.TimelineClipReplaceSourceArguments,
        timeline_models.ClipResult,
        timeline.replace_clip_source,
        reversible=True,
    ),
    "timeline.clip.effect.add": _write(
        timeline_models.TimelineClipVisualEffectAddArguments,
        timeline_models.ClipResult,
        timeline.add_clip_visual_effect,
        reversible=True,
    ),
    "timeline.clip.effect.update": _write(
        timeline_models.TimelineClipVisualEffectUpdateArguments,
        timeline_models.ClipResult,
        timeline.update_clip_visual_effect,
        reversible=True,
    ),
    "timeline.clip.effect.move": _write(
        timeline_models.TimelineClipVisualEffectMoveArguments,
        timeline_models.ClipResult,
        timeline.move_clip_visual_effect,
        reversible=True,
    ),
    "timeline.clip.effect.remove": _write(
        timeline_models.TimelineClipVisualEffectRemoveArguments,
        timeline_models.ClipResult,
        timeline.remove_clip_visual_effect,
        reversible=True,
    ),
    "timeline.clip.effect.keyframe.set": _write(
        timeline_models.TimelineClipVisualEffectKeyframeSetArguments,
        timeline_models.ClipResult,
        timeline.set_clip_effect_keyframe,
        reversible=True,
    ),
    "timeline.clip.effect.keyframe.remove": _write(
        timeline_models.TimelineClipVisualEffectKeyframeRemoveArguments,
        timeline_models.ClipResult,
        timeline.remove_clip_effect_keyframe,
        reversible=True,
    ),
    "timeline.clip.effect.keyframe.move": _write(
        timeline_models.TimelineClipVisualEffectKeyframeMoveArguments,
        timeline_models.ClipResult,
        timeline.move_clip_effect_keyframe,
        reversible=True,
    ),
    "timeline.clip.effect.keyframe.retime": _write(
        timeline_models.TimelineClipVisualEffectKeyframeRetimeArguments,
        timeline_models.ClipResult,
        timeline.retime_clip_effect_keyframes,
        reversible=True,
    ),
    "timeline.clip.effect.mask.assign": _write(
        timeline_models.TimelineClipVisualEffectMaskAssignArguments,
        timeline_models.ClipResult,
        timeline.assign_clip_effect_mask,
        reversible=True,
    ),
    "timeline.clip.mask.add": _write(
        timeline_models.TimelineClipMaskAddArguments,
        timeline_models.ClipResult,
        timeline.add_clip_mask,
        reversible=True,
    ),
    "timeline.clip.mask.update": _write(
        timeline_models.TimelineClipMaskUpdateArguments,
        timeline_models.ClipResult,
        timeline.update_clip_mask,
        reversible=True,
    ),
    "timeline.clip.mask.move": _write(
        timeline_models.TimelineClipMaskMoveArguments,
        timeline_models.ClipResult,
        timeline.move_clip_mask,
        reversible=True,
    ),
    "timeline.clip.mask.remove": _write(
        timeline_models.TimelineClipMaskRemoveArguments,
        timeline_models.ClipResult,
        timeline.remove_clip_mask,
        reversible=True,
    ),
    "timeline.clip.mask.keyframe.set": _write(
        timeline_models.TimelineClipMaskKeyframeSetArguments,
        timeline_models.ClipResult,
        timeline.set_clip_mask_keyframe,
        reversible=True,
    ),
    "timeline.clip.mask.keyframe.remove": _write(
        timeline_models.TimelineClipMaskKeyframeRemoveArguments,
        timeline_models.ClipResult,
        timeline.remove_clip_mask_keyframe,
        reversible=True,
    ),
    "timeline.clip.mask.keyframe.move": _write(
        timeline_models.TimelineClipMaskKeyframeMoveArguments,
        timeline_models.ClipResult,
        timeline.move_clip_mask_keyframe,
        reversible=True,
    ),
    "timeline.clip.mask.keyframe.retime": _write(
        timeline_models.TimelineClipMaskKeyframeRetimeArguments,
        timeline_models.ClipResult,
        timeline.retime_clip_mask_keyframes,
        reversible=True,
    ),
    "review.list": _read(
        review_models.ReviewListArguments,
        review_models.ReviewThreadListResult,
        review.list_threads,
    ),
    "review.summary": _read(
        common_models.SequenceArguments,
        review_models.ReviewSummaryResult,
        review.summarize,
    ),
    "review.thread.create": _write(
        review_models.ReviewThreadCreateArguments,
        review_models.ReviewThreadResult,
        review.create_thread,
        reversible=True,
    ),
    "review.thread.reply": _write(
        review_models.ReviewThreadReplyArguments,
        review_models.ReviewThreadResult,
        review.reply_thread,
        reversible=True,
    ),
    "review.message.edit": _write(
        review_models.ReviewMessageEditArguments,
        review_models.ReviewThreadResult,
        review.edit_message,
        reversible=True,
    ),
    "review.thread.update": _write(
        review_models.ReviewThreadUpdateArguments,
        review_models.ReviewThreadResult,
        review.update_thread,
        reversible=True,
    ),
    "review.thread.resolve": _write(
        review_models.ReviewThreadArguments,
        review_models.ReviewThreadResult,
        review.resolve_thread,
        reversible=True,
    ),
    "review.thread.reopen": _write(
        review_models.ReviewThreadArguments,
        review_models.ReviewThreadResult,
        review.reopen_thread,
        reversible=True,
    ),
    "review.thread.archive": _write(
        review_models.ReviewThreadArguments,
        review_models.ReviewThreadResult,
        review.archive_thread,
        reversible=True,
    ),
    "review.thread.restore": _write(
        review_models.ReviewThreadArguments,
        review_models.ReviewThreadResult,
        review.restore_thread,
        reversible=True,
    ),
    "review.snapshot.capture": _write(
        review_models.ReviewSnapshotCaptureArguments,
        review_models.ReviewSnapshotResult,
        review.capture_snapshot,
        capabilities=("project-editing", "review-collaboration"),
        reversible=True,
    ),
    "review.snapshot.markup.set": _write(
        review_models.ReviewSnapshotMarkupArguments,
        review_models.ReviewSnapshotResult,
        review.set_snapshot_markup,
        capabilities=("project-editing", "review-collaboration"),
        reversible=True,
    ),
    "review.package.export": _write(
        review_models.ReviewPackageExportArguments,
        review_models.ReviewPackageOperationResult,
        review.export_package,
        capabilities=("project-editing", "review-collaboration"),
    ),
    "review.package.import": _write(
        review_models.ReviewPackageImportArguments,
        review_models.ReviewPackageOperationResult,
        review.import_package,
        capabilities=("project-editing", "review-collaboration"),
        reversible=True,
    ),
    "subtitle.list": _read(
        common_models.SequenceArguments,
        language_models.SubtitleListResult,
        language_audio.list_subtitles,
    ),
    "subtitle.segment.update": _write(
        language_models.SubtitleSegmentUpdateArguments,
        language_models.SubtitleSegmentResult,
        language_audio.update_subtitle_segment,
        reversible=True,
    ),
    "dubbing.session.list": _read(
        language_models.DubbingListArguments,
        language_models.DubbingSessionListResult,
        dubbing.list_sessions,
    ),
    "dubbing.session.get": _read(
        language_models.DubbingSessionArguments,
        language_models.DubbingSessionResult,
        dubbing.get_session,
    ),
    "dubbing.prepare": _write(
        language_models.DubbingPrepareArguments,
        delivery_models.TaskReceiptResult,
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
        language_models.DubbingSynthesizeArguments,
        delivery_models.TaskReceiptResult,
        dubbing.synthesize,
        task_backed=True,
        capabilities=("project-editing", "gpt-sovits-v2pro", "ffmpeg"),
    ),
    "dubbing.commit": _write(
        language_models.DubbingCommitArguments,
        delivery_models.TaskReceiptResult,
        dubbing.commit,
        task_backed=True,
        capabilities=("project-editing", "ffprobe"),
    ),
    "dubbing.speaker.update": _write(
        language_models.DubbingSpeakerUpdateArguments,
        language_models.DubbingSessionResult,
        dubbing.update_speaker,
    ),
    "dubbing.reference.update": _write(
        language_models.DubbingReferenceUpdateArguments,
        language_models.DubbingSessionResult,
        dubbing.update_reference,
    ),
    "dubbing.utterance.update": _write(
        language_models.DubbingUtteranceUpdateArguments,
        language_models.DubbingSessionResult,
        dubbing.update_utterance,
    ),
    "transcript.get": _read(
        language_models.TranscriptGetArguments,
        language_models.TranscriptResult,
        language_audio.get_transcript,
    ),
    "script.inspect": _read(
        language_models.TranscriptGetArguments,
        language_models.ScriptInspectResult,
        language_audio.inspect_script,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "script.segment.update": _write(
        language_models.ScriptSegmentUpdateArguments,
        language_models.SubtitleSegmentResult,
        language_audio.update_script_segment,
        reversible=True,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "script.segment.split": _write(
        language_models.ScriptSegmentSplitArguments,
        language_models.ScriptSegmentSplitResult,
        language_audio.split_script_segment,
        reversible=True,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "script.segment.merge": _write(
        language_models.ScriptSegmentMergeArguments,
        language_models.SubtitleSegmentResult,
        language_audio.merge_script_segments,
        reversible=True,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "script.segment.move": _write(
        language_models.ScriptSegmentMoveArguments,
        language_models.ScriptTimelineEditResult,
        language_audio.move_script_segment,
        reversible=True,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "script.gap.close": _write(
        language_models.ScriptGapCloseArguments,
        language_models.ScriptTimelineEditResult,
        language_audio.close_script_gap,
        reversible=True,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "transcript.sequence.transcribe": _write(
        language_models.TranscriptSequenceTranscribeArguments,
        delivery_models.TaskReceiptResult,
        language_audio.transcribe_sequence,
        task_backed=True,
        capabilities=("project-editing", "faster-whisper-xxl"),
    ),
    "speech.review.inspect": _read(
        language_models.SpeechReviewInspectArguments,
        language_models.SpeechReviewInspectResult,
        language_audio.inspect_speech_review,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "transcript.edit.preview": _read(
        language_models.TranscriptEditPreviewArguments,
        language_models.TranscriptEditPlanResult,
        language_audio.preview_transcript_edit,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "transcript.edit.apply": _write(
        language_models.TranscriptEditApplyArguments,
        language_models.TranscriptEditResultDocument,
        language_audio.apply_transcript_edit,
        reversible=True,
        capabilities=("project-editing", "transcript-edit-plans"),
    ),
    "audio.inspect": _read(
        common_models.SequenceArguments,
        delivery_models.AudioInspectResult,
        language_audio.inspect_audio,
    ),
    "audio.bus.update": _write(
        delivery_models.AudioBusUpdateArguments,
        delivery_models.AudioBusResult,
        language_audio.update_audio_bus,
    ),
    "audio.effect.save": _write(
        delivery_models.AudioEffectSaveArguments,
        delivery_models.AudioEffectResult,
        language_audio.save_audio_effect,
    ),
    "audio.effect.remove": _write(
        delivery_models.AudioEffectRemoveArguments,
        delivery_models.RemovedResult,
        language_audio.remove_audio_effect,
    ),
    "preview.render": _write(
        delivery_models.PreviewRenderArguments,
        delivery_models.PreviewRenderResult,
        timeline.render_preview,
    ),
    "preview.frames.render": _read(
        delivery_models.PreviewFramesRenderArguments,
        delivery_models.PreviewFramesRenderResult,
        timeline.render_preview_frames,
    ),
    "export.sequence": _write(
        delivery_models.ExportSequenceArguments,
        delivery_models.TaskReceiptResult,
        timeline.export_sequence,
        task_backed=True,
        capabilities=("project-editing", "mlt", "ffmpeg", "ffprobe"),
    ),
    "export.sequence.build": _write(
        delivery_models.BuildSequenceArguments,
        delivery_models.TaskReceiptResult,
        timeline.build_sequence,
        task_backed=True,
        capabilities=("project-editing", "mlt", "ffmpeg", "ffprobe"),
    ),
    "export.fcpxml": _write(
        delivery_models.ExportFcpxmlArguments,
        delivery_models.FcpxmlExportResult,
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
        common_models.EmptyArguments,
        delivery_models.TaskListResult,
        tasks.list_tasks,
    ),
    "task.get": _read(
        delivery_models.TaskStatusArguments,
        delivery_models.TaskStatusResult,
        tasks.get_task,
    ),
    "task.cancel": _write(
        delivery_models.TaskStatusArguments,
        delivery_models.TaskStatusResult,
        tasks.cancel_task,
    ),
    "task.wait": _read(
        delivery_models.TaskWaitArguments,
        delivery_models.TaskStatusResult,
        tasks.wait_for_task,
    ),
    "task.start": _write(
        delivery_models.TaskStartArguments,
        delivery_models.TaskReceiptResult,
        tasks.start_task,
        task_backed=True,
    ),
    "task.resume": _write(
        delivery_models.TaskResumeArguments,
        delivery_models.TaskReceiptResult,
        tasks.resume_task,
        task_backed=True,
    ),
    "web.import": _write(
        web_models.WebImportArguments,
        web_models.WebImportResult,
        web.import_web,
        capabilities=(*WEB, "chromium"),
    ),
    "web.inspect": _read(
        web_models.WebInspectArguments,
        web_models.WebInspectResult,
        web.inspect_web,
        capabilities=WEB,
    ),
    "web.clip.get": _read(
        web_models.WebClipGetArguments,
        web_models.WebClipStateResult,
        web.get_web_clip,
        capabilities=WEB,
    ),
    "web.clip.edit.describe": _read(
        web_models.WebClipEditDescribeArguments,
        web_models.WebEditDocumentResult,
        web.describe_web_clip_editing,
        capabilities=WEB,
    ),
    "web.clip.update": _write(
        web_models.WebClipUpdateArguments,
        web_models.WebClipStateResult,
        web.update_web_clip,
        capabilities=WEB,
    ),
    "web.clip.diff": _read(
        web_models.WebClipUpdateArguments,
        web_models.WebStateDiffResult,
        web.diff_web_clip,
        capabilities=WEB,
    ),
    "web.clip.variant.select": _write(
        web_models.WebClipVariantSelectArguments,
        web_models.WebClipStateResult,
        web.select_variant,
        capabilities=(*WEB, "web-responsive-variants"),
    ),
    "web.clip.keyframe.set": _write(
        web_models.WebClipKeyframeSetArguments,
        web_models.WebClipStateResult,
        web.set_keyframe,
        capabilities=(*WEB, "web-keyframes"),
    ),
    "web.clip.keyframe.remove": _write(
        web_models.WebClipKeyframeRemoveArguments,
        web_models.WebClipStateResult,
        web.remove_keyframe,
        capabilities=(*WEB, "web-keyframes"),
    ),
    "web.clip.parameter.update": _write(
        web_models.WebParameterUpdateArguments,
        web_models.WebClipStateResult,
        web.update_parameter,
        capabilities=(*WEB, "web-parameters"),
    ),
    "web.clip.parameter.keyframe.set": _write(
        web_models.WebParameterKeyframeSetArguments,
        web_models.WebClipStateResult,
        web.set_parameter_keyframe,
        capabilities=(*WEB, "web-parameters", "web-keyframes"),
    ),
    "web.clip.parameter.keyframe.remove": _write(
        web_models.WebParameterKeyframeRemoveArguments,
        web_models.WebClipStateResult,
        web.remove_parameter_keyframe,
        capabilities=(*WEB, "web-parameters", "web-keyframes"),
    ),
    "web.clip.parameter.lock.update": _write(
        web_models.WebParameterLockUpdateArguments,
        web_models.WebClipStateResult,
        web.update_parameter_lock,
        capabilities=(*WEB, "web-parameters", "web-field-locks"),
    ),
    "web.clip.theme.update": _write(
        web_models.WebThemeUpdateArguments,
        web_models.WebClipStateResult,
        web.update_theme,
        capabilities=(*WEB, "web-themes"),
    ),
    "web.clip.data.update": _write(
        web_models.WebDataUpdateArguments,
        web_models.WebClipStateResult,
        web.update_data,
        capabilities=(*WEB, "web-data-snapshots"),
    ),
    "web.clip.data.snapshot": _write(
        web_models.WebDataSnapshotArguments,
        web_models.WebClipStateResult,
        web.snapshot_data,
        capabilities=(*WEB, "web-data-snapshots"),
    ),
    "web.clip.lock.update": _write(
        web_models.WebFieldLockUpdateArguments,
        web_models.WebClipStateResult,
        web.update_locks,
        capabilities=(*WEB, "web-field-locks"),
    ),
    "web.clip.render.inspect": _read(
        web_models.WebClipRenderInspectArguments,
        web_models.WebClipRenderInspectionResult,
        web.inspect_web_clip_render,
        capabilities=WEB,
    ),
    "web.clip.render": _write(
        web_models.WebClipRenderArguments,
        delivery_models.TaskReceiptResult,
        web.render_web_clip,
        task_backed=True,
        capabilities=(*WEB, "chromium", "ffmpeg", "ffprobe"),
    ),
    "web.clip.export": _write(
        web_models.WebClipExportArguments,
        delivery_models.TaskReceiptResult,
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
        web_models.WebBatchCreateArguments,
        web_models.WebBatchResult,
        web.create_batch,
        capabilities=(*WEB, "web-batch-variants"),
    ),
    "web.asset.rebind.plan": _read(
        web_models.WebAssetRebindPlanArguments,
        web_models.WebRebindPlanResult,
        web.plan_rebind_asset,
        capabilities=(*WEB, "web-template-rebinding", "chromium"),
    ),
    "web.asset.rebind.commit": _write(
        web_models.WebAssetRebindCommitArguments,
        web_models.WebRebindCommitResult,
        web.commit_rebind_asset,
        capabilities=(*WEB, "web-template-rebinding", "chromium"),
    ),
}
