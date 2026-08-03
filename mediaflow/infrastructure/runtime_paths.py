from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from mediaflow.environment import (
    RUNTIME_DIRECTORY_VARIABLE,
    configured_path,
    development_root,
)
from mediaflow.infrastructure.runtime_contract import (
    RuntimeContract,
    load_runtime_contract,
)


def configured_runtime_directory() -> Path | None:
    configured = configured_path(RUNTIME_DIRECTORY_VARIABLE)
    if configured is not None:
        return configured
    root = development_root(required=False)
    return (root / "runtime").resolve() if root is not None else None


def runtime_directory() -> Path:
    configured = configured_runtime_directory()
    if configured is None:
        raise RuntimeError(
            "MEDIAFLOW_RUNTIME_DIR or MEDIAFLOW_DEV_ROOT is required for the media runtime. "
            "Copy .env.example to .env and configure this machine before starting MediaFlow Pro."
        )
    return configured


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    runtime_dir: Path
    ffmpeg: Path
    ffprobe: Path
    melt: Path | None = None
    native_qml: Path | None = None

    def project_cache_dir(self, project_dir: str | Path) -> Path:
        """Return the machine-local cache root for one project location."""

        normalized = str(Path(project_dir).expanduser().resolve()).casefold()
        identity = hashlib.sha256(
            normalized.encode("utf-8")
        ).hexdigest()[:24]
        return self.runtime_dir / "cache" / "projects" / identity

    @classmethod
    def discover(cls) -> RuntimePaths:
        discovered = RuntimePathDiscovery.discover()
        if discovered.ffmpeg is None:
            raise FileNotFoundError(
                "Required tool 'ffmpeg' was not found. Set MEDIAFLOW_FFMPEG "
                f"or install it under {(discovered.runtime_dir / 'deps/ffmpeg/bin').resolve()}."
            )
        if discovered.ffprobe is None:
            raise FileNotFoundError(
                "Required tool 'ffprobe' was not found. Set MEDIAFLOW_FFPROBE "
                f"or install it under {(discovered.runtime_dir / 'deps/ffmpeg/bin').resolve()}."
            )
        return cls(
            runtime_dir=discovered.runtime_dir,
            ffmpeg=discovered.ffmpeg,
            ffprobe=discovered.ffprobe,
            melt=discovered.melt,
            native_qml=discovered.native_qml,
        )

    @staticmethod
    def _first_existing(candidates: list[Path | None]) -> Path | None:
        return RuntimePathDiscovery._first_existing(candidates)

    @staticmethod
    def _first_existing_directory(candidates: list[Path | None]) -> Path | None:
        return RuntimePathDiscovery._first_existing_directory(candidates)


@dataclass(frozen=True, slots=True)
class RuntimePathDiscovery:
    runtime_dir: Path
    ffmpeg: Path | None
    ffprobe: Path | None
    melt: Path | None
    native_qml: Path | None

    @classmethod
    def discover(cls) -> RuntimePathDiscovery:
        runtime_dir = runtime_directory()
        try:
            contract: RuntimeContract | None = load_runtime_contract()
        except (OSError, ValueError):
            contract = None
        shotcut_dir = (
            contract.shotcut_directory(runtime_dir.parent)
            if contract is not None
            else None
        )
        root = development_root(required=False)
        reviewed_runtime = (root / "runtime").resolve() if root is not None else None
        reviewed_shotcut_dir = (
            contract.shotcut_directory(reviewed_runtime.parent)
            if contract is not None
            and reviewed_runtime is not None
            and reviewed_runtime != runtime_dir
            else None
        )
        ffmpeg = cls._optional_tool(
            "MEDIAFLOW_FFMPEG",
            runtime_dir / "deps/ffmpeg/bin/ffmpeg.exe",
            [
                shotcut_dir / "ffmpeg.exe" if shotcut_dir is not None else None,
                (
                    reviewed_shotcut_dir / "ffmpeg.exe"
                    if reviewed_shotcut_dir is not None
                    else None
                ),
            ],
            "ffmpeg",
        )
        ffprobe = cls._optional_tool(
            "MEDIAFLOW_FFPROBE",
            runtime_dir / "deps/ffmpeg/bin/ffprobe.exe",
            [
                shotcut_dir / "ffprobe.exe" if shotcut_dir is not None else None,
                (
                    reviewed_shotcut_dir / "ffprobe.exe"
                    if reviewed_shotcut_dir is not None
                    else None
                ),
            ],
            "ffprobe",
        )
        system_melt = shutil.which("melt")
        melt = cls._first_existing(
            [
                Path(os.environ["MEDIAFLOW_MELT"]).expanduser() if os.environ.get("MEDIAFLOW_MELT") else None,
                runtime_dir / "deps/mlt/bin/melt.exe",
                shotcut_dir / "melt.exe" if shotcut_dir is not None else None,
                (
                    reviewed_shotcut_dir / "melt.exe"
                    if reviewed_shotcut_dir is not None
                    else None
                ),
                Path(system_melt) if system_melt else None,
            ]
        )
        native_qml = cls._first_existing_directory(
            [
                Path(os.environ["MEDIAFLOW_NATIVE_QML"]).expanduser()
                if os.environ.get("MEDIAFLOW_NATIVE_QML")
                else None,
                root / "build/native-qt611/qml" if root is not None else None,
            ]
        )
        return cls(
            runtime_dir=runtime_dir,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            melt=melt,
            native_qml=native_qml,
        )

    @staticmethod
    def _optional_tool(
        variable: str,
        bundled: Path,
        reviewed: list[Path | None],
        command: str,
    ) -> Path | None:
        configured = os.environ.get(variable)
        candidates = [
            Path(configured).expanduser() if configured else None,
            bundled,
            *reviewed,
        ]
        located = shutil.which(command)
        if located:
            candidates.append(Path(located))
        for candidate in candidates:
            if candidate is not None and candidate.is_file():
                return candidate.resolve()
        return None

    @staticmethod
    def _first_existing(candidates: list[Path | None]) -> Path | None:
        for candidate in candidates:
            if candidate is not None and candidate.is_file():
                return candidate.resolve()
        return None

    @staticmethod
    def _first_existing_directory(candidates: list[Path | None]) -> Path | None:
        for candidate in candidates:
            if candidate is not None and candidate.is_dir():
                return candidate.resolve()
        return None
