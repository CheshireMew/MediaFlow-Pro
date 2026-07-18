from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from mediaflow.application.export_catalog import default_export_preset
from mediaflow.domain.enums import (
    ExportFormat,
    TrackKind,
)
from mediaflow.domain.task_commands import (
    AnalyzeHighlightsCommand,
    ExportHighlightsCommand,
)

from .controller_facet import ControllerFacet


class HighlightController(ControllerFacet):
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
    def highlightsModel(self) -> QObject:
        return self._highlight_model

    @Property(str, notify=selectionChanged)
    def selectedHighlightId(self) -> str:
        return self._selected_highlight_id

    @Property("QVariantMap", notify=selectionChanged)
    def selectedHighlightData(self) -> dict:
        row = self._highlight_model.findRow("highlightId", self._selected_highlight_id)
        return self._highlight_model.get(row)

    @Slot(str)
    def selectHighlight(self, highlight_id: str) -> None:
        self._selected_highlight_id = highlight_id
        self.selectionChanged.emit()

    @Slot(str)
    def analyzeHighlights(self, document_id: str) -> None:
        if not document_id:
            self.errorOccurred.emit("请先选择字幕文档")
            return
        self._start_task(
            AnalyzeHighlightsCommand(document_id=document_id),
        )

    @Slot(str)
    def createShortFromHighlight(self, highlight_id: str) -> None:
        try:
            self._require_writable()
            sequence = self._project.highlights.create_short_sequence(highlight_id)
            self._active_sequence_id = sequence.id
            self._editor = self._project.timeline(sequence.id)
            self._projector.refresh_all()
            self._set_status("已从高光创建短视频序列")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(int, int, str)
    def addManualHighlight(self, start_frame: int, end_frame: int, title: str) -> None:
        try:
            self._require_writable()
            if not self.selectedAssetId:
                raise ValueError("请先选择视频或音频素材")
            candidate = self._project.highlights.add_manual_candidate(
                self.selectedAssetId,
                start_frame=start_frame,
                end_frame=end_frame,
                title=title or None,
                document_id=self._selected_document_id or None,
            )
            self._selected_highlight_id = candidate.id
            self._projector.refresh_highlights()
            self.selectionChanged.emit()
            self._set_status("已添加手动高光候选")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, int, int, str)
    def updateHighlight(
        self,
        highlight_id: str,
        start_frame: int,
        end_frame: int,
        title: str,
    ) -> None:
        try:
            self._require_writable()
            self._project.highlights.update_candidate(
                highlight_id,
                start_frame=start_frame,
                end_frame=end_frame,
                title=title,
            )
            self._projector.refresh_highlights()
            self.selectionChanged.emit()
            self._set_status("高光候选已保存")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str, bool)
    def setHighlightSelected(self, highlight_id: str, selected: bool) -> None:
        try:
            self._require_writable()
            self._project.highlights.set_selected(highlight_id, selected)
            self._projector.refresh_highlights()
            self.selectionChanged.emit()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def deleteHighlight(self, highlight_id: str) -> None:
        try:
            self._require_writable()
            self._project.highlights.delete_candidate(highlight_id)
            if self._selected_highlight_id == highlight_id:
                self._selected_highlight_id = ""
            self._projector.refresh_highlights()
            self.selectionChanged.emit()
            self._set_status("高光候选已删除")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def previewHighlight(self, highlight_id: str) -> None:
        try:
            candidate = next(item for item in self._documents.list_highlights() if item.id == highlight_id)
            project = self._documents.get_project()
            if self._active_sequence_id != project.main_sequence_id:
                self._active_sequence_id = project.main_sequence_id
                self._editor = self._project.timeline(self._active_sequence_id)
                self._projector.refresh_all()
            self._pending_preview_range = (candidate.start_frame, candidate.end_frame)
            self._projector.schedule_preview_graph()
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def addHighlightToMainSequence(self, highlight_id: str) -> None:
        try:
            self._require_writable()
            candidate = next(item for item in self._documents.list_highlights() if item.id == highlight_id)
            main_sequence_id = self._documents.get_project().main_sequence_id
            editor = self._project.timeline(main_sequence_id)
            video_track = next(track for track in editor.state.tracks if track.kind == TrackKind.VIDEO)
            timeline_start = editor.state.duration_frames
            clip = editor.add_clip(
                track_id=video_track.id,
                asset_id=candidate.asset_id,
                timeline_start=timeline_start,
                source_in=candidate.start_frame,
                duration=candidate.end_frame - candidate.start_frame,
            )
            if self._active_sequence_id == main_sequence_id:
                self._editor = editor
                self._selected_clip_ids = [clip.id]
                self._projector.refresh_timeline()
                self.selectionChanged.emit()
                self.historyChanged.emit()
            self._set_status("高光区间已添加到主序列")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def createAllHighlightShorts(self) -> None:
        try:
            self._require_writable()
            service = self._project.highlights
            candidates = service.selected_candidates(self.selectedAssetId or None)
            if not candidates:
                raise ValueError("没有选中的高光候选")
            for candidate in candidates:
                service.create_short_sequence(candidate.id)
            self._projector.refresh_sequences()
            self._projector.refresh_highlights()
            self.projectStateChanged.emit()
            self._set_status(f"已创建 {len(candidates)} 个短视频草稿")
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot(str)
    def exportSelectedHighlights(self, directory_url: str) -> None:
        try:
            self._require_writable()
            candidates = self._project.highlights.selected_candidates(self.selectedAssetId or None)
            if not candidates:
                raise ValueError("没有选中的高光候选")
            output_dir = self._local_path(directory_url)
            source_sequence = self._documents.get_sequence(self._active_sequence_id)
            saved_preset = source_sequence.export_preset
            preset = saved_preset or default_export_preset(
                ExportFormat.H264,
                source_sequence.profile.color_mode,
                source_sequence.profile.fps,
            )
            self._start_task(
                ExportHighlightsCommand(
                    sequence_id=self._active_sequence_id,
                    candidate_ids=[candidate.id for candidate in candidates],
                    output_dir=str(output_dir),
                    preset=preset,
                    burn_subtitles=(bool(saved_preset.burn_subtitle_track_id) if saved_preset else True),
                ),
                [candidate.asset_id for candidate in candidates],
                sequence_id=self._active_sequence_id,
            )
        except Exception as error:
            self.errorOccurred.emit(str(error))

    @Slot()
    def exportSelectedHighlightsToDefaultLocation(self) -> None:
        if not self._documents:
            self.errorOccurred.emit("当前没有打开的项目")
            return
        self.exportSelectedHighlights(str(self._documents.project_dir / "exports" / "Shorts"))
