from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

from mediaflow.domain.web_rendering import WebRenderActualCapture

from .ffmpeg_runner import FfmpegRunner
from .ffprobe_runner import FfprobeRunner
from .web_render_target import WebRenderTarget


class WebRenderProbe:
    """Validate the complete video, frame clock, audio clock, and decode contract."""

    def __init__(self, ffmpeg: FfmpegRunner, ffprobe: FfprobeRunner) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def validate(
        self,
        path: Path,
        target: WebRenderTarget,
        actual_capture: WebRenderActualCapture,
    ) -> dict[str, object]:
        result = self.ffprobe.run(
            [
                "-v",
                "error",
                "-count_packets",
                "-show_entries",
                (
                    "stream=index,codec_type,codec_name,pix_fmt,width,height,"
                    "r_frame_rate,avg_frame_rate,nb_read_packets,sample_rate,channels,"
                    "color_range,color_space,color_transfer,color_primaries:format=duration"
                ),
                "-of",
                "json",
                str(path),
            ],
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"FFprobe rejected editable web media cache: {result.stderr.strip()}")
        try:
            payload = json.loads(result.stdout)
            streams = payload.get("streams") or []
            video_streams = [item for item in streams if item.get("codec_type") == "video"]
            audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
            if len(video_streams) != 1 or len(audio_streams) > 1:
                raise ValueError("unexpected editable media stream count")
            stream = video_streams[0]
            video_stream_index = int(stream["index"])
            codec_name = str(stream["codec_name"])
            pixel_format = str(stream["pix_fmt"])
            width = int(stream["width"])
            height = int(stream["height"])
            reported_frame_rate = Fraction(str(stream.get("r_frame_rate") or "0/1"))
            average_frame_rate = Fraction(str(stream.get("avg_frame_rate") or "0/1"))
            actual_frames = int(stream.get("nb_read_packets") or 0)
            duration = Fraction(str((payload.get("format") or {}).get("duration") or "0"))
            color_range = stream.get("color_range")
            color_space = stream.get("color_space")
            color_transfer = stream.get("color_transfer")
            color_primaries = stream.get("color_primaries")
            audio = audio_streams[0] if audio_streams else None
            audio_stream_index = int(audio["index"]) if audio is not None else None
            audio_codec_name = str(audio["codec_name"]) if audio is not None else None
            audio_sample_rate = int(audio["sample_rate"]) if audio is not None else None
            audio_channels = int(audio["channels"]) if audio is not None else None
        except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError) as error:
            raise RuntimeError("FFprobe returned incomplete editable web media cache metadata") from error
        expected_codec = (
            "h264"
            if actual_capture.backend == "webcodecs-h264"
            else "ffv1"
            if target.animated
            else "png"
        )
        expected_frames = target.frame_count if target.animated else 1
        expected_duration = Fraction(
            expected_frames * target.fps_denominator,
            target.fps_numerator,
        )
        expected_frame_rate = Fraction(target.fps_numerator, target.fps_denominator)
        packet_clock = (
            self._probe_packet_clock(
                path,
                target,
                video_stream_index=video_stream_index,
                audio_stream_index=audio_stream_index,
            )
            if target.animated
            else {
                "packet_pts_monotonic": True,
                "packet_dts_monotonic": True,
                "maximum_video_clock_error_microseconds": None,
                "audio_video_end_drift_microseconds": None,
            }
        )
        if target.animated:
            self._decode_representative_frames(path, target)
        if (
            codec_name != expected_codec
            or (width, height) != (target.width, target.height)
            or (
                target.animated
                and actual_capture.backend != "webcodecs-h264"
                and pixel_format != "bgra"
            )
            or (
                actual_capture.backend == "webcodecs-h264"
                and pixel_format not in {"yuv420p", "yuvj420p"}
            )
            or actual_frames != expected_frames
            or (
                target.animated
                and abs(reported_frame_rate - expected_frame_rate)
                > expected_frame_rate / 1_000_000
            )
            or (
                target.animated
                and abs(duration - expected_duration)
                > Fraction(target.fps_denominator, target.fps_numerator)
            )
            or (audio is not None) != target.has_audio
            or (
                actual_capture.backend == "webcodecs-h264"
                and (color_space, color_transfer, color_primaries)
                != ("bt709", "bt709", "bt709")
            )
            or (
                target.has_audio
                and (
                    audio_codec_name != "flac"
                    or audio_sample_rate != target.audio_sample_rate
                    or audio_channels != target.audio_channels
                )
            )
        ):
            raise RuntimeError(
                "Editable web media cache does not match its render target: "
                f"codec={codec_name}, pixel_format={pixel_format}, size={width}x{height}, "
                f"frames={actual_frames}/{expected_frames}, rate={reported_frame_rate}, "
                f"average_rate={average_frame_rate}, duration={duration}, "
                f"color={color_range}/{color_space}/{color_transfer}/{color_primaries}, "
                f"audio={audio_codec_name}/{audio_sample_rate}/{audio_channels}"
            )
        return {
            "codec_name": codec_name,
            "pixel_format": pixel_format,
            "width": width,
            "height": height,
            "frame_count": expected_frames,
            "fps_numerator": target.fps_numerator,
            "fps_denominator": target.fps_denominator,
            "reported_fps_numerator": reported_frame_rate.numerator,
            "reported_fps_denominator": reported_frame_rate.denominator,
            "average_fps_numerator": average_frame_rate.numerator,
            "average_fps_denominator": average_frame_rate.denominator,
            "has_audio": audio is not None,
            "audio_codec_name": audio_codec_name,
            "audio_sample_rate": audio_sample_rate,
            "audio_channels": audio_channels,
            "color_range": color_range,
            "color_space": color_space,
            "color_transfer": color_transfer,
            "color_primaries": color_primaries,
            **packet_clock,
        }

    def _decode_representative_frames(self, path: Path, target: WebRenderTarget) -> None:
        for frame_index in sorted({0, target.frame_count - 1}):
            timestamp = Fraction(
                frame_index * target.fps_denominator,
                target.fps_numerator,
            )
            result = self.ffmpeg.run(
                [
                    "-v",
                    "error",
                    "-xerror",
                    "-ss",
                    f"{float(timestamp):.9f}",
                    "-i",
                    str(path),
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-f",
                    "null",
                    "-",
                ],
                timeout=60,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    "FFmpeg could not decode a representative editable web frame: "
                    f"frame={frame_index}, {result.stderr.strip()}"
                )

    def _probe_packet_clock(
        self,
        path: Path,
        target: WebRenderTarget,
        *,
        video_stream_index: int,
        audio_stream_index: int | None,
    ) -> dict[str, object]:
        result = self.ffprobe.run(
            [
                "-v",
                "error",
                "-show_packets",
                "-show_entries",
                "packet=stream_index,pts_time,dts_time,duration_time",
                "-of",
                "json",
                str(path),
            ],
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"FFprobe rejected editable web packet timestamps: {result.stderr.strip()}"
            )
        try:
            packets = json.loads(result.stdout).get("packets") or []

            def stream_times(stream_index: int, key: str) -> list[Fraction]:
                return [
                    Fraction(str(packet[key]))
                    for packet in packets
                    if int(packet["stream_index"]) == stream_index
                ]

            video_pts = stream_times(video_stream_index, "pts_time")
            video_dts = stream_times(video_stream_index, "dts_time")
            video_durations = stream_times(video_stream_index, "duration_time")
            if not (
                len(video_pts)
                == len(video_dts)
                == len(video_durations)
                == target.frame_count
            ):
                raise ValueError("video packet timestamp count mismatch")
            pts_monotonic = all(
                left <= right for left, right in zip(video_pts, video_pts[1:], strict=False)
            )
            dts_monotonic = all(
                left <= right for left, right in zip(video_dts, video_dts[1:], strict=False)
            )
            expected_boundaries = [
                Fraction(index * target.fps_denominator, target.fps_numerator)
                for index in range(target.frame_count + 1)
            ]
            video_clock_errors = [
                abs(actual - expected)
                for actual, expected in zip(
                    video_pts,
                    expected_boundaries[:-1],
                    strict=True,
                )
            ]
            video_end = video_pts[-1] + video_durations[-1]
            video_clock_errors.append(abs(video_end - expected_boundaries[-1]))
            maximum_video_clock_error_microseconds = round(
                float(max(video_clock_errors) * 1_000_000)
            )
            if maximum_video_clock_error_microseconds > 1_000:
                raise ValueError(
                    "video packet clock differs from the rational frame clock by more than 1 ms"
                )
            audio_video_end_drift_microseconds: int | None = None
            if audio_stream_index is not None:
                audio_pts = stream_times(audio_stream_index, "pts_time")
                audio_dts = stream_times(audio_stream_index, "dts_time")
                audio_durations = stream_times(audio_stream_index, "duration_time")
                if not audio_pts or not (
                    len(audio_pts) == len(audio_dts) == len(audio_durations)
                ):
                    raise ValueError(
                        "audio packet timestamps are incomplete: "
                        f"pts={len(audio_pts)}, dts={len(audio_dts)}, "
                        f"duration={len(audio_durations)}"
                    )
                pts_monotonic = pts_monotonic and all(
                    left <= right for left, right in zip(audio_pts, audio_pts[1:], strict=False)
                )
                dts_monotonic = dts_monotonic and all(
                    left <= right for left, right in zip(audio_dts, audio_dts[1:], strict=False)
                )
                audio_end = audio_pts[-1] + audio_durations[-1]
                drift = abs(audio_end - video_end) * 1_000_000
                audio_video_end_drift_microseconds = round(float(drift))
                maximum_drift = Fraction(
                    target.fps_denominator * 1_000_000,
                    target.fps_numerator,
                )
                if drift > maximum_drift:
                    raise ValueError("audio and video packet clocks differ by more than one frame")
            if not pts_monotonic or not dts_monotonic:
                raise ValueError("packet timestamps are not monotonic")
        except (KeyError, TypeError, ValueError, ZeroDivisionError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"FFprobe returned invalid editable web packet timestamps: {error}"
            ) from error
        return {
            "packet_pts_monotonic": pts_monotonic,
            "packet_dts_monotonic": dts_monotonic,
            "maximum_video_clock_error_microseconds": maximum_video_clock_error_microseconds,
            "audio_video_end_drift_microseconds": audio_video_end_drift_microseconds,
        }
