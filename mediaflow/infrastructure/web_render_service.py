from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from mediaflow.application.ports import TimelineCompilationDocuments
from mediaflow.application.web_media_service import editable_media_source_hash
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import Asset
from mediaflow.domain.timeline import Clip, TimelineState
from mediaflow.domain.web_media import (
    WebAssetSpec,
    WebClipExportResult,
    WebClipState,
    WebExportFormat,
    web_runtime_state,
)
from mediaflow.infrastructure.chromium_runtime import find_chromium_executable
from mediaflow.infrastructure.process_observers import (
    FfmpegProgressObserver,
    ffmpeg_progress_command,
)
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.subprocess_runner import run_cancellable_streaming
from mediaflow.infrastructure.web_browser import validate_editable_media_page

WEB_RENDERER_VERSION = "2"


@dataclass(frozen=True, slots=True)
class WebRenderTarget:
    key: str
    path: Path
    animated: bool
    frame_count: int


class WebRenderCache:
    def __init__(self, documents: TimelineCompilationDocuments):
        self.documents = documents

    def target(
        self,
        state: TimelineState,
        clip: Clip,
        asset: Asset | None = None,
    ) -> WebRenderTarget:
        asset = asset or self.documents.get_asset(clip.asset_id)
        if asset.kind != AssetKind.WEB:
            raise ValueError("Web render cache only accepts web clips")
        spec = self.documents.get_web_asset_spec(asset.id)
        clip_state = state.web_states.get(clip.id)
        if clip_state is None:
            raise ValueError(f"Web clip has no editable state: {clip.id}")
        animated = spec.manifest.timeline.duration_ms > 0 or bool(clip_state.animations)
        speed = Fraction(abs(clip.speed_numerator), clip.speed_denominator)
        consumed = max(1, -(-(clip.duration * speed.numerator) // speed.denominator))
        frame_count = max(
            1,
            clip.source_in + consumed if clip.speed_numerator > 0 else clip.source_in + 1,
        )
        payload = {
            "renderer_version": WEB_RENDERER_VERSION,
            "source_hash": editable_media_source_hash(
                self.documents.resolve_asset_path(asset).parent
            ),
            "state": clip_state.model_dump(mode="json"),
            "sequence": state.sequence.profile.model_dump(mode="json"),
            "clip_range": {
                "source_in": clip.source_in,
                "duration": clip.duration,
                "speed_numerator": clip.speed_numerator,
                "speed_denominator": clip.speed_denominator,
            },
            "frame_count": frame_count,
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        suffix = ".mkv" if animated else ".png"
        return WebRenderTarget(
            key=digest,
            # The complete digest remains the logical key. A 128-bit prefix is
            # sufficient for the cache filename and keeps deep Windows project
            # paths below legacy path-length limits.
            path=self.documents.project_dir / "cache" / "web" / f"{digest[:32]}{suffix}",
            animated=animated,
            frame_count=frame_count,
        )


class WebRenderService:
    def __init__(
        self,
        documents: TimelineCompilationDocuments,
        paths: RuntimePaths | None = None,
    ) -> None:
        self.documents = documents
        self.paths = paths or RuntimePaths.discover()
        self.cache = WebRenderCache(documents)

    def ensure_sequence(
        self,
        state: TimelineState,
        *,
        progress=None,
        check_cancelled=None,
    ) -> list[Path]:
        assets = {asset.id: asset for asset in self.documents.list_assets()}
        web_clips = [
            clip for clip in state.clips if assets.get(clip.asset_id, None) is not None
            and assets[clip.asset_id].kind == AssetKind.WEB
        ]
        results: list[Path] = []
        for index, clip in enumerate(web_clips):
            if check_cancelled is not None:
                check_cancelled()
            results.append(
                self.render_clip(
                    state,
                    clip.id,
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            )
            if progress is not None and web_clips:
                progress(
                    OperationProgress.determinate(
                        "web_render_items",
                        completed=index + 1,
                        total=len(web_clips),
                        unit="items",
                    )
                )
        return results

    def render_clip(
        self,
        state: TimelineState,
        clip_id: str,
        *,
        progress=None,
        check_cancelled=None,
    ) -> Path:
        try:
            clip = next(item for item in state.clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
        asset = self.documents.get_asset(clip.asset_id)
        target = self.cache.target(state, clip, asset)
        if self._cache_is_ready(target.path):
            if progress:
                progress(
                    OperationProgress.determinate(
                        "web_render_cache_ready",
                        completed=1,
                        total=1,
                        unit="items",
                    )
                )
            return target.path
        if progress:
            progress(OperationProgress.indeterminate("web_render_preparing"))
        target.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.path.with_name(f"{target.path.name}.lock")
        owns_lock = self._acquire_cache_lock(
            lock_path,
            target.path,
            check_cancelled=check_cancelled,
        )
        if not owns_lock:
            return target.path
        try:
            if self._cache_is_ready(target.path):
                return target.path
            spec = self.documents.get_web_asset_spec(asset.id)
            clip_state = state.web_states[clip.id]
            entry = self.documents.resolve_asset_path(asset)
            if not entry.is_file():
                raise FileNotFoundError(entry)
            self._render_browser(
                entry,
                spec,
                clip_state,
                state,
                target,
                progress=progress,
                check_cancelled=check_cancelled,
            )
            if not self._cache_is_ready(target.path):
                raise RuntimeError("Editable web media renderer did not produce a cache file")
            return target.path
        finally:
            lock_path.unlink(missing_ok=True)

    @staticmethod
    def _cache_is_ready(path: Path) -> bool:
        return path.is_file() and path.stat().st_size > 0

    @classmethod
    def _acquire_cache_lock(
        cls,
        lock_path: Path,
        target_path: Path,
        *,
        check_cancelled=None,
    ) -> bool:
        deadline = time.monotonic() + 900
        while True:
            if cls._cache_is_ready(target_path):
                return False
            try:
                descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    stale = time.time() - lock_path.stat().st_mtime > 3600
                except FileNotFoundError:
                    continue
                if stale:
                    lock_path.unlink(missing_ok=True)
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out waiting for editable media cache: {target_path}"
                    ) from None
                if check_cancelled is not None:
                    check_cancelled()
                time.sleep(0.1)
                continue
            else:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(f"pid={os.getpid()}\ncreated={time.time()}\n")
                return True

    def export_clip(
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
        try:
            clip = next(item for item in state.clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
        cache_path = self.render_clip(
            state,
            clip_id,
            progress=progress,
            check_cancelled=check_cancelled,
        )
        destination = Path(output_path).expanduser().resolve()
        if destination.exists() and not overwrite:
            raise FileExistsError(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = self.cache.target(state, clip)
        expected_suffixes = {
            "png": {".png"},
            "gif": {".gif"},
            "alpha_video": {".mkv"},
            "video": {".mp4", ".mov", ".mkv"},
            "overlay": {cache_path.suffix.lower()},
        }
        if destination.suffix.lower() not in expected_suffixes[format]:
            raise ValueError(
                f"Editable media {format} export needs one of: "
                f"{sorted(expected_suffixes[format])}"
            )
        if format == "overlay" or (format == "alpha_video" and target.animated):
            if progress:
                progress(OperationProgress.indeterminate("web_export_copying"))
            shutil.copyfile(cache_path, destination)
        elif format == "png":
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
        elif format == "alpha_video":
            self._encode_static_alpha(
                cache_path,
                state,
                clip,
                destination,
                progress=progress,
                check_cancelled=check_cancelled,
            )
        elif format == "gif":
            fps = state.sequence.profile.fps
            duration = max(1 / fps, clip.duration / fps)
            input_args = self._looped_input(cache_path, fps, duration)
            self._run_ffmpeg(
                [
                    *input_args,
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
        elif format == "video":
            if not re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", background):
                raise ValueError("Video background must be a #RRGGBB or #RRGGBBAA color")
            profile = state.sequence.profile
            fps = profile.fps
            duration = max(1 / fps, clip.duration / fps)
            source_args = self._looped_input(cache_path, fps, duration)
            self._run_ffmpeg(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    (
                        f"color=c={background}:s={profile.width}x{profile.height}:"
                        f"r={fps:.6f}:d={duration:.6f}"
                    ),
                    *source_args,
                    "-filter_complex",
                    (
                        f"[1:v]scale={profile.width}:{profile.height}:"
                        "force_original_aspect_ratio=decrease[web];"
                        "[0:v][web]overlay=(W-w)/2:(H-h)/2:shortest=1,format=yuv420p"
                    ),
                    "-t",
                    f"{duration:.6f}",
                    "-an",
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
        else:
            raise ValueError(f"Unknown editable media export format: {format}")
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("Editable media export did not produce an output file")
        return WebClipExportResult(
            clip_id=clip_id,
            format=format,
            output_path=str(destination),
            cache_path=str(cache_path),
        )

    def _encode_static_alpha(
        self,
        cache_path: Path,
        state: TimelineState,
        clip: Clip,
        destination: Path,
        *,
        progress=None,
        check_cancelled=None,
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
        progress=None,
        check_cancelled=None,
    ) -> None:
        if duration_seconds is not None and duration_seconds > 0:
            observer = FfmpegProgressObserver(
                duration_seconds,
                lambda position: progress(
                    OperationProgress.determinate(
                        "web_export_encoding",
                        completed=position,
                        total=duration_seconds,
                        unit="media_seconds",
                    )
                )
                if progress
                else None,
            )
        else:
            observer = None
            if progress:
                progress(OperationProgress.indeterminate("web_export_encoding"))
        result = run_cancellable_streaming(
            ffmpeg_progress_command(
                [str(self.paths.ffmpeg), "-hide_banner", "-loglevel", "error", *arguments]
            ),
            on_stderr_line=observer,
            check_cancelled=check_cancelled,
            timeout=1800,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "FFmpeg editable media export failed: "
                + result.stderr
            )

    def _render_browser(
        self,
        entry: Path,
        spec: WebAssetSpec,
        clip_state: WebClipState,
        state: TimelineState,
        target: WebRenderTarget,
        *,
        progress=None,
        check_cancelled=None,
    ) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as error:
            raise RuntimeError("Playwright is required for editable web media rendering") from error
        executable = find_chromium_executable()
        manifest = spec.manifest
        layout = manifest.layout_for(
            clip_state.layout_id,
            state.sequence.profile.width,
            state.sequence.profile.height,
        )
        runtime_state = web_runtime_state(
            clip_state,
            manifest,
            width=state.sequence.profile.width,
            height=state.sequence.profile.height,
        )
        partial = target.path.with_name(
            f"{target.path.stem}.{uuid.uuid4().hex[:8]}.partial{target.path.suffix}"
        )
        ffmpeg: subprocess.Popen[bytes] | None = None
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=str(executable),
                    headless=True,
                    args=["--disable-renderer-backgrounding", "--disable-background-timer-throttling"],
                )
                context = browser.new_context(
                    viewport={
                        "width": layout.canvas.width,
                        "height": layout.canvas.height,
                    },
                    device_scale_factor=1,
                )
                context.route(
                    "http://**/*",
                    lambda route: route.abort(),
                )
                context.route(
                    "https://**/*",
                    lambda route: route.abort(),
                )
                page = context.new_page()
                page.goto(f"{entry.as_uri()}?capture=1", wait_until="load", timeout=15000)
                validate_editable_media_page(page, manifest)
                page.evaluate(
                    "state => window.editableMedia.setState(state)",
                    runtime_state,
                )
                if target.animated:
                    fps = Fraction(
                        state.sequence.profile.fps_numerator,
                        state.sequence.profile.fps_denominator,
                    )
                    command = [
                        str(self.paths.ffmpeg),
                        "-hide_banner",
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
                        "-an",
                        "-c:v",
                        "ffv1",
                        "-level",
                        "3",
                        "-pix_fmt",
                        "bgra",
                        "-y",
                        str(partial),
                    ]
                    ffmpeg = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
                    if ffmpeg.stdin is None:
                        raise RuntimeError("FFmpeg web render pipe was not created")
                    if progress:
                        progress(
                            OperationProgress.determinate(
                                "web_rendering",
                                completed=0,
                                total=target.frame_count,
                                unit="frames",
                            )
                        )
                    for frame in range(target.frame_count):
                        if check_cancelled is not None:
                            check_cancelled()
                        milliseconds = frame * 1000 * fps.denominator / fps.numerator
                        page.evaluate("time => window.editableMedia.setTime(time)", milliseconds)
                        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => resolve()))")
                        ffmpeg.stdin.write(page.screenshot(type="png", omit_background=True))
                        if progress:
                            progress(
                                OperationProgress.determinate(
                                    "web_rendering",
                                    completed=frame + 1,
                                    total=target.frame_count,
                                    unit="frames",
                                )
                            )
                    ffmpeg.stdin.close()
                    stderr = ffmpeg.stderr.read().decode("utf-8", errors="replace") if ffmpeg.stderr else ""
                    return_code = ffmpeg.wait()
                    ffmpeg = None
                    if return_code != 0:
                        raise RuntimeError(f"FFmpeg editable web media render failed: {stderr}")
                else:
                    if progress:
                        progress(
                            OperationProgress.determinate(
                                "web_rendering",
                                completed=0,
                                total=1,
                                unit="frames",
                            )
                        )
                    page.evaluate("time => window.editableMedia.setTime(time)", 0)
                    partial.write_bytes(page.screenshot(type="png", omit_background=True))
                    if progress:
                        progress(
                            OperationProgress.determinate(
                                "web_rendering",
                                completed=1,
                                total=1,
                                unit="frames",
                            )
                        )
                browser.close()
            partial.replace(target.path)
        except Exception:
            if ffmpeg is not None:
                ffmpeg.kill()
                ffmpeg.wait()
            if partial.exists():
                partial.unlink()
            raise
