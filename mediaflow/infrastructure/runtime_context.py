from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from mediaflow.domain.runtime import DesktopRuntimeDescriptor
from mediaflow.infrastructure.runtime_contract import (
    PlatformTarget,
    RuntimeContract,
    load_runtime_contract,
)
from mediaflow.infrastructure.runtime_paths import RuntimePaths


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    """One immutable, process-wide view of the selected platform runtime."""

    target: PlatformTarget
    contract: RuntimeContract
    paths: RuntimePaths
    application_paths: ApplicationPaths
    media_runtime: MediaRuntime
    platform_services: PlatformServices
    capabilities: RuntimeCapabilities

    @classmethod
    def discover(
        cls,
        *,
        target: PlatformTarget | None = None,
    ) -> RuntimeContext:
        selected = target or PlatformTarget.current()
        contract = load_runtime_contract(target=selected)
        paths = RuntimePaths.from_contract(contract, target=selected)
        if paths.target != contract.target:
            raise RuntimeError("Runtime paths and contract selected different platforms")
        return cls(
            target=selected,
            contract=contract,
            paths=paths,
            application_paths=ApplicationPaths.from_runtime(paths.runtime_dir),
            media_runtime=MediaRuntime.from_contract(contract, paths),
            platform_services=PlatformServices(
                subprocess_creation_flags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if selected.operating_system == "windows"
                else 0
                ),
                hdr_preview=selected.operating_system == "windows",
                hdr_unavailable_reason=(
                    ""
                    if selected.operating_system == "windows"
                    else "HDR preview is not available on this platform"
                ),
            ),
            capabilities=RuntimeCapabilities.from_paths(paths),
        )

    @property
    def subprocess_creation_flags(self) -> int:
        return self.platform_services.subprocess_creation_flags

    @property
    def contract_digest(self) -> str:
        return self.contract.digest

    def desktop_descriptor(self) -> DesktopRuntimeDescriptor:
        paths = self.paths
        contract = self.contract
        if paths.render_identity is None:
            raise RuntimeError("Runtime render identity is unavailable")
        return DesktopRuntimeDescriptor(
            target=cast(
                Literal["windows-x86_64", "linux-x86_64", "macos-arm64"],
                self.target.key,
            ),
            runtime_root=str(paths.runtime_dir),
            ffmpeg=str(paths.ffmpeg),
            ffprobe=str(paths.ffprobe),
            melt=str(paths.melt or ""),
            mlt_library=str(paths.mlt_library or ""),
            mlt_root=str(paths.mlt_root or ""),
            mlt_repository=str(paths.mlt_repository or ""),
            mlt_preview_repository=str(paths.mlt_preview_repository or ""),
            mlt_data=str(paths.mlt_data or ""),
            chromium=str(paths.chromium or ""),
            native_qml=str(paths.native_qml or ""),
            contract_digest=self.contract_digest,
            render_identity=paths.render_identity,
            shotcut_version=contract.reviewed_bundle.version,
            mlt_version=contract.melt_version,
            ffmpeg_version=contract.ffmpeg_version,
            qt_version=contract.qt_version,
            playwright_version=contract.playwright.version,
            chromium_version=contract.playwright.browser_version,
            hdr_preview=self.platform_services.hdr_preview,
            hdr_unavailable_reason=self.platform_services.hdr_unavailable_reason,
        )

    @classmethod
    def from_desktop_descriptor(
        cls,
        descriptor: DesktopRuntimeDescriptor,
    ) -> RuntimeContext:
        target_by_key = {
            "windows-x86_64": PlatformTarget("windows", "x86_64"),
            "linux-x86_64": PlatformTarget("linux", "x86_64"),
            "macos-arm64": PlatformTarget("macos", "arm64"),
        }
        target = target_by_key[descriptor.target]
        contract = load_runtime_contract(target=target)
        paths = RuntimePaths.from_contract(
            contract,
            runtime_root=Path(descriptor.runtime_root),
        )
        context = cls(
            target=target,
            contract=contract,
            paths=paths,
            application_paths=ApplicationPaths.from_runtime(paths.runtime_dir),
            media_runtime=MediaRuntime.from_contract(contract, paths),
            platform_services=PlatformServices(
                subprocess_creation_flags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if target.operating_system == "windows"
                    else 0
                ),
                hdr_preview=descriptor.hdr_preview,
                hdr_unavailable_reason=descriptor.hdr_unavailable_reason,
            ),
            capabilities=RuntimeCapabilities.from_paths(paths),
        )
        if context.desktop_descriptor() != descriptor:
            raise RuntimeError("Editor Service returned a mismatched runtime descriptor")
        return context


@dataclass(frozen=True, slots=True)
class ApplicationPaths:
    runtime_root: Path
    cache_root: Path
    log_root: Path
    component_root: Path

    @classmethod
    def from_runtime(cls, root: Path) -> ApplicationPaths:
        return cls(
            runtime_root=root,
            cache_root=root / "cache",
            log_root=root / "logs",
            component_root=root / "components",
        )


@dataclass(frozen=True, slots=True)
class MediaRuntime:
    paths: RuntimePaths
    shotcut_version: str
    mlt_version: str
    ffmpeg_version: str
    qt_version: str
    playwright_version: str
    chromium_version: str

    @classmethod
    def from_contract(
        cls,
        contract: RuntimeContract,
        paths: RuntimePaths,
    ) -> MediaRuntime:
        return cls(
            paths=paths,
            shotcut_version=contract.reviewed_bundle.version,
            mlt_version=contract.melt_version,
            ffmpeg_version=contract.ffmpeg_version,
            qt_version=contract.qt_version,
            playwright_version=contract.playwright.version,
            chromium_version=contract.playwright.browser_version,
        )


@dataclass(frozen=True, slots=True)
class PlatformServices:
    subprocess_creation_flags: int
    hdr_preview: bool
    hdr_unavailable_reason: str


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    ffmpeg: bool
    ffprobe: bool
    mlt: bool
    chromium: bool
    native_preview: bool
    sdr_preview: bool = True

    @classmethod
    def from_paths(cls, paths: RuntimePaths) -> RuntimeCapabilities:
        return cls(
            ffmpeg=paths.ffmpeg.is_file(),
            ffprobe=paths.ffprobe.is_file(),
            mlt=bool(paths.melt and paths.melt.is_file()),
            chromium=bool(paths.chromium and paths.chromium.is_file()),
            native_preview=bool(paths.native_qml and paths.native_qml.is_dir()),
        )
