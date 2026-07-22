from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from mediaflow.application.ports import SubtitleAcquisitionDocuments
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.domain.asr import AsrEngine
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.media_association import related_media_paths
from mediaflow.domain.project import Asset
from mediaflow.domain.subtitle_file import SubtitleCue, SubtitleFile
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.timebase import seconds_to_frames

if TYPE_CHECKING:
    from mediaflow.application.asset_service import AssetService


class SubtitleAcquisitionService:
    """Create source subtitle documents from ASR results and subtitle files."""

    def __init__(
        self,
        repository: SubtitleAcquisitionDocuments,
        publication: SubtitlePublicationService,
    ):
        self.repository = repository
        self.publication = publication

    def transcribe_sequence_audio(
        self,
        sequence_id: str,
        audio_asset_id: str,
        source: str | Path,
        engine: AsrEngine,
        *,
        start_frame: int,
        end_frame: int,
        language: str | None = None,
        check_cancelled: Callable[[], None] | None = None,
        progress: Callable[[float, str], None] | None = None,
    ) -> SubtitleDocument:
        audio_source = Path(source).resolve(strict=True)
        audio_asset = self.repository.get_asset(audio_asset_id)
        if audio_asset.kind != AssetKind.AUDIO:
            raise ValueError("时间轴转录源必须是生成的音频素材")
        sequence = self.repository.get_sequence(sequence_id)
        start = max(0, int(start_frame))
        end = int(end_frame)
        if end <= start:
            raise ValueError("当前时间轴没有可转录的范围")
        project = self.repository.get_project()
        existing = [
            document
            for document in self.repository.list_subtitle_documents(sequence_id=sequence_id)
            if document.is_source and document.source_document_id is None
        ]
        if existing:
            document = existing[-1].model_copy(update={"asset_id": audio_asset_id})
        else:
            document = SubtitleDocument(
                project_id=project.id,
                asset_id=audio_asset_id,
                sequence_id=sequence_id,
                language=language or "und",
                is_source=True,
            )

        def report(value: float, code: str) -> None:
            if progress:
                progress(15.0 + min(100.0, max(0.0, value)) * 0.8, code)

        if check_cancelled:
            check_cancelled()
        asr_result = engine.transcribe(audio_source, language=language, progress=report)
        document = document.model_copy(update={"language": asr_result.language})
        segments: list[SubtitleSegment] = []
        for recognized in asr_result.segments:
            recognized_start = start + seconds_to_frames(
                recognized.start_seconds,
                sequence.profile.fps_numerator,
                sequence.profile.fps_denominator,
            )
            recognized_end = start + seconds_to_frames(
                recognized.end_seconds,
                sequence.profile.fps_numerator,
                sequence.profile.fps_denominator,
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
            raise RuntimeError("当前时间轴范围内没有识别出可用语音")
        if existing:
            self.repository.save_subtitle_document(document)
            self.repository.save_subtitle_segments(document.id, segments)
        else:
            self.repository.create_subtitle_document(document, segments)
        self.publication.write_document_srt(document.id)
        return document

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
