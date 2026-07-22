from __future__ import annotations

import json
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PySide6.QtGui import QImage

from mediaflow.application.asset_service import AssetService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.composition import EditorProject
from mediaflow.domain.downloads import DownloadRequest
from mediaflow.domain.enums import AssetKind, AssetOrigin, ColorMode, TaskStatus, TrackKind
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.settings import GlobalSettings
from mediaflow.domain.task_commands import DownloadMediaCommand
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.media_thumbnail_service import MediaThumbnailService
from mediaflow.infrastructure.mlt import TimelineCompiler
from mediaflow.infrastructure.project_cover_service import ProjectCoverService
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


def generate_black_intro_video(path: Path, paths: RuntimePaths) -> None:
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
            "color=black:size=160x90:rate=25:duration=0.4",
            "-f",
            "lavfi",
            "-i",
            "color=0xd33f32:size=160x90:rate=25:duration=0.6",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[out]",
            "-map",
            "[out]",
            "-c:v",
            "libx264",
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
        cover = ProjectCoverService(paths).cover_for(repository)
        assert cover is not None and cover.is_file() and cover.stat().st_size > 0
        cover_probe = MediaProbe(paths).probe(cover)
        assert cover_probe.kind == AssetKind.IMAGE
        assert (cover_probe.metadata.width, cover_probe.metadata.height) == (640, 360)
        assert ProjectCoverService(paths).cover_for(repository) == cover
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


def test_media_thumbnail_uses_first_visible_video_frame_and_scales_images(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    video_source = tmp_path / "black-intro.mp4"
    generate_black_intro_video(video_source, paths)
    image_source = tmp_path / "portrait.png"
    portrait = QImage(40, 100, QImage.Format.Format_RGB32)
    portrait.fill(0xFF3A7DC4)
    assert portrait.save(str(image_source))

    with ProjectRepository.create(tmp_path / "Thumbnail Project", "Thumbnail Project") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        video = assets.import_external(video_source)
        image = assets.import_external(image_source)
        thumbnails = MediaThumbnailService(paths)

        video_thumbnail = thumbnails.thumbnail_for(repository, video, width=160, height=90)
        image_thumbnail = thumbnails.thumbnail_for(repository, image, width=160, height=90)

        assert video_thumbnail is not None and video_thumbnail.is_file()
        assert image_thumbnail is not None and image_thumbnail.is_file()
        rendered_video = QImage(str(video_thumbnail))
        rendered_image = QImage(str(image_thumbnail))
        assert (rendered_video.width(), rendered_video.height()) == (160, 90)
        assert (rendered_image.width(), rendered_image.height()) == (160, 90)
        video_center = rendered_video.pixelColor(80, 45)
        assert video_center.red() > 150 and video_center.red() > video_center.green() * 2
        assert rendered_image.pixelColor(80, 45).blue() > 120
        assert rendered_image.pixelColor(10, 45).lightness() < 60
        assert thumbnails.thumbnail_for(repository, video, width=160, height=90) == video_thumbnail


def test_real_ytdlp_download_returns_files_then_application_registers_assets(tmp_path: Path) -> None:
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
            downloader = YtDlpDownloadService()
            url = f"http://127.0.0.1:{server.server_address[1]}/sample.mp4"
            analyzed = downloader.analyze(url)
            media_workspace = tmp_path / "WorkSpace"
            downloaded = downloader.download(
                DownloadRequest(
                    entry=analyzed.entries[0],
                    output_directory=str(media_workspace.resolve()),
                ),
            )

            assert analyzed.title == "sample"
            assert len(downloaded) == 1
            asset = asset_service.register_output(downloaded[0], AssetOrigin.DOWNLOAD)
            assert asset.managed is False
            assert Path(asset.path).parent == media_workspace.resolve()
            assert Path(asset.path).is_file()
            assert repository.list_assets()[0].id == asset.id
            collection_entry = analyzed.entries[0].model_copy(update={"index": 1, "title": "Lesson One"})
            collection_output = downloader.download(
                DownloadRequest(
                    entry=collection_entry,
                    collection_title="Course",
                    output_directory=str(media_workspace.resolve()),
                ),
            )[0]
            collection_asset = asset_service.register_output(
                collection_output,
                AssetOrigin.DOWNLOAD,
            )
            collection_path = Path(collection_asset.path)
            assert collection_path.parent.name == "Course"
            assert collection_path.name.startswith("001 Lesson One [")
        external_directory = tmp_path / "Configured Downloads"
        with ProjectRepository.create(tmp_path / "External Project", "External") as repository:
            downloaded = YtDlpDownloadService().download(
                DownloadRequest(
                    entry=analyzed.entries[0],
                    output_directory=str(external_directory.resolve()),
                ),
            )
            asset = AssetService(repository, MediaProbe(paths)).register_output(
                downloaded[0],
                AssetOrigin.DOWNLOAD,
            )
            assert asset.managed is False
            assert Path(asset.path).parent == external_directory.resolve()
            assert Path(asset.path).is_file()
            assert repository.resolve_asset_path(asset) == Path(asset.path)
        task_repository = ProjectRepository.create(tmp_path / "Task Download", "Task Download")
        project = EditorProject(task_repository, settings=GlobalSettings(), paths=paths)
        try:
            plan = YtDlpDownloadService().analyze(url)
            entry = plan.entries[0].model_copy(update={"index": 7, "title": "Task Lesson"})
            selected_output = tmp_path / "Task Selected Output"
            request = DownloadRequest(
                entry=entry,
                collection_title="Task Course",
                output_directory=str(selected_output.resolve()),
            )
            task = project.start_task(
                DownloadMediaCommand(request=request),
            )

            completed = project.tasks.wait(task.id, timeout=30)
            persisted = project.tasks.get(task.id)
            registered = task_repository.list_assets()

            assert completed.status == TaskStatus.COMPLETED
            assert isinstance(persisted.command, DownloadMediaCommand)
            assert persisted.command.request == request
            assert len(registered) == 1
            visible_path = task_repository.resolve_asset_path(registered[0])
            assert visible_path.is_file()
            assert visible_path.parent.name == "Task Course"
            assert visible_path.name.startswith("007 Task Lesson [")
            assert visible_path.parent.parent == selected_output.resolve()
            assert registered[0].managed is False
        finally:
            project.close()
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
