from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from mediaflow.domain.enums import ColorMode, ExportFormat, TrackKind
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.srt_time import format_srt_timestamp
from mediaflow.domain.timebase import frames_to_seconds
from mediaflow.domain.timeline import TimelineState
from mediaflow.infrastructure.font_assets import apply_bundled_font_environment
from mediaflow.infrastructure.process_observers import MeltProgressObserver
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.subprocess_runner import run_cancellable_streaming

from .compiler import TimelineCompiler


@dataclass(frozen=True, slots=True)
class ExportResult:
    output_path: Path
    project_graph_path: Path
    probe: dict
    subtitle_files: tuple[Path, ...] = ()
    start_frame: int = 0
    end_frame: int = 1


class MltExportService:
    def __init__(self, compiler: TimelineCompiler, paths: RuntimePaths | None = None):
        self.compiler = compiler
        self.paths = paths or RuntimePaths.discover()

    def export(
        self,
        state: TimelineState,
        preset: ExportPreset,
        output_path: str | Path,
        *,
        progress=None,
        check_cancelled=None,
    ) -> ExportResult:
        if self.paths.melt is None:
            raise FileNotFoundError("MLT melt runtime is not installed")
        output = Path(output_path).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        graph_path = (
            self.compiler.repository.project_dir / "cache" / "mlt" / f"{state.sequence.id}-export.mlt"
        )
        if progress:
            progress(OperationProgress.indeterminate("export_compiling"))
        self.compiler.write(
            state,
            graph_path,
            use_proxies=False,
            subtitle_track_id=preset.burn_subtitle_track_id,
            subtitle_style=preset.subtitle_style,
            watermark=preset.watermark,
        )
        start_frame, end_frame = self._resolve_export_range(state, preset)
        consumer = self._consumer_properties(state, preset)
        command = [
            str(self.paths.melt),
            "-progress2",
            str(graph_path),
            f"in={start_frame}",
            f"out={end_frame - 1}",
            "-consumer",
            f"avformat:{output}",
            *consumer,
            "terminate_on_pause=1",
            "real_time=-1",
        ]
        environment = os.environ.copy()
        environment.pop("MLT_REPOSITORY_DENY", None)
        mlt_root = self.paths.melt.parent
        environment["MLT_REPOSITORY"] = str(mlt_root / "lib" / "mlt")
        environment["MLT_DATA"] = str(mlt_root / "share" / "mlt")
        apply_bundled_font_environment(
            preset.subtitle_style.font_family if preset.subtitle_style else None,
            environment,
        )
        total_frames = end_frame - start_frame
        observer = MeltProgressObserver(
            total_frames,
            lambda frame: progress(
                OperationProgress.determinate(
                    "export_rendering",
                    completed=frame,
                    total=total_frames,
                    unit="frames",
                )
            )
            if progress
            else None,
        )
        result = run_cancellable_streaming(
            command,
            cwd=mlt_root,
            env=environment,
            encoding="utf-8",
            errors="replace",
            timeout=3600,
            check_cancelled=check_cancelled,
            on_stdout_line=observer,
            on_stderr_line=observer,
            split_carriage_returns=True,
        )
        if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(
                "MLT export failed:\n" + "\n".join(part for part in (result.stdout, result.stderr) if part)
            )
        if progress:
            progress(
                OperationProgress.determinate(
                    "export_rendering",
                    completed=total_frames,
                    total=total_frames,
                    unit="frames",
                )
            )
        if progress:
            progress(OperationProgress.indeterminate("export_verifying"))
        probe = self._probe(output)
        self._validate_probe(
            state,
            preset,
            probe,
            expected_duration_frames=end_frame - start_frame,
        )
        subtitle_files = self._write_external_subtitles(
            state,
            output,
            start_frame=start_frame,
            end_frame=end_frame,
        )
        return ExportResult(
            output_path=output,
            project_graph_path=graph_path,
            probe=probe,
            subtitle_files=subtitle_files,
            start_frame=start_frame,
            end_frame=end_frame,
        )

    def _resolve_export_range(
        self,
        state: TimelineState,
        preset: ExportPreset,
    ) -> tuple[int, int]:
        del preset
        duration = max(1, state.duration_frames)
        bounds = state.sequence.in_out
        start = min(duration, bounds.in_frame) if bounds else 0
        end = min(duration, bounds.out_frame) if bounds else duration
        if end <= start:
            raise ValueError("Sequence in and out points do not contain exportable media")
        return start, end

    def _write_external_subtitles(
        self,
        state: TimelineState,
        output: Path,
        *,
        start_frame: int,
        end_frame: int,
    ) -> tuple[Path, ...]:
        segments = {
            segment.id: segment
            for document in self.compiler.repository.list_subtitle_documents()
            for segment in self.compiler.repository.list_subtitle_segments(document.id)
        }
        generated: list[Path] = []
        for track in state.tracks:
            if track.kind != TrackKind.SUBTITLE or not track.enabled:
                continue
            placements = self.compiler.repository.list_subtitle_placements(track.id)
            if not placements:
                continue
            safe_name = (
                "".join(
                    character if character.isalnum() or character in "-_" else "_" for character in track.name
                ).strip("_")
                or "subtitle"
            )
            lines: list[str] = []
            included = [
                placement
                for placement in placements
                if placement.end_frame > start_frame and placement.start_frame < end_frame
            ]
            if not included:
                continue
            destination = output.with_name(f"{output.stem}.{safe_name}.srt")
            for index, placement in enumerate(included, start=1):
                segment = segments.get(placement.segment_id)
                if segment is None:
                    raise RuntimeError("Subtitle export references a missing segment")
                start = frames_to_seconds(
                    max(start_frame, placement.start_frame) - start_frame,
                    state.sequence.profile.fps_numerator,
                    state.sequence.profile.fps_denominator,
                )
                end = frames_to_seconds(
                    min(end_frame, placement.end_frame) - start_frame,
                    state.sequence.profile.fps_numerator,
                    state.sequence.profile.fps_denominator,
                )
                lines.extend(
                    [
                        str(index),
                        f"{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}",
                        placement.text_override or segment.text,
                        "",
                    ]
                )
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            temporary.write_text("\n".join(lines), encoding="utf-8-sig")
            temporary.replace(destination)
            generated.append(destination)
        return tuple(generated)

    def _consumer_properties(self, state: TimelineState, preset: ExportPreset) -> list[str]:
        profile = state.sequence.profile
        values = [f"f={preset.container}"]
        codec = preset.video_codec or ""
        hardware_codec = codec.endswith(("_nvenc", "_qsv", "_amf"))
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
                if codec.endswith("_nvenc"):
                    values.extend(["rc=vbr", f"cq={preset.quality_value:g}"])
                elif codec.endswith("_qsv"):
                    values.append(f"global_quality={preset.quality_value:g}")
                else:
                    values.append(f"qp_i={preset.quality_value:g}")
            else:
                values.append(f"crf={preset.quality_value:g}")
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

    def _probe(self, output: Path) -> dict:
        result = subprocess.run(
            [
                str(self.paths.ffprobe),
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
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Export verification failed: {result.stderr.strip()}")
        return json.loads(result.stdout)

    @staticmethod
    def _validate_probe(
        state: TimelineState,
        preset: ExportPreset,
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
        if preset.format == ExportFormat.AUDIO and audio is not None:
            if video is not None:
                raise RuntimeError("Audio-only export unexpectedly contains video")
        if preset.format != ExportFormat.AUDIO:
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
            if (
                int(video.get("width") or 0) != expected_width
                or int(video.get("height") or 0) != expected_height
            ):
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
            actual_fps_text = str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1")
            actual_fps = Fraction(actual_fps_text)
            if abs(actual_fps - expected_fps) > Fraction(1, 1000):
                raise RuntimeError(f"Export frame rate mismatch: expected {expected_fps}, got {actual_fps}")
            expected_duration = Fraction(
                expected_duration_frames,
                1,
            ) / Fraction(profile.fps_numerator, profile.fps_denominator)
            actual_duration = Fraction(str(probe.get("format", {}).get("duration") or "0"))
            if abs(actual_duration - expected_duration) > Fraction(2, 1) / expected_fps:
                raise RuntimeError(
                    f"Export duration mismatch: expected {float(expected_duration):.3f}s, "
                    f"got {float(actual_duration):.3f}s"
                )
            if profile.color_mode == ColorMode.HDR10_BT2020_PQ:
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
                        raise RuntimeError(
                            "HDR10 export is missing mastering-display or content-light metadata"
                        )
