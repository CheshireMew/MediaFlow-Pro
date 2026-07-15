from __future__ import annotations

from copy import deepcopy

from mediaflow.domain.enums import AudioEffectKind

_PRESETS: dict[AudioEffectKind, dict[str, dict]] = {
    AudioEffectKind.PARAMETRIC_EQ: {
        "default": {},
        "dialogue": {"low_db": -2.0, "low_mid_db": -1.0, "high_mid_db": 2.5, "high_db": 1.0},
    },
    AudioEffectKind.HIGH_PASS: {
        "default": {},
        "gentle": {"frequency_hz": 60.0},
        "dialogue": {"frequency_hz": 100.0},
    },
    AudioEffectKind.LOW_PASS: {
        "default": {},
        "dialogue": {"frequency_hz": 14_000.0},
    },
    AudioEffectKind.COMPRESSOR: {
        "default": {},
        "dialogue": {
            "threshold_db": -20.0,
            "ratio": 3.5,
            "attack_ms": 8.0,
            "release_ms": 100.0,
        },
        "strong": {
            "threshold_db": -24.0,
            "ratio": 6.0,
            "attack_ms": 5.0,
            "release_ms": 160.0,
        },
    },
    AudioEffectKind.LIMITER: {"default": {}, "social": {"ceiling_db": -1.0}},
    AudioEffectKind.NOISE_GATE: {
        "default": {},
        "gentle": {"threshold_db": -52.0},
        "strong": {"threshold_db": -36.0},
    },
    AudioEffectKind.RNNOISE: {
        "default": {},
        "gentle": {"mix": 0.65},
        "strong": {"mix": 1.0},
    },
    AudioEffectKind.CHANNEL_MAP: {
        "mono": {"layout": "mono"},
        "stereo": {"layout": "stereo"},
        "5.1": {"layout": "5.1"},
    },
    AudioEffectKind.LOUDNESS_NORMALIZE: {
        "social": {"target_lufs": -14.0, "true_peak_db": -1.0},
        "web": {"target_lufs": -16.0, "true_peak_db": -1.0},
        "broadcast": {"target_lufs": -23.0, "true_peak_db": -1.0},
    },
    AudioEffectKind.DUCKING: {
        "default": {},
        "gentle": {"threshold_db": -26.0, "reduction_db": -6.0},
        "strong": {"threshold_db": -22.0, "reduction_db": -14.0},
    },
}


def audio_effect_preset_ids(kind: AudioEffectKind) -> tuple[str, ...]:
    return tuple(_PRESETS[kind])


def audio_effect_preset(kind: AudioEffectKind, preset_id: str) -> dict:
    try:
        return deepcopy(_PRESETS[kind][preset_id])
    except KeyError as error:
        raise KeyError(f"Unknown preset {preset_id!r} for {kind.value}") from error
