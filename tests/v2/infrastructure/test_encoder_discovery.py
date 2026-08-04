from __future__ import annotations

from types import SimpleNamespace

import pytest

from mediaflow.domain.enums import ExportFormat
from mediaflow.domain.exports import EncoderVendor, VideoEncoderPolicy
from mediaflow.infrastructure.encoder_discovery import EncoderDiscoveryService
from mediaflow.infrastructure.encoder_policy import VideoEncoderPolicyResolver
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.runtime_contract import OperatingSystem, PlatformTarget


def test_actual_ffmpeg_encoder_catalog_drives_export_options() -> None:
    options = EncoderDiscoveryService(RuntimeContext.discover().paths).video_options()
    software_formats = {
        format_name
        for item in options
        if item["value"] == "software"
        for format_name in item["formats"]
    }

    assert {"h264", "hevc", "av1", "prores"} <= software_formats
    assert all(item["formats"] for item in options)
    assert all(item["labelKey"] for item in options)


@pytest.mark.parametrize(
    ("operating_system", "vendor", "working", "expected"),
    [
        ("windows", "nvidia", {"h264_nvenc"}, "h264_nvenc"),
        ("windows", "intel", {"h264_qsv"}, "h264_qsv"),
        ("windows", "amd", {"h264_amf"}, "h264_amf"),
        ("linux", "nvidia", {"h264_nvenc"}, "h264_nvenc"),
        ("linux", "intel", {"h264_vaapi"}, "h264_vaapi"),
        ("linux", "amd", {"h264_vaapi"}, "h264_vaapi"),
        ("macos", "apple", {"h264_videotoolbox"}, "h264_videotoolbox"),
    ],
)
def test_portable_vendor_intent_resolves_only_at_the_target_runtime(
    operating_system: OperatingSystem,
    vendor: EncoderVendor,
    working: set[str],
    expected: str,
) -> None:
    resolver = VideoEncoderPolicyResolver.__new__(VideoEncoderPolicyResolver)
    resolver.paths = SimpleNamespace(
        target=PlatformTarget(
            operating_system=operating_system,
            architecture="arm64" if operating_system == "macos" else "x86_64",
        )
    )
    resolver.discovery = SimpleNamespace(
        encoder_works=lambda codec: codec in working,
    )

    resolved = resolver.resolve(
        ExportFormat.H264,
        VideoEncoderPolicy(mode="prefer_hardware", vendor=vendor),
    )

    assert resolved.codec == expected
    assert resolved.fallback_allowed is True


def test_unavailable_preferred_hardware_resolves_to_one_software_attempt() -> None:
    resolver = VideoEncoderPolicyResolver.__new__(VideoEncoderPolicyResolver)
    resolver.paths = SimpleNamespace(
        target=PlatformTarget(operating_system="linux", architecture="x86_64")
    )
    resolver.discovery = SimpleNamespace(encoder_works=lambda _codec: False)

    resolved = resolver.resolve(
        ExportFormat.HEVC,
        VideoEncoderPolicy(mode="prefer_hardware", vendor="auto"),
    )

    assert resolved.codec == "libx265"
    assert resolved.backend == "software"
    assert resolved.fallback_allowed is False
