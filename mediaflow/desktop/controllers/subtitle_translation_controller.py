from __future__ import annotations

from typing import Any

from PySide6.QtCore import Property, Signal, Slot

from mediaflow.domain.task_commands import TranslateDocumentCommand, TranslateSegmentsCommand
from mediaflow.domain.translation import TranslationComparison, validate_translation_mode

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import SubtitlePresentationScope


class SubtitleTranslationController(ControllerFacet[SubtitlePresentationScope]):
    presentationChanged = Signal()

    def __init__(self, session: SubtitlePresentationScope) -> None:
        super().__init__(session)
        self._comparison = self._empty_comparison()
        self._task_data: dict[str, Any] = {}
        self._selected_row_ids: set[str] = set()
        self._drafts: dict[tuple[str, str], str] = {}
        self._observed_document_id = ""
        self._comparison_request_id = 0

    @Property(dict, notify=presentationChanged)
    def comparisonData(self) -> dict[str, Any]:
        document = dict(self._comparison)
        target_document_id = str(document.get("targetDocumentId") or "")
        document["rows"] = [
            {
                **row,
                "draftText": self._drafts.get(
                    (target_document_id, str(row.get("targetSegmentId") or "")),
                    str(row.get("targetText") or ""),
                ),
            }
            for row in document.get("rows", [])
        ]
        return document

    @Property(dict, notify=presentationChanged)
    def taskData(self) -> dict[str, Any]:
        return dict(self._task_data)

    @Property(list, notify=presentationChanged)
    def selectedRowIds(self) -> list[str]:
        return sorted(self._selected_row_ids)

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

    @Slot(str, str)
    @report_ui_errors
    def translateComparisonSegments(
        self,
        target_language: str,
        mode: str,
    ) -> None:
        self._session._require_writable()
        source_document_id = str(self._comparison.get("sourceDocumentId") or "")
        target_document_id = str(self._comparison.get("targetDocumentId") or "")
        segment_ids = self._selected_source_segment_ids()
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

    @Slot(str, str)
    def refreshComparison(self, document_id: str, target_language: str) -> None:
        self._comparison_request_id += 1
        request_id = self._comparison_request_id
        if document_id != self._observed_document_id:
            self._observed_document_id = document_id
            self._selected_row_ids.clear()
        if not document_id or not self._session.state.binding.current:
            self._comparison = self._empty_comparison()
            self._task_data = {}
            self.presentationChanged.emit()
            return
        project = self._session.state.binding.require_current()
        self._session.background.submit_project_callback(
            "translation_comparison",
            request_id,
            lambda: project.translation_comparison(document_id, target_language),
            on_result=lambda result: self._apply_comparison(
                request_id,
                document_id,
                result,
            ),
            on_error=lambda error: self._comparison_failed(request_id, error),
        )

    @Slot(str, str)
    def storeTranslationDraft(self, target_segment_id: str, text: str) -> None:
        target_document_id = str(self._comparison.get("targetDocumentId") or "")
        if not target_document_id or not target_segment_id:
            return
        self._drafts[(target_document_id, target_segment_id)] = text
        self.presentationChanged.emit()

    @Slot(str)
    @report_ui_errors
    def saveTranslationSegment(self, target_segment_id: str) -> None:
        self._session._require_writable()
        target_document_id = str(self._comparison.get("targetDocumentId") or "")
        key = (target_document_id, target_segment_id)
        if key not in self._drafts:
            return
        text = self._drafts[key]
        project = self._session.state.binding.require_current()
        request_id = (self._comparison_request_id, target_document_id, target_segment_id)
        self._session.background.submit_project_callback(
            "translation_segment_save",
            request_id,
            lambda: project.update_translation_segment_text(
                target_document_id,
                target_segment_id,
                text,
            ),
            on_result=lambda _result: self._translation_segment_saved(key),
            on_error=lambda error: self._session.updates.report_error(
                f"保存译文失败：{error}"
            ),
        )

    @Slot(str, result=bool)
    def rowSelected(self, row_id: str) -> bool:
        return row_id in self._selected_row_ids

    @Slot(str)
    def toggleRow(self, row_id: str) -> None:
        if row_id in self._selected_row_ids:
            self._selected_row_ids.remove(row_id)
        else:
            self._selected_row_ids.add(row_id)
        self.presentationChanged.emit()

    @Slot()
    def refreshTaskData(self) -> None:
        context_id = str(
            self._comparison.get("sourceDocumentId")
            or self._observed_document_id
            or ""
        )
        self._task_data = self._latest_translation_task(context_id)
        self.presentationChanged.emit()

    def _apply_comparison(
        self,
        request_id: int,
        document_id: str,
        result: object | None,
    ) -> None:
        if request_id != self._comparison_request_id:
            return
        if not isinstance(result, TranslationComparison):
            raise TypeError("Translation comparison returned an invalid result")
        data = result.model_dump(mode="python")
        self._comparison = {
            "sourceDocumentId": data["source_document_id"],
            "targetDocumentId": data["target_document_id"],
            "sourceLanguage": data["source_language"],
            "targetLanguage": data["target_language"],
            "glossaryHitCount": data["glossary_hit_count"],
            "rows": [
                {
                    "rowId": row["row_id"],
                    "sourceSegmentIds": row["source_segment_ids"],
                    "sourceText": row["source_text"],
                    "targetSegmentId": row["target_segment_id"],
                    "targetText": row["target_text"],
                    "startFrame": row["start_frame"],
                    "endFrame": row["end_frame"],
                    "status": row["status"],
                }
                for row in data["rows"]
            ],
        }
        target_document_id = str(self._comparison["targetDocumentId"])
        target_texts = {
            str(row["targetSegmentId"]): str(row["targetText"])
            for row in self._comparison["rows"]
            if row["targetSegmentId"]
        }
        self._drafts = {
            key: value
            for key, value in self._drafts.items()
            if key[0] != target_document_id
            or target_texts.get(key[1]) != value
        }
        self._selected_row_ids.intersection_update(
            str(row["rowId"]) for row in self._comparison["rows"]
        )
        self._observed_document_id = document_id
        self.refreshTaskData()

    def _comparison_failed(self, request_id: int, error: BaseException) -> None:
        if request_id != self._comparison_request_id:
            return
        self._session.updates.report_error(f"读取翻译对照失败：{error}")

    def _translation_segment_saved(self, key: tuple[str, str]) -> None:
        self._drafts.pop(key, None)
        self._session.projectors.subtitles.refresh_documents()
        self._session.projectors.timeline.refresh_preview_subtitles()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session._set_status("译文已保存")
        self._session.updates.commit(project=True)
        self._session.updates.commit(history=True)
        self.presentationChanged.emit()

    def _selected_source_segment_ids(self) -> list[str]:
        selected: list[str] = []
        for row in self._comparison.get("rows", []):
            if str(row.get("rowId") or "") not in self._selected_row_ids:
                continue
            for segment_id in row.get("sourceSegmentIds", []):
                value = str(segment_id)
                if value not in selected:
                    selected.append(value)
        return selected

    def _latest_translation_task(self, context_id: str) -> dict[str, Any]:
        matches = [
            self._session.models.tasks.get(index)
            for index in range(self._session.models.tasks.rowCount())
        ]
        matches = [
            row
            for row in matches
            if row.get("kind") == "translate"
            and (
                not context_id
                or context_id in (row.get("inputAssetIds") or [])
                or context_id == row.get("contextId")
            )
        ]
        return dict(
            next(
                (
                    row
                    for row in matches
                    if row.get("status") in {"pending", "running", "paused"}
                ),
                matches[0] if matches else {},
            )
        )

    @staticmethod
    def _empty_comparison() -> dict[str, Any]:
        return {
            "sourceDocumentId": "",
            "targetDocumentId": "",
            "sourceLanguage": "",
            "targetLanguage": "",
            "glossaryHitCount": 0,
            "rows": [],
        }
