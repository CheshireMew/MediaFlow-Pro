from __future__ import annotations

from mediaflow.application.ports import TranslationDocuments
from mediaflow.domain.settings import GlossaryTermSettings
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.translation import (
    TranslationComparison,
    TranslationComparisonRow,
)


class TranslationComparisonService:
    """Build one translation presentation snapshot in one project read."""

    def __init__(self, documents: TranslationDocuments) -> None:
        self._documents = documents

    def compare(
        self,
        document_id: str,
        target_language: str,
        glossary_terms: list[GlossaryTermSettings],
    ) -> TranslationComparison:
        selected = self._documents.subtitles.get_subtitle_document(document_id)
        documents = self._documents.subtitles.list_subtitle_documents()
        target: SubtitleDocument | None
        if selected.source_document_id:
            source = self._documents.subtitles.get_subtitle_document(
                selected.source_document_id
            )
            target = selected
        else:
            source = selected
            candidates = [item for item in documents if item.source_document_id == source.id]
            preferred = [item for item in candidates if item.language == target_language]
            target = (preferred or candidates)[-1] if (preferred or candidates) else None
        source_segments = self._documents.subtitles.list_subtitle_segments(source.id)
        target_segments = (
            self._documents.subtitles.list_subtitle_segments(target.id)
            if target is not None
            else []
        )
        rows = self._rows(source_segments, target_segments)
        source_text = "\n".join(segment.text for segment in source_segments).casefold()
        return TranslationComparison(
            source_document_id=source.id,
            target_document_id=target.id if target is not None else "",
            source_language=source.language,
            target_language=target.language if target is not None else target_language,
            glossary_hit_count=sum(
                1 for term in glossary_terms if term.source.casefold() in source_text
            ),
            rows=rows,
        )

    @classmethod
    def _rows(
        cls,
        source_segments: list[SubtitleSegment],
        target_segments: list[SubtitleSegment],
    ) -> list[TranslationComparisonRow]:
        if target_segments and all(segment.source_segment_id for segment in target_segments):
            target_by_source = {
                segment.source_segment_id: segment for segment in target_segments
            }
            return [
                cls._row([source], target_by_source.get(source.id))
                for source in source_segments
            ]

        ordered_source = sorted(
            source_segments,
            key=lambda item: (item.start_frame, item.end_frame, item.id),
        )
        ordered_target = sorted(
            target_segments,
            key=lambda item: (item.start_frame, item.end_frame, item.id),
        )
        matched_source_ids: set[str] = set()
        rows: list[TranslationComparisonRow] = []
        left = 0
        for translated in ordered_target:
            while (
                left < len(ordered_source)
                and ordered_source[left].end_frame <= translated.start_frame
            ):
                left += 1
            overlapping: list[SubtitleSegment] = []
            cursor = left
            while (
                cursor < len(ordered_source)
                and ordered_source[cursor].start_frame < translated.end_frame
            ):
                source = ordered_source[cursor]
                if source.end_frame > translated.start_frame:
                    overlapping.append(source)
                    matched_source_ids.add(source.id)
                cursor += 1
            rows.append(cls._row(overlapping, translated))
        rows.extend(
            cls._row([source], None)
            for source in ordered_source
            if source.id not in matched_source_ids
        )
        return sorted(rows, key=lambda row: (row.start_frame, row.end_frame, row.row_id))

    @staticmethod
    def _row(
        source_segments: list[SubtitleSegment],
        target_segment: SubtitleSegment | None,
    ) -> TranslationComparisonRow:
        source_ids = [segment.id for segment in source_segments]
        start_frames = [segment.start_frame for segment in source_segments]
        end_frames = [segment.end_frame for segment in source_segments]
        if target_segment is not None:
            start_frames.append(target_segment.start_frame)
            end_frames.append(target_segment.end_frame)
        start_frame = min(start_frames) if start_frames else 0
        end_frame = max(end_frames) if end_frames else start_frame + 1
        return TranslationComparisonRow(
            row_id=target_segment.id if target_segment is not None else source_ids[0],
            source_segment_ids=source_ids,
            source_text="\n".join(segment.text for segment in source_segments),
            target_segment_id=target_segment.id if target_segment is not None else "",
            target_text=target_segment.text if target_segment is not None else "",
            start_frame=start_frame,
            end_frame=end_frame,
            status="translated" if target_segment is not None else "missing",
        )
