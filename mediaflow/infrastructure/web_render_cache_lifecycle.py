from __future__ import annotations

import json
import re
import time
from pathlib import Path

from mediaflow.application.ports import TimelineCompilationDocuments
from mediaflow.domain.project import AssetFingerprint
from mediaflow.domain.web_rendering import WebRenderActualCapture, WebRenderPlan

from .file_fingerprint import fingerprint_matches
from .project_lock import ProcessFileLock
from .runtime_paths import RuntimePaths
from .storage_budget import estimate_video_cache_bytes, reserve_project_cache
from .web_render_target import (
    WEB_CACHE_MANIFEST_SCHEMA,
    WEB_RENDERER_VERSION,
    WebRenderTarget,
)


class WebRenderCacheLifecycle:
    def __init__(
        self,
        documents: TimelineCompilationDocuments,
        paths: RuntimePaths,
    ) -> None:
        self.documents = documents
        self.paths = paths

    @staticmethod
    def cache_is_ready(
        target: WebRenderTarget,
        render_plan: WebRenderPlan | None = None,
    ) -> bool:
        if not target.path.is_file() or not target.manifest_path.is_file():
            return False
        try:
            payload = json.loads(target.manifest_path.read_text(encoding="utf-8"))
            fingerprint = AssetFingerprint.model_validate(payload["fingerprint"])
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
        capture = payload.get("capture")
        if not WebRenderCacheLifecycle._capture_contract_is_valid(
            capture,
            target,
            render_plan,
        ):
            return False
        assert isinstance(capture, dict)
        try:
            actual_capture = WebRenderActualCapture(
                backend=capture["actual_backend"],
                reason=capture["actual_reason"],
                fallback_reason=capture.get("fallback_reason"),
                worker_count=capture["worker_count"],
                captured_frames=capture["captured_frames"],
                elapsed_seconds=capture["elapsed_seconds"],
                encoder=capture.get("encoder"),
            )
        except (TypeError, ValueError):
            return False
        if not WebRenderCacheLifecycle._encoder_contract_is_valid(
            actual_capture,
            capture,
            target,
        ):
            return False
        if not WebRenderCacheLifecycle._probe_contract_is_valid(
            payload.get("probe"),
            actual_capture,
            target,
        ):
            return False
        return fingerprint_matches(target.path, fingerprint)

    @staticmethod
    def _capture_contract_is_valid(
        capture: object,
        target: WebRenderTarget,
        render_plan: WebRenderPlan | None,
    ) -> bool:
        if (
            not isinstance(capture, dict)
            or not re.fullmatch(r"[a-f0-9]{64}", str(capture.get("plan_digest") or ""))
            or capture.get("planned_mode") not in {"auto", "screenshot"}
            or capture.get("planned_backend") not in {"webcodecs-h264", "frame-pipe"}
            or capture.get("fallback_backend") not in {None, "frame-pipe"}
            or not isinstance(capture.get("backend_selection_reasons"), list)
            or not capture["backend_selection_reasons"]
            or capture.get("actual_backend")
            not in {"webcodecs-h264", "drawelement", "screenshot"}
            or not isinstance(capture.get("actual_reason"), str)
            or not capture["actual_reason"]
            or not isinstance(capture.get("worker_count"), int)
            or capture["worker_count"] < 1
            or capture.get("captured_frames") != (target.frame_count if target.animated else 1)
        ):
            return False
        return render_plan is None or (
            capture.get("plan_digest") == render_plan.plan_digest
            and capture.get("planned_backend") == render_plan.planned_backend
            and capture.get("fallback_backend") == render_plan.fallback_backend
        )

    @staticmethod
    def _encoder_contract_is_valid(
        actual: WebRenderActualCapture,
        capture: dict,
        target: WebRenderTarget,
    ) -> bool:
        if actual.backend != "webcodecs-h264":
            return actual.encoder is None
        encoder = actual.encoder
        return bool(
            capture.get("planned_backend") == "webcodecs-h264"
            and encoder is not None
            and not target.native_media_plan.video_segments
            and encoder.maximum_encode_queue_size <= 4
            and encoder.maximum_pending_writes <= 4
            and encoder.timestamps_monotonic
            and encoder.exact_frame_time_boundaries
            and encoder.attestation_method == "chromium-trace"
            and encoder.hardware_acceleration_verified
            and encoder.actual_encoder_name == "MediaFoundationVideoEncodeAccelerator"
            and encoder.actual_encoder_type == "hardware"
            and encoder.attested_frames <= 8
            and encoder.platform_encode_events >= encoder.attested_frames
            and encoder.platform_output_events >= encoder.attested_frames
            and (
                encoder.input_copy_path
                not in {"gpu-readback-to-shared-memory", "gpu-readback-to-memory"}
                or (
                    encoder.gpu_readback_events >= encoder.attested_frames
                    and not encoder.zero_copy_verified
                )
            )
        )

    @staticmethod
    def _probe_contract_is_valid(
        probe: object,
        actual: WebRenderActualCapture,
        target: WebRenderTarget,
    ) -> bool:
        if not isinstance(probe, dict):
            return False
        expected = {
            "codec_name": (
                "h264"
                if actual.backend == "webcodecs-h264"
                else "ffv1"
                if target.animated
                else "png"
            ),
            "width": target.width,
            "height": target.height,
            "frame_count": target.frame_count if target.animated else 1,
            "has_audio": target.has_audio,
            "audio_codec_name": "flac" if target.has_audio else None,
            "audio_sample_rate": target.audio_sample_rate if target.has_audio else None,
            "audio_channels": target.audio_channels if target.has_audio else None,
            "packet_pts_monotonic": True,
            "packet_dts_monotonic": True,
        }
        if any(probe.get(key) != value for key, value in expected.items()):
            return False
        if target.animated and (
            (
                actual.backend == "webcodecs-h264"
                and probe.get("pixel_format") not in {"yuv420p", "yuvj420p"}
            )
            or (
                actual.backend != "webcodecs-h264"
                and probe.get("pixel_format") != "bgra"
            )
            or probe.get("fps_numerator") != target.fps_numerator
            or probe.get("fps_denominator") != target.fps_denominator
        ):
            return False
        if actual.backend == "webcodecs-h264" and (
            probe.get("color_space"),
            probe.get("color_transfer"),
            probe.get("color_primaries"),
        ) != ("bt709", "bt709", "bt709"):
            return False
        clock_error = probe.get("maximum_video_clock_error_microseconds")
        if target.animated:
            if not isinstance(clock_error, int) or not 0 <= clock_error <= 1_000:
                return False
        elif clock_error is not None:
            return False
        drift = probe.get("audio_video_end_drift_microseconds")
        if target.has_audio:
            maximum_drift = round(
                target.fps_denominator * 1_000_000 / target.fps_numerator
            )
            if not isinstance(drift, int) or not 0 <= drift <= maximum_drift:
                return False
        elif drift is not None:
            return False
        return True

    def reserve(self, target: WebRenderTarget, *, label: str) -> None:
        cache_root = self.paths.project_cache_dir(self.documents.project_dir)
        reserve_project_cache(
            cache_root,
            self.documents.project_dir,
            expected_new_bytes=estimate_video_cache_bytes(
                target.width,
                target.height,
                target.frame_count,
            ),
            label=label,
            case_sensitive_paths=self.paths.target.case_sensitive_paths,
        )

    @classmethod
    def acquire_lock(
        cls,
        lock_path: Path,
        target: WebRenderTarget,
        render_plan: WebRenderPlan,
        *,
        check_cancelled=None,
    ) -> ProcessFileLock | None:
        deadline = time.monotonic() + 900
        lock = ProcessFileLock(lock_path)
        while True:
            if cls.cache_is_ready(target, render_plan):
                return None
            if lock.acquire():
                return lock
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for editable media cache: {target.path}"
                ) from None
            if check_cancelled is not None:
                check_cancelled()
            time.sleep(0.1)
