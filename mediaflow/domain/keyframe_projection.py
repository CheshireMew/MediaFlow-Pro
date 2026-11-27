from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, Self

from .keyframes import KeyframeCurve, keyframe_progress
from .timebase import timeline_offset_for_source_frame


class KeyframeClock(Protocol):
    @property
    def source_in(self) -> int: ...

    @property
    def speed_numerator(self) -> int: ...

    @property
    def speed_denominator(self) -> int: ...

    @property
    def freeze_source_frame(self) -> int | None: ...

    @property
    def duration(self) -> int: ...


class AnchoredKeyframe(Protocol):
    @property
    def timeline_offset(self) -> int | None: ...

    @property
    def source_frame(self) -> int | None: ...

    @property
    def curve(self) -> KeyframeCurve: ...

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self: ...


@dataclass(frozen=True, slots=True)
class KeyframeProjection[ValueT]:
    points: tuple[tuple[int, ValueT], ...]
    curves: tuple[tuple[int, KeyframeCurve], ...]
    has_keyframes: bool


def project_timeline_keyframes[KeyframeT: AnchoredKeyframe](
    clock: KeyframeClock,
    keyframes: Sequence[KeyframeT],
) -> tuple[KeyframeT, ...]:
    """Resolve source anchors, discard invisible points, and keep the last point at each offset."""

    converted: dict[int, KeyframeT] = {}
    for item in keyframes:
        if item.timeline_offset is not None:
            offset = item.timeline_offset
        else:
            assert item.source_frame is not None
            try:
                offset = timeline_offset_for_source_frame(
                    clock.source_in,
                    item.source_frame,
                    clock.speed_numerator,
                    clock.speed_denominator,
                    freeze_source_frame=clock.freeze_source_frame,
                )
            except ValueError:
                continue
        if 0 <= offset < clock.duration:
            converted[offset] = item.model_copy(update={"source_frame": None, "timeline_offset": offset})
    return tuple(converted[offset] for offset in sorted(converted))


def project_keyframe_values[KeyframeT: AnchoredKeyframe, ValueT](
    initial: ValueT,
    keyframes: Sequence[KeyframeT],
    value_of: Callable[[KeyframeT], ValueT],
) -> KeyframeProjection[ValueT]:
    points = {0: initial}
    curves = {0: KeyframeCurve()}
    for item in keyframes:
        assert item.timeline_offset is not None
        points[item.timeline_offset] = value_of(item)
        curves[item.timeline_offset] = item.curve
    return KeyframeProjection(
        points=tuple(sorted(points.items())),
        curves=tuple(sorted(curves.items())),
        has_keyframes=bool(keyframes),
    )


def sample_keyframe_values[ValueT](
    projection: KeyframeProjection[ValueT],
    duration: int,
    interpolate: Callable[[ValueT, ValueT, float], ValueT],
) -> tuple[tuple[int, ValueT], ...]:
    """Bake non-linear curves while leaving linear spans to the renderer."""

    curves = dict(projection.curves)
    ordered = projection.points
    compiled: dict[int, ValueT] = {}
    for index, (start_frame, start_value) in enumerate(ordered):
        compiled[start_frame] = start_value
        if index + 1 >= len(ordered):
            continue
        end_frame, end_value = ordered[index + 1]
        curve = curves[start_frame]
        if curve.interpolation == "linear":
            compiled[end_frame] = end_value
            continue
        span = end_frame - start_frame
        for frame in range(start_frame + 1, end_frame + 1):
            progress = keyframe_progress(curve, (frame - start_frame) / span)
            compiled[frame] = interpolate(start_value, end_value, progress)
    final_frame = duration - 1
    if final_frame not in compiled:
        compiled[final_frame] = ordered[-1][1]
    return tuple(sorted(compiled.items()))
