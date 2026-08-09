from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from .editor_fields import (
    EditorFieldChoice,
    EditorFieldConstraints,
    EditorFieldDescriptor,
)
from .enums import AudioEffectKind
from .model_base import DomainModel, new_id


class AudioBus(DomainModel):
    id: str = Field(default_factory=new_id)
    sequence_id: str
    name: str
    parent_bus_id: str | None = None
    position: int = 0
    gain_db: float = 0.0
    muted: bool = False
    solo: bool = False
    channel_layout: str = "stereo"

    @field_validator("channel_layout")
    @classmethod
    def valid_channel_layout(cls, value: str) -> str:
        if value not in {"mono", "stereo", "5.1"}:
            raise ValueError("Unsupported channel layout")
        return value


class AudioEffectDefinition(DomainModel):
    label: str
    descriptors: tuple[EditorFieldDescriptor, ...]

    @model_validator(mode="after")
    def unique_fields(self) -> AudioEffectDefinition:
        ids = [item.id for item in self.descriptors]
        if len(ids) != len(set(ids)):
            raise ValueError("Audio effect field identifiers must be unique")
        return self


def _number_field(
    field_id: str,
    label: str,
    default: float,
    minimum: float,
    maximum: float,
    step: float,
    unit: str,
) -> EditorFieldDescriptor:
    return EditorFieldDescriptor(
        id=field_id,
        label=label,
        description="",
        group="音频效果",
        kind="number",
        control="slider",
        default=default,
        unit=unit or None,
        constraints=EditorFieldConstraints(
            minimum=minimum,
            maximum=maximum,
            step=step,
        ),
        options_source=None,
        timeline="none",
    )


def _choice_field(
    field_id: str,
    label: str,
    default: str,
    *,
    choices: tuple[str, ...] = (),
    options_source: str | None = None,
) -> EditorFieldDescriptor:
    return EditorFieldDescriptor(
        id=field_id,
        label=label,
        description="",
        group="音频效果",
        kind="choice",
        control="select",
        default=default,
        unit=None,
        constraints=EditorFieldConstraints(
            choices=[EditorFieldChoice(value=value, label=value) for value in choices],
        ),
        options_source=options_source,
        timeline="none",
    )


AUDIO_EFFECT_DEFINITIONS: dict[AudioEffectKind, AudioEffectDefinition] = {
    AudioEffectKind.PARAMETRIC_EQ: AudioEffectDefinition(
        label="参数均衡器",
        descriptors=tuple(
            _number_field(field_id, label, 0.0, -24.0, 24.0, 0.5, "dB")
            for field_id, label in (
                ("low_db", "低频增益"),
                ("low_mid_db", "中低频增益"),
                ("high_mid_db", "中高频增益"),
                ("high_db", "高频增益"),
            )
        ),
    ),
    AudioEffectKind.HIGH_PASS: AudioEffectDefinition(
        label="高通",
        descriptors=(_number_field("frequency_hz", "截止频率", 80.0, 20.0, 20_000.0, 10.0, "Hz"),),
    ),
    AudioEffectKind.LOW_PASS: AudioEffectDefinition(
        label="低通",
        descriptors=(_number_field("frequency_hz", "截止频率", 16_000.0, 20.0, 24_000.0, 10.0, "Hz"),),
    ),
    AudioEffectKind.COMPRESSOR: AudioEffectDefinition(
        label="压缩器",
        descriptors=(
            _number_field("threshold_db", "阈值", -18.0, -60.0, 0.0, 0.5, "dB"),
            _number_field("ratio", "压缩比", 3.0, 1.0, 20.0, 0.1, ":1"),
            _number_field("attack_ms", "启动时间", 10.0, 0.1, 2_000.0, 1.0, "ms"),
            _number_field("release_ms", "释放时间", 120.0, 10.0, 5_000.0, 5.0, "ms"),
        ),
    ),
    AudioEffectKind.LIMITER: AudioEffectDefinition(
        label="限制器",
        descriptors=(_number_field("ceiling_db", "上限", -1.0, -20.0, 0.0, 0.1, "dB"),),
    ),
    AudioEffectKind.NOISE_GATE: AudioEffectDefinition(
        label="噪声门",
        descriptors=(_number_field("threshold_db", "阈值", -45.0, -80.0, 0.0, 0.5, "dB"),),
    ),
    AudioEffectKind.RNNOISE: AudioEffectDefinition(
        label="RNNoise",
        descriptors=(_number_field("mix", "混合", 1.0, 0.0, 1.0, 0.05, ""),),
    ),
    AudioEffectKind.CHANNEL_MAP: AudioEffectDefinition(
        label="声道映射",
        descriptors=(_choice_field("layout", "声道布局", "stereo", choices=("mono", "stereo", "5.1")),),
    ),
    AudioEffectKind.LOUDNESS_NORMALIZE: AudioEffectDefinition(
        label="响度标准化",
        descriptors=(
            _number_field("target_lufs", "目标响度", -14.0, -30.0, -5.0, 0.5, "LUFS"),
            _number_field("true_peak_db", "True Peak 上限", -1.0, -9.0, 0.0, 0.1, "dBTP"),
        ),
    ),
    AudioEffectKind.DUCKING: AudioEffectDefinition(
        label="自动闪避",
        descriptors=(
            _choice_field("driver_bus_id", "驱动总线", "", options_source="audio-buses"),
            _number_field("threshold_db", "阈值", -24.0, -60.0, 0.0, 0.5, "dB"),
            _number_field("reduction_db", "衰减量", -10.0, -40.0, 0.0, 0.5, "dB"),
            _number_field("attack_ms", "启动时间", 120.0, 0.0, 2_000.0, 5.0, "ms"),
            _number_field("release_ms", "释放时间", 300.0, 0.0, 5_000.0, 5.0, "ms"),
        ),
    ),
}


def audio_effect_definition(kind: AudioEffectKind) -> AudioEffectDefinition:
    return AUDIO_EFFECT_DEFINITIONS[kind]


class AudioEffect(DomainModel):
    id: str = Field(default_factory=new_id)
    bus_id: str
    kind: AudioEffectKind
    position: int
    enabled: bool = True
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_parameters(self) -> AudioEffect:
        descriptors = {
            item.id: item for item in AUDIO_EFFECT_DEFINITIONS[self.kind].descriptors
        }
        unknown = set(self.parameters) - set(descriptors)
        if unknown:
            raise ValueError(f"Unknown audio effect parameters: {sorted(unknown)}")
        validated: dict[str, Any] = {}
        for field_id, descriptor in descriptors.items():
            value = self.parameters.get(field_id, descriptor.default)
            descriptor.validate_value(value)
            validated[field_id] = value
        object.__setattr__(self, "parameters", validated)
        return self
