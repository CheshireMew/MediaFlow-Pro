from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from mediaflow.application.ports import AssetProcessingDocuments
from mediaflow.domain.enums import ColorMode
from mediaflow.domain.project import (
    Asset,
    ProjectProfile,
)

from .runtime_paths import RuntimePaths
from .subprocess_runner import run_cancellable


@dataclass(frozen=True, slots=True)
class ProxyDecision:
    required: bool
    reasons: tuple[str, ...]


class ProxyService:
    def __init__(
        self,
        repository: AssetProcessingDocuments,
        paths: RuntimePaths | None = None,
    ):
        self.repository = repository
        self.paths = paths or RuntimePaths.discover()

    @staticmethod
    def decision(asset: Asset, *, dropped_frames: int = 0, manual: bool = False) -> ProxyDecision:
        metadata = asset.metadata
        reasons: list[str] = []
        if manual:
            reasons.append("manual")
        if (metadata.width or 0) > 1920 or (metadata.height or 0) > 1080:
            reasons.append("high_resolution")
        if (metadata.bitrate or 0) > 60_000_000:
            reasons.append("high_bitrate")
        if metadata.variable_frame_rate:
            reasons.append("variable_frame_rate")
        if metadata.fps_numerator and metadata.fps_denominator:
            if metadata.fps_numerator / metadata.fps_denominator > 60:
                reasons.append("high_frame_rate")
        if metadata.pixel_format and any(token in metadata.pixel_format for token in ("10", "12", "p010")):
            reasons.append("high_bit_depth")
        if dropped_frames >= 3:
            reasons.append("decoder_drops")
        return ProxyDecision(required=bool(reasons), reasons=tuple(reasons))

    def generate(
        self,
        asset: Asset,
        profile: ProjectProfile,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> Asset:
        source = self.repository.resolve_asset_path(asset)
        if not source.is_file():
            raise FileNotFoundError(source)
        output = self.repository.project_dir / "proxies" / f"{asset.id}.mp4"
        fps = f"{profile.fps_numerator}/{profile.fps_denominator}"
        gop = max(1, math.ceil(profile.fps))
        scale = "scale='if(gt(iw,ih),-2,540)':'if(gt(iw,ih),540,-2)'"
        source_hdr = asset.metadata.color_primaries == "bt2020" and asset.metadata.color_transfer in {
            "smpte2084",
            "arib-std-b67",
        }
        if profile.color_mode == ColorMode.HDR10_BT2020_PQ and not source_hdr:
            color = (
                "zscale=pin=bt709:tin=bt709:min=bt709:p=bt2020:t=smpte2084:"
                "m=bt2020nc:r=tv:npl=203:d=error_diffusion"
            )
        elif profile.color_mode == ColorMode.SDR_BT709 and source_hdr:
            color = (
                "zscale=t=linear:npl=100,format=gbrpf32le,"
                "tonemap=tonemap=mobius:param=0.3:desat=2:peak=10,"
                "zscale=p=bt709:t=bt709:m=bt709:r=tv:d=error_diffusion"
            )
        else:
            color = ""
        filters = ",".join(item for item in (color, scale, f"fps={fps}") if item)
        command = [
            str(self.paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(source),
            "-vf",
            filters,
        ]
        if profile.color_mode == ColorMode.HDR10_BT2020_PQ:
            command.extend(
                [
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
            )
        else:
            command.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "24"])
        command.extend(
            [
                "-preset",
                "veryfast",
                "-g",
                str(gop),
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                str(output),
            ]
        )
        result = run_cancellable(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check_cancelled=check_cancelled,
        )
        if result.returncode != 0 or not output.is_file():
            raise RuntimeError(f"Proxy generation failed: {result.stderr.strip()}")
        sdr_preview_output = None
        if profile.color_mode == ColorMode.HDR10_BT2020_PQ:
            sdr_preview_output = self.repository.project_dir / "proxies" / f"{asset.id}-sdr.mp4"
            sdr_color = (
                "zscale=t=linear:npl=100,format=gbrpf32le,"
                "tonemap=tonemap=mobius:param=0.3:desat=2:peak=10,"
                "zscale=p=bt709:t=bt709:m=bt709:r=tv:d=error_diffusion"
                if source_hdr
                else ""
            )
            sdr_filters = ",".join(item for item in (sdr_color, scale, f"fps={fps}") if item)
            sdr_result = run_cancellable(
                [
                    str(self.paths.ffmpeg),
                    "-y",
                    "-hide_banner",
                    "-v",
                    "error",
                    "-i",
                    str(source),
                    "-vf",
                    sdr_filters,
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-color_primaries",
                    "bt709",
                    "-color_trc",
                    "bt709",
                    "-colorspace",
                    "bt709",
                    "-x264-params",
                    "colorprim=bt709:transfer=bt709:colormatrix=bt709",
                    "-crf",
                    "24",
                    "-preset",
                    "veryfast",
                    "-g",
                    str(gop),
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    str(sdr_preview_output),
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
                check_cancelled=check_cancelled,
            )
            if sdr_result.returncode != 0 or not sdr_preview_output.is_file():
                raise RuntimeError(f"SDR preview proxy generation failed: {sdr_result.stderr.strip()}")
        return self.repository.set_asset_proxy_paths(
            asset.id,
            expected_fingerprint=asset.fingerprint,
            proxy_path=output,
            sdr_preview_proxy_path=sdr_preview_output,
        )
