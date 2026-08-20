from __future__ import annotations

import uuid

from mediaflow.application.timeline_rules import TimelineRules
from mediaflow.domain.project import SequenceInOut
from mediaflow.domain.timebase import source_frame_at_timeline_offset
from mediaflow.domain.timeline import Clip, CompoundClip, TimelineRange, TimelineState
from mediaflow.domain.web_state import WebClipState


class RippleDeletePolicy:
    @staticmethod
    def apply(
        state: TimelineState,
        start_frame: int,
        end_frame: int,
    ) -> None:
        """Remove one interval from an in-memory timeline state."""
        start = max(0, int(start_frame))
        end = min(state.duration_frames, int(end_frame))
        if end <= start:
            raise ValueError("删除范围必须包含至少一帧")
        duration = end - start

        def advanced_source_in(clip: Clip, timeline_frames: int) -> int:
            return source_frame_at_timeline_offset(
                clip.source_in,
                timeline_frames,
                clip.speed_numerator,
                clip.speed_denominator,
                freeze_source_frame=clip.freeze_source_frame,
            )

        def collapse(frame: int) -> int:
            if frame < start:
                return frame
            if frame < end:
                return start
            return frame - duration

        locked_track_ids = {track.id for track in state.tracks if track.locked}
        output: list[Clip] = []
        removed_ids: set[str] = set()
        split_ids: dict[str, str] = {}
        split_web_states: dict[str, WebClipState] = {}
        for clip in state.clips:
            if clip.track_id in locked_track_ids or clip.timeline_end <= start:
                output.append(clip)
                continue
            if clip.timeline_start >= end:
                output.append(clip.model_copy(update={"timeline_start": clip.timeline_start - duration}))
                continue
            if clip.timeline_start >= start and clip.timeline_end <= end:
                removed_ids.add(clip.id)
                continue
            if clip.timeline_start < start and clip.timeline_end > end:
                left = clip.model_copy(update={"duration": start - clip.timeline_start})
                right_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"mediaflow:ripple:{clip.id}:{start}:{end}",
                    )
                )
                right = clip.model_copy(
                    update={
                        "id": right_id,
                        "timeline_start": start,
                        "source_in": advanced_source_in(clip, end - clip.timeline_start),
                        "duration": clip.timeline_end - end,
                    }
                )
                output.extend((left, right))
                split_ids[clip.id] = right.id
                if clip.id in state.web_states:
                    split_web_states[right.id] = state.web_states[clip.id].model_copy(
                        update={"clip_id": right.id, "revision": 0}
                    )
                continue
            if clip.timeline_start < start:
                output.append(clip.model_copy(update={"duration": start - clip.timeline_start}))
                continue
            output.append(
                clip.model_copy(
                    update={
                        "timeline_start": start,
                        "source_in": advanced_source_in(clip, end - clip.timeline_start),
                        "duration": clip.timeline_end - end,
                    }
                )
            )
        state.clips = output
        state.web_states = {
            clip_id: web_state
            for clip_id, web_state in state.web_states.items()
            if clip_id not in removed_ids
        }
        state.web_states.update(split_web_states)
        clips_by_id = {clip.id: clip for clip in state.clips}
        rebuilt_compounds: list[CompoundClip] = []
        for compound in state.compounds:
            clip_ids: list[str] = []
            for clip_id in compound.clip_ids:
                if clip_id in clips_by_id:
                    clip_ids.append(clip_id)
                if clip_id in split_ids:
                    clip_ids.append(split_ids[clip_id])
            if len(clip_ids) >= 2:
                rebuilt_compounds.append(compound.model_copy(update={"clip_ids": clip_ids}))
        state.compounds = rebuilt_compounds
        state.transitions = [
            item for item in state.transitions if TimelineRules.transition_is_valid(item, clips_by_id)
        ]
        state.markers = [
            marker.model_copy(update={"frame": collapse(marker.frame)}) for marker in state.markers
        ]
        ranges: list[TimelineRange] = []
        for item in state.ranges:
            range_start = collapse(item.start_frame)
            range_end = collapse(item.end_frame)
            if range_end > range_start:
                ranges.append(item.model_copy(update={"start_frame": range_start, "end_frame": range_end}))
        state.ranges = ranges
        if state.sequence.in_out is not None:
            in_frame = collapse(state.sequence.in_out.in_frame)
            out_frame = collapse(state.sequence.in_out.out_frame)
            state.sequence = state.sequence.model_copy(
                update={
                    "in_out": (
                        SequenceInOut(in_frame=in_frame, out_frame=out_frame)
                        if out_frame > in_frame
                        else None
                    )
                }
            )
