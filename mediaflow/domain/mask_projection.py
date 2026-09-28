from __future__ import annotations

import json
import math
from dataclasses import dataclass

from .enums import MaskShapeKind
from .keyframes import KeyframeCurve, interpolate_number, keyframe_progress
from .masks import ClipMask, MaskGeometry, MaskKeyframe, MaskPoint
from .timebase import timeline_offset_for_source_frame
from .timeline import Clip


@dataclass(frozen=True, slots=True)
class MaskProjection:
    points: tuple[tuple[int, MaskGeometry], ...]
    curves: tuple[tuple[int, KeyframeCurve], ...]
    has_keyframes: bool


def project_mask_keyframes(
    clip: Clip,
    mask: ClipMask,
) -> tuple[MaskKeyframe, ...]:
    converted: dict[int, MaskKeyframe] = {}
    for item in mask.keyframes:
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


def project_mask_points(clip: Clip, mask: ClipMask) -> MaskProjection:
    points: dict[int, MaskGeometry] = {0: mask.geometry}
    curves: dict[int, KeyframeCurve] = {0: KeyframeCurve()}
    keyframes = project_mask_keyframes(clip, mask)
    for item in keyframes:
        assert item.timeline_offset is not None
        points[item.timeline_offset] = item.geometry
        curves[item.timeline_offset] = item.curve
    return MaskProjection(
        points=tuple(sorted(points.items())),
        curves=tuple(sorted(curves.items())),
        has_keyframes=bool(keyframes),
    )


def sampled_mask_points(
    clip: Clip,
    mask: ClipMask,
) -> tuple[tuple[int, MaskGeometry], ...]:
    projection = project_mask_points(clip, mask)
    curves = dict(projection.curves)
    ordered = list(projection.points)
    compiled: dict[int, MaskGeometry] = {}
    for index, (start_frame, start_geometry) in enumerate(ordered):
        compiled[start_frame] = start_geometry
        if index + 1 >= len(ordered):
            continue
        end_frame, end_geometry = ordered[index + 1]
        curve = curves[start_frame]
        if curve.interpolation == "linear":
            compiled[end_frame] = end_geometry
            continue
        span = end_frame - start_frame
        for frame in range(start_frame + 1, end_frame + 1):
            progress = keyframe_progress(curve, (frame - start_frame) / span)
            compiled[frame] = _interpolate_geometry(
                start_geometry,
                end_geometry,
                progress,
            )
    final_frame = clip.duration - 1
    if final_frame not in compiled:
        compiled[final_frame] = ordered[-1][1]
    return tuple(sorted(compiled.items()))


def mask_spline_json(
    clip: Clip,
    mask: ClipMask,
    *,
    producer_start: int,
) -> str:
    splines = {
        str(producer_start + frame): _geometry_spline(mask.kind, geometry)
        for frame, geometry in sampled_mask_points(clip, mask)
    }
    return json.dumps(splines, ensure_ascii=False, separators=(",", ":"))


def _interpolate_geometry(
    start: MaskGeometry,
    end: MaskGeometry,
    progress: float,
) -> MaskGeometry:
    return MaskGeometry(
        center_x=min(1.0, max(0.0, interpolate_number(start.center_x, end.center_x, progress))),
        center_y=min(1.0, max(0.0, interpolate_number(start.center_y, end.center_y, progress))),
        width=min(2.0, max(0.0001, interpolate_number(start.width, end.width, progress))),
        height=min(2.0, max(0.0001, interpolate_number(start.height, end.height, progress))),
        rotation=interpolate_number(start.rotation, end.rotation, progress),
        points=tuple(
            MaskPoint(
                x=min(1.0, max(0.0, interpolate_number(left.x, right.x, progress))),
                y=min(1.0, max(0.0, interpolate_number(left.y, right.y, progress))),
                incoming_x=_interpolate_optional_handle(
                    left.incoming_x, right.incoming_x, left.x, right.x, progress
                ),
                incoming_y=_interpolate_optional_handle(
                    left.incoming_y, right.incoming_y, left.y, right.y, progress
                ),
                outgoing_x=_interpolate_optional_handle(
                    left.outgoing_x, right.outgoing_x, left.x, right.x, progress
                ),
                outgoing_y=_interpolate_optional_handle(
                    left.outgoing_y, right.outgoing_y, left.y, right.y, progress
                ),
            )
            for left, right in zip(start.points, end.points, strict=True)
        ),
    )


def _geometry_spline(
    kind: MaskShapeKind,
    geometry: MaskGeometry,
) -> list[list[list[float]]]:
    if kind == MaskShapeKind.POLYGON:
        return [_linear_bpoint(point.x, point.y) for point in geometry.points]
    if kind == MaskShapeKind.BEZIER:
        return [
            [list(point.incoming()), [point.x, point.y], list(point.outgoing())]
            for point in geometry.points
        ]
    half_width = geometry.width / 2.0
    half_height = geometry.height / 2.0
    if kind == MaskShapeKind.RECTANGLE:
        local = (
            (-half_width, -half_height),
            (half_width, -half_height),
            (half_width, half_height),
            (-half_width, half_height),
        )
        return [
            _linear_bpoint(*_rotate(point, geometry))
            for point in local
        ]

    kappa = 0.5522847498307936
    anchors = (
        ((0.0, -half_height), (-kappa * half_width, -half_height), (kappa * half_width, -half_height)),
        ((half_width, 0.0), (half_width, -kappa * half_height), (half_width, kappa * half_height)),
        ((0.0, half_height), (kappa * half_width, half_height), (-kappa * half_width, half_height)),
        ((-half_width, 0.0), (-half_width, kappa * half_height), (-half_width, -kappa * half_height)),
    )
    output = []
    for point, incoming, outgoing in anchors:
        output.append(
            [
                list(_rotate(incoming, geometry)),
                list(_rotate(point, geometry)),
                list(_rotate(outgoing, geometry)),
            ]
        )
    return output


def _linear_bpoint(x: float, y: float) -> list[list[float]]:
    return [[x, y], [x, y], [x, y]]


def _interpolate_optional_handle(
    start: float | None,
    end: float | None,
    start_anchor: float,
    end_anchor: float,
    progress: float,
) -> float | None:
    if start is None and end is None:
        return None
    return min(
        1.0,
        max(
            0.0,
            interpolate_number(
                start_anchor if start is None else start,
                end_anchor if end is None else end,
                progress,
            ),
        ),
    )


def _rotate(
    point: tuple[float, float],
    geometry: MaskGeometry,
) -> tuple[float, float]:
    radians = math.radians(geometry.rotation)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    x, y = point
    return (
        geometry.center_x + x * cosine - y * sine,
        geometry.center_y + x * sine + y * cosine,
    )
