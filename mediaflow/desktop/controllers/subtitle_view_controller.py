from __future__ import annotations

from bisect import bisect_right

from PySide6.QtCore import Property, QObject, Signal, Slot

from mediaflow.desktop.presentation_translation import (
    translation_language_options,
    translation_mode_options,
)
from mediaflow.domain.timebase import reframe_frames

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import SubtitlePresentationScope
from .subtitle_selection import select_subtitle_placement_context


class SubtitleViewController(ControllerFacet[SubtitlePresentationScope]):
    projectStateChanged = Signal()
    selectionChanged = Signal()
    historyChanged = Signal()
    previewRangeRequested = Signal(int, int)
    errorOccurred = Signal(str)

    @Property(QObject, constant=True)
    def subtitleDocumentsModel(self) -> QObject:
        return self._session.models.documents

    @Property(QObject, constant=True)
    def subtitleSegmentsModel(self) -> QObject:
        return self._session.models.segments

    @Property(QObject, constant=True)
    def subtitlePlacementsModel(self) -> QObject:
        return self._session.models.subtitle_placements

    @Property(list, constant=True)
    def translationModeOptions(self) -> list[dict]:
        return translation_mode_options()

    @Property(list, constant=True)
    def translationLanguageOptions(self) -> list[dict]:
        return translation_language_options()

    @Property(str, notify=selectionChanged)
    def selectedDocumentId(self) -> str:
        return self._session.state.selection.document_id

    @Property(str, notify=selectionChanged)
    def selectedSubtitleSegmentId(self) -> str:
        return (
            self._session.state.selection.subtitle_segment_ids[-1]
            if self._session.state.selection.subtitle_segment_ids
            else ""
        )

    @Property(list, notify=selectionChanged)
    def selectedSubtitleSegmentIds(self) -> list[str]:
        return list(self._session.state.selection.subtitle_segment_ids)

    @Property(dict, notify=selectionChanged)
    def selectedSubtitleSegmentData(self) -> dict:
        row = self._session.models.segments.findRow(
            "segmentId",
            self._session.state.selection.subtitle_segment_ids[-1]
            if self._session.state.selection.subtitle_segment_ids
            else "",
        )
        return self._session.models.segments.get(row)

    @Property(str, notify=selectionChanged)
    def selectedSubtitlePlacementId(self) -> str:
        return self._session.state.selection.subtitle_placement_id

    @Property(dict, notify=selectionChanged)
    def selectedSubtitlePlacementData(self) -> dict:
        row = self._session.models.subtitle_placements.findRow(
            "placementId", self._session.state.selection.subtitle_placement_id
        )
        return self._session.models.subtitle_placements.get(row)

    @Slot(str)
    def selectSubtitleDocument(self, document_id: str) -> None:
        self._session.state.selection.document_id = document_id
        self._session.state.selection.subtitle_segment_ids = []
        self._session.projectors.subtitles.refresh_segments()
        self._session.updates.commit(selection=True)

    @Slot(str)
    @Slot(str, bool)
    def selectSubtitleSegment(self, segment_id: str, toggle: bool = False) -> None:
        self._session.state.selection.subtitle_segment_ids = self._session._updated_selection(
            self._session.state.selection.subtitle_segment_ids,
            segment_id,
            toggle=toggle,
        )
        self._session.updates.commit(selection=True)

    @Slot(str, result=bool)
    def isSubtitleSegmentSelected(self, segment_id: str) -> bool:
        return segment_id in self._session.state.selection.subtitle_segment_ids

    @Slot(str)
    def selectSubtitlePlacement(self, placement_id: str) -> None:
        select_subtitle_placement_context(self._session, placement_id)

    @Slot(int)
    def followSubtitleAtFrame(self, frame: int) -> None:
        frame = max(0, int(frame))
        active = None
        for index in range(self._session.models.subtitle_placements.rowCount()):
            row = self._session.models.subtitle_placements.get(index)
            if int(row.get("startFrame", 0)) <= frame < int(row.get("endFrame", 0)):
                active = row
                if row.get("placementId") == self._session.state.selection.subtitle_placement_id:
                    break
        if active is None:
            if self._session.state.selection.subtitle_placement_id:
                self._session.state.selection.subtitle_placement_id = ""
                self._session.state.selection.subtitle_segment_ids = []
                self._session.updates.commit(selection=True)
            return
        if active.get("placementId") != self._session.state.selection.subtitle_placement_id:
            select_subtitle_placement_context(self._session, str(active["placementId"]))

    @Slot(str)
    @report_ui_errors
    def previewSubtitlePlacement(self, placement_id: str) -> None:
        placement = self._session.state.binding.require_current().get_subtitle_placement(placement_id)
        select_subtitle_placement_context(self._session, placement_id)
        self._session.updates.request_preview_range(placement.start_frame, placement.end_frame)

    @Slot(str)
    @report_ui_errors
    def previewSubtitleSegment(self, segment_id: str) -> None:
        for index in range(self._session.models.subtitle_placements.rowCount()):
            row = self._session.models.subtitle_placements.get(index)
            if row.get("segmentId") == segment_id:
                self.previewSubtitlePlacement(str(row["placementId"]))
                return
        self.selectSubtitleSegment(segment_id)
        segment = next(
            item
            for item in self._session.state.binding.require_current().list_subtitle_segments(
                self._session.state.selection.document_id
            )
            if item.id == segment_id
        )
        start_frame = self._main_frame_in_active_clock(segment.start_frame)
        end_frame = max(
            start_frame + 1,
            self._main_frame_in_active_clock(segment.end_frame),
        )
        self._session.updates.request_preview_range(start_frame, end_frame)

    @Slot(str, int, result=int)
    def subtitleSegmentTimelineFrame(self, segment_id: str, fallback_frame: int) -> int:
        for index in range(self._session.models.subtitle_placements.rowCount()):
            row = self._session.models.subtitle_placements.get(index)
            if row.get("segmentId") == segment_id:
                return int(row.get("startFrame", fallback_frame))
        return self._main_frame_in_active_clock(fallback_frame)

    def _main_frame_in_active_clock(self, frame: int) -> int:
        current = self._session.state.binding.current
        active_sequence_id = self._session.state.binding.active_sequence_id
        if current is None or not active_sequence_id:
            return max(0, int(frame))
        project = current.get_project()
        main_profile = current.get_sequence(project.main_sequence_id).profile
        active_profile = current.get_sequence(active_sequence_id).profile
        return max(
            0,
            reframe_frames(
                int(frame),
                main_profile,
                active_profile,
            ),
        )

    @Slot(int, result=str)
    def subtitleTextAtFrame(self, frame: int) -> str:
        if not self._session.state.presentation.preview_subtitles:
            return ""
        index = (
            bisect_right(self._session.state.presentation.preview_subtitles, frame, key=lambda item: item[0])
            - 1
        )
        if index >= 0:
            start, end, text = self._session.state.presentation.preview_subtitles[index]
            if start <= frame < end:
                return text
        return ""

    @Slot(str, int, result=str)
    def subtitleTextForTrackAtFrame(self, track_id: str, frame: int) -> str:
        subtitles = self._session.state.presentation.preview_subtitles_by_track.get(track_id, [])
        if not subtitles:
            return ""
        index = bisect_right(subtitles, frame, key=lambda item: item[0]) - 1
        if index >= 0:
            start, end, text = subtitles[index]
            if start <= frame < end:
                return text
        return ""
