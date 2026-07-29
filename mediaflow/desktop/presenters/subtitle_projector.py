from __future__ import annotations

from mediaflow.domain.timebase import seconds_to_frames

from .base import Projector


class SubtitleProjector(Projector):
    def refresh_documents(self) -> None:
        if not self._session.binding.current:
            self._session.models.documents.set_items([])
            self._session.models.segments.set_items([])
            return
        documents = self._session.binding.current.list_subtitle_documents()
        self._session.models.documents.set_items(
            [
                {
                    "documentId": document.id,
                    "assetId": document.asset_id,
                    "mediaAssetId": document.media_asset_id or document.asset_id,
                    "sequenceId": document.sequence_id or "",
                    "language": document.language,
                    "isSource": document.is_source,
                    "sourceDocumentId": document.source_document_id or "",
                    "segmentCount": self._session.binding.current.subtitle_segment_summary(document.id)[0],
                }
                for document in documents
            ]
        )
        if self._session.selection.document_id and all(
            document.id != self._session.selection.document_id for document in documents
        ):
            self._session.selection.document_id = ""
        self.refresh_segments()

    def refresh_segments(self) -> None:
        if not self._session.binding.current or not self._session.selection.document_id:
            self._session.models.segments.set_items([])
            self._session.selection.subtitle_segment_ids = []
            return
        segments = self._session.binding.current.list_subtitle_segments(self._session.selection.document_id)
        project = self._session.binding.current.get_project()
        profile = self._session.binding.current.get_sequence(project.main_sequence_id).profile
        tolerance = max(
            1,
            seconds_to_frames(0.05, profile.fps_numerator, profile.fps_denominator),
        )
        overlap_ids: set[str] = set()
        for previous, current in zip(segments, segments[1:], strict=False):
            if current.start_frame < previous.end_frame - tolerance:
                overlap_ids.update((previous.id, current.id))
        rows = [
            {
                "segmentId": segment.id,
                "startFrame": segment.start_frame,
                "endFrame": segment.end_frame,
                "text": segment.text,
                "speaker": segment.speaker or "",
                "confidence": segment.confidence if segment.confidence is not None else -1,
                "hasOverlap": segment.id in overlap_ids,
            }
            for segment in segments
        ]
        self._session.models.segments.set_items(rows)
        available = {row["segmentId"] for row in rows}
        self._session.selection.subtitle_segment_ids = [
            segment_id
            for segment_id in self._session.selection.subtitle_segment_ids
            if segment_id in available
        ]
