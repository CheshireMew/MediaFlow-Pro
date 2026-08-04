from __future__ import annotations

from typing import Literal

from mediaflow.domain.model_base import DomainModel


class RenderRuntimeIdentity(DomainModel):
    platform: Literal["windows", "linux", "macos"]
    architecture: Literal["x86_64", "arm64"]
    chromium_version: str
    chromium_sha256: str
    font_digest: str
    runtime_digest: str
    native_plugin_digest: str


class DesktopRuntimeDescriptor(DomainModel):
    target: Literal["windows-x86_64", "linux-x86_64", "macos-arm64"]
    runtime_root: str
    ffmpeg: str
    ffprobe: str
    melt: str
    mlt_library: str
    mlt_root: str
    mlt_repository: str
    mlt_preview_repository: str
    mlt_data: str
    chromium: str
    native_qml: str
    contract_digest: str
    render_identity: RenderRuntimeIdentity
    shotcut_version: str
    mlt_version: str
    ffmpeg_version: str
    qt_version: str
    playwright_version: str
    chromium_version: str
    sdr_preview: bool = True
    hdr_preview: bool
    hdr_unavailable_reason: str = ""
