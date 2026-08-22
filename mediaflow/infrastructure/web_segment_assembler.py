from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

from mediaflow.atomic_file import atomic_write_text, unique_temporary_sibling
from mediaflow.domain.web_rendering import WebRenderActualCapture, WebRenderPlan

from .ffmpeg_runner import FfmpegRunner
from .web_render_manifest import publish_web_render_manifest
from .web_render_probe import WebRenderProbe
from .web_render_target import WebRenderTarget


class WebSegmentAssembler:
    """Atomically assemble validated lossless browser-rendered frame segments."""

    def __init__(self, ffmpeg: FfmpegRunner, probe: WebRenderProbe) -> None:
        self.ffmpeg = ffmpeg
        self.probe = probe

    def compose(
        self,
        target: WebRenderTarget,
        render_plan: WebRenderPlan,
        segments: list[tuple[WebRenderTarget, WebRenderPlan, bool]],
        *,
        check_cancelled=None,
    ) -> None:
        if not segments:
            raise ValueError("Editable web segment assembly requires at least one segment")
        if (
            target.has_audio
            or target.native_media_plan.video_segments
            or target.native_media_plan.audio_segments
        ):
            raise ValueError("Editable web segment assembly only accepts browser-only video")
        if sum(item.frame_count for item, _, _ in segments) != target.frame_count:
            raise ValueError("Editable web segments do not cover the complete target")

        actual_segments = [
            self._read_actual_capture(segment, segment_plan)
            for segment, segment_plan, _ in segments
        ]
        if any(item.backend == "webcodecs-h264" for item in actual_segments):
            raise ValueError("Direct H.264 caches cannot be mixed into lossless web segments")

        started = time.perf_counter()
        partial = unique_temporary_sibling(target.path, label="web-segments")
        concat_path = unique_temporary_sibling(
            target.path.with_suffix(".concat.txt"),
            label="web-segments",
        )
        reused_count = sum(reused for _, _, reused in segments)
        try:
            atomic_write_text(
                concat_path,
                "\n".join(
                    f"file '{self._concat_path(segment.path)}'"
                    for segment, _, _ in segments
                )
                + "\n",
            )
            result = self.ffmpeg.run(
                [
                    "-loglevel",
                    "error",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    concat_path,
                    "-map",
                    "0:v:0",
                    "-c",
                    "copy",
                    "-fflags",
                    "+genpts",
                    "-y",
                    partial,
                ],
                check_cancelled=check_cancelled,
                timeout=1800,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"FFmpeg editable web segment assembly failed: {result.stderr.strip()}"
                )
            backends = {item.backend for item in actual_segments}
            backend: Literal["drawelement", "screenshot"] = (
                "screenshot" if "screenshot" in backends else "drawelement"
            )
            reasons = list(dict.fromkeys(item.reason for item in actual_segments))
            fallback_reasons = list(
                dict.fromkeys(
                    item.fallback_reason
                    for item in actual_segments
                    if item.fallback_reason
                )
            )
            actual_capture = WebRenderActualCapture(
                backend=backend,
                reason=(
                    f"assembled {len(segments)} validated lossless frame segments; "
                    f"{reused_count} reused; "
                    + "; ".join(reasons)
                ),
                fallback_reason="; ".join(fallback_reasons) or None,
                worker_count=max(item.worker_count for item in actual_segments),
                captured_frames=target.frame_count,
                elapsed_seconds=(
                    time.perf_counter()
                    - started
                    + sum(
                        actual.elapsed_seconds
                        for actual, (_, _, reused) in zip(
                            actual_segments,
                            segments,
                            strict=True,
                        )
                        if not reused
                    )
                ),
            )
            probe = self.probe.validate(partial, target, actual_capture)
            partial.replace(target.path)
            publish_web_render_manifest(
                target,
                probe,
                render_plan,
                actual_capture,
                segmentation={
                    "schema": "mediaflow-web-render-segments/v1",
                    "segment_count": len(segments),
                    "rendered_segment_count": len(segments) - reused_count,
                    "reused_segment_count": reused_count,
                    "segments": [
                        {
                            "key": segment.key,
                            "frame_count": segment.frame_count,
                            "reused": reused,
                        }
                        for segment, _, reused in segments
                    ],
                },
            )
        finally:
            partial.unlink(missing_ok=True)
            concat_path.unlink(missing_ok=True)

    @staticmethod
    def _read_actual_capture(
        target: WebRenderTarget,
        render_plan: WebRenderPlan,
    ) -> WebRenderActualCapture:
        try:
            payload = json.loads(target.manifest_path.read_text(encoding="utf-8"))
            capture = payload["capture"]
            if capture["plan_digest"] != render_plan.plan_digest:
                raise ValueError("segment plan digest changed")
            return WebRenderActualCapture(
                backend=capture["actual_backend"],
                reason=capture["actual_reason"],
                fallback_reason=capture.get("fallback_reason"),
                worker_count=capture["worker_count"],
                captured_frames=capture["captured_frames"],
                elapsed_seconds=capture["elapsed_seconds"],
                encoder=capture.get("encoder"),
            )
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("Editable web segment has invalid capture evidence") from error

    @staticmethod
    def _concat_path(path: Path) -> str:
        return str(path.resolve()).replace("\\", "/").replace("'", "'\\''")
