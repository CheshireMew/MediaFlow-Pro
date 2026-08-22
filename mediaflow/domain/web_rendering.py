from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from .model_base import DomainModel


class WebRenderCompatibilityFinding(DomainModel):
    code: str = Field(min_length=1)
    severity: Literal["blocking", "warning"]
    source: Literal["entry-html", "declared-resource"]
    path: str = Field(min_length=1)
    line: int = Field(ge=1)
    message: str = Field(min_length=1)


class WebRenderVerificationFrame(DomainModel):
    frame_index: int = Field(ge=0)
    time_seconds: float = Field(ge=0)


class WebRenderEncoderTelemetry(DomainModel):
    codec: str = Field(min_length=1)
    browser_version: str = Field(min_length=1)
    requested_hardware_acceleration: Literal["prefer-hardware"]
    hardware_acceleration_verified: bool = False
    zero_copy_verified: bool = False
    attestation_method: Literal["chromium-trace"]
    actual_encoder_name: str = Field(min_length=1)
    actual_encoder_type: Literal["hardware", "software", "unknown"]
    encoder_storage_type: Literal["SharedMemory", "GpuMemoryBuffer", "unknown"]
    input_copy_path: Literal[
        "gpu-readback-to-shared-memory",
        "gpu-readback-to-memory",
        "gpu-native-unverified",
        "gpu-native-zero-copy",
        "unknown",
    ]
    attested_frames: int = Field(ge=1)
    trace_event_count: int = Field(ge=1)
    platform_encode_events: int = Field(ge=1)
    platform_output_events: int = Field(ge=1)
    gpu_readback_events: int = Field(ge=0)
    input_surface: Literal["canvas-videoframe"]
    requested_config: dict[str, object]
    accepted_config: dict[str, object]
    gpu: dict[str, object] | None = None
    encoded_chunks: int = Field(ge=1)
    encoded_bytes: int = Field(ge=1)
    maximum_encode_queue_size: int = Field(ge=0)
    maximum_pending_writes: int = Field(ge=0)
    timestamps_monotonic: bool
    exact_frame_time_boundaries: bool
    encode_seconds: float = Field(ge=0)
    mux_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def attestation_is_coherent(self) -> WebRenderEncoderTelemetry:
        if self.hardware_acceleration_verified and self.actual_encoder_type != "hardware":
            raise ValueError(
                "Verified hardware acceleration requires a traced hardware encoder"
            )
        if self.zero_copy_verified and (
            self.gpu_readback_events > 0
            or self.input_copy_path != "gpu-native-zero-copy"
        ):
            raise ValueError("Zero-copy evidence conflicts with the traced input path")
        return self


class WebRenderActualCapture(DomainModel):
    backend: Literal["webcodecs-h264", "drawelement", "screenshot"]
    reason: str = Field(min_length=1)
    fallback_reason: str | None = None
    worker_count: int = Field(ge=1)
    captured_frames: int = Field(ge=1)
    elapsed_seconds: float = Field(ge=0)
    encoder: WebRenderEncoderTelemetry | None = None


class WebRenderPlan(DomainModel):
    model_config = ConfigDict(
        serialize_by_alias=True,
        validate_by_alias=True,
        validate_by_name=True,
    )

    schema_id: Literal["mediaflow-web-render-plan/v1"] = Field(
        default="mediaflow-web-render-plan/v1",
        alias="schema",
    )
    plan_digest: str = Field(pattern="^[a-f0-9]{64}$")
    sequence_id: str = Field(min_length=1)
    clip_id: str = Field(min_length=1)
    asset_id: str = Field(min_length=1)
    source_hash: str = Field(pattern="^[a-f0-9]{64}$")
    render_key: str = Field(pattern="^[a-f0-9]{64}$")
    variant_id: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frame_count: int = Field(gt=0)
    fps_numerator: int = Field(gt=0)
    fps_denominator: int = Field(gt=0)
    cache_path: str = Field(min_length=1)
    cache_status: Literal["missing", "ready"] = "missing"
    static_compatibility: Literal["eligible", "screenshot-required"]
    capture_mode: Literal["auto", "screenshot"]
    planned_backend: Literal["webcodecs-h264", "frame-pipe"]
    fallback_backend: Literal["frame-pipe"] | None = None
    backend_selection_reasons: list[str] = Field(min_length=1)
    strategy: Literal[
        "verified-drawelement-with-atomic-screenshot-fallback",
        "screenshot-only",
    ]
    runtime_validation_required: bool = True
    findings: list[WebRenderCompatibilityFinding]
    verification_frames: list[WebRenderVerificationFrame] = Field(min_length=1)
    actual_capture: WebRenderActualCapture | None = None
