from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from mediaflow.application.asset_service import AssetService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.enums import ColorMode, ExportFormat, TrackKind
from mediaflow.domain.models import ExportPreset, ProjectProfile
from mediaflow.infrastructure.media_probe import MediaProbe
from mediaflow.infrastructure.mlt import MltExportService, TimelineCompiler
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from tests.v2.infrastructure.test_media_pipeline import generate_real_media


def test_native_qt_quick_item_decodes_real_mlt_frames_and_advances_clock(tmp_path: Path) -> None:
    paths = RuntimePaths.discover()
    assert paths.melt is not None
    assert paths.native_qml is not None
    assert (paths.native_qml / "MediaFlow" / "Native" / "mediaflownativeplugin.dll").is_file()
    source = tmp_path / "native-source.mp4"
    generate_real_media(source, paths, width=320, height=180)

    with ProjectRepository.create(tmp_path / "Native Project", "Native Project") as repository:
        asset = AssetService(repository, MediaProbe(paths)).import_external(source)
        editor = TimelineEditor(repository, repository.get_project().main_sequence_id)
        track = next(item for item in editor.state.tracks if item.kind == TrackKind.VIDEO)
        editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=25,
        )
        graph = repository.project_dir / "cache" / "mlt" / "native-test.mlt"
        TimelineCompiler(repository).write(editor.state, graph, native_preview=True)

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
        assert time.monotonic() - open_started < 0.5

        seek_started = time.monotonic()
        preview.seek(15)
        while time.monotonic() - seek_started < 0.5 and preview.property("position") != 15:
            QCoreApplication.processEvents()
            time.sleep(0.005)
        assert preview.property("position") == 15
        assert time.monotonic() - seek_started < 0.5
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

        preview.play()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and preview.property("position") < 5:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert preview.property("position") >= 5
        assert preview.property("droppedFrames") <= 2
        if preview.property("audioClockActive"):
            assert abs(float(preview.property("clockDriftMs"))) <= 40.0
        preview.pause()
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
        editor = TimelineEditor(repository, repository.get_project().main_sequence_id)
        track = next(item for item in editor.state.tracks if item.kind == TrackKind.VIDEO)
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
