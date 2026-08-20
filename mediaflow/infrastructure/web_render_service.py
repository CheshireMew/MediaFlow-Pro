from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

from mediaflow.application.ports import TimelineCompilationDocuments
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import AssetFingerprint
from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.web_exports import (
    WebClipExportResult,
    WebExportFormat,
)
from mediaflow.infrastructure.ffmpeg_runner import FfmpegRunner
from mediaflow.infrastructure.file_fingerprint import fingerprint_matches
from mediaflow.infrastructure.project_lock import ProcessFileLock
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.storage_budget import (
    estimate_video_cache_bytes,
    register_project_cache_owner,
    require_project_cache_budget,
)
from mediaflow.infrastructure.web_browser_cache_renderer import WebBrowserCacheRenderer
from mediaflow.infrastructure.web_clip_export_writer import WebClipExportWriter
from mediaflow.infrastructure.web_native_media import slice_web_native_media_plan_for_frame
from mediaflow.infrastructure.web_render_target import (
    WEB_CACHE_MANIFEST_SCHEMA,
    WEB_RENDERER_VERSION,
    WebRenderCache,
    WebRenderTarget,
)


class WebRenderService:
    def __init__(
        self,
        documents: TimelineCompilationDocuments,
        paths: RuntimePaths,
    ) -> None:
        self.documents = documents
        self.paths = paths
        self.ffmpeg = FfmpegRunner(self.paths.ffmpeg)
        self.cache = WebRenderCache(documents, self.paths)
        self.browser_renderer = WebBrowserCacheRenderer(self.paths, self.ffmpeg)
        self.export_writer = WebClipExportWriter(
            self.paths,
            self.ffmpeg,
            self.cache,
            self.render_clip,
        )

    def ensure_sequence(
        self,
        state: TimelineState,
        *,
        progress=None,
        check_cancelled=None,
    ) -> list[Path]:
        assets = {asset.id: asset for asset in self.documents.assets.list_assets()}
        web_clip_ids = [
            clip.id
            for clip in state.clips
            if assets.get(clip.asset_id, None) is not None and assets[clip.asset_id].kind == AssetKind.WEB
        ]
        return self.ensure_clips(
            state,
            web_clip_ids,
            progress=progress,
            check_cancelled=check_cancelled,
        )

    def ensure_clips(
        self,
        state: TimelineState,
        clip_ids: list[str] | tuple[str, ...] | set[str],
        *,
        progress=None,
        check_cancelled=None,
    ) -> list[Path]:
        requested = set(clip_ids)
        ordered = [clip.id for clip in state.clips if clip.id in requested]
        missing = requested - set(ordered)
        if missing:
            raise KeyError(f"Unknown web clip ids: {sorted(missing)}")
        results: list[Path] = []
        for index, clip_id in enumerate(ordered):
            if check_cancelled is not None:
                check_cancelled()
            results.append(
                self.render_clip(
                    state,
                    clip_id,
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            )
            if progress is not None and ordered:
                progress(
                    OperationProgress.determinate(
                        "web_render_items",
                        completed=index + 1,
                        total=len(ordered),
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
        asset = self.documents.assets.get_asset(clip.asset_id)
        target = self.cache.target(state, clip, asset)
        if self._cache_is_ready(target):
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
        self._reserve_cache(target, label="MediaFlow editable web render cache")
        target.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.path.with_name(f"{target.path.name}.lock")
        cache_lock = self._acquire_cache_lock(
            lock_path,
            target,
            check_cancelled=check_cancelled,
        )
        if cache_lock is None:
            return target.path
        try:
            if self._cache_is_ready(target):
                return target.path
            spec = self.documents.web.get_web_asset_spec(asset.id)
            clip_state = state.web_states[clip.id]
            entry = self.documents.assets.resolve_asset_path(asset)
            if not entry.is_file():
                raise FileNotFoundError(entry)
            self.browser_renderer.render(
                entry,
                spec,
                clip_state,
                state,
                target,
                progress=progress,
                check_cancelled=check_cancelled,
            )
            if not self._cache_is_ready(target):
                raise RuntimeError("Editable web media renderer did not produce a cache file")
            return target.path
        finally:
            cache_lock.release()

    def render_filmstrip_source(
        self,
        state: TimelineState,
        clip_id: str,
        source_frame: int,
        *,
        check_cancelled=None,
    ) -> Path:
        """Capture and composite exactly one requested web source frame."""

        try:
            clip = next(item for item in state.clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
        asset = self.documents.assets.get_asset(clip.asset_id)
        target = self.cache.target(state, clip, asset)
        if not 0 <= source_frame < target.frame_count:
            raise ValueError(
                f"Editable media filmstrip frame {source_frame} is outside "
                f"the rendered source range 0..{target.frame_count - 1}"
            )
        source_key = hashlib.sha256(
            f"{target.key}:{source_frame}:{WEB_RENDERER_VERSION}:filmstrip-frame-v1".encode()
        ).hexdigest()
        source_path = (
            self.paths.project_cache_dir(self.documents.project_dir)
            / "filmstrip"
            / "sources"
            / source_key[:2]
            / f"{source_key}.mkv"
        )
        one_frame_target = replace(
            target,
            key=source_key,
            path=source_path,
            animated=True,
            frame_count=1,
            has_audio=False,
            native_media_plan=slice_web_native_media_plan_for_frame(
                target.native_media_plan,
                source_frame=source_frame,
                fps_numerator=target.fps_numerator,
                fps_denominator=target.fps_denominator,
            ),
        )
        if self._cache_is_ready(one_frame_target):
            return one_frame_target.path
        self._reserve_cache(
            one_frame_target,
            label="MediaFlow editable web filmstrip cache",
        )
        one_frame_target.path.parent.mkdir(parents=True, exist_ok=True)
        cache_lock = self._acquire_cache_lock(
            one_frame_target.path.with_name(f"{one_frame_target.path.name}.lock"),
            one_frame_target,
            check_cancelled=check_cancelled,
        )
        if cache_lock is None:
            return one_frame_target.path
        try:
            if self._cache_is_ready(one_frame_target):
                return one_frame_target.path
            spec = self.documents.web.get_web_asset_spec(asset.id)
            clip_state = state.web_states[clip.id]
            entry = self.documents.assets.resolve_asset_path(asset)
            if not entry.is_file():
                raise FileNotFoundError(entry)
            self.browser_renderer.render(
                entry,
                spec,
                clip_state,
                state,
                one_frame_target,
                check_cancelled=check_cancelled,
                capture_start_frame=source_frame,
            )
            if not self._cache_is_ready(one_frame_target):
                raise RuntimeError("Editable web filmstrip renderer did not produce a cache file")
            return one_frame_target.path
        finally:
            cache_lock.release()

    @staticmethod
    def _cache_is_ready(target: WebRenderTarget) -> bool:
        if not target.path.is_file() or not target.manifest_path.is_file():
            return False
        try:
            payload = json.loads(target.manifest_path.read_text(encoding="utf-8"))
            fingerprint_payload = payload["fingerprint"]
            fingerprint = AssetFingerprint.model_validate(fingerprint_payload)
        except (KeyError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return False
        expected = {
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
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            return False
        probe = payload.get("probe")
        if not isinstance(probe, dict):
            return False
        expected_probe = {
            "codec_name": "ffv1" if target.animated else "png",
            "width": target.width,
            "height": target.height,
            "frame_count": target.frame_count if target.animated else 1,
            "has_audio": target.has_audio,
            "audio_codec_name": "flac" if target.has_audio else None,
            "audio_sample_rate": (target.audio_sample_rate if target.has_audio else None),
            "audio_channels": target.audio_channels if target.has_audio else None,
        }
        if any(probe.get(key) != value for key, value in expected_probe.items()):
            return False
        if target.animated and (
            probe.get("pixel_format") != "bgra"
            or probe.get("fps_numerator") != target.fps_numerator
            or probe.get("fps_denominator") != target.fps_denominator
        ):
            return False
        return fingerprint_matches(target.path, fingerprint)

    def _reserve_cache(self, target: WebRenderTarget, *, label: str) -> None:
        cache_root = self.paths.project_cache_dir(self.documents.project_dir)
        require_project_cache_budget(
            cache_root,
            expected_new_bytes=estimate_video_cache_bytes(
                target.width,
                target.height,
                target.frame_count,
            ),
            label=label,
        )
        register_project_cache_owner(
            cache_root,
            self.documents.project_dir,
            case_sensitive_paths=self.paths.target.case_sensitive_paths,
        )

    @classmethod
    def _acquire_cache_lock(
        cls,
        lock_path: Path,
        target: WebRenderTarget,
        *,
        check_cancelled=None,
    ) -> ProcessFileLock | None:
        deadline = time.monotonic() + 900
        lock = ProcessFileLock(lock_path)
        while True:
            if cls._cache_is_ready(target):
                return None
            if lock.acquire():
                return lock
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for editable media cache: {target.path}") from None
            if check_cancelled is not None:
                check_cancelled()
            time.sleep(0.1)

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
        return self.export_writer.export(
            state,
            clip_id,
            output_path,
            format,
            time_ms=time_ms,
            background=background,
            overwrite=overwrite,
            progress=progress,
            check_cancelled=check_cancelled,
        )
