from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

from mediaflow.domain.enums import ColorMode, ExportFormat
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.ffprobe_runner import FfprobeRunner
from mediaflow.infrastructure.runtime_paths import RuntimePaths

from .export_types import RuntimeExportPreset


class MltExportProbe:
    def __init__(self, paths: RuntimePaths):
        self.paths = paths
        self.ffprobe = FfprobeRunner(self.paths.ffprobe)

    def read(self, output: Path) -> dict:
        result = self.ffprobe.run(
            [
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-read_intervals",
                "%+#1",
                "-show_frames",
                "-of",
                "json",
                str(output),
            ],
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Export verification failed: {result.stderr.strip()}")
        return json.loads(result.stdout)

    def validate(
        self,
        state: TimelineState,
        preset: RuntimeExportPreset,
        probe: dict,
        *,
        expected_duration_frames: int,
    ) -> None:
        streams = probe.get("streams") or []
        video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        if preset.audio_codec and audio is None:
            raise RuntimeError("Export has no audio stream")
        if audio is not None:
            expected_audio_codecs = {
                "aac": {"aac"},
                "libopus": {"opus"},
                "opus": {"opus"},
                "flac": {"flac"},
                "pcm_s16le": {"pcm_s16le"},
                "pcm_s24le": {"pcm_s24le"},
            }
            expected = expected_audio_codecs.get(preset.audio_codec or "")
            if expected and audio.get("codec_name") not in expected:
                raise RuntimeError(
                    "Export audio codec does not match the preset: "
                    f"expected {sorted(expected)}, got {audio.get('codec_name')}"
                )
            expected_channels = preset.advanced.get("audio_channels")
            if expected_channels and int(audio.get("channels") or 0) != int(expected_channels):
                raise RuntimeError("Export audio channel count does not match the preset")
            expected_rate = preset.advanced.get("audio_sample_rate")
            if expected_rate and int(audio.get("sample_rate") or 0) != int(expected_rate):
                raise RuntimeError("Export audio sample rate does not match the preset")
        if preset.format == ExportFormat.AUDIO and audio is not None and video is not None:
            raise RuntimeError("Audio-only export unexpectedly contains video")
        if preset.format != ExportFormat.AUDIO:
            self._validate_video(state, preset, probe, video)
        profile = state.sequence.profile
        expected_fps = Fraction(
            int(preset.advanced.get("fps_numerator", profile.fps_numerator)),
            int(preset.advanced.get("fps_denominator", profile.fps_denominator)),
        )
        expected_duration = Fraction(expected_duration_frames, 1) / Fraction(
            profile.fps_numerator,
            profile.fps_denominator,
        )
        actual_duration = Fraction(str(probe.get("format", {}).get("duration") or "0"))
        duration_tolerance = Fraction(2, 1) / expected_fps
        if preset.format == ExportFormat.AUDIO:
            duration_tolerance = max(duration_tolerance, Fraction(1, 10))
        if abs(actual_duration - expected_duration) > duration_tolerance:
            raise RuntimeError(
                f"Export duration mismatch: expected {float(expected_duration):.3f}s, "
                f"got {float(actual_duration):.3f}s"
            )

    @staticmethod
    def _validate_video(
        state: TimelineState,
        preset: RuntimeExportPreset,
        probe: dict,
        video: dict | None,
    ) -> None:
        if video is None:
            raise RuntimeError("Export has no video stream")
        expected_codec = {
            ExportFormat.H264: "h264",
            ExportFormat.HEVC: "hevc",
            ExportFormat.AV1: "av1",
            ExportFormat.PRORES: "prores",
        }[preset.format]
        if video.get("codec_name") != expected_codec:
            raise RuntimeError(
                f"Export codec mismatch: expected {expected_codec}, got {video.get('codec_name')}"
            )
        profile = state.sequence.profile
        expected_width = int(preset.advanced.get("width", profile.width))
        expected_height = int(preset.advanced.get("height", profile.height))
        if int(video.get("width") or 0) != expected_width or int(video.get("height") or 0) != expected_height:
            raise RuntimeError(
                "Export resolution does not match the sequence profile: "
                f"expected {expected_width}x{expected_height}, got "
                f"{video.get('width')}x{video.get('height')}"
            )
        if preset.pixel_format and video.get("pix_fmt") != preset.pixel_format:
            raise RuntimeError(
                "Export pixel format does not match the preset: "
                f"expected {preset.pixel_format}, got {video.get('pix_fmt')}"
            )
        expected_fps = Fraction(
            int(preset.advanced.get("fps_numerator", profile.fps_numerator)),
            int(preset.advanced.get("fps_denominator", profile.fps_denominator)),
        )
        actual_fps = Fraction(str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"))
        if abs(actual_fps - expected_fps) > Fraction(1, 1000):
            raise RuntimeError(f"Export frame rate mismatch: expected {expected_fps}, got {actual_fps}")
        if profile.color_mode != ColorMode.HDR10_BT2020_PQ:
            return
        pixel_format = str(video.get("pix_fmt") or "")
        if "10" not in pixel_format and "12" not in pixel_format:
            raise RuntimeError("HDR10 export is not 10-bit")
        if video.get("color_primaries") != "bt2020" or video.get("color_transfer") != "smpte2084":
            raise RuntimeError("HDR10 export metadata is incomplete")
        side_data = list(video.get("side_data_list") or [])
        frames = probe.get("frames") or []
        if frames:
            side_data.extend(frames[0].get("side_data_list") or [])
        side_types = {item.get("side_data_type") for item in side_data}
        if preset.format == ExportFormat.HEVC and (preset.video_codec or "libx265") == "libx265":
            required = {"Mastering display metadata", "Content light level metadata"}
            if not required.issubset(side_types):
                raise RuntimeError("HDR10 export is missing mastering-display or content-light metadata")
