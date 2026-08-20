from __future__ import annotations

from PySide6.QtCore import Slot

from mediaflow.domain.subtitles import SubtitleSegment
from mediaflow.domain.task_commands import TranslateDocumentCommand, TranslateSegmentsCommand
from mediaflow.domain.translation import validate_translation_mode

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import SubtitlePresentationScope


class SubtitleTranslationController(ControllerFacet[SubtitlePresentationScope]):
    @Slot(str, str, str)
    @report_ui_errors
    def translateDocument(self, document_id: str, target_language: str, mode: str) -> None:
        self._session._require_writable()
        if not document_id:
            raise ValueError("请先选择源字幕文档")
        language = target_language.strip() or self._session.state.service_settings.translation.target_language
        translation_mode = validate_translation_mode(mode)
        self._session.tasks.start(
            TranslateDocumentCommand(
                document_id=document_id,
                target_language=language or "und",
                mode=translation_mode,
            ),
        )

    @Slot()
    @report_ui_errors
    def translateSelectedSubtitleSegments(self) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        if not self._session.state.selection.subtitle_segment_ids:
            raise ValueError("请先选择要翻译的字幕段")
        mode = self._session.state.service_settings.translation.mode
        language = self._session.state.service_settings.translation.target_language
        self._session.tasks.start(
            TranslateSegmentsCommand(
                document_id=self._session.state.selection.document_id,
                segment_ids=list(self._session.state.selection.subtitle_segment_ids),
                target_language=language or "und",
                mode=mode,
            ),
        )

    @Slot(str, str, "QVariantList", str, str)
    @report_ui_errors
    def translateComparisonSegments(
        self,
        source_document_id: str,
        target_document_id: str,
        segment_ids: list[str],
        target_language: str,
        mode: str,
    ) -> None:
        self._session._require_writable()
        if not source_document_id or not target_document_id:
            raise ValueError("请先完成整篇翻译，再局部重译")
        if not segment_ids:
            raise ValueError("请先选择要重译的字幕段")
        translation_mode = validate_translation_mode(mode)
        self._session.tasks.start(
            TranslateSegmentsCommand(
                document_id=source_document_id,
                target_document_id=target_document_id,
                segment_ids=[str(item) for item in segment_ids],
                target_language=target_language or "und",
                mode=translation_mode,
            )
        )

    @Slot(str, str, result="QVariantMap")
    def translationComparison(self, document_id: str, target_language: str) -> dict:
        if not document_id or not self._session.state.binding.current:
            return {}
        selected = self._session.state.binding.require_current().get_subtitle_document(document_id)
        documents = self._session.state.binding.require_current().list_subtitle_documents()
        if selected.source_document_id:
            source = self._session.state.binding.require_current().get_subtitle_document(
                selected.source_document_id
            )
            target = selected
        else:
            source = selected
            candidates = [item for item in documents if item.source_document_id == source.id]
            preferred = [item for item in candidates if item.language == target_language]
            target = (preferred or candidates)[-1] if (preferred or candidates) else None
        source_segments = self._session.state.binding.require_current().list_subtitle_segments(source.id)
        target_segments = (
            self._session.state.binding.require_current().list_subtitle_segments(target.id)
            if target is not None
            else []
        )
        rows: list[dict] = []
        matched_source_ids: set[str] = set()
        if target_segments and all(segment.source_segment_id for segment in target_segments):
            target_by_source = {segment.source_segment_id: segment for segment in target_segments}
            for source_segment in source_segments:
                translated = target_by_source.get(source_segment.id)
                if translated is not None:
                    matched_source_ids.add(source_segment.id)
                rows.append(
                    self._translation_comparison_row(
                        source_segments=[source_segment],
                        target_segment=translated,
                    )
                )
        else:
            for translated in target_segments:
                overlapping = [
                    source_segment
                    for source_segment in source_segments
                    if source_segment.end_frame > translated.start_frame
                    and source_segment.start_frame < translated.end_frame
                ]
                matched_source_ids.update(item.id for item in overlapping)
                rows.append(
                    self._translation_comparison_row(
                        source_segments=overlapping,
                        target_segment=translated,
                    )
                )
            for source_segment in source_segments:
                if source_segment.id not in matched_source_ids:
                    rows.append(
                        self._translation_comparison_row(
                            source_segments=[source_segment],
                            target_segment=None,
                        )
                    )
            rows.sort(key=lambda row: (row["startFrame"], row["endFrame"], row["rowId"]))
        source_text = "\n".join(segment.text for segment in source_segments).casefold()
        glossary_hits = sum(
            1
            for term in self._session.state.service_settings.translation.glossary_terms
            if term.source.casefold() in source_text
        )
        return {
            "sourceDocumentId": source.id,
            "targetDocumentId": target.id if target is not None else "",
            "sourceLanguage": source.language,
            "targetLanguage": target.language if target is not None else target_language,
            "glossaryHitCount": glossary_hits,
            "rows": rows,
        }

    @Slot(str, str, str, result=bool)
    @report_ui_errors
    def updateTranslationSegment(
        self,
        target_document_id: str,
        target_segment_id: str,
        text: str,
    ) -> bool:
        self._session._require_writable()
        segment = next(
            item
            for item in self._session.state.binding.require_current().list_subtitle_segments(
                target_document_id
            )
            if item.id == target_segment_id
        )
        self._session.state.binding.require_current().update_subtitle_segment(
            target_document_id,
            target_segment_id,
            start_frame=segment.start_frame,
            end_frame=segment.end_frame,
            text=text,
        )
        self._session.projectors.subtitles.refresh_documents()
        self._session.projectors.timeline.refresh_preview_subtitles()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session._set_status("译文已保存")
        self._session.updates.commit(project=True)
        self._session.updates.commit(history=True)
        return True

    @staticmethod
    def _translation_comparison_row(
        *,
        source_segments: list[SubtitleSegment],
        target_segment: SubtitleSegment | None,
    ) -> dict:
        source_ids = [segment.id for segment in source_segments]
        start_frames = [segment.start_frame for segment in source_segments]
        end_frames = [segment.end_frame for segment in source_segments]
        if target_segment is not None:
            start_frames.append(target_segment.start_frame)
            end_frames.append(target_segment.end_frame)
        start_frame = min(start_frames) if start_frames else 0
        end_frame = max(end_frames) if end_frames else start_frame + 1
        return {
            "rowId": target_segment.id if target_segment is not None else source_ids[0],
            "sourceSegmentIds": source_ids,
            "sourceText": "\n".join(segment.text for segment in source_segments),
            "targetSegmentId": target_segment.id if target_segment is not None else "",
            "targetText": target_segment.text if target_segment is not None else "",
            "startFrame": start_frame,
            "endFrame": end_frame,
            "status": "translated" if target_segment is not None else "missing",
        }
