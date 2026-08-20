from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from mediaflow.domain.downloads import DownloadEntry, DownloadPlan

from .xiaoyuzhou_media import XiaoyuzhouEpisodeResolver


@dataclass(frozen=True)
class ResolvedPlatformMedia:
    source_url: str
    download_url: str
    title: str | None = None
    extractor: str | None = None


class PlatformMediaResolver:
    """Resolve platform behavior that yt-dlp alone cannot reproduce reliably."""

    _BILIBILI_ID = re.compile(r"(BV[a-zA-Z0-9]{10}|av\d+)")
    _MEDIA_MARKERS = (".mp4", ".m3u8", "aweme/v1/play", "video_id=")

    def __init__(self, chromium: Path | None):
        self.chromium = chromium
        self.xiaoyuzhou = XiaoyuzhouEpisodeResolver()

    def analyze(
        self,
        url: str,
        *,
        proxy: str | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> DownloadPlan | None:
        self._checkpoint(check_cancelled)
        if self.xiaoyuzhou.supports(url):
            return self.xiaoyuzhou.analyze(
                url,
                proxy=proxy,
                check_cancelled=check_cancelled,
            )
        if "bilibili.com/video/" in url:
            result = self._analyze_bilibili(
                url,
                proxy=proxy,
                check_cancelled=check_cancelled,
            )
            if result:
                return result
        if self._requires_browser_resolution(url):
            resolved = self.resolve_download(
                url,
                proxy=proxy,
                check_cancelled=check_cancelled,
            )
            if resolved:
                title = resolved.title or "Platform video"
                media_id = self._platform_id(url)
                return DownloadPlan(
                    source_url=url,
                    kind="single",
                    media_id=media_id,
                    title=title,
                    extractor=resolved.extractor or "Browser",
                    entries=[
                        DownloadEntry(
                            index=1,
                            media_id=media_id,
                            title=title,
                            page_url=url,
                            download_url=resolved.download_url,
                        )
                    ],
                )
        return None

    def resolve_download(
        self,
        url: str,
        *,
        proxy: str | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> ResolvedPlatformMedia | None:
        self._checkpoint(check_cancelled)
        if not self._requires_browser_resolution(url):
            return None
        return self._sniff_browser_media(
            url,
            executable=self.chromium,
            proxy=proxy,
            check_cancelled=check_cancelled,
        )

    def _analyze_bilibili(
        self,
        url: str,
        *,
        proxy: str | None,
        check_cancelled: Callable[[], None] | None,
    ) -> DownloadPlan | None:
        self._checkpoint(check_cancelled)
        match = self._BILIBILI_ID.search(url)
        if not match:
            return None
        media_id = match.group(1)
        query = {"bvid": media_id} if media_id.startswith("BV") else {"aid": media_id[2:]}
        request = Request(
            f"https://api.bilibili.com/x/web-interface/view?{urlencode(query)}",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        handlers = [ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
        try:
            with build_opener(*handlers).open(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError):
            return None
        self._checkpoint(check_cancelled)
        if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
            return None
        return self._bilibili_plan(url, payload["data"])

    @classmethod
    def _bilibili_plan(cls, url: str, data: dict[str, Any]) -> DownloadPlan | None:
        owner = data.get("owner") or {}
        uploader = str(owner.get("name") or "")
        title = str(data.get("title") or "Bilibili video")
        entries: list[DownloadEntry] = []
        season = data.get("ugc_season")
        if isinstance(season, dict):
            for section in season.get("sections") or []:
                for episode in section.get("episodes") or []:
                    arc = episode.get("arc") or {}
                    bvid = str(episode.get("bvid") or arc.get("bvid") or "")
                    if not bvid:
                        continue
                    entry_url = f"https://www.bilibili.com/video/{bvid}"
                    entries.append(
                        DownloadEntry(
                            index=len(entries) + 1,
                            media_id=bvid,
                            title=str(episode.get("title") or arc.get("title") or bvid),
                            page_url=entry_url,
                            download_url=entry_url,
                            duration=float(arc.get("duration") or 0),
                            uploader=uploader,
                            thumbnail=str(arc.get("pic") or ""),
                        )
                    )
            if entries:
                title = str(season.get("title") or title)
        if not entries:
            pages = data.get("pages") or []
            if len(pages) <= 1:
                return None
            bvid = str(data.get("bvid") or "")
            for index, page in enumerate(pages, start=1):
                page_number = int(page.get("page") or index)
                entry_url = f"https://www.bilibili.com/video/{bvid}?p={page_number}"
                entries.append(
                    DownloadEntry(
                        index=index,
                        media_id=f"{bvid}-p{page_number}",
                        title=f"P{page_number} - {page.get('part') or page_number}",
                        page_url=entry_url,
                        download_url=entry_url,
                        duration=float(page.get("duration") or 0),
                        uploader=uploader,
                        thumbnail=str(data.get("pic") or ""),
                    )
                )
        media_id = str(data.get("bvid") or data.get("aid") or "")
        return DownloadPlan(
            source_url=url,
            kind="collection",
            media_id=media_id,
            title=title,
            extractor="Bilibili",
            thumbnail=str(data.get("pic") or ""),
            duration=float(data.get("duration") or 0),
            collection_title=title,
            entries=entries,
        )

    @classmethod
    def _requires_browser_resolution(cls, url: str) -> bool:
        lowered = url.lower()
        return any(
            domain in lowered
            for domain in (
                "douyin.com",
                "kuaishou.com",
                "chenzhongtech.com",
                "kwai.com",
                "gifshow.com",
            )
        )

    @classmethod
    def _platform_id(cls, url: str) -> str:
        patterns = (
            r"(?:modal_id=|/(?:video|note)/)(\d+)",
            r"short-video/([a-zA-Z0-9_]+)",
            r"/(?:f/)?([a-zA-Z0-9_-]+)(?:\?|$)",
        )
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return ""

    @classmethod
    def _sniff_browser_media(
        cls,
        url: str,
        *,
        executable: Path | None,
        timeout: float = 15.0,
        proxy: str | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> ResolvedPlatformMedia | None:
        cls._checkpoint(check_cancelled)
        if executable is None or not executable.is_file():
            return None
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None

        found_url: str | None = None
        title: str | None = None
        platform = "Douyin" if "douyin.com" in url.lower() else "Kuaishou"

        def capture(request) -> None:
            nonlocal found_url
            candidate = request.url
            lowered = candidate.lower()
            if (
                found_url is None
                and not lowered.startswith("blob:")
                and ".html" not in lowered
                and any(marker in lowered for marker in cls._MEDIA_MARKERS)
            ):
                found_url = candidate

        try:
            with sync_playwright() as playwright:
                deadline = time.monotonic() + timeout
                launch_options: dict[str, Any] = {
                    "executable_path": str(executable),
                    "headless": True,
                    "args": [
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--disable-renderer-backgrounding",
                    ],
                }
                if proxy:
                    launch_options["proxy"] = {"server": proxy}
                browser = playwright.chromium.launch(**launch_options)
                context = browser.new_context(
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    viewport={"width": 1366, "height": 768},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/136.0.0.0 Safari/537.36"
                    ),
                )
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
                page = context.new_page()
                page.on("request", capture)
                cls._checkpoint(check_cancelled)
                try:
                    remaining = max(0.001, deadline - time.monotonic())
                    page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=max(1, int(remaining * 1000)),
                    )
                except PlaywrightError:
                    pass
                while (found_url is None or not title) and time.monotonic() < deadline:
                    cls._checkpoint(check_cancelled)
                    try:
                        candidate_title = page.evaluate(
                            "document.querySelector('meta[property=\"og:title\"]')?.content || document.title"
                        )
                        if candidate_title:
                            title = str(candidate_title)
                        page.evaluate(
                            """
                            () => {
                                const video = document.querySelector('video');
                                if (video) { video.muted = true; video.play().catch(() => {}); }
                                document.body && document.body.click();
                            }
                            """
                        )
                    except PlaywrightError:
                        pass
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        page.wait_for_timeout(min(500, max(1, int(remaining * 1000))))
                cls._checkpoint(check_cancelled)
                if not title:
                    try:
                        candidate_title = page.evaluate(
                            "document.querySelector('meta[property=\"og:title\"]')?.content || document.title"
                        )
                        title = str(candidate_title) if candidate_title else None
                    except PlaywrightError:
                        title = None
                context.close()
                browser.close()
        except PlaywrightError:
            return None
        if not found_url:
            return None
        cleaned_title = cls._clean_title(title)
        return ResolvedPlatformMedia(url, found_url, cleaned_title, platform)

    @staticmethod
    def _checkpoint(check_cancelled: Callable[[], None] | None) -> None:
        if check_cancelled is not None:
            check_cancelled()

    @staticmethod
    def _clean_title(title: str | None) -> str | None:
        value = str(title or "").strip()
        for suffix in (" - 抖音", " - 快手"):
            if suffix in value:
                value = value.split(suffix, maxsplit=1)[0].strip()
        return value or None
