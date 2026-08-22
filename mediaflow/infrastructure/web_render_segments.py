from __future__ import annotations

import hashlib
from dataclasses import replace
from math import ceil
from pathlib import Path

from mediaflow.application.ports import TimelineCompilationDocuments
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.timeline import TimelineState
from mediaflow.domain.web_manifest import WebAssetSpec
from mediaflow.domain.web_rendering import WebRenderPlan
from mediaflow.domain.web_state import WebClipState

from .runtime_paths import RuntimePaths
from .web_browser_cache_renderer import WebBrowserCacheRenderer
from .web_native_media import WebNativeMediaPlan
from .web_render_cache_lifecycle import WebRenderCacheLifecycle
from .web_render_preflight import build_web_render_plan
from .web_render_target import WebRenderTarget


class WebRenderSegments:
    def __init__(
        self,
        documents: TimelineCompilationDocuments,
        paths: RuntimePaths,
        renderer: WebBrowserCacheRenderer,
    ) -> None:
        self.documents = documents
        self.paths = paths
        self.renderer = renderer
        self.lifecycle = WebRenderCacheLifecycle(documents, paths)

    @staticmethod
    def frame_count(target: WebRenderTarget, *, segment_seconds: float) -> int:
        return max(
            1,
            ceil(segment_seconds * target.fps_numerator / target.fps_denominator),
        )

    @classmethod
    def can_render(
        cls,
        target: WebRenderTarget,
        render_plan: WebRenderPlan,
        *,
        segment_seconds: float,
    ) -> bool:
        return (
            target.animated
            and render_plan.planned_backend == "frame-pipe"
            and target.frame_count
            > cls.frame_count(target, segment_seconds=segment_seconds)
            and not target.has_audio
            and not target.native_media_plan.video_segments
            and not target.native_media_plan.audio_segments
        )

    def targets(
        self,
        target: WebRenderTarget,
        *,
        segment_seconds: float,
    ) -> list[tuple[int, WebRenderTarget]]:
        segment_frames = self.frame_count(target, segment_seconds=segment_seconds)
        cache_root = (
            self.paths.project_cache_dir(self.documents.project_dir)
            / "web"
            / "segments"
        )
        empty_native_plan = WebNativeMediaPlan(video_segments=(), audio_segments=())
        result: list[tuple[int, WebRenderTarget]] = []
        for start_frame in range(0, target.frame_count, segment_frames):
            frame_count = min(segment_frames, target.frame_count - start_frame)
            key = hashlib.sha256(
                (
                    f"{target.segment_namespace}:lossless-v1:"
                    f"{start_frame}:{frame_count}"
                ).encode()
            ).hexdigest()
            result.append(
                (
                    start_frame,
                    replace(
                        target,
                        key=key,
                        path=cache_root / key[:2] / f"{key[:32]}.mkv",
                        frame_count=frame_count,
                        has_audio=False,
                        native_media_plan=empty_native_plan,
                    ),
                )
            )
        return result

    def render(
        self,
        entry: Path,
        spec: WebAssetSpec,
        clip_state: WebClipState,
        state: TimelineState,
        target: WebRenderTarget,
        render_plan: WebRenderPlan,
        *,
        segment_seconds: float,
        progress,
        check_cancelled,
    ) -> None:
        completed: list[tuple[WebRenderTarget, WebRenderPlan, bool]] = []
        for start_frame, segment in self.targets(
            target,
            segment_seconds=segment_seconds,
        ):
            if check_cancelled is not None:
                check_cancelled()
            segment_plan = build_web_render_plan(
                entry=entry,
                spec=spec,
                clip_state=clip_state,
                state=state,
                target=segment,
                capture_start_frame=start_frame,
            )
            reused = self.lifecycle.cache_is_ready(segment, segment_plan)
            if not reused:
                reused = self._render_missing_segment(
                    entry,
                    spec,
                    clip_state,
                    state,
                    target,
                    segment,
                    segment_plan,
                    start_frame=start_frame,
                    progress=progress,
                    check_cancelled=check_cancelled,
                )
            if not self.lifecycle.cache_is_ready(segment, segment_plan):
                raise RuntimeError("Editable web segment renderer did not produce a cache file")
            completed.append((segment, segment_plan, reused))
            if progress is not None:
                progress(
                    OperationProgress.determinate(
                        "web_rendering",
                        completed=start_frame + segment.frame_count,
                        total=target.frame_count,
                        unit="frames",
                    )
                )
        self.renderer.compose_segments(
            target,
            render_plan,
            completed,
            check_cancelled=check_cancelled,
        )

    def _render_missing_segment(
        self,
        entry: Path,
        spec: WebAssetSpec,
        clip_state: WebClipState,
        state: TimelineState,
        target: WebRenderTarget,
        segment: WebRenderTarget,
        segment_plan: WebRenderPlan,
        *,
        start_frame: int,
        progress,
        check_cancelled,
    ) -> bool:
        self.lifecycle.reserve(segment, label="MediaFlow editable web segment cache")
        segment.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.lifecycle.acquire_lock(
            segment.path.with_name(f"{segment.path.name}.lock"),
            segment,
            segment_plan,
            check_cancelled=check_cancelled,
        )
        if lock is None:
            return True
        try:
            if self.lifecycle.cache_is_ready(segment, segment_plan):
                return True
            self.renderer.render(
                entry,
                spec,
                clip_state,
                state,
                segment,
                segment_plan,
                progress=self._progress_adapter(
                    progress,
                    start_frame=start_frame,
                    total_frames=target.frame_count,
                ),
                check_cancelled=check_cancelled,
                capture_start_frame=start_frame,
            )
            return False
        finally:
            lock.release()

    @staticmethod
    def _progress_adapter(progress, *, start_frame: int, total_frames: int):
        if progress is None:
            return None

        def report(value: OperationProgress) -> None:
            if (
                value.mode == "determinate"
                and value.message_code == "web_rendering"
                and value.completed is not None
            ):
                progress(
                    OperationProgress.determinate(
                        "web_rendering",
                        completed=min(total_frames, start_frame + value.completed),
                        total=total_frames,
                        unit="frames",
                    )
                )
            elif value.mode == "indeterminate":
                progress(value)

        return report
