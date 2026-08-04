from __future__ import annotations

import hashlib
import json
import platform
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, cast

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_CONTRACT = ROOT / "runtime.lock.json"

OperatingSystem = Literal["windows", "linux", "macos"]
Architecture = Literal["x86_64", "arm64"]
ArchiveFormat = Literal["zip", "txz", "dmg"]


def reported_version_at_least(output: str, minimum: str) -> bool:
    def parts(value: str) -> tuple[int, ...]:
        match = re.search(r"(?<!\d)(\d+(?:\.\d+){1,3})", value)
        return (
            tuple(int(item) for item in match.group(1).split("."))
            if match is not None
            else ()
        )

    reported = parts(output)
    required = parts(minimum)
    if not reported or not required:
        return False
    width = max(len(reported), len(required))
    return reported + (0,) * (width - len(reported)) >= required + (0,) * (
        width - len(required)
    )


@dataclass(frozen=True, slots=True)
class PlatformTarget:
    operating_system: OperatingSystem
    architecture: Architecture

    @property
    def key(self) -> str:
        return f"{self.operating_system}-{self.architecture}"

    @property
    def executable_suffix(self) -> str:
        return ".exe" if self.operating_system == "windows" else ""

    @property
    def case_sensitive_paths(self) -> bool:
        return self.operating_system != "windows"

    @classmethod
    def current(cls) -> PlatformTarget:
        system = platform.system().casefold()
        operating_systems: dict[str, OperatingSystem] = {
            "windows": "windows",
            "linux": "linux",
            "darwin": "macos",
        }
        operating_system = operating_systems.get(system)
        machine = platform.machine().casefold()
        architectures: dict[str, Architecture] = {
            "amd64": "x86_64",
            "x86_64": "x86_64",
            "aarch64": "arm64",
            "arm64": "arm64",
        }
        architecture = architectures.get(machine)
        if operating_system is None or architecture is None:
            raise RuntimeError(
                f"Unsupported MediaFlow platform: {platform.system()} {platform.machine()}"
            )
        return cls(operating_system=operating_system, architecture=architecture)


@dataclass(frozen=True, slots=True)
class ReviewedBundle:
    provider: str
    version: str
    archive_url: str
    archive_sha256: str
    archive_format: ArchiveFormat
    archive_root: str


@dataclass(frozen=True, slots=True)
class RuntimeLayout:
    ffmpeg: str
    ffprobe: str
    melt: str
    mlt_library: str
    mlt_root: str
    mlt_repository: str
    mlt_preview_repository: str
    mlt_data: str
    native_qml: str


@dataclass(frozen=True, slots=True)
class PlaywrightRuntime:
    version: str
    chromium_revision: str
    browser_version: str
    archive_url: str
    archive_sha256: str
    archive_root: str
    executable: str
    probe_arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuntimeContract:
    target: PlatformTarget
    minimum_release: str
    ffmpeg_version: str
    ffmpeg_version_match: Literal["exact", "minimum"]
    ffmpeg_executable: str
    ffprobe_executable: str
    melt_version: str
    melt_version_match: Literal["exact", "minimum"]
    melt_executable: str
    qt_version: str
    qt_toolchain: str
    qt_architecture: str
    qt_install_directory: str
    ffmpeg_probe_arguments: tuple[str, ...]
    melt_probe_arguments: tuple[str, ...]
    reviewed_bundle: ReviewedBundle
    layout: RuntimeLayout
    playwright: PlaywrightRuntime
    qt_archives: tuple[dict[str, str], ...]

    def reviewed_bundle_directory(self, runtime_root: Path) -> Path:
        bundle = self.reviewed_bundle
        return (
            runtime_root
            / "deps"
            / f"{bundle.provider}-{bundle.version}"
            / bundle.archive_root
        ).resolve()

    def shotcut_directory(self, runtime_root: Path) -> Path:
        if self.reviewed_bundle.provider != "shotcut":
            raise RuntimeError(f"{self.target.key} does not use a reviewed Shotcut bundle")
        return self.reviewed_bundle_directory(runtime_root)

    def chromium_directory(self, runtime_root: Path) -> Path:
        return (
            runtime_root
            / "deps"
            / f"chromium-{self.playwright.browser_version}"
            / self.playwright.archive_root
        ).resolve()

    @property
    def digest(self) -> str:
        payload = json.dumps(
            asdict(self),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def _required_text(record: dict[str, Any], name: str, context: str) -> str:
    value = str(record.get(name) or "").strip()
    if not value:
        raise ValueError(f"Runtime contract {context}.{name} is required")
    return value


def _version_match(record: dict[str, Any], context: str) -> Literal["exact", "minimum"]:
    value = str(record.get("version_match") or "exact")
    if value == "exact":
        return "exact"
    if value == "minimum":
        return "minimum"
    raise ValueError(f"Runtime contract {context}.version_match is not supported")


def _required_sha256(record: dict[str, Any], name: str, context: str) -> str:
    value = _required_text(record, name, context).casefold()
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"Runtime contract {context}.{name} must be SHA-256")
    return value


def _required_arguments(
    record: dict[str, Any],
    name: str,
    context: str,
) -> tuple[str, ...]:
    value = record.get(name)
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"Runtime contract {context}.{name} must be a string array")
    return tuple(value)


def load_runtime_contract(
    path: str | Path = DEFAULT_RUNTIME_CONTRACT,
    *,
    target: PlatformTarget | None = None,
) -> RuntimeContract:
    source = Path(path).resolve()
    document: Any = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != 2:
        raise ValueError("Runtime contract schema is not supported")
    targets = document.get("targets")
    if not isinstance(targets, dict):
        raise ValueError("Runtime contract targets are missing")
    selected = target or PlatformTarget.current()
    record = targets.get(selected.key)
    if not isinstance(record, dict):
        raise ValueError(f"Runtime contract target is missing: {selected.key}")
    if record.get("operating_system") != selected.operating_system:
        raise ValueError(f"Runtime contract operating system mismatch: {selected.key}")
    if record.get("architecture") != selected.architecture:
        raise ValueError(f"Runtime contract architecture mismatch: {selected.key}")
    ffmpeg = record.get("ffmpeg")
    mlt = record.get("mlt")
    qt = record.get("qt")
    if not isinstance(ffmpeg, dict) or not isinstance(mlt, dict) or not isinstance(qt, dict):
        raise ValueError(f"Runtime contract tool sections are missing: {selected.key}")
    bundle_document = record.get("reviewed_bundle")
    layout_document = record.get("layout")
    playwright_document = record.get("playwright")
    if not isinstance(bundle_document, dict):
        raise ValueError("Runtime reviewed_bundle must be an object")
    if not isinstance(layout_document, dict):
        raise ValueError("Runtime layout must be an object")
    if not isinstance(playwright_document, dict):
        raise ValueError("Runtime playwright must be an object")
    archive_format = _required_text(
        bundle_document,
        "archive_format",
        "reviewed_bundle",
    )
    if archive_format not in {"zip", "txz", "dmg"}:
        raise ValueError("Runtime reviewed_bundle.archive_format is unsupported")
    bundle = ReviewedBundle(
        provider=_required_text(bundle_document, "provider", "reviewed_bundle"),
        version=_required_text(bundle_document, "version", "reviewed_bundle"),
        archive_url=_required_text(bundle_document, "archive_url", "reviewed_bundle"),
        archive_sha256=_required_sha256(
            bundle_document,
            "archive_sha256",
            "reviewed_bundle",
        ),
        archive_format=cast(ArchiveFormat, archive_format),
        archive_root=_required_text(bundle_document, "archive_root", "reviewed_bundle"),
    )
    layout = RuntimeLayout(
        **{
            name: _required_text(layout_document, name, "layout")
            for name in (
                "ffmpeg",
                "ffprobe",
                "melt",
                "mlt_library",
                "mlt_root",
                "mlt_repository",
                "mlt_preview_repository",
                "mlt_data",
                "native_qml",
            )
        }
    )
    playwright = PlaywrightRuntime(
        version=_required_text(playwright_document, "version", "playwright"),
        chromium_revision=_required_text(
            playwright_document,
            "chromium_revision",
            "playwright",
        ),
        browser_version=_required_text(
            playwright_document,
            "browser_version",
            "playwright",
        ),
        archive_url=_required_text(playwright_document, "archive_url", "playwright"),
        archive_sha256=_required_sha256(
            playwright_document,
            "archive_sha256",
            "playwright",
        ),
        archive_root=_required_text(playwright_document, "archive_root", "playwright"),
        executable=_required_text(playwright_document, "executable", "playwright"),
        probe_arguments=_required_arguments(
            playwright_document,
            "probe_arguments",
            "playwright",
        ),
    )
    archives = record.get("qt_archives") or ()
    expected_qt_archives = {"qtbase", "qtdeclarative"}
    if selected.operating_system == "linux":
        expected_qt_archives.add("icu")
    if len(archives) != len(expected_qt_archives) or not all(
        isinstance(item, dict) for item in archives
    ):
        raise ValueError("Runtime qt_archives must be an array of objects")
    normalized_archives = tuple(
        {
            "name": _required_text(item, "name", "qt_archives"),
            "url": _required_text(item, "url", "qt_archives"),
            "sha256": _required_sha256(item, "sha256", "qt_archives"),
        }
        for item in archives
    )
    if {item["name"] for item in normalized_archives} != expected_qt_archives:
        expected = ", ".join(sorted(expected_qt_archives))
        raise ValueError(f"Runtime {selected.key} qt_archives must contain {expected}")
    return RuntimeContract(
        target=selected,
        minimum_release=_required_text(record, "minimum_release", selected.key),
        ffmpeg_version=_required_text(ffmpeg, "version", "ffmpeg"),
        ffmpeg_version_match=_version_match(ffmpeg, "ffmpeg"),
        ffmpeg_executable=_required_text(ffmpeg, "executable", "ffmpeg"),
        ffprobe_executable=_required_text(ffmpeg, "probe", "ffmpeg"),
        ffmpeg_probe_arguments=_required_arguments(
            ffmpeg,
            "probe_arguments",
            "ffmpeg",
        ),
        melt_version=_required_text(mlt, "version", "mlt"),
        melt_version_match=_version_match(mlt, "mlt"),
        melt_executable=_required_text(mlt, "executable", "mlt"),
        melt_probe_arguments=_required_arguments(
            mlt,
            "probe_arguments",
            "mlt",
        ),
        qt_version=_required_text(qt, "version", "qt"),
        qt_toolchain=_required_text(qt, "toolchain", "qt"),
        qt_architecture=_required_text(qt, "architecture", "qt"),
        qt_install_directory=_required_text(qt, "install_directory", "qt"),
        reviewed_bundle=bundle,
        layout=layout,
        playwright=playwright,
        qt_archives=normalized_archives,
    )
