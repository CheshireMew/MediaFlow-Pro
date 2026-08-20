from __future__ import annotations

import math
from pathlib import Path

from mediaflow.domain.enums import ColorMode
from mediaflow.domain.project import Asset, ProjectProfile
from mediaflow.infrastructure.storage_budget import (
    PROXY_AUDIO_BITRATE,
    PROXY_VIDEO_MAX_BITRATE,
)


def _source_is_hdr(asset: Asset) -> bool:
    return asset.metadata.color_primaries == "bt2020" and asset.metadata.color_transfer in {
        "smpte2084",
        "arib-std-b67",
    }


def _color_filter(
    profile: ProjectProfile,
    *,
    source_hdr: bool,
    force_sdr: bool,
) -> str:
    if force_sdr or profile.color_mode == ColorMode.SDR_BT709:
        if not source_hdr:
            return ""
        return (
            "zscale=t=linear:npl=100,format=gbrpf32le,"
            "tonemap=tonemap=mobius:param=0.3:desat=2:peak=10,"
            "zscale=p=bt709:t=bt709:m=bt709:r=tv:d=error_diffusion"
        )
    if profile.color_mode == ColorMode.HDR10_BT2020_PQ and not source_hdr:
        return (
            "zscale=pin=bt709:tin=bt709:min=bt709:p=bt2020:"
            "t=smpte2084:m=bt2020nc:r=tv:npl=203:d=error_diffusion"
        )
    return ""


def _video_codec_arguments(
    profile: ProjectProfile,
    *,
    force_sdr: bool,
) -> list[str]:
    if profile.color_mode == ColorMode.HDR10_BT2020_PQ and not force_sdr:
        return [
            "-c:v",
            "libx265",
            "-profile:v",
            "main10",
            "-pix_fmt",
            "yuv420p10le",
            "-color_primaries",
            "bt2020",
            "-color_trc",
            "smpte2084",
            "-colorspace",
            "bt2020nc",
            "-crf",
            "25",
        ]
    arguments = ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if force_sdr:
        arguments.extend(
            [
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-colorspace",
                "bt709",
                "-x264-params",
                "colorprim=bt709:transfer=bt709:colormatrix=bt709",
            ]
        )
    arguments.extend(["-crf", "24"])
    return arguments


def build_proxy_command(
    source: Path,
    destination: Path,
    asset: Asset,
    profile: ProjectProfile,
    *,
    force_sdr: bool = False,
) -> list[str]:
    source_hdr = _source_is_hdr(asset)
    fps = f"{profile.fps_numerator}/{profile.fps_denominator}"
    scale = "scale='if(gt(iw,ih),-2,540)':'if(gt(iw,ih),540,-2)'"
    filters = ",".join(
        item
        for item in (
            _color_filter(profile, source_hdr=source_hdr, force_sdr=force_sdr),
            scale,
            f"fps={fps}",
        )
        if item
    )
    command = ["-n", "-v", "error", "-i", str(source), "-vf", filters]
    command.extend(_video_codec_arguments(profile, force_sdr=force_sdr))
    command.extend(
        [
            "-maxrate",
            str(PROXY_VIDEO_MAX_BITRATE),
            "-bufsize",
            str(PROXY_VIDEO_MAX_BITRATE * 2),
            "-preset",
            "veryfast",
            "-g",
            str(max(1, math.ceil(profile.fps))),
            "-c:a",
            "aac",
            "-b:a",
            str(PROXY_AUDIO_BITRATE),
            str(destination),
        ]
    )
    return command
