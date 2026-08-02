from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from .enums import VisualEffectKind
from .model_base import DomainModel, new_id

VISUAL_EFFECT_SPECS: dict[VisualEffectKind, dict[str, Any]] = {
    VisualEffectKind.COLOR_ADJUSTMENT: {
        "label": "亮度 / 对比度 / 饱和度",
        "service": "avfilter.eq",
        "parameters": {
            "brightness": {"label": "亮度", "default": 0.0, "minimum": -1.0, "maximum": 1.0},
            "contrast": {"label": "对比度", "default": 1.0, "minimum": 0.0, "maximum": 2.0},
            "saturation": {"label": "饱和度", "default": 1.0, "minimum": 0.0, "maximum": 3.0},
        },
    },
    VisualEffectKind.GAUSSIAN_BLUR: {
        "label": "高斯模糊",
        "service": "avfilter.gblur",
        "parameters": {
            "sigma": {"label": "强度", "default": 3.0, "minimum": 0.1, "maximum": 20.0},
        },
    },
    VisualEffectKind.VIGNETTE: {
        "label": "暗角",
        "service": "avfilter.vignette",
        "parameters": {
            "angle": {"label": "范围", "default": 0.5, "minimum": 0.05, "maximum": 1.5},
        },
    },
}


def visual_effect_defaults(kind: VisualEffectKind) -> dict[str, float]:
    return {
        key: float(spec["default"])
        for key, spec in VISUAL_EFFECT_SPECS[kind]["parameters"].items()
    }


class ClipVisualEffect(DomainModel):
    id: str = Field(default_factory=new_id)
    kind: VisualEffectKind
    position: int = Field(ge=0)
    enabled: bool = True
    parameters: dict[str, float]

    @model_validator(mode="after")
    def validate_parameters(self) -> ClipVisualEffect:
        schema = VISUAL_EFFECT_SPECS[self.kind]["parameters"]
        if set(self.parameters) != set(schema):
            raise ValueError(f"{self.kind.value} visual effect parameters do not match its schema")
        for key, spec in schema.items():
            value = float(self.parameters[key])
            if not float(spec["minimum"]) <= value <= float(spec["maximum"]):
                raise ValueError(f"Visual effect parameter is outside its range: {key}")
        return self


def new_visual_effect(kind: VisualEffectKind, position: int) -> ClipVisualEffect:
    return ClipVisualEffect(
        kind=kind,
        position=position,
        parameters=visual_effect_defaults(kind),
    )


def visual_effect_mlt(effect: ClipVisualEffect) -> tuple[str, dict[str, float]]:
    spec = VISUAL_EFFECT_SPECS[effect.kind]
    return str(spec["service"]), {
        f"av.{key}": value for key, value in effect.parameters.items()
    }
