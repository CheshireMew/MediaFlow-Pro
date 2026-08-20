from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path

from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.timeline import Clip, TimelineState
from mediaflow.domain.web_exports import (
    WebClipExportResult,
    WebExportFormat,
    require_web_export_destination,
)

from .ffmpeg_runner import FfmpegRunner
from .output_reservation import (
    archive_failed_output,
    require_output_transaction_path,
    reserve_output,
    temporary_output_path,
)
from .runtime_paths import RuntimePaths
from .web_render_target import WebRenderCache, WebRenderTarget


class WebClipExportWriter:
    def __init__(
        self,
        paths: RuntimePaths,
        ffmpeg: FfmpegRunner,
        cache: WebRenderCache,
        render_clip: Callable[..., Path],
    ):
        self.paths = paths
        self.ffmpeg = ffmpeg
        self.cache = cache
        self.render_clip = render_clip

    def export(
        self,
        state: TimelineState,
        clip_id: str,
        output_path: str | Path,
        format: WebExportFormat,
        *,
        time_ms: int = 0,
        background: str = "#000000",
        overwrite: bool = False,
        progress=None,
        check_cancelled=None,
    ) -> WebClipExportResult:
        destination = require_output_transaction_path(output_path)
        try:
            clip = next(item for item in state.clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
        target = self.cache.target(state, clip)
        require_web_export_destination(
            destination,
            format,
            overlay_suffix=target.path.suffix,
        )
        if destination.exists() and not overwrite:
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with reserve_output(destination, runtime_dir=self.paths.runtime_dir):
            return self._export_reserved(
                state,
                clip_id,
                destination,
                format,
                clip=clip,
                target=target,
                time_ms=time_ms,
                background=background,
                overwrite=overwrite,
                progress=progress,
                check_cancelled=check_cancelled,
            )

    def _export_reserved(
        self,
        state: TimelineState,
        clip_id: str,
        output_path: str | Path,
        format: WebExportFormat,
        *,
        clip: Clip,
        target: WebRenderTarget,
        time_ms: int,
        background: str,
        overwrite: bool,
        progress,
        check_cancelled,
    ) -> WebClipExportResult:
        destination = Path(output_path).expanduser().resolve()
        if destination.exists() and not overwrite:
            raise FileExistsError(destination)
        cache_path = self.render_clip(
            state,
            clip_id,
            progress=progress,
            check_cancelled=check_cancelled,
        )
        temporary = temporary_output_path(destination, f"web-{format}")
        try:
            self._write(
                format=format,
                cache_path=cache_path,
                target=target,
                state=state,
                clip=clip,
                destination=temporary,
                time_ms=time_ms,
                background=background,
                progress=progress,
                check_cancelled=check_cancelled,
            )
            if not temporary.is_file() or temporary.stat().st_size == 0:
                raise RuntimeError("Editable media export did not produce an output file")
            temporary.replace(destination)
        except Exception:
            archive_failed_output(temporary, destination)
            raise
        return WebClipExportResult(
            clip_id=clip_id,
            format=format,
            output_path=str(destination),
            cache_path=str(cache_path),
        )

    def _write(
        self,
        *,
        format: WebExportFormat,
        cache_path: Path,
        target: WebRenderTarget,
        state: TimelineState,
        clip: Clip,
        destination: Path,
        time_ms: int,
        background: str,
        progress,
        check_cancelled,
    ) -> None:
        if format == "overlay" or (format == "alpha_video" and target.animated):
            if progress:
                progress(OperationProgress.indeterminate("web_export_copying"))
            shutil.copyfile(cache_path, destination)
            return
        if format == "png":
            if cache_path.suffix.lower() == ".png" and time_ms == 0:
                if progress:
                    progress(OperationProgress.indeterminate("web_export_copying"))
                shutil.copyfile(cache_path, destination)
            else:
                self._run_ffmpeg(
                    [
                        "-ss",
                        f"{max(0, time_ms) / 1000:.6f}",
                        "-i",
                        str(cache_path),
                        "-frames:v",
                        "1",
                        "-y",
                        str(destination),
                    ],
                    duration_seconds=None,
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            return
        if format == "alpha_video":
            self._encode_static_alpha(
                cache_path,
                state,
                clip,
                destination,
                progress=progress,
                check_cancelled=check_cancelled,
            )
            return
        if format == "gif":
            fps = state.sequence.profile.fps
            duration = max(1 / fps, clip.duration / fps)
            self._run_ffmpeg(
                [
                    *self._looped_input(cache_path, fps, duration),
                    "-t",
                    f"{duration:.6f}",
                    "-filter_complex",
                    (
                        f"fps={fps:.6f},split[gif_a][gif_b];"
                        "[gif_a]palettegen=reserve_transparent=1[palette];"
                        "[gif_b][palette]paletteuse=alpha_threshold=128"
                    ),
                    "-loop",
                    "0",
                    "-y",
                    str(destination),
                ],
                duration_seconds=duration,
                progress=progress,
                check_cancelled=check_cancelled,
            )
            return
        if format != "video":
            raise ValueError(f"Unknown editable media export format: {format}")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", background):
            raise ValueError("Video background must be a #RRGGBB or #RRGGBBAA color")
        profile = state.sequence.profile
        fps = profile.fps
        duration = max(1 / fps, clip.duration / fps)
        audio_output = ["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"] if target.has_audio else ["-an"]
        self._run_ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c={background}:s={profile.width}x{profile.height}:r={fps:.6f}:d={duration:.6f}",
                *self._looped_input(cache_path, fps, duration),
                "-filter_complex",
                (
                    f"[1:v]scale={profile.width}:{profile.height}:"
                    "force_original_aspect_ratio=decrease[web];"
                    "[0:v][web]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p[video]"
                ),
                "-map",
                "[video]",
                *audio_output,
                "-t",
                f"{duration:.6f}",
                "-c:v",
                "libx264",
                "-movflags",
                "+faststart",
                "-y",
                str(destination),
            ],
            duration_seconds=duration,
            progress=progress,
            check_cancelled=check_cancelled,
        )

    def _encode_static_alpha(
        self,
        cache_path: Path,
        state: TimelineState,
        clip: Clip,
        destination: Path,
        *,
        progress,
        check_cancelled,
    ) -> None:
        fps = state.sequence.profile.fps
        duration = max(1 / fps, clip.duration / fps)
        self._run_ffmpeg(
            [
                "-loop",
                "1",
                "-framerate",
                f"{fps:.6f}",
                "-i",
                str(cache_path),
                "-t",
                f"{duration:.6f}",
                "-an",
                "-c:v",
                "ffv1",
                "-level",
                "3",
                "-pix_fmt",
                "bgra",
                "-y",
                str(destination),
            ],
            duration_seconds=duration,
            progress=progress,
            check_cancelled=check_cancelled,
        )

    @staticmethod
    def _looped_input(cache_path: Path, fps: float, duration: float) -> list[str]:
        if cache_path.suffix.lower() == ".png":
            return [
                "-loop",
                "1",
                "-framerate",
                f"{fps:.6f}",
                "-t",
                f"{duration:.6f}",
                "-i",
                str(cache_path),
            ]
        return ["-i", str(cache_path)]

    def _run_ffmpeg(
        self,
        arguments: list[str],
        *,
        duration_seconds: float | None,
        progress,
        check_cancelled,
    ) -> None:
        on_position: Callable[[float], None] | None = None
        if duration_seconds is not None and duration_seconds > 0 and progress is not None:

            def report_position(position: float) -> None:
                progress(
                    OperationProgress.determinate(
                        "web_export_encoding",
                        completed=position,
                        total=duration_seconds,
                        unit="media_seconds",
                    )
                )

            on_position = report_position
        elif progress is not None:
            progress(OperationProgress.indeterminate("web_export_encoding"))
        result = self.ffmpeg.run_progress(
            ["-loglevel", "error", *arguments],
            total_seconds=duration_seconds,
            on_position=on_position,
            check_cancelled=check_cancelled,
            timeout=1800,
        )
        if result.returncode != 0:
            raise RuntimeError("FFmpeg editable media export failed: " + result.stderr)
