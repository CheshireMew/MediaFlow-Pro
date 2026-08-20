from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from mediaflow.application.asset_service import AssetService
from mediaflow.application.ports import (
    TranscriptionTaskDocuments,
    TranscriptionTaskRuntime,
)
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_editing import SubtitleEditingService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.task_handler_support import ProjectTaskHandler
from mediaflow.application.task_service import TaskCompletion, TaskContext
from mediaflow.application.timeline_clock import project_frame_profile
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.asr import (
    AsrResult,
    AsrSegment,
    AsrWord,
    RegionAsrPipeline,
    TranscriptionPlan,
    TranscriptionSourcePlan,
)
from mediaflow.domain.enums import AssetOrigin, TrackKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import Asset
from mediaflow.domain.sequence_audio import (
    DialogueTranscriptionSelection,
    ProjectedDialogueSegment,
    build_dialogue_transcription_plan,
    project_dialogue_transcript,
    select_dialogue_transcription_sources,
)
from mediaflow.domain.settings import AsrSettings
from mediaflow.domain.subtitle_file import SubtitleCue
from mediaflow.domain.task_commands import TranscribeSequenceCommand
from mediaflow.domain.timeline import TimelineState


@dataclass(slots=True)
class _TranscriptionProgress:
    region_index: int = 0
    recognized_seconds: float = 0.0


class TranscriptionTaskHandler(ProjectTaskHandler):
    def __init__(
        self,
        documents: TranscriptionTaskDocuments,
        assets: AssetService,
        runtime: TranscriptionTaskRuntime,
        subtitle_acquisition: SubtitleAcquisitionService,
        subtitle_editing: SubtitleEditingService,
        subtitle_publication: SubtitlePublicationService,
    ):
        super().__init__(documents.project_dir)
        self.documents = documents
        self.assets = assets
        self.runtime = runtime
        self.subtitle_acquisition = subtitle_acquisition
        self.subtitle_editing = subtitle_editing
        self.subtitle_publication = subtitle_publication

    def handle(self, context: TaskContext) -> TaskCompletion:
        command = self.command(context, TranscribeSequenceCommand)
        plan = command.plan
        state, assets, selection = self._validated_selection(plan)
        pipeline = self.runtime.create_asr_pipeline(
            plan.asr,
            check_cancelled=context.cancellation.raise_if_requested,
        )
        transcripts = self._transcribe_sources(
            context,
            plan,
            assets,
            pipeline,
        )
        self._require_current_plan(plan)
        context.report(
            OperationProgress.indeterminate("transcription_mapping_timeline").with_task_context(
                item_index=plan.region_count,
                item_total=plan.region_count,
                item_label="",
                overall_completed=plan.recognition_seconds,
                overall_total=plan.recognition_seconds,
                overall_unit="media_seconds",
            )
        )
        projected = project_dialogue_transcript(
            state,
            selection.clips,
            transcripts,
            start_frame=plan.timeline_start_frame,
            end_frame=plan.timeline_end_frame,
        )
        if not projected:
            raise RuntimeError("主要对白轨范围内没有识别出可用语音")
        context.cancellation.raise_if_requested()
        output = self._defer_transcript_commit(
            context,
            command,
            plan,
            state,
            projected,
            transcripts,
        )
        return self.completion(output)

    def _validated_selection(
        self,
        plan: TranscriptionPlan,
    ) -> tuple[
        TimelineState,
        dict[str, Asset],
        DialogueTranscriptionSelection,
    ]:
        state = self.documents.timeline.load_timeline(plan.sequence_id)
        assets = {asset.id: asset for asset in self.documents.assets.list_assets()}
        current_plan = build_dialogue_transcription_plan(
            state,
            assets,
            plan.asr,
            project_profile=project_frame_profile(
                self.documents.projects,
                self.documents.sequences,
            ),
            start_frame=plan.timeline_start_frame,
            end_frame=plan.timeline_end_frame,
        )
        if current_plan.timeline_signature != plan.timeline_signature:
            raise RuntimeError("时间轴在转录任务创建后已发生变化，请重新发起转录")
        selection = select_dialogue_transcription_sources(
            state,
            assets,
            start_frame=plan.timeline_start_frame,
            end_frame=plan.timeline_end_frame,
        )
        if selection.track_id != plan.dialogue_track_id:
            raise RuntimeError("主要对白轨在转录任务创建后已发生变化，请重新发起转录")
        if any(clip.speed_numerator <= 0 for clip in selection.clips):
            raise ValueError("主要对白轨包含倒放片段；请改为正向播放或移出主要对白轨")
        return state, assets, selection

    def _transcribe_sources(
        self,
        context: TaskContext,
        plan: TranscriptionPlan,
        assets: dict[str, Asset],
        pipeline: RegionAsrPipeline,
    ) -> dict[str, AsrResult]:
        transcripts: dict[str, AsrResult] = {}
        progress = _TranscriptionProgress()
        for source_plan in plan.sources:
            asset = assets.get(source_plan.asset_id)
            if asset is None:
                raise RuntimeError(f"转录计划中的素材已经不存在：{source_plan.asset_name}")
            source_path = self.documents.assets.resolve_asset_path(asset)
            if not self.runtime.fingerprint_matches(
                source_path,
                source_plan.fingerprint,
            ):
                raise RuntimeError(f"源素材在转录任务创建后已发生变化：{source_plan.asset_name}")
            transcripts[source_plan.asset_id] = self._transcribe_source(
                context,
                plan,
                source_plan,
                source_path,
                pipeline,
                progress,
            )
        return transcripts

    def _transcribe_source(
        self,
        context: TaskContext,
        plan: TranscriptionPlan,
        source_plan: TranscriptionSourcePlan,
        source_path: Path,
        pipeline: RegionAsrPipeline,
        progress: _TranscriptionProgress,
    ) -> AsrResult:
        source_segments: list[AsrSegment] = []
        source_languages: list[str] = []
        for region in source_plan.regions:
            context.cancellation.raise_if_requested()
            region_seconds = region.duration_frames * plan.fps_denominator / plan.fps_numerator
            region_start_seconds = region.start_frame * plan.fps_denominator / plan.fps_numerator
            region_end_seconds = region.end_frame * plan.fps_denominator / plan.fps_numerator
            current_index = progress.region_index + 1
            recognized_in_region_max = 0.0

            def report_region(
                value: OperationProgress,
                *,
                current_duration: float = region_seconds,
                current_region_index: int = current_index,
                current_label: str = source_plan.asset_name,
            ) -> None:
                nonlocal recognized_in_region_max
                recognized = 0.0
                if (
                    value.mode == "determinate"
                    and value.completed is not None
                    and value.total is not None
                    and value.message_code in {"transcribing", "asr_chunks_transcribing"}
                ):
                    recognized = current_duration * (value.completed / value.total)
                if value.message_code == "transcription_source_cached":
                    recognized = current_duration
                recognized_in_region_max = max(
                    recognized_in_region_max,
                    recognized,
                )
                context.report(
                    value.with_task_context(
                        item_index=current_region_index,
                        item_total=plan.region_count,
                        item_label=current_label,
                        overall_completed=min(
                            plan.recognition_seconds,
                            progress.recognized_seconds + recognized_in_region_max,
                        ),
                        overall_total=plan.recognition_seconds,
                        overall_unit="media_seconds",
                    )
                )

            signature = self._region_transcript_signature(
                source_plan.asset_id,
                source_plan.fingerprint.model_dump(mode="json"),
                region.start_frame,
                region.end_frame,
                plan.asr,
            )
            result, _cached = self.subtitle_acquisition.transcribe_asset_region(
                source_plan.asset_id,
                source_path,
                pipeline,
                start_seconds=region_start_seconds,
                end_seconds=region_end_seconds,
                signature=signature,
                language=(None if plan.asr.language == "auto" else plan.asr.language),
                check_cancelled=(context.cancellation.raise_if_requested),
                progress=report_region,
            )
            source_languages.append(result.language)
            source_segments.extend(
                self._offset_asr_segment(segment, region_start_seconds) for segment in result.segments
            )
            progress.recognized_seconds += region_seconds
            progress.region_index += 1
            context.report(
                OperationProgress.determinate(
                    "transcription_regions_completed",
                    completed=progress.recognized_seconds,
                    total=plan.recognition_seconds,
                    unit="media_seconds",
                ).with_task_context(
                    item_index=progress.region_index,
                    item_total=plan.region_count,
                    item_label=source_plan.asset_name,
                    overall_completed=progress.recognized_seconds,
                    overall_total=plan.recognition_seconds,
                    overall_unit="media_seconds",
                )
            )
        languages = {value for value in source_languages if value}
        return AsrResult(
            language=(next(iter(languages)) if len(languages) == 1 else "multi"),
            duration_seconds=max(
                (segment.end_seconds for segment in source_segments),
                default=0.0,
            ),
            segments=tuple(
                sorted(
                    source_segments,
                    key=lambda item: (
                        item.start_seconds,
                        item.end_seconds,
                    ),
                )
            ),
        )

    def _require_current_plan(self, plan: TranscriptionPlan) -> None:
        latest_state = self.documents.timeline.load_timeline(plan.sequence_id)
        latest_assets = {asset.id: asset for asset in self.documents.assets.list_assets()}
        latest_plan = build_dialogue_transcription_plan(
            latest_state,
            latest_assets,
            plan.asr,
            project_profile=project_frame_profile(
                self.documents.projects,
                self.documents.sequences,
            ),
            start_frame=plan.timeline_start_frame,
            end_frame=plan.timeline_end_frame,
        )
        if latest_plan.timeline_signature != plan.timeline_signature:
            raise RuntimeError("时间轴在转录期间已发生变化，未写入过期字幕")

    def _defer_transcript_commit(
        self,
        context: TaskContext,
        command: TranscribeSequenceCommand,
        plan: TranscriptionPlan,
        state: TimelineState,
        projected: tuple[ProjectedDialogueSegment, ...],
        transcripts: dict[str, AsrResult],
    ) -> Path:
        document_id = self.subtitle_acquisition.sequence_transcript_document_id(plan.sequence_id)
        output = self.subtitle_publication.document_srt_path(document_id)
        languages = {result.language for result in transcripts.values() if result.language}

        def prepare_output(destination: Path) -> None:
            self.subtitle_acquisition.subtitle_files.write_srt(
                destination,
                [
                    SubtitleCue(
                        start_frame=item.start_frame,
                        end_frame=item.end_frame,
                        text=item.text,
                    )
                    for item in projected
                ],
                fps_numerator=state.sequence.profile.fps_numerator,
                fps_denominator=state.sequence.profile.fps_denominator,
            )

        def save_transcript() -> Asset:
            resolved_output = output.resolve()
            subtitle_asset = next(
                (
                    asset
                    for asset in self.documents.assets.list_assets()
                    if self.documents.assets.resolve_asset_path(asset).resolve() == resolved_output
                ),
                None,
            )
            if subtitle_asset is None:
                subtitle_asset = self.assets.register_output(
                    output,
                    AssetOrigin.GENERATED,
                )
            document = self.subtitle_acquisition.save_sequence_transcript(
                plan.sequence_id,
                subtitle_asset.id,
                projected,
                document_id=document_id,
                language=(next(iter(languages)) if len(languages) == 1 else "multi"),
            )
            self.subtitle_editing.smart_split_document(
                document.id,
                text_limit=plan.asr.smart_split_limit,
            )
            subtitle_track = next(
                (track for track in state.tracks if track.kind == TrackKind.SUBTITLE and not track.locked),
                None,
            )
            if subtitle_track is None:
                subtitle_track = TimelineEditor(
                    self.documents,
                    command.sequence_id,
                ).add_track(TrackKind.SUBTITLE)
            self.documents.subtitles.place_subtitle_document(
                document.id,
                subtitle_track.id,
                follow_clips=False,
            )
            return subtitle_asset

        def update_fingerprint(
            destination: Path,
            subtitle_asset: Asset,
        ) -> None:
            self.documents.assets.update_asset(
                subtitle_asset.model_copy(update={"fingerprint": self.runtime.fingerprint_file(destination)})
            )

        def commit_transcript() -> None:
            self.subtitle_publication.commit_document_change(
                document_id,
                save_transcript,
                destination=output,
                prepare_output=prepare_output,
                after_write=update_fingerprint,
            )

        context.defer_project_change(commit_transcript)
        return output

    @staticmethod
    def _region_transcript_signature(
        asset_id: str,
        fingerprint: dict,
        start_frame: int,
        end_frame: int,
        settings: AsrSettings,
    ) -> str:
        payload = {
            "asset_id": asset_id,
            "fingerprint": fingerprint,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "pipeline": 2,
            "asr": settings.model_dump(
                mode="json",
                exclude={"smart_split_limit", "parallel_chunks"},
            ),
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _offset_asr_segment(
        segment: AsrSegment,
        offset_seconds: float,
    ) -> AsrSegment:
        return AsrSegment(
            start_seconds=segment.start_seconds + offset_seconds,
            end_seconds=segment.end_seconds + offset_seconds,
            text=segment.text,
            confidence=segment.confidence,
            words=tuple(
                AsrWord(
                    start_seconds=word.start_seconds + offset_seconds,
                    end_seconds=word.end_seconds + offset_seconds,
                    text=word.text,
                    confidence=word.confidence,
                )
                for word in segment.words
            ),
        )
