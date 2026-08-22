from __future__ import annotations

import json
from pathlib import Path

from mediaflow.application.ports import TimelineCompilationDocuments
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.web_exports import WebClipExportResult, WebExportFormat
from mediaflow.domain.web_rendering import WebRenderActualCapture, WebRenderPlan
from mediaflow.infrastructure.ffmpeg_runner import FfmpegRunner
from mediaflow.infrastructure.project_lock import ProcessFileLock
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.web_browser_cache_renderer import WebBrowserCacheRenderer
from mediaflow.infrastructure.web_capture_prewarm import prewarm_web_capture_engine
from mediaflow.infrastructure.web_clip_export_writer import WebClipExportWriter
from mediaflow.infrastructure.web_filmstrip_renderer import WebFilmstripRenderer
from mediaflow.infrastructure.web_render_cache_lifecycle import WebRenderCacheLifecycle
from mediaflow.infrastructure.web_render_preflight import build_web_render_plan
from mediaflow.infrastructure.web_render_segments import WebRenderSegments
from mediaflow.infrastructure.web_render_target import WebRenderCache, WebRenderTarget


class WebRenderService:
    _SEGMENT_SECONDS = 10

    def __init__(
        self,
        documents: TimelineCompilationDocuments,
        paths: RuntimePaths,
    ) -> None:
        self.documents = documents
        self.paths = paths
        self.ffmpeg = FfmpegRunner(self.paths.ffmpeg)
        self.cache = WebRenderCache(documents, self.paths)
        self.cache_lifecycle = WebRenderCacheLifecycle(documents, self.paths)
        self.browser_renderer = WebBrowserCacheRenderer(self.paths, self.ffmpeg)
        self.segments = WebRenderSegments(documents, self.paths, self.browser_renderer)
        self.filmstrip = WebFilmstripRenderer(
            documents,
            paths,
            self.cache,
            self.cache_lifecycle,
            self.browser_renderer,
        )
        self.export_writer = WebClipExportWriter(
            self.paths,
            self.ffmpeg,
            self.cache,
            self.render_clip,
        )
        chromium = self.paths.chromium
        if (
            chromium is not None
            and chromium.is_file()
            and any(asset.kind == AssetKind.WEB for asset in documents.assets.list_assets())
        ):
            prewarm_web_capture_engine(chromium)

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
        spec = self.documents.web.get_web_asset_spec(asset.id)
        clip_state = state.web_states[clip.id]
        entry = self.documents.assets.resolve_asset_path(asset)
        if not entry.is_file():
            raise FileNotFoundError(entry)
        render_plan = build_web_render_plan(
            entry=entry,
            spec=spec,
            clip_state=clip_state,
            state=state,
            target=target,
        )
        if self._cache_is_ready(target, render_plan):
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
            render_plan,
            check_cancelled=check_cancelled,
        )
        if cache_lock is None:
            return target.path
        try:
            if self._cache_is_ready(target, render_plan):
                return target.path
            if self.segments.can_render(
                target,
                render_plan,
                segment_seconds=self._SEGMENT_SECONDS,
            ):
                self.segments.render(
                    entry,
                    spec,
                    clip_state,
                    state,
                    target,
                    render_plan,
                    segment_seconds=self._SEGMENT_SECONDS,
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            else:
                self.browser_renderer.render(
                    entry,
                    spec,
                    clip_state,
                    state,
                    target,
                    render_plan,
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            if not self._cache_is_ready(target, render_plan):
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
        return self.filmstrip.render(
            state,
            clip_id,
            source_frame,
            check_cancelled=check_cancelled,
        )

    @classmethod
    def _can_render_segments(
        cls,
        target: WebRenderTarget,
        render_plan: WebRenderPlan,
    ) -> bool:
        return WebRenderSegments.can_render(
            target,
            render_plan,
            segment_seconds=cls._SEGMENT_SECONDS,
        )

    @classmethod
    def _segment_frame_count(cls, target: WebRenderTarget) -> int:
        return WebRenderSegments.frame_count(
            target,
            segment_seconds=cls._SEGMENT_SECONDS,
        )

    def _segment_targets(
        self,
        target: WebRenderTarget,
    ) -> list[tuple[int, WebRenderTarget]]:
        return self.segments.targets(
            target,
            segment_seconds=self._SEGMENT_SECONDS,
        )

    def inspect_clip_render(
        self,
        state: TimelineState,
        clip_id: str,
    ) -> WebRenderPlan:
        try:
            clip = next(item for item in state.clips if item.id == clip_id)
        except StopIteration as error:
            raise KeyError(clip_id) from error
        asset = self.documents.assets.get_asset(clip.asset_id)
        target = self.cache.target(state, clip, asset)
        spec = self.documents.web.get_web_asset_spec(asset.id)
        clip_state = state.web_states[clip.id]
        entry = self.documents.assets.resolve_asset_path(asset)
        if not entry.is_file():
            raise FileNotFoundError(entry)
        plan = build_web_render_plan(
            entry=entry,
            spec=spec,
            clip_state=clip_state,
            state=state,
            target=target,
        )
        if not self._cache_is_ready(target, plan):
            return plan
        try:
            payload = json.loads(target.manifest_path.read_text(encoding="utf-8"))
            capture = payload["capture"]
            if capture.get("plan_digest") != plan.plan_digest:
                return plan
            actual_capture = WebRenderActualCapture(
                backend=capture["actual_backend"],
                reason=capture["actual_reason"],
                fallback_reason=capture.get("fallback_reason"),
                worker_count=capture["worker_count"],
                captured_frames=capture["captured_frames"],
                elapsed_seconds=capture["elapsed_seconds"],
                encoder=capture.get("encoder"),
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return plan
        return plan.model_copy(
            update={
                "cache_status": "ready",
                "actual_capture": actual_capture,
            }
        )

    @staticmethod
    def _cache_is_ready(
        target: WebRenderTarget,
        render_plan: WebRenderPlan | None = None,
    ) -> bool:
        return WebRenderCacheLifecycle.cache_is_ready(target, render_plan)

    def _reserve_cache(self, target: WebRenderTarget, *, label: str) -> None:
        self.cache_lifecycle.reserve(target, label=label)

    @classmethod
    def _acquire_cache_lock(
        cls,
        lock_path: Path,
        target: WebRenderTarget,
        render_plan: WebRenderPlan,
        *,
        check_cancelled=None,
    ) -> ProcessFileLock | None:
        return WebRenderCacheLifecycle.acquire_lock(
            lock_path,
            target,
            render_plan,
            check_cancelled=check_cancelled,
        )

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
