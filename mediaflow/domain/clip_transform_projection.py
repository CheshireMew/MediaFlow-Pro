from __future__ import annotations

from mediaflow.domain.keyframe_projection import (
    KeyframeProjection,
    project_keyframe_values,
    project_timeline_keyframes,
    sample_keyframe_values,
)
from mediaflow.domain.keyframes import interpolate_number
from mediaflow.domain.timeline import Clip, ClipTransform, ClipTransformKeyframe


def project_clip_transform_points(clip: Clip) -> KeyframeProjection[ClipTransform]:
    """Resolve every transform anchor into the clip-local timeline clock."""

    return project_keyframe_values(
        clip.transform,
        project_clip_transform_keyframes(clip),
        lambda item: item.transform,
    )


def project_clip_transform_keyframes(
    clip: Clip,
) -> tuple[ClipTransformKeyframe, ...]:
    """Resolve persisted source or timeline anchors into editable clip offsets."""

    return project_timeline_keyframes(clip, clip.transform_keyframes)


def sampled_clip_transform_points(
    clip: Clip,
) -> tuple[tuple[int, ClipTransform], ...]:
    """Expand non-linear native curves into exact per-frame transform values."""

    return sample_keyframe_values(project_clip_transform_points(clip), clip.duration, _interpolate_transform)


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
