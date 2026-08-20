from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from mediaflow.application.export_catalog import available_export_variants
from mediaflow.domain.enums import ColorMode

_EXPORT_LABELS = {
    "h264": "H.264",
    "hevc": "HEVC",
    "av1": "AV1",
    "prores_proxy": "ProRes Proxy",
    "prores_lt": "ProRes LT",
    "prores_standard": "ProRes Standard",
    "prores_hq": "ProRes HQ",
    "prores_4444": "ProRes 4444",
    "audio_aac": "AAC / M4A",
    "audio_opus": "Opus / OGG",
    "audio_pcm": "PCM / WAV",
    "audio_flac": "FLAC",
}

def encoder_label(label_key: str) -> str:
    labels = {
        "h264_software": QCoreApplication.translate("EncoderCatalog", "H.264 软件"),
        "h264_nvidia": "H.264 NVIDIA",
        "h264_intel_qsv": "H.264 Intel QSV",
        "h264_amd_amf": "H.264 AMD AMF",
        "h264_apple_videotoolbox": "H.264 Apple VideoToolbox",
        "h264_linux_vaapi": "H.264 Linux VAAPI",
        "hevc_software": QCoreApplication.translate("EncoderCatalog", "HEVC 软件"),
        "hevc_nvidia": "HEVC NVIDIA",
        "hevc_intel_qsv": "HEVC Intel QSV",
        "hevc_amd_amf": "HEVC AMD AMF",
        "hevc_apple_videotoolbox": "HEVC Apple VideoToolbox",
        "hevc_linux_vaapi": "HEVC Linux VAAPI",
        "av1_svt_software": QCoreApplication.translate("EncoderCatalog", "AV1 SVT 软件"),
        "av1_nvidia": "AV1 NVIDIA",
        "av1_intel_qsv": "AV1 Intel QSV",
        "av1_amd_amf": "AV1 AMD AMF",
        "av1_apple_videotoolbox": "AV1 Apple VideoToolbox",
        "av1_linux_vaapi": "AV1 Linux VAAPI",
        "prores_software": QCoreApplication.translate("EncoderCatalog", "ProRes 软件"),
        "h264_hardware_auto": QCoreApplication.translate("EncoderCatalog", "H.264 硬件优先（自动）"),
        "h264_hardware_nvidia": "H.264 NVIDIA",
        "h264_hardware_intel": "H.264 Intel",
        "h264_hardware_amd": "H.264 AMD",
        "h264_hardware_apple": "H.264 Apple",
        "hevc_hardware_auto": QCoreApplication.translate("EncoderCatalog", "HEVC 硬件优先（自动）"),
        "hevc_hardware_nvidia": "HEVC NVIDIA",
        "hevc_hardware_intel": "HEVC Intel",
        "hevc_hardware_amd": "HEVC AMD",
        "hevc_hardware_apple": "HEVC Apple",
        "av1_hardware_auto": QCoreApplication.translate("EncoderCatalog", "AV1 硬件优先（自动）"),
        "av1_hardware_nvidia": "AV1 NVIDIA",
        "av1_hardware_intel": "AV1 Intel",
        "av1_hardware_amd": "AV1 AMD",
        "av1_hardware_apple": "AV1 Apple",
    }
    return labels[label_key]


def no_subtitle_burn_label() -> str:
    return QCoreApplication.translate("ExportCatalog", "不烧录")

def export_format_options(color_mode: ColorMode) -> list[dict]:
    filters = {
        "mp4": QCoreApplication.translate("ExportCatalog", "MP4 视频 (*.mp4)"),
        "mkv": QCoreApplication.translate("ExportCatalog", "MKV 视频 (*.mkv)"),
        "mov": QCoreApplication.translate("ExportCatalog", "MOV 视频 (*.mov)"),
        "m4a": QCoreApplication.translate("ExportCatalog", "M4A 音频 (*.m4a)"),
        "ogg": QCoreApplication.translate("ExportCatalog", "OGG 音频 (*.ogg)"),
        "wav": QCoreApplication.translate("ExportCatalog", "WAV 音频 (*.wav)"),
        "flac": QCoreApplication.translate("ExportCatalog", "FLAC 音频 (*.flac)"),
    }
    options: list[dict] = []
    for variant in available_export_variants(color_mode):
        label = _EXPORT_LABELS[variant.id]
        if color_mode == ColorMode.HDR10_BT2020_PQ and variant.id == "hevc":
            label = "HEVC Main10"
        elif color_mode == ColorMode.HDR10_BT2020_PQ and variant.id == "av1":
            label = "AV1 10-bit"
        option = {
            "id": variant.id,
            "label": label,
            "value": variant.format.value,
            "suffix": variant.suffix,
            "container": variant.container,
            "encoderPolicy": (
                variant.encoder_policy.model_dump(mode="json") if variant.encoder_policy is not None else None
            ),
            "audioCodec": variant.audio_codec,
            "pixelFormat": variant.pixel_format(color_mode) or "",
            "qualityValue": variant.quality_value,
            "preset": variant.preset,
            "filter": filters[variant.suffix],
        }
        if variant.prores_profile is not None:
            option["profile"] = variant.prores_profile
        options.append(option)
    return options
