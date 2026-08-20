from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from mediaflow.application.ports import ProjectWorkflowDocuments
from mediaflow.application.timeline_clock import project_frame_profile
from mediaflow.application.workflow_coordinator import WorkflowCoordinator
from mediaflow.domain.enums import AssetKind, AssetOrigin, WorkflowStage
from mediaflow.domain.project import Sequence
from mediaflow.domain.sequence_audio import build_dialogue_transcription_plan
from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.task_commands import (
    AnalyzeHighlightsCommand,
    DownloadMediaCommand,
    GenerateProxyCommand,
    TaskCommand,
    TranscribeSequenceCommand,
    TranslateDocumentCommand,
)
from mediaflow.domain.tasks import Task
from mediaflow.domain.workflows import WorkflowPayloadPatch, WorkflowRun

WorkflowTaskSpec = tuple[TaskCommand, list[str]]


class ProxyDecision(Protocol):
    required: bool
    reasons: tuple[str, ...]


@dataclass(slots=True)
class WorkflowUpdate:
    selected_asset_ids: list[str] = field(default_factory=list)
    status_source: str = ""
    status_arguments: tuple[str, ...] = ()

    def merge(self, other: WorkflowUpdate) -> WorkflowUpdate:
        return WorkflowUpdate(
            selected_asset_ids=other.selected_asset_ids or self.selected_asset_ids,
            status_source=other.status_source or self.status_source,
            status_arguments=(other.status_arguments if other.status_source else self.status_arguments),
        )


@dataclass(frozen=True, slots=True)
class WorkflowStageContext:
    documents: ProjectWorkflowDocuments
    coordinator: WorkflowCoordinator
    settings: ServiceSettings
    start_tasks: Callable[..., WorkflowUpdate]
    continue_after: Callable[[WorkflowRun], WorkflowUpdate]
    proxy_decision: Callable[..., ProxyDecision]
    create_highlight_short: Callable[[str], Sequence]

    def block(self, run: WorkflowRun, message_code: str) -> WorkflowUpdate:
        self.coordinator.block(run.id, message_code)
        return WorkflowUpdate()

    def advance(
        self,
        run: WorkflowRun,
        *,
        payload: WorkflowPayloadPatch | None = None,
        asset_ids: list[str] | None = None,
    ) -> WorkflowUpdate:
        advanced = self.coordinator.advance(run.id, payload=payload, asset_ids=asset_ids)
        return self.continue_after(advanced)

    def run_tasks(
        self,
        run: WorkflowRun,
        specs: list[WorkflowTaskSpec],
        *,
        payload: WorkflowPayloadPatch | None = None,
    ) -> WorkflowUpdate:
        return self.start_tasks(run, specs, payload=payload)

    def run_or_advance(
        self,
        run: WorkflowRun,
        specs: list[WorkflowTaskSpec],
    ) -> WorkflowUpdate:
        if specs:
            return self.run_tasks(run, specs)
        return self.advance(run)


class WorkflowStageHandler(Protocol):
    stage: WorkflowStage

    def start(
        self,
        context: WorkflowStageContext,
        run: WorkflowRun,
        *,
        target_language: str = "",
    ) -> WorkflowUpdate: ...

    def complete(
        self,
        context: WorkflowStageContext,
        run: WorkflowRun,
        tasks: list[Task],
    ) -> WorkflowUpdate: ...


class AdvancingStageHandler:
    stage: WorkflowStage

    def complete(
        self,
        context: WorkflowStageContext,
        run: WorkflowRun,
        tasks: list[Task],
    ) -> WorkflowUpdate:
        del tasks
        return context.advance(run)


class DownloadStageHandler(AdvancingStageHandler):
    stage = WorkflowStage.DOWNLOAD

    def start(
        self,
        context: WorkflowStageContext,
        run: WorkflowRun,
        *,
        target_language: str = "",
    ) -> WorkflowUpdate:
        del target_language
        if not run.payload.requests:
            return context.block(run, "workflow_download_request_required")
        return context.run_tasks(
            run,
            [(DownloadMediaCommand(request=request), []) for request in run.payload.requests],
        )

    def complete(
        self,
        context: WorkflowStageContext,
        run: WorkflowRun,
        tasks: list[Task],
    ) -> WorkflowUpdate:
        artifact_paths = {
            str(value.resolve(context.documents.project_dir)) for task in tasks for value in task.artifacts
        }
        assets = [
            asset
            for asset in context.documents.assets.list_assets()
            if asset.origin == AssetOrigin.DOWNLOAD
            and str(context.documents.assets.resolve_asset_path(asset).resolve()) in artifact_paths
        ]
        if not assets:
            return context.block(run, "workflow_download_artifacts_missing")
        return WorkflowUpdate(selected_asset_ids=[assets[0].id]).merge(
            context.advance(run, asset_ids=[asset.id for asset in assets])
        )


class PrepareMediaStageHandler(AdvancingStageHandler):
    stage = WorkflowStage.PREPARE_MEDIA

    def start(
        self,
        context: WorkflowStageContext,
        run: WorkflowRun,
        *,
        target_language: str = "",
    ) -> WorkflowUpdate:
        del target_language
        specs: list[WorkflowTaskSpec] = []
        for asset_id in run.asset_ids:
            asset = context.documents.assets.get_asset(asset_id)
            decision = context.proxy_decision(asset, dropped_frames=0)
            if not asset.proxy_path and decision.required:
                specs.append(
                    (
                        GenerateProxyCommand(
                            asset_id=asset.id,
                            reasons=list(decision.reasons),
                        ),
                        [asset.id],
                    )
                )
        return context.run_or_advance(run, specs)


class TranscribeStageHandler(AdvancingStageHandler):
    stage = WorkflowStage.TRANSCRIBE

    def start(
        self,
        context: WorkflowStageContext,
        run: WorkflowRun,
        *,
        target_language: str = "",
    ) -> WorkflowUpdate:
        del target_language
        transcribable = [
            asset_id
            for asset_id in run.asset_ids
            if context.documents.assets.get_asset(asset_id).kind
            in {
                AssetKind.VIDEO,
                AssetKind.AUDIO,
            }
        ]
        if not transcribable:
            return context.block(run, "workflow_no_transcribable_assets")
        state = context.documents.timeline.load_timeline(run.sequence_id)
        duration = state.duration_frames
        bounds = state.sequence.in_out
        start_frame = min(duration, bounds.in_frame) if bounds else 0
        end_frame = min(duration, bounds.out_frame) if bounds else duration
        assets = {asset.id: asset for asset in context.documents.assets.list_assets()}
        try:
            plan = build_dialogue_transcription_plan(
                state,
                assets,
                context.settings.asr,
                project_profile=project_frame_profile(
                    context.documents.projects,
                    context.documents.sequences,
                ),
                start_frame=start_frame,
                end_frame=end_frame,
            )
        except ValueError:
            return context.block(run, "workflow_no_transcribable_assets")
        if not plan.sources:
            return context.block(run, "workflow_no_transcribable_assets")
        return context.run_tasks(
            run,
            [
                (
                    TranscribeSequenceCommand(plan=plan),
                    [source.asset_id for source in plan.sources],
                )
            ],
        )

    def complete(
        self,
        context: WorkflowStageContext,
        run: WorkflowRun,
        tasks: list[Task],
    ) -> WorkflowUpdate:
        del tasks
        document_ids = [
            document.id
            for document in context.documents.subtitles.list_subtitle_documents(sequence_id=run.sequence_id)
            if document.is_source
            and document.source_document_id is None
            and document.purpose == "sequence_transcript"
        ]
        if not document_ids:
            return context.block(run, "workflow_transcription_artifacts_missing")
        return context.advance(
            run,
            payload=WorkflowPayloadPatch(source_document_ids=document_ids),
        )


class TranslateStageHandler(AdvancingStageHandler):
    stage = WorkflowStage.TRANSLATE

    def start(
        self,
        context: WorkflowStageContext,
        run: WorkflowRun,
        *,
        target_language: str = "",
    ) -> WorkflowUpdate:
        language = (
            target_language.strip()
            or run.payload.target_language.strip()
            or context.settings.translation.target_language
        )
        if not _has_active_llm_provider(context.settings):
            return context.block(run, "workflow_llm_provider_required")
        source_ids = set(run.payload.source_document_ids)
        documents = [
            document
            for document in context.documents.subtitles.list_subtitle_documents()
            if document.id in source_ids
        ]
        if not documents:
            return context.block(run, "workflow_source_subtitles_required")
        before = [document.id for document in context.documents.subtitles.list_subtitle_documents()]
        return context.run_tasks(
            run,
            [
                (
                    TranslateDocumentCommand(
                        document_id=document.id,
                        target_language=language,
                        mode=context.settings.translation.mode,
                    ),
                    [document.media_asset_id or document.asset_id],
                )
                for document in documents
            ],
            payload=WorkflowPayloadPatch(
                target_language=language,
                document_ids_before_translate=before,
            ),
        )

    def complete(
        self,
        context: WorkflowStageContext,
        run: WorkflowRun,
        tasks: list[Task],
    ) -> WorkflowUpdate:
        del tasks
        before = set(run.payload.document_ids_before_translate)
        document_ids = [
            document.id
            for document in context.documents.subtitles.list_subtitle_documents()
            if not document.is_source and document.id not in before
        ]
        if not document_ids:
            return context.block(run, "workflow_translation_artifacts_missing")
        return context.advance(
            run,
            payload=WorkflowPayloadPatch(translated_document_ids=document_ids),
        )


class HighlightStageHandler(AdvancingStageHandler):
    stage = WorkflowStage.HIGHLIGHT

    def start(
        self,
        context: WorkflowStageContext,
        run: WorkflowRun,
        *,
        target_language: str = "",
    ) -> WorkflowUpdate:
        del target_language
        if not _has_active_llm_provider(context.settings):
            return context.block(run, "workflow_llm_provider_required")
        selected_ids = set(run.payload.translated_document_ids) or set(run.payload.source_document_ids)
        documents = [
            document
            for document in context.documents.subtitles.list_subtitle_documents()
            if document.id in selected_ids
        ]
        if not documents:
            return context.block(run, "workflow_subtitles_required")
        before = [candidate.id for candidate in context.documents.highlights.list_highlights()]
        return context.run_tasks(
            run,
            [
                (
                    AnalyzeHighlightsCommand(document_id=document.id),
                    [document.media_asset_id or document.asset_id],
                )
                for document in documents
            ],
            payload=WorkflowPayloadPatch(highlight_ids_before=before),
        )

    def complete(
        self,
        context: WorkflowStageContext,
        run: WorkflowRun,
        tasks: list[Task],
    ) -> WorkflowUpdate:
        del tasks
        before = set(run.payload.highlight_ids_before)
        candidate_ids = [
            candidate.id
            for candidate in context.documents.highlights.list_highlights()
            if candidate.id not in before
        ]
        if not candidate_ids:
            return context.block(run, "workflow_highlight_artifacts_missing")
        return context.advance(
            run,
            payload=WorkflowPayloadPatch(highlight_candidate_ids=candidate_ids),
        )


class CreateShortsStageHandler(AdvancingStageHandler):
    stage = WorkflowStage.CREATE_SHORTS

    def start(
        self,
        context: WorkflowStageContext,
        run: WorkflowRun,
        *,
        target_language: str = "",
    ) -> WorkflowUpdate:
        del target_language
        candidate_ids = set(run.payload.highlight_candidate_ids)
        candidates = [
            candidate
            for asset_id in run.asset_ids
            for candidate in context.documents.highlights.list_highlights(asset_id)
            if candidate.id in candidate_ids
        ]
        if not candidates:
            return context.block(run, "workflow_highlights_required")
        sequence_ids = [context.create_highlight_short(candidate.id).id for candidate in candidates]
        return context.advance(
            run,
            payload=WorkflowPayloadPatch(short_sequence_ids=sequence_ids),
        )


class ExportStageHandler(AdvancingStageHandler):
    stage = WorkflowStage.EXPORT

    def start(
        self,
        context: WorkflowStageContext,
        run: WorkflowRun,
        *,
        target_language: str = "",
    ) -> WorkflowUpdate:
        del target_language
        return context.block(run, "workflow_export_confirmation_required")


class CompleteStageHandler(AdvancingStageHandler):
    stage = WorkflowStage.COMPLETE

    def start(
        self,
        context: WorkflowStageContext,
        run: WorkflowRun,
        *,
        target_language: str = "",
    ) -> WorkflowUpdate:
        del context, run, target_language
        return WorkflowUpdate()


def workflow_stage_handlers() -> dict[WorkflowStage, WorkflowStageHandler]:
    handlers: tuple[WorkflowStageHandler, ...] = (
        DownloadStageHandler(),
        PrepareMediaStageHandler(),
        TranscribeStageHandler(),
        TranslateStageHandler(),
        HighlightStageHandler(),
        CreateShortsStageHandler(),
        ExportStageHandler(),
        CompleteStageHandler(),
    )
    return {handler.stage: handler for handler in handlers}


def _has_active_llm_provider(settings: ServiceSettings) -> bool:
    return any(provider.enabled for provider in settings.llm_providers)
