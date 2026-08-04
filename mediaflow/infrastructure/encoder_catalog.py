from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mediaflow.domain.enums import ExportFormat

EncoderBackend = Literal["nvenc", "qsv", "amf", "videotoolbox", "vaapi"]


@dataclass(frozen=True, slots=True)
class VideoEncoderSpec:
    label_key: str
    format: ExportFormat
    backend: EncoderBackend | Literal["software"]

    @property
    def hardware(self) -> bool:
        return self.backend != "software"


VIDEO_ENCODERS: dict[str, VideoEncoderSpec] = {
    "libx264": VideoEncoderSpec("h264_software", ExportFormat.H264, "software"),
    "h264_nvenc": VideoEncoderSpec("h264_nvidia", ExportFormat.H264, "nvenc"),
    "h264_qsv": VideoEncoderSpec("h264_intel_qsv", ExportFormat.H264, "qsv"),
    "h264_amf": VideoEncoderSpec("h264_amd_amf", ExportFormat.H264, "amf"),
    "h264_videotoolbox": VideoEncoderSpec(
        "h264_apple_videotoolbox",
        ExportFormat.H264,
        "videotoolbox",
    ),
    "h264_vaapi": VideoEncoderSpec("h264_linux_vaapi", ExportFormat.H264, "vaapi"),
    "libx265": VideoEncoderSpec("hevc_software", ExportFormat.HEVC, "software"),
    "hevc_nvenc": VideoEncoderSpec("hevc_nvidia", ExportFormat.HEVC, "nvenc"),
    "hevc_qsv": VideoEncoderSpec("hevc_intel_qsv", ExportFormat.HEVC, "qsv"),
    "hevc_amf": VideoEncoderSpec("hevc_amd_amf", ExportFormat.HEVC, "amf"),
    "hevc_videotoolbox": VideoEncoderSpec(
        "hevc_apple_videotoolbox",
        ExportFormat.HEVC,
        "videotoolbox",
    ),
    "hevc_vaapi": VideoEncoderSpec("hevc_linux_vaapi", ExportFormat.HEVC, "vaapi"),
    "libsvtav1": VideoEncoderSpec("av1_svt_software", ExportFormat.AV1, "software"),
    "av1_nvenc": VideoEncoderSpec("av1_nvidia", ExportFormat.AV1, "nvenc"),
    "av1_qsv": VideoEncoderSpec("av1_intel_qsv", ExportFormat.AV1, "qsv"),
    "av1_amf": VideoEncoderSpec("av1_amd_amf", ExportFormat.AV1, "amf"),
    "av1_videotoolbox": VideoEncoderSpec(
        "av1_apple_videotoolbox",
        ExportFormat.AV1,
        "videotoolbox",
    ),
    "av1_vaapi": VideoEncoderSpec("av1_linux_vaapi", ExportFormat.AV1, "vaapi"),
    "prores_ks": VideoEncoderSpec("prores_software", ExportFormat.PRORES, "software"),
}


@dataclass(frozen=True, slots=True)
class SoftwareEncoderFallback:
    codec: str
    preset: str


SOFTWARE_ENCODER_FALLBACKS: dict[ExportFormat, SoftwareEncoderFallback] = {
    ExportFormat.H264: SoftwareEncoderFallback("libx264", "medium"),
    ExportFormat.HEVC: SoftwareEncoderFallback("libx265", "medium"),
    ExportFormat.AV1: SoftwareEncoderFallback("libsvtav1", "8"),
    ExportFormat.PRORES: SoftwareEncoderFallback("prores_ks", "medium"),
}


def is_hardware_codec(codec: str | None) -> bool:
    spec = VIDEO_ENCODERS.get(str(codec or "").strip())
    return bool(spec and spec.hardware)


def codec_backend(
    codec: str | None,
) -> EncoderBackend | Literal["software"] | None:
    spec = VIDEO_ENCODERS.get(str(codec or "").strip())
    return spec.backend if spec else None


def software_encoder_for_format(format: ExportFormat) -> SoftwareEncoderFallback | None:
    return SOFTWARE_ENCODER_FALLBACKS.get(format)
