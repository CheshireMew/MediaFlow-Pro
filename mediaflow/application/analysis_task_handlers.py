from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mediaflow.application.ports import (
    AnalysisOutputPublication,
    AnalysisOutputTransaction,
    AnalysisTaskDocuments,
    AnalysisTaskRuntime,
)
from mediaflow.application.task_handler_support import ProjectTaskHandler
from mediaflow.application.task_service import TaskCompletion, TaskContext
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.model_base import new_id
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.storage_names import content_addressed_child_path
from mediaflow.domain.task_commands import (
    AnalyzeDownloadCommand,
    AnalyzeLoudnessCommand,
    AnalyzeScenesCommand,
    AnalyzeSequenceBoundsCommand,
    TrackSubjectCommand,
)
from mediaflow.domain.tasks import (
    DownloadAnalysisTaskOutcome,
    SequenceBoundaryTaskOutcome,
)


class AnalysisTaskHandlers(ProjectTaskHandler):
    def __init__(
        self,
        documents: AnalysisTaskDocuments,
        runtime: AnalysisTaskRuntime,
        settings: Callable[[], ServiceSettings],
    ):
        super().__init__(documents.project_dir)
        self.documents = documents
        self.runtime = runtime
        self.settings = settings

    def handle(self, context: TaskContext) -> TaskCompletion:
        command = context.task.command
        if isinstance(command, AnalyzeDownloadCommand):
            return self._analyze_download(context, command)
        if isinstance(command, AnalyzeSequenceBoundsCommand):
            state = self.documents.timeline.load_timeline(command.sequence_id)
            analysis, result_path = self.runtime.analyze_sequence_bounds(
                state,
                expected_snapshot_hash=command.snapshot_hash,
                check_cancelled=context.cancellation.raise_if_requested,
                progress=context.report,
            )
            return self.completion(
                result_path,
                outcome=SequenceBoundaryTaskOutcome(analysis=analysis),
            )
        if isinstance(command, AnalyzeLoudnessCommand):
            state = self.documents.timeline.load_timeline(command.sequence_id)
            outcome, result_path = self.runtime.analyze_loudness(
                state,
                check_cancelled=context.cancellation.raise_if_requested,
                progress=context.report,
            )
            return self.completion(result_path, outcome=outcome)
        if isinstance(command, AnalyzeScenesCommand):
            return self._detect_scenes(context, command)
        if isinstance(command, TrackSubjectCommand):
            return self._track_subject(context, command)
        raise TypeError(f"Unexpected analysis command: {type(command).__name__}")

    def _detect_scenes(
        self,
        context: TaskContext,
        command: AnalyzeScenesCommand,
    ) -> TaskCompletion:
        state = self.documents.timeline.load_timeline(command.sequence_id)
        clip = next(item for item in state.clips if item.id == command.clip_id)
        asset = self.documents.catalog.get_asset(clip.asset_id)
        if asset.kind != AssetKind.VIDEO:
            raise ValueError("场景检测只适用于视频片段")
        context.report(OperationProgress.indeterminate("scene_detection_preparing"))
        frames = self.runtime.detect_scenes(
            self.documents.catalog.resolve_asset_path(asset),
            clip,
            state.sequence.profile,
            threshold=command.threshold,
            check_cancelled=context.cancellation.raise_if_requested,
            progress=context.report,
        )

        def apply_result() -> None:
            TimelineEditor(
                self.documents,
                command.sequence_id,
            ).replace_scene_markers(
                clip.id,
                frames,
                expected_clip=clip,
            )

        result_path = self._publish_visual_analysis(
            context,
            message_code="scene_detection_saving",
            payload={
                "type": "scene_detection",
                "sequence_id": command.sequence_id,
                "clip_id": clip.id,
                "threshold": command.threshold,
                "frames": frames,
            },
            apply_result=apply_result,
        )
        return self.completion(result_path)

    def _track_subject(
        self,
        context: TaskContext,
        command: TrackSubjectCommand,
    ) -> TaskCompletion:
        state = self.documents.timeline.load_timeline(command.sequence_id)
        clip = next(item for item in state.clips if item.id == command.clip_id)
        asset = self.documents.catalog.get_asset(clip.asset_id)
        if asset.kind != AssetKind.VIDEO:
            raise ValueError("自动构图和主体跟踪只适用于视频片段")
        context.report(OperationProgress.indeterminate("subject_tracking_preparing"))
        keyframes = self.runtime.track_subject(
            self.documents.catalog.resolve_asset_path(asset),
            clip,
            state.sequence.profile,
            mode=command.mode,
            check_cancelled=context.cancellation.raise_if_requested,
            progress=context.report,
        )

        def apply_result() -> None:
            TimelineEditor(
                self.documents,
                command.sequence_id,
            ).set_clip_transform_keyframes(
                clip.id,
                keyframes,
                expected_clip=clip,
            )

        result_path = self._publish_visual_analysis(
            context,
            message_code="subject_tracking_saving",
            payload={
                "type": command.mode,
                "sequence_id": command.sequence_id,
                "clip_id": clip.id,
                "keyframes": [item.model_dump(mode="json") for item in keyframes],
            },
            apply_result=apply_result,
        )
        return self.completion(result_path)

    def _analyze_download(
        self,
        context: TaskContext,
        command: AnalyzeDownloadCommand,
    ) -> TaskCompletion:
        context.report(OperationProgress.indeterminate("download_analyzing"))
        plan = self.runtime.analyze_download(
            command.url,
            self.settings().download,
            check_cancelled=context.cancellation.raise_if_requested,
        )
        destination = self.project_dir / "cache" / "download-analysis" / f"{context.task.id}.json"
        context.report(OperationProgress.indeterminate("download_analysis_saving"))
        context.cancellation.raise_if_requested()
        atomic_write_text(
            destination,
            plan.model_dump_json(indent=2),
        )
        return self.completion(
            destination,
            outcome=DownloadAnalysisTaskOutcome(plan=plan),
        )

    def _publish_visual_analysis(
        self,
        context: TaskContext,
        *,
        message_code: str,
        payload: dict[str, Any],
        apply_result: Callable[[], None],
    ) -> Path:
        destination = self.project_dir / "generated" / "visual-analysis" / f"{context.task.id}.json"
        output_scope = self.runtime.output_transaction(
            (destination,),
            overwrite=True,
        )
        publication = output_scope.__enter__()
        staged = publication.temporary_path(destination, "analysis")
        context.report(OperationProgress.indeterminate(message_code))
        context.cancellation.raise_if_requested()
        try:
            written = self.runtime.write_visual_analysis(staged, payload)
            if written.resolve() != staged.resolve():
                raise RuntimeError("Visual analysis runtime wrote outside the staged result path")
        except BaseException as error:
            output_scope.__exit__(type(error), error, error.__traceback__)
            self._archive_visual_analysis_failures(
                publication,
                context.task.id,
                error,
            )
            raise

        def commit_result() -> None:
            try:
                apply_result()
                publication.publish()
            except BaseException as error:
                output_scope.__exit__(
                    type(error),
                    error,
                    error.__traceback__,
                )
                self._archive_visual_analysis_failures(
                    publication,
                    context.task.id,
                    error,
                )
                raise

            def rollback(error: BaseException) -> None:
                output_scope.__exit__(
                    type(error),
                    error,
                    error.__traceback__,
                )
                self._archive_visual_analysis_failures(
                    publication,
                    context.task.id,
                    error,
                )

            self.documents.enlist_transaction_publication(
                on_commit=lambda: self._finish_visual_analysis_publication(
                    output_scope,
                    publication,
                ),
                on_rollback=rollback,
            )

        context.defer_project_change(commit_result)
        return destination

    def _finish_visual_analysis_publication(
        self,
        output_scope: AnalysisOutputTransaction,
        publication: AnalysisOutputPublication,
    ) -> None:
        publication.finalize(
            archive_replaced_to=(
                self.project_dir / "archive" / "replaced-visual-analysis"
            )
        )
        output_scope.__exit__(None, None, None)

    def _archive_visual_analysis_failures(
        self,
        publication: AnalysisOutputPublication,
        task_id: str,
        error: BaseException,
    ) -> None:
        for source in publication.archived_outputs:
            if not source.is_file():
                continue
            archive_path = content_addressed_child_path(
                self.project_dir / "archive" / "failed-task-artifacts",
                f"visual-analysis:{task_id}:{source.name}:{new_id()}",
                namespace="va",
                suffix=".json",
            )
            try:
                archive_path.parent.mkdir(parents=True, exist_ok=True)
                source.replace(archive_path)
            except OSError as archive_error:
                error.add_note(
                    "Failed visual-analysis artifact could not be archived: "
                    f"{archive_error}"
                )
