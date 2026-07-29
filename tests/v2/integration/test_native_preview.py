import os
import subprocess
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from mediaflow.application.asset_service import AssetService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.enums import AssetKind, ClipMediaKind, ColorMode, ExportFormat, TrackKind
from mediaflow.domain.exports import ExportPreset
from mediaflow.domain.project import ProjectProfile
from mediaflow.domain.timeline import Clip
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt import MltExportService, TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from tests.v2.infrastructure.test_media_pipeline import generate_real_media

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_native_qt_quick_item_decodes_real_mlt_frames_and_advances_clock(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    assert paths.melt is not None
    assert paths.native_qml is not None
    assert (paths.native_qml / "MediaFlow" / "Native" / "mediaflownativeplugin.dll").is_file()
    source = tmp_path / "native-source.mp4"
    generate_real_media(source, paths, width=320, height=180)

    with ProjectRepository.create(tmp_path / "Native Project", "Native Project") as repository:
        asset = AssetService(repository, MediaProbe(paths)).import_external(source)
        editor = TimelineEditor(repository, repository.catalog.get_project().main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )
        graph = repository.project_dir / "cache" / "mlt" / "native-test.mlt"
        TimelineCompiler(repository).write(editor.state, graph, native_preview=True)
        short_graph = repository.project_dir / "cache" / "mlt" / "native-short-test.mlt"
        short_state = editor.state.model_copy(
            update={
                "clips": [
                    clip.model_copy(update={"duration": 12})
                    for clip in editor.state.clips
                ]
            }
        )
        TimelineCompiler(repository).write(short_state, short_graph, native_preview=True)

        app = QGuiApplication.instance() or QGuiApplication([])
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(paths.native_qml))
        engine.loadData(
            b"""
import QtQuick
import QtQuick.Controls
import MediaFlow.Native 1.0
ApplicationWindow {
    visible: true
    width: 320
    height: 180
    color: "black"
    MltPreviewItem { objectName: "preview"; anchors.fill: parent }
}
""",
            QUrl(),
        )
        assert engine.rootObjects()
        window = engine.rootObjects()[0]
        preview = window.findChild(QObject, "preview")
        assert preview is not None
        preview.setProperty("runtimeRoot", str(paths.melt.parent))
        open_started = time.monotonic()
        preview.setProperty("source", str(graph))

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and preview.property("duration") <= 0:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert preview.property("errorString") == ""
        assert preview.property("duration") == 25
        assert time.monotonic() - open_started < 0.75

        seek_started = time.monotonic()
        preview.seek(15)
        while time.monotonic() - seek_started < 0.5 and preview.property("position") != 15:
            QCoreApplication.processEvents()
            time.sleep(0.005)
        assert preview.property("position") == 15
        assert time.monotonic() - seek_started < 0.5

        delivered_seek_positions: list[int] = []
        preview.positionChanged.connect(
            lambda: delivered_seek_positions.append(int(preview.property("position")))
        )
        scrub_started = time.monotonic()
        for frame in [*range(25), *range(24, -1, -1), 4, 9, 14, 19, 17]:
            preview.seek(frame)
        while time.monotonic() - scrub_started < 0.5 and preview.property("position") != 17:
            QCoreApplication.processEvents()
            time.sleep(0.005)
        assert preview.property("position") == 17
        assert time.monotonic() - scrub_started < 0.5
        for _ in range(20):
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert delivered_seek_positions[-1] == 17
        assert len(delivered_seek_positions) <= 8

        preview.seek(0)

        for _ in range(20):
            QCoreApplication.processEvents()
            time.sleep(0.01)
        image = app.primaryScreen().grabWindow(int(window.winId())).toImage()
        assert not image.isNull()
        colors = {
            image.pixelColor(x, y).rgb()
            for x in range(20, image.width(), 40)
            for y in range(20, image.height(), 40)
        }
        assert len(colors) > 5

        exported = MltExportService(TimelineCompiler(repository), paths).export(
            editor.state,
            ExportPreset(
                name="Preview Parity",
                format=ExportFormat.H264,
                container="mp4",
                video_codec="libx264",
                audio_codec="aac",
                pixel_format="yuv420p",
                quality_value=18,
                preset="veryfast",
                gop_frames=25,
            ),
            repository.project_dir / "exports" / "preview-parity.mp4",
        )
        extracted = subprocess.run(
            [
                str(paths.ffmpeg),
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(exported.output_path),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            capture_output=True,
            timeout=30,
            check=True,
        ).stdout
        export_means = [sum(extracted[channel::3]) / (len(extracted) / 3) for channel in range(3)]
        preview_means = [
            sum(
                image.pixelColor(x, y).getRgb()[channel]
                for x in range(image.width())
                for y in range(image.height())
            )
            / (image.width() * image.height())
            for channel in range(3)
        ]
        assert all(
            abs(preview_value - export_value) <= 15
            for preview_value, export_value in zip(preview_means, export_means, strict=True)
        )

        forward_range_positions: list[int] = []
        preview.positionChanged.connect(
            lambda: forward_range_positions.append(int(preview.property("position"))))
        preview.seek(4)
        preview.playRange(4, 9)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and (
            preview.property("position") != 8 or preview.property("playing")
        ):
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert preview.property("position") == 8
        assert preview.property("playing") is False
        assert forward_range_positions
        assert all(4 <= frame <= 8 for frame in forward_range_positions)

        preview.setProperty("playbackRate", -1.0)
        reverse_range_positions: list[int] = []
        preview.positionChanged.connect(
            lambda: reverse_range_positions.append(int(preview.property("position"))))
        preview.seek(8)
        preview.playRange(4, 9)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and (
            preview.property("position") != 4 or preview.property("playing")
        ):
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert preview.property("position") == 4
        assert preview.property("playing") is False
        assert reverse_range_positions
        assert all(4 <= frame <= 8 for frame in reverse_range_positions)

        preview.setProperty("playbackRate", 1.0)
        preview.seek(12)
        preview.playRange(12, 13)
        for _ in range(20):
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert preview.property("position") == 12
        assert preview.property("playing") is False

        preview.setProperty("source", str(short_graph))
        QCoreApplication.processEvents()
        time.sleep(0.01)
        preview.setProperty("source", str(graph))
        durations_after_latest_source: list[int] = []
        preview.durationChanged.connect(
            lambda: durations_after_latest_source.append(int(preview.property("duration"))))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and preview.property("duration") != 25:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert preview.property("duration") == 25
        assert 12 not in durations_after_latest_source

        preview.seek(17)
        preview.setProperty("source", str(short_graph))
        QCoreApplication.processEvents()
        preview.setProperty("source", str(graph))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (
            preview.property("position") != 17 or preview.property("duration") != 25
        ):
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert preview.property("position") == 17
        assert preview.property("duration") == 25

        preview.seek(17)
        preview.setProperty("reloadToken", int(preview.property("reloadToken")) + 1)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (
            preview.property("position") != 17 or preview.property("duration") != 25
        ):
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert preview.property("position") == 17
        assert preview.property("duration") == 25

        preview.setProperty("source", str(graph.with_name("missing.mlt")))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not preview.property("errorString"):
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert "not found" in preview.property("errorString")
        preview.setProperty("source", str(graph))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (
            preview.property("duration") != 25 or preview.property("errorString")
        ):
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert preview.property("duration") == 25
        assert preview.property("errorString") == ""

        preview.play()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and preview.property("position") < 5:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert preview.property("position") >= 5
        assert preview.property("droppedFrames") == 0
        preview.pause()
        preview.setProperty("source", "")
        assert preview.property("playing") is False
        assert preview.property("position") == 0
        assert preview.property("duration") == 0
        for _ in range(20):
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert preview.property("position") == 0
        assert preview.property("duration") == 0
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def test_native_preview_handles_silent_video_audio_only_and_still_image(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    assert paths.ffmpeg is not None and paths.melt is not None and paths.native_qml is not None
    silent_video = tmp_path / "silent-portrait.mp4"
    audio_only = tmp_path / "tone.wav"
    still_image = tmp_path / "still.png"
    commands = [
        [
            str(paths.ffmpeg), "-y", "-hide_banner", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=180x320:rate=25:duration=1",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent_video),
        ],
        [
            str(paths.ffmpeg), "-y", "-hide_banner", "-v", "error",
            "-f", "lavfi", "-i", "sine=frequency=660:sample_rate=48000:duration=1",
            "-c:a", "pcm_s16le", str(audio_only),
        ],
        [
            str(paths.ffmpeg), "-y", "-hide_banner", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=180x320:rate=1",
            "-frames:v", "1", str(still_image),
        ],
    ]
    for command in commands:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, completed.stderr

    profile = ProjectProfile(
        width=180,
        height=320,
        fps_numerator=25,
        fps_denominator=1,
    )
    with ProjectRepository.create(
        tmp_path / "Native Media Matrix",
        "Native Media Matrix",
        profile,
    ) as repository:
        assets = AssetService(repository, MediaProbe(paths))
        silent_asset = assets.import_external(silent_video)
        audio_asset = assets.import_external(audio_only)
        image_asset = assets.import_external(still_image)
        assert silent_asset.kind == AssetKind.VIDEO and not silent_asset.metadata.has_audio
        assert audio_asset.kind == AssetKind.AUDIO and audio_asset.metadata.has_audio
        assert image_asset.kind == AssetKind.IMAGE

        editor = TimelineEditor(repository, repository.catalog.get_project().main_sequence_id)
        video_track = editor.add_track(TrackKind.VIDEO)
        audio_track = editor.add_track(TrackKind.AUDIO)
        specifications = [
            (silent_asset, video_track.id, 18, ClipMediaKind.VIDEO_ONLY),
            (audio_asset, audio_track.id, 19, ClipMediaKind.AUDIO_ONLY),
            (image_asset, video_track.id, 20, ClipMediaKind.VIDEO_ONLY),
        ]
        graphs: list[tuple[Path, int, AssetKind]] = []
        for asset, track_id, duration, media_kind in specifications:
            state = editor.state.model_copy(
                update={
                    "clips": [
                        Clip(
                            track_id=track_id,
                            asset_id=asset.id,
                            timeline_start=0,
                            source_in=0,
                            duration=duration,
                            media_kind=media_kind,
                        )
                    ]
                }
            )
            graph = repository.project_dir / "cache" / "mlt" / f"matrix-{asset.kind.value}.mlt"
            TimelineCompiler(repository).write(state, graph, native_preview=True)
            graphs.append((graph, duration, asset.kind))

        _app = QGuiApplication.instance() or QGuiApplication([])
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(paths.native_qml))
        engine.loadData(
            b"""
import QtQuick
import QtQuick.Controls
import MediaFlow.Native 1.0
ApplicationWindow {
    visible: true
    width: 240
    height: 400
    color: "black"
    MltPreviewItem { objectName: "preview"; anchors.fill: parent }
}
""",
            QUrl(),
        )
        assert engine.rootObjects()
        window = engine.rootObjects()[0]
        preview = window.findChild(QObject, "preview")
        assert preview is not None
        preview.setProperty("runtimeRoot", str(paths.melt.parent))

        for graph, duration, _kind in graphs:
            preview.setProperty("source", str(graph))
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and preview.property("duration") != duration:
                QCoreApplication.processEvents()
                time.sleep(0.01)
            assert preview.property("errorString") == ""
            assert preview.property("duration") == duration
            start = duration // 2
            end = min(duration, start + 4)
            preview.seek(start)
            preview.playRange(start, end)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and (
                preview.property("position") != end - 1 or preview.property("playing")
            ):
                QCoreApplication.processEvents()
                time.sleep(0.01)
            assert preview.property("position") == end - 1
            assert preview.property("playing") is False
            assert preview.property("droppedFrames") == 0

        preview.setProperty("source", "")
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def test_native_preview_tone_maps_hdr_graph_on_sdr_output(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    assert paths.melt is not None
    assert paths.native_qml is not None
    source = tmp_path / "hdr-preview-source.mp4"
    generate_real_media(source, paths, width=320, height=180)
    profile = ProjectProfile(
        width=320,
        height=180,
        fps_numerator=25,
        fps_denominator=1,
        color_mode=ColorMode.HDR10_BT2020_PQ,
        bit_depth=10,
    )

    with ProjectRepository.create(tmp_path / "HDR Preview", "HDR Preview", profile) as repository:
        asset = AssetService(repository, MediaProbe(paths)).import_external(source)
        editor = TimelineEditor(repository, repository.catalog.get_project().main_sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )
        graph = repository.project_dir / "cache" / "mlt" / "native-hdr-test.mlt"
        document = TimelineCompiler(repository).write(editor.state, graph, native_preview=True)
        assert "color_sdr_to_hdr_" in document.xml
        assert "avfilter.zscale" in document.xml

        app = QGuiApplication.instance() or QGuiApplication([])
        engine = QQmlApplicationEngine()
        engine.addImportPath(str(paths.native_qml))
        engine.loadData(
            b"""
import QtQuick
import QtQuick.Controls
import MediaFlow.Native 1.0
ApplicationWindow {
    visible: true
    width: 320
    height: 180
    color: "black"
    MltPreviewItem { objectName: "preview"; anchors.fill: parent; hdrEnabled: true }
}
""",
            QUrl(),
        )
        assert engine.rootObjects()
        window = engine.rootObjects()[0]
        preview = window.findChild(QObject, "preview")
        assert preview is not None
        preview.setProperty("runtimeRoot", str(paths.melt.parent))
        preview.setProperty("source", str(graph))

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and preview.property("duration") <= 0:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert preview.property("errorString") == ""
        assert preview.property("hdrEnabled") is True
        assert preview.property("hdrActive") is False
        assert preview.property("duration") == 25

        for _ in range(20):
            QCoreApplication.processEvents()
            time.sleep(0.01)
        image = app.primaryScreen().grabWindow(int(window.winId())).toImage()
        assert not image.isNull()
        colors = {
            image.pixelColor(x, y).rgb()
            for x in range(20, image.width(), 40)
            for y in range(20, image.height(), 40)
        }
        luminances = [
            image.pixelColor(x, y).lightnessF()
            for x in range(20, image.width(), 40)
            for y in range(20, image.height(), 40)
        ]
        assert len(colors) > 5
        assert max(luminances) > 0.2

        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()
