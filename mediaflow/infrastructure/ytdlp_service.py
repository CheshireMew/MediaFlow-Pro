from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from mediaflow.domain.downloads import DownloadEntry, DownloadPlan, DownloadRequest
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.settings import DownloadSettings
from mediaflow.domain.storage_names import (
    WINDOWS_COMPONENT_UTF16_LIMIT,
    WINDOWS_INTEROP_PATH_UTF16_LIMIT,
    require_windows_interop_path,
    safe_path_component,
    utf16_units,
)

from .cookie_store import CookieStore
from .download_errors import YtDlpErrorCapture, classify_download_error
from .output_reservation import (
    output_set_transaction,
    require_output_transaction_path,
)
from .platform_media import PlatformMediaResolver
from .runtime_paths import RuntimePaths
from .runtime_tools import prepare_ytdlp_import

DownloadProgress = Callable[[OperationProgress], None]
CancellationCheck = Callable[[], None]
YTDLP_SOCKET_TIMEOUT_SECONDS = 10.0
FAILED_DOWNLOAD_DIRECTORY_NAME = "MediaFlow Failed Downloads"


def _checkpoint(check_cancelled: CancellationCheck | None) -> None:
    if check_cancelled is not None:
        check_cancelled()


def _youtube_dl(
    yt_dlp: Any,
    options: dict[str, Any],
    check_cancelled: CancellationCheck | None,
) -> Any:
    """Build a yt-dlp boundary that checks cancellation before every request."""

    class CancellableYoutubeDL(yt_dlp.YoutubeDL):  # type: ignore[misc]
        def urlopen(self, request: Any) -> Any:
            _checkpoint(check_cancelled)
            return super().urlopen(request)

    return CancellableYoutubeDL(options)


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
        check_cancelled: CancellationCheck | None = None,
    ) -> DownloadPlan:
        _checkpoint(check_cancelled)
        prepare_ytdlp_import()
        import yt_dlp

        normalized_url = self.normalize_url(url)
        platform_result = self.platforms.analyze(
            normalized_url,
            proxy=proxy,
            check_cancelled=check_cancelled,
        )
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
            with _youtube_dl(
                yt_dlp,
                {**options, "logger": capture},
                check_cancelled,
            ) as ydl:
                info = ydl.extract_info(normalized_url, download=False)
        except yt_dlp.utils.DownloadError as error:
            _checkpoint(check_cancelled)
            classified = classify_download_error(
                capture.text or str(error),
                url=normalized_url,
                fallback_code="no_info",
            )
            raise RuntimeError(classified.display_message) from error
        _checkpoint(check_cancelled)
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
        check_cancelled: CancellationCheck | None = None,
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
            check_cancelled=check_cancelled,
        )

    def download(
        self,
        request: DownloadRequest,
        *,
        cookie_file: str | None = None,
        browser_cookies: str | None = None,
        proxy: str | None = None,
        progress: DownloadProgress | None = None,
        check_cancelled: CancellationCheck | None = None,
    ) -> list[Path]:
        _checkpoint(check_cancelled)
        output_dir = Path(request.output_directory).expanduser().resolve()
        # Validate the real destination before creating any directory or
        # starting network traffic.  The download itself is isolated from the
        # user-visible destination until every requested file is complete.
        self._output_template(output_dir, request)
        staging_root = _create_download_staging_root(output_dir)
        try:
            staged_paths = self._download_to_directory(
                request,
                staging_root,
                cookie_file=cookie_file,
                browser_cookies=browser_cookies,
                proxy=proxy,
                progress=progress,
                check_cancelled=check_cancelled,
            )
            _checkpoint(check_cancelled)
            outputs = _publish_download_outputs(
                staging_root,
                output_dir,
                staged_paths,
                check_cancelled=check_cancelled,
            )
        except BaseException as error:
            try:
                archived = _archive_download_staging(
                    staging_root,
                    output_dir,
                )
            except BaseException as archive_error:
                error.add_note(
                    "未完成的下载内容无法移入失败归档："
                    f"{archive_error}"
                )
                archived = None
            if archived is not None:
                error.add_note(
                    f"未完成的下载内容已保留在：{archived}"
                )
            raise
        _remove_empty_download_staging(staging_root)
        return outputs

    def _download_to_directory(
        self,
        request: DownloadRequest,
        output_dir: Path,
        *,
        cookie_file: str | None,
        browser_cookies: str | None,
        proxy: str | None,
        progress: DownloadProgress | None,
        check_cancelled: CancellationCheck | None,
    ) -> list[Path]:
        moved_paths: list[Path] = []

        def hook(event: dict[str, Any]) -> None:
            _checkpoint(check_cancelled)
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
            _checkpoint(check_cancelled)
            path = event.get("filepath") or (event.get("info_dict") or {}).get("filepath")
            if path:
                moved_paths.append(Path(path).resolve())

        page_url = self.normalize_url(request.entry.page_url)
        download_url = self.normalize_url(request.entry.download_url)
        output_template = self._output_template(output_dir, request)
        download_format = self._format(
            request.resolution,
            request.codec,
        )
        options = self._base_options(cookie_file, browser_cookies, proxy)
        if page_url != download_url:
            options["http_headers"] = {
                **options["http_headers"],
                "Referer": page_url,
            }
        options.update(
            {
                "outtmpl": str(output_template),
                "format": download_format,
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
        _checkpoint(check_cancelled)
        prepare_ytdlp_import()
        import yt_dlp

        output_template.parent.mkdir(parents=True, exist_ok=True)
        info = None
        prepared_paths: list[Path] = []
        capture = YtDlpErrorCapture()
        try:
            with _youtube_dl(
                yt_dlp,
                {**options, "logger": capture},
                check_cancelled,
            ) as ydl:
                info = ydl.extract_info(download_url, download=True)
                _checkpoint(check_cancelled)
                if info:
                    prepared_paths = [
                        Path(ydl.prepare_filename(item)).resolve()
                        for item in (info.get("entries") or [info])
                        if item
                    ]
        except yt_dlp.utils.DownloadError as error:
            _checkpoint(check_cancelled)
            classified = classify_download_error(
                capture.text or str(error),
                url=page_url,
                fallback_code="no_info",
            )
            raise RuntimeError(classified.display_message) from error
        _checkpoint(check_cancelled)
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
            _checkpoint(check_cancelled)
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
            _checkpoint(check_cancelled)
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
            "socket_timeout": YTDLP_SOCKET_TIMEOUT_SECONDS,
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
        output_dir = Path(output_dir).expanduser().resolve()
        if request.collection_title:
            return _collection_output_template(output_dir, request)
        if request.filename_prefix.strip():
            filename_units = _available_filename_units(output_dir)
            prefix_budget = min(48, filename_units - 1 - _EXTENSION_BUDGET)
            prefix = _literal_template_component(
                request.filename_prefix,
                max_utf16_units=_require_literal_budget(prefix_budget),
            )
            template = output_dir / f"{prefix}.{_placeholder('ext', _EXTENSION_BUDGET)}"
            _require_template_path(template, filename_units)
            return template

        filename_units = _available_filename_units(output_dir)
        fixed_units = 4 + _DEFAULT_ID_BUDGET + _EXTENSION_BUDGET
        title_budget = min(160, filename_units - fixed_units)
        template = output_dir / (
            f"{_placeholder('title', _require_literal_budget(title_budget))} "
            f"[{_placeholder('id', _DEFAULT_ID_BUDGET)}]."
            f"{_placeholder('ext', _EXTENSION_BUDGET)}"
        )
        _require_template_path(template, filename_units)
        return template

    @staticmethod
    def _is_twitter_url(url: str) -> bool:
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            return False
        return host in {"x.com", "twitter.com"} or host.endswith((".x.com", ".twitter.com"))


_MIN_LITERAL_COMPONENT_UNITS = 8
_PREFIX_BUDGET = 48
_ENTRY_TITLE_BUDGET = 120
_COLLECTION_BUDGET = 120
_COLLECTION_ID_BUDGET = 48
_DEFAULT_ID_BUDGET = 64
_EXTENSION_BUDGET = 16


def _collection_output_template(output_dir: Path, request: DownloadRequest) -> Path:
    """Build a literal-yet-bounded collection filename template for yt-dlp."""

    output_dir = Path(output_dir).expanduser().resolve()
    prefix_requested = bool(request.filename_prefix.strip())
    index = f"{request.entry.index:03d}"
    minimum_filename_units = (
        utf16_units(index)
        + 1
        + (3 + _MIN_LITERAL_COMPONENT_UNITS if prefix_requested else 0)
        + _MIN_LITERAL_COMPONENT_UNITS
        + 4
        + _COLLECTION_ID_BUDGET
        + _EXTENSION_BUDGET
    )
    collection_budget = min(
        _COLLECTION_BUDGET,
        WINDOWS_INTEROP_PATH_UTF16_LIMIT
        - utf16_units(str(output_dir))
        - 1
        - minimum_filename_units,
    )
    # The collection component and file component share the same 240-unit path
    # budget.  Reserve the file first and then choose the longest collection
    # component that still leaves space for all literal user text.
    for budget in range(_require_literal_budget(collection_budget), 7, -1):
        collection = _literal_template_component(
            request.collection_title,
            fallback="Collection",
            max_utf16_units=budget,
        )
        target_dir = output_dir / collection
        try:
            filename_units = _available_filename_units(target_dir)
            fixed_units = (
                utf16_units(index)
                + 1
                + 4
                + _COLLECTION_ID_BUDGET
                + _EXTENSION_BUDGET
            )
            prefix = ""
            if prefix_requested:
                prefix_budget = min(
                    _PREFIX_BUDGET,
                    filename_units - fixed_units - 3 - _MIN_LITERAL_COMPONENT_UNITS,
                )
                prefix = _literal_template_component(
                    request.filename_prefix,
                    max_utf16_units=_require_literal_budget(prefix_budget),
                )
                fixed_units += utf16_units(prefix) + 3
            title_budget = min(_ENTRY_TITLE_BUDGET, filename_units - fixed_units)
            entry_title = _literal_template_component(
                request.entry.title,
                fallback=(request.entry.media_id or f"Item {request.entry.index}"),
                max_utf16_units=_require_literal_budget(title_budget),
            )
        except ValueError:
            continue
        display_title = f"{prefix} - {entry_title}" if prefix else entry_title
        template = target_dir / (
            f"{index} {display_title} "
            f"[{_placeholder('id', _COLLECTION_ID_BUDGET)}]."
            f"{_placeholder('ext', _EXTENSION_BUDGET)}"
        )
        _require_template_path(template, filename_units)
        return template
    raise ValueError("文件目录过深，无法生成安全的下载文件名")


def _available_filename_units(parent: Path) -> int:
    directory = Path(parent).expanduser().resolve()
    # Validate the existing directory boundary first.  The file component is
    # budgeted separately because yt-dlp expands it after receiving this
    # template.
    require_windows_interop_path(directory)
    available = min(
        WINDOWS_COMPONENT_UTF16_LIMIT,
        WINDOWS_INTEROP_PATH_UTF16_LIMIT - utf16_units(str(directory)) - 1,
    )
    return _require_literal_budget(available)


def _require_literal_budget(units: int) -> int:
    if units < _MIN_LITERAL_COMPONENT_UNITS:
        raise ValueError("文件目录过深，无法生成安全的下载文件名")
    return units


def _placeholder(field: str, byte_budget: int) -> str:
    return f"%({field}).{byte_budget}B"


def _require_template_path(template: Path, filename_units: int) -> None:
    # The template directive text is shorter than its expanded result.  Validate
    # a same-size concrete sibling so the actual yt-dlp output stays within the
    # native Windows-tool boundary.
    concrete = template.with_name("x" * filename_units)
    require_windows_interop_path(concrete)


def _literal_template_component(
    value: str,
    *,
    fallback: str | None = None,
    max_utf16_units: int = 120,
) -> str:
    """Sanitize one user-provided component embedded in a yt-dlp template.

    yt-dlp interprets percent directives in the output template.  Collection
    names, title snapshots, and user prefixes are literal names, so double
    percent signs before applying the component budget.  Doing it first keeps
    the final template component inside the Windows filename limit as well.
    """

    escaped = str(value).replace("%", "%%")
    escaped_fallback = None if fallback is None else str(fallback).replace("%", "%%")
    return safe_path_component(
        escaped,
        fallback=escaped_fallback,
        max_utf16_units=max_utf16_units,
    )


def _create_download_staging_root(output_dir: Path) -> Path:
    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    for _attempt in range(10):
        candidate = require_windows_interop_path(
            parent / f".mf-dl-{uuid.uuid4().hex[:8]}"
        )
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise RuntimeError(
        "无法创建隔离下载目录，请稍后重试"
    )


def _publish_download_outputs(
    staging_root: Path,
    output_dir: Path,
    staged_paths: list[Path],
    *,
    check_cancelled: CancellationCheck | None,
) -> list[Path]:
    pairs: list[tuple[Path, Path]] = []
    seen_destinations: set[Path] = set()
    for staged_path in staged_paths:
        staged = staged_path.resolve(strict=True)
        try:
            relative = staged.relative_to(staging_root)
        except ValueError as error:
            raise RuntimeError(
                f"下载器返回了隔离目录之外的文件：{staged}"
            ) from error
        destination = require_output_transaction_path(
                output_dir / relative,
                failure_archive_directory_name=(
                FAILED_DOWNLOAD_DIRECTORY_NAME
                ),
        )
        if destination in seen_destinations:
            continue
        seen_destinations.add(destination)
        pairs.append((staged, destination))
    if not pairs:
        raise RuntimeError(
            "下载完成但没有可发布的文件"
        )

    destinations = [destination for _staged, destination in pairs]
    with output_set_transaction(
        destinations,
        overwrite=False,
        failure_archive_directory_name=(
            FAILED_DOWNLOAD_DIRECTORY_NAME
        ),
    ) as transaction:
        for staged, destination in pairs:
            destination.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            staged.replace(
                transaction.temporary_path(
                    destination,
                    "download",
                )
            )
        _checkpoint(check_cancelled)
        transaction.publish()
        _checkpoint(check_cancelled)
        transaction.finalize()
    return destinations


def _archive_download_staging(
    staging_root: Path,
    output_dir: Path,
) -> Path | None:
    if not staging_root.exists():
        return None
    try:
        has_content = next(staging_root.iterdir(), None) is not None
    except OSError:
        has_content = True
    if not has_content:
        _remove_empty_download_staging(staging_root)
        return None
    archive_root = require_windows_interop_path(
        output_dir / FAILED_DOWNLOAD_DIRECTORY_NAME
    )
    archived = require_windows_interop_path(
        archive_root / f"run-{uuid.uuid4().hex[:12]}"
    )
    archive_root.mkdir(parents=True, exist_ok=True)
    try:
        staging_root.replace(archived)
    except OSError:
        return staging_root
    return archived


def _remove_empty_download_staging(
    staging_root: Path,
) -> None:
    if not staging_root.exists():
        return
    directories = sorted(
        (
            path
            for path in staging_root.rglob("*")
            if path.is_dir()
        ),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in (*directories, staging_root):
        try:
            directory.rmdir()
        except OSError:
            continue
