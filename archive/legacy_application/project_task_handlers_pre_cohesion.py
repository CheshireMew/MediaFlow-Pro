from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from mediaflow.application.asset_service import AssetService
from mediaflow.application.export_catalog import default_export_preset
from mediaflow.application.highlight_service import HighlightService
from mediaflow.application.ports import TaskExecutionRuntime, TaskHandlerDocuments
from mediaflow.application.subtitle_acquisition import SubtitleAcquisitionService
from mediaflow.application.subtitle_editing import SubtitleEditingService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.task_service import TaskContext, TaskService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.application.translation_service import TranslationService
from mediaflow.domain.asr import AsrResult, AsrSegment, AsrWord
from mediaflow.domain.enums import AssetKind, AssetOrigin, ExportFormat, TaskKind, TrackKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project_records import ExportHistoryRecord
from mediaflow.domain.sequence_audio import (
    build_dialogue_transcription_plan,
    project_dialogue_transcript,
    select_dialogue_transcription_sources,
)
from mediaflow.domain.settings import AsrSettings, GlobalSettings, LlmProviderSettings
from mediaflow.domain.subtitle_file import SubtitleCue, SubtitleFile
from mediaflow.domain.task_commands import (
    AnalyzeDownloadCommand,
    AnalyzeHighlightsCommand,
    AnalyzeLoudnessCommand,
    AnalyzeScenesCommand,
    AnalyzeSequenceBoundsCommand,
    CommandModel,
    DownloadMediaCommand,
    ExportHighlightsCommand,
    ExportSequenceCommand,
    ExportWebClipCommand,
    GenerateProxyCommand,
    GenerateWaveformCommand,
    ImportAssetCommand,
    RenderWebClipCommand,
    TrackSubjectCommand,
    TranscribeSequenceCommand,
    TranslateDocumentCommand,
    TranslateSegmentsCommand,
)
from mediaflow.domain.tasks import ArtifactReference

CommandT = TypeVar("CommandT", bound=CommandModel)


class ProjectTaskHandlers:
    """All background task consumers for one open project."""

    def __init__(
        self,
        documents: TaskHandlerDocuments,
        assets: AssetService,
        runtime: TaskExecutionRuntime,
        subtitle_acquisition: SubtitleAcquisitionService,
        subtitle_editing: SubtitleEditingService,
        subtitle_publication: SubtitlePublicationService,
        highlights: HighlightService,
        translations: TranslationService,
        settings: Callable[[], GlobalSettings],
        active_llm_provider: Callable[[], LlmProviderSettings],
    ):
        self.documents = documents
        self.assets = assets
        self.runtime = runtime
        self.subtitle_acquisition = subtitle_acquisition
        self.subtitle_editing = subtitle_editing
        self.subtitle_publication = subtitle_publication
        self.settings = settings
        self.active_llm_provider = active_llm_provider
        self.highlights = highlights
        self.translations = translations

    def register_with(self, tasks: TaskService) -> None:
        tasks.register(TaskKind.ANALYZE, self.analyze)
        tasks.register(TaskKind.IMPORT, self.import_asset)
        tasks.register(TaskKind.PROXY, self.proxy)
        tasks.register(TaskKind.WAVEFORM, self.waveform)
        tasks.register(TaskKind.DOWNLOAD, self.download)
        tasks.register(TaskKind.EXPORT, self.export)
        tasks.register(TaskKind.TRANSCRIBE, self.transcribe)
        tasks.register(TaskKind.TRANSLATE, self.translate)
        tasks.register(TaskKind.HIGHLIGHT, self.highlight)
        tasks.register(TaskKind.WEB_RENDER, self.render_web_clip)
        tasks.resume_interrupted()

    def render_web_clip(self, context: TaskContext) -> list[ArtifactReference]:
        command = context.task.command
        if not isinstance(command, (RenderWebClipCommand, ExportWebClipCommand)):
            raise TypeError(f"Unexpected web render command: {type(command).__name__}")
        state = self.documents.timeline.load_timeline(command.sequence_id)
        context.report(OperationProgress.indeterminate("web_render_preparing"))
        if isinstance(command, ExportWebClipCommand):
            output = Path(
                self.runtime.render_web_export(
                    state,
                    command.clip_id,
                    command.output_path,
                    command.format,
                    time_ms=command.time_ms,
                    background=command.background,
                    overwrite=command.overwrite or context.recovered,
                    progress=context.report,
                    check_cancelled=context.cancellation.raise_if_requested,
                ).output_path
            )
        else:
            output = self.runtime.render_web_clip(
                state,
                command.clip_id,
                progress=context.report,
                check_cancelled=context.cancellation.raise_if_requested,
            )
        return [self._artifact(output)]

    @property
    def project_dir(self) -> Path:
        return self.documents.project_dir

    def import_asset(self, context: TaskContext) -> list[ArtifactReference]:
        command = context.task.command
        if not isinstance(command, ImportAssetCommand):
            raise TypeError(f"Unexpected import command: {type(command).__name__}")
        context.report(OperationProgress.indeterminate("import_probing"))
        if command.purpose == "subtitle":
            document = self.subtitle_acquisition.import_subtitle_file(
                command.source_path,
                self.assets,
                language=command.language,
                media_asset_id=command.media_asset_id,
            )
            asset = self.documents.catalog.get_asset(document.asset_id)
        else:
            asset = self.assets.import_external(
                command.source_path,
                expected_kind=(AssetKind.IMAGE if command.purpose == "watermark" else None),
            )
        context.report(OperationProgress.indeterminate("import_registering"))
        return [self._artifact(asset.path)]

    def proxy(self, context: TaskContext) -> list[ArtifactReference]:
        command = self._command(context, GenerateProxyCommand)
        asset = self.documents.catalog.get_asset(command.asset_id)
        sequence_id = context.task.sequence_id or self.documents.catalog.get_project().main_sequence_id
        sequence = self.documents.catalog.get_sequence(sequence_id)
        updated = self.runtime.generate_proxy(
            asset,
            sequence.profile,
            progress=context.report,
            check_cancelled=context.cancellation.raise_if_requested,
        )
        return self._artifacts(updated.proxy_path, updated.sdr_preview_proxy_path)

    def waveform(self, context: TaskContext) -> list[ArtifactReference]:
        command = self._command(context, GenerateWaveformCommand)
        asset = self.documents.catalog.get_asset(command.asset_id)
        sequence_id = context.task.sequence_id or self.documents.catalog.get_project().main_sequence_id
        profile = self.documents.catalog.get_sequence(sequence_id).profile
        updated = self.runtime.generate_waveform(
            asset,
            duration_seconds=asset.metadata.duration_frames / profile.fps,
            progress=context.report,
            check_cancelled=context.cancellation.raise_if_requested,
        )
        return self._artifacts(updated.waveform_path)

    def download(self, context: TaskContext) -> list[ArtifactReference]:
        command = self._command(context, DownloadMediaCommand)
        settings = self.settings().download
        request = command.request
        paths = self.runtime.download_media(
            request,
            settings,
            progress=context.report,
        )
        context.report(OperationProgress.indeterminate("download_registering"))
        existing = {
            self.documents.catalog.resolve_asset_path(asset).resolve(): asset
            for asset in self.documents.catalog.list_assets()
        }
        assets = [
            existing.get(path.resolve()) or self.assets.register_output(path, AssetOrigin.DOWNLOAD)
            for path in paths
        ]
        for asset in assets:
            if asset.kind == AssetKind.SUBTITLE and Path(asset.path).suffix.lower() == ".srt":
                self.subtitle_acquisition.create_document_from_subtitle_asset(asset.id)
        return [self._artifact(asset.path) for asset in assets]

    def export(self, context: TaskContext) -> list[ArtifactReference]:
        command = context.task.command
        if isinstance(command, ExportHighlightsCommand):
            return self._export_highlights(context, command)
        if isinstance(command, ExportSequenceCommand):
            state = self.documents.timeline.load_timeline(command.sequence_id)
            content_revision = self.documents.content_revision()
            preset = command.preset or default_export_preset(
                command.format,
                state.sequence.profile.color_mode,
                state.sequence.profile.fps,
            )
            self.runtime.ensure_web_sequence(
                state,
                progress=context.report,
                check_cancelled=context.cancellation.raise_if_requested,
            )
            result = self.runtime.export_sequence(
                state,
                preset,
                command.output_path,
                progress=context.report,
                check_cancelled=context.cancellation.raise_if_requested,
            )
            quality, report_path = self.runtime.analyze_export_quality(
                state,
                preset,
                result,
                report_id=context.task.id,
                progress=context.report,
                check_cancelled=context.cancellation.raise_if_requested,
            )
            self.documents.records.save_export_history(
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
            return self._artifacts(
                result.output_path,
                *result.subtitle_files,
                report_path,
                *quality.proof_frames,
            )
        raise TypeError(f"Unexpected export command: {type(command).__name__}")

    def _export_highlights(
        self,
        context: TaskContext,
        command: ExportHighlightsCommand,
    ) -> list[ArtifactReference]:
        output_dir = Path(command.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        candidates = {
            candidate.id: candidate
            for candidate in self.documents.highlights.list_highlights()
        }
        artifacts: list[ArtifactReference] = []
        service = self.highlights
        for index, candidate_id in enumerate(command.candidate_ids, start=1):
            context.cancellation.raise_if_requested()
            candidate = candidates.get(candidate_id)
            if candidate is None:
                raise KeyError(candidate_id)
            sequence = service.create_short_sequence(candidate.id)
            state = self.documents.timeline.load_timeline(sequence.id)
            self.runtime.ensure_web_sequence(
                state,
                check_cancelled=context.cancellation.raise_if_requested,
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
                    and self.documents.subtitles.list_subtitle_placements(track.id)
                ),
                None,
            )
            preset = preset.model_copy(
                update={
                    "burn_subtitle_track_id": (
                        subtitle_track.id if subtitle_track and command.burn_subtitles else None
                    ),
                }
            )
            safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", candidate.title).strip(" ._")
            filename = f"{index:02d}-{safe_title or 'clip'}-{candidate.id[:8]}.{preset.container}"
            result = self.runtime.export_sequence(
                state,
                preset,
                output_dir / filename,
                progress=context.report,
                check_cancelled=context.cancellation.raise_if_requested,
            )
            artifacts.extend(
                self._artifacts(result.output_path, *result.subtitle_files)
            )
            context.report(
                OperationProgress.determinate(
                    "clip_export_items",
                    completed=index,
                    total=len(command.candidate_ids),
                    unit="items",
                )
            )
        return artifacts

    def transcribe(self, context: TaskContext) -> list[ArtifactReference]:
        command = self._command(context, TranscribeSequenceCommand)
        plan = command.plan
        if not plan.sources or plan.recognition_seconds <= 0:
            raise ValueError("转录计划没有可识别的源音频区间")
        state = self.documents.timeline.load_timeline(plan.sequence_id)
        assets = {asset.id: asset for asset in self.documents.catalog.list_assets()}
        current_plan = build_dialogue_transcription_plan(
            state,
            assets,
            plan.asr,
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
            raise ValueError(
                "主要对白轨包含倒放片段；请改为正向播放或移出主要对白轨"
            )
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
            if not self.runtime.fingerprint_matches(source_path, source_plan.fingerprint):
                raise RuntimeError(
                    f"源素材在转录任务创建后已发生变化：{source_plan.asset_name}"
                )
            source_segments: list[AsrSegment] = []
            source_languages: list[str] = []
            for region in source_plan.regions:
                context.cancellation.raise_if_requested()
                current_region_index = region_index + 1
                region_seconds = (
                    region.duration_frames
                    * plan.fps_denominator
                    / plan.fps_numerator
                )
                region_start_seconds = (
                    region.start_frame
                    * plan.fps_denominator
                    / plan.fps_numerator
                )
                region_end_seconds = (
                    region.end_frame
                    * plan.fps_denominator
                    / plan.fps_numerator
                )
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
                        and value.message_code
                        in {"transcribing", "asr_chunks_transcribing"}
                    ):
                        recognized_in_region = current_duration * (
                            value.completed / value.total
                        )
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
                    check_cancelled=context.cancellation.raise_if_requested,
                    progress=report_region,
                )
                source_languages.append(result.language)
                source_segments.extend(
                    self._offset_asr_segment(segment, region_start_seconds)
                    for segment in result.segments
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
                language=next(iter(languages)) if len(languages) == 1 else "multi",
                duration_seconds=max(
                    (
                        segment.end_seconds
                        for segment in source_segments
                    ),
                    default=0.0,
                ),
                segments=tuple(
                    sorted(
                        source_segments,
                        key=lambda item: (item.start_seconds, item.end_seconds),
                    )
                ),
            )
        latest_state = self.documents.timeline.load_timeline(plan.sequence_id)
        latest_plan = build_dialogue_transcription_plan(
            latest_state,
            {asset.id: asset for asset in self.documents.catalog.list_assets()},
            plan.asr,
            start_frame=plan.timeline_start_frame,
            end_frame=plan.timeline_end_frame,
        )
        if latest_plan.timeline_signature != plan.timeline_signature:
            raise RuntimeError("时间轴在转录期间已发生变化，未写入过期字幕")
        context.report(
            OperationProgress.indeterminate(
                "transcription_mapping_timeline"
            ).with_task_context(
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

        output = (
            self.project_dir
            / "generated"
            / "subtitles"
            / f"{plan.sequence_id}-transcription.srt"
        )
        SubtitleFile.write_srt(
            output,
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
        else:
            subtitle_asset = self.documents.catalog.update_asset(
                subtitle_asset.model_copy(
                    update={"fingerprint": self.runtime.fingerprint_file(output)}
                )
            )
        languages = {
            result.language for result in transcripts.values() if result.language
        }
        document = self.subtitle_acquisition.save_sequence_transcript(
            plan.sequence_id,
            subtitle_asset.id,
            projected,
            language=next(iter(languages)) if len(languages) == 1 else "multi",
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
        self.subtitle_publication.write_document_srt(document.id, output)
        self.documents.catalog.update_asset(
            subtitle_asset.model_copy(
                update={"fingerprint": self.runtime.fingerprint_file(output)}
            )
        )
        return [self._artifact(output)]

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

    def translate(self, context: TaskContext) -> list[ArtifactReference]:
        command = context.task.command
        settings = self.settings()
        service = self.translations
        if isinstance(command, TranslateSegmentsCommand):
            if command.target_document_id:
                service.translate_selected_to_document(
                    command.document_id,
                    command.target_document_id,
                    command.segment_ids,
                    target_language=command.target_language,
                    provider=self.active_llm_provider(),
                    mode=command.mode,
                    glossary=settings.translation.glossary_terms,
                    progress=context.report,
                    check_cancelled=context.cancellation.raise_if_requested,
                )
                document_id = command.target_document_id
            else:
                service.translate_selected_in_document(
                    command.document_id,
                    command.segment_ids,
                    target_language=command.target_language,
                    provider=self.active_llm_provider(),
                    mode=command.mode,
                    glossary=settings.translation.glossary_terms,
                    progress=context.report,
                    check_cancelled=context.cancellation.raise_if_requested,
                )
                document_id = command.document_id
        elif isinstance(command, TranslateDocumentCommand):
            document = service.translate_document(
                command.document_id,
                target_language=command.target_language,
                provider=self.active_llm_provider(),
                mode=command.mode,
                glossary=settings.translation.glossary_terms,
                progress=context.report,
                check_cancelled=context.cancellation.raise_if_requested,
                operation_id=context.task.id,
            )
            document_id = document.id
        else:
            raise TypeError(f"Unexpected translation command: {type(command).__name__}")
        output = self.subtitle_publication.write_document_srt(document_id)
        return [self._artifact(output)]

    def highlight(self, context: TaskContext) -> list[ArtifactReference]:
        command = self._command(context, AnalyzeHighlightsCommand)
        self.highlights.analyze_document(
            command.document_id,
            provider=self.active_llm_provider(),
            progress=context.report,
        )
        return []

    def analyze(self, context: TaskContext) -> list[ArtifactReference]:
        command = context.task.command
        if isinstance(command, AnalyzeDownloadCommand):
            return self._analyze_download(context, command)
        if isinstance(command, AnalyzeSequenceBoundsCommand):
            state = self.documents.timeline.load_timeline(command.sequence_id)
            _analysis, result_path = self.runtime.analyze_sequence_bounds(
                state,
                expected_snapshot_hash=command.snapshot_hash,
                check_cancelled=context.cancellation.raise_if_requested,
                progress=context.report,
            )
            return [self._artifact(result_path)]
        if isinstance(command, AnalyzeLoudnessCommand):
            state = self.documents.timeline.load_timeline(command.sequence_id)
            _metrics, result_path = self.runtime.analyze_loudness(
                state,
                check_cancelled=context.cancellation.raise_if_requested,
                progress=context.report,
            )
            return [self._artifact(result_path)]
        if isinstance(command, AnalyzeScenesCommand):
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
            TimelineEditor(
                self.documents,
                command.sequence_id,
            ).replace_scene_markers(
                clip.id,
                frames,
                expected_clip=clip,
            )
            result_path = self.runtime.write_visual_analysis(
                self.project_dir / "generated" / "visual-analysis" / f"{context.task.id}.json",
                {
                    "type": "scene_detection",
                    "sequence_id": command.sequence_id,
                    "clip_id": clip.id,
                    "threshold": command.threshold,
                    "frames": frames,
                },
            )
            context.report(OperationProgress.indeterminate("scene_detection_saving"))
            return [self._artifact(result_path)]
        if isinstance(command, TrackSubjectCommand):
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
            TimelineEditor(self.documents, command.sequence_id).set_clip_transform_keyframes(
                clip.id,
                keyframes,
                expected_clip=clip,
            )
            result_path = self.runtime.write_visual_analysis(
                self.project_dir / "generated" / "visual-analysis" / f"{context.task.id}.json",
                {
                    "type": command.mode,
                    "sequence_id": command.sequence_id,
                    "clip_id": clip.id,
                    "keyframes": [item.model_dump(mode="json") for item in keyframes],
                },
            )
            context.report(OperationProgress.indeterminate("subject_tracking_saving"))
            return [self._artifact(result_path)]
        raise TypeError(f"Unexpected analysis command: {type(command).__name__}")

    def _analyze_download(
        self,
        context: TaskContext,
        command: AnalyzeDownloadCommand,
    ) -> list[ArtifactReference]:
        context.report(OperationProgress.indeterminate("download_analyzing"))
        settings = self.settings().download
        plan = self.runtime.analyze_download(command.url, settings)
        destination = self.project_dir / "cache" / "download-analysis" / f"{context.task.id}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        context.report(OperationProgress.indeterminate("download_analysis_saving"))
        return [self._artifact(destination)]

    def _artifact(self, value: str | Path) -> ArtifactReference:
        return ArtifactReference.from_path(self.project_dir, value)

    def _artifacts(self, *values: str | Path | None) -> list[ArtifactReference]:
        return [self._artifact(value) for value in values if value]

    @staticmethod
    def _command(context: TaskContext, expected: type[CommandT]) -> CommandT:
        command = context.task.command
        if not isinstance(command, expected):
            raise TypeError(
                f"Task {context.task.id} expected {expected.__name__}, got {type(command).__name__}"
            )
        return command
