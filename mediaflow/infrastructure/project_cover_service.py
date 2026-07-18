from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from mediaflow.domain.enums import AssetKind
from mediaflow.domain.project import Asset

from .project_repository import ProjectRepository
from .runtime_paths import RuntimePaths


class ProjectCoverService:
    """Build the recent-project cover from the first readable video asset."""

    WIDTH = 640
    HEIGHT = 360
    CACHE_VERSION = 1

    def __init__(self, paths: RuntimePaths):
        self.paths = paths

    def cover_for(self, repository: ProjectRepository) -> Path | None:
        try:
            selected = self._first_video_source(repository)
            if selected is None:
                return None
            asset, source = selected
            source_stat = source.stat()
            signature = hashlib.sha256(
                "|".join(
                    (
                        str(self.CACHE_VERSION),
                        asset.id,
                        str(source),
                        str(source_stat.st_size),
                        str(source_stat.st_mtime_ns),
                    )
                ).encode("utf-8")
            ).hexdigest()[:20]
            cover_dir = repository.project_dir / "cache" / "project-covers"
            cover = cover_dir / f"{signature}.jpg"
            ready = cover.with_suffix(".ready")
            if cover.is_file() and cover.stat().st_size > 0 and ready.is_file():
                return cover.resolve()

            cover_dir.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                [
                    str(self.paths.ffmpeg),
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-an",
                    "-vf",
                    (
                        f"scale={self.WIDTH}:{self.HEIGHT}:"
                        "force_original_aspect_ratio=increase,"
                        f"crop={self.WIDTH}:{self.HEIGHT},setsar=1"
                    ),
                    "-q:v",
                    "3",
                    str(cover),
                ],
                capture_output=True,
                timeout=20,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0 or not cover.is_file() or cover.stat().st_size == 0:
                return None
            try:
                ready.write_text("ready", encoding="ascii")
            except OSError:
                pass
            return cover.resolve()
        except (OSError, subprocess.SubprocessError):
            return None

    @staticmethod
    def _first_video_source(repository: ProjectRepository) -> tuple[Asset, Path] | None:
        for asset in repository.list_assets():
            if asset.kind != AssetKind.VIDEO and not asset.metadata.has_video:
                continue
            original = repository.resolve_asset_path(asset)
            candidates = [original]
            for proxy_value in (asset.sdr_preview_proxy_path, asset.proxy_path):
                if not proxy_value:
                    continue
                proxy = Path(proxy_value)
                candidates.append(
                    (repository.project_dir / proxy).resolve() if not proxy.is_absolute() else proxy.resolve()
                )
            source = next((candidate for candidate in candidates if candidate.is_file()), None)
            if source is not None:
                return asset, source
        return None
