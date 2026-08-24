from __future__ import annotations

import os
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

import mediaflow.infrastructure.waveform_service as waveform_service_module
from mediaflow.application.asset_service import AssetService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.composition import EditorProject
from mediaflow.domain.downloads import DownloadRequest
from mediaflow.domain.enums import AssetKind, AssetOrigin, ColorMode, TaskStatus, TrackKind
from mediaflow.domain.progress import OperationProgress
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.storage_names import (
    WINDOWS_INTEROP_PATH_UTF16_LIMIT,
    safe_child_path,
    utf16_units,
)
from mediaflow.domain.task_commands import DownloadMediaCommand, GenerateWaveformCommand
from mediaflow.domain.tasks import Task
from mediaflow.infrastructure import media_probe
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.media_thumbnail_service import MediaThumbnailService
from mediaflow.infrastructure.mlt import TimelineCompiler
from mediaflow.infrastructure.project_cover_service import ProjectCoverService
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.proxy_service import ProxyService
from mediaflow.infrastructure.runtime_context import RuntimeContext
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.visual_analysis import (
    SceneDetectionService,
    SubjectMotionService,
)
from mediaflow.infrastructure.waveform_service import WaveformService
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService
from mediaflow.waveform_cache import (
    inspect_waveform_cache,
    read_waveform_peaks,
)
from tests.v2.real_media import generate_real_media


def test_native_media_services_reject_an_overlong_external_source_before_launch(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    short_source = tmp_path / "source.mp4"
    short_source.write_bytes(b"not launched by this boundary test")
    deep_source = safe_child_path(
        tmp_path,
        "deep-native-source-" * 32,
        suffix=".mp4",
        max_path_utf16_units=WINDOWS_INTEROP_PATH_UTF16_LIMIT + 1,
    )
    assert utf16_units(str(deep_source)) == WINDOWS_INTEROP_PATH_UTF16_LIMIT + 1
    deep_source.write_bytes(short_source.read_bytes())

    with ProjectRepository.create(
        tmp_path / "Native Boundaries",
        "Native Boundaries",
    ) as repository:
        asset = repository.assets.import_external_asset(
            short_source,
            AssetKind.VIDEO,
        )
        asset = repository.assets.update_asset(
            asset.model_copy(update={"path": str(deep_source)})
        )
        profile = repository.sequences.get_sequence(
            repository.projects.get_project().main_sequence_id
        ).profile
        editor = TimelineEditor(
            repository,
            repository.projects.get_project().main_sequence_id,
        )
        track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=1,
        )

        with pytest.raises(ValueError, match="路径过深"):
            MediaProbe(paths).probe(deep_source)
        with pytest.raises(ValueError, match="路径过深"):
            ProxyService(repository, paths).generate(asset, profile)
        with pytest.raises(ValueError, match="路径过深"):
            WaveformService(repository, paths).generate(
                asset,
                duration_seconds=1,
            )
        with pytest.raises(ValueError, match="路径过深"):
            MediaThumbnailService(paths).thumbnail_for(
                repository,
                asset,
                width=160,
                height=90,
            )
        with pytest.raises(ValueError, match="路径过深"):
            SceneDetectionService(paths).detect(
                deep_source,
                clip,
                profile,
            )
        with pytest.raises(ValueError, match="路径过深"):
            SubjectMotionService().analyze(
                deep_source,
                clip,
                profile,
                mode="subject_tracking",
            )


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


def test_media_probe_rejects_deep_source_before_starting_ffprobe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = safe_child_path(
        tmp_path,
        "deep-native-source-" * 32,
        suffix=".mp4",
        max_path_utf16_units=WINDOWS_INTEROP_PATH_UTF16_LIMIT + 1,
    )
    assert utf16_units(str(source)) == WINDOWS_INTEROP_PATH_UTF16_LIMIT + 1
    source.write_bytes(b"not-probed")
    subprocess_started = False

    def fail_if_started(*_args, **_kwargs):
        nonlocal subprocess_started
        subprocess_started = True
        raise AssertionError("ffprobe must not start for an over-budget path")

    monkeypatch.setattr(media_probe.FfprobeRunner, "run", fail_if_started)

    with pytest.raises(ValueError, match="路径过深"):
        MediaProbe(RuntimeContext.discover().paths).probe(source)

    assert subprocess_started is False


def test_waveform_storage_budget_blocks_before_cache_or_decoder_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "source.wav"
    source.write_bytes(b"not-decoded")

    with ProjectRepository.create(tmp_path / "Waveform Budget", "Waveform Budget") as repository:
        asset = repository.assets.import_external_asset(source, AssetKind.AUDIO)
        cache_root = paths.project_cache_dir(repository.project_dir)
        decoder_started = False

        def block_storage(*_args, **kwargs) -> None:
            assert kwargs["expected_new_bytes"] == WaveformService._estimated_peak_bytes(1)
            assert not cache_root.exists()
            raise RuntimeError("storage preflight blocked")

        def fail_if_decoder_starts(*_args, **_kwargs):
            nonlocal decoder_started
            decoder_started = True
            raise AssertionError("waveform decoder started before storage preflight")

        monkeypatch.setattr(
            waveform_service_module,
            "reserve_project_cache",
            block_storage,
        )
        monkeypatch.setattr(
            waveform_service_module.av,
            "open",
            fail_if_decoder_starts,
        )

        with pytest.raises(RuntimeError, match="storage preflight blocked"):
            WaveformService(repository, paths).prepare(asset, duration_seconds=1)

        assert decoder_started is False
        assert not cache_root.exists()


def test_waveform_storage_peak_requires_known_duration() -> None:
    assert WaveformService._estimated_peak_bytes(1) > 0
    with pytest.raises(ValueError, match="known positive media duration"):
        WaveformService._estimated_peak_bytes(0)


def test_real_ffmpeg_media_becomes_project_asset_proxy_and_waveform(
    tmp_path: Path,
    max_project_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "source.mp4"
    generate_real_media(source, paths)

    with ProjectRepository.create(max_project_path, "Project") as repository:
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
        default_profile = repository.sequences.get_sequence(
            repository.projects.get_project().main_sequence_id
        ).profile
        assert (default_profile.width, default_profile.height, default_profile.fps_numerator) == (
            1920,
            1080,
            30,
        )

        asset = assets.adopt_main_profile_from_video(asset.id)
        assert asset.metadata.duration_frames == 25
        main_sequence_id = repository.projects.get_project().main_sequence_id
        profile = repository.sequences.get_sequence(main_sequence_id).profile
        assert (profile.width, profile.height, profile.fps_numerator) == (640, 360, 25)

        proxy_progress = []
        proxied = ProxyService(repository, paths).generate(
            asset,
            profile,
            progress=proxy_progress.append,
        )
        proxy_path = repository.project_dir / proxied.proxy_path
        assert proxy_path.is_file()
        assert utf16_units(str(proxy_path)) <= 240
        proxy_probe = MediaProbe(paths).probe(proxy_path, timeline_profile=profile)
        assert proxy_probe.metadata.has_video is True
        proxy_encoding = [
            item for item in proxy_progress if item.message_code == "proxy_encoding"
        ]
        assert proxy_encoding
        assert proxy_encoding[-1].completed == proxy_encoding[-1].total

        waveform_progress = []
        waveform_asset = WaveformService(repository, paths).generate(
            proxied,
            duration_seconds=proxied.metadata.duration_frames / profile.fps,
            progress=waveform_progress.append,
        )
        waveform_path = repository.project_dir / waveform_asset.waveform_path
        header = inspect_waveform_cache(waveform_path)
        assert header.sample_rate == 8000
        assert header.sample_count > 7000
        assert header.levels[0].count > 0
        assert read_waveform_peaks(
            waveform_path,
            offset=header.levels[0].offset,
            count=header.levels[0].count,
            first=0,
            last=1,
        )
        calculating = [
            item for item in waveform_progress if item.message_code == "waveform_calculating"
        ]
        assert calculating
        assert all(item.mode == "determinate" and item.unit == "samples" for item in calculating)
        assert calculating[-1].completed == calculating[-1].total
        assert waveform_progress[-1].message_code == "waveform_saving"
        assert utf16_units(str(waveform_path)) <= 240
        assert not list(repository.project_dir.rglob(".mf-*"))


def test_waveform_task_progress_survives_events_storage_and_artifact_consumption(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "task-waveform.mp4"
    generate_real_media(source, paths)
    repository = ProjectRepository.create(tmp_path / "Task Waveform", "Task Waveform")
    project = EditorProject(repository, settings=ServiceSettings(), paths=paths)
    progress_events: list[OperationProgress] = []
    transported_tasks: list[Task] = []

    def capture(event) -> None:
        transported = Task.model_validate(event.payload)
        transported_tasks.append(transported)
        if event.event_type == "progress":
            progress_events.append(transported.progress)

    project.subscribe_task_events(capture, include_snapshot=False)
    try:
        asset = project.import_external_asset(source)
        task = project.start_task(
            GenerateWaveformCommand(asset_id=asset.id),
            [asset.id],
        )
        completed = project.wait_for_task(task.id, timeout=30)
        persisted = project.get_task(task.id)
        updated_asset = repository.assets.get_asset(asset.id)
        assert updated_asset.waveform_path
        waveform_path = repository.project_dir / updated_asset.waveform_path
        waveform_header = inspect_waveform_cache(waveform_path)

        decoding = [
            item for item in progress_events if item.message_code == "waveform_decoding"
        ]
        calculating = [
            item for item in progress_events if item.message_code == "waveform_calculating"
        ]
        assert decoding
        assert decoding[-1].mode == "determinate"
        assert decoding[-1].unit == "media_seconds"
        assert decoding[-1].completed == decoding[-1].total
        assert calculating
        assert calculating[-1].unit == "samples"
        assert calculating[-1].completed == calculating[-1].total
        assert any(item.message_code == "waveform_saving" for item in progress_events)
        assert completed.status == TaskStatus.COMPLETED
        assert persisted == completed
        assert persisted.progress.mode == "determinate"
        assert persisted.progress.completed == persisted.progress.total == 1
        assert persisted.progress.unit == "task"
        assert transported_tasks[-1] == persisted
        waveform_path = Path(updated_asset.waveform_path)
        expected_waveform = (
            waveform_path
            if waveform_path.is_absolute()
            else repository.project_dir / waveform_path
        )
        assert [
            artifact.resolve(repository.project_dir)
            for artifact in completed.artifacts
        ] == [expected_waveform]
        assert waveform_header.sample_count > 0
        assert waveform_header.levels[0].count > 0
    finally:
        project.close()


def test_stale_waveform_producer_cannot_overwrite_current_asset_waveform(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    source = tmp_path / "changing-source.mp4"
    generate_real_media(source, paths, width=320, height=180)

    with ProjectRepository.create(tmp_path / "Waveform Versions", "Waveform Versions") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        stale_asset = assets.import_external(source)
        initial = WaveformService(repository, paths).generate(
            stale_asset,
            duration_seconds=1,
        )
        initial_path = repository.project_dir / initial.waveform_path
        initial_payload = initial_path.read_bytes()

        generate_real_media(source, paths, width=640, height=360)
        current_asset = repository.assets.refresh_asset_status(stale_asset.id)
        current = WaveformService(repository, paths).generate(
            current_asset,
            duration_seconds=1,
        )
        current_path = repository.project_dir / current.waveform_path
        current_payload = current_path.read_bytes()

        assert current_path != initial_path
        assert current_payload
        with pytest.raises(RuntimeError, match="素材在波形生成期间发生了变化"):
            WaveformService(repository, paths).generate(
                stale_asset,
                duration_seconds=1,
            )

        reloaded = repository.assets.get_asset(stale_asset.id)
        assert repository.project_dir / reloaded.waveform_path == current_path
        assert current_path.read_bytes() == current_payload
        assert initial_path.read_bytes() == initial_payload


def test_media_thumbnail_uses_first_visible_video_frame_and_scales_images(
    tmp_path: Path,
    max_project_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    video_source = tmp_path / "black-intro.mp4"
    generate_black_intro_video(video_source, paths)
    image_source = tmp_path / "portrait.png"
    portrait = QImage(40, 100, QImage.Format.Format_RGB32)
    portrait.fill(0xFF3A7DC4)
    assert portrait.save(str(image_source))

    with ProjectRepository.create(
        max_project_path,
        "Thumbnail Project",
    ) as repository:
        assets = AssetService(repository, MediaProbe(paths))
        video = assets.import_external(video_source)
        image = assets.import_external(image_source)
        thumbnails = MediaThumbnailService(paths)

        video_thumbnail = thumbnails.thumbnail_for(repository, video, width=160, height=90)
        image_thumbnail = thumbnails.thumbnail_for(repository, image, width=160, height=90)

        assert video_thumbnail is not None and video_thumbnail.is_file()
        assert image_thumbnail is not None and image_thumbnail.is_file()
        assert utf16_units(str(video_thumbnail)) <= 240
        assert utf16_units(str(image_thumbnail)) <= 240
        rendered_video = QImage(str(video_thumbnail))
        rendered_image = QImage(str(image_thumbnail))
        assert (rendered_video.width(), rendered_video.height()) == (160, 90)
        assert (rendered_image.width(), rendered_image.height()) == (160, 90)
        video_center = rendered_video.pixelColor(80, 45)
        assert video_center.red() > 150 and video_center.red() > video_center.green() * 2
        assert rendered_image.pixelColor(80, 45).blue() > 120
        assert rendered_image.pixelColor(10, 45).lightness() < 60
        assert thumbnails.thumbnail_for(repository, video, width=160, height=90) == video_thumbnail
        assert not list(repository.project_dir.rglob(".mf-*"))


def test_thumbnail_cache_tracks_content_when_size_and_timestamp_are_unchanged(
    tmp_path: Path,
) -> None:
    paths = RuntimeContext.discover().paths
    image_source = tmp_path / "changing.bmp"
    blue = QImage(64, 64, QImage.Format.Format_RGB32)
    blue.fill(0xFF145AC4)
    assert blue.save(str(image_source))
    source_stat = image_source.stat()

    with ProjectRepository.create(tmp_path / "Thumbnail Identity", "Thumbnail Identity") as repository:
        assets = AssetService(repository, MediaProbe(paths))
        initial_asset = assets.import_external(image_source)
        thumbnails = MediaThumbnailService(paths)
        initial_thumbnail = thumbnails.thumbnail_for(
            repository,
            initial_asset,
            width=64,
            height=64,
        )
        assert initial_thumbnail is not None
        assert QImage(str(initial_thumbnail)).pixelColor(32, 32).blue() > 150

        red = QImage(64, 64, QImage.Format.Format_RGB32)
        red.fill(0xFFD32626)
        assert red.save(str(image_source))
        assert image_source.stat().st_size == source_stat.st_size
        os.utime(
            image_source,
            ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
        )
        current_asset = repository.assets.refresh_asset_status(initial_asset.id)
        assert current_asset.fingerprint != initial_asset.fingerprint

        current_thumbnail = thumbnails.thumbnail_for(
            repository,
            current_asset,
            width=64,
            height=64,
        )

        assert current_thumbnail is not None
        assert current_thumbnail != initial_thumbnail
        rendered = QImage(str(current_thumbnail))
        center = rendered.pixelColor(32, 32)
        assert center.red() > 150 and center.red() > center.blue() * 2


def test_real_ytdlp_download_returns_files_then_application_registers_assets(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
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
            downloader = YtDlpDownloadService(paths)
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
            assert repository.assets.list_assets()[0].id == asset.id
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
            downloaded = YtDlpDownloadService(paths).download(
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
            assert repository.assets.resolve_asset_path(asset) == Path(asset.path)
        task_repository = ProjectRepository.create(tmp_path / "Task Download", "Task Download")
        project = EditorProject(task_repository, settings=ServiceSettings(), paths=paths)
        try:
            plan = YtDlpDownloadService(paths).analyze(url)
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

            completed = project.wait_for_task(task.id, timeout=30)
            persisted = project.get_task(task.id)
            registered = task_repository.assets.list_assets()

            assert completed.status == TaskStatus.COMPLETED
            assert isinstance(persisted.command, DownloadMediaCommand)
            assert persisted.command.request == request
            assert len(registered) == 1
            visible_path = task_repository.assets.resolve_asset_path(registered[0])
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


def test_real_download_registration_failure_withdraws_user_visible_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = RuntimeContext.discover().paths
    web_root = tmp_path / "web"
    web_root.mkdir()
    source = web_root / "sample.mp4"
    generate_real_media(
        source,
        paths,
        width=320,
        height=180,
    )
    handler = partial(
        SimpleHTTPRequestHandler,
        directory=str(web_root),
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler,
    )
    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
    )
    thread.start()
    repository = ProjectRepository.create(
        tmp_path / "Failed Task Download",
        "Failed Task Download",
    )
    project = EditorProject(
        repository,
        settings=ServiceSettings(),
        paths=paths,
    )
    selected_output = tmp_path / "Selected Output"
    try:
        url = (
            f"http://127.0.0.1:"
            f"{server.server_address[1]}/sample.mp4"
        )
        plan = YtDlpDownloadService(paths).analyze(url)

        def fail_registration(*_args, **_kwargs):
            raise RuntimeError(
                "injected asset registration failure"
            )

        monkeypatch.setattr(
            repository.assets,
            "commit_external_asset",
            fail_registration,
        )
        started = project.start_task(
            DownloadMediaCommand(
                request=DownloadRequest(
                    entry=plan.entries[0],
                    output_directory=str(
                        selected_output.resolve()
                    ),
                )
            )
        )
        completed = project.wait_for_task(
            started.id,
            timeout=30,
        )

        assert completed.status == TaskStatus.FAILED
        assert repository.assets.list_assets() == []
        visible_files = [
            path
            for path in selected_output.rglob("*")
            if path.is_file()
            and "MediaFlow Pro Failed Downloads"
            not in path.parts
        ]
        archived_files = list(
            (
                selected_output
                / "MediaFlow Pro Failed Downloads"
            ).rglob("*.mp4")
        )
        assert visible_files == []
        assert len(archived_files) == 1
        assert archived_files[0].stat().st_size == (
            source.stat().st_size
        )
        assert not list(tmp_path.glob(".mf-dl-*"))
    finally:
        project.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_hdr_project_generates_hdr_and_sdr_display_proxies(tmp_path: Path) -> None:
    paths = RuntimeContext.discover().paths
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
        editor = TimelineEditor(repository, repository.projects.get_project().main_sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=video_track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )
        document = TimelineCompiler(repository, RuntimeContext.discover().paths).compile(
            editor.state,
            use_proxies=True,
            native_preview=True,
            prefer_sdr_preview_proxy=True,
        )
        assert sdr_path.resolve() in document.source_paths
        assert hdr_path.resolve() not in document.source_paths
