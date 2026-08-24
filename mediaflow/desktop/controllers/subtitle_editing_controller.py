from __future__ import annotations

from PySide6.QtCore import QUrl, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import SubtitlePresentationScope
from .subtitle_selection import selected_subtitle_segment_id


class SubtitleEditingController(ControllerFacet[SubtitlePresentationScope]):
    @Slot(str, int, int, str, result=bool)
    @report_ui_errors
    def updateSubtitleSegment(
        self,
        segment_id: str,
        start_frame: int,
        end_frame: int,
        text: str,
    ) -> bool:
        self._session._require_writable()
        self._session._require_subtitle_document()
        segment = self._session.state.binding.require_current().update_subtitle_segment(
            self._session.state.selection.document_id,
            segment_id,
            start_frame=start_frame,
            end_frame=end_frame,
            text=text,
        )
        self._session._finish_subtitle_edit(
            [segment.id],
            "字幕已保存",
            changed_segments=[segment],
        )
        return True

    @Slot()
    @report_ui_errors
    def addSubtitleSegment(self) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        segments = self._session.state.binding.require_current().list_subtitle_segments(
            self._session.state.selection.document_id
        )
        selected = next(
            (item for item in segments if item.id == selected_subtitle_segment_id(self._session)),
            None,
        )
        start_frame = selected.end_frame if selected else (segments[-1].end_frame if segments else 0)
        profile = (
            self._session.state.binding.require_current()
            .get_sequence(self._session.state.binding.require_current().get_project().main_sequence_id)
            .profile
        )
        segment = self._session.state.binding.require_current().add_subtitle_segment(
            self._session.state.selection.document_id,
            start_frame=start_frame,
            end_frame=start_frame + max(1, round(profile.fps * 2)),
            text="新字幕",
        )
        self._session._finish_subtitle_edit([segment.id], "已添加字幕")

    @Slot()
    @report_ui_errors
    def deleteSelectedSubtitleSegments(self) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        count = self._session.state.binding.require_current().delete_subtitle_segments(
            self._session.state.selection.document_id,
            self._session.state.selection.subtitle_segment_ids,
        )
        self._session._finish_subtitle_edit([], "已删除 %1 条字幕", count)

    @Slot()
    @report_ui_errors
    def mergeSelectedSubtitleSegments(self) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        merged = self._session.state.binding.require_current().merge_subtitle_segments(
            self._session.state.selection.document_id,
            self._session.state.selection.subtitle_segment_ids,
        )
        self._session._finish_subtitle_edit([merged.id], "字幕已合并")

    @Slot(str, int)
    @report_ui_errors
    def splitSubtitleSegment(self, segment_id: str, split_frame: int) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        first, second = self._session.state.binding.require_current().split_subtitle_segment(
            self._session.state.selection.document_id,
            segment_id,
            split_frame=None if split_frame < 0 else split_frame,
        )
        self._session._finish_subtitle_edit([first.id, second.id], "字幕已拆分")

    @Slot(int)
    @report_ui_errors
    def smartSplitSubtitleDocument(self, text_limit: int) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        count = self._session.state.binding.require_current().smart_split_subtitle_document(
            self._session.state.selection.document_id,
            text_limit=text_limit,
        )
        self._session._finish_subtitle_edit([], "智能拆分完成，共拆分 %1 条", count)

    @Slot()
    @report_ui_errors
    def fixSubtitleOverlaps(self) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        count = self._session.state.binding.require_current().fix_subtitle_overlaps(
            self._session.state.selection.document_id
        )
        self._session._finish_subtitle_edit([], "已修复 %1 条重叠字幕", count)

    @Slot()
    @report_ui_errors
    def copySelectedSubtitleSegments(self) -> None:
        self._session._require_subtitle_document()
        text = self._session.state.binding.require_current().selected_subtitle_segments_srt(
            self._session.state.selection.document_id,
            self._session.state.selection.subtitle_segment_ids,
        )
        QGuiApplication.clipboard().setText(text)
        self._session._set_status(
            "已复制 %1 条字幕",
            len(self._session.state.selection.subtitle_segment_ids),
        )

    @Slot()
    @report_ui_errors
    def pasteReplaceSelectedSubtitleSegments(self) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        count = self._session.state.binding.require_current().replace_selected_subtitle_texts(
            self._session.state.selection.document_id,
            self._session.state.selection.subtitle_segment_ids,
            QGuiApplication.clipboard().text(),
        )
        self._session._finish_subtitle_edit(
            list(self._session.state.selection.subtitle_segment_ids),
            "已替换 %1 条字幕",
            count,
        )

    @Slot()
    @report_ui_errors
    def openSubtitleFolder(self) -> None:
        self._session._require_subtitle_document()
        output = self._session.state.binding.require_current().write_subtitle_srt(
            self._session.state.selection.document_id
        )
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(output.parent))):
            raise RuntimeError("无法打开字幕所在文件夹")

    @Slot(str, str, bool)
    @report_ui_errors
    def replaceSubtitleText(self, search: str, replacement: str, match_case: bool) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        count = self._session.state.binding.require_current().replace_all_subtitle_text(
            self._session.state.selection.document_id,
            search,
            replacement,
            match_case=match_case,
        )
        self._session._finish_subtitle_edit([], "已替换 %1 处文本", count)

    @Slot(str, int, int, str, str, bool)
    @report_ui_errors
    def replaceSubtitleMatch(
        self,
        segment_id: str,
        start: int,
        end: int,
        search: str,
        replacement: str,
        match_case: bool,
    ) -> None:
        self._session._require_writable()
        self._session._require_subtitle_document()
        updated = self._session.state.binding.require_current().replace_subtitle_match(
            self._session.state.selection.document_id,
            segment_id,
            start,
            end,
            search,
            replacement,
            match_case=match_case,
        )
        self._session._finish_subtitle_edit(
            [updated.id],
            "已替换当前匹配",
            changed_segments=[updated],
        )

    @Slot(str, bool, result="QVariantList")
    def findSubtitleMatches(self, search: str, match_case: bool) -> list[dict]:
        try:
            self._session._require_subtitle_document()
            return self._session.state.binding.require_current().find_subtitle_matches(
                self._session.state.selection.document_id,
                search,
                match_case=match_case,
            )
        except Exception as error:
            self._session.updates.report_error(str(error))
            return []

    @Slot(str, str)
    @report_ui_errors
    def exportSubtitleDocument(self, document_id: str, path_url: str) -> None:
        if not document_id:
            raise RuntimeError("请先选择字幕文档")
        destination = self._session._local_path(path_url)
        if destination.suffix.lower() != ".srt":
            destination = destination.with_suffix(".srt")
        output = self._session.state.binding.require_current().write_subtitle_srt(document_id, destination)
        self._session._set_status("字幕已导出到 %1", output)
