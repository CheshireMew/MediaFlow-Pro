from __future__ import annotations

import hashlib
import json
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
from mediaflow.domain.asr import AsrResult, AsrSegment, AsrWord
from mediaflow.domain.enums import AssetOrigin, TrackKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import Asset
from mediaflow.domain.sequence_audio import (
    build_dialogue_transcription_plan,
    project_dialogue_transcript,
    select_dialogue_transcription_sources,
)
from mediaflow.domain.settings import AsrSettings
from mediaflow.domain.subtitle_file import SubtitleCue, SubtitleFile
from mediaflow.domain.task_commands import TranscribeSequenceCommand


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
        state = self.documents.timeline.load_timeline(plan.sequence_id)
        assets = {asset.id: asset for asset in self.documents.catalog.list_assets()}
        current_plan = build_dialogue_transcription_plan(
            state,
            assets,
            plan.asr,
            project_profile=project_frame_profile(self.documents.catalog),
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
        pipeline = self.runtime.create_asr_pipeline(
            plan.asr,
            check_cancelled=context.cancellation.raise_if_requested,
        )
        transcripts: dict[str, AsrResult] = {}
        region_total = plan.region_count
        region_index = 0
        recognized_before_region = 0.0
        for source_plan in plan.sources:
            asset = assets.get(source_plan.asset_id)
            if asset is None:
                raise RuntimeError(f"转录计划中的素材已经不存在：{source_plan.asset_name}")
            source_path = self.documents.catalog.resolve_asset_path(asset)
            if not self.runtime.fingerprint_matches(
                source_path,
                source_plan.fingerprint,
            ):
                raise RuntimeError(f"源素材在转录任务创建后已发生变化：{source_plan.asset_name}")
            source_segments: list[AsrSegment] = []
            source_languages: list[str] = []
            for region in source_plan.regions:
                context.cancellation.raise_if_requested()
                current_region_index = region_index + 1
                region_seconds = region.duration_frames * plan.fps_denominator / plan.fps_numerator
                region_start_seconds = region.start_frame * plan.fps_denominator / plan.fps_numerator
                region_end_seconds = region.end_frame * plan.fps_denominator / plan.fps_numerator
                recognized_in_region_max = 0.0

                def report_region(
                    value: OperationProgress,
                    *,
                    completed_before: float = recognized_before_region,
                    current_duration: float = region_seconds,
                    current_index: int = current_region_index,
                    label: str = source_plan.asset_name,
                ) -> None:
                    nonlocal recognized_in_region_max
                    recognized_in_region = 0.0
                    if (
                        value.mode == "determinate"
                        and value.completed is not None
                        and value.total is not None
                        and value.message_code in {"transcribing", "asr_chunks_transcribing"}
                    ):
                        recognized_in_region = current_duration * (value.completed / value.total)
                    if value.message_code == "transcription_source_cached":
                        recognized_in_region = current_duration
                    recognized_in_region_max = max(
                        recognized_in_region_max,
                        recognized_in_region,
                    )
                    context.report(
                        value.with_task_context(
                            item_index=current_index,
                            item_total=region_total,
                            item_label=label,
                            overall_completed=min(
                                plan.recognition_seconds,
                                completed_before + recognized_in_region_max,
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
                recognized_before_region += region_seconds
                region_index += 1
                context.report(
                    OperationProgress.determinate(
                        "transcription_regions_completed",
                        completed=recognized_before_region,
                        total=plan.recognition_seconds,
                        unit="media_seconds",
                    ).with_task_context(
                        item_index=region_index,
                        item_total=region_total,
                        item_label=source_plan.asset_name,
                        overall_completed=recognized_before_region,
                        overall_total=plan.recognition_seconds,
                        overall_unit="media_seconds",
                    )
                )
            languages = {value for value in source_languages if value}
            transcripts[source_plan.asset_id] = AsrResult(
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
        latest_state = self.documents.timeline.load_timeline(plan.sequence_id)
        latest_plan = build_dialogue_transcription_plan(
            latest_state,
            {asset.id: asset for asset in self.documents.catalog.list_assets()},
            plan.asr,
            project_profile=project_frame_profile(self.documents.catalog),
            start_frame=plan.timeline_start_frame,
            end_frame=plan.timeline_end_frame,
        )
        if latest_plan.timeline_signature != plan.timeline_signature:
            raise RuntimeError("时间轴在转录期间已发生变化，未写入过期字幕")
        context.report(
            OperationProgress.indeterminate("transcription_mapping_timeline").with_task_context(
                item_index=region_total,
                item_total=region_total,
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
        document_id = self.subtitle_acquisition.sequence_transcript_document_id(
            plan.sequence_id
        )
        output = self.subtitle_publication.document_srt_path(document_id)
        languages = {result.language for result in transcripts.values() if result.language}

        def prepare_output(destination: Path) -> None:
            SubtitleFile.write_srt(
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
                    for asset in self.documents.catalog.list_assets()
                    if self.documents.catalog.resolve_asset_path(asset).resolve()
                    == resolved_output
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
                language=(
                    next(iter(languages))
                    if len(languages) == 1
                    else "multi"
                ),
            )
            self.subtitle_editing.smart_split_document(
                document.id,
                text_limit=plan.asr.smart_split_limit,
            )
            subtitle_track = next(
                (
                    track
                    for track in state.tracks
                    if track.kind == TrackKind.SUBTITLE and not track.locked
                ),
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
            self.documents.catalog.update_asset(
                subtitle_asset.model_copy(
                    update={
                        "fingerprint": self.runtime.fingerprint_file(
                            destination
                        )
                    }
                )
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
        return self.completion(output)

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
