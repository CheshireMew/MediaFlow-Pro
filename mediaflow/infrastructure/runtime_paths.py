from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    runtime_dir: Path
    ffmpeg: Path
    ffprobe: Path
    melt: Path | None = None
    native_qml: Path | None = None

    @classmethod
    def discover(cls) -> RuntimePaths:
        runtime_dir = cls._runtime_dir()
        ffmpeg = cls._tool("MEDIAFLOW_FFMPEG", runtime_dir / "deps/ffmpeg/bin/ffmpeg.exe", "ffmpeg")
        ffprobe = cls._tool("MEDIAFLOW_FFPROBE", runtime_dir / "deps/ffmpeg/bin/ffprobe.exe", "ffprobe")
        system_melt = shutil.which("melt")
        melt = cls._first_existing(
            [
                Path(os.environ["MEDIAFLOW_MELT"]).expanduser() if os.environ.get("MEDIAFLOW_MELT") else None,
                runtime_dir / "deps/mlt/bin/melt.exe",
                Path("D:/Tools/MediaFlow/deps/shotcut-26.6.25/Shotcut/melt.exe"),
                Path(system_melt) if system_melt else None,
            ]
        )
        native_qml = cls._first_existing_directory(
            [
                Path(os.environ["MEDIAFLOW_NATIVE_QML"]).expanduser()
                if os.environ.get("MEDIAFLOW_NATIVE_QML")
                else None,
                Path("D:/Tools/MediaFlow/build/native-qt611/qml"),
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
    def _runtime_dir() -> Path:
        configured = os.environ.get("MEDIAFLOW_RUNTIME_DIR")
        if configured:
            return Path(configured).expanduser().resolve()
        drive = Path("D:/")
        if not drive.exists():
            raise RuntimeError("D: drive is unavailable. Set MEDIAFLOW_RUNTIME_DIR to a non-system drive.")
        return Path("D:/Tools/MediaFlow/runtime")

    @classmethod
    def _tool(cls, variable: str, bundled: Path, command: str) -> Path:
        result = cls._optional_tool(variable, bundled, command)
        if result is None:
            raise FileNotFoundError(
                f"Required tool '{command}' was not found. "
                f"Set {variable} or install it under {bundled.parent}."
            )
        return result

    @staticmethod
    def _optional_tool(variable: str, bundled: Path, command: str) -> Path | None:
        configured = os.environ.get(variable)
        candidates = [Path(configured).expanduser() if configured else None, bundled]
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
