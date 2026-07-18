from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

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


class ParametricEqParameters(DomainModel):
    low_db: float = Field(default=0.0, ge=-24.0, le=24.0, json_schema_extra={"step": 0.5, "unit": "dB"})
    low_mid_db: float = Field(default=0.0, ge=-24.0, le=24.0, json_schema_extra={"step": 0.5, "unit": "dB"})
    high_mid_db: float = Field(default=0.0, ge=-24.0, le=24.0, json_schema_extra={"step": 0.5, "unit": "dB"})
    high_db: float = Field(default=0.0, ge=-24.0, le=24.0, json_schema_extra={"step": 0.5, "unit": "dB"})


class HighPassParameters(DomainModel):
    frequency_hz: float = Field(
        default=80.0, ge=20.0, le=20_000.0, json_schema_extra={"step": 10.0, "unit": "Hz"}
    )


class LowPassParameters(DomainModel):
    frequency_hz: float = Field(
        default=16_000.0,
        ge=20.0,
        le=24_000.0,
        json_schema_extra={"step": 10.0, "unit": "Hz"},
    )


class CompressorParameters(DomainModel):
    threshold_db: float = Field(
        default=-18.0, ge=-60.0, le=0.0, json_schema_extra={"step": 0.5, "unit": "dB"}
    )
    ratio: float = Field(default=3.0, ge=1.0, le=20.0, json_schema_extra={"step": 0.1, "unit": ":1"})
    attack_ms: float = Field(default=10.0, ge=0.1, le=2_000.0, json_schema_extra={"step": 1.0, "unit": "ms"})
    release_ms: float = Field(
        default=120.0,
        ge=10.0,
        le=5_000.0,
        json_schema_extra={"step": 5.0, "unit": "ms"},
    )


class LimiterParameters(DomainModel):
    ceiling_db: float = Field(default=-1.0, ge=-20.0, le=0.0, json_schema_extra={"step": 0.1, "unit": "dB"})


class NoiseGateParameters(DomainModel):
    threshold_db: float = Field(
        default=-45.0, ge=-80.0, le=0.0, json_schema_extra={"step": 0.5, "unit": "dB"}
    )


class RnnoiseParameters(DomainModel):
    mix: float = Field(default=1.0, ge=0.0, le=1.0, json_schema_extra={"step": 0.05, "unit": ""})


class ChannelMapParameters(DomainModel):
    layout: Literal["mono", "stereo", "5.1"] = Field(
        default="stereo",
        json_schema_extra={"step": 0.0, "unit": "", "value_type": "layout"},
    )


class LoudnessNormalizeParameters(DomainModel):
    target_lufs: float = Field(
        default=-14.0, ge=-30.0, le=-5.0, json_schema_extra={"step": 0.5, "unit": "LUFS"}
    )
    true_peak_db: float = Field(
        default=-1.0, ge=-9.0, le=0.0, json_schema_extra={"step": 0.1, "unit": "dBTP"}
    )


class DuckingParameters(DomainModel):
    driver_bus_id: str = Field(
        default="",
        json_schema_extra={"step": 0.0, "unit": "", "value_type": "bus"},
    )
    threshold_db: float = Field(
        default=-24.0, ge=-60.0, le=0.0, json_schema_extra={"step": 0.5, "unit": "dB"}
    )
    reduction_db: float = Field(
        default=-10.0, ge=-40.0, le=0.0, json_schema_extra={"step": 0.5, "unit": "dB"}
    )
    attack_ms: float = Field(default=120.0, ge=0.0, le=2_000.0, json_schema_extra={"step": 5.0, "unit": "ms"})
    release_ms: float = Field(
        default=300.0, ge=0.0, le=5_000.0, json_schema_extra={"step": 5.0, "unit": "ms"}
    )


_AUDIO_EFFECT_PARAMETER_TYPES: dict[AudioEffectKind, type[DomainModel]] = {
    AudioEffectKind.PARAMETRIC_EQ: ParametricEqParameters,
    AudioEffectKind.HIGH_PASS: HighPassParameters,
    AudioEffectKind.LOW_PASS: LowPassParameters,
    AudioEffectKind.COMPRESSOR: CompressorParameters,
    AudioEffectKind.LIMITER: LimiterParameters,
    AudioEffectKind.NOISE_GATE: NoiseGateParameters,
    AudioEffectKind.RNNOISE: RnnoiseParameters,
    AudioEffectKind.CHANNEL_MAP: ChannelMapParameters,
    AudioEffectKind.LOUDNESS_NORMALIZE: LoudnessNormalizeParameters,
    AudioEffectKind.DUCKING: DuckingParameters,
}


def audio_effect_parameter_schema(kind: AudioEffectKind) -> dict[str, dict[str, Any]]:
    return _AUDIO_EFFECT_PARAMETER_TYPES[kind].model_json_schema()["properties"]


class AudioEffect(DomainModel):
    id: str = Field(default_factory=new_id)
    bus_id: str
    kind: AudioEffectKind
    position: int
    enabled: bool = True
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_parameters(self) -> AudioEffect:
        parameter_type = _AUDIO_EFFECT_PARAMETER_TYPES[self.kind]
        validated = parameter_type.model_validate(self.parameters)
        object.__setattr__(self, "parameters", validated.model_dump())
        return self
