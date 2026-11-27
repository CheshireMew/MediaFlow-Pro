from __future__ import annotations

from collections.abc import Iterable

from mediaflow.application.timeline_change_session import TimelineChangeSession
from mediaflow.application.timeline_entities import (
    clip_index,
    marker_by_id,
    range_by_id,
)
from mediaflow.domain.timeline import (
    Clip,
    TimelineMarker,
    TimelineMergeConflict,
    TimelineRange,
    TimelineState,
)


class TimelineMarkerRangeEditing:
    def __init__(self, changes: TimelineChangeSession) -> None:
        self._changes = changes

    def add_marker(self, frame: int, name: str = "", color: str = "#4ea1ff") -> TimelineMarker:
        marker = TimelineMarker(
            sequence_id=self._changes.sequence_id,
            frame=frame,
            name=name,
            color=color,
        )

        def mutate(state: TimelineState) -> None:
            state.markers.append(marker)

        self._changes.commit_change("添加标记", mutate)
        return marker_by_id(self._changes.current, marker.id)

    def replace_scene_markers(
        self,
        clip_id: str,
        frames: Iterable[int],
        *,
        expected_clip: Clip,
    ) -> list[TimelineMarker]:
        marker_prefix = f"场景切点 · {clip_id[:8]} · "
        markers = [
            TimelineMarker(
                sequence_id=self._changes.sequence_id,
                frame=frame,
                name=f"{marker_prefix}{index}",
                color="#ff9f43",
            )
            for index, frame in enumerate(frames, start=1)
        ]

        def mutate(state: TimelineState) -> None:
            index = clip_index(state, clip_id)
            if state.clips[index] != expected_clip:
                raise TimelineMergeConflict("clip", clip_id)
            state.markers = [marker for marker in state.markers if not marker.name.startswith(marker_prefix)]
            state.markers.extend(markers)

        self._changes.commit_change("更新场景切点", mutate)
        return [marker_by_id(self._changes.current, marker.id) for marker in markers]

    def update_marker(
        self,
        marker_id: str,
        *,
        frame: int,
        name: str,
        color: str,
    ) -> TimelineMarker:
        source = marker_by_id(self._changes.current, marker_id)

        def mutate(state: TimelineState) -> None:
            index = next(index for index, item in enumerate(state.markers) if item.id == marker_id)
            state.markers[index] = source.model_copy(update={"frame": frame, "name": name, "color": color})

        self._changes.commit_change("调整标记", mutate)
        return marker_by_id(self._changes.current, marker_id)

    def remove_marker(self, marker_id: str) -> None:
        marker_by_id(self._changes.current, marker_id)

        def mutate(state: TimelineState) -> None:
            state.markers = [item for item in state.markers if item.id != marker_id]

        self._changes.commit_change("删除标记", mutate)

    def add_range(
        self,
        start_frame: int,
        end_frame: int,
        name: str = "",
        color: str = "#4ea1ff",
    ) -> TimelineRange:
        item = TimelineRange(
            sequence_id=self._changes.sequence_id,
            start_frame=start_frame,
            end_frame=end_frame,
            name=name,
            color=color,
        )

        def mutate(state: TimelineState) -> None:
            state.ranges.append(item)

        self._changes.commit_change("添加范围", mutate)
        return range_by_id(self._changes.current, item.id)

    def update_range(
        self,
        range_id: str,
        *,
        start_frame: int,
        end_frame: int,
        name: str,
        color: str,
    ) -> TimelineRange:
        source = range_by_id(self._changes.current, range_id)

        def mutate(state: TimelineState) -> None:
            index = next(index for index, item in enumerate(state.ranges) if item.id == range_id)
            state.ranges[index] = source.model_copy(
                update={
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "name": name,
                    "color": color,
                }
            )

        self._changes.commit_change("调整范围", mutate)
        return range_by_id(self._changes.current, range_id)

    def remove_range(self, range_id: str) -> None:
        range_by_id(self._changes.current, range_id)

        def mutate(state: TimelineState) -> None:
            state.ranges = [item for item in state.ranges if item.id != range_id]

        self._changes.commit_change("删除范围", mutate)
