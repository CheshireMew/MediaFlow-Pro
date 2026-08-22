from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class DirectH264FallbackRequired(RuntimeError):
    """Discard the direct attempt and repeat the complete render through the frame pipe."""


@dataclass(frozen=True, slots=True)
class EncodedChunk:
    timestamp: int
    duration: int
    kind: str
    size: int


@dataclass(frozen=True, slots=True)
class EncoderTraceEvidence:
    actual_encoder_name: str
    actual_encoder_type: Literal["hardware", "software", "unknown"]
    encoder_storage_type: Literal["SharedMemory", "GpuMemoryBuffer", "unknown"]
    input_copy_path: Literal[
        "gpu-readback-to-shared-memory",
        "gpu-readback-to-memory",
        "gpu-native-unverified",
        "gpu-native-zero-copy",
        "unknown",
    ]
    attested_frames: int
    trace_event_count: int
    platform_encode_events: int
    platform_output_events: int
    gpu_readback_events: int
    hardware_acceleration_verified: bool
    zero_copy_verified: bool


@dataclass(frozen=True, slots=True)
class BrowserEncodeResult:
    requested_config: dict[str, object]
    accepted_config: dict[str, object]
    chunk_count: int
    encoded_bytes: int
    maximum_encode_queue_size: int
    maximum_pending_writes: int
    attestation: EncoderTraceEvidence
