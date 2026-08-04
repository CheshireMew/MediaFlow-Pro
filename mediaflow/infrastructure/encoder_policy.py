from __future__ import annotations

from dataclasses import dataclass

from mediaflow.domain.enums import ExportFormat
from mediaflow.domain.exports import EncoderVendor, VideoEncoderPolicy

from .encoder_catalog import VIDEO_ENCODERS, EncoderBackend, software_encoder_for_format
from .encoder_discovery import EncoderDiscoveryService
from .runtime_paths import RuntimePaths


@dataclass(frozen=True, slots=True)
class ResolvedVideoEncoder:
    codec: str
    backend: EncoderBackend | str
    fallback_allowed: bool


class VideoEncoderPolicyResolver:
    """Resolve portable intent against one machine without persisting FFmpeg names."""

    def __init__(self, paths: RuntimePaths):
        self.paths = paths
        self.discovery = EncoderDiscoveryService(paths)

    def resolve(
        self,
        export_format: ExportFormat,
        policy: VideoEncoderPolicy,
    ) -> ResolvedVideoEncoder:
        software = software_encoder_for_format(export_format)
        if software is None:
            raise ValueError(f"No software encoder is registered for {export_format.value}")
        if policy.mode == "software":
            return ResolvedVideoEncoder(software.codec, "software", False)
        supported = self._supported_backends()
        requested = self._backends_for_vendor(policy.vendor)
        for backend in requested:
            if backend not in supported:
                continue
            codec = next(
                (
                    name
                    for name, spec in VIDEO_ENCODERS.items()
                    if spec.format == export_format and spec.backend == backend
                ),
                None,
            )
            if codec is not None and self.discovery.encoder_works(codec):
                return ResolvedVideoEncoder(
                    codec,
                    backend,
                    True,
                )
        return ResolvedVideoEncoder(software.codec, "software", False)

    def _supported_backends(self) -> frozenset[EncoderBackend]:
        operating_system = self.paths.target.operating_system
        if operating_system == "windows":
            return frozenset({"nvenc", "qsv", "amf"})
        if operating_system == "macos":
            return frozenset({"videotoolbox"})
        return frozenset({"vaapi", "nvenc", "qsv"})

    def _preference_order(self) -> tuple[EncoderBackend, ...]:
        operating_system = self.paths.target.operating_system
        if operating_system == "windows":
            return ("nvenc", "qsv", "amf")
        if operating_system == "macos":
            return ("videotoolbox",)
        return ("nvenc", "qsv", "vaapi")

    def _backends_for_vendor(
        self,
        vendor: EncoderVendor,
    ) -> tuple[EncoderBackend, ...]:
        if vendor == "auto":
            return self._preference_order()
        operating_system = self.paths.target.operating_system
        by_platform: dict[str, dict[EncoderVendor, tuple[EncoderBackend, ...]]] = {
            "windows": {
                "nvidia": ("nvenc",),
                "intel": ("qsv",),
                "amd": ("amf",),
                "apple": (),
                "auto": self._preference_order(),
            },
            "linux": {
                "nvidia": ("nvenc",),
                "intel": ("qsv", "vaapi"),
                "amd": ("vaapi",),
                "apple": (),
                "auto": self._preference_order(),
            },
            "macos": {
                "nvidia": (),
                "intel": (),
                "amd": (),
                "apple": ("videotoolbox",),
                "auto": self._preference_order(),
            },
        }
        return by_platform[operating_system][vendor]
