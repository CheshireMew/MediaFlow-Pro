from __future__ import annotations

import time
from typing import Literal

from .web_direct_h264_models import (
    DirectH264FallbackRequired,
    EncoderTraceEvidence,
)


class EncoderTraceAttestation:
    """Collect bounded Chromium evidence for the encoder selected by WebCodecs."""

    def __init__(self, browser, *, frame_limit: int) -> None:
        self._session = browser.new_browser_cdp_session()
        self._frame_limit = frame_limit
        self._active = False
        self._completed = False
        self._storage_type: Literal[
            "SharedMemory", "GpuMemoryBuffer", "unknown"
        ] = "unknown"
        self._backend_names: set[str] = set()
        self._trace_event_count = 0
        self._platform_encode_events = 0
        self._platform_output_events = 0
        self._gpu_readback_events = 0
        self._session.on("Tracing.dataCollected", self._record_batch)
        self._session.on("Tracing.tracingComplete", self._record_complete)

    def _record_batch(self, payload: dict[str, object]) -> None:
        values = payload.get("value")
        if not isinstance(values, list):
            return
        for event in values:
            if not isinstance(event, dict):
                continue
            self._trace_event_count += 1
            name = str(event.get("name") or "")
            if name.startswith("MediaFoundationVideoEncodeAccelerator::"):
                operation = name.rsplit("::", 1)[-1]
                if operation in {"PopulateInputSampleBuffer", "ProcessInput", "ProcessOutput"}:
                    self._backend_names.add("MediaFoundationVideoEncodeAccelerator")
                if operation == "ProcessInput":
                    self._platform_encode_events += 1
                elif operation == "ProcessOutput":
                    self._platform_output_events += 1
            if name == "D3DImageBacking::ReadbackToMemoryAsync":
                self._gpu_readback_events += 1

    def _record_complete(self, _payload: dict[str, object]) -> None:
        self._completed = True

    def start(self) -> None:
        try:
            self._session.send(
                "Tracing.start",
                {
                    "categories": "media,gpu",
                    "options": "record-as-much-as-possible",
                    "transferMode": "ReportEvents",
                },
            )
        except BaseException as error:
            raise DirectH264FallbackRequired(
                f"Chromium encoder attestation could not start: {error}"
            ) from error
        self._active = True

    def finish(self, page) -> EncoderTraceEvidence:
        if not self._active:
            raise DirectH264FallbackRequired("Chromium encoder attestation was not active")
        try:
            self._session.send("Tracing.end")
            deadline = time.monotonic() + 5
            while not self._completed and time.monotonic() < deadline:
                page.wait_for_timeout(10)
            if not self._completed:
                raise DirectH264FallbackRequired(
                    "Chromium encoder attestation did not finish within five seconds"
                )
        finally:
            self._active = False
            try:
                self._session.detach()
            except BaseException:
                pass
        if len(self._backend_names) != 1:
            raise DirectH264FallbackRequired(
                "Chromium trace did not identify one platform encoder backend"
            )
        backend = next(iter(self._backend_names))
        hardware_verified = (
            backend == "MediaFoundationVideoEncodeAccelerator"
            and self._platform_encode_events >= 1
            and self._platform_output_events >= 1
        )
        if not hardware_verified:
            raise DirectH264FallbackRequired(
                "Chromium did not prove a Windows Media Foundation hardware encoder"
            )
        if self._gpu_readback_events > 0:
            input_copy_path: Literal[
                "gpu-readback-to-shared-memory",
                "gpu-readback-to-memory",
                "gpu-native-unverified",
                "gpu-native-zero-copy",
                "unknown",
            ] = (
                "gpu-readback-to-shared-memory"
                if self._storage_type == "SharedMemory"
                else "gpu-readback-to-memory"
            )
        elif self._storage_type == "GpuMemoryBuffer":
            input_copy_path = "gpu-native-unverified"
        else:
            input_copy_path = "unknown"
        return EncoderTraceEvidence(
            actual_encoder_name=backend,
            actual_encoder_type="hardware",
            encoder_storage_type=self._storage_type,
            input_copy_path=input_copy_path,
            attested_frames=max(
                1,
                min(
                    self._frame_limit,
                    self._platform_encode_events,
                    self._platform_output_events,
                ),
            ),
            trace_event_count=self._trace_event_count,
            platform_encode_events=self._platform_encode_events,
            platform_output_events=self._platform_output_events,
            gpu_readback_events=self._gpu_readback_events,
            hardware_acceleration_verified=hardware_verified,
            zero_copy_verified=False,
        )

    def abort(self) -> None:
        if not self._active:
            return
        self._active = False
        try:
            self._session.send("Tracing.end")
        except BaseException:
            pass
        try:
            self._session.detach()
        except BaseException:
            pass
