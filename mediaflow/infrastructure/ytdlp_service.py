from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mediaflow.application.asset_service import AssetService
from mediaflow.domain.enums import AssetOrigin

DownloadProgress = Callable[[float, str], None]


class YtDlpDownloadService:
    """The sole URL analysis and download implementation in MediaFlow Pro."""

    def __init__(self, asset_service: AssetService):
        self.asset_service = asset_service

    def analyze(
        self,
        url: str,
        *,
        cookie_file: str | None = None,
        browser_cookies: str | None = None,
    ) -> dict[str, Any]:
        import yt_dlp

        options = self._base_options(cookie_file, browser_cookies)
        options.update({"quiet": True, "skip_download": True, "extract_flat": False})
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            raise RuntimeError("yt-dlp returned no media information")
        return self._summary(info)

    def download(
        self,
        url: str,
        *,
        resolution: str = "best",
        cookie_file: str | None = None,
        browser_cookies: str | None = None,
        playlist_items: str | None = None,
        progress: DownloadProgress | None = None,
    ):
        import yt_dlp

        output_dir = self.asset_service.repository.project_dir / "downloads"
        moved_paths: list[Path] = []

        def hook(event: dict[str, Any]) -> None:
            status = event.get("status")
            if status == "downloading" and progress:
                total = event.get("total_bytes") or event.get("total_bytes_estimate") or 0
                downloaded = event.get("downloaded_bytes") or 0
                value = (float(downloaded) / float(total) * 95.0) if total else 0.0
                progress(value, "downloading")
            elif status == "finished" and progress:
                progress(97.0, "postprocessing")

        def after_move(event: dict[str, Any]) -> None:
            path = event.get("filepath") or (event.get("info_dict") or {}).get("filepath")
            if path:
                moved_paths.append(Path(path).resolve())

        options = self._base_options(cookie_file, browser_cookies)
        options.update(
            {
                "outtmpl": str(output_dir / "%(title).180B [%(id)s].%(ext)s"),
                "format": self._format(resolution),
                "merge_output_format": "mp4",
                "noplaylist": playlist_items is None,
                "playlist_items": playlist_items,
                "progress_hooks": [hook],
                "postprocessor_hooks": [after_move],
                "quiet": True,
                "no_warnings": True,
            }
        )
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise RuntimeError("yt-dlp returned no download result")
            if not moved_paths:
                candidates = [info]
                if info.get("entries"):
                    candidates = [item for item in info["entries"] if item]
                for item in candidates:
                    requested = item.get("requested_downloads") or []
                    filepath = item.get("filepath") or (requested[0].get("filepath") if requested else None)
                    if filepath:
                        moved_paths.append(Path(filepath).resolve())
                    else:
                        moved_paths.append(Path(ydl.prepare_filename(item)).resolve())

        assets = []
        seen: set[Path] = set()
        for path in moved_paths:
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            assets.append(self.asset_service.register_managed(path, AssetOrigin.DOWNLOAD))
        if not assets:
            raise RuntimeError("yt-dlp completed without an observable downloaded file")
        if progress:
            progress(100.0, "completed")
        return assets

    @staticmethod
    def _base_options(cookie_file: str | None, browser_cookies: str | None) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if cookie_file:
            options["cookiefile"] = str(Path(cookie_file).resolve(strict=True))
        if browser_cookies:
            if browser_cookies not in {"chrome", "edge"}:
                raise ValueError("Browser cookies must use Chrome or Edge")
            options["cookiesfrombrowser"] = (browser_cookies, None, None, None)
        return options

    @staticmethod
    def _format(resolution: str) -> str:
        if resolution == "best":
            return "bestvideo+bestaudio/best"
        height = int(resolution.rstrip("p"))
        return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"

    @staticmethod
    def _summary(info: dict[str, Any]) -> dict[str, Any]:
        entries = info.get("entries") or []
        return {
            "id": info.get("id"),
            "title": info.get("title"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "extractor": info.get("extractor_key") or info.get("extractor"),
            "is_playlist": bool(entries),
            "entry_count": len(entries),
        }
