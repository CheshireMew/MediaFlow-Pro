from __future__ import annotations

from typing import Literal, cast

from PySide6.QtCore import Slot

from mediaflow.domain.task_commands import (
    AnalyzeScenesCommand,
    AnalyzeSequenceBoundsCommand,
    TrackSubjectCommand,
)

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import TimelinePresentationScope
from .timeline_selection import (
    selected_video_clip,
    sequence_boundary_analysis_running,
)


class TimelineAnalysisController(ControllerFacet[TimelinePresentationScope]):
    @Slot(float)
    @report_ui_errors
    def detectScenesSelected(self, threshold: float = 0.35) -> None:
        self._session._require_writable()
        clip = selected_video_clip(self._session)
        self._session.tasks.start(
            AnalyzeScenesCommand(
                sequence_id=self._session.state.binding.active_sequence_id,
                clip_id=clip.id,
                threshold=threshold,
            ),
            [clip.asset_id],
            sequence_id=self._session.state.binding.active_sequence_id,
        )
        self._session._set_status("正在检测场景切点")

    @Slot()
    def autoReframeSelected(self) -> None:
        self._start_subject_tracking("auto_reframe")

    @Slot()
    def trackSubjectSelected(self) -> None:
        self._start_subject_tracking("subject_tracking")

    @report_ui_errors
    def _start_subject_tracking(self, mode: str) -> None:
        self._session._require_writable()
        if mode not in {"auto_reframe", "subject_tracking"}:
            raise ValueError("请选择有效的主体跟踪模式")
        tracking_mode = cast(Literal["auto_reframe", "subject_tracking"], mode)
        clip = selected_video_clip(self._session)
        self._session.tasks.start(
            TrackSubjectCommand(
                sequence_id=self._session.state.binding.active_sequence_id,
                clip_id=clip.id,
                mode=tracking_mode,
            ),
            [clip.asset_id],
            sequence_id=self._session.state.binding.active_sequence_id,
        )
        self._session._set_status("正在分析画面主体")

    @Slot()
    @report_ui_errors
    def analyzeSequenceBoundaries(self) -> None:
        self._session._require_writable()
        if not self._session.state.binding.require_timeline().state.clips:
            raise ValueError("请先向时间线添加媒体")
        if sequence_boundary_analysis_running(self._session):
            raise RuntimeError("当前序列正在分析入出点")
        snapshot_hash = self._session.state.binding.require_current().sequence_boundary_snapshot_hash(
            self._session.state.binding.active_sequence_id
        )
        self._session.tasks.start(
            AnalyzeSequenceBoundsCommand(
                sequence_id=self._session.state.binding.active_sequence_id,
                snapshot_hash=snapshot_hash,
            ),
            sequence_id=self._session.state.binding.active_sequence_id,
        )
