from __future__ import annotations

import threading
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yt_dlp
from yt_dlp.extractor.twitter import TwitterIE

from mediaflow.desktop.download_selection import parse_download_entry_selection
from mediaflow.domain.downloads import DownloadEntry, DownloadRequest
from mediaflow.domain.storage_names import WINDOWS_INTEROP_PATH_UTF16_LIMIT, utf16_units
from mediaflow.infrastructure import ytdlp_service
from mediaflow.infrastructure.cookie_store import CookieStore
from mediaflow.infrastructure.download_errors import classify_download_error
from mediaflow.infrastructure.platform_media import PlatformMediaResolver, ResolvedPlatformMedia
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService


def test_download_format_options_cover_audio_resolution_and_avc_compatibility() -> None:
    assert YtDlpDownloadService._format("audio") == "bestaudio[ext=m4a]/bestaudio/best"
    assert YtDlpDownloadService._format("1080p", "avc") == (
        "bestvideo[vcodec^=avc][height<=1080]+bestaudio/"
        "best[vcodec^=avc][height<=1080]/best[height<=1080]/best"
    )
    assert YtDlpDownloadService._format("4k") == ("bestvideo[height<=2160]+bestaudio/best[height<=2160]")
    assert YtDlpDownloadService._format("2k", "avc") == (
        "bestvideo[vcodec^=avc][height<=1440]+bestaudio/"
        "best[vcodec^=avc][height<=1440]/best[height<=1440]/best"
    )
    assert YtDlpDownloadService.normalize_url("https://pro.x.com/user/status/123") == (
        "https://x.com/user/status/123"
    )
    assert YtDlpDownloadService._base_options(None, None, None)["socket_timeout"] == 10.0


def test_download_output_template_bounds_and_sanitizes_every_component(
    tmp_path: Path,
) -> None:
    request = DownloadRequest(
        entry=DownloadEntry(
            index=7,
            media_id="NUL",
            title="NUL",
            page_url="https://example.com/watch",
            download_url="https://example.com/media",
        ),
        collection_title="CON",
        filename_prefix="😀" * 200,
        output_directory=str(tmp_path),
    )

    template = YtDlpDownloadService._output_template(tmp_path, request)

    assert template.parent.name == "_CON"
    assert "_NUL" in template.name
    assert len(template.parent.name.encode("utf-16-le")) // 2 <= 120
    assert len(template.name.encode("utf-16-le")) // 2 < 255
    assert "%(id).48B" in template.name
    assert template.parent.exists() is False


def test_download_output_template_keeps_user_percent_text_literal(tmp_path: Path) -> None:
    request = DownloadRequest(
        entry=DownloadEntry(
            index=1,
            media_id="actual-id",
            title="100%(id)s",
            page_url="https://example.com/watch",
            download_url="https://example.com/media",
        ),
        collection_title="Collection %(title)s",
        filename_prefix="Prefix %(id)s",
        output_directory=str(tmp_path),
    )

    template = YtDlpDownloadService._output_template(tmp_path, request)
    rendered = yt_dlp.YoutubeDL({"outtmpl": str(template), "quiet": True}).prepare_filename(
        {"id": "actual-id", "title": "actual-title", "ext": "mp4"}
    )

    assert "Collection %(title)s" in rendered
    assert "Prefix %(id)s - 100%(id)s" in rendered
    assert "actual-title" not in Path(rendered).parent.name


def test_download_output_template_budgets_the_final_expanded_windows_path(
    tmp_path: Path,
) -> None:
    request = DownloadRequest(
        entry=DownloadEntry(
            index=123456789,
            media_id="entry",
            title="😀" * 200,
            page_url="https://example.com/watch",
            download_url="https://example.com/media",
        ),
        collection_title="Collection " + "😀" * 200,
        filename_prefix="Prefix " + "😀" * 200,
        output_directory=str(tmp_path),
    )

    template = YtDlpDownloadService._output_template(tmp_path, request)
    rendered = yt_dlp.YoutubeDL({"outtmpl": str(template), "quiet": True}).prepare_filename(
        {
            "id": "i" * 256,
            "title": "t" * 256,
            "ext": "e" * 128,
        }
    )

    assert utf16_units(rendered) <= WINDOWS_INTEROP_PATH_UTF16_LIMIT
    assert utf16_units(Path(rendered).name) <= WINDOWS_INTEROP_PATH_UTF16_LIMIT


def test_download_path_preflight_runs_before_directory_creation_or_network(
    tmp_path: Path,
) -> None:
    request_seen = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            request_seen.set()
            self.send_response(500)
            self.end_headers()

        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    first_missing = tmp_path / "must-not-be-created"
    output_dir = first_missing
    while (
        utf16_units(str(output_dir))
        <= WINDOWS_INTEROP_PATH_UTF16_LIMIT
    ):
        output_dir /= "deep-download-directory"
    url = f"http://127.0.0.1:{server.server_address[1]}/media.mp4"
    request = DownloadRequest(
        entry=DownloadEntry(
            index=1,
            media_id="deep",
            title="Deep path",
            page_url=url,
            download_url=url,
        ),
        output_directory=str(output_dir),
    )
    try:
        with pytest.raises(ValueError, match="路径过深"):
            YtDlpDownloadService().download(request)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert not first_missing.exists()
    assert request_seen.is_set() is False


def test_youtube_collection_planner_uses_independent_urls_and_keeps_unavailable_slots() -> None:
    plan = YtDlpDownloadService._plan_from_info(
        {
            "_type": "playlist",
            "id": "playlist",
            "title": "Course",
            "extractor_key": "YoutubeTab",
            "uploader": "Teacher",
            "entries": [
                {
                    "id": "one",
                    "playlist_index": 1,
                    "title": "Lesson 1",
                    "webpage_url": "https://example.com/one",
                    "duration": 61,
                },
                None,
                {
                    "id": "three",
                    "playlist_index": 3,
                    "ie_key": "Youtube",
                    "title": "Lesson 3",
                    "duration": 72,
                },
            ],
        },
        "https://www.youtube.com/playlist?list=course",
    )

    assert plan.kind == "collection"
    assert plan.collection_title == "Course"
    assert plan.entries[0].download_url == "https://example.com/one"
    assert plan.entries[0].selector is None
    assert plan.entries[1].available is False
    assert plan.entries[2].download_url == "https://www.youtube.com/watch?v=three"
    assert plan.entries[2].selector is None
    assert parse_download_entry_selection("1,3", {1, 3}) == [1, 3]


def _twitter_video(media_id: str) -> dict:
    return {
        "id_str": media_id,
        "type": "video",
        "media_url_https": f"https://pbs.twimg.com/{media_id}.jpg",
        "sizes": {"small": {"w": 320, "h": 180}},
        "original_info": {"width": 1280, "height": 720},
        "video_info": {
            "duration_millis": 2_000,
            "variants": [
                {
                    "content_type": "video/mp4",
                    "bitrate": 832_000,
                    "url": f"https://video.twimg.com/{media_id}.mp4",
                }
            ],
        },
    }


def _twitter_status(*, outer_video: bool) -> dict:
    return {
        "full_text": "Outer tweet quoting a video",
        "created_at": "Wed Oct 10 20:19:24 +0000 2018",
        "user": {"name": "Outer Author", "screen_name": "outer", "id_str": "7"},
        "extended_entities": {"media": [_twitter_video("outer-video")] if outer_video else []},
        "quoted_status": {"extended_entities": {"media": [_twitter_video("quoted-video")]}},
    }


def test_real_twitter_extractor_maps_quoted_video_and_outer_video_to_page_selectors(
    monkeypatch,
) -> None:
    source_url = "https://x.com/outer/status/123"
    extractor = TwitterIE()
    extractor.set_downloader(yt_dlp.YoutubeDL({"quiet": True}))
    monkeypatch.setattr(extractor, "_extract_status", lambda _tweet_id: _twitter_status(outer_video=True))

    extracted = extractor._real_extract(source_url)
    plan = YtDlpDownloadService._plan_from_info(extracted, source_url)

    assert extracted["_type"] == "playlist"
    assert [entry.media_id for entry in plan.entries] == ["outer-video", "quoted-video"]
    assert [entry.selector for entry in plan.entries] == [1, 2]
    assert {entry.download_url for entry in plan.entries} == {source_url}

    monkeypatch.setattr(
        extractor,
        "_extract_status",
        lambda _tweet_id: _twitter_status(outer_video=False),
    )
    quoted_only = extractor._real_extract(source_url)
    quoted_plan = YtDlpDownloadService._plan_from_info(quoted_only, source_url)
    assert quoted_plan.kind == "single"
    assert quoted_plan.entries[0].media_id == "quoted-video"


def test_x_guest_token_error_keeps_mediaflow_classification() -> None:
    error = classify_download_error(
        "ERROR: [twitter] Bad guest token; please report this issue",
        url="https://x.com/outer/status/123",
    )

    assert error.code == "twitter_guest_token"
    assert "X/Twitter 游客访问失败" in error.display_message


def test_bilibili_analysis_restores_collections_and_multi_part_entries() -> None:
    collection = PlatformMediaResolver._bilibili_plan(
        "https://www.bilibili.com/video/BV1234567890",
        {
            "bvid": "BV1234567890",
            "title": "Current video",
            "duration": 80,
            "pic": "cover.jpg",
            "owner": {"name": "Creator"},
            "ugc_season": {
                "title": "Complete course",
                "sections": [
                    {
                        "episodes": [
                            {
                                "bvid": "BV0000000001",
                                "title": "First lesson",
                                "arc": {"duration": 41, "pic": "one.jpg"},
                            },
                            {
                                "bvid": "BV0000000002",
                                "title": "Second lesson",
                                "arc": {"duration": 39, "pic": "two.jpg"},
                            },
                        ]
                    }
                ],
            },
        },
    )

    assert collection is not None
    assert collection.title == "Complete course"
    assert len(collection.entries) == 2
    assert collection.entries[1].download_url == "https://www.bilibili.com/video/BV0000000002"

    parts = PlatformMediaResolver._bilibili_plan(
        "https://www.bilibili.com/video/BV1234567890",
        {
            "bvid": "BV1234567890",
            "title": "Series",
            "owner": {"name": "Creator"},
            "pages": [
                {"page": 1, "part": "Opening", "duration": 10},
                {"page": 2, "part": "Main", "duration": 20},
            ],
        },
    )
    assert parts is not None
    assert parts.entries[1].title == "P2 - Main"
    assert parts.entries[1].download_url.endswith("?p=2")


def test_real_browser_sniffer_observes_page_media_request_and_title() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/watch":
                payload = (
                    b'<html><head><meta property="og:title" content="Browser video"></head>'
                    b'<body><video autoplay muted src="/stream.mp4?video_id=local"></video></body></html>'
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
            else:
                payload = b"not-a-real-video-but-an-observable-media-request"
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/watch"
        result = PlatformMediaResolver._sniff_browser_media(url, timeout=3)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result is not None
    assert result.source_url == url
    assert result.download_url.endswith("/stream.mp4?video_id=local")
    assert result.title == "Browser video"


def test_browser_platform_analysis_forwards_configured_proxy(monkeypatch) -> None:
    observed: dict[str, str | None] = {}

    def sniff(
        _resolver_type,
        url: str,
        *,
        timeout: float = 15.0,
        proxy: str | None = None,
        check_cancelled=None,
    ) -> ResolvedPlatformMedia:
        observed["url"] = url
        observed["proxy"] = proxy
        observed["timeout"] = str(timeout)
        return ResolvedPlatformMedia(url, "https://media.example/video.mp4", "Video", "Douyin")

    monkeypatch.setattr(PlatformMediaResolver, "_sniff_browser_media", classmethod(sniff))
    url = "https://www.douyin.com/video/123"

    plan = PlatformMediaResolver().analyze(url, proxy="http://127.0.0.1:7890")

    assert plan is not None
    assert plan.entries[0].download_url == "https://media.example/video.mp4"
    assert observed == {
        "url": url,
        "proxy": "http://127.0.0.1:7890",
        "timeout": "15.0",
    }


def test_ytdlp_analysis_cancels_after_a_bounded_socket_wait(monkeypatch) -> None:
    request_seen = threading.Event()
    release_response = threading.Event()
    cancel_requested = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            request_seen.set()
            release_response.wait(timeout=5)

        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(ytdlp_service, "YTDLP_SOCKET_TIMEOUT_SECONDS", 0.25)

    def check_cancelled() -> None:
        if cancel_requested.is_set():
            raise CancelledError("closing")

    url = f"http://127.0.0.1:{server.server_address[1]}/watch"
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                YtDlpDownloadService().analyze,
                url,
                check_cancelled=check_cancelled,
            )
            assert request_seen.wait(timeout=3)
            started = time.monotonic()
            cancel_requested.set()
            with pytest.raises(CancelledError, match="closing"):
                future.result(timeout=2)
            assert time.monotonic() - started < 1.5
    finally:
        release_response.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_ytdlp_download_observes_cancellation_while_streaming(tmp_path: Path) -> None:
    web_root = tmp_path / "web"
    web_root.mkdir()
    source = web_root / "large.mp4"
    source.write_bytes(b"x" * (4 * 1024 * 1024))
    download_started = threading.Event()
    cancel_requested = threading.Event()

    class Handler(SimpleHTTPRequestHandler):
        def copyfile(self, source_file, output_file) -> None:
            try:
                while chunk := source_file.read(4096):
                    output_file.write(chunk)
                    output_file.flush()
                    time.sleep(0.005)
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(Handler, directory=str(web_root)),
    )
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def check_cancelled() -> None:
        if cancel_requested.is_set():
            raise CancelledError("cancel download")

    url = f"http://127.0.0.1:{server.server_address[1]}/{source.name}"
    request = DownloadRequest(
        entry=DownloadEntry(
            index=1,
            media_id="large",
            title="Large",
            page_url=url,
            download_url=url,
        ),
        output_directory=str((tmp_path / "downloads").resolve()),
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                YtDlpDownloadService().download,
                request,
                progress=lambda _progress: download_started.set(),
                check_cancelled=check_cancelled,
            )
            assert download_started.wait(timeout=3)
            cancel_requested.set()
            with pytest.raises(CancelledError, match="cancel download"):
                future.result(timeout=3)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    output_dir = Path(request.output_directory)
    visible_files = [
        path
        for path in output_dir.rglob("*")
        if path.is_file()
        and "MediaFlow Failed Downloads"
        not in path.parts
    ]
    assert visible_files == []
    assert not list(
        output_dir.parent.glob(".mf-dl-*")
    )


def test_cookie_store_converts_browser_json_and_resolves_site_cookie(tmp_path) -> None:
    store = CookieStore(tmp_path / "cookies")
    path = store.save(
        "www.x.com",
        [
            {
                "domain": ".x.com",
                "path": "/",
                "secure": True,
                "expirationDate": 4_102_444_800,
                "name": "auth_token",
                "value": "secret-value",
            }
        ],
    )

    content = path.read_text(encoding="utf-8")
    assert content.startswith("# Netscape HTTP Cookie File")
    assert ".x.com\tTRUE\t/\tTRUE\t4102444800\tauth_token\tsecret-value" in content
    assert store.status("x.com")["valid"] is True
    assert store.resolve_for_url("https://video.x.com/watch/1") == path
    assert store.resolve_for_url("https://twitter.com/user/status/1") == path


def test_cookie_store_rejects_expired_cookie_files_but_keeps_session_cookies(
    tmp_path: Path,
) -> None:
    store = CookieStore(tmp_path / "cookies")
    session_path = store.save(
        "example.com",
        [
            {
                "domain": ".example.com",
                "name": "session",
                "value": "active",
                "expires": -1,
            },
            {
                "domain": ".example.com",
                "name": "expired",
                "value": "stale",
                "expires": 1,
            },
        ],
    )

    content = session_path.read_text(encoding="utf-8")
    assert "\t0\tsession\tactive" in content
    assert "\texpired\tstale" not in content
    assert store.resolve_for_url("https://www.example.com/watch") == session_path

    expired_path = store.path_for_domain("expired.example")
    expired_path.write_text(
        "\n".join(
            (
                "# Netscape HTTP Cookie File",
                ".expired.example\tTRUE\t/\tTRUE\t1\tauth\tstale",
                "",
            )
        ),
        encoding="utf-8",
    )

    assert store.status("expired.example")["exists"] is True
    assert store.status("expired.example")["valid"] is False
    assert store.resolve_for_url("https://expired.example/video") is None
