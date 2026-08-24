from __future__ import annotations

from typing import TYPE_CHECKING

from mediaflow.application.transcript_editing import (
    ScriptTimelineEditOutcome,
    TranscriptEditingService,
)

if TYPE_CHECKING:
    from mediaflow.application.timeline_editor import TimelineEditor


class EditorProjectScriptTimelineCommands:
    _transcript_editing: TranscriptEditingService

    if TYPE_CHECKING:
        def timeline(self, sequence_id: str) -> TimelineEditor: ...

    def move_script_segment(
        self,
        sequence_id: str,
        document_id: str,
        segment_id: str,
        *,
        position: int,
        expected_content_revision: int,
    ) -> ScriptTimelineEditOutcome:
        return self._transcript_editing.move_script_segment(
            sequence_id,
            document_id,
            segment_id,
            position=position,
            expected_content_revision=expected_content_revision,
            timeline=self.timeline(sequence_id),
        )

    def close_script_gap(
        self,
        sequence_id: str,
        document_id: str,
        segment_id: str,
        *,
        expected_content_revision: int,
    ) -> ScriptTimelineEditOutcome:
        return self._transcript_editing.close_script_gap(
            sequence_id,
            document_id,
            segment_id,
            expected_content_revision=expected_content_revision,
            timeline=self.timeline(sequence_id),
        )
