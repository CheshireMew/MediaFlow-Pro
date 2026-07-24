from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from mediaflow.domain.downloads import DownloadEntry, DownloadPlan, DownloadRequest
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.settings import DownloadSettings

from .cookie_store import CookieStore
from .download_errors import YtDlpErrorCapture, classify_download_error
from .platform_media import PlatformMediaResolver
from .runtime_paths import RuntimePaths
from .runtime_tools import prepare_ytdlp_import

DownloadProgress = Callable[[OperationProgress], None]


class YtDlpDownloadService:
    """The sole URL analysis and download implementation in MediaFlow Pro."""

    def __init__(self):
        self.platforms = PlatformMediaResolver()

    def analyze(
        self,
        url: str,
        *,
        cookie_file: str | None = None,
        browser_cookies: str | None = None,
        proxy: str | None = None,
    ) -> DownloadPlan:
        prepare_ytdlp_import()
        import yt_dlp

        normalized_url = self.normalize_url(url)
        platform_result = self.platforms.analyze(normalized_url, proxy=proxy)
        if platform_result:
            return platform_result
        options = self._base_options(cookie_file, browser_cookies, proxy)
        options.update(
            {
                "skip_download": True,
                "extract_flat": "in_playlist",
                "ignoreerrors": True,
            }
        )
        info = None
        capture = YtDlpErrorCapture()
        try:
            with yt_dlp.YoutubeDL({**options, "logger": capture}) as ydl:
                info = ydl.extract_info(normalized_url, download=False)
        except yt_dlp.utils.DownloadError as error:
            classified = classify_download_error(
                capture.text or str(error),
                url=normalized_url,
                fallback_code="no_info",
            )
            raise RuntimeError(classified.display_message) from error
        if not info:
            classified = classify_download_error(
                capture.text or None,
                url=normalized_url,
                fallback_code="no_info",
            )
            raise RuntimeError(classified.display_message)
        return self._plan_from_info(info, normalized_url)

    @classmethod
    def analyze_configured(
        cls,
        url: str,
        *,
        settings: DownloadSettings,
        cookies: CookieStore,
    ) -> DownloadPlan:
        """Analyze through the single configured cookie and proxy path."""
        managed_cookie = cookies.resolve_for_url(url)
        cookie_file = settings.cookie_file or (
            str(managed_cookie) if managed_cookie is not None else None
        )
        return cls().analyze(
            url,
            cookie_file=cookie_file,
            browser_cookies=None if cookie_file else settings.browser_cookies,
            proxy=settings.proxy,
        )

    def download(
        self,
        request: DownloadRequest,
        *,
        cookie_file: str | None = None,
        browser_cookies: str | None = None,
        proxy: str | None = None,
        progress: DownloadProgress | None = None,
    ) -> list[Path]:
        prepare_ytdlp_import()
        import yt_dlp

        output_dir = Path(request.output_directory).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        moved_paths: list[Path] = []

        def hook(event: dict[str, Any]) -> None:
            status = event.get("status")
            if status == "downloading" and progress:
                total = event.get("total_bytes") or event.get("total_bytes_estimate") or 0
                downloaded = event.get("downloaded_bytes") or 0
                if total:
                    progress(
                        OperationProgress.determinate(
                            "downloading",
                            completed=min(float(downloaded), float(total)),
                            total=float(total),
                            unit="bytes",
                        )
                    )
                else:
                    progress(OperationProgress.indeterminate("downloading"))
            elif status == "finished" and progress:
                progress(OperationProgress.indeterminate("postprocessing"))

        def after_move(event: dict[str, Any]) -> None:
            path = event.get("filepath") or (event.get("info_dict") or {}).get("filepath")
            if path:
                moved_paths.append(Path(path).resolve())

        page_url = self.normalize_url(request.entry.page_url)
        download_url = self.normalize_url(request.entry.download_url)
        options = self._base_options(cookie_file, browser_cookies, proxy)
        if page_url != download_url:
            options["http_headers"] = {
                **options["http_headers"],
                "Referer": page_url,
            }
        options.update(
            {
                "outtmpl": str(self._output_template(output_dir, request)),
                "format": self._format(request.resolution, request.codec),
                "noplaylist": request.entry.selector is None,
                "playlist_items": (
                    str(request.entry.selector) if request.entry.selector is not None else None
                ),
                "writesubtitles": request.download_subtitles,
                "writeautomaticsub": request.download_subtitles,
                "subtitleslangs": request.subtitle_languages,
                "postprocessors": (
                    [
                        {
                            "key": "FFmpegSubtitlesConvertor",
                            "format": "srt",
                            "when": "before_dl",
                        }
                    ]
                    if request.download_subtitles
                    else []
                ),
                "progress_hooks": [hook],
                "postprocessor_hooks": [after_move],
                "continuedl": True,
                "overwrites": False,
            }
        )
        if request.resolution != "audio":
            options["merge_output_format"] = "mp4"
        info = None
        prepared_paths: list[Path] = []
        capture = YtDlpErrorCapture()
        try:
            with yt_dlp.YoutubeDL({**options, "logger": capture}) as ydl:
                info = ydl.extract_info(download_url, download=True)
                if info:
                    prepared_paths = [
                        Path(ydl.prepare_filename(item)).resolve()
                        for item in (info.get("entries") or [info])
                        if item
                    ]
        except yt_dlp.utils.DownloadError as error:
            classified = classify_download_error(
                capture.text or str(error),
                url=page_url,
                fallback_code="no_info",
            )
            raise RuntimeError(classified.display_message) from error
        if not info:
            classified = classify_download_error(
                capture.text or None,
                url=page_url,
                fallback_code="no_info",
            )
            raise RuntimeError(classified.display_message)
        candidates = [item for item in (info.get("entries") or [info]) if item]
        if not moved_paths:
            for item, prepared_path in zip(candidates, prepared_paths, strict=False):
                requested = item.get("requested_downloads") or []
                filepath = item.get("filepath") or (requested[0].get("filepath") if requested else None)
                moved_paths.append(Path(filepath).resolve() if filepath else prepared_path)
        for item in candidates:
            for subtitle in (item.get("requested_subtitles") or {}).values():
                subtitle_path = subtitle.get("filepath") if isinstance(subtitle, dict) else None
                if subtitle_path:
                    moved_paths.append(Path(subtitle_path).resolve())
            if request.download_subtitles and item.get("id"):
                marker = f"[{item['id']}]"
                moved_paths.extend(
                    path.resolve()
                    for path in output_dir.rglob("*.srt")
                    if path.is_file() and marker in path.name and path.suffix.lower() == ".srt"
                )

        outputs: list[Path] = []
        seen: set[Path] = set()
        for path in moved_paths:
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            outputs.append(path)
        if not outputs:
            raise RuntimeError("yt-dlp completed without an observable downloaded file")
        return outputs

    @staticmethod
    def _base_options(
        cookie_file: str | None,
        browser_cookies: str | None,
        proxy: str | None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/136.0 Safari/537.36"
                )
            },
            "quiet": True,
            "no_warnings": True,
            "retries": 10,
            "fragment_retries": 10,
            "extractor_retries": 5,
            "file_access_retries": 3,
        }
        runtime_paths = RuntimePaths.discover()
        if runtime_paths.ffmpeg is not None:
            options["ffmpeg_location"] = str(runtime_paths.ffmpeg.parent)
        if cookie_file:
            options["cookiefile"] = str(Path(cookie_file).resolve(strict=True))
        if browser_cookies:
            if browser_cookies not in {"chrome", "edge"}:
                raise ValueError("Browser cookies must use Chrome or Edge")
            options["cookiesfrombrowser"] = (browser_cookies, None, None, None)
        if proxy:
            options["proxy"] = proxy
        return options

    @staticmethod
    def _format(resolution: str, codec: str = "best") -> str:
        if codec not in {"best", "avc"}:
            raise ValueError("Codec preference must be best or avc")
        if resolution == "audio":
            return "bestaudio[ext=m4a]/bestaudio/best"
        video = "bestvideo[vcodec^=avc]" if codec == "avc" else "bestvideo"
        preferred = "best[vcodec^=avc]" if codec == "avc" else "best"
        if resolution == "best":
            fallback = f"/{preferred}/best" if codec == "avc" else "/best"
            return f"{video}+bestaudio{fallback}"
        height = {"4k": 2160, "2k": 1440}.get(resolution)
        if height is None:
            height = int(resolution.rstrip("p"))
        primary = f"{video}[height<={height}]+bestaudio/{preferred}[height<={height}]"
        if codec == "avc":
            return f"{primary}/best[height<={height}]/best"
        return primary

    @classmethod
    def _plan_from_info(cls, info: dict[str, Any], source_url: str) -> DownloadPlan:
        title = str(info.get("title") or "Unknown media")
        extractor = str(info.get("extractor_key") or info.get("extractor") or "yt-dlp")
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        fps = float(info.get("fps") or 0)
        available_heights = sorted(
            {
                int(item.get("height") or 0)
                for item in (info.get("formats") or [])
                if int(item.get("height") or 0) > 0 and item.get("vcodec") != "none"
            }
            | ({height} if height > 0 else set()),
            reverse=True,
        )
        raw_entries = info.get("entries")
        is_collection = info.get("_type") == "playlist" or raw_entries is not None
        if not is_collection:
            media_id = str(info.get("id") or "")
            return DownloadPlan(
                source_url=source_url,
                kind="single",
                media_id=media_id,
                title=title,
                extractor=extractor,
                thumbnail=str(info.get("thumbnail") or ""),
                duration=float(info.get("duration") or 0),
                width=width,
                height=height,
                fps=fps,
                available_heights=available_heights,
                entries=[
                    DownloadEntry(
                        index=1,
                        media_id=media_id,
                        title=title,
                        page_url=source_url,
                        download_url=source_url,
                        duration=float(info.get("duration") or 0),
                        uploader=str(info.get("uploader") or ""),
                        thumbnail=str(info.get("thumbnail") or ""),
                    )
                ],
            )

        entries: list[DownloadEntry] = []
        for position, item in enumerate(raw_entries or [], start=1):
            if item is None:
                entries.append(
                    DownloadEntry(
                        index=position,
                        title=f"不可用项目 {position}",
                        page_url="",
                        download_url="",
                        available=False,
                        unavailable_reason="视频已失效、设为私密或当前账号无权访问",
                    )
                )
                continue
            index = int(item.get("playlist_index") or position)
            page_url = cls._entry_page_url(item)
            if cls._is_twitter_url(source_url):
                page_url = source_url
                download_url = source_url
                selector = index
            elif page_url:
                download_url = page_url
                selector = None
            else:
                page_url = source_url
                download_url = source_url
                selector = index
            entries.append(
                DownloadEntry(
                    index=index,
                    media_id=str(item.get("id") or ""),
                    title=str(item.get("title") or f"Item {index}"),
                    page_url=page_url,
                    download_url=download_url,
                    selector=selector,
                    duration=float(item.get("duration") or 0),
                    uploader=str(item.get("uploader") or info.get("uploader") or ""),
                    thumbnail=str(item.get("thumbnail") or ""),
                )
            )
        if not entries:
            classified = classify_download_error(None, url=source_url, fallback_code="no_info")
            raise RuntimeError(classified.display_message)
        return DownloadPlan(
            source_url=source_url,
            kind="collection",
            media_id=str(info.get("id") or ""),
            title=title,
            extractor=extractor,
            thumbnail=str(info.get("thumbnail") or ""),
            duration=float(info.get("duration") or 0),
            width=width,
            height=height,
            fps=fps,
            available_heights=available_heights,
            collection_title=title,
            entries=entries,
        )

    @classmethod
    def _entry_page_url(cls, item: dict[str, Any]) -> str:
        for key in ("webpage_url", "original_url", "url"):
            candidate = str(item.get(key) or "").strip()
            if candidate.startswith(("http://", "https://")):
                return candidate
        extractor = str(item.get("extractor_key") or item.get("ie_key") or "").lower()
        media_id = str(item.get("id") or "")
        if "youtube" in extractor and media_id:
            return f"https://www.youtube.com/watch?v={media_id}"
        return ""

    @staticmethod
    def normalize_url(url: str) -> str:
        raw = str(url).strip()
        try:
            parts = urlsplit(raw)
        except ValueError:
            return raw
        aliases = {"pro.x.com": "x.com"}
        host = (parts.hostname or "").lower()
        replacement = aliases.get(host)
        if not replacement:
            return raw
        netloc = replacement + (f":{parts.port}" if parts.port else "")
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    @staticmethod
    def _output_template(output_dir: Path, request: DownloadRequest) -> Path:
        prefix = YtDlpDownloadService._safe_path_component(request.filename_prefix)
        if request.collection_title:
            collection = YtDlpDownloadService._safe_path_component(request.collection_title)
            if not collection:
                raise ValueError("合集标题无法用于创建下载目录")
            target_dir = output_dir / collection
            target_dir.mkdir(parents=True, exist_ok=True)
            entry_title = YtDlpDownloadService._safe_path_component(request.entry.title)
            if not entry_title:
                entry_title = request.entry.media_id or f"Item {request.entry.index}"
            display_title = f"{prefix} - {entry_title}" if prefix else entry_title
            return target_dir / (f"{request.entry.index:03d} {display_title} [%(id)s].%(ext)s")
        if prefix:
            return output_dir / f"{prefix}.%(ext)s"
        return output_dir / "%(title).180B [%(id)s].%(ext)s"

    @staticmethod
    def _safe_path_component(value: str) -> str:
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value.strip()).strip(" .")

    @staticmethod
    def _is_twitter_url(url: str) -> bool:
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            return False
        return host in {"x.com", "twitter.com"} or host.endswith((".x.com", ".twitter.com"))
