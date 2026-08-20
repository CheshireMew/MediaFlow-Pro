from __future__ import annotations

import json
import re
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from mediaflow.domain.downloads import DownloadEntry, DownloadPlan

CancellationCheck = Callable[[], None]
_EPISODE_PATH = re.compile(r"^/episode/([0-9a-zA-Z]{24})/?$")
_MAX_PAGE_BYTES = 4 * 1024 * 1024


class _EpisodePageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metadata: dict[str, str] = {}
        self.next_data_parts: list[str] = []
        self._in_next_data = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            content = attributes.get("content", "").strip()
            if key and content:
                self.metadata.setdefault(key, content)
        elif tag.lower() == "script" and attributes.get("id") == "__NEXT_DATA__":
            self._in_next_data = True

    def handle_data(self, data: str) -> None:
        if self._in_next_data:
            self.next_data_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_next_data:
            self._in_next_data = False

    @property
    def next_data(self) -> str:
        return "".join(self.next_data_parts).strip()


class XiaoyuzhouEpisodeResolver:
    """Resolve a public Xiaoyuzhou episode page into its direct audio asset."""

    @classmethod
    def supports(cls, url: str) -> bool:
        try:
            parts = urlsplit(str(url).strip())
        except ValueError:
            return False
        host = (parts.hostname or "").lower().rstrip(".")
        return (
            parts.scheme.lower() in {"http", "https"}
            and host in {"xiaoyuzhoufm.com", "www.xiaoyuzhoufm.com"}
            and _EPISODE_PATH.fullmatch(parts.path) is not None
        )

    def analyze(
        self,
        url: str,
        *,
        proxy: str | None = None,
        check_cancelled: CancellationCheck | None = None,
    ) -> DownloadPlan:
        if not self.supports(url):
            raise ValueError("请输入有效的小宇宙单集链接")
        self._checkpoint(check_cancelled)
        try:
            html = self._fetch_episode_html(url, proxy=proxy)
        except OSError as error:
            raise RuntimeError(f"无法读取小宇宙单集页面：{error}") from error
        self._checkpoint(check_cancelled)
        plan = self._plan_from_html(url, html)
        self._checkpoint(check_cancelled)
        return plan

    @staticmethod
    def _fetch_episode_html(url: str, *, proxy: str | None) -> str:
        request = Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/136.0.0.0 Safari/537.36"
                ),
            },
        )
        handlers = [ProxyHandler({"http": proxy, "https": proxy})] if proxy else []
        with build_opener(*handlers).open(request, timeout=12) as response:
            payload = response.read(_MAX_PAGE_BYTES + 1)
            if len(payload) > _MAX_PAGE_BYTES:
                raise RuntimeError("小宇宙单集页面异常过大，已停止解析")
            encoding = response.headers.get_content_charset() or "utf-8"
        return payload.decode(encoding, errors="replace")

    @classmethod
    def _plan_from_html(cls, source_url: str, html: str) -> DownloadPlan:
        match = _EPISODE_PATH.fullmatch(urlsplit(source_url).path)
        if match is None:
            raise ValueError("请输入有效的小宇宙单集链接")
        episode_id = match.group(1)
        parser = _EpisodePageParser()
        parser.feed(html)

        episode: dict[str, Any] = {}
        if parser.next_data:
            try:
                payload = json.loads(parser.next_data)
            except (TypeError, ValueError):
                payload = {}
            candidate = cls._nested(payload, "props", "pageProps", "episode")
            if isinstance(candidate, dict):
                episode = candidate

        title = cls._text(episode.get("title")) or parser.metadata.get("og:title", "").strip()
        podcast_value = episode.get("podcast")
        podcast: dict[str, Any] = podcast_value if isinstance(podcast_value, dict) else {}
        podcast_title = cls._text(podcast.get("title"))
        audio_url = cls._first_http_url(
            cls._nested(episode, "enclosure", "url"),
            cls._nested(episode, "media", "source", "url"),
            parser.metadata.get("og:audio"),
        )
        thumbnail = cls._first_http_url(
            cls._nested(episode, "image", "picUrl"),
            cls._nested(podcast, "image", "picUrl"),
            parser.metadata.get("og:image"),
        )
        duration = cls._duration(episode.get("duration"))

        if not title:
            raise RuntimeError("小宇宙单集页面缺少标题，暂时无法下载")
        if not audio_url:
            raise RuntimeError("小宇宙单集页面没有提供可下载的音频地址")

        display_title = f"{title} - {podcast_title}" if podcast_title else title
        return DownloadPlan(
            source_url=source_url,
            kind="single",
            media_kind="audio",
            media_id=episode_id,
            title=display_title,
            extractor="Xiaoyuzhou",
            thumbnail=thumbnail,
            duration=duration,
            entries=[
                DownloadEntry(
                    index=1,
                    media_id=episode_id,
                    title=display_title,
                    page_url=source_url,
                    download_url=audio_url,
                    duration=duration,
                    uploader=podcast_title,
                    thumbnail=thumbnail,
                    suggested_filename=f"{display_title} [{episode_id}]",
                )
            ],
        )

    @staticmethod
    def _nested(value: Any, *keys: str) -> Any:
        current = value
        for key in keys:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    @staticmethod
    def _text(value: Any) -> str:
        return str(value).strip() if isinstance(value, str) else ""

    @staticmethod
    def _first_http_url(*values: Any) -> str:
        for value in values:
            if not isinstance(value, str):
                continue
            candidate = value.strip()
            try:
                scheme = urlsplit(candidate).scheme.lower()
            except ValueError:
                continue
            if scheme in {"http", "https"}:
                return candidate
        return ""

    @staticmethod
    def _duration(value: Any) -> float:
        try:
            return max(0.0, float(value or 0.0))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _checkpoint(check_cancelled: CancellationCheck | None) -> None:
        if check_cancelled is not None:
            check_cancelled()
