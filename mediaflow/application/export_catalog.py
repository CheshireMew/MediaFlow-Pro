from __future__ import annotations

from dataclasses import dataclass

from mediaflow.domain.enums import ColorMode, ExportFormat
from mediaflow.domain.exports import ExportPreset


@dataclass(frozen=True, slots=True)
class ExportVariant:
    id: str
    format: ExportFormat
    suffix: str
    container: str
    video_codec: str | None
    audio_codec: str
    quality_value: float
    preset: str = "medium"
    prores_profile: int | None = None

    def pixel_format(self, color_mode: ColorMode) -> str | None:
        if self.format == ExportFormat.AUDIO:
            return None
        if self.format == ExportFormat.PRORES:
            return "yuva444p10le" if self.prores_profile == 4 else "yuv422p10le"
        if color_mode == ColorMode.HDR10_BT2020_PQ:
            return "yuv420p10le"
        return "yuv420p"

    def to_preset(self, color_mode: ColorMode, fps: float) -> ExportPreset:
        if color_mode == ColorMode.HDR10_BT2020_PQ and self.format == ExportFormat.H264:
            raise ValueError("HDR10 序列不能导出 H.264，请选择 HEVC、AV1 或 ProRes")
        advanced = {"profile": self.prores_profile} if self.prores_profile is not None else {}
        return ExportPreset(
            name=f"{self.format.value.upper()} 高质量",
            format=self.format,
            container=self.container,
            video_codec=self.video_codec,
            audio_codec=self.audio_codec,
            pixel_format=self.pixel_format(color_mode),
            quality_value=self.quality_value,
            preset=self.preset,
            gop_frames=max(1, round(fps * 2)),
            advanced=advanced,
        )


EXPORT_VARIANTS: tuple[ExportVariant, ...] = (
    ExportVariant("h264", ExportFormat.H264, "mp4", "mp4", "libx264", "aac", 18.0),
    ExportVariant("hevc", ExportFormat.HEVC, "mp4", "mp4", "libx265", "aac", 20.0),
    ExportVariant("av1", ExportFormat.AV1, "mkv", "mkv", "libsvtav1", "aac", 24.0, "8"),
    ExportVariant(
        "prores_proxy", ExportFormat.PRORES, "mov", "mov", "prores_ks", "aac", 0.0, prores_profile=0
    ),
    ExportVariant("prores_lt", ExportFormat.PRORES, "mov", "mov", "prores_ks", "aac", 0.0, prores_profile=1),
    ExportVariant(
        "prores_standard",
        ExportFormat.PRORES,
        "mov",
        "mov",
        "prores_ks",
        "aac",
        0.0,
        prores_profile=2,
    ),
    ExportVariant("prores_hq", ExportFormat.PRORES, "mov", "mov", "prores_ks", "aac", 0.0, prores_profile=3),
    ExportVariant(
        "prores_4444", ExportFormat.PRORES, "mov", "mov", "prores_ks", "aac", 0.0, prores_profile=4
    ),
    ExportVariant("audio_aac", ExportFormat.AUDIO, "m4a", "ipod", None, "aac", 0.0),
    ExportVariant("audio_opus", ExportFormat.AUDIO, "ogg", "ogg", None, "libopus", 0.0),
    ExportVariant("audio_pcm", ExportFormat.AUDIO, "wav", "wav", None, "pcm_s24le", 0.0),
    ExportVariant("audio_flac", ExportFormat.AUDIO, "flac", "flac", None, "flac", 0.0),
)

_DEFAULT_VARIANTS = {
    ExportFormat.H264: "h264",
    ExportFormat.HEVC: "hevc",
    ExportFormat.AV1: "av1",
    ExportFormat.PRORES: "prores_hq",
    ExportFormat.AUDIO: "audio_flac",
}


def available_export_variants(color_mode: ColorMode) -> tuple[ExportVariant, ...]:
    return tuple(
        variant
        for variant in EXPORT_VARIANTS
        if color_mode != ColorMode.HDR10_BT2020_PQ or variant.format != ExportFormat.H264
    )


def default_export_preset(
    export_format: ExportFormat,
    color_mode: ColorMode,
    fps: float,
) -> ExportPreset:
    variant_id = _DEFAULT_VARIANTS[export_format]
    variant = next(item for item in EXPORT_VARIANTS if item.id == variant_id)
    return variant.to_preset(color_mode, fps)
