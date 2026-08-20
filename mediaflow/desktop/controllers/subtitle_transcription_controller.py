from __future__ import annotations

from PySide6.QtCore import Property, Signal, Slot

from mediaflow.desktop.editor_planning import (
    current_transcription_plan,
    start_current_transcription_task,
)
from mediaflow.domain.settings import AsrSettings

from .controller_facet import ControllerFacet, report_ui_errors
from .controller_scopes import SubtitlePresentationScope


class SubtitleTranscriptionController(ControllerFacet[SubtitlePresentationScope]):
    historyChanged = Signal()

    @Property(bool, notify=historyChanged)
    def canTranscribeCurrentSequence(self) -> bool:
        try:
            return current_transcription_plan(self._session).region_count > 0
        except (RuntimeError, ValueError):
            return False

    @Property(dict, notify=historyChanged)
    def transcriptionPlanSummary(self) -> dict:
        try:
            plan = current_transcription_plan(self._session)
        except (RuntimeError, ValueError) as error:
            return {"available": False, "error": str(error)}
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
