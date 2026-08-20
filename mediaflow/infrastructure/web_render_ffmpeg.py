from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from .web_native_media import WebNativeAudioSegment, WebNativeVideoSegment
from .web_render_target import WebRenderTarget


def build_web_render_ffmpeg_command(
    target: WebRenderTarget,
    output_path: Path,
) -> list[str]:
    if not target.animated:
        raise ValueError("FFmpeg frame-pipe rendering requires an animated target")
    fps = Fraction(target.fps_numerator, target.fps_denominator)
    duration_ms = Fraction(
        target.frame_count * target.fps_denominator * 1000,
        target.fps_numerator,
    )
    arguments = [
        "-loglevel",
        "error",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-framerate",
        f"{fps.numerator}/{fps.denominator}",
        "-i",
        "-",
    ]
    if (
        not target.native_media_plan.video_segments
        and not target.native_media_plan.audio_segments
        and not target.has_audio
    ):
        return [
            *arguments,
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-pix_fmt",
            "bgra",
            "-y",
            str(output_path),
        ]
    next_input = 1
    video_inputs: list[tuple[WebNativeVideoSegment, int]] = []
    for video_segment in target.native_media_plan.video_segments:
        if video_segment.playback == "repeat":
            arguments.extend(["-stream_loop", "-1"])
        if video_segment.source_in_ms:
            arguments.extend(["-ss", _milliseconds_as_seconds(Fraction(video_segment.source_in_ms))])
        arguments.extend(["-i", str(video_segment.path)])
        video_inputs.append((video_segment, next_input))
        next_input += 1
    audio_inputs: list[tuple[WebNativeAudioSegment, int]] = []
    for audio_segment in target.native_media_plan.audio_segments:
        if audio_segment.loop == "repeat":
            arguments.extend(["-stream_loop", "-1"])
        if audio_segment.source_in_ms:
            arguments.extend(["-ss", _milliseconds_as_seconds(Fraction(audio_segment.source_in_ms))])
        arguments.extend(["-i", str(audio_segment.path)])
        audio_inputs.append((audio_segment, next_input))
        next_input += 1

    filters: list[str] = []
    video_map = "0:v:0"
    if video_inputs:
        timeline_labels: list[str] = []
        cursor_ms = Fraction(0)
        for index, (video_segment, input_index) in enumerate(video_inputs):
            if video_segment.start_ms < cursor_ms:
                raise ValueError("Editable media native video segments overlap")
            if video_segment.start_ms > cursor_ms:
                gap_label = f"native_gap_{index}"
                filters.append(
                    "color="
                    f"c=black@0.0:s={target.width}x{target.height}:"
                    f"r={fps.numerator}/{fps.denominator}:"
                    f"d={_milliseconds_as_seconds(video_segment.start_ms - cursor_ms)},"
                    f"format=bgra,setpts=PTS-STARTPTS[{gap_label}]"
                )
                timeline_labels.append(gap_label)
            segment_label = f"native_video_{index}"
            scale = (
                f"scale={target.width}:{target.height}:"
                "force_original_aspect_ratio=increase,"
                f"crop={target.width}:{target.height}"
                if video_segment.fit == "cover"
                else (
                    f"scale={target.width}:{target.height}:"
                    "force_original_aspect_ratio=decrease,"
                    f"pad={target.width}:{target.height}:"
                    "(ow-iw)/2:(oh-ih)/2:color=black@0.0"
                )
            )
            hold = (
                f",tpad=stop_mode=clone:stop_duration={_milliseconds_as_seconds(video_segment.active_duration_ms)}"
                if video_segment.playback == "hold"
                else ""
            )
            frozen_tail_ms = video_segment.duration_ms - video_segment.active_duration_ms
            frozen_tail = (
                f",tpad=stop_mode=clone:stop_duration={_milliseconds_as_seconds(frozen_tail_ms)}"
                if frozen_tail_ms > 0
                else ""
            )
            filters.append(
                f"[{input_index}:v:0]"
                f"fps={fps.numerator}/{fps.denominator},{scale},format=bgra"
                f"{hold},trim=duration={_milliseconds_as_seconds(video_segment.active_duration_ms)}"
                f"{frozen_tail},trim=duration={_milliseconds_as_seconds(video_segment.duration_ms)},"
                f"setpts=PTS-STARTPTS[{segment_label}]"
            )
            timeline_labels.append(segment_label)
            cursor_ms = video_segment.start_ms + video_segment.duration_ms
        if cursor_ms < duration_ms:
            gap_label = "native_gap_tail"
            filters.append(
                "color="
                f"c=black@0.0:s={target.width}x{target.height}:"
                f"r={fps.numerator}/{fps.denominator}:"
                f"d={_milliseconds_as_seconds(duration_ms - cursor_ms)},"
                f"format=bgra,setpts=PTS-STARTPTS[{gap_label}]"
            )
            timeline_labels.append(gap_label)
        underlay_label = timeline_labels[0]
        if len(timeline_labels) > 1:
            underlay_label = "native_underlay"
            inputs = "".join(f"[{label}]" for label in timeline_labels)
            filters.append(f"{inputs}concat=n={len(timeline_labels)}:v=1:a=0[{underlay_label}]")
        filters.extend(
            [
                "[0:v:0]format=rgba[web_overlay]",
                f"[{underlay_label}][web_overlay]"
                "overlay=0:0:shortest=1:format=auto,format=bgra[web_composite]",
            ]
        )
        video_map = "[web_composite]"

    audio_map: str | None = None
    if target.has_audio:
        layout = _audio_channel_layout(target.audio_channels)
        audio_labels: list[str] = []
        for index, (audio_segment, input_index) in enumerate(audio_inputs):
            if audio_segment.start_ms.denominator != 1:
                raise ValueError("Editable media native audio start must use whole milliseconds")
            audio_label = f"native_audio_{index}"
            filters.append(
                f"[{input_index}:a:0]"
                f"aresample={target.audio_sample_rate},"
                f"aformat=sample_fmts=fltp:channel_layouts={layout},"
                f"volume={audio_segment.gain_db:.6f}dB,apad,"
                f"atrim=duration={_milliseconds_as_seconds(audio_segment.duration_ms)},"
                "asetpts=PTS-STARTPTS,"
                f"adelay={audio_segment.start_ms.numerator}:all=1[{audio_label}]"
            )
            audio_labels.append(audio_label)
        if audio_labels:
            inputs = "".join(f"[{label}]" for label in audio_labels)
            filters.append(
                f"{inputs}amix=inputs={len(audio_labels)}:duration=longest:normalize=0,"
                f"atrim=duration={_milliseconds_as_seconds(duration_ms)},"
                f"aformat=sample_rates={target.audio_sample_rate}:"
                f"channel_layouts={layout},asetpts=PTS-STARTPTS[web_audio]"
            )
        else:
            filters.append(
                f"anullsrc=r={target.audio_sample_rate}:cl={layout},"
                f"atrim=duration={_milliseconds_as_seconds(duration_ms)},"
                "asetpts=PTS-STARTPTS[web_audio]"
            )
        audio_map = "[web_audio]"

    if filters:
        arguments.extend(["-filter_complex", ";".join(filters)])
    arguments.extend(["-map", video_map])
    if audio_map is None:
        arguments.append("-an")
    else:
        arguments.extend(["-map", audio_map])
    arguments.extend(
        [
            "-frames:v",
            str(target.frame_count),
            "-t",
            _milliseconds_as_seconds(duration_ms),
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-pix_fmt",
            "bgra",
        ]
    )
    if audio_map is not None:
        arguments.extend(["-c:a", "flac"])
    arguments.extend(["-y", str(output_path)])
    return arguments


def _fraction_decimal(value: Fraction, digits: int = 9) -> str:
    rendered = f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    return rendered or "0"


def _milliseconds_as_seconds(value: Fraction) -> str:
    return _fraction_decimal(value / 1000)


def _audio_channel_layout(channels: int) -> str:
    try:
        return {1: "mono", 2: "stereo", 6: "5.1"}[channels]
    except KeyError as error:
        raise ValueError(f"Unsupported editable media audio channels: {channels}") from error
