from __future__ import annotations

from mediaflow.application.ports import SequenceServiceDocuments
from mediaflow.application.sequence_copy_planner import (
    PreparedShortSequence,
    SequenceCopyPlanner,
)
from mediaflow.domain.project import Sequence
from mediaflow.domain.timeline import (
    TimelineRange,
    TimelineState,
)


class SequenceService:
    def __init__(self, repository: SequenceServiceDocuments):
        self.repository = repository
        self._copy_planner = SequenceCopyPlanner(repository)

    def create_short_from_range(
        self,
        source_sequence_id: str,
        range_id: str,
        *,
        name: str | None = None,
    ) -> Sequence:
        return self.commit_prepared_short(
            self.prepare_short_from_range(
                source_sequence_id,
                range_id,
                name=name,
            )
        )

    def create_short_from_bounds(
        self,
        source_sequence_id: str,
        start_frame: int,
        end_frame: int,
        *,
        name: str | None = None,
    ) -> Sequence:
        return self.commit_prepared_short(
            self.prepare_short_from_bounds(
                source_sequence_id,
                start_frame,
                end_frame,
                name=name,
            )
        )

    def sync_short_from_bounds(
        self,
        source_sequence_id: str,
        short_sequence_id: str,
        start_frame: int,
        end_frame: int,
        *,
        name: str | None = None,
    ) -> Sequence:
        return self.commit_prepared_short(
            self.prepare_short_from_bounds(
                source_sequence_id,
                start_frame,
                end_frame,
                name=name,
                destination_sequence=(self.repository.sequences.get_sequence(short_sequence_id)),
            )
        )

    def prepare_short_from_range(
        self,
        source_sequence_id: str,
        range_id: str,
        *,
        name: str | None = None,
    ) -> PreparedShortSequence:
        source = self.repository.timeline.load_timeline(source_sequence_id)
        try:
            selected = next(item for item in source.ranges if item.id == range_id)
        except StopIteration as error:
            raise KeyError(range_id) from error
        return self._prepare_copy_selection(source, selected, name=name)

    def prepare_short_from_bounds(
        self,
        source_sequence_id: str,
        start_frame: int,
        end_frame: int,
        *,
        name: str | None = None,
        destination_sequence: Sequence | None = None,
    ) -> PreparedShortSequence:
        source, selected = self._bounded_selection(
            source_sequence_id,
            start_frame,
            end_frame,
            name=name,
        )
        return self._prepare_copy_selection(
            source,
            selected,
            name=name,
            destination_sequence=destination_sequence,
        )

    def _bounded_selection(
        self,
        source_sequence_id: str,
        start_frame: int,
        end_frame: int,
        *,
        name: str | None,
    ) -> tuple[TimelineState, TimelineRange]:
        source = self.repository.timeline.load_timeline(source_sequence_id)
        start = max(0, int(start_frame))
        end = min(source.duration_frames, int(end_frame))
        if end <= start:
            raise ValueError("短视频区间必须落在源时间轴内")
        return source, TimelineRange(
            sequence_id=source_sequence_id,
            start_frame=start,
            end_frame=end,
            name=(name or "短视频").strip() or "短视频",
        )

    def _prepare_copy_selection(
        self,
        source: TimelineState,
        selected: TimelineRange,
        *,
        name: str | None,
        destination_sequence: Sequence | None = None,
    ) -> PreparedShortSequence:
        return self._copy_planner.prepare(
            source,
            selected,
            name=name,
            destination_sequence=destination_sequence,
        )

    def commit_prepared_short(
        self,
        prepared: PreparedShortSequence,
    ) -> Sequence:
        sequence = prepared.state.sequence
        with self.repository.transaction():
            if prepared.new_sequence:
                self.repository.sequences.commit_short_sequence(sequence)
            self.repository.audio.replace_audio_graph(
                sequence.id,
                list(prepared.audio_buses),
                list(prepared.audio_effects),
            )
            self.repository.timeline.save_timeline(prepared.state)
            self.repository.subtitles.add_subtitle_placements(list(prepared.subtitle_placements))
        return self.repository.sequences.get_sequence(sequence.id)
