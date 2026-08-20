from __future__ import annotations

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mediaflow.domain.downloads import DownloadEntry, DownloadRequest
from mediaflow.infrastructure.platform_media import PlatformMediaResolver
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.xiaoyuzhou_media import XiaoyuzhouEpisodeResolver
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService

EPISODE_ID = "6966f416109824f9e15f3cb5"
EPISODE_URL = f"https://www.xiaoyuzhoufm.com/episode/{EPISODE_ID}"
AUDIO_URL = "https://media.xyzcdn.net/media/episode.m4a"
COVER_URL = "https://image.xyzcdn.net/cover.jpg"


def _episode_html(*, include_next_data: bool = True) -> str:
    next_data = {
        "props": {
            "pageProps": {
                "episode": {
                    "title": "开场白",
                    "duration": 89,
                    "enclosure": {"url": AUDIO_URL},
                    "podcast": {
                        "title": "嘿，你好，生活",
                        "image": {"picUrl": COVER_URL},
                    },
                }
            }
        }
    }
    script = (
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'
        if include_next_data
        else ""
    )
    return (
        "<html><head>"
        '<meta property="og:title" content="后备标题">'
        f'<meta property="og:audio" content="{AUDIO_URL}">'
        f'<meta property="og:image" content="{COVER_URL}">'
        f"{script}</head><body></body></html>"
    )


def test_xiaoyuzhou_resolver_accepts_only_public_episode_pages() -> None:
    assert XiaoyuzhouEpisodeResolver.supports(EPISODE_URL)
    assert XiaoyuzhouEpisodeResolver.supports(
        f"https://xiaoyuzhoufm.com/episode/{EPISODE_ID}?utm_source=test"
    )
    assert not XiaoyuzhouEpisodeResolver.supports(f"https://www.xiaoyuzhoufm.com/podcast/{EPISODE_ID}")
    assert not XiaoyuzhouEpisodeResolver.supports(
        f"https://xiaoyuzhoufm.com.example.com/episode/{EPISODE_ID}"
    )


def test_xiaoyuzhou_next_data_produces_a_complete_audio_plan() -> None:
    plan = XiaoyuzhouEpisodeResolver._plan_from_html(EPISODE_URL, _episode_html())

    assert plan.media_kind == "audio"
    assert plan.extractor == "Xiaoyuzhou"
    assert plan.title == "开场白 - 嘿，你好，生活"
    assert plan.duration == 89
    assert plan.thumbnail == COVER_URL
    assert plan.entries[0].download_url == AUDIO_URL
    assert plan.entries[0].page_url == EPISODE_URL
    assert plan.entries[0].uploader == "嘿，你好，生活"
    assert plan.entries[0].suggested_filename == (f"开场白 - 嘿，你好，生活 [{EPISODE_ID}]")


def test_xiaoyuzhou_og_audio_remains_a_same_page_fallback() -> None:
    plan = XiaoyuzhouEpisodeResolver._plan_from_html(
        EPISODE_URL,
        _episode_html(include_next_data=False),
    )

    assert plan.title == "后备标题"
    assert plan.entries[0].download_url == AUDIO_URL
    assert plan.entries[0].suggested_filename == f"后备标题 [{EPISODE_ID}]"


def test_platform_media_routes_xiaoyuzhou_with_proxy_and_cancellation(monkeypatch) -> None:
    observed: dict[str, str | None] = {}
    checkpoints: list[str] = []

    def fetch(_url: str, *, proxy: str | None) -> str:
        observed["proxy"] = proxy
        return _episode_html()

    monkeypatch.setattr(XiaoyuzhouEpisodeResolver, "_fetch_episode_html", staticmethod(fetch))
    plan = PlatformMediaResolver(None).analyze(
        EPISODE_URL,
        proxy="http://127.0.0.1:7890",
        check_cancelled=lambda: checkpoints.append("checked"),
    )

    assert plan is not None
    assert plan.media_kind == "audio"
    assert observed == {"proxy": "http://127.0.0.1:7890"}
    assert len(checkpoints) >= 3


def test_direct_audio_download_uses_the_episode_filename_and_referer(tmp_path: Path) -> None:
    web_root = tmp_path / "web"
    web_root.mkdir()
    source = web_root / "episode.m4a"
    source.write_bytes(b"local-podcast-audio")

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(Handler, directory=str(web_root)),
    )
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    media_url = f"http://127.0.0.1:{server.server_address[1]}/{source.name}"
    request = DownloadRequest(
        entry=DownloadEntry(
            index=1,
            media_id=EPISODE_ID,
            title="开场白 - 嘿，你好，生活",
            page_url=EPISODE_URL,
            download_url=media_url,
            suggested_filename=f"开场白 - 嘿，你好，生活 [{EPISODE_ID}]",
        ),
        resolution="audio",
        output_directory=str((tmp_path / "downloads").resolve()),
    )
    try:
        outputs = YtDlpDownloadService(RuntimeContext.discover().paths).download(request)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert len(outputs) == 1
    assert outputs[0].name == f"开场白 - 嘿，你好，生活 [{EPISODE_ID}].m4a"
    assert outputs[0].read_bytes() == source.read_bytes()
