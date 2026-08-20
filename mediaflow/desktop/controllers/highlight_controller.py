from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from mediaflow.application.export_catalog import default_export_preset
from mediaflow.domain.enums import (
    ExportFormat,
    TrackKind,
)
from mediaflow.domain.storage_names import (
    DEFAULT_HIGHLIGHT_EXPORT_RELATIVE_DIRECTORY,
)
from mediaflow.domain.task_commands import (
    AnalyzeHighlightsCommand,
    ExportHighlightsCommand,
)

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import CreativeControllerScope


class HighlightController(ControllerFacet[CreativeControllerScope]):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    previewRangeRequested = Signal(int, int)
    errorOccurred = Signal(str)

    @Property(QObject, constant=True)
    def highlightsModel(self) -> QObject:
        return self._session.models.highlights

    @Property(str, notify=selectionChanged)
    def selectedHighlightId(self) -> str:
        return self._session.state.selection.highlight_id

    @Property(dict, notify=selectionChanged)
    def selectedHighlightData(self) -> dict:
        row = self._session.models.highlights.findRow(
            "highlightId", self._session.state.selection.highlight_id
        )
        return self._session.models.highlights.get(row)

    @Slot(str)
    def selectHighlight(self, highlight_id: str) -> None:
        self._session.state.selection.highlight_id = highlight_id
        self._session.updates.commit(selection=True)

    @Slot(str)
    @report_ui_errors
    def analyzeHighlights(self, document_id: str) -> None:
        self._session._require_writable()
        if not document_id:
            raise ValueError("请先选择字幕文档")
        self._session.tasks.start(
            AnalyzeHighlightsCommand(document_id=document_id),
        )

    @Slot(str)
    @report_ui_errors
    def createShortFromHighlight(self, highlight_id: str) -> None:
        self._session._require_writable()
        sequence = self._session.state.binding.require_current().create_highlight_short(highlight_id)
        self._session.state.binding.active_sequence_id = sequence.id
        self._session.state.binding.timeline = self._session.state.binding.require_current().timeline(
            sequence.id
        )
        self._session.projectors.refresh_active_sequence(refresh_sequences=True)
        self._session._set_status("已从高光创建短视频序列")

    @Slot(int, int, str)
    @report_ui_errors
    def addManualHighlight(self, start_frame: int, end_frame: int, title: str) -> None:
        self._session._require_writable()
        selected_asset_id = (
            self._session.state.selection.asset_ids[0] if self._session.state.selection.asset_ids else ""
        )
        if not selected_asset_id:
            raise ValueError("请先选择视频或音频素材")
        candidate = self._session.state.binding.require_current().add_manual_highlight(
            selected_asset_id,
            start_frame=start_frame,
            end_frame=end_frame,
            title=title or None,
            document_id=self._session.state.selection.document_id or None,
        )
        self._session.state.selection.highlight_id = candidate.id
        self._session.projectors.highlights.refresh_highlights()
        self._session.updates.commit(selection=True)
        self._session._set_status("已添加手动高光候选")

    @Slot(str, int, int, str)
    @report_ui_errors
    def updateHighlight(
        self,
        highlight_id: str,
        start_frame: int,
        end_frame: int,
        title: str,
    ) -> None:
        self._session._require_writable()
        self._session.state.binding.require_current().update_highlight(
            highlight_id,
            start_frame=start_frame,
            end_frame=end_frame,
            title=title,
        )
        self._session.projectors.highlights.refresh_highlights()
        self._session.updates.commit(selection=True)
        self._session._set_status("高光候选已保存")

    @Slot(str, bool)
    @report_ui_errors
    def setHighlightSelected(self, highlight_id: str, selected: bool) -> None:
        self._session._require_writable()
        self._session.state.binding.require_current().set_highlight_selected(highlight_id, selected)
        self._session.projectors.highlights.refresh_highlights()
        self._session.updates.commit(selection=True)

    @Slot(str)
    @report_ui_errors
    def deleteHighlight(self, highlight_id: str) -> None:
        self._session._require_writable()
        self._session.state.binding.require_current().delete_highlight(highlight_id)
        if self._session.state.selection.highlight_id == highlight_id:
            self._session.state.selection.highlight_id = ""
        self._session.projectors.highlights.refresh_highlights()
        self._session.updates.commit(selection=True)
        self._session._set_status("高光候选已删除")

    @Slot(str)
    @report_ui_errors
    def previewHighlight(self, highlight_id: str) -> None:
        candidate = next(
            item
            for item in self._session.state.binding.require_current().list_highlights()
            if item.id == highlight_id
        )
        project = self._session.state.binding.require_current().get_project()
        source_sequence_id = self._highlight_source_sequence_id(candidate) or project.main_sequence_id
        sequence_changed = self._session.state.binding.active_sequence_id != source_sequence_id
        if sequence_changed:
            self._session.state.binding.active_sequence_id = source_sequence_id
            self._session.state.binding.timeline = self._session.state.binding.require_current().timeline(
                self._session.state.binding.active_sequence_id
            )
        self._session.state.presentation.pending_preview_range = (candidate.start_frame, candidate.end_frame)
        if sequence_changed:
            self._session.projectors.refresh_active_sequence()
        else:
            self._session.projectors.timeline.schedule_preview_graph()

    @Slot(str)
    @report_ui_errors
    def addHighlightToMainSequence(self, highlight_id: str) -> None:
        self._session._require_writable()
        candidate = next(
            item
            for item in self._session.state.binding.require_current().list_highlights()
            if item.id == highlight_id
        )
        main_sequence_id = self._session.state.binding.require_current().get_project().main_sequence_id
        source_sequence_id = self._highlight_source_sequence_id(candidate)
        if source_sequence_id:
            if source_sequence_id != main_sequence_id:
                raise ValueError("时间轴高光请创建短视频序列后再加入主序列")
            sequence_changed = self._session.state.binding.active_sequence_id != main_sequence_id
            self._session.state.binding.active_sequence_id = main_sequence_id
            self._session.state.binding.timeline = self._session.state.binding.require_current().timeline(
                main_sequence_id
            )
            self._session.state.presentation.pending_preview_range = (
                candidate.start_frame,
                candidate.end_frame,
            )
            if sequence_changed:
                self._session.projectors.refresh_active_sequence()
            else:
                self._session.projectors.timeline.schedule_preview_graph()
            self._session._set_status("该高光区间已经位于主序列中")
            return
        editor = self._session.state.binding.require_current().timeline(main_sequence_id)
        video_track = next(track for track in editor.state.tracks if track.kind == TrackKind.VIDEO)
        timeline_start = editor.state.duration_frames
        clip = editor.add_clip(
            track_id=video_track.id,
            asset_id=candidate.asset_id,
            timeline_start=timeline_start,
            source_in=candidate.start_frame,
            duration=candidate.end_frame - candidate.start_frame,
        )
        if self._session.state.binding.active_sequence_id == main_sequence_id:
            self._session.state.binding.timeline = editor
            self._session.state.selection.clip_ids = [clip.id]
            self._session.state.selection.compound_id = ""
            self._session.projectors.timeline.refresh_timeline()
            self._session.updates.commit(selection=True)
            self._session.updates.commit(history=True)
        self._session._set_status("高光区间已添加到主序列")

    def _highlight_source_sequence_id(self, candidate) -> str:
        if not candidate.document_id:
            return ""
        document = self._session.state.binding.require_current().get_subtitle_document(candidate.document_id)
        return document.sequence_id or ""

    @Slot()
    @report_ui_errors
    def createAllHighlightShorts(self) -> None:
        self._session._require_writable()
        selected_asset_id = (
            self._session.state.selection.asset_ids[0] if self._session.state.selection.asset_ids else None
        )
        candidates = self._session.state.binding.require_current().selected_highlights(selected_asset_id)
        if not candidates:
            raise ValueError("没有选中的高光候选")
        for candidate in candidates:
            self._session.state.binding.require_current().create_highlight_short(candidate.id)
        self._session.projectors.timeline.refresh_sequences()
        self._session.projectors.highlights.refresh_highlights()
        self._session.updates.commit(project=True)
        self._session._set_status("已创建 %1 个短视频草稿", len(candidates))

    @Slot(str)
    @report_ui_errors
    def exportSelectedHighlights(self, directory_url: str) -> None:
        self._session._require_writable()
        selected_asset_id = (
            self._session.state.selection.asset_ids[0] if self._session.state.selection.asset_ids else None
        )
        candidates = self._session.state.binding.require_current().selected_highlights(selected_asset_id)
        if not candidates:
            raise ValueError("没有选中的高光候选")
        output_dir = self._session._local_path(directory_url)
        source_sequence = self._session.state.binding.require_current().get_sequence(
            self._session.state.binding.active_sequence_id
        )
        saved_preset = source_sequence.export_preset
        saved_video_preset = (
            saved_preset if (saved_preset is not None and saved_preset.format != ExportFormat.AUDIO) else None
        )
        preset = saved_video_preset or default_export_preset(
            ExportFormat.H264,
            source_sequence.profile.color_mode,
            source_sequence.profile.fps,
        )
        self._session.tasks.start(
            ExportHighlightsCommand(
                sequence_id=self._session.state.binding.active_sequence_id,
                candidate_ids=[candidate.id for candidate in candidates],
                output_dir=str(output_dir),
                preset=preset,
                burn_subtitles=(
                    bool(saved_video_preset.burn_subtitle_track_id) if saved_video_preset else True
                ),
            ),
            [candidate.asset_id for candidate in candidates],
            sequence_id=self._session.state.binding.active_sequence_id,
        )

    @Slot()
    @report_ui_errors
    def exportSelectedHighlightsToDefaultLocation(self) -> None:
        self._session._require_writable()
        self.exportSelectedHighlights(
            str(
                self._session.state.binding.require_current().project_dir
                / DEFAULT_HIGHLIGHT_EXPORT_RELATIVE_DIRECTORY
            )
        )
