from __future__ import annotations

import json
from pathlib import Path

from mediaflow.application.export_catalog import default_export_preset
from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.ports import (
    ExportExecutionResult,
    ExportSequenceRequest,
    ExportTaskDocuments,
    ExportTaskRuntime,
)
from mediaflow.application.task_handler_support import ProjectTaskHandler
from mediaflow.application.task_service import TaskCompletion, TaskContext
from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.enums import ExportFormat, TrackKind
from mediaflow.domain.project_records import ExportHistoryRecord
from mediaflow.domain.storage_names import (
    OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS,
    safe_child_path,
)
from mediaflow.domain.task_commands import (
    BuildSequenceCommand,
    ExportHighlightsCommand,
    ExportSequenceCommand,
)
from mediaflow.domain.tasks import (
    ArtifactReference,
    ExportFileTaskOutcome,
    ExportTaskOutcome,
    SequenceBuildAudioOutcome,
    SequenceBuildTaskOutcome,
    SequenceBuildUnitOutcome,
)


class ExportTaskHandlers(ProjectTaskHandler):
    def __init__(
        self,
        documents: ExportTaskDocuments,
        runtime: ExportTaskRuntime,
        highlights: HighlightService,
    ):
        super().__init__(documents.project_dir)
        self.documents = documents
        self.runtime = runtime
        self.highlights = highlights

    def handle(self, context: TaskContext) -> TaskCompletion:
        command = context.task.command
        if isinstance(command, ExportHighlightsCommand):
            return self._export_highlights(context, command)
        if isinstance(command, BuildSequenceCommand):
            return self._build_sequence(context, command)
        if not isinstance(command, ExportSequenceCommand):
            raise TypeError(f"Unexpected export command: {type(command).__name__}")
        state = self.documents.timeline.load_timeline(command.sequence_id)
        content_revision = self.documents.content_revision()
        preset = command.preset or default_export_preset(
            command.format,
            state.sequence.profile.color_mode,
            state.sequence.profile.fps,
        )
        result = self.runtime.export_sequence(
            state,
            preset,
            command.output_path,
            overwrite=command.overwrite or context.recovered,
            progress=context.report,
            check_cancelled=context.cancellation.raise_if_requested,
        )
        try:
            quality, report_path = self.runtime.analyze_export_quality(
                state,
                preset,
                result,
                report_id=context.task.id,
                progress=context.report,
                check_cancelled=(
                    context.cancellation.raise_if_requested
                ),
            )
            completion = self.completion(
                result.output_path,
                *result.subtitle_files,
                result.project_graph_path,
                report_path,
                *quality.proof_frames,
                outcome=ExportTaskOutcome(
                    files=[self._export_file_outcome(result)]
                ),
            )
            self._defer_export_history(
                context,
                result,
                ExportHistoryRecord(
                    id=context.task.id,
                    task_id=context.task.id,
                    sequence_id=command.sequence_id,
                    output_path=str(result.output_path),
                    format=command.format,
                    preset=preset.model_dump(mode="json"),
                    quality=quality,
                    content_revision=content_revision,
                )
            )
            return completion
        except BaseException as error:
            self._archive_unrecorded_exports(
                [result],
                error,
                quality_report_id=context.task.id,
            )
            raise

    def _build_sequence(
        self,
        context: TaskContext,
        command: BuildSequenceCommand,
    ) -> TaskCompletion:
        state = self.documents.timeline.load_timeline(command.sequence_id)
        content_revision = self.documents.content_revision()
        preset = command.preset or default_export_preset(
            command.format,
            state.sequence.profile.color_mode,
            state.sequence.profile.fps,
        )
        build = self.runtime.build_sequence(
            state,
            preset,
            command.units,
            command.output_path,
            overwrite=command.overwrite or context.recovered,
            progress=context.report,
            check_cancelled=context.cancellation.raise_if_requested,
        )
        result = build.export
        try:
            quality, quality_report_path = self.runtime.analyze_export_quality(
                state,
                preset,
                result,
                report_id=context.task.id,
                progress=context.report,
                check_cancelled=context.cancellation.raise_if_requested,
            )
            report_path = (
                self.project_dir
                / "generated"
                / "build-reports"
                / f"{context.task.id}.json"
            )
            report_payload = {
                "protocol": "mediaflow-sequence-build-report",
                "version": 1,
                "task_id": context.task.id,
                "sequence_id": command.sequence_id,
                "content_revision": content_revision,
                "output": str(result.output_path),
                "units": [
                    {
                        "id": item.unit.id,
                        "start_frame": item.unit.start_frame,
                        "end_frame": item.unit.end_frame,
                        "status": item.status,
                        "cache_key": item.cache_key,
                        "output": str(item.output_path),
                        "sha256": item.sha256,
                    }
                    for item in build.units
                ],
                "audio": {
                    "status": build.audio.status,
                    "cache_key": build.audio.cache_key,
                    "output": (
                        str(build.audio.output_path)
                        if build.audio.output_path is not None
                        else None
                    ),
                    "sha256": build.audio.sha256,
                },
                "assembly": {
                    "status": build.assembly_status,
                    "cache_key": build.assembly_key,
                },
                "quality_report": str(quality_report_path),
                "quality_passed": quality.passed,
            }
            atomic_write_text(
                report_path,
                json.dumps(report_payload, ensure_ascii=False, indent=2),
            )
            outcome = SequenceBuildTaskOutcome(
                output=self._export_file_outcome(result),
                units=[
                    SequenceBuildUnitOutcome(
                        id=item.unit.id,
                        start_frame=item.unit.start_frame,
                        end_frame=item.unit.end_frame,
                        status=item.status,
                        cache_key=item.cache_key,
                        output=ArtifactReference.from_path(
                            self.project_dir,
                            item.output_path,
                        ),
                        sha256=item.sha256,
                    )
                    for item in build.units
                ],
                audio=SequenceBuildAudioOutcome(
                    status=build.audio.status,
                    cache_key=build.audio.cache_key,
                    output=(
                        ArtifactReference.from_path(
                            self.project_dir,
                            build.audio.output_path,
                        )
                        if build.audio.output_path is not None
                        else None
                    ),
                    sha256=build.audio.sha256,
                ),
                assembly_status=build.assembly_status,
                assembly_key=build.assembly_key,
                report=ArtifactReference.from_path(
                    self.project_dir,
                    report_path,
                ),
            )
            completion = self.completion(
                result.output_path,
                result.project_graph_path,
                quality_report_path,
                *quality.proof_frames,
                report_path,
                outcome=outcome,
            )
            self._defer_export_history(
                context,
                result,
                ExportHistoryRecord(
                    id=context.task.id,
                    task_id=context.task.id,
                    sequence_id=command.sequence_id,
                    output_path=str(result.output_path),
                    format=command.format,
                    preset=preset.model_dump(mode="json"),
                    quality=quality,
                    content_revision=content_revision,
                )
            )
            return completion
        except BaseException as error:
            self._archive_unrecorded_exports(
                [result],
                error,
                quality_report_id=context.task.id,
            )
            raise

    def _defer_export_history(
        self,
        context: TaskContext,
        result: ExportExecutionResult,
        history: ExportHistoryRecord,
    ) -> None:
        def commit_history() -> None:
            try:
                self.documents.records.save_export_history(history)
            except BaseException as error:
                self._archive_unrecorded_exports(
                    [result],
                    error,
                    quality_report_id=context.task.id,
                )
                raise

        context.defer_project_change(commit_history)

    def _export_highlights(
        self,
        context: TaskContext,
        command: ExportHighlightsCommand,
    ) -> TaskCompletion:
        output_dir = Path(command.output_dir).resolve()
        candidates = {
            candidate.id: candidate
            for candidate in self.documents.highlights.list_highlights()
        }
        missing = [
            candidate_id
            for candidate_id in command.candidate_ids
            if candidate_id not in candidates
        ]
        if missing:
            raise KeyError(
                "Unknown highlight candidates: "
                + ", ".join(missing)
            )
        outputs: list[str | Path] = []
        outcome_files: list[ExportFileTaskOutcome] = []
        requests: list[ExportSequenceRequest] = []
        def commit_short_sequences() -> None:
            for index, candidate_id in enumerate(
                command.candidate_ids,
                start=1,
            ):
                context.cancellation.raise_if_requested()
                candidate = candidates[candidate_id]
                sequence = self.highlights.create_short_sequence(
                    candidate.id
                )
                state = self.documents.timeline.load_timeline(
                    sequence.id
                )
                preset = command.preset or default_export_preset(
                    ExportFormat.H264,
                    state.sequence.profile.color_mode,
                    state.sequence.profile.fps,
                )
                subtitle_track = next(
                    (
                        track
                        for track in state.tracks
                        if track.kind == TrackKind.SUBTITLE
                        and self.documents.subtitles.list_subtitle_placements(
                            track.id
                        )
                    ),
                    None,
                )
                preset = preset.model_copy(
                    update={
                        "burn_subtitle_track_id": (
                            subtitle_track.id
                            if (
                                subtitle_track
                                and command.burn_subtitles
                            )
                            else None
                        ),
                    }
                )
                prefix = f"{index:02d}-"
                suffix = (
                    f"-{candidate.id[:8]}.{preset.preferred_extension}"
                )
                output_path = safe_child_path(
                    output_dir,
                    candidate.title,
                    prefix=prefix,
                    suffix=suffix,
                    fallback="clip",
                    required_sibling_component_utf16_units=(
                        OUTPUT_WORKSPACE_COMPONENT_RESERVE_UTF16_UNITS
                    ),
                )
                requests.append(
                    ExportSequenceRequest(
                        state=state,
                        preset=preset,
                        output_path=output_path,
                    )
                )
            # Output validation belongs to the same transaction as temporary
            # short-sequence creation.  A conflict must roll the project back
            # before any derived sequence becomes observable.
            self.runtime.preflight_sequence_exports(
                requests,
                overwrite=context.recovered,
            )
        context.commit_project_change(commit_short_sequences)
        results = self.runtime.export_sequences_atomically(
            requests,
            overwrite=context.recovered,
            progress=context.report,
            check_cancelled=context.cancellation.raise_if_requested,
        )
        try:
            for result in results:
                outputs.extend(
                    (
                        result.output_path,
                        *result.subtitle_files,
                        result.project_graph_path,
                    )
                )
                outcome_files.append(
                    self._export_file_outcome(result)
                )
            return self.completion(
                *outputs,
                outcome=ExportTaskOutcome(files=outcome_files),
            )
        except BaseException as error:
            self._archive_unrecorded_exports(
                list(results),
                error,
            )
            raise

    def _archive_unrecorded_exports(
        self,
        results: list[ExportExecutionResult],
        original_error: BaseException,
        *,
        quality_report_id: str | None = None,
    ) -> None:
        try:
            archived = self.runtime.archive_unrecorded_exports(
                results,
                quality_report_id=quality_report_id,
            )
        except BaseException as archive_error:
            original_error.add_note(
                "导出失败后无法完整撤回已发布文件："
                f"{archive_error}"
            )
            return
        if archived:
            original_error.add_note(
                "未登记的导出文件已移至失败归档："
                + ", ".join(str(path) for path in archived)
            )

    def _export_file_outcome(self, result: ExportExecutionResult) -> ExportFileTaskOutcome:
        return ExportFileTaskOutcome(
            output=ArtifactReference.from_path(self.project_dir, result.output_path),
            requested_video_codec=result.requested_video_codec,
            actual_video_codec=result.actual_video_codec,
            hardware_fallback_reason=result.hardware_fallback_reason,
            archived_failed_outputs=[
                ArtifactReference.from_path(self.project_dir, path)
                for path in result.archived_failed_outputs
            ],
        )
