from __future__ import annotations

from .base import Projector


class HighlightProjector(Projector):
    def refresh_highlights(self) -> None:
        if not self._session.binding.current:
            self._session.models.highlights.set_items([])
            return
        selected_asset_id = (
            self._session.selection.asset_ids[0] if self._session.selection.asset_ids else None
        )
        candidates = self._session.binding.current.list_highlights(selected_asset_id)
        documents = {
            document.id: document for document in self._session.binding.current.list_subtitle_documents()
        }
        self._session.models.highlights.set_items(
            [
                {
                    "highlightId": item.id,
                    "assetId": item.asset_id,
                    "documentId": item.document_id or "",
                    "sequenceId": item.sequence_id or "",
                    "sourceSequenceId": (
                        documents[item.document_id].sequence_id or "" if item.document_id in documents else ""
                    ),
                    "startFrame": item.start_frame,
                    "endFrame": item.end_frame,
                    "title": item.title,
                    "reason": item.reason,
                    "score": item.score,
                    "selected": item.selected,
                }
                for item in candidates
            ]
        )
