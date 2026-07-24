from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mediaflow.domain.stock_media import StockMediaItem


class StockMediaService:
    USER_AGENT = "MediaFlow-Pro/2.0"
    PEXELS_SEARCH_URL = "https://api.pexels.com/v1/videos/search"
    PIXABAY_SEARCH_URL = "https://pixabay.com/api/videos/"
    UNSPLASH_SEARCH_URL = "https://api.unsplash.com/search/photos"

    @classmethod
    def search(
        cls,
        provider: str,
        query: str,
        api_key: str,
        *,
        per_page: int = 24,
    ) -> list[StockMediaItem]:
        value = query.strip()
        if not value:
            raise ValueError("请输入素材搜索词")
        if not api_key.strip():
            raise ValueError(f"请先在设置中填写 {provider} API Key")
        if provider == "pexels":
            return cls._search_pexels(value, api_key, per_page)
        if provider == "pixabay":
            return cls._search_pixabay(value, api_key, per_page)
        if provider == "unsplash":
            return cls._search_unsplash(value, api_key, per_page)
        raise ValueError(f"未知的素材提供商：{provider}")

    @classmethod
    def download(
        cls,
        item: StockMediaItem,
        project_dir: Path,
        api_key: str,
        *,
        progress=None,
        check_cancelled=None,
    ) -> Path:
        url = item.download_url
        headers = {"User-Agent": cls.USER_AGENT}
        if item.provider == "unsplash":
            headers.update({"Authorization": f"Client-ID {api_key}", "Accept-Version": "v1"})
            if item.tracking_url:
                tracked = cls._request_json(item.tracking_url, headers=headers)
                url = str(tracked.get("url") or url)
        request = Request(url, headers={"User-Agent": cls.USER_AGENT})
        directory = project_dir / "sources" / "stock" / item.provider
        directory.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(item.filename).name)
        destination = cls._unique_path(directory / (safe_name or f"{item.id}.bin"))
        with urlopen(request, timeout=120) as response:
            total = int(response.headers.get("Content-Length", "0") or 0)
            content_type = response.headers.get_content_type()
            if destination.suffix == ".bin":
                suffix = mimetypes.guess_extension(content_type) or ""
                if suffix:
                    destination = destination.with_suffix(suffix)
            temporary = destination.with_suffix(destination.suffix + ".partial")
            written = 0
            with temporary.open("wb") as stream:
                while True:
                    if check_cancelled:
                        check_cancelled()
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
                    written += len(chunk)
                    if progress and total:
                        progress(min(94.0, written / total * 90.0 + 5.0), "stock_downloading")
        temporary.replace(destination)
        return destination

    @classmethod
    def _search_pexels(cls, query: str, key: str, per_page: int) -> list[StockMediaItem]:
        payload = cls._request_json(
            cls.PEXELS_SEARCH_URL
            + "?"
            + urlencode({"query": query, "per_page": per_page, "locale": "zh-CN"}),
            headers={"Authorization": key},
        )
        values = []
        for item in payload.get("videos", []):
            files = [candidate for candidate in item.get("video_files", []) if candidate.get("link")]
            selected = min(
                files,
                key=lambda candidate: (
                    0 if int(candidate.get("width") or 0) <= 1920 else 1,
                    abs(int(candidate.get("width") or 0) - 1920),
                ),
            )
            user = item.get("user") or {}
            poster = next(iter(item.get("video_pictures") or []), {}).get("picture", "")
            identifier = str(item["id"])
            values.append(
                StockMediaItem(
                    id=f"pexels:{identifier}",
                    provider="pexels",
                    kind="video",
                    title=f"Pexels · {query} · {identifier}",
                    preview_url=poster,
                    download_url=str(selected["link"]),
                    source_url=str(item.get("url") or ""),
                    attribution=str(user.get("name") or "Pexels"),
                    attribution_url=str(user.get("url") or ""),
                    width=int(selected.get("width") or item.get("width") or 0),
                    height=int(selected.get("height") or item.get("height") or 0),
                    duration_seconds=float(item.get("duration") or 0),
                    filename=f"pexels-{identifier}.mp4",
                )
            )
        return values

    @classmethod
    def _search_pixabay(cls, query: str, key: str, per_page: int) -> list[StockMediaItem]:
        payload = cls._request_json(
            cls.PIXABAY_SEARCH_URL
            + "?"
            + urlencode({"key": key, "q": query, "per_page": per_page, "lang": "zh"})
        )
        values = []
        for item in payload.get("hits", []):
            renditions = item.get("videos") or {}
            selected = next(
                (
                    renditions.get(name)
                    for name in ("medium", "small", "large", "tiny")
                    if renditions.get(name, {}).get("url")
                ),
                None,
            )
            if selected is None:
                continue
            identifier = str(item["id"])
            values.append(
                StockMediaItem(
                    id=f"pixabay:{identifier}",
                    provider="pixabay",
                    kind="video",
                    title=str(item.get("tags") or f"Pixabay {identifier}"),
                    preview_url=str(selected.get("thumbnail") or ""),
                    download_url=str(selected["url"]),
                    source_url=str(item.get("pageURL") or ""),
                    attribution=str(item.get("user") or "Pixabay"),
                    attribution_url=(
                        f"https://pixabay.com/users/{item.get('user')}-{item.get('user_id')}/"
                    ),
                    width=int(selected.get("width") or 0),
                    height=int(selected.get("height") or 0),
                    duration_seconds=float(item.get("duration") or 0),
                    filename=f"pixabay-{identifier}.mp4",
                )
            )
        return values

    @classmethod
    def _search_unsplash(cls, query: str, key: str, per_page: int) -> list[StockMediaItem]:
        payload = cls._request_json(
            cls.UNSPLASH_SEARCH_URL
            + "?"
            + urlencode({"query": query, "per_page": min(30, per_page), "content_filter": "high"}),
            headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1"},
        )
        values = []
        for item in payload.get("results", []):
            user = item.get("user") or {}
            urls = item.get("urls") or {}
            links = item.get("links") or {}
            identifier = str(item["id"])
            values.append(
                StockMediaItem(
                    id=f"unsplash:{identifier}",
                    provider="unsplash",
                    kind="image",
                    title=str(item.get("alt_description") or item.get("description") or identifier),
                    preview_url=str(urls.get("small") or urls.get("thumb") or ""),
                    download_url=str(urls.get("full") or urls.get("regular") or ""),
                    source_url=str(links.get("html") or ""),
                    attribution=str(user.get("name") or "Unsplash"),
                    attribution_url=str((user.get("links") or {}).get("html") or ""),
                    width=int(item.get("width") or 0),
                    height=int(item.get("height") or 0),
                    filename=f"unsplash-{identifier}.jpg",
                    tracking_url=str(links.get("download_location") or ""),
                )
            )
        return values

    @classmethod
    def _request_json(cls, url: str, *, headers: dict[str, str] | None = None) -> dict:
        request = Request(url, headers={"User-Agent": cls.USER_AGENT, **(headers or {})})
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
        if not isinstance(payload, dict):
            raise RuntimeError("素材服务返回了无效数据")
        return payload

    @staticmethod
    def _unique_path(path: Path) -> Path:
        candidate = path
        index = 2
        while candidate.exists() or candidate.with_suffix(candidate.suffix + ".partial").exists():
            candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
            index += 1
        return candidate
