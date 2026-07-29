from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mediaflow.application.ports import (
    AnalysisTaskDocuments,
    AnalysisTaskRuntime,
)
from mediaflow.application.task_handler_support import ProjectTaskHandler
from mediaflow.application.task_service import TaskCompletion, TaskContext
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.atomic_file import atomic_write_text, unique_temporary_sibling
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.model_base import new_id
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.settings import GlobalSettings
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
        settings: Callable[[], GlobalSettings],
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
            result_type="scene-detection",
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
            result_type=command.mode.replace("_", "-"),
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
        result_type: str,
        message_code: str,
        payload: dict[str, Any],
        apply_result: Callable[[], None],
    ) -> Path:
        destination = self.project_dir / "generated" / "visual-analysis" / f"{context.task.id}.json"
        staged = unique_temporary_sibling(destination, label="analysis")
        context.report(OperationProgress.indeterminate(message_code))
        context.cancellation.raise_if_requested()
        published = False
        try:
            written = self.runtime.write_visual_analysis(staged, payload)
            if written.resolve() != staged.resolve():
                raise RuntimeError("Visual analysis runtime wrote outside the staged result path")
            with self.documents.transaction():
                apply_result()
                staged.replace(destination)
                published = True
            return destination
        except BaseException as error:
            failed = destination if published else staged
            self._archive_failed_visual_analysis(
                failed,
                task_id=context.task.id,
                result_type=result_type,
                error=error,
            )
            raise

    def _archive_failed_visual_analysis(
        self,
        source: Path,
        *,
        task_id: str,
        result_type: str,
        error: BaseException,
    ) -> None:
        if not source.is_file():
            return
        archive_path = content_addressed_child_path(
            self.project_dir / "archive" / "failed-task-artifacts",
            f"visual-analysis:{task_id}:{result_type}:{new_id()}",
            namespace="va",
            suffix=".json",
        )
        try:
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            source.replace(archive_path)
        except OSError as archive_error:
            error.add_note(f"Failed visual-analysis artifact could not be archived: {archive_error}")
