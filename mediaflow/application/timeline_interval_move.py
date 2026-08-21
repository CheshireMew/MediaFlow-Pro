from __future__ import annotations

import uuid

from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.domain.project import SequenceInOut
from mediaflow.domain.timebase import source_frame_at_timeline_offset
from mediaflow.domain.timeline import Clip, CompoundClip, TimelineRange, TimelineState
from mediaflow.domain.web_state import WebClipState


class TimelineIntervalMovePolicy:
    """Relocate one timeline interval while preserving total duration.

    The move is a permutation of the source interval and the frames between it
    and the insertion boundary. Clips crossing a permutation boundary are split
    so every resulting piece keeps its original media time.
    """

    def __init__(self, start_frame: int, end_frame: int, destination_frame: int):
        self.start = int(start_frame)
        self.end = int(end_frame)
        self.destination = int(destination_frame)
        if self.start < 0 or self.end <= self.start:
            raise ValueError("移动范围必须包含至少一帧")
        if self.start < self.destination < self.end:
            raise ValueError("插入位置不能位于正在移动的范围内部")
        if self.destination in {self.start, self.end}:
            raise ValueError("脚本段落已经位于目标位置")

    @property
    def duration(self) -> int:
        return self.end - self.start

    @property
    def affected_start(self) -> int:
        return min(self.start, self.destination)

    @property
    def affected_end(self) -> int:
        return max(self.end, self.destination)

    def shift_for_interval(self, start_frame: int, end_frame: int) -> int:
        start = int(start_frame)
        end = int(end_frame)
        if end <= start:
            raise ValueError("待移动对象必须包含至少一帧")
        if self.destination < self.start:
            if end <= self.destination or start >= self.end:
                return 0
            if start >= self.destination and end <= self.start:
                return self.duration
            if start >= self.start and end <= self.end:
                return self.destination - self.start
        else:
            if end <= self.start or start >= self.destination:
                return 0
            if start >= self.start and end <= self.end:
                return self.destination - self.end
            if start >= self.end and end <= self.destination:
                return -self.duration
        raise ValueError("对象跨越脚本移动边界，无法在不改变内容的情况下重排")

    def map_frame(self, frame: int) -> int:
        value = int(frame)
        if self.destination < self.start:
            if value < self.destination:
                return value
            if value < self.start:
                return value + self.duration
            if value < self.end:
                return self.destination + value - self.start
            return value
        if value < self.start:
            return value
        if value < self.end:
            return self.destination - self.duration + value - self.start
        if value < self.destination:
            return value - self.duration
        return value

    def apply(self, state: TimelineState) -> None:
        if self.end > state.duration_frames or self.destination > state.duration_frames:
            raise ValueError("脚本移动范围超出当前时间轴")
        locked = {
            track.id
            for track in state.tracks
            if track.locked
            and any(
                clip.track_id == track.id
                and clip.timeline_end > self.affected_start
                and clip.timeline_start < self.affected_end
                for clip in state.clips
            )
        }
        if locked:
            raise ValueError("移动范围经过锁定轨道，请先解锁：" + ", ".join(sorted(locked)))

        boundaries = sorted({self.start, self.end, self.destination})
        output: list[Clip] = []
        split_ids: dict[str, list[str]] = {}
        split_web_states: dict[str, WebClipState] = {}
        for clip in state.clips:
            cut_points = [
                boundary
                for boundary in boundaries
                if clip.timeline_start < boundary < clip.timeline_end
            ]
            piece_starts = [clip.timeline_start, *cut_points]
            piece_ends = [*cut_points, clip.timeline_end]
            piece_ids: list[str] = []
            for position, (piece_start, piece_end) in enumerate(
                zip(piece_starts, piece_ends, strict=True)
            ):
                piece_id = (
                    clip.id
                    if position == 0
                    else str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            "mediaflow:interval-move:"
                            f"{clip.id}:{piece_start}:{piece_end}:{self.start}:"
                            f"{self.end}:{self.destination}",
                        )
                    )
                )
                offset = piece_start - clip.timeline_start
                source_in = source_frame_at_timeline_offset(
                    clip.source_in,
                    offset,
                    clip.speed_numerator,
                    clip.speed_denominator,
                    freeze_source_frame=clip.freeze_source_frame,
                )
                keyframes = []
                for keyframe in clip.transform_keyframes:
                    if keyframe.timeline_offset is None:
                        keyframes.append(keyframe)
                        continue
                    if offset <= keyframe.timeline_offset < offset + (piece_end - piece_start):
                        keyframes.append(
                            keyframe.model_copy(
                                update={"timeline_offset": keyframe.timeline_offset - offset}
                            )
                        )
                piece = clip.model_copy(
                    update={
                        "id": piece_id,
                        "timeline_start": piece_start
                        + self.shift_for_interval(piece_start, piece_end),
                        "source_in": source_in,
                        "duration": piece_end - piece_start,
                        "transform_keyframes": keyframes,
                    }
                )
                output.append(piece)
                piece_ids.append(piece.id)
                if piece.id != clip.id and clip.id in state.web_states:
                    split_web_states[piece.id] = state.web_states[clip.id].model_copy(
                        update={"clip_id": piece.id, "revision": 0}
                    )
            split_ids[clip.id] = piece_ids
        state.clips = sorted(output, key=lambda clip: (clip.track_id, clip.timeline_start, clip.id))
        state.web_states.update(split_web_states)

        clips_by_id = {clip.id: clip for clip in state.clips}
        rebuilt_compounds: list[CompoundClip] = []
        for compound in state.compounds:
            clip_ids = [
                moved_id
                for clip_id in compound.clip_ids
                for moved_id in split_ids.get(clip_id, [clip_id])
                if moved_id in clips_by_id
            ]
            if len(clip_ids) >= 2:
                rebuilt_compounds.append(compound.model_copy(update={"clip_ids": clip_ids}))
        state.compounds = rebuilt_compounds
        rebound_transitions = [
            transition.model_copy(
                update={
                    "left_clip_id": split_ids.get(
                        transition.left_clip_id,
                        [transition.left_clip_id],
                    )[-1],
                    "right_clip_id": split_ids.get(
                        transition.right_clip_id,
                        [transition.right_clip_id],
                    )[0],
                }
            )
            for transition in state.transitions
        ]
        state.transitions = [
            transition
            for transition in rebound_transitions
            if TimelineRules.transition_is_valid(transition, clips_by_id)
        ]
        state.markers = [
            marker.model_copy(update={"frame": self.map_frame(marker.frame)})
            for marker in state.markers
        ]
        state.ranges = [self._move_range(item) for item in state.ranges]
        if state.sequence.in_out is not None:
            in_out = state.sequence.in_out
            moved = self._move_bounds(in_out.in_frame, in_out.out_frame)
            state.sequence = state.sequence.model_copy(
                update={"in_out": SequenceInOut(in_frame=moved[0], out_frame=moved[1])}
            )

    def _move_range(self, item: TimelineRange) -> TimelineRange:
        start, end = self._move_bounds(item.start_frame, item.end_frame)
        return item.model_copy(update={"start_frame": start, "end_frame": end})

    def _move_bounds(self, start: int, end: int) -> tuple[int, int]:
        if start <= self.affected_start and end >= self.affected_end:
            return start, end
        try:
            shift = self.shift_for_interval(start, end)
        except ValueError:
            mapped = sorted((self.map_frame(start), self.map_frame(end)))
            return mapped[0], max(mapped[0] + 1, mapped[1])
        return start + shift, end + shift
