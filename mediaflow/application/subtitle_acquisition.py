from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from mediaflow.application.ports import SubtitleAcquisitionDocuments
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.domain.asr import AsrEngine, AudioRegionExtractor
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.media_association import related_media_paths
from mediaflow.domain.project import Asset
from mediaflow.domain.subtitle_file import SubtitleCue, SubtitleFile
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.timebase import frames_to_seconds, seconds_to_frames

if TYPE_CHECKING:
    from mediaflow.application.asset_service import AssetService


@dataclass(frozen=True, slots=True)
class PreparedRegionTranscription:
    document: SubtitleDocument
    segments: tuple[SubtitleSegment, ...]
    creates_document: bool


class SubtitleAcquisitionService:
    """Create source subtitle documents from ASR results and subtitle files."""

    def __init__(
        self,
        repository: SubtitleAcquisitionDocuments,
        publication: SubtitlePublicationService,
        region_audio_extractor: AudioRegionExtractor | None = None,
    ):
        self.repository = repository
        self.publication = publication
        self.region_audio_extractor = region_audio_extractor

    def transcribe_asset(
        self,
        asset_id: str,
        engine: AsrEngine,
        *,
        language: str | None = None,
        progress=None,
    ) -> SubtitleDocument:
        asset = self.repository.get_asset(asset_id)
        source = self.repository.resolve_asset_path(asset)
        project = self.repository.get_project()
        profile = self.repository.get_sequence(project.main_sequence_id).profile
        result = engine.transcribe(source, language=language, progress=progress)
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset.id,
            language=result.language,
            is_source=True,
        )
        segments = [
            SubtitleSegment(
                document_id=document.id,
                start_frame=seconds_to_frames(
                    segment.start_seconds,
                    profile.fps_numerator,
                    profile.fps_denominator,
                ),
                end_frame=max(
                    1,
                    seconds_to_frames(
                        segment.end_seconds,
                        profile.fps_numerator,
                        profile.fps_denominator,
                    ),
                ),
                text=segment.text,
                confidence=segment.confidence,
            )
            for segment in result.segments
        ]
        if not segments:
            raise RuntimeError("ASR completed without subtitle segments")
        self.repository.create_subtitle_document(document, segments)
        self.publication.write_document_srt(document.id)
        return document

    def prepare_region_transcription(
        self,
        asset_id: str,
        engine: AsrEngine,
        *,
        start_frame: int,
        end_frame: int,
        document_id: str | None = None,
        language: str | None = None,
        check_cancelled: Callable[[], None] | None = None,
        progress: Callable[[float, str], None] | None = None,
    ) -> PreparedRegionTranscription:
        asset = self.repository.get_asset(asset_id)
        if asset.kind not in {AssetKind.VIDEO, AssetKind.AUDIO}:
            raise ValueError("只有视频或音频素材可以转录")
        start = max(0, int(start_frame))
        end = int(end_frame)
        if end <= start:
            raise ValueError("转录选区的结束帧必须晚于开始帧")
        if asset.metadata.duration_frames > 0 and end > asset.metadata.duration_frames:
            raise ValueError("转录选区超出了素材时长")

        project = self.repository.get_project()
        profile = self.repository.get_sequence(project.main_sequence_id).profile
        creates_document = not document_id
        if document_id:
            document = self.repository.get_subtitle_document(document_id)
            if (document.media_asset_id or document.asset_id) != asset.id:
                raise ValueError("字幕文档与所选转录素材不匹配")
        else:
            document = SubtitleDocument(
                project_id=project.id,
                asset_id=asset.id,
                language=language or "und",
                is_source=True,
            )

        if self.region_audio_extractor is None:
            raise RuntimeError("Region transcription requires an audio region extractor")
        output = self.repository.project_dir / "cache" / "asr-regions" / f"{uuid.uuid4()}.wav"
        output.parent.mkdir(parents=True, exist_ok=True)
        start_seconds = frames_to_seconds(
            start,
            profile.fps_numerator,
            profile.fps_denominator,
        )
        duration_seconds = frames_to_seconds(
            end - start,
            profile.fps_numerator,
            profile.fps_denominator,
        )
        if progress:
            progress(2.0, "extracting_asr_region")
        self.region_audio_extractor.extract(
            self.repository.resolve_asset_path(asset),
            output,
            start_seconds=float(start_seconds),
            duration_seconds=float(duration_seconds),
            check_cancelled=check_cancelled,
        )

        def report(value: float, code: str) -> None:
            if progress:
                progress(10.0 + min(100.0, max(0.0, value)) * 0.85, code)

        asr_result = engine.transcribe(output, language=language, progress=report)
        if creates_document:
            document = document.model_copy(update={"language": asr_result.language})
        segments: list[SubtitleSegment] = []
        for recognized in asr_result.segments:
            recognized_start = start + seconds_to_frames(
                recognized.start_seconds,
                profile.fps_numerator,
                profile.fps_denominator,
            )
            recognized_end = start + seconds_to_frames(
                recognized.end_seconds,
                profile.fps_numerator,
                profile.fps_denominator,
            )
            recognized_start = max(start, min(end - 1, recognized_start))
            recognized_end = max(recognized_start + 1, min(end, recognized_end))
            text = recognized.text.strip()
            if text:
                segments.append(
                    SubtitleSegment(
                        document_id=document.id,
                        start_frame=recognized_start,
                        end_frame=recognized_end,
                        text=text,
                        confidence=recognized.confidence,
                    )
                )
        if not segments:
            raise RuntimeError("所选区间没有识别出可用语音")
        return PreparedRegionTranscription(
            document=document,
            segments=tuple(segments),
            creates_document=creates_document,
        )

    def commit_region_transcription(
        self,
        prepared: PreparedRegionTranscription,
        segments: list[SubtitleSegment] | tuple[SubtitleSegment, ...] | None = None,
    ) -> list[SubtitleSegment]:
        inserted = list(segments if segments is not None else prepared.segments)
        if not inserted:
            raise ValueError("转录结果不能为空")
        if any(item.document_id != prepared.document.id for item in inserted):
            raise ValueError("转录结果不属于目标字幕文档")
        if prepared.creates_document:
            self.repository.create_subtitle_document(prepared.document, inserted)
            self.publication.write_document_srt(prepared.document.id)
        else:
            existing = self.repository.list_subtitle_segments(prepared.document.id)
            self._save_segments(prepared.document.id, [*existing, *inserted])
        return inserted

    def import_subtitle_file(
        self,
        path: str | Path,
        asset_service: AssetService,
        *,
        language: str | None = None,
        media_asset_id: str | None = None,
    ) -> SubtitleDocument:
        source = Path(path).resolve(strict=True)
        if source.suffix.lower() not in {".srt", ".vtt", ".ass", ".ssa"}:
            raise ValueError("支持导入 SRT、WebVTT、ASS 和 SSA 字幕")
        project = self.repository.get_project()
        profile = self.repository.get_sequence(project.main_sequence_id).profile
        cues = SubtitleFile.read(
            source,
            fps_numerator=profile.fps_numerator,
            fps_denominator=profile.fps_denominator,
        )
        media_asset = self._resolve_media_asset(
            source,
            media_asset_id=media_asset_id,
            asset_service=asset_service,
        )
        asset = asset_service.import_external(source)
        if asset.kind != AssetKind.SUBTITLE:
            raise ValueError("SRT 文件没有被识别为字幕素材")
        return self._create_document_from_cues(
            asset.id,
            source,
            cues,
            language=language,
            media_asset_id=media_asset.id if media_asset else None,
        )

    def create_document_from_subtitle_asset(
        self,
        asset_id: str,
        *,
        language: str | None = None,
    ) -> SubtitleDocument:
        asset = self.repository.get_asset(asset_id)
        if asset.kind != AssetKind.SUBTITLE:
            raise ValueError("只有字幕素材可以创建字幕文档")
        existing = [
            document for document in self.repository.list_subtitle_documents(asset.id) if document.is_source
        ]
        if existing:
            return existing[0]
        source = self.repository.resolve_asset_path(asset)
        if source.suffix.lower() not in {".srt", ".vtt", ".ass", ".ssa"}:
            raise ValueError("该素材不是受支持的字幕文件")
        project = self.repository.get_project()
        profile = self.repository.get_sequence(project.main_sequence_id).profile
        cues = SubtitleFile.read(
            source,
            fps_numerator=profile.fps_numerator,
            fps_denominator=profile.fps_denominator,
        )
        media_asset = self._resolve_media_asset(source)
        return self._create_document_from_cues(
            asset.id,
            source,
            cues,
            language=language,
            media_asset_id=media_asset.id if media_asset else None,
        )

    def _create_document_from_cues(
        self,
        asset_id: str,
        source: Path,
        cues: list[SubtitleCue],
        *,
        language: str | None,
        media_asset_id: str | None,
    ) -> SubtitleDocument:
        project = self.repository.get_project()
        document = SubtitleDocument(
            project_id=project.id,
            asset_id=asset_id,
            media_asset_id=media_asset_id,
            language=SubtitleFile.infer_language(source, language),
            is_source=True,
        )
        segments = [
            SubtitleSegment(
                document_id=document.id,
                start_frame=cue.start_frame,
                end_frame=cue.end_frame,
                text=cue.text,
            )
            for cue in cues
        ]
        self.repository.create_subtitle_document(document, segments)
        self.publication.write_document_srt(document.id)
        return document

    def _resolve_media_asset(
        self,
        subtitle_path: Path,
        *,
        media_asset_id: str | None = None,
        asset_service: AssetService | None = None,
    ) -> Asset | None:
        if media_asset_id:
            media_asset = self.repository.get_asset(media_asset_id)
            if media_asset.kind not in {AssetKind.VIDEO, AssetKind.AUDIO}:
                raise ValueError("关联字幕的素材必须是视频或音频")
            return media_asset

        candidates = [path.resolve() for path in related_media_paths(subtitle_path)]
        candidate_positions = {str(path).casefold(): position for position, path in enumerate(candidates)}
        existing = []
        for asset in self.repository.list_assets():
            if asset.kind not in {AssetKind.VIDEO, AssetKind.AUDIO}:
                continue
            path = self.repository.resolve_asset_path(asset).resolve()
            position = candidate_positions.get(str(path).casefold())
            if position is not None:
                existing.append((position, asset.kind != AssetKind.VIDEO, asset))
        if existing:
            existing.sort(key=lambda item: (item[0], item[1], item[2].id))
            return existing[0][2]

        if asset_service:
            for candidate in candidates:
                if not candidate.is_file() or candidate.stat().st_size <= 0:
                    continue
                asset = asset_service.import_external(candidate)
                if asset.kind in {AssetKind.VIDEO, AssetKind.AUDIO}:
                    return asset
                raise ValueError("同名文件不是可用的视频或音频")
        return None

    def _save_segments(self, document_id: str, segments: list[SubtitleSegment]) -> None:
        self.repository.save_subtitle_segments(document_id, segments)
        self.publication.write_document_srt(document_id)
