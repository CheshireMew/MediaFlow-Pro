from __future__ import annotations

from bisect import bisect_right

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from mediaflow.desktop.presentation_catalogs import (
    translation_language_options,
    translation_mode_options,
)
from mediaflow.domain.enums import (
    AssetKind,
    TrackKind,
)
from mediaflow.domain.task_commands import (
    TranscribeAssetCommand,
    TranscribeRegionCommand,
    TranslateDocumentCommand,
    TranslateSegmentsCommand,
)

from .controller_facet import ControllerFacet


class SubtitleController(ControllerFacet):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    statusChanged = Signal()
    taskDrawerChanged = Signal()
    tasksChanged = Signal()
    previewGraphChanged = Signal()
    profileConfirmationChanged = Signal()
    settingsChanged = Signal()
    relinkConfirmationChanged = Signal()
    audioMetricsChanged = Signal()
    workflowChanged = Signal()
    downloadPlanChanged = Signal()
    runtimeToolsChanged = Signal()
    waveformDataChanged = Signal(str)
    previewRangeRequested = Signal(int, int)
    errorOccurred = Signal(str)
    errorReferenceChanged = Signal()

    @Property(QObject, constant=True)
    def subtitleDocumentsModel(self) -> QObject:
        return self._document_model

    @Property(QObject, constant=True)
    def subtitleSegmentsModel(self) -> QObject:
        return self._segment_model

    @Property(QObject, constant=True)
    def subtitlePlacementsModel(self) -> QObject:
        return self._subtitle_placement_model

    @Property("QVariantList", constant=True)
    def translationModeOptions(self) -> list[dict]:
        return translation_mode_options()

    @Property("QVariantList", constant=True)
    def translationLanguageOptions(self) -> list[dict]:
        return translation_language_options()

    @Property(str, notify=selectionChanged)
    def selectedDocumentId(self) -> str:
        return self._selected_document_id

    @Property(str, notify=selectionChanged)
    def selectedSubtitleSegmentId(self) -> str:
        return self._selected_subtitle_segment_ids[-1] if self._selected_subtitle_segment_ids else ""

    @Property("QVariantList", notify=selectionChanged)
    def selectedSubtitleSegmentIds(self) -> list[str]:
        return list(self._selected_subtitle_segment_ids)

    @Property("QVariantMap", notify=selectionChanged)
    def selectedSubtitleSegmentData(self) -> dict:
        row = self._segment_model.findRow("segmentId", self.selectedSubtitleSegmentId)
        return self._segment_model.get(row)

    @Property(str, notify=selectionChanged)
    def selectedSubtitlePlacementId(self) -> str:
        return self._selected_subtitle_placement_id

    @Property("QVariantMap", notify=selectionChanged)
    def selectedSubtitlePlacementData(self) -> dict:
        row = self._subtitle_placement_model.findRow("placementId", self._selected_subtitle_placement_id)
        return self._subtitle_placement_model.get(row)

    @Slot(str)
    def selectSubtitleDocument(self, document_id: str) -> None:
        self._selected_document_id = document_id
        self._selected_subtitle_segment_ids = []
        self._projector.refresh_segments()
        self.selectionChanged.emit()

    @Slot(str)
    @Slot(str, bool)
    def selectSubtitleSegment(self, segment_id: str, toggle: bool = False) -> None:
        self._selected_subtitle_segment_ids = self._updated_selection(
            self._selected_subtitle_segment_ids,
            segment_id,
            toggle=toggle,
        )
        self.selectionChanged.emit()

    @Slot(str, result=bool)
    def isSubtitleSegmentSelected(self, segment_id: str) -> bool:
        return segment_id in self._selected_subtitle_segment_ids

    @Slot(str)
    def selectSubtitlePlacement(self, placement_id: str) -> None:
        self._selected_subtitle_placement_id = placement_id
        self.selectionChanged.emit()

    @Slot()
    def transcribeSelectedAsset(self) -> None:
        if not self.selectedAssetId:
            self.errorOccurred.emit("请先选择一个视频或音频素材")
            return
        self._start_task(
            TranscribeAssetCommand(asset_id=self.selectedAssetId),
            [self.selectedAssetId],
        )

    @Slot(int, int, bool)
    def transcribeRegion(
        self,
        start_frame: int,
        end_frame: int,
        translate_after: bool,
    ) -> None:
        try:
            self._require_writable()
            if not self.selectedAssetId:
                raise ValueError("请先选择一个视频或音频素材")
            asset = self._documents.get_asset(self.selectedAssetId)
            if asset.kind not in {AssetKind.VIDEO, AssetKind.AUDIO}:
                raise ValueError("只有视频或音频素材可以转录")
            if end_frame <= start_frame:
                raise ValueError("转录选区的结束帧必须晚于开始帧")
            mode = self.settings.translation.mode
            target_language = self.settings.translation.target_language
            self._start_task(
                TranscribeRegionCommand(
                    asset_id=asset.id,
                    start_frame=int(start_frame),
                    end_frame=int(end_frame),
                    document_id=self._selected_document_id or None,
                    translate_after=bool(translate_after),
                    target_language=target_language,
                    mode=mode,
                ),
                [asset.id],
            )
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str, str)
    def translateDocument(self, document_id: str, target_language: str, mode: str) -> None:
        if not document_id:
            self.errorOccurred.emit("请先选择源字幕文档")
            return
        language = target_language.strip() or self.settings.translation.target_language
        if mode not in {"standard", "intelligent", "proofread"}:
            self.errorOccurred.emit("请选择有效的翻译模式")
            return
        self._start_task(
            TranslateDocumentCommand(
                document_id=document_id,
                target_language=language or "und",
                mode=mode,
            ),
        )

    @Slot()
    def translateSelectedSubtitleSegments(self) -> None:
        try:
            self._require_writable()
            self._require_subtitle_document()
            if not self._selected_subtitle_segment_ids:
                raise ValueError("请先选择要翻译的字幕段")
            mode = self.settings.translation.mode
            language = self.settings.translation.target_language
            self._start_task(
                TranslateSegmentsCommand(
                    document_id=self._selected_document_id,
                    segment_ids=list(self._selected_subtitle_segment_ids),
                    target_language=language or "und",
                    mode=mode,
                ),
            )
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def placeSubtitleDocument(self, document_id: str) -> None:
        try:
            self._require_writable()
            subtitle_track = next(
                track for track in self._editor.state.tracks if track.kind == TrackKind.SUBTITLE
            )
            document = self._documents.get_subtitle_document(document_id)
            media_asset_id = document.media_asset_id or document.asset_id
            matching_clips = [clip for clip in self._editor.state.clips if clip.asset_id == media_asset_id]
            if matching_clips:
                placements = self._documents.place_subtitle_document(
                    document_id,
                    subtitle_track.id,
                    follow_clips=True,
                )
            else:
                placements = self._documents.place_subtitle_document(document_id, subtitle_track.id)
            self._projector.refresh_preview_subtitles()
            self._set_status(f"已放入 {len(placements)} 条字幕")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str, bool)
    def updateSubtitlePlacementText(
        self,
        placement_id: str,
        text: str,
        apply_to_document: bool,
    ) -> None:
        try:
            self._require_writable()
            if apply_to_document:
                self._documents.apply_subtitle_placement_to_document(placement_id, text)
                self._projector.refresh_documents()
                self._set_status("修改已应用到字幕文档")
            else:
                self._documents.update_subtitle_placement_text(placement_id, text)
                self._set_status("已保存序列字幕覆盖")
            self._projector.refresh_preview_subtitles()
            self.selectionChanged.emit()
            self._projector.schedule_preview_graph()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, int, int, str)
    def updateSubtitleSegment(
        self,
        segment_id: str,
        start_frame: int,
        end_frame: int,
        text: str,
    ) -> None:
        try:
            self._require_writable()
            self._require_subtitle_document()
            segment = self._subtitle_editor.update_segment(
                self._selected_document_id,
                segment_id,
                start_frame=start_frame,
                end_frame=end_frame,
                text=text,
            )
            self._finish_subtitle_edit([segment.id], "字幕已保存")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def addSubtitleSegment(self) -> None:
        try:
            self._require_writable()
            self._require_subtitle_document()
            segments = self._documents.list_subtitle_segments(self._selected_document_id)
            selected = next(
                (item for item in segments if item.id == self.selectedSubtitleSegmentId),
                None,
            )
            start_frame = selected.end_frame if selected else (segments[-1].end_frame if segments else 0)
            profile = self._documents.get_sequence(self._documents.get_project().main_sequence_id).profile
            segment = self._subtitle_editor.add_segment(
                self._selected_document_id,
                start_frame=start_frame,
                end_frame=start_frame + max(1, round(profile.fps * 2)),
                text="新字幕",
            )
            self._finish_subtitle_edit([segment.id], "已添加字幕")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def deleteSelectedSubtitleSegments(self) -> None:
        try:
            self._require_writable()
            self._require_subtitle_document()
            count = self._subtitle_editor.delete_segments(
                self._selected_document_id,
                self._selected_subtitle_segment_ids,
            )
            self._finish_subtitle_edit([], f"已删除 {count} 条字幕")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def mergeSelectedSubtitleSegments(self) -> None:
        try:
            self._require_writable()
            self._require_subtitle_document()
            merged = self._subtitle_editor.merge_segments(
                self._selected_document_id,
                self._selected_subtitle_segment_ids,
            )
            self._finish_subtitle_edit([merged.id], "字幕已合并")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, int)
    def splitSubtitleSegment(self, segment_id: str, split_frame: int) -> None:
        try:
            self._require_writable()
            self._require_subtitle_document()
            first, second = self._subtitle_editor.split_segment(
                self._selected_document_id,
                segment_id,
                split_frame=None if split_frame < 0 else split_frame,
            )
            self._finish_subtitle_edit([first.id, second.id], "字幕已拆分")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int)
    def smartSplitSubtitleDocument(self, text_limit: int) -> None:
        try:
            self._require_writable()
            self._require_subtitle_document()
            count = self._subtitle_editor.smart_split_document(
                self._selected_document_id,
                text_limit=text_limit,
            )
            self._finish_subtitle_edit([], f"智能拆分完成，共拆分 {count} 条")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def fixSubtitleOverlaps(self) -> None:
        try:
            self._require_writable()
            self._require_subtitle_document()
            count = self._subtitle_editor.fix_overlaps(self._selected_document_id)
            self._finish_subtitle_edit([], f"已修复 {count} 条重叠字幕")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def copySelectedSubtitleSegments(self) -> None:
        try:
            self._require_subtitle_document()
            text = self._subtitle_editor.selected_segments_srt(
                self._selected_document_id,
                self._selected_subtitle_segment_ids,
            )
            QGuiApplication.clipboard().setText(text)
            self._set_status(f"已复制 {len(self._selected_subtitle_segment_ids)} 条字幕")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def pasteReplaceSelectedSubtitleSegments(self) -> None:
        try:
            self._require_writable()
            self._require_subtitle_document()
            count = self._subtitle_editor.replace_selected_texts(
                self._selected_document_id,
                self._selected_subtitle_segment_ids,
                QGuiApplication.clipboard().text(),
            )
            self._finish_subtitle_edit(
                list(self._selected_subtitle_segment_ids),
                f"已替换 {count} 条字幕",
            )
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def openSubtitleFolder(self) -> None:
        try:
            self._require_subtitle_document()
            output = self._subtitle_publication.write_document_srt(self._selected_document_id)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(output.parent))):
                raise RuntimeError("无法打开字幕所在文件夹")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, str, bool)
    def replaceSubtitleText(self, search: str, replacement: str, match_case: bool) -> None:
        try:
            self._require_writable()
            self._require_subtitle_document()
            count = self._subtitle_editor.replace_all(
                self._selected_document_id,
                search,
                replacement,
                match_case=match_case,
            )
            self._finish_subtitle_edit([], f"已替换 {count} 处文本")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, int, int, str, str, bool)
    def replaceSubtitleMatch(
        self,
        segment_id: str,
        start: int,
        end: int,
        search: str,
        replacement: str,
        match_case: bool,
    ) -> None:
        try:
            self._require_writable()
            self._require_subtitle_document()
            updated = self._subtitle_editor.replace_match(
                self._selected_document_id,
                segment_id,
                start,
                end,
                search,
                replacement,
                match_case=match_case,
            )
            self._finish_subtitle_edit([updated.id], "已替换当前匹配")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, bool, result="QVariantList")
    def findSubtitleMatches(self, search: str, match_case: bool) -> list[dict]:
        try:
            self._require_subtitle_document()
            return self._subtitle_editor.find_matches(
                self._selected_document_id,
                search,
                match_case=match_case,
            )
        except Exception as error:
            self.errorOccurred.emit(str(error))
            return []

    @Slot(str, str)
    def exportSubtitleDocument(self, document_id: str, path_url: str) -> None:
        try:
            if not document_id:
                raise RuntimeError("请先选择字幕文档")
            destination = self._local_path(path_url)
            if destination.suffix.lower() != ".srt":
                destination = destination.with_suffix(".srt")
            output = self._subtitle_publication.write_document_srt(document_id, destination)
            self._set_status(f"字幕已导出到 {output}")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int, result=str)
    def subtitleTextAtFrame(self, frame: int) -> str:
        if not self._preview_subtitles:
            return ""
        index = bisect_right(self._preview_subtitles, frame, key=lambda item: item[0]) - 1
        if index >= 0:
            start, end, text = self._preview_subtitles[index]
            if start <= frame < end:
                return text
        return ""
