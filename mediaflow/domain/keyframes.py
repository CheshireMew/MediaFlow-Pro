from __future__ import annotations

from typing import Literal

from pydantic import Field

from .model_base import DomainModel

KeyframeInterpolation = Literal[
    "hold",
    "linear",
    "ease_in",
    "ease_out",
    "ease_in_out",
    "bezier",
]


class KeyframeCurve(DomainModel):
    """Deterministic outgoing interpolation for one native keyframe."""

    interpolation: KeyframeInterpolation = "linear"
    x1: float = Field(default=0.25, ge=0.0, le=1.0)
    y1: float = 0.1
    x2: float = Field(default=0.25, ge=0.0, le=1.0)
    y2: float = 1.0

    def control_points(self) -> tuple[float, float, float, float]:
        presets: dict[str, tuple[float, float, float, float]] = {
            "linear": (0.0, 0.0, 1.0, 1.0),
            "ease_in": (0.42, 0.0, 1.0, 1.0),
            "ease_out": (0.0, 0.0, 0.58, 1.0),
            "ease_in_out": (0.42, 0.0, 0.58, 1.0),
        }
        return presets.get(
            self.interpolation,
            (self.x1, self.y1, self.x2, self.y2),
        )


def keyframe_progress(curve: KeyframeCurve, fraction: float) -> float:
    value = min(1.0, max(0.0, float(fraction)))
    if curve.interpolation == "hold":
        return 1.0 if value >= 1.0 else 0.0
    if curve.interpolation == "linear":
        return value
    x1, y1, x2, y2 = curve.control_points()
    parameter = _solve_curve_parameter(value, x1, x2)
    return _cubic(parameter, y1, y2)


def interpolate_number(start: float, end: float, progress: float) -> float:
    return float(start) + (float(end) - float(start)) * float(progress)


def _cubic(parameter: float, first: float, second: float) -> float:
    inverse = 1.0 - parameter
    return (
        3.0 * inverse * inverse * parameter * first
        + 3.0 * inverse * parameter * parameter * second
        + parameter * parameter * parameter
    )


def _cubic_derivative(parameter: float, first: float, second: float) -> float:
    inverse = 1.0 - parameter
    return (
        3.0 * inverse * inverse * first
        + 6.0 * inverse * parameter * (second - first)
        + 3.0 * parameter * parameter * (1.0 - second)
    )


def _solve_curve_parameter(target_x: float, x1: float, x2: float) -> float:
    parameter = target_x
    for _ in range(8):
        error = _cubic(parameter, x1, x2) - target_x
        derivative = _cubic_derivative(parameter, x1, x2)
        if abs(error) <= 1e-7:
            return min(1.0, max(0.0, parameter))
        if abs(derivative) <= 1e-7:
            break
        candidate = parameter - error / derivative
        if not 0.0 <= candidate <= 1.0:
            break
        parameter = candidate

    lower = 0.0
    upper = 1.0
    for _ in range(24):
        parameter = (lower + upper) / 2.0
        sampled = _cubic(parameter, x1, x2)
        if abs(sampled - target_x) <= 1e-7:
            break
        if sampled < target_x:
            lower = parameter
        else:
            upper = parameter
    return parameter
