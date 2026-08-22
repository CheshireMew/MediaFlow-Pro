from __future__ import annotations

import json

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.web_rendering import WebRenderActualCapture, WebRenderPlan

from .file_fingerprint import fingerprint_file
from .web_render_target import (
    WEB_CACHE_MANIFEST_SCHEMA,
    WEB_RENDERER_VERSION,
    WebRenderTarget,
)


def publish_web_render_manifest(
    target: WebRenderTarget,
    probe: dict[str, object],
    render_plan: WebRenderPlan,
    actual_capture: WebRenderActualCapture,
    *,
    segmentation: dict[str, object] | None = None,
) -> None:
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
        "capture": {
            "plan_digest": render_plan.plan_digest,
            "planned_mode": render_plan.capture_mode,
            "strategy": render_plan.strategy,
            "static_compatibility": render_plan.static_compatibility,
            "verification_frames": [
                item.model_dump(mode="json") for item in render_plan.verification_frames
            ],
            "planned_backend": render_plan.planned_backend,
            "fallback_backend": render_plan.fallback_backend,
            "backend_selection_reasons": render_plan.backend_selection_reasons,
            "actual_backend": actual_capture.backend,
            "actual_reason": actual_capture.reason,
            "fallback_reason": actual_capture.fallback_reason,
            "worker_count": actual_capture.worker_count,
            "captured_frames": actual_capture.captured_frames,
            "elapsed_seconds": actual_capture.elapsed_seconds,
            "encoder": (
                actual_capture.encoder.model_dump(mode="json")
                if actual_capture.encoder is not None
                else None
            ),
        },
        "fingerprint": fingerprint_file(target.path).model_dump(mode="json"),
        "probe": probe,
    }
    if segmentation is not None:
        payload["segmentation"] = segmentation
    atomic_write_text(
        target.manifest_path,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
