from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from mediaflow.application.ports import SubtitleAcquisitionDocuments
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.domain.asr import AsrResult, RegionAsrPipeline
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.media_association import related_media_paths
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import Asset
from mediaflow.domain.sequence_audio import ProjectedDialogueSegment
from mediaflow.domain.subtitle_file import SubtitleCue, SubtitleFile
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment, SubtitleWord

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

    def transcribe_asset_region(
        self,
        asset_id: str,
        source: str | Path,
        pipeline: RegionAsrPipeline,
        *,
        start_seconds: float,
        end_seconds: float,
        signature: str,
        language: str | None = None,
        check_cancelled: Callable[[], None] | None = None,
        progress: Callable[[OperationProgress], None] | None = None,
    ) -> tuple[AsrResult, bool]:
        media_source = Path(source).resolve(strict=True)
        asset = self.repository.get_asset(asset_id)
        if (
            asset.kind not in {AssetKind.VIDEO, AssetKind.AUDIO}
            or not asset.metadata.has_audio
        ):
            raise ValueError("主要对白轨上的素材必须包含音频")
        cached = self.repository.get_asset_transcript(asset_id, signature)
        if cached is not None:
            if progress:
                progress(
                    OperationProgress.determinate(
                        "transcription_source_cached",
                        completed=1,
                        total=1,
                        unit="items",
                    )
                )
            return cached, True
        if check_cancelled:
            check_cancelled()
        result = pipeline.transcribe_region(
            media_source,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            language=language,
            progress=progress,
        )
        if check_cancelled:
            check_cancelled()
        self.repository.save_asset_transcript(asset_id, signature, result)
        return result, False

    def save_sequence_transcript(
        self,
        sequence_id: str,
        subtitle_asset_id: str,
        projected: tuple[ProjectedDialogueSegment, ...],
        *,
        language: str,
    ) -> SubtitleDocument:
        if not projected:
            raise RuntimeError("主要对白轨范围内没有识别出可用语音")
        project = self.repository.get_project()
        self.repository.get_sequence(sequence_id)
        subtitle_asset = self.repository.get_asset(subtitle_asset_id)
        if subtitle_asset.kind != AssetKind.SUBTITLE:
            raise ValueError("时间线转录文档必须关联生成的字幕素材")
        existing = [
            document
            for document in self.repository.list_subtitle_documents(
                sequence_id=sequence_id
            )
            if document.is_source
            and document.source_document_id is None
            and document.purpose == "sequence_transcript"
        ]
        document = (
            existing[-1].model_copy(
                update={
                    "asset_id": subtitle_asset_id,
                    "media_asset_id": None,
                    "language": language,
                    "purpose": "sequence_transcript",
                }
            )
            if existing
            else SubtitleDocument(
                project_id=project.id,
                asset_id=subtitle_asset_id,
                sequence_id=sequence_id,
                language=language,
                is_source=True,
                purpose="sequence_transcript",
            )
        )
        segments: list[SubtitleSegment] = []
        words: list[SubtitleWord] = []
        for item in projected:
            if item.text.strip():
                segment = SubtitleSegment(
                    document_id=document.id,
                    start_frame=item.start_frame,
                    end_frame=item.end_frame,
                    text=item.text.strip(),
                    confidence=item.confidence,
                )
                segments.append(segment)
                if item.words:
                    words.extend(
                        SubtitleWord(
                            segment_id=segment.id,
                            position=position,
                            start_frame=word.start_frame,
                            end_frame=word.end_frame,
                            text=word.text,
                            confidence=word.confidence,
                            timing_source="recognized",
                        )
                        for position, word in enumerate(item.words)
                    )
                else:
                    words.extend(self._estimated_words(segment))
        if existing:
            self.repository.save_subtitle_document(document)
            self.repository.save_subtitle_segments(document.id, segments)
            self.repository.save_subtitle_words(document.id, words)
        else:
            self.repository.create_subtitle_document(document, segments, words)
        return document

    @staticmethod
    def _estimated_words(segment: SubtitleSegment) -> list[SubtitleWord]:
        tokens = re.findall(
            r"[\u3400-\u9fff]|[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*|[^\s]",
            segment.text,
        )
        if not tokens:
            return []
        duration = segment.end_frame - segment.start_frame
        return [
            SubtitleWord(
                segment_id=segment.id,
                position=position,
                start_frame=segment.start_frame + duration * position // len(tokens),
                end_frame=max(
                    segment.start_frame + duration * position // len(tokens) + 1,
                    segment.start_frame + duration * (position + 1) // len(tokens),
                ),
                text=token,
                timing_source="estimated",
            )
            for position, token in enumerate(tokens)
        ]

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
