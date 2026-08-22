from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from mediaflow.application.ports import TimelineCompilationDocuments
from mediaflow.domain.timeline import TimelineState

from .runtime_paths import RuntimePaths
from .web_browser_cache_renderer import WebBrowserCacheRenderer
from .web_native_media import slice_web_native_media_plan_for_frame
from .web_render_cache_lifecycle import WebRenderCacheLifecycle
from .web_render_preflight import build_web_render_plan
from .web_render_target import WEB_RENDERER_VERSION, WebRenderCache


class WebFilmstripRenderer:
    """Render one deterministic source frame for the timeline filmstrip."""

    def __init__(
        self,
        documents: TimelineCompilationDocuments,
        paths: RuntimePaths,
        cache: WebRenderCache,
        cache_lifecycle: WebRenderCacheLifecycle,
        browser_renderer: WebBrowserCacheRenderer,
    ) -> None:
        self.documents = documents
        self.paths = paths
        self.cache = cache
        self.cache_lifecycle = cache_lifecycle
        self.browser_renderer = browser_renderer

    def render(
        self,
        state: TimelineState,
        clip_id: str,
        source_frame: int,
        *,
        check_cancelled=None,
    ) -> Path:
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
        spec = self.documents.web.get_web_asset_spec(asset.id)
        clip_state = state.web_states[clip.id]
        entry = self.documents.assets.resolve_asset_path(asset)
        if not entry.is_file():
            raise FileNotFoundError(entry)
        full_render_plan = build_web_render_plan(
            entry=entry,
            spec=spec,
            clip_state=clip_state,
            state=state,
            target=target,
        )
        render_plan = build_web_render_plan(
            entry=entry,
            spec=spec,
            clip_state=clip_state,
            state=state,
            target=one_frame_target,
            capture_start_frame=source_frame,
        )
        if self.cache_lifecycle.cache_is_ready(target, full_render_plan):
            return target.path
        if self.cache_lifecycle.cache_is_ready(one_frame_target, render_plan):
            return one_frame_target.path
        self.cache_lifecycle.reserve(
            one_frame_target,
            label="MediaFlow editable web filmstrip cache",
        )
        one_frame_target.path.parent.mkdir(parents=True, exist_ok=True)
        cache_lock = self.cache_lifecycle.acquire_lock(
            one_frame_target.path.with_name(f"{one_frame_target.path.name}.lock"),
            one_frame_target,
            render_plan,
            check_cancelled=check_cancelled,
        )
        if cache_lock is None:
            return one_frame_target.path
        try:
            if self.cache_lifecycle.cache_is_ready(target, full_render_plan):
                return target.path
            if self.cache_lifecycle.cache_is_ready(one_frame_target, render_plan):
                return one_frame_target.path
            self.browser_renderer.render(
                entry,
                spec,
                clip_state,
                state,
                one_frame_target,
                render_plan,
                check_cancelled=check_cancelled,
                capture_start_frame=source_frame,
            )
            if not self.cache_lifecycle.cache_is_ready(one_frame_target, render_plan):
                raise RuntimeError("Editable web filmstrip renderer did not produce a cache file")
            return one_frame_target.path
        finally:
            cache_lock.release()
