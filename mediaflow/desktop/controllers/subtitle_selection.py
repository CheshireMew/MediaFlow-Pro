from __future__ import annotations


def selected_subtitle_segment_id(session) -> str:
    return (
        session.state.selection.subtitle_segment_ids[-1]
        if session.state.selection.subtitle_segment_ids
        else ""
    )


def select_subtitle_placement_context(session, placement_id: str) -> None:
    row_index = session.models.subtitle_placements.findRow("placementId", placement_id)
    row = session.models.subtitle_placements.get(row_index)
    if not row:
        return
    document_id = str(row.get("documentId") or "")
    segment_id = str(row.get("segmentId") or "")
    document_changed = document_id and document_id != session.state.selection.document_id
    session.state.selection.subtitle_placement_id = placement_id
    if document_changed:
        session.state.selection.document_id = document_id
        session.projectors.subtitles.refresh_segments()
    session.state.selection.subtitle_segment_ids = [segment_id] if segment_id else []
    session.updates.commit(selection=True)
