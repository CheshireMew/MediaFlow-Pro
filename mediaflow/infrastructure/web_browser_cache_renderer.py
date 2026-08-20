from __future__ import annotations

import json
from fractions import Fraction
from functools import partial as bind_arguments
from pathlib import Path

from mediaflow.application.web_package_files import web_package_root
from mediaflow.atomic_file import atomic_write_text, unique_temporary_sibling
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.web_manifest import WebAssetSpec
from mediaflow.domain.web_state import (
    WebClipState,
    web_runtime_state,
)

from .ffmpeg_runner import FfmpegRunner
from .ffprobe_runner import FfprobeRunner
from .file_fingerprint import fingerprint_file
from .runtime_paths import RuntimePaths
from .web_browser import WebPackagePreviewServer
from .web_capture_engine import (
    FastCaptureFallbackRequired,
    WebCaptureMode,
    get_web_capture_engine,
)
from .web_render_ffmpeg import build_web_render_ffmpeg_command
from .web_render_target import (
    WEB_CACHE_MANIFEST_SCHEMA,
    WEB_RENDERER_VERSION,
    WebRenderTarget,
)


class WebBrowserCacheRenderer:
    def __init__(self, paths: RuntimePaths, ffmpeg: FfmpegRunner):
        self.paths = paths
        self.ffmpeg = ffmpeg
        self.ffprobe = FfprobeRunner(self.paths.ffprobe)

    def render(
        self,
        entry: Path,
        spec: WebAssetSpec,
        clip_state: WebClipState,
        state: TimelineState,
        target: WebRenderTarget,
        *,
        progress=None,
        check_cancelled=None,
        capture_start_frame: int = 0,
    ) -> None:
        executable = self.paths.chromium
        if executable is None or not executable.is_file():
            raise FileNotFoundError("Pinned Playwright Chromium is unavailable")
        engine = get_web_capture_engine(executable)
        manifest = spec.manifest
        package_root = web_package_root(entry, manifest)
        variant = manifest.variant_for(clip_state.variant.id if clip_state.variant is not None else None)
        runtime_state = web_runtime_state(clip_state, manifest)
        partial = unique_temporary_sibling(target.path, label="web-render")
        try:
            with WebPackagePreviewServer(package_root) as preview:
                capture_url = preview.url_for(
                    manifest.entry,
                    query=(f"capture=1&variant={variant.id}&scene={runtime_state['scene_id']}"),
                )
                capture_modes: tuple[WebCaptureMode, ...] = ("auto", "screenshot")
                if target.animated:
                    self._render_animation(
                        engine,
                        preview,
                        capture_url,
                        variant.canvas.width,
                        variant.canvas.height,
                        spec,
                        runtime_state,
                        state,
                        target,
                        partial,
                        capture_modes=capture_modes,
                        progress=progress,
                        check_cancelled=check_cancelled,
                        capture_start_frame=capture_start_frame,
                    )
                else:
                    self._render_still(
                        engine,
                        preview,
                        capture_url,
                        variant.canvas.width,
                        variant.canvas.height,
                        spec,
                        runtime_state,
                        state,
                        target,
                        partial,
                        capture_modes=capture_modes,
                        progress=progress,
                        check_cancelled=check_cancelled,
                        capture_start_frame=capture_start_frame,
                    )
            probe = self._probe(partial, target)
            partial.replace(target.path)
            self._publish_manifest(target, probe)
        finally:
            partial.unlink(missing_ok=True)

    def _render_animation(
        self,
        engine,
        preview: WebPackagePreviewServer,
        capture_url: str,
        width: int,
        height: int,
        spec: WebAssetSpec,
        runtime_state: dict,
        state: TimelineState,
        target: WebRenderTarget,
        partial: Path,
        *,
        capture_modes: tuple[WebCaptureMode, ...],
        progress,
        check_cancelled,
        capture_start_frame: int,
    ) -> None:
        fps = Fraction(
            state.sequence.profile.fps_numerator,
            state.sequence.profile.fps_denominator,
        )
        command = build_web_render_ffmpeg_command(target, partial)
        if progress:
            progress(
                OperationProgress.determinate(
                    "web_rendering", completed=0, total=target.frame_count, unit="frames"
                )
            )

        def report_frame(completed: int) -> None:
            if progress:
                progress(
                    OperationProgress.determinate(
                        "web_rendering",
                        completed=completed,
                        total=target.frame_count,
                        unit="frames",
                    )
                )

        fallback_reason: str | None = None
        for capture_mode in capture_modes:
            pipe = self.ffmpeg.open_input_pipe(command)
            try:
                engine.render_frames(
                    url=capture_url,
                    allowed_origin=preview.url_for(""),
                    width=width,
                    height=height,
                    fps_numerator=fps.numerator,
                    fps_denominator=fps.denominator,
                    runtime_state=runtime_state,
                    determinism_key=target.key,
                    frame_count=target.frame_count,
                    on_frame=bind_arguments(pipe.write, check_cancelled=check_cancelled),
                    on_progress=report_frame,
                    check_cancelled=check_cancelled,
                    capture_mode=capture_mode,
                    fallback_reason=fallback_reason,
                    retry_limit=spec.manifest.frame_readiness.retry_limit,
                    start_frame=capture_start_frame,
                )
            except FastCaptureFallbackRequired as error:
                pipe.abort()
                fallback_reason = str(error)
                continue
            except BaseException:
                pipe.abort()
                raise
            result = pipe.finish(timeout=1800)
            if result.returncode != 0:
                raise RuntimeError(f"FFmpeg editable web media render failed: {result.stderr}")
            return
        raise RuntimeError("Editable web media capture exhausted its screenshot fallback")

    @staticmethod
    def _render_still(
        engine,
        preview: WebPackagePreviewServer,
        capture_url: str,
        width: int,
        height: int,
        spec: WebAssetSpec,
        runtime_state: dict,
        state: TimelineState,
        target: WebRenderTarget,
        partial: Path,
        *,
        capture_modes: tuple[WebCaptureMode, ...],
        progress,
        check_cancelled,
        capture_start_frame: int,
    ) -> None:
        if progress:
            progress(OperationProgress.determinate("web_rendering", completed=0, total=1, unit="frames"))
        frames: list[bytes] = []
        fallback_reason: str | None = None
        for capture_mode in capture_modes:
            frames.clear()
            try:
                engine.render_frames(
                    url=capture_url,
                    allowed_origin=preview.url_for(""),
                    width=width,
                    height=height,
                    fps_numerator=state.sequence.profile.fps_numerator,
                    fps_denominator=state.sequence.profile.fps_denominator,
                    runtime_state=runtime_state,
                    determinism_key=target.key,
                    frame_count=1,
                    on_frame=frames.append,
                    check_cancelled=check_cancelled,
                    capture_mode=capture_mode,
                    fallback_reason=fallback_reason,
                    retry_limit=spec.manifest.frame_readiness.retry_limit,
                    start_frame=capture_start_frame,
                )
            except FastCaptureFallbackRequired as error:
                fallback_reason = str(error)
                continue
            break
        else:
            raise RuntimeError("Editable web media capture exhausted its screenshot fallback")
        partial.write_bytes(frames[0])
        if progress:
            progress(OperationProgress.determinate("web_rendering", completed=1, total=1, unit="frames"))

    def _probe(self, path: Path, target: WebRenderTarget) -> dict[str, object]:
        result = self.ffprobe.run(
            [
                "-v",
                "error",
                "-show_entries",
                (
                    "stream=codec_type,codec_name,pix_fmt,width,height,"
                    "avg_frame_rate,sample_rate,channels:format=duration"
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
            codec_name = str(stream["codec_name"])
            pixel_format = str(stream["pix_fmt"])
            width = int(stream["width"])
            height = int(stream["height"])
            frame_rate = Fraction(str(stream.get("avg_frame_rate") or "0/1"))
            duration = Fraction(str((payload.get("format") or {}).get("duration") or "0"))
            audio = audio_streams[0] if audio_streams else None
            audio_codec_name = str(audio["codec_name"]) if audio is not None else None
            audio_sample_rate = int(audio["sample_rate"]) if audio is not None else None
            audio_channels = int(audio["channels"]) if audio is not None else None
        except (IndexError, KeyError, TypeError, ValueError, ZeroDivisionError) as error:
            raise RuntimeError("FFprobe returned incomplete editable web media cache metadata") from error
        expected_codec = "ffv1" if target.animated else "png"
        expected_frames = target.frame_count if target.animated else 1
        expected_duration = Fraction(
            expected_frames * target.fps_denominator,
            target.fps_numerator,
        )
        if (
            codec_name != expected_codec
            or (width, height) != (target.width, target.height)
            or (target.animated and pixel_format != "bgra")
            or (target.animated and frame_rate != Fraction(target.fps_numerator, target.fps_denominator))
            or (
                target.animated
                and abs(duration - expected_duration) > Fraction(target.fps_denominator, target.fps_numerator)
            )
            or (audio is not None) != target.has_audio
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
                f"frames={expected_frames}, rate={frame_rate}, duration={duration}, "
                f"audio={audio_codec_name}/{audio_sample_rate}/{audio_channels}"
            )
        return {
            "codec_name": codec_name,
            "pixel_format": pixel_format,
            "width": width,
            "height": height,
            "frame_count": expected_frames,
            "fps_numerator": frame_rate.numerator,
            "fps_denominator": frame_rate.denominator,
            "has_audio": audio is not None,
            "audio_codec_name": audio_codec_name,
            "audio_sample_rate": audio_sample_rate,
            "audio_channels": audio_channels,
        }

    @staticmethod
    def _publish_manifest(target: WebRenderTarget, probe: dict[str, object]) -> None:
        payload = {
            "schema": WEB_CACHE_MANIFEST_SCHEMA,
            "renderer_version": WEB_RENDERER_VERSION,
            "key": target.key,
            "animated": target.animated,
            "frame_count": target.frame_count,
            "width": target.width,
            "height": target.height,
            "fps_numerator": target.fps_numerator,
            "fps_denominator": target.fps_denominator,
            "has_audio": target.has_audio,
            "audio_sample_rate": target.audio_sample_rate,
            "audio_channels": target.audio_channels,
            "fingerprint": fingerprint_file(target.path).model_dump(mode="json"),
            "probe": probe,
        }
        atomic_write_text(
            target.manifest_path,
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
