from __future__ import annotations

import re
from pathlib import Path

from mediaflow.domain.enums import ColorMode, ExportFormat
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.encoder_catalog import codec_backend
from mediaflow.infrastructure.encoder_policy import VideoEncoderPolicyResolver
from mediaflow.infrastructure.file_fingerprint import fingerprint_file
from mediaflow.infrastructure.runtime_paths import RuntimePaths

from .export_types import RuntimeExportPreset


class MltExportEncoder:
    def __init__(
        self,
        paths: RuntimePaths,
        resolver: VideoEncoderPolicyResolver,
    ):
        self.paths = paths
        self.resolver = resolver

    def resolve_preset(self, preset: ExportPreset) -> RuntimeExportPreset:
        if preset.format == ExportFormat.AUDIO:
            codec = None
        else:
            policy = preset.encoder_policy
            if policy is None:
                raise ValueError("Video export requires a video encoder policy")
            codec = self.resolver.resolve(preset.format, policy).codec
        return RuntimeExportPreset.model_validate({**preset.model_dump(mode="python"), "video_codec": codec})

    def execution_identity(self, preset: ExportPreset) -> dict[str, object]:
        runtime_preset = self.resolve_preset(preset)

        def binary(path: Path | None) -> dict[str, object] | None:
            if path is None or not path.is_file():
                return None
            value = fingerprint_file(path)
            return {
                "name": path.name,
                "size": value.size,
                "modified_ns": value.modified_ns,
                "edge_sha256": value.edge_sha256,
            }

        return {
            "target": self.paths.target.key,
            "video_codec": runtime_preset.video_codec,
            "melt": binary(self.paths.melt),
            "ffmpeg": binary(self.paths.ffmpeg),
            "ffprobe": binary(self.paths.ffprobe),
        }

    @staticmethod
    def consumer_properties(
        state: TimelineState,
        preset: RuntimeExportPreset,
    ) -> list[str]:
        profile = state.sequence.profile
        values = [f"f={preset.container}"]
        codec = preset.video_codec or ""
        encoder_backend = codec_backend(codec)
        hardware_codec = encoder_backend not in {None, "software"}
        if preset.format == ExportFormat.H264:
            values.append(f"vcodec={codec or 'libx264'}")
        elif preset.format == ExportFormat.HEVC:
            values.append(f"vcodec={codec or 'libx265'}")
        elif preset.format == ExportFormat.AV1:
            values.append(f"vcodec={codec or 'libsvtav1'}")
        elif preset.format == ExportFormat.PRORES:
            values.extend(
                [
                    f"vcodec={preset.video_codec or 'prores_ks'}",
                    f"profile:v={preset.advanced.get('profile', 3)}",
                ]
            )
        elif preset.format == ExportFormat.AUDIO:
            values.append("vn=1")
        if preset.format in {ExportFormat.H264, ExportFormat.HEVC, ExportFormat.AV1}:
            if hardware_codec:
                if encoder_backend == "nvenc":
                    values.extend(["rc=vbr", f"cq={preset.quality_value:g}"])
                elif encoder_backend == "qsv":
                    values.append(f"global_quality={preset.quality_value:g}")
                elif encoder_backend == "amf":
                    values.append(f"qp_i={preset.quality_value:g}")
                elif encoder_backend == "vaapi":
                    values.extend([f"qp={preset.quality_value:g}", "vf=format=nv12,hwupload"])
                else:
                    values.append(f"q:v={preset.quality_value:g}")
            else:
                values.append(f"crf={preset.quality_value:g}")
            if encoder_backend in {None, "software", "nvenc", "qsv"}:
                values.append(f"preset={preset.preset}")
        if preset.pixel_format:
            values.append(f"pix_fmt={preset.pixel_format}")
        if preset.audio_codec:
            values.extend([f"acodec={preset.audio_codec}", f"ab={preset.audio_bitrate}"])
        else:
            values.append("an=1")
        values.append(f"g={preset.gop_frames}")
        if preset.advanced.get("profile") is not None and preset.format != ExportFormat.PRORES:
            values.append(f"profile={preset.advanced['profile']}")
        if preset.advanced.get("level"):
            values.append(f"level={preset.advanced['level']}")
        if preset.advanced.get("max_bitrate"):
            values.append(f"maxrate={int(preset.advanced['max_bitrate'])}")
        if preset.advanced.get("target_bitrate"):
            values.append(f"vb={int(preset.advanced['target_bitrate'])}")
        if preset.advanced.get("audio_sample_rate"):
            values.append(f"ar={int(preset.advanced['audio_sample_rate'])}")
        if preset.advanced.get("audio_channels"):
            values.append(f"ac={int(preset.advanced['audio_channels'])}")
        if preset.advanced.get("scaling_method"):
            values.append(f"sws_flags={preset.advanced['scaling_method']}")
        output_width = int(preset.advanced.get("width", profile.width))
        output_height = int(preset.advanced.get("height", profile.height))
        output_fps_numerator = int(preset.advanced.get("fps_numerator", profile.fps_numerator))
        output_fps_denominator = int(preset.advanced.get("fps_denominator", profile.fps_denominator))
        if (
            profile.color_mode == ColorMode.HDR10_BT2020_PQ
            or "width" in preset.advanced
            or "height" in preset.advanced
            or "fps_numerator" in preset.advanced
            or "fps_denominator" in preset.advanced
        ):
            values.extend(
                [
                    f"s={output_width}x{output_height}",
                    f"r={output_fps_numerator}/{output_fps_denominator}",
                ]
            )
        if profile.color_mode == ColorMode.HDR10_BT2020_PQ:
            if preset.format not in {ExportFormat.HEVC, ExportFormat.AV1, ExportFormat.PRORES}:
                raise ValueError("HDR10 export requires HEVC, AV1, or ProRes")
            values.extend(
                [
                    "color_primaries=bt2020",
                    "color_trc=smpte2084",
                    "colorspace=bt2020nc",
                ]
            )
            master_display = preset.advanced.get(
                "master_display",
                "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)",
            )
            max_cll = preset.advanced.get("max_cll", "1000,400")
            if preset.format == ExportFormat.HEVC and (preset.video_codec or "libx265") == "libx265":
                values.append(
                    "x265-params="
                    f"hdr10=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:"
                    f"colormatrix=bt2020nc:master-display={master_display}:max-cll={max_cll}"
                )
            elif preset.format == ExportFormat.AV1 and (preset.video_codec or "libsvtav1") == "libsvtav1":
                values.append("svtav1-params=enable-hdr=1")
        return values


def diagnostic_tail(diagnostic: str, limit: int = 4000) -> str:
    normalized = diagnostic.strip()
    return normalized[-limit:] if len(normalized) > limit else normalized


def hardware_failure_reason(codec: str | None, diagnostic: str) -> str | None:
    backend = codec_backend(codec)
    if backend not in {"nvenc", "qsv", "amf", "videotoolbox", "vaapi"}:
        return None
    normalized = diagnostic.lower()
    backend_patterns = {
        "nvenc": (
            r"no nvenc capable devices",
            r"cannot load (?:nvcuda|nvencodeapi)",
            r"failed to load (?:nvcuda|nvencodeapi)",
            r"openencodesession",
            r"nvenc.*(?:driver|device|initializ|not available|not supported|unsupported)",
            r"cuda_error",
        ),
        "qsv": (
            r"mfx_err",
            r"(?:qsv|quick sync).*(?:device|session|initializ|not available|not supported|unsupported)",
            r"(?:create|initialize|initializing).*(?:mfx|qsv).*session",
            r"cannot load libmfx",
        ),
        "amf": (
            r"amf_(?:not_supported|no_device|fail)",
            r"(?:amf|amfrt64).*(?:device|initializ|not available|not supported|unsupported|failed)",
        ),
        "videotoolbox": (
            r"videotoolbox.*(?:not available|not supported|failed|error)",
            r"cannot create.*videotoolbox",
        ),
        "vaapi": (
            r"vaapi.*(?:device|initializ|not available|not supported|failed|error)",
            r"failed to initialise vaapi",
            r"no va display found",
        ),
    }
    generic_encoder_patterns = (
        r"unknown encoder",
        r"encoder .* not found",
        r"error (?:initializing|while opening).*encoder",
        r"failed to (?:open|initialize|initialise|create).*encoder",
        r"(?:could not|unable to) open (?:video )?(?:encoder|codec)",
        r"avcodec_open2 failed",
        r"invalid (?:value .* for option )?preset",
        r"unable to parse option value.*preset",
    )
    if not (
        any(re.search(pattern, normalized) for pattern in backend_patterns[backend])
        or any(re.search(pattern, normalized) for pattern in generic_encoder_patterns)
    ):
        return None
    return {
        "nvenc": "NVIDIA NVENC 硬件编码器无法初始化",
        "qsv": "Intel Quick Sync 硬件编码器无法初始化",
        "amf": "AMD AMF 硬件编码器无法初始化",
        "videotoolbox": "Apple VideoToolbox 硬件编码器无法初始化",
        "vaapi": "Linux VAAPI 硬件编码器无法初始化",
    }[backend]
