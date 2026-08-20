from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from mediaflow.application.timeline_change_session import TimelineChangeSession
from mediaflow.domain.timeline import (
    Clip,
    TimelineMarker,
    TimelineMergeConflict,
    TimelineRange,
    TimelineState,
)


class TimelineMarkerRangeEditing:
    sequence_id: str
    _changes: TimelineChangeSession

    if TYPE_CHECKING:
        def _commit(
            self,
            label: str,
            mutate: Callable[[TimelineState], None],
            *,
            allow_locked_changes: bool = False,
        ) -> None: ...

        def _clip_index(self, state: TimelineState, clip_id: str) -> int: ...

        def _marker(self, marker_id: str) -> TimelineMarker: ...

        def _range(self, range_id: str) -> TimelineRange: ...
    def add_marker(self, frame: int, name: str = "", color: str = "#4ea1ff") -> TimelineMarker:
        marker = TimelineMarker(
            sequence_id=self.sequence_id,
            frame=frame,
            name=name,
            color=color,
        )

        def mutate(state: TimelineState) -> None:
            state.markers.append(marker)

        self._commit("添加标记", mutate)
        return self._marker(marker.id)

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
                sequence_id=self.sequence_id,
                frame=frame,
                name=f"{marker_prefix}{index}",
                color="#ff9f43",
            )
            for index, frame in enumerate(frames, start=1)
        ]

        def mutate(state: TimelineState) -> None:
            index = self._clip_index(state, clip_id)
            if state.clips[index] != expected_clip:
                raise TimelineMergeConflict("clip", clip_id)
            state.markers = [marker for marker in state.markers if not marker.name.startswith(marker_prefix)]
            state.markers.extend(markers)

        self._commit("更新场景切点", mutate)
        return [self._marker(marker.id) for marker in markers]

    def update_marker(
        self,
        marker_id: str,
        *,
        frame: int,
        name: str,
        color: str,
    ) -> TimelineMarker:
        source = self._marker(marker_id)

        def mutate(state: TimelineState) -> None:
            index = next(index for index, item in enumerate(state.markers) if item.id == marker_id)
            state.markers[index] = source.model_copy(update={"frame": frame, "name": name, "color": color})

        self._commit("调整标记", mutate)
        return self._marker(marker_id)

    def remove_marker(self, marker_id: str) -> None:
        self._marker(marker_id)

        def mutate(state: TimelineState) -> None:
            state.markers = [item for item in state.markers if item.id != marker_id]

        self._commit("删除标记", mutate)

    def add_range(
        self,
        start_frame: int,
        end_frame: int,
        name: str = "",
        color: str = "#4ea1ff",
    ) -> TimelineRange:
        item = TimelineRange(
            sequence_id=self.sequence_id,
            start_frame=start_frame,
            end_frame=end_frame,
            name=name,
            color=color,
        )

        def mutate(state: TimelineState) -> None:
            state.ranges.append(item)

        self._commit("添加范围", mutate)
        return self._range(item.id)

    def update_range(
        self,
        range_id: str,
        *,
        start_frame: int,
        end_frame: int,
        name: str,
        color: str,
    ) -> TimelineRange:
        source = self._range(range_id)

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

        self._commit("调整范围", mutate)
        return self._range(range_id)

    def remove_range(self, range_id: str) -> None:
        self._range(range_id)

        def mutate(state: TimelineState) -> None:
            state.ranges = [item for item in state.ranges if item.id != range_id]

        self._commit("删除范围", mutate)
