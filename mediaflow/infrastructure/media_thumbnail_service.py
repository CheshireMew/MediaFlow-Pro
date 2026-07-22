from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

from mediaflow.domain.enums import AssetKind
from mediaflow.domain.project import Asset

from .project_repository import ProjectRepository
from .runtime_paths import RuntimePaths


class MediaThumbnailService:
    """Create cached, display-ready thumbnails for visual project assets."""

    CACHE_VERSION = 2

    def __init__(self, paths: RuntimePaths):
        self.paths = paths

    def thumbnail_for(
        self,
        repository: ProjectRepository,
        asset: Asset,
        *,
        width: int,
        height: int,
    ) -> Path | None:
        if width <= 0 or height <= 0:
            raise ValueError("Thumbnail dimensions must be positive")
        if asset.status.value != "online" or asset.kind not in {AssetKind.VIDEO, AssetKind.IMAGE}:
            return None
        try:
            source = self._visual_source(repository, asset)
            if source is None:
                return None
            source_stat = source.stat()
            signature = hashlib.sha256(
                "|".join(
                    (
                        str(self.CACHE_VERSION),
                        asset.id,
                        asset.kind.value,
                        str(source),
                        str(source_stat.st_size),
                        str(source_stat.st_mtime_ns),
                        str(width),
                        str(height),
                    )
                ).encode("utf-8")
            ).hexdigest()[:24]
            thumbnail_dir = repository.project_dir / "cache" / "media-thumbnails"
            destination = thumbnail_dir / f"{signature}.jpg"
            if destination.is_file() and destination.stat().st_size > 0:
                return destination.resolve()

            thumbnail_dir.mkdir(parents=True, exist_ok=True)
            if asset.kind == AssetKind.VIDEO:
                created = self._render_video(source, destination, width, height)
            else:
                created = self._render_frame(source, destination, width, height)
            return destination.resolve() if created else None
        except (OSError, subprocess.SubprocessError):
            return None

    def _render_video(self, source: Path, destination: Path, width: int, height: int) -> bool:
        visible_time = self._first_visible_time(source)
        if self._render_frame(
            source,
            destination,
            width,
            height,
            seek_seconds=visible_time,
        ):
            return True
        return visible_time > 0 and self._render_frame(source, destination, width, height)

    def _render_frame(
        self,
        source: Path,
        destination: Path,
        width: int,
        height: int,
        *,
        seek_seconds: float = 0,
    ) -> bool:
        return self._run_ffmpeg(
            source,
            destination,
            self._fit_filter(width, height),
            seek_seconds=seek_seconds,
        )

    def _run_ffmpeg(
        self,
        source: Path,
        destination: Path,
        video_filter: str,
        *,
        seek_seconds: float = 0,
    ) -> bool:
        command = [
            str(self.paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
        ]
        if seek_seconds > 0:
            command.extend(["-ss", f"{seek_seconds:.6f}"])
        command.extend(
            [
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-an",
                "-vf",
                video_filter,
                "-q:v",
                "3",
                str(destination),
            ]
        )
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=20,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0 and destination.is_file() and destination.stat().st_size > 0

    def _first_visible_time(self, source: Path) -> float:
        result = subprocess.run(
            [
                str(self.paths.ffmpeg),
                "-hide_banner",
                "-loglevel",
                "info",
                "-i",
                str(source),
                "-t",
                "10",
                "-map",
                "0:v:0",
                "-vf",
                "blackdetect=d=0.04:pic_th=0.98:pix_th=0.10",
                "-an",
                "-f",
                "null",
                os.devnull,
            ],
            capture_output=True,
            timeout=20,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        match = re.search(rb"black_start:0(?:\.0+)?\s+black_end:([0-9.]+)", result.stderr)
        return float(match.group(1)) if match else 0

    @staticmethod
    def _fit_filter(width: int, height: int) -> str:
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x151a21,setsar=1"
        )

    @staticmethod
    def _visual_source(repository: ProjectRepository, asset: Asset) -> Path | None:
        original = repository.resolve_asset_path(asset)
        candidates = [original]
        if asset.kind == AssetKind.VIDEO:
            for proxy_value in (asset.sdr_preview_proxy_path, asset.proxy_path):
                if not proxy_value:
                    continue
                proxy = Path(proxy_value)
                candidates.append(
                    (repository.project_dir / proxy).resolve()
                    if not proxy.is_absolute()
                    else proxy.resolve()
                )
        return next((candidate for candidate in candidates if candidate.is_file()), None)
