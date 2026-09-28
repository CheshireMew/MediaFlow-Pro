from __future__ import annotations

from dataclasses import dataclass

from mediaflow.domain.keyframes import KeyframeCurve, interpolate_number, keyframe_progress
from mediaflow.domain.timebase import timeline_offset_for_source_frame
from mediaflow.domain.timeline import Clip, ClipTransform, ClipTransformKeyframe


@dataclass(frozen=True, slots=True)
class ClipTransformProjection:
    points: tuple[tuple[int, ClipTransform], ...]
    curves: tuple[tuple[int, KeyframeCurve], ...]
    has_keyframes: bool


def project_clip_transform_points(clip: Clip) -> ClipTransformProjection:
    """Resolve every transform anchor into the clip-local timeline clock."""

    points: dict[int, ClipTransform] = {0: clip.transform}
    curves: dict[int, KeyframeCurve] = {0: KeyframeCurve()}
    has_keyframes = False
    for keyframe in project_clip_transform_keyframes(clip):
        assert keyframe.timeline_offset is not None
        points[keyframe.timeline_offset] = keyframe.transform
        curves[keyframe.timeline_offset] = keyframe.curve
        has_keyframes = True
    return ClipTransformProjection(
        points=tuple(sorted(points.items())),
        curves=tuple(sorted(curves.items())),
        has_keyframes=has_keyframes,
    )


def project_clip_transform_keyframes(
    clip: Clip,
) -> tuple[ClipTransformKeyframe, ...]:
    """Resolve persisted source or timeline anchors into editable clip offsets."""

    converted: dict[int, ClipTransformKeyframe] = {}
    for item in clip.transform_keyframes:
        if item.timeline_offset is not None:
            offset = item.timeline_offset
        else:
            assert item.source_frame is not None
            try:
                offset = timeline_offset_for_source_frame(
                    clip.source_in,
                    item.source_frame,
                    clip.speed_numerator,
                    clip.speed_denominator,
                    freeze_source_frame=clip.freeze_source_frame,
                )
            except ValueError:
                continue
        if 0 <= offset < clip.duration:
            converted[offset] = item.model_copy(
                update={"source_frame": None, "timeline_offset": offset}
            )
    return tuple(converted[offset] for offset in sorted(converted))


def sampled_clip_transform_points(
    clip: Clip,
) -> tuple[tuple[int, ClipTransform], ...]:
    """Expand non-linear native curves into exact per-frame transform values."""

    projection = project_clip_transform_points(clip)
    curves = dict(projection.curves)
    ordered = list(projection.points)
    compiled: dict[int, ClipTransform] = {}
    for index, (start_frame, start_transform) in enumerate(ordered):
        compiled[start_frame] = start_transform
        if index + 1 >= len(ordered):
            continue
        end_frame, end_transform = ordered[index + 1]
        curve = curves[start_frame]
        if curve.interpolation == "linear":
            compiled[end_frame] = end_transform
            continue
        span = end_frame - start_frame
        for frame in range(start_frame + 1, end_frame + 1):
            progress = keyframe_progress(curve, (frame - start_frame) / span)
            compiled[frame] = _interpolate_transform(
                start_transform,
                end_transform,
                progress,
            )
    last_frame = clip.duration - 1
    if last_frame not in compiled:
        compiled[last_frame] = ordered[-1][1]
    return tuple(sorted(compiled.items()))


def _interpolate_transform(
    start: ClipTransform,
    end: ClipTransform,
    progress: float,
) -> ClipTransform:
    values = {
        name: interpolate_number(
            getattr(start, name),
            getattr(end, name),
            progress,
        )
        for name in ClipTransform.model_fields
    }
    values["scale_x"] = max(0.01, values["scale_x"])
    values["scale_y"] = max(0.01, values["scale_y"])
    for name in (
        "crop_left",
        "crop_top",
        "crop_right",
        "crop_bottom",
        "opacity",
    ):
        values[name] = min(1.0, max(0.0, values[name]))
    return ClipTransform(**values)
