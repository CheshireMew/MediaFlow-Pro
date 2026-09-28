from __future__ import annotations

import hashlib
import json
import zipfile
from concurrent.futures import CancelledError
from pathlib import Path

import pytest
import yt_dlp

from mediaflow.domain.downloads import DownloadEntry, DownloadRequest
from mediaflow.infrastructure import youtube_runtime
from mediaflow.infrastructure.cookie_store import CookieStore
from mediaflow.infrastructure.download_errors import (
    DownloadExtractionError,
    YtDlpErrorCapture,
    classify_download_error,
)
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.ytdlp_installer import install_ytdlp
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("HTTP Error 403: Forbidden. Use --cookies for authentication", "access_denied"),
        ("Could not copy Chrome cookie database", "cookie_read_failed"),
        ("Failed to decrypt with DPAPI", "cookie_read_failed"),
        ("does not look like a Netscape format cookies file", "cookie_read_failed"),
        ("Sign in to confirm you’re not a bot. Use --cookies for authentication", "bot_check"),
        ("HTTP Error 429: Too Many Requests. Use --cookies for authentication", "rate_limited"),
        ("HTTP Error 407: Proxy Authentication Required", "proxy"),
        ("Unable to connect to proxy: connection refused", "proxy"),
        ("Connection timed out", "network"),
        ("No supported JavaScript runtime could be found\nNo video formats found", "youtube_runtime"),
        ("n challenge solving failed\nRequested format is not available", "youtube_runtime"),
        ("PO Token required\nNo video formats found", "youtube_playback"),
        ("This video is private. Sign in to access it", "auth_required"),
        ("This video is age-restricted", "auth_required"),
        ("[twitter] Bad guest token", "twitter_guest_token"),
        ("Video unavailable", "content_unavailable"),
        ("No supported JavaScript runtime\nThis video is private", "auth_required"),
    ],
)
def test_errors_distinguish_account_permissions_from_runtime_and_network(message, expected):
    url = (
        "https://x.com/user/status/1" if expected == "twitter_guest_token" else "https://youtu.be/QggFvDB3qwU"
    )
    result = classify_download_error(message, url=url)
    assert result.code == expected
    assert result.original == message
    if expected == "auth_required":
        assert result.cookie_domain == "youtube.com"
        assert "下载设置" in result.action
    assert classify_download_error(DownloadExtractionError(result)) is result


def test_capture_preserves_warnings_in_diagnostics_and_log(caplog):
    capture = YtDlpErrorCapture()
    capture.warning("No supported JavaScript runtime")
    capture.error("No video formats found")
    assert "No supported JavaScript runtime" in capture.text
    assert "No video formats found" in capture.text
    assert "No supported JavaScript runtime" in caplog.text


@pytest.mark.parametrize("operation", ["analyze", "download"])
def test_warning_never_hides_the_final_download_error(tmp_path, monkeypatch, operation):
    class FailingYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, *_args, **_kwargs):
            self.options["logger"].warning("No supported JavaScript runtime could be found")
            raise yt_dlp.utils.DownloadError("This video is private. Sign in to access it")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FailingYoutubeDL)
    paths = RuntimePaths(runtime_dir=tmp_path, ffmpeg=tmp_path / "ffmpeg", ffprobe=tmp_path / "ffprobe")
    service = YtDlpDownloadService(paths)
    url = "https://youtu.be/QggFvDB3qwU"
    with pytest.raises(DownloadExtractionError) as caught:
        if operation == "analyze":
            service.analyze(url)
        else:
            service._download_to_directory(
                DownloadRequest(
                    entry=DownloadEntry(index=1, title="test", page_url=url, download_url=url),
                    output_directory=str(tmp_path),
                ),
                tmp_path,
                cookie_file=None,
                browser_cookies=None,
                proxy=None,
                maximum_file_bytes=1024,
                progress=None,
                check_cancelled=None,
            )
    assert caught.value.error.code == "auth_required"
    assert "No supported JavaScript runtime" in caught.value.error.original
    assert "This video is private" in caught.value.error.original


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/QggFvDB3qwU",
        "https://www.youtube.com/watch?v=QggFvDB3qwU",
        "https://m.youtube.com/watch?v=QggFvDB3qwU",
    ],
)
def test_youtube_short_links_reuse_youtube_account_cookies(tmp_path, url):
    store = CookieStore(tmp_path / "cookies")
    cookie = store.save("youtube.com", [{"domain": ".youtube.com", "name": "session", "value": "test"}])
    assert store.resolve_for_url(url) == cookie
    assert store.resolve_for_url("https://notyoutube.com/watch") is None


def test_analysis_and_download_share_enabled_js_and_visible_warnings(tmp_path, monkeypatch):
    monkeypatch.setattr(
        youtube_runtime.shutil, "which", lambda name: "D:/Tools/NodeJS/node.exe" if name == "node" else None
    )
    paths = RuntimePaths(
        runtime_dir=tmp_path, ffmpeg=tmp_path / "ffmpeg.exe", ffprobe=tmp_path / "ffprobe.exe"
    )
    options = YtDlpDownloadService(paths)._base_options(None, None, None)
    assert options["js_runtimes"] == {"node": {"path": "D:/Tools/NodeJS/node.exe"}}
    assert options["cachedir"] == str(tmp_path / "cache" / "yt-dlp")
    assert options["no_warnings"] is False
    assert "cookiefile" not in options and "cookiesfrombrowser" not in options


def _release(root: Path, package: str, version: str, *, requires: str = "") -> Path:
    wheel = root / f"{package}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"{package}/__init__.py", f"__version__ = {version!r}\n")
        archive.writestr(
            f"{package}-{version}.dist-info/METADATA",
            (f"Name: {package.replace('_', '-')}\nVersion: {version}\n{requires}"),
        )
    metadata = root / f"{package}.json"
    metadata.write_text(
        json.dumps(
            {
                "info": {"version": version},
                "urls": [
                    {
                        "filename": wheel.name,
                        "packagetype": "bdist_wheel",
                        "url": wheel.as_uri(),
                        "size": wheel.stat().st_size,
                        "digests": {"sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return metadata


@pytest.fixture
def releases(tmp_path):
    ytdlp = _release(
        tmp_path, "yt_dlp", "2099.1", requires='Requires-Dist: yt-dlp-ejs==0.8.0; extra == "default"\n'
    )
    ejs = _release(tmp_path, "yt_dlp_ejs", "0.8.0")
    runtime = tmp_path / "runtime"
    pointer = runtime / "tools" / "yt-dlp-active.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text('{"version":"previous","path":"previous-install"}', encoding="utf-8")
    return runtime, pointer, ytdlp, ejs


def test_updater_publishes_ytdlp_and_matching_ejs_together(releases):
    runtime, pointer, ytdlp, ejs = releases
    result = install_ytdlp(runtime, ytdlp.as_uri(), ejs_metadata_url=ejs.as_uri())
    assert json.loads(pointer.read_text(encoding="utf-8")) == result
    assert result["ejs_version"] == "0.8.0"
    assert (Path(result["path"]) / "yt_dlp_ejs" / "__init__.py").is_file()
    again = install_ytdlp(runtime, ytdlp.as_uri(), ejs_metadata_url=ejs.as_uri())
    assert again["path"] != result["path"]
    assert (Path(result["path"]) / "yt_dlp" / "__init__.py").is_file()


@pytest.mark.parametrize("failure", ["hash", "version", "missing_requirement", "missing_package", "cancel"])
def test_failed_or_cancelled_update_preserves_previous_install(releases, failure):
    runtime, pointer, ytdlp, ejs = releases
    previous = pointer.read_bytes()
    metadata = json.loads(ejs.read_text(encoding="utf-8"))
    if failure == "hash":
        metadata["urls"][0]["digests"]["sha256"] = "0" * 64
    elif failure == "version":
        metadata["info"]["version"] = "0.9.0"
    elif failure == "missing_requirement":
        ytdlp = _release(ytdlp.parent, "yt_dlp", "2099.1")
    elif failure == "missing_package":
        wheel = ejs.parent / metadata["urls"][0]["filename"]
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("yt_dlp_ejs-0.8.0.dist-info/METADATA", "Name: yt-dlp-ejs\nVersion: 0.8.0\n")
        metadata["urls"][0].update(
            size=wheel.stat().st_size, digests={"sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
        )
    ejs.write_text(json.dumps(metadata), encoding="utf-8")

    def check_cancelled():
        if failure == "cancel" and (runtime / "tools" / "python").exists():
            raise CancelledError("cancel before activation")

    with pytest.raises((RuntimeError, CancelledError)):
        install_ytdlp(runtime, ytdlp.as_uri(), ejs_metadata_url=ejs.as_uri(), check_cancelled=check_cancelled)
    assert pointer.read_bytes() == previous
