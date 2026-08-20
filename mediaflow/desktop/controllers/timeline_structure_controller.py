from __future__ import annotations

from PySide6.QtCore import Slot

from mediaflow.domain.enums import TrackKind, TransitionKind

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import TimelinePresentationScope


class TimelineStructureController(ControllerFacet[TimelinePresentationScope]):
    @Slot(str)
    @report_ui_errors
    def addTrack(self, kind: str) -> None:
        self._session._require_writable()
        track_kind = TrackKind(kind)
        self._session.timeline_assets.add_timeline_track(track_kind)
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot(str, bool, bool, bool, bool, str)
    @report_ui_errors
    def updateTrack(
        self,
        track_id: str,
        enabled: bool,
        locked: bool,
        muted: bool,
        solo: bool,
        audio_bus_id: str,
    ) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().set_track_state(
            track_id,
            enabled=enabled,
            locked=locked,
            muted=muted,
            solo=solo,
            audio_bus_id=audio_bus_id or None,
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(history=True)

    @Slot(str)
    @report_ui_errors
    def setPrimaryDialogueTrack(self, track_id: str) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().set_primary_dialogue_track(track_id)
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(history=True)

    @Slot(str, int)
    @report_ui_errors
    def moveTrack(self, track_id: str, position: int) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().move_track(track_id, position)
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(history=True)

    @Slot()
    @report_ui_errors
    def createCompoundClip(self) -> None:
        self._session._require_writable()
        compound = self._session.state.binding.require_timeline().create_compound_clip(
            self._session.state.selection.clip_ids
        )
        self._session.state.selection.compound_id = compound.id
        self._session.state.selection.clip_ids = list(compound.clip_ids)
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)
        self._session._set_status("已创建复合片段")

    @Slot()
    @report_ui_errors
    def dissolveSelectedCompoundClip(self) -> None:
        self._session._require_writable()
        if not self._session.state.selection.compound_id:
            return
        selected_ids = list(self._session.state.selection.clip_ids)
        self._session.state.binding.require_timeline().dissolve_compound_clip(
            self._session.state.selection.compound_id
        )
        self._session.state.selection.compound_id = ""
        self._session.state.selection.clip_ids = selected_ids
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)
        self._session._set_status("已解除复合片段")

    @Slot(str, str, int)
    def addTransitionAfter(self, clip_id: str, kind: str, duration: int) -> None:
        try:
            self._session._require_writable()
            state = self._session.state.binding.require_timeline().state
            left = next(clip for clip in state.clips if clip.id == clip_id)
            right = next(
                clip
                for clip in state.clips_for_track(left.track_id)
                if clip.timeline_start == left.timeline_end
            )
            transition = self._session.state.binding.require_timeline().create_transition(
                left.id,
                right.id,
                TransitionKind(kind),
                max(1, duration),
            )
            self._session.state.selection.transition_id = transition.id
            self._session.projectors.timeline.refresh_timeline()
            self._session.updates.commit(selection=True)
            self._session.updates.commit(history=True)
            self._session._set_status("转场已添加")
        except StopIteration:
            self._session.updates.report_error("所选片段后没有同轨道的相邻片段")
        except Exception as error:
            self._session.updates.report_error(str(error))

    @Slot(str, str, int)
    @report_ui_errors
    def updateTransition(self, transition_id: str, kind: str, duration: int) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().update_transition(
            transition_id,
            kind=TransitionKind(kind),
            duration=max(1, duration),
        )
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot(str)
    @report_ui_errors
    def removeTransition(self, transition_id: str) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().remove_transition(transition_id)
        self._session.state.selection.transition_id = ""
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot(int)
    @report_ui_errors
    def addTimelineMarker(self, frame: int) -> None:
        self._session._require_writable()
        marker = self._session.state.binding.require_timeline().add_marker(
            max(0, frame), f"标记 {len(self._session.state.binding.require_timeline().state.markers) + 1}"
        )
        self._session.state.selection.marker_id = marker.id
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot(str)
    @report_ui_errors
    def removeTimelineMarker(self, marker_id: str) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().remove_marker(marker_id)
        self._session.state.selection.marker_id = ""
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot(int)
    def setRangeIn(self, frame: int) -> None:
        self._session.state.selection.range_in_frame = max(0, frame)
        self._session.updates.commit(selection=True)

    @Slot(int)
    @report_ui_errors
    def setSequenceInPoint(self, frame: int) -> None:
        self._session._require_writable()
        in_out = self._session.state.binding.require_timeline().state.sequence.in_out
        out_frame = in_out.out_frame if in_out else max(1, frame + 1)
        self._session.state.binding.require_timeline().set_sequence_in_out(max(0, frame), out_frame)
        self._session._finish_sequence_in_out_edit("已设置序列入点")

    @Slot(int)
    @report_ui_errors
    def setSequenceOutPoint(self, frame: int) -> None:
        self._session._require_writable()
        in_out = self._session.state.binding.require_timeline().state.sequence.in_out
        in_frame = in_out.in_frame if in_out else 0
        self._session.state.binding.require_timeline().set_sequence_in_out(in_frame, max(1, frame))
        self._session._finish_sequence_in_out_edit("已设置序列出点")

    @Slot(int, int)
    @report_ui_errors
    def setSequenceInOut(self, in_frame: int, out_frame: int) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().set_sequence_in_out(in_frame, out_frame)
        self._session._finish_sequence_in_out_edit("已调整序列入出点")

    @Slot()
    @report_ui_errors
    def clearSequenceInOut(self) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().clear_sequence_in_out()
        self._session._finish_sequence_in_out_edit("已清除序列入出点")

    @Slot(int)
    @report_ui_errors
    def commitTimelineRange(self, frame: int) -> None:
        self._session._require_writable()
        if self._session.state.selection.range_in_frame is None:
            raise ValueError("请先设置选区入点")
        start_frame, end_frame = sorted((self._session.state.selection.range_in_frame, max(0, frame)))
        if start_frame == end_frame:
            raise ValueError("选区必须包含至少一帧")
        item = self._session.state.binding.require_timeline().add_range(
            start_frame,
            end_frame,
            f"选区 {len(self._session.state.binding.require_timeline().state.ranges) + 1}",
        )
        self._session.state.selection.range_id = item.id
        self._session.state.selection.range_in_frame = None
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot(str)
    @report_ui_errors
    def removeTimelineRange(self, range_id: str) -> None:
        self._session._require_writable()
        self._session.state.binding.require_timeline().remove_range(range_id)
        self._session.state.selection.range_id = ""
        self._session.projectors.timeline.refresh_timeline()
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot(str)
    @report_ui_errors
    def createShortFromRange(self, range_id: str) -> None:
        self._session._require_writable()
        sequence = self._session.state.binding.require_current().create_short_from_range(
            self._session.state.binding.active_sequence_id,
            range_id,
        )
        self._session.state.binding.active_sequence_id = sequence.id
        self._session.state.binding.timeline = self._session.state.binding.require_current().timeline(
            sequence.id
        )
        self._session.state.selection.range_id = ""
        self._session.projectors.refresh_active_sequence(refresh_sequences=True)
        self._session._set_status("已从时间线选区创建短视频序列")

    @Slot()
    @report_ui_errors
    def undo(self) -> None:
        self._session.state.binding.require_timeline().undo()
        self._session.projectors.assets.refresh_assets()
        self._session.projectors.timeline.refresh_sequences()
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.subtitles.refresh_documents()
        self._session.projectors.timeline.refresh_preview_subtitles()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.updates.commit(project=True)
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)

    @Slot()
    @report_ui_errors
    def redo(self) -> None:
        self._session.state.binding.require_timeline().redo()
        self._session.projectors.assets.refresh_assets()
        self._session.projectors.timeline.refresh_sequences()
        self._session.projectors.timeline.refresh_timeline()
        self._session.projectors.subtitles.refresh_documents()
        self._session.projectors.timeline.refresh_preview_subtitles()
        self._session.projectors.timeline.schedule_preview_graph()
        self._session.updates.commit(project=True)
        self._session.updates.commit(selection=True)
        self._session.updates.commit(history=True)
