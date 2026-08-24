from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from mediaflow.application.ports import SubtitleAcquisitionDocuments, SubtitleFileStore
from mediaflow.application.subtitle_publication import (
    SubtitleDocumentPublication,
    SubtitlePublicationService,
)
from mediaflow.application.subtitle_word_timing import estimate_subtitle_words
from mediaflow.domain.asr import AsrResult, RegionAsrPipeline
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.model_base import new_id
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import Asset, ProjectProfile
from mediaflow.domain.sequence_audio import (
    ProjectedDialogueSegment,
    ProjectedDialogueWord,
)
from mediaflow.domain.subtitle_file import SubtitleCue, SubtitleFile
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment, SubtitleWord
from mediaflow.domain.timebase import reframe_interval

if TYPE_CHECKING:
    from mediaflow.application.asset_service import (
        AssetService,
        PreparedAssetRegistration,
    )


@dataclass(frozen=True, slots=True)
class PreparedSubtitleImport:
    document: SubtitleDocument
    subtitle: PreparedAssetRegistration
    related_media: PreparedAssetRegistration | None
    publication: SubtitleDocumentPublication | None


class SubtitleAcquisitionService:
    """Create source subtitle documents from ASR results and subtitle files."""

    def __init__(
        self,
        repository: SubtitleAcquisitionDocuments,
        publication: SubtitlePublicationService,
        subtitle_files: SubtitleFileStore,
    ):
        self.repository = repository
        self.publication = publication
        self.subtitle_files = subtitle_files

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
        media_source = self.subtitle_files.resolve_existing_file(source)
        asset = self.repository.assets.get_asset(asset_id)
        if (
            asset.kind not in {AssetKind.VIDEO, AssetKind.AUDIO}
            or not asset.metadata.has_audio
        ):
            raise ValueError("主要对白轨上的素材必须包含音频")
        cached = self.repository.subtitles.get_asset_transcript(asset_id, signature)
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
        self.repository.subtitles.save_asset_transcript(asset_id, signature, result)
        return result, False

    def save_sequence_transcript(
        self,
        sequence_id: str,
        subtitle_asset_id: str,
        projected: tuple[ProjectedDialogueSegment, ...],
        *,
        document_id: str,
        language: str,
    ) -> SubtitleDocument:
        if not projected:
            raise RuntimeError("主要对白轨范围内没有识别出可用语音")
        project = self.repository.projects.get_project()
        sequence_profile = self.repository.sequences.get_sequence(sequence_id).profile
        main_profile = self.repository.sequences.get_sequence(
            project.main_sequence_id
        ).profile
        subtitle_asset = self.repository.assets.get_asset(subtitle_asset_id)
        if subtitle_asset.kind != AssetKind.SUBTITLE:
            raise ValueError("时间线转录文档必须关联生成的字幕素材")
        existing = [
            document
            for document in self.repository.subtitles.list_subtitle_documents(
                sequence_id=sequence_id
            )
            if document.is_source
            and document.source_document_id is None
            and document.purpose == "sequence_transcript"
        ]
        if existing and existing[-1].id != document_id:
            raise RuntimeError(
                "时间轴转录文档在任务执行期间发生变化，请重新发起转录"
            )
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
                id=document_id,
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
                segment_start, segment_end = reframe_interval(
                    item.start_frame,
                    item.end_frame,
                    sequence_profile,
                    main_profile,
                )
                segment = SubtitleSegment(
                    document_id=document.id,
                    start_frame=segment_start,
                    end_frame=segment_end,
                    text=item.text.strip(),
                    confidence=item.confidence,
                )
                segments.append(segment)
                if item.words:
                    words.extend(
                        self._recognized_word(
                            segment,
                            word,
                            position=position,
                            source_profile=sequence_profile,
                            destination_profile=main_profile,
                        )
                        for position, word in enumerate(item.words)
                    )
                else:
                    words.extend(estimate_subtitle_words(segment))
        if existing:
            self.repository.subtitles.save_subtitle_document(document)
            self.repository.subtitles.save_subtitle_segments(document.id, segments)
            self.repository.subtitles.save_subtitle_words(document.id, words)
        else:
            self.repository.subtitles.create_subtitle_document(document, segments, words)
        return document

    def sequence_transcript_document_id(self, sequence_id: str) -> str:
        existing = [
            document
            for document in self.repository.subtitles.list_subtitle_documents(
                sequence_id=sequence_id
            )
            if document.is_source
            and document.source_document_id is None
            and document.purpose == "sequence_transcript"
        ]
        return existing[-1].id if existing else new_id()

    @staticmethod
    def _recognized_word(
        segment: SubtitleSegment,
        word: ProjectedDialogueWord,
        *,
        position: int,
        source_profile: ProjectProfile,
        destination_profile: ProjectProfile,
    ) -> SubtitleWord:
        start_frame, end_frame = reframe_interval(
            word.start_frame,
            word.end_frame,
            source_profile,
            destination_profile,
        )
        return SubtitleWord(
            segment_id=segment.id,
            position=position,
            start_frame=max(segment.start_frame, start_frame),
            end_frame=min(segment.end_frame, end_frame),
            text=word.text,
            confidence=word.confidence,
            timing_source="recognized",
        )

    def import_subtitle_file(
        self,
        path: str | Path,
        asset_service: AssetService,
        *,
        language: str | None = None,
        media_asset_id: str | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> SubtitleDocument:
        prepared = self.prepare_subtitle_import(
            path,
            asset_service,
            language=language,
            media_asset_id=media_asset_id,
            check_cancelled=check_cancelled,
        )
        return self.commit_subtitle_import(prepared, asset_service)

    def prepare_subtitle_import(
        self,
        path: str | Path,
        asset_service: AssetService,
        *,
        language: str | None = None,
        media_asset_id: str | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> PreparedSubtitleImport:
        source = self.subtitle_files.resolve_existing_file(path)
        if source.suffix.lower() not in {".srt", ".vtt", ".ass", ".ssa"}:
            raise ValueError("支持导入 SRT、WebVTT、ASS 和 SSA 字幕")
        project = self.repository.projects.get_project()
        profile = self.repository.sequences.get_sequence(project.main_sequence_id).profile
        cues = self.subtitle_files.read(
            source,
            fps_numerator=profile.fps_numerator,
            fps_denominator=profile.fps_denominator,
        )
        media_asset, prepared_media = self._prepare_related_media(
            source,
            media_asset_id=media_asset_id,
            asset_service=asset_service,
            check_cancelled=check_cancelled,
        )
        prepared_subtitle = asset_service.prepare_external(
            source,
            expected_kind=AssetKind.SUBTITLE,
        )
        if check_cancelled is not None:
            check_cancelled()
        asset = prepared_subtitle.asset
        associated_media = (
            media_asset
            if media_asset is not None
            else prepared_media.asset
            if prepared_media is not None
            else None
        )
        existing = [
            document
            for document in self.repository.subtitles.list_subtitle_documents(asset.id)
            if document.is_source
            and document.media_asset_id
            == (associated_media.id if associated_media else None)
        ]
        if existing:
            return PreparedSubtitleImport(
                document=existing[0],
                subtitle=prepared_subtitle,
                related_media=None,
                publication=None,
            )
        document, segments = self._build_document_from_cues(
            asset.id,
            source,
            cues,
            language=language,
            media_asset_id=(
                associated_media.id if associated_media is not None else None
            ),
        )
        if check_cancelled is not None:
            check_cancelled()

        return PreparedSubtitleImport(
            document=document,
            subtitle=prepared_subtitle,
            related_media=prepared_media,
            publication=SubtitleDocumentPublication(
                document=document,
                segments=tuple(segments),
            ),
        )

    def commit_subtitle_import(
        self,
        prepared: PreparedSubtitleImport,
        asset_service: AssetService,
    ) -> SubtitleDocument:
        if prepared.publication is None:
            return prepared.document

        def commit_import() -> SubtitleDocument:
            if prepared.related_media is not None:
                committed_media = asset_service.commit_prepared(
                    prepared.related_media
                )
                if committed_media.id != prepared.related_media.asset.id:
                    raise RuntimeError(
                        "关联媒体在导入准备后发生冲突，请重试"
                    )
            committed_subtitle = asset_service.commit_prepared(
                prepared.subtitle
            )
            if committed_subtitle.id != prepared.document.asset_id:
                raise RuntimeError(
                    "字幕素材在导入准备后发生冲突，请重试"
                )
            return prepared.document

        self.publication.commit_prepared_documents(
            commit_import,
            (prepared.publication,),
        )
        return prepared.document

    def create_document_from_subtitle_asset(
        self,
        asset_id: str,
        *,
        language: str | None = None,
    ) -> SubtitleDocument:
        asset = self.repository.assets.get_asset(asset_id)
        document, publication = self.prepare_document_publication(
            asset,
            language=language,
        )
        if publication is not None:
            self.publication.commit_prepared_documents(
                lambda: None,
                (publication,),
            )
        return document

    def prepare_document_publication(
        self,
        asset: Asset,
        *,
        available_assets: Iterable[Asset] = (),
        language: str | None = None,
    ) -> tuple[SubtitleDocument, SubtitleDocumentPublication | None]:
        if asset.kind != AssetKind.SUBTITLE:
            raise ValueError("只有字幕素材可以创建字幕文档")
        existing = [
            document
            for document in self.repository.subtitles.list_subtitle_documents(asset.id)
            if document.is_source
        ]
        if existing:
            return existing[0], None
        source = self.repository.assets.resolve_asset_path(asset)
        if source.suffix.lower() not in {".srt", ".vtt", ".ass", ".ssa"}:
            raise ValueError("该素材不是受支持的字幕文件")
        project = self.repository.projects.get_project()
        profile = self.repository.sequences.get_sequence(project.main_sequence_id).profile
        cues = self.subtitle_files.read(
            source,
            fps_numerator=profile.fps_numerator,
            fps_denominator=profile.fps_denominator,
        )
        media_asset = self._find_related_media(
            source,
            available_assets=available_assets,
        )
        document, segments = self._build_document_from_cues(
            asset.id,
            source,
            cues,
            language=language,
            media_asset_id=media_asset.id if media_asset else None,
        )
        return document, SubtitleDocumentPublication(
            document=document,
            segments=tuple(segments),
        )

    def _build_document_from_cues(
        self,
        asset_id: str,
        source: Path,
        cues: list[SubtitleCue],
        *,
        language: str | None,
        media_asset_id: str | None,
    ) -> tuple[SubtitleDocument, list[SubtitleSegment]]:
        project = self.repository.projects.get_project()
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
        return document, segments

    def _prepare_related_media(
        self,
        subtitle_path: Path,
        *,
        media_asset_id: str | None = None,
        asset_service: AssetService | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> tuple[Asset | None, PreparedAssetRegistration | None]:
        if media_asset_id:
            media_asset = self.repository.assets.get_asset(media_asset_id)
            if media_asset.kind not in {AssetKind.VIDEO, AssetKind.AUDIO}:
                raise ValueError("关联字幕的素材必须是视频或音频")
            return media_asset, None

        existing = self._find_related_media(subtitle_path)
        if existing is not None:
            return existing, None

        if asset_service:
            for candidate in self.subtitle_files.existing_related_media(subtitle_path):
                prepared = asset_service.prepare_external(
                    candidate,
                )
                if check_cancelled is not None:
                    check_cancelled()
                if prepared.asset.kind in {
                    AssetKind.VIDEO,
                    AssetKind.AUDIO,
                }:
                    return None, prepared
                raise ValueError("同名文件不是可用的视频或音频")
        return None, None

    def _find_related_media(
        self,
        subtitle_path: Path,
        *,
        available_assets: Iterable[Asset] = (),
    ) -> Asset | None:
        candidates = self.subtitle_files.related_media_candidates(subtitle_path)
        candidate_positions = {
            str(path).casefold(): position
            for position, path in enumerate(candidates)
        }
        assets_by_id = {
            asset.id: asset
            for asset in self.repository.assets.list_assets()
        }
        assets_by_id.update(
            {asset.id: asset for asset in available_assets}
        )
        matches = []
        for asset in assets_by_id.values():
            if asset.kind not in {AssetKind.VIDEO, AssetKind.AUDIO}:
                continue
            path = self.subtitle_files.canonical_path(
                self.repository.assets.resolve_asset_path(asset)
            )
            position = candidate_positions.get(str(path).casefold())
            if position is not None:
                matches.append(
                    (
                        position,
                        asset.kind != AssetKind.VIDEO,
                        asset,
                    )
                )
        if not matches:
            return None
        matches.sort(key=lambda item: (item[0], item[1], item[2].id))
        return matches[0][2]

    def _save_segments(self, document_id: str, segments: list[SubtitleSegment]) -> None:
        self.publication.commit_document_change(
            document_id,
            lambda: self.repository.subtitles.save_subtitle_segments(
                document_id,
                segments,
            ),
        )
