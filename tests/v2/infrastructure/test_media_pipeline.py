from __future__ import annotations

import json
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mediaflow.application.asset_service import AssetService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.enums import AssetKind, ColorMode, TrackKind
from mediaflow.domain.models import ProjectProfile
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt import TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.proxy_service import ProxyService
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.waveform_service import WaveformService
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService


def generate_real_media(path: Path, paths: RuntimePaths, *, width: int = 640, height: int = 360) -> None:
    result = subprocess.run(
        [
            str(paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={width}x{height}:rate=25:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert path.is_file() and path.stat().st_size > 0


def test_real_ffmpeg_media_becomes_project_asset_proxy_and_waveform(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "source.mp4"
    generate_real_media(source, paths)

    with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        asset = assets.import_external(source)

        assert asset.kind == AssetKind.VIDEO
        assert asset.metadata.has_video is True
        assert asset.metadata.has_audio is True
        assert asset.metadata.duration_frames == 30
        default_profile = repository.get_sequence(repository.get_project().main_sequence_id).profile
        assert (default_profile.width, default_profile.height, default_profile.fps_numerator) == (
            1920,
            1080,
            30,
        )

        asset = assets.adopt_main_profile_from_video(asset.id)
        assert asset.metadata.duration_frames == 25
        profile = repository.get_sequence(repository.get_project().main_sequence_id).profile
        assert (profile.width, profile.height, profile.fps_numerator) == (640, 360, 25)

        proxied = ProxyService(repository, paths).generate(asset, profile)
        proxy_path = repository.project_dir / proxied.proxy_path
        assert proxy_path.is_file()
        proxy_probe = MediaProbe(paths).probe(proxy_path, timeline_profile=profile)
        assert proxy_probe.metadata.has_video is True

        waveform_asset = WaveformService(repository, paths).generate(proxied)
        waveform_path = repository.project_dir / waveform_asset.waveform_path
        payload = json.loads(waveform_path.read_text(encoding="utf-8"))
        assert payload["sample_rate"] == 8000
        assert payload["sample_count"] > 7000
        assert len(payload["levels"]["128"]) > 0


def test_real_ytdlp_download_registers_observable_managed_asset(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    web_root = tmp_path / "web"
    web_root.mkdir()
    source = web_root / "sample.mp4"
    generate_real_media(source, paths, width=320, height=180)

    handler = partial(SimpleHTTPRequestHandler, directory=str(web_root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with ProjectRepository.create(tmp_path / "Project", "Project") as repository:
            asset_service = AssetService(repository, MediaProbe(paths))
            downloader = YtDlpDownloadService(asset_service)
            url = f"http://127.0.0.1:{server.server_address[1]}/sample.mp4"
            analyzed = downloader.analyze(url)
            downloaded = downloader.download(url)

            assert analyzed["title"] == "sample"
            assert len(downloaded) == 1
            asset = downloaded[0]
            assert asset.managed is True
            assert asset.path.startswith("downloads/")
            assert (repository.project_dir / asset.path).is_file()
            assert repository.list_assets()[0].id == asset.id
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_hdr_project_generates_hdr_and_sdr_display_proxies(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    source = tmp_path / "source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    profile = ProjectProfile(
        width=320,
        height=180,
        fps_numerator=25,
        fps_denominator=1,
        color_mode=ColorMode.HDR10_BT2020_PQ,
        bit_depth=10,
    )
    with ProjectRepository.create(tmp_path / "HDR Proxy", "HDR Proxy", profile) as repository:
        asset = AssetService(repository, MediaProbe(paths)).import_external(source)
        proxied = ProxyService(repository, paths).generate(asset, profile)
        hdr_path = repository.project_dir / proxied.proxy_path
        sdr_path = repository.project_dir / proxied.sdr_preview_proxy_path

        hdr = MediaProbe(paths).probe(hdr_path, timeline_profile=profile)
        sdr = MediaProbe(paths).probe(sdr_path, timeline_profile=profile)
        assert hdr.metadata.pixel_format == "yuv420p10le"
        assert hdr.metadata.color_primaries == "bt2020"
        assert sdr.metadata.pixel_format == "yuv420p"
        assert sdr.metadata.color_primaries == "bt709"
        editor = TimelineEditor(repository, repository.get_project().main_sequence_id)
        video_track = next(track for track in editor.state.tracks if track.kind == TrackKind.VIDEO)
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )
        document = TimelineCompiler(repository).compile(
            editor.state,
            use_proxies=True,
            native_preview=True,
            prefer_sdr_preview_proxy=True,
        )
        assert sdr_path.resolve() in document.source_paths
        assert hdr_path.resolve() not in document.source_paths
