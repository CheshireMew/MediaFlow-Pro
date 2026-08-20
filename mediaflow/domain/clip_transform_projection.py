from __future__ import annotations

from dataclasses import dataclass

from mediaflow.domain.timebase import timeline_offset_for_source_frame
from mediaflow.domain.timeline import Clip, ClipTransform


@dataclass(frozen=True, slots=True)
class ClipTransformProjection:
    points: tuple[tuple[int, ClipTransform], ...]
    has_keyframes: bool


def project_clip_transform_points(clip: Clip) -> ClipTransformProjection:
    """Resolve every transform anchor into the clip-local timeline clock."""

    points: dict[int, ClipTransform] = {0: clip.transform}
    has_keyframes = False
    for keyframe in clip.transform_keyframes:
        if keyframe.timeline_offset is not None:
            local_frame = keyframe.timeline_offset
        else:
            assert keyframe.source_frame is not None
            try:
                local_frame = timeline_offset_for_source_frame(
                    clip.source_in,
                    keyframe.source_frame,
                    clip.speed_numerator,
                    clip.speed_denominator,
                    freeze_source_frame=clip.freeze_source_frame,
                )
            except ValueError:
                continue
        if 0 <= local_frame < clip.duration:
            points[local_frame] = keyframe.transform
            has_keyframes = True
    return ClipTransformProjection(
        points=tuple(sorted(points.items())),
        has_keyframes=has_keyframes,
    )
