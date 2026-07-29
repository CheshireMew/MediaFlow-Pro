from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from mediaflow.domain.enums import AssetKind, ColorMode
from mediaflow.domain.project import MediaMetadata, ProjectProfile
from mediaflow.domain.storage_names import require_windows_interop_path
from mediaflow.domain.timebase import seconds_to_frames

from .runtime_paths import RuntimePaths


@dataclass(frozen=True, slots=True)
class ProbeResult:
    kind: AssetKind
    metadata: MediaMetadata
    suggested_profile: ProjectProfile | None


class MediaProbe:
    def __init__(self, paths: RuntimePaths | None = None):
        self.paths = paths or RuntimePaths.discover()

    def probe(self, path: str | Path, *, timeline_profile: ProjectProfile | None = None) -> ProbeResult:
        source = require_windows_interop_path(
            Path(path).resolve(strict=True)
        )
        result = subprocess.run(
            [
                str(self.paths.ffprobe),
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(source),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed for {source}: {result.stderr.strip()}")
        payload = json.loads(result.stdout)
        streams = payload.get("streams") or []
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        subtitle = next(
            (stream for stream in streams if stream.get("codec_type") == "subtitle"),
            None,
        )
        if source.suffix.lower() in {".srt", ".ass", ".ssa", ".vtt"} or subtitle:
            kind = AssetKind.SUBTITLE
        elif source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
            kind = AssetKind.IMAGE
        elif video:
            kind = AssetKind.VIDEO
        elif audio:
            kind = AssetKind.AUDIO
        else:
            raise RuntimeError(f"ffprobe found no supported media stream in {source}")

        native_rate = self._rate(video.get("avg_frame_rate")) if video else None
        real_rate = self._rate(video.get("r_frame_rate")) if video else None
        duration = self._duration(payload, video, audio)
        suggested_profile = (
            self._profile_from_video(video, native_rate) if video and kind == AssetKind.VIDEO else None
        )
        profile = timeline_profile or suggested_profile
        duration_frames = (
            seconds_to_frames(duration, profile.fps_numerator, profile.fps_denominator)
            if profile and duration > 0
            else 0
        )
        format_info = payload.get("format") or {}
        metadata = MediaMetadata(
            duration_frames=duration_frames,
            width=int(video["width"]) if video and video.get("width") else None,
            height=int(video["height"]) if video and video.get("height") else None,
            fps_numerator=native_rate.numerator if native_rate else None,
            fps_denominator=native_rate.denominator if native_rate else None,
            bitrate=self._optional_int(format_info.get("bit_rate")),
            video_codec=video.get("codec_name") if video else None,
            audio_codec=audio.get("codec_name") if audio else None,
            pixel_format=video.get("pix_fmt") if video else None,
            color_primaries=video.get("color_primaries") if video else None,
            color_transfer=video.get("color_transfer") if video else None,
            color_space=video.get("color_space") if video else None,
            variable_frame_rate=bool(native_rate and real_rate and native_rate != real_rate),
            has_video=video is not None,
            has_audio=audio is not None,
        )
        return ProbeResult(kind=kind, metadata=metadata, suggested_profile=suggested_profile)

    @staticmethod
    def _rate(value: str | None) -> Fraction | None:
        if not value or value in {"0/0", "N/A"}:
            return None
        try:
            rate = Fraction(value)
        except (ValueError, ZeroDivisionError):
            return None
        return rate if rate > 0 else None

    @staticmethod
    def _duration(payload: dict, video: dict | None, audio: dict | None) -> Fraction:
        candidates = [
            (payload.get("format") or {}).get("duration"),
            video.get("duration") if video else None,
            audio.get("duration") if audio else None,
        ]
        for value in candidates:
            if value and value != "N/A":
                try:
                    return Fraction(str(value))
                except ValueError:
                    continue
        return Fraction(0)

    @classmethod
    def _profile_from_video(cls, video: dict, rate: Fraction | None) -> ProjectProfile:
        pixel_format = str(video.get("pix_fmt") or "")
        hdr = video.get("color_primaries") == "bt2020" and video.get("color_transfer") in {
            "smpte2084",
            "arib-std-b67",
        }
        bit_depth = 10 if hdr or any(token in pixel_format for token in ("10", "12", "p010")) else 8
        fps = rate or Fraction(30, 1)
        audio_channels = 2
        return ProjectProfile(
            width=int(video.get("width") or 1920),
            height=int(video.get("height") or 1080),
            fps_numerator=fps.numerator,
            fps_denominator=fps.denominator,
            color_mode=ColorMode.HDR10_BT2020_PQ if hdr else ColorMode.SDR_BT709,
            bit_depth=max(10, bit_depth) if hdr else bit_depth,
            audio_channels=audio_channels,
        )

    @staticmethod
    def _optional_int(value: object) -> int | None:
        try:
            if not isinstance(value, (str, bytes, bytearray, int, float)):
                return None
            return int(value)
        except (TypeError, ValueError):
            return None
