from __future__ import annotations

from mediaflow.domain.subtitles import SubtitleSegment
from mediaflow.domain.timebase import seconds_to_frames

from .base import Projector


class SubtitleProjector(Projector):
    def refresh_documents(self) -> None:
        if not self._session.state.binding.current:
            self._session.models.documents.set_items([])
            self._session.models.segments.set_items([])
            return
        documents = self._session.state.binding.require_current().list_subtitle_documents()
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
                    "segmentCount": self._session.state.binding.require_current().subtitle_segment_summary(
                        document.id
                    )[0],
                }
                for document in documents
            ]
        )
        if self._session.state.selection.document_id and all(
            document.id != self._session.state.selection.document_id for document in documents
        ):
            self._session.state.selection.document_id = ""
        self.refresh_segments()

    def refresh_segments(self) -> None:
        if not self._session.state.binding.current or not self._session.state.selection.document_id:
            self._session.models.segments.set_items([])
            self._session.state.selection.subtitle_segment_ids = []
            return
        segments = self._session.state.binding.require_current().list_subtitle_segments(
            self._session.state.selection.document_id
        )
        project = self._session.state.binding.require_current().get_project()
        profile = self._session.state.binding.require_current().get_sequence(project.main_sequence_id).profile
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
        self._session.state.selection.subtitle_segment_ids = [
            segment_id
            for segment_id in self._session.state.selection.subtitle_segment_ids
            if segment_id in available
        ]

    def refresh_segment_rows(self, segments: list[SubtitleSegment]) -> None:
        """Refresh edited subtitle rows without reloading the document collection."""

        document_id = self._session.state.selection.document_id
        if (
            not self._session.state.binding.current
            or not document_id
            or any(segment.document_id != document_id for segment in segments)
        ):
            self.refresh_documents()
            return
        replacements = {segment.id: segment for segment in segments}
        current_rows = self._session.models.segments.snapshot()
        available = {str(row["segmentId"]) for row in current_rows}
        if not set(replacements) <= available:
            self.refresh_documents()
            return
        projected_rows = [
            self._segment_row(replacements.get(str(row["segmentId"])), row)
            for row in current_rows
        ]
        projected_rows.sort(
            key=lambda row: (
                int(row["startFrame"]),
                int(row["endFrame"]),
                str(row["segmentId"]),
            )
        )
        tolerance = self._overlap_tolerance()
        current_order = [str(row["segmentId"]) for row in current_rows]
        projected_order = [str(row["segmentId"]) for row in projected_rows]
        affected_ids = set(replacements)
        for order in (current_order, projected_order):
            for segment_id in replacements:
                index = order.index(segment_id)
                if index > 0:
                    affected_ids.add(order[index - 1])
                if index + 1 < len(order):
                    affected_ids.add(order[index + 1])
        projected_by_id = {
            str(row["segmentId"]): row for row in projected_rows
        }
        changed_rows = []
        for segment_id in affected_ids:
            index = projected_order.index(segment_id)
            row = projected_by_id[segment_id]
            has_overlap = False
            if index > 0:
                previous = projected_rows[index - 1]
                has_overlap = int(row["startFrame"]) < int(previous["endFrame"]) - tolerance
            if not has_overlap and index + 1 < len(projected_rows):
                following = projected_rows[index + 1]
                has_overlap = int(following["startFrame"]) < int(row["endFrame"]) - tolerance
            changed_rows.append({**row, "hasOverlap": has_overlap})
        if not self._session.models.segments.patch_items_by_key(
            changed_rows,
            removed_keys=set(),
            ordered_keys=projected_order,
        ):
            self.refresh_documents()

    @staticmethod
    def _segment_row(
        segment: SubtitleSegment | None,
        current: dict,
    ) -> dict:
        if segment is None:
            return current
        return {
            **current,
            "startFrame": segment.start_frame,
            "endFrame": segment.end_frame,
            "text": segment.text,
            "speaker": segment.speaker or "",
            "confidence": segment.confidence if segment.confidence is not None else -1,
        }

    def _overlap_tolerance(self) -> int:
        project = self._session.state.binding.require_current().get_project()
        profile = self._session.state.binding.require_current().get_sequence(
            project.main_sequence_id
        ).profile
        return max(
            1,
            seconds_to_frames(
                0.05,
                profile.fps_numerator,
                profile.fps_denominator,
            ),
        )
