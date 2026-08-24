from __future__ import annotations

from PySide6.QtCore import Property, QTimer, Signal, Slot

from mediaflow.desktop.editor_planning import (
    current_transcription_plan,
    start_current_transcription_task,
)
from mediaflow.desktop.presentation_asr import transcription_plan_error_label
from mediaflow.domain.asr import TranscriptionPlan
from mediaflow.domain.settings import AsrSettings

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import SubtitlePresentationScope


class SubtitleTranscriptionController(ControllerFacet[SubtitlePresentationScope]):
    planChanged = Signal()

    def __init__(self, session: SubtitlePresentationScope) -> None:
        super().__init__(session)
        self._cached_plan: TranscriptionPlan | None = None
        self._cached_plan_error: RuntimeError | ValueError | None = None
        self._plan_resolved = False
        self._plan_change_timer = QTimer(self)
        self._plan_change_timer.setSingleShot(True)
        self._plan_change_timer.timeout.connect(self.planChanged.emit)
        session.events.projectStateChanged.connect(self._invalidate_plan)
        session.events.historyChanged.connect(self._invalidate_plan)
        session.events.settingsChanged.connect(self._invalidate_plan)

    @Property(bool, notify=planChanged)
    def canTranscribeCurrentSequence(self) -> bool:
        try:
            return self._current_plan().region_count > 0
        except (RuntimeError, ValueError):
            return False

    @Property(dict, notify=planChanged)
    def transcriptionPlanSummary(self) -> dict:
        try:
            plan = self._current_plan()
        except (RuntimeError, ValueError) as error:
            return {
                "available": False,
                "error": transcription_plan_error_label(str(error)),
            }
        return {
            "available": plan.region_count > 0,
            "timelineStartFrame": plan.timeline_start_frame,
            "timelineEndFrame": plan.timeline_end_frame,
            "timelineDurationFrames": (plan.timeline_end_frame - plan.timeline_start_frame),
            "recognitionSeconds": plan.recognition_seconds,
            "sourceCount": plan.source_count,
            "regionCount": plan.region_count,
            "engine": plan.asr.engine,
            "model": plan.asr.model,
            "device": plan.asr.device,
            "language": plan.asr.language,
            "parallelChunks": plan.asr.parallel_chunks,
        }

    def _current_plan(self) -> TranscriptionPlan:
        if not self._plan_resolved:
            try:
                self._cached_plan = current_transcription_plan(self._session)
                self._cached_plan_error = None
            except (RuntimeError, ValueError) as error:
                self._cached_plan = None
                self._cached_plan_error = error
            self._plan_resolved = True
        if self._cached_plan_error is not None:
            raise self._cached_plan_error
        if self._cached_plan is None:
            raise RuntimeError("当前没有可转录的时间轴")
        return self._cached_plan

    def _invalidate_plan(self) -> None:
        self._cached_plan = None
        self._cached_plan_error = None
        self._plan_resolved = False
        if not self._plan_change_timer.isActive():
            self._plan_change_timer.start(0)

    @Slot(str, result="QVariantMap")
    def sequenceTranscriptionSummary(self, sequence_id: str) -> dict:
        if not sequence_id or not self._session.state.binding.current:
            return {}
        documents = [
            item
            for item in self._session.state.binding.require_current().list_subtitle_documents(
                sequence_id=sequence_id
            )
            if item.is_source and item.source_document_id is None and item.purpose == "sequence_transcript"
        ]
        if not documents:
            return {}
        document = documents[-1]
        segment_count, start_frame, end_frame = (
            self._session.state.binding.require_current().subtitle_segment_summary(document.id)
        )
        return {
            "documentId": document.id,
            "language": document.language,
            "segmentCount": segment_count,
            "startFrame": start_frame,
            "endFrame": end_frame,
        }

    @Slot(str, str, str, int)
    @report_ui_errors
    def transcribeCurrentSequence(
        self,
        model: str,
        device: str,
        language: str,
        parallel_chunks: int,
    ) -> None:
        self._session._require_writable()
        selected_asr = AsrSettings.model_validate(
            {
                **self._session.state.service_settings.asr.model_dump(mode="python"),
                "model": model.strip(),
                "device": device,
                "language": language.strip() or "auto",
                "parallel_chunks": parallel_chunks,
            }
        )
        start_current_transcription_task(self._session, selected_asr)
