from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .enums import MaskShapeKind
from .keyframes import KeyframeCurve
from .model_base import DomainModel, new_id


class MaskPoint(DomainModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    incoming_x: float | None = Field(default=None, ge=0.0, le=1.0)
    incoming_y: float | None = Field(default=None, ge=0.0, le=1.0)
    outgoing_x: float | None = Field(default=None, ge=0.0, le=1.0)
    outgoing_y: float | None = Field(default=None, ge=0.0, le=1.0)

    def incoming(self) -> tuple[float, float]:
        return (
            self.x if self.incoming_x is None else self.incoming_x,
            self.y if self.incoming_y is None else self.incoming_y,
        )

    def outgoing(self) -> tuple[float, float]:
        return (
            self.x if self.outgoing_x is None else self.outgoing_x,
            self.y if self.outgoing_y is None else self.outgoing_y,
        )


class MaskGeometry(DomainModel):
    center_x: float = Field(default=0.5, ge=0.0, le=1.0)
    center_y: float = Field(default=0.5, ge=0.0, le=1.0)
    width: float = Field(default=0.5, gt=0.0, le=2.0)
    height: float = Field(default=0.5, gt=0.0, le=2.0)
    rotation: float = 0.0
    points: tuple[MaskPoint, ...] = ()


class MaskKeyframe(DomainModel):
    source_frame: int | None = Field(default=None, ge=0)
    timeline_offset: int | None = Field(default=None, ge=0)
    geometry: MaskGeometry
    source: Literal["manual", "subject_tracking"] = "manual"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    curve: KeyframeCurve = Field(default_factory=KeyframeCurve)

    @model_validator(mode="after")
    def one_time_anchor(self) -> MaskKeyframe:
        if (self.source_frame is None) == (self.timeline_offset is None):
            raise ValueError(
                "A mask keyframe must use exactly one source-frame or timeline-offset anchor"
            )
        return self


class ClipMask(DomainModel):
    id: str = Field(default_factory=new_id)
    name: str = Field(default="蒙版", min_length=1, max_length=120)
    kind: MaskShapeKind = MaskShapeKind.ELLIPSE
    position: int = Field(ge=0)
    enabled: bool = True
    combine_mode: Literal["replace", "add", "subtract", "intersect"] = "replace"
    inverted: bool = False
    feather: int = Field(default=0, ge=0, le=1000)
    feather_passes: int = Field(default=1, ge=1, le=10)
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    geometry: MaskGeometry = Field(default_factory=MaskGeometry)
    keyframes: list[MaskKeyframe] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_shape_and_keyframes(self) -> ClipMask:
        self._validate_geometry(self.geometry)
        for keyframe in self.keyframes:
            self._validate_geometry(keyframe.geometry)
        anchor_modes = {
            "timeline" if item.timeline_offset is not None else "source"
            for item in self.keyframes
        }
        if len(anchor_modes) > 1:
            raise ValueError("Mask keyframes cannot mix time anchor modes")
        frames = [
            item.timeline_offset if item.timeline_offset is not None else item.source_frame
            for item in self.keyframes
        ]
        resolved = [int(frame) for frame in frames if frame is not None]
        if len(resolved) != len(frames) or resolved != sorted(set(resolved)):
            raise ValueError("Mask keyframes must have unique ordered anchors")
        return self

    def _validate_geometry(self, geometry: MaskGeometry) -> None:
        if self.kind in {MaskShapeKind.POLYGON, MaskShapeKind.BEZIER}:
            if len(geometry.points) < 3:
                raise ValueError("Polygon and Bezier masks require at least three points")
            expected = len(self.geometry.points)
            if expected and len(geometry.points) != expected:
                raise ValueError("Path mask keyframes must keep the same point count")
            if self.kind == MaskShapeKind.POLYGON and any(
                value is not None
                for point in geometry.points
                for value in (
                    point.incoming_x,
                    point.incoming_y,
                    point.outgoing_x,
                    point.outgoing_y,
                )
            ):
                raise ValueError("Only Bezier masks can carry control handles")
        elif geometry.points:
            raise ValueError("Only polygon and Bezier masks can carry custom points")
