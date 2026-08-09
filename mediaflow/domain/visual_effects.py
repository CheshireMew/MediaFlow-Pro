from __future__ import annotations

from pydantic import Field, model_validator

from .editor_fields import EditorFieldConstraints, EditorFieldDescriptor
from .enums import VisualEffectKind
from .model_base import DomainModel, new_id


class VisualEffectDefinition(DomainModel):
    label: str
    service: str
    descriptors: tuple[EditorFieldDescriptor, ...]

    @model_validator(mode="after")
    def unique_fields(self) -> VisualEffectDefinition:
        ids = [item.id for item in self.descriptors]
        if len(ids) != len(set(ids)):
            raise ValueError("Visual effect field identifiers must be unique")
        return self


def _number_field(
    field_id: str,
    label: str,
    default: float,
    minimum: float,
    maximum: float,
    *,
    step: float = 0.01,
) -> EditorFieldDescriptor:
    return EditorFieldDescriptor(
        id=field_id,
        label=label,
        description="",
        group="视觉效果",
        kind="number",
        control="slider",
        default=default,
        unit=None,
        constraints=EditorFieldConstraints(
            minimum=minimum,
            maximum=maximum,
            step=step,
        ),
        options_source=None,
        timeline="keyframe",
    )


VISUAL_EFFECT_DEFINITIONS: dict[VisualEffectKind, VisualEffectDefinition] = {
    VisualEffectKind.COLOR_ADJUSTMENT: VisualEffectDefinition(
        label="亮度 / 对比度 / 饱和度",
        service="avfilter.eq",
        descriptors=(
            _number_field("brightness", "亮度", 0.0, -1.0, 1.0),
            _number_field("contrast", "对比度", 1.0, 0.0, 2.0),
            _number_field("saturation", "饱和度", 1.0, 0.0, 3.0),
        ),
    ),
    VisualEffectKind.GAUSSIAN_BLUR: VisualEffectDefinition(
        label="高斯模糊",
        service="avfilter.gblur",
        descriptors=(
            _number_field("sigma", "强度", 3.0, 0.1, 20.0, step=0.1),
        ),
    ),
    VisualEffectKind.VIGNETTE: VisualEffectDefinition(
        label="暗角",
        service="avfilter.vignette",
        descriptors=(
            _number_field("angle", "范围", 0.5, 0.05, 1.5),
        ),
    ),
}


def visual_effect_defaults(kind: VisualEffectKind) -> dict[str, float]:
    return {
        descriptor.id: float(descriptor.default)
        for descriptor in VISUAL_EFFECT_DEFINITIONS[kind].descriptors
    }


class ClipVisualEffect(DomainModel):
    id: str = Field(default_factory=new_id)
    kind: VisualEffectKind
    position: int = Field(ge=0)
    enabled: bool = True
    parameters: dict[str, float]

    @model_validator(mode="after")
    def validate_parameters(self) -> ClipVisualEffect:
        descriptors = {
            item.id: item for item in VISUAL_EFFECT_DEFINITIONS[self.kind].descriptors
        }
        if set(self.parameters) != set(descriptors):
            raise ValueError(
                f"{self.kind.value} visual effect parameters do not match its descriptors"
            )
        for field_id, descriptor in descriptors.items():
            descriptor.validate_value(self.parameters[field_id])
        return self


def new_visual_effect(kind: VisualEffectKind, position: int) -> ClipVisualEffect:
    return ClipVisualEffect(
        kind=kind,
        position=position,
        parameters=visual_effect_defaults(kind),
    )


def visual_effect_mlt(effect: ClipVisualEffect) -> tuple[str, dict[str, float]]:
    definition = VISUAL_EFFECT_DEFINITIONS[effect.kind]
    return definition.service, {
        f"av.{key}": value for key, value in effect.parameters.items()
    }
