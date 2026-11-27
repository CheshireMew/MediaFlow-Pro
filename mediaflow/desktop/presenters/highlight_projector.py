from __future__ import annotations

from mediaflow.desktop.component_contexts import HighlightProjectorContext

from .base import Projector


class HighlightProjector(Projector[HighlightProjectorContext]):
    def refresh_highlights(self) -> None:
        if not self._context.state.binding.current:
            self._context.models.highlights.set_items([])
            return
        selected_asset_id = (
            self._context.state.selection.asset_ids[0] if self._context.state.selection.asset_ids else None
        )
        candidates = self._context.state.binding.require_current().list_highlights(selected_asset_id)
        documents = {
            document.id: document
            for document in self._context.state.binding.require_current().list_subtitle_documents()
        }
        self._context.models.highlights.set_items(
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
