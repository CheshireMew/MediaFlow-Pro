from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from mediaflow.domain.audio import AUDIO_EFFECT_DEFINITIONS
from mediaflow.domain.audio_effect_presets import audio_effect_preset_ids
from mediaflow.domain.effect_registry import TRANSITION_CAPABILITIES, transition_is_available
from mediaflow.domain.enums import AudioEffectKind, ColorMode


def transition_options(color_mode: ColorMode) -> list[dict[str, object]]:
    return [
        {
            "label": QCoreApplication.translate("TransitionCatalog", capability.label_key),
            "value": kind.value,
            "category": capability.category,
            "description": QCoreApplication.translate("TransitionCatalog", capability.description_key),
            "previewDirection": capability.preview_direction,
            "defaultDurationFrames": capability.default_duration_frames,
            "minimumBitDepth": capability.minimum_bit_depth,
            "hdr10Verified": capability.hdr10_verified,
        }
        for kind, capability in TRANSITION_CAPABILITIES.items()
        if transition_is_available(kind, color_mode)
    ]


def audio_effect_label(kind: AudioEffectKind) -> str:
    return QCoreApplication.translate(
        "AudioCatalog",
        AUDIO_EFFECT_DEFINITIONS[kind].label,
    )


def audio_preset_options(kind: AudioEffectKind) -> list[dict[str, str]]:
    labels = {
        "default": QCoreApplication.translate("AudioCatalog", "默认"),
        "dialogue": QCoreApplication.translate("AudioCatalog", "对白"),
        "gentle": QCoreApplication.translate("AudioCatalog", "轻柔"),
        "strong": QCoreApplication.translate("AudioCatalog", "强力"),
        "social": QCoreApplication.translate("AudioCatalog", "社交平台"),
        "web": QCoreApplication.translate("AudioCatalog", "网络视频"),
        "broadcast": QCoreApplication.translate("AudioCatalog", "广播"),
        "mono": QCoreApplication.translate("AudioCatalog", "单声道"),
        "stereo": QCoreApplication.translate("AudioCatalog", "立体声"),
        "5.1": "5.1",
    }
    return [
        {"presetId": preset_id, "label": labels[preset_id]} for preset_id in audio_effect_preset_ids(kind)
    ]
