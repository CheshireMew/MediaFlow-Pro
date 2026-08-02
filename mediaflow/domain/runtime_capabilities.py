from __future__ import annotations

from typing import Literal

from mediaflow.domain.model_base import DomainModel
from mediaflow.domain.product_identity import PRODUCT_NAME

CapabilityAvailability = Literal["built-in", "runtime-inspected"]
RuntimeCapabilityState = Literal["ready", "unavailable", "unverified"]


class CapabilityDefinition(DomainModel):
    id: str
    availability: CapabilityAvailability
    description: str


class RuntimeCapabilityStatus(DomainModel):
    id: str
    status: RuntimeCapabilityState
    version: str = ""
    path: str = ""
    reason: str = ""


class RuntimeInspection(DomainModel):
    checked_at: int
    runtime_root: str
    capabilities: list[RuntimeCapabilityStatus]


CAPABILITY_CATALOG: tuple[CapabilityDefinition, ...] = (
    CapabilityDefinition(
        id="project-editing",
        availability="built-in",
        description=f"Create, inspect, version, and edit local {PRODUCT_NAME} projects.",
    ),
    CapabilityDefinition(
        id="cooperative-desktop-updates",
        availability="built-in",
        description="Coordinate command-line writes with an open desktop project.",
    ),
    CapabilityDefinition(
        id="editable-web-media",
        availability="built-in",
        description="Import and edit local editable-media web packages.",
    ),
    CapabilityDefinition(
        id="web-keyframes",
        availability="built-in",
        description="Edit deterministic layer and parameter keyframes.",
    ),
    CapabilityDefinition(
        id="web-parameters",
        availability="built-in",
        description="Edit declared editable-media parameters and locks.",
    ),
    CapabilityDefinition(
        id="web-themes",
        availability="built-in",
        description="Edit declared web-package theme variables.",
    ),
    CapabilityDefinition(
        id="web-responsive-variants",
        availability="built-in",
        description="Select and preserve declared responsive variants.",
    ),
    CapabilityDefinition(
        id="web-data-snapshots",
        availability="built-in",
        description="Bind inline or file-backed data snapshots to web scenes.",
    ),
    CapabilityDefinition(
        id="web-field-locks",
        availability="built-in",
        description="Protect selected web fields from automation edits.",
    ),
    CapabilityDefinition(
        id="web-template-rebinding",
        availability="built-in",
        description="Plan and commit hash-bound web-package replacements.",
    ),
    CapabilityDefinition(
        id="web-batch-variants",
        availability="built-in",
        description="Create project sequences from structured web bindings.",
    ),
    CapabilityDefinition(
        id="web-multi-format-export",
        availability="built-in",
        description="Export web clips as images, GIFs, video, or overlays.",
    ),
    CapabilityDefinition(
        id="transcript-edit-plans",
        availability="built-in",
        description="Preview and apply revision-bound transcript edit plans.",
    ),
    CapabilityDefinition(
        id="fcpxml-export",
        availability="built-in",
        description="Export timelines only when their editing semantics can be preserved in FCPXML.",
    ),
    CapabilityDefinition(
        id="reference-video-comparison",
        availability="built-in",
        description="Compare decoded reference and candidate video frames and publish reproducible evidence.",
    ),
    CapabilityDefinition(
        id="ffmpeg",
        availability="runtime-inspected",
        description="Pinned FFmpeg runtime for encoding and media analysis.",
    ),
    CapabilityDefinition(
        id="ffprobe",
        availability="runtime-inspected",
        description="Pinned FFprobe runtime for media inspection.",
    ),
    CapabilityDefinition(
        id="mlt",
        availability="runtime-inspected",
        description="Pinned MLT runtime for timeline rendering and export.",
    ),
    CapabilityDefinition(
        id="chromium",
        availability="runtime-inspected",
        description="A browser runtime that can execute editable web media.",
    ),
    CapabilityDefinition(
        id="native-preview",
        availability="runtime-inspected",
        description="The native Qt/MLT preview plug-in matched to the pinned Qt runtime.",
    ),
    CapabilityDefinition(
        id="faster-whisper-xxl",
        availability="runtime-inspected",
        description="The optional Faster-Whisper XXL executable used for external transcription.",
    ),
    CapabilityDefinition(
        id="gpt-sovits-v2pro",
        availability="runtime-inspected",
        description="The optional GPT-SoVITS v2Pro runtime used for reference-voice synthesis.",
    ),
)

CAPABILITY_IDS = frozenset(item.id for item in CAPABILITY_CATALOG)
RUNTIME_CAPABILITY_IDS = frozenset(
    item.id
    for item in CAPABILITY_CATALOG
    if item.availability == "runtime-inspected"
)
