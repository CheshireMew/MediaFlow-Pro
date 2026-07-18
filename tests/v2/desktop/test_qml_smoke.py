from __future__ import annotations

import os
import shutil
import threading
import time
from ctypes import WinDLL, create_unicode_buffer
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QPointF, QUrl
from PySide6.QtGui import QGuiApplication, QImage, QWindow
from PySide6.QtQuick import QQuickItem

from mediaflow.desktop.app import configure_application_font, create_engine
from mediaflow.domain.enums import TaskStatus
from mediaflow.domain.highlights import HighlightCandidate
from mediaflow.domain.subtitles import SubtitleDocument, SubtitleSegment
from mediaflow.domain.task_commands import ExportSequenceCommand
from mediaflow.domain.tasks import Task
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.settings_repository import SettingsRepository
from mediaflow.infrastructure.task_repository import TaskRepository
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService
from tests.v2.infrastructure.test_media_pipeline import generate_real_media


def _windows_environment(name: str) -> str | None:
    buffer = create_unicode_buffer(32768)
    length = WinDLL("kernel32", use_last_error=True).GetEnvironmentVariableW(
        name,
        buffer,
        len(buffer),
    )
    return buffer.value if length else None


def _windows_dll_directory() -> str | None:
    buffer = create_unicode_buffer(32768)
    length = WinDLL("kernel32", use_last_error=True).GetDllDirectoryW(len(buffer), buffer)
    return buffer.value if length else None


def _visual_items(root: QQuickItem):
    for child in root.childItems():
        yield child
        yield from _visual_items(child)


def _process_until(predicate, *, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_home_recent_empty_state_is_centered(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    settings_repository = SettingsRepository()
    settings = settings_repository.load()
    settings.download.last_url = "https://example.com/remembered-video"
    settings.ui.default_project_directory = str(tmp_path / "Projects")
    settings_repository.save(settings)
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app)
    try:
        window = engine.rootObjects()[0]
        window.setWidth(1600)
        window.setHeight(980)
        page_loader = window.findChild(QObject, "pageLoader")
        assert page_loader is not None
        assert _process_until(lambda: page_loader.property("item") is not None)

        home = page_loader.property("item")
        download_url = home.findChild(QQuickItem, "downloadUrlField")
        assert download_url is not None
        assert download_url.property("text") == "https://example.com/remembered-video"
        recent_section = home.findChild(QQuickItem, "homeRecentSection")
        empty_state = home.findChild(QQuickItem, "homeRecentEmptyState")
        assert recent_section is not None and empty_state is not None
        assert empty_state.isVisible()
        assert empty_state.width() >= recent_section.width() - 34

        recent_origin = recent_section.mapToScene(QPointF(0, 0))
        bottom_gap = window.height() - (recent_origin.y() + recent_section.height())
        assert 20 <= bottom_gap <= 40

        content = empty_state.childItems()[0]
        texts = [item for item in content.childItems() if item.property("text") is not None]
        title = next(item for item in texts if item.property("text") == "还没有最近项目")
        description = next(
            item for item in texts if str(item.property("text")).startswith("创建第一个项目后")
        )
        assert title.property("font").pixelSize() == 18
        assert description.property("font").pixelSize() == 14
        empty_origin = empty_state.mapToScene(QPointF(0, 0))
        content_origin = content.mapToScene(QPointF(0, 0))
        empty_center_x = empty_origin.x() + empty_state.width() / 2
        content_center_x = content_origin.x() + content.width() / 2
        assert abs(content_center_x - empty_center_x) <= 1

        rendered = window.grabWindow()
        screenshot = tmp_path / "home-empty-state-centered.png"
        assert not rendered.isNull() and rendered.save(str(screenshot))
        assert screenshot.is_file() and screenshot.stat().st_size > 0

        create_hero = home.findChild(QQuickItem, "homeCreateHero")
        assert create_hero is not None
        assert QMetaObject.invokeMethod(create_hero, "click")
        create_dialog = home.findChild(QObject, "createProjectDialog")
        create_name = home.findChild(QQuickItem, "createProjectNameField")
        create_button = home.findChild(QQuickItem, "confirmCreateProjectButton")
        assert create_dialog is not None and create_dialog.property("visible")
        assert create_name is not None and create_name.property("text") == ""
        assert create_button is not None and create_button.property("enabled") is True
        create_render = window.grabWindow()
        assert not create_render.isNull()
        assert create_render.save(str(tmp_path / "optional-project-name.png"))
        assert QMetaObject.invokeMethod(create_button, "click")
        assert _process_until(
            lambda: controllers.workspace.hasProject
            and controllers.workspace.projectName == "未命名项目 1"
        )
        assert (tmp_path / "Projects" / "未命名项目 1" / "project.mfp").is_file()

        controllers.workspace.closeProject()
        assert _process_until(
            lambda: page_loader.property("item") is not None
            and page_loader.property("item").objectName() == "homeView"
        )
        home = page_loader.property("item")
        create_hero = home.findChild(QQuickItem, "homeCreateHero")
        assert create_hero is not None and QMetaObject.invokeMethod(create_hero, "click")
        create_button = home.findChild(QQuickItem, "confirmCreateProjectButton")
        assert create_button is not None and QMetaObject.invokeMethod(create_button, "click")
        assert _process_until(
            lambda: controllers.workspace.hasProject
            and controllers.workspace.projectName == "未命名项目 2"
        )
        assert (tmp_path / "Projects" / "未命名项目 2" / "project.mfp").is_file()
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def test_drag_import_placement_snap_tracks_and_first_video_profile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app)
    try:
        controllers.workspace.createProject(
            QUrl.fromLocalFile(str(tmp_path)).toString(),
            "Drag Placement",
        )
        assert controllers.workspace.profileConfirmed is False
        assert controllers.workspace.profileLabel == "等待首个视频"

        window = engine.rootObjects()[0]
        assert _process_until(lambda: window.findChild(QQuickItem, "timelineDropArea") is not None)
        assert window.findChild(QQuickItem, "mediaFileDropArea") is not None
        snap_button = window.findChild(QQuickItem, "timelineSnapButton")
        assert snap_button is not None and snap_button.property("checked") is True

        video_track = next(
            controllers.timeline.tracksModel.get(index)
            for index in range(controllers.timeline.tracksModel.rowCount())
            if controllers.timeline.tracksModel.get(index)["kind"] == "video"
        )
        video_source = tmp_path / "first-video.mp4"
        generate_real_media(video_source, RuntimePaths.discover(), width=640, height=360)
        controllers.timeline.importFilesToTimeline(
            [QUrl.fromLocalFile(str(video_source))],
            video_track["trackId"],
            0,
            3.0,
            0,
            True,
            False,
        )
        assert _process_until(
            lambda: controllers.timeline.clipsModel.rowCount() == 1
            and controllers.workspace.profileConfirmed,
            timeout=20,
        )
        first_clip = controllers.timeline.clipsModel.get(0)
        assert first_clip["trackId"] == video_track["trackId"]
        assert first_clip["startFrame"] == 0
        assert controllers.workspace.profileLabel == "640×360  25 fps"

        image_source = tmp_path / "still.png"
        image = QImage(64, 36, QImage.Format.Format_RGB32)
        image.fill(0xFF336699)
        assert image.save(str(image_source))
        controllers.media.importFiles([QUrl.fromLocalFile(str(image_source))])
        assert _process_until(lambda: controllers.media.assetsModel.rowCount() == 2, timeout=20)
        image_asset = next(
            controllers.media.assetsModel.get(index)
            for index in range(controllers.media.assetsModel.rowCount())
            if controllers.media.assetsModel.get(index)["kind"] == "image"
        )

        controllers.timeline.dropAssets(
            [image_asset["assetId"]],
            video_track["trackId"],
            first_clip["endFrame"] - 1,
            3.0,
            0,
            True,
            False,
        )
        assert controllers.timeline.clipsModel.rowCount() == 2
        snapped_image = next(
            controllers.timeline.clipsModel.get(index)
            for index in range(controllers.timeline.clipsModel.rowCount())
            if controllers.timeline.clipsModel.get(index)["assetKind"] == "image"
        )
        assert snapped_image["trackId"] == video_track["trackId"]
        assert snapped_image["startFrame"] == first_clip["endFrame"]

        track_count = controllers.timeline.tracksModel.rowCount()
        controllers.timeline.dropAssets(
            [image_asset["assetId"]],
            video_track["trackId"],
            0,
            3.0,
            0,
            True,
            False,
        )
        assert controllers.timeline.tracksModel.rowCount() == track_count + 1
        overlapping_image = next(
            controllers.timeline.clipsModel.get(index)
            for index in range(controllers.timeline.clipsModel.rowCount())
            if controllers.timeline.clipsModel.get(index)["assetKind"] == "image"
            and controllers.timeline.clipsModel.get(index)["startFrame"] == 0
        )
        assert overlapping_image["trackId"] != video_track["trackId"]

        subtitle_source = tmp_path / "captions.srt"
        subtitle_source.write_text(
            "1\n00:00:00,000 --> 00:00:00,800\n拖入的字幕\n",
            encoding="utf-8",
        )
        controllers.media.importFiles([QUrl.fromLocalFile(str(subtitle_source))])
        assert _process_until(lambda: controllers.media.assetsModel.rowCount() == 3, timeout=20)
        subtitle_asset = next(
            controllers.media.assetsModel.get(index)
            for index in range(controllers.media.assetsModel.rowCount())
            if controllers.media.assetsModel.get(index)["kind"] == "subtitle"
        )
        audio_track = next(
            controllers.timeline.tracksModel.get(index)
            for index in range(controllers.timeline.tracksModel.rowCount())
            if controllers.timeline.tracksModel.get(index)["kind"] == "audio"
        )
        track_count = controllers.timeline.tracksModel.rowCount()
        controllers.timeline.dropAssets(
            [subtitle_asset["assetId"]],
            audio_track["trackId"],
            0,
            3.0,
            0,
            True,
            False,
        )
        assert controllers.timeline.tracksModel.rowCount() == track_count + 1
        assert controllers.subtitles.subtitlePlacementsModel.rowCount() == 1
        placement = controllers.subtitles.subtitlePlacementsModel.get(0)
        assert placement["audioTrackPosition"] == audio_track["position"]
        if controllers.tasks.taskDrawerOpen:
            controllers.tasks.toggleTaskDrawer()
        window.setWidth(1920)
        window.setHeight(1080)
        for _ in range(10):
            QCoreApplication.processEvents()
            time.sleep(0.01)
        rendered = window.grabWindow()
        screenshot = tmp_path / "drag-drop-timeline.png"
        assert not rendered.isNull() and rendered.save(str(screenshot))
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def test_qml_real_project_chain_is_visible_in_models(tmp_path: Path, monkeypatch) -> None:
    mlt_environment_before = {
        name: _windows_environment(name) for name in ("MLT_DATA", "MLT_REPOSITORY", "MLT_REPOSITORY_DENY")
    }
    dll_directory_before = _windows_dll_directory()
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app)
    try:
        controllers.workspace.createProject(QUrl.fromLocalFile(str(tmp_path)).toString(), "UI Test")
        assert controllers.workspace.hasProject is True
        assert controllers.workspace.sequencesModel.rowCount() == 1
        assert controllers.timeline.tracksModel.rowCount() == 3
        assert controllers.timeline.tracksModel.get(0)["displayName"].startswith("视频 ")

        assert _process_until(
            lambda: any(option["value"] == "libx264" for option in controllers.export.videoEncoderOptions)
        )
        assert (
            next(
                option["label"]
                for option in controllers.export.videoEncoderOptions
                if option["value"] == "libx264"
            )
            == "H.264 软件"
        )
        assert controllers.export.subtitleTrackOptions[0]["label"] == "不烧录"

        source = tmp_path / "ui-source.mp4"
        generate_real_media(source, RuntimePaths.discover(), width=320, height=180)
        handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            download_url = f"http://127.0.0.1:{server.server_address[1]}/{source.name}"
            controllers.tasks.analyzeDownloadUrl(download_url)
            analyze_deadline = time.monotonic() + 20
            while time.monotonic() < analyze_deadline and not controllers.tasks.downloadPlanReady:
                QCoreApplication.processEvents()
                time.sleep(0.02)
            assert controllers.tasks.downloadPlanReady is True
            assert controllers.tasks.downloadPlanData["title"] == "ui-source"
            assert controllers.tasks.downloadPlanData["source_url"] == download_url
            assert controllers.settings.settingsData["lastDownloadUrl"] == download_url
            collection_plan = YtDlpDownloadService._plan_from_info(
                {
                    "_type": "playlist",
                    "id": "visible-course",
                    "title": "Visible Course",
                    "extractor_key": "YoutubeTab",
                    "entries": [
                        {
                            "id": "visible",
                            "title": "Visible Lesson",
                            "webpage_url": download_url,
                        },
                        None,
                    ],
                },
                "https://www.youtube.com/playlist?list=visible-course",
            )
            controllers.session._set_download_plan(collection_plan)
            controllers.tasks.downloadPlanChanged.emit()
            QCoreApplication.processEvents()
            root_window = engine.rootObjects()[0]
            assert root_window.property("downloadPlanVisible") is True
            assert root_window.property("downloadPlanEntryCount") == 2
            dialog_render = root_window.grabWindow()
            dialog_render_path = tmp_path / "download-plan-with-unavailable-entry.png"
            assert not dialog_render.isNull() and dialog_render.save(str(dialog_render_path))
            controllers.tasks.dismissDownloadPlan()
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)
        controllers.media.importMedia(QUrl.fromLocalFile(str(source)).toString())
        assert _process_until(
            lambda: controllers.media.assetsModel.rowCount() == 1
            and controllers.workspace.workflowStage == "prepare_media"
            and controllers.workspace.workflowStatus == "awaiting_confirmation",
            timeout=20,
        )
        assert controllers.media.assetsModel.rowCount() == 1
        assert controllers.workspace.workflowStage == "prepare_media"
        assert controllers.workspace.workflowStatus == "awaiting_confirmation"

        asset_id = controllers.media.assetsModel.get(0)["assetId"]
        controllers.media.addAssetToTimeline(asset_id)
        QCoreApplication.processEvents()

        assert controllers.timeline.clipsModel.rowCount() == 1
        assert controllers.timeline.selectedClipId == controllers.timeline.clipsModel.get(0)["clipId"]
        assert engine.rootObjects()[0].title() == "UI Test"

        controllers.workspace.reportPreviewDroppedFrames(
            controllers.workspace.settings.preview.dropped_frame_proxy_threshold
        )
        assert _process_until(
            lambda: any(
                controllers.tasks.tasksModel.get(index)["kind"] == "proxy"
                and controllers.tasks.tasksModel.get(index)["status"] == "completed"
                for index in range(controllers.tasks.tasksModel.rowCount())
            )
            and controllers.media.assetsModel.get(0)["proxyReady"],
            timeout=30,
        )
        assert controllers.workspace.workflowStage == "prepare_media"
        assert controllers.workspace.workflowStatus == "awaiting_confirmation"

        analyzed_clip_before = dict(controllers.timeline.clipsModel.get(0))
        controllers.timeline.analyzeSequenceBoundaries()
        boundary_deadline = time.monotonic() + 30
        boundary_task = None
        while time.monotonic() < boundary_deadline:
            QCoreApplication.processEvents()
            boundary_task = next(
                (
                    controllers.tasks.tasksModel.get(index)
                    for index in range(controllers.tasks.tasksModel.rowCount())
                    if controllers.tasks.tasksModel.get(index)["displayName"] == "智能设置序列入出点"
                ),
                None,
            )
            if boundary_task and (
                boundary_task["status"] == "failed"
                or (boundary_task["status"] == "completed" and controllers.workspace.hasSequenceInOut)
            ):
                break
            time.sleep(0.02)
        assert boundary_task and boundary_task["status"] == "completed", boundary_task
        assert controllers.workspace.hasSequenceInOut is True
        assert (controllers.workspace.sequenceInFrame, controllers.workspace.sequenceOutFrame) == (
            0,
            analyzed_clip_before["endFrame"],
        )
        assert controllers.timeline.clipsModel.get(0) == analyzed_clip_before

        controllers.timeline.setSequenceInOut(1, analyzed_clip_before["endFrame"] - 1)
        assert (controllers.workspace.sequenceInFrame, controllers.workspace.sequenceOutFrame) == (
            1,
            analyzed_clip_before["endFrame"] - 1,
        )
        QCoreApplication.processEvents()
        sequence_layer = engine.rootObjects()[0].findChild(QQuickItem, "sequenceInOutLayer")
        assert sequence_layer is not None and sequence_layer.isVisible()
        smart_bounds_button = engine.rootObjects()[0].findChild(QQuickItem, "smartSequenceBoundsButton")
        assert smart_bounds_button is not None and smart_bounds_button.isVisible()
        preview_slider = engine.rootObjects()[0].findChild(QQuickItem, "previewPositionSlider")
        assert preview_slider is not None
        assert (preview_slider.property("from"), preview_slider.property("to")) == (
            1.0,
            float(analyzed_clip_before["endFrame"] - 2),
        )
        in_out_render = engine.rootObjects()[0].grabWindow()
        assert not in_out_render.isNull()
        assert in_out_render.save(str(tmp_path / "sequence-in-out.png"))
        controllers.timeline.undo()
        assert (controllers.workspace.sequenceInFrame, controllers.workspace.sequenceOutFrame) == (
            0,
            analyzed_clip_before["endFrame"],
        )
        controllers.timeline.redo()
        assert controllers.workspace.sequenceInFrame == 1

        first_clip = controllers.timeline.clipsModel.get(0)
        controllers.timeline.copyClip(first_clip["clipId"], 3.0, first_clip["endFrame"])
        assert controllers.timeline.clipsModel.rowCount() == 2
        second_clip_id = controllers.timeline.clipsModel.get(1)["clipId"]
        controllers.timeline.addTransitionAfter(first_clip["clipId"], "dissolve", 8)
        assert controllers.timeline.transitionsModel.rowCount() == 1
        transition_id = controllers.timeline.transitionsModel.get(0)["transitionId"]
        controllers.timeline.updateTransition(transition_id, "fade", 6)
        assert controllers.timeline.transitionsModel.get(0)["durationFrames"] == 6
        controllers.timeline.selectClip(first_clip["clipId"])
        controllers.timeline.selectClip(second_clip_id, True)
        assert controllers.timeline.selectedClipIds == [first_clip["clipId"], second_clip_id]
        controllers.timeline.moveClip(
            first_clip["clipId"],
            5,
            first_clip["trackId"],
            3.0,
            0,
            False,
        )
        assert controllers.timeline.clipsModel.get(0)["startFrame"] == 5
        assert controllers.timeline.clipsModel.get(1)["startFrame"] == first_clip["endFrame"] + 5
        controllers.timeline.moveClip(
            first_clip["clipId"],
            2,
            first_clip["trackId"],
            3.0,
            0,
            True,
        )
        assert controllers.timeline.clipsModel.get(0)["startFrame"] == 0
        controllers.timeline.deleteSelectedClips(False)
        assert controllers.timeline.clipsModel.rowCount() == 0
        controllers.timeline.undo()
        assert controllers.timeline.clipsModel.rowCount() == 2
        controllers.timeline.selectClip(first_clip["clipId"])
        controllers.timeline.addTimelineMarker(12)
        controllers.timeline.setRangeIn(10)
        controllers.timeline.commitTimelineRange(24)
        assert controllers.timeline.timelineMarkersModel.rowCount() == 1
        assert controllers.timeline.timelineRangesModel.get(0)["endFrame"] == 24
        controllers.timeline.undo()
        assert controllers.timeline.timelineRangesModel.rowCount() == 0
        controllers.timeline.redo()
        assert controllers.timeline.timelineRangesModel.rowCount() == 1
        QCoreApplication.processEvents()

        controllers.workspace.updateSequenceProfile(1080, 1920, 30, 1, "sdr_bt709", 2)
        assert (controllers.workspace.profileWidth, controllers.workspace.profileHeight) == (1080, 1920)
        assert (controllers.workspace.profileFpsNumerator, controllers.workspace.profileFpsDenominator) == (
            30,
            1,
        )
        assert _process_until(
            lambda: engine.rootObjects()[0].findChild(QQuickItem, "previewSurface") is not None
        )
        preview_surface = engine.rootObjects()[0].findChild(QQuickItem, "previewSurface")
        assert preview_surface is not None and preview_surface.height() > 0
        assert abs(preview_surface.width() / preview_surface.height() - 1080 / 1920) <= 0.02

        sequence_menu_button = engine.rootObjects()[0].findChild(
            QQuickItem,
            "sequenceMenuButton",
        )
        assert sequence_menu_button is not None and sequence_menu_button.isVisible()
        assert QMetaObject.invokeMethod(sequence_menu_button, "click")
        assert _process_until(
            lambda: (
                engine.rootObjects()[0].findChild(QQuickItem, "createShortSequenceButton")
                is not None
                and engine.rootObjects()[0]
                .findChild(QQuickItem, "createShortSequenceButton")
                .isVisible()
            )
        )
        create_short_button = engine.rootObjects()[0].findChild(
            QQuickItem,
            "createShortSequenceButton",
        )
        assert create_short_button is not None and create_short_button.isVisible()
        assert engine.rootObjects()[0].findChild(QObject, "shortDialog") is None
        assert QMetaObject.invokeMethod(create_short_button, "click")
        assert _process_until(lambda: controllers.workspace.sequencesModel.rowCount() == 2)
        active_short = next(
            controllers.workspace.sequencesModel.get(index)
            for index in range(controllers.workspace.sequencesModel.rowCount())
            if controllers.workspace.sequencesModel.get(index)["sequenceId"]
            == controllers.workspace.activeSequenceId
        )
        assert active_short["name"] == "短视频 1"
        archived_sequence_id = controllers.workspace.activeSequenceId
        assert QMetaObject.invokeMethod(sequence_menu_button, "click")
        assert _process_until(
            lambda: (
                engine.rootObjects()[0].findChild(QQuickItem, "archiveActiveSequenceButton")
                is not None
                and engine.rootObjects()[0]
                .findChild(QQuickItem, "archiveActiveSequenceButton")
                .isVisible()
            )
        )
        archive_button = engine.rootObjects()[0].findChild(
            QQuickItem,
            "archiveActiveSequenceButton",
        )
        assert archive_button is not None and archive_button.isVisible()
        assert QMetaObject.invokeMethod(archive_button, "click")
        assert controllers.workspace.activeSequenceId != archived_sequence_id
        assert archived_sequence_id not in {
            controllers.workspace.sequencesModel.get(index)["sequenceId"]
            for index in range(controllers.workspace.sequencesModel.rowCount())
        }
        controllers.timeline.undo()
        assert archived_sequence_id in {
            controllers.workspace.sequencesModel.get(index)["sequenceId"]
            for index in range(controllers.workspace.sequencesModel.rowCount())
        }
        controllers.timeline.redo()
        assert archived_sequence_id not in {
            controllers.workspace.sequencesModel.get(index)["sequenceId"]
            for index in range(controllers.workspace.sequencesModel.rowCount())
        }
        controllers.timeline.selectClip(first_clip["clipId"])

        highlight = HighlightCandidate(
            project_id=controllers.session._documents.get_project().id,
            asset_id=asset_id,
            start_frame=2,
            end_frame=10,
            title="UI 高光",
            reason="验证区间预览",
            score=0.9,
        )
        controllers.session._documents.save_highlights([highlight])
        controllers.session._projector.refresh_highlights()
        preview_ranges: list[tuple[int, int]] = []
        controllers.highlights.previewRangeRequested.connect(
            lambda start, end: preview_ranges.append((start, end))
        )
        controllers.highlights.previewHighlight(highlight.id)
        preview_deadline = time.monotonic() + 10
        while time.monotonic() < preview_deadline and not preview_ranges:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        assert preview_ranges == [(2, 10)]

        root = engine.rootObjects()[0]
        title_bar = root.findChild(QQuickItem, "appTitleBar")
        minimize_button = root.findChild(QQuickItem, "minimizeWindowButton")
        maximize_button = root.findChild(QQuickItem, "maximizeWindowButton")
        close_button = root.findChild(QQuickItem, "closeWindowButton")
        assert all(
            item is not None and item.isVisible()
            for item in (
                title_bar,
                minimize_button,
                maximize_button,
                close_button,
            )
        )
        assert QMetaObject.invokeMethod(maximize_button, "click")
        for _ in range(6):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        assert root.visibility() == QWindow.Visibility.Maximized
        assert QMetaObject.invokeMethod(maximize_button, "click")
        for _ in range(6):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        assert root.visibility() == QWindow.Visibility.Windowed
        assert QMetaObject.invokeMethod(minimize_button, "click")
        for _ in range(6):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        assert root.visibility() == QWindow.Visibility.Minimized
        root.showNormal()
        for _ in range(6):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        assert root.visibility() == QWindow.Visibility.Windowed
        page_loader = root.findChild(QQuickItem, "pageLoader")
        assert page_loader is not None
        workspace = page_loader.property("item")
        assert workspace is not None
        root.setWidth(1280)
        root.setHeight(720)
        for _ in range(12):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        inspector = workspace.findChild(QQuickItem, "inspectorContainer")
        compact_inspector = workspace.findChild(QQuickItem, "compactInspectorDrawer")
        compact_inspector_button = workspace.findChild(QQuickItem, "compactInspectorButton")
        navigation = workspace.findChild(QQuickItem, "workspaceNavigation")
        tool_panel = workspace.findChild(QQuickItem, "toolPanelContainer")
        assert inspector is not None and not inspector.isVisible()
        assert compact_inspector is not None and compact_inspector.isVisible()
        assert compact_inspector_button is not None and compact_inspector_button.isVisible()
        assert navigation is not None
        assert abs(navigation.width() - workspace.width()) <= 2
        assert 46 <= navigation.height() <= 54
        assert tool_panel is not None
        tool_panel_position = tool_panel.mapToItem(workspace, QPointF(0, 0))
        assert abs(tool_panel_position.x()) <= 2
        assert abs(
            tool_panel_position.y()
            - navigation.height()
            - workspace.property("workspaceBannerHeight")
        ) <= 2
        navigation_items = {
            item.objectName(): item for item in _visual_items(navigation)
        }
        for mode in ("media", "transcript", "translate", "highlight", "edit", "audio", "export"):
            navigation_item = navigation_items.get(f"navigationItem_{mode}")
            assert navigation_item is not None and navigation_item.isVisible()
        navigation_positions = [
            navigation_items[f"navigationItem_{mode}"].mapToItem(navigation, QPointF(0, 0))
            for mode in ("media", "transcript", "translate", "highlight", "edit", "audio", "export")
        ]
        assert all(abs(point.y() - navigation_positions[0].y()) <= 1 for point in navigation_positions)
        assert all(
            navigation_positions[index].x() < navigation_positions[index + 1].x()
            for index in range(len(navigation_positions) - 1)
        )
        settings_navigation_item = navigation_items.get("navigationItem_settings")
        assert settings_navigation_item is not None and settings_navigation_item.isVisible()
        assert settings_navigation_item.mapToItem(navigation, QPointF(0, 0)).x() > (
            navigation_positions[-1].x()
        )
        assert QMetaObject.invokeMethod(compact_inspector_button, "click")
        for _ in range(12):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        assert workspace.property("inspectorDrawerOpen") is True
        assert compact_inspector.isEnabled()
        assert abs(compact_inspector.x() + compact_inspector.width() - root.width()) <= 2
        assert QMetaObject.invokeMethod(compact_inspector_button, "click")
        root.setWidth(1440)
        root.setHeight(900)
        for _ in range(12):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        assert inspector.isVisible()
        assert not compact_inspector.isVisible()
        preview = workspace.findChild(QQuickItem, "previewPlayer")
        overlay = workspace.findChild(QQuickItem, "previewTransformOverlay")
        assert preview is not None and overlay is not None
        preview.setProperty("volume", 0.35)
        assert abs(float(preview.property("volume")) - 0.35) < 0.001
        preview.setProperty("position", 0)
        QCoreApplication.processEvents()
        assert overlay.isVisible()
        overlay.setProperty("draftX", 12.5)
        overlay.setProperty("draftY", 7.5)
        assert QMetaObject.invokeMethod(overlay, "commit")
        QCoreApplication.processEvents()
        assert controllers.timeline.selectedClipData["x"] == 12.5
        assert controllers.timeline.selectedClipData["y"] == 7.5
        preview_viewport = workspace.findChild(QQuickItem, "previewViewport")
        assert preview_viewport is not None
        preview_viewport.setProperty("viewportZoom", 2.0)
        preview_viewport.setProperty("viewportPanX", 30.0)
        assert QMetaObject.invokeMethod(workspace, "resetPreviewViewport")
        assert preview_viewport.property("viewportZoom") == 1.0
        assert preview_viewport.property("viewportPanX") == 0.0
        controllers.settings.savePanelLayout(340, 360, 380)
        controllers.settings.saveWindowSize(1440, 900)
        persisted_ui = SettingsRepository(os.environ["MEDIAFLOW_SETTINGS_PATH"]).load().ui
        assert (
            persisted_ui.left_panel_width,
            persisted_ui.inspector_width,
            persisted_ui.timeline_height,
        ) == (340, 360, 380)
        assert (persisted_ui.window_width, persisted_ui.window_height) == (1440, 900)
        workflow_banner = workspace.findChild(QQuickItem, "workflowBanner")
        workflow_mode = workspace.findChild(QQuickItem, "workflowMode")
        assert workflow_banner is not None
        assert workflow_banner.isVisible() is controllers.workspace.workflowPending
        assert workflow_mode is None
        controllers.workspace.setProjectWorkflowMode("auto")
        assert controllers.workspace.projectWorkflowMode == "auto"
        controllers.workspace.setProjectWorkflowMode("confirm")
        assert controllers.workspace.projectWorkflowMode == "confirm"
        workflow_run_id = controllers.workspace.workflowRunId
        controllers.workspace.continueWorkflow(workflow_run_id, "")
        workflow_deadline = time.monotonic() + 20
        while time.monotonic() < workflow_deadline:
            QCoreApplication.processEvents()
            if (
                controllers.workspace.workflowStage == "transcribe"
                and controllers.workspace.workflowStatus == "awaiting_confirmation"
            ):
                break
            time.sleep(0.02)
        assert controllers.workspace.workflowStage == "transcribe"
        assert controllers.workspace.workflowStatus == "awaiting_confirmation"
        assert workflow_banner.isVisible()
        assert controllers.media.assetsModel.get(0)["waveformReady"] is True
        duration_frames = controllers.media.assetsModel.get(0)["durationFrames"]
        visible_peaks: list[float] = []

        def waveform_is_visible() -> bool:
            nonlocal visible_peaks
            visible_peaks = controllers.media.waveformPeaks(
                asset_id,
                0,
                duration_frames,
                1.0,
                max(0, duration_frames // 2),
                min(10, duration_frames),
                30,
            )
            return bool(visible_peaks)

        assert _process_until(waveform_is_visible)
        assert len(visible_peaks) <= 30
        timeline = workspace.findChild(QQuickItem, "timelinePanel")
        timeline_toolbar = workspace.findChild(QQuickItem, "timelineToolbarScroll")
        track_controls_button = workspace.findChild(QQuickItem, "trackControlsButton")
        track_controls_panel = workspace.findChild(QQuickItem, "trackControlsPanel")
        assert timeline is not None
        assert timeline_toolbar is not None
        assert track_controls_button is not None and track_controls_panel is not None
        assert not track_controls_panel.isVisible()
        assert QMetaObject.invokeMethod(track_controls_button, "click")
        assert _process_until(track_controls_panel.isVisible)
        assert QMetaObject.invokeMethod(track_controls_button, "click")
        assert _process_until(lambda: not track_controls_panel.isVisible())
        timeline_origin = timeline.mapToItem(workspace, QPointF(0, 0))
        tool_panel_bottom = tool_panel.mapToItem(workspace, QPointF(0, tool_panel.height()))
        assert abs(timeline_origin.x()) <= 2
        assert abs(timeline.width() - workspace.width()) <= 2
        assert abs(timeline_origin.y() - tool_panel_bottom.y() - 6) <= 2
        first_clip_projection = controllers.timeline.clipsModel.get(0)
        assert first_clip_projection["assetKind"] == "video"
        assert first_clip_projection["trackKind"] == "video"
        assert first_clip_projection["allowedTrackKinds"] == ["video", "audio"]
        assert first_clip_projection["hasAudio"] is True
        assert first_clip_projection["audioTrackPosition"] == 1
        if timeline_toolbar.property("contentWidth") > timeline_toolbar.width():
            expected_content_x = (
                timeline_toolbar.property("contentWidth") - timeline_toolbar.width()
            )
            timeline_toolbar.setProperty("contentX", expected_content_x)
            assert _process_until(
                lambda: abs(timeline_toolbar.property("contentX") - expected_content_x) <= 2
            )
            timeline_toolbar.setProperty("contentX", 0)
        timeline.setProperty("pixelsPerFrame", 50.0)
        QCoreApplication.processEvents()
        preview_player = workspace.findChild(QQuickItem, "previewPlayer")
        timeline_playhead = timeline.findChild(QQuickItem, "timelinePlayhead")
        assert preview_player is not None and timeline_playhead is not None
        sequence_last_frame = controllers.workspace.timelineDurationFrames - 1
        assert sequence_last_frame >= 0
        assert timeline.property("maxPlayheadFrame") == sequence_last_frame
        timeline.seekToFrame(sequence_last_frame + 5_000)
        assert timeline.property("interactivePlayheadFrame") == sequence_last_frame
        assert timeline.property("visiblePlayheadFrame") == sequence_last_frame
        assert abs(timeline_playhead.x() - sequence_last_frame * 50.0) <= 1
        assert _process_until(
            lambda: preview_player.property("position") == sequence_last_frame
            and timeline.property("playheadSeekPending") is False
        ), {
            "expected": sequence_last_frame,
            "position": preview_player.property("position"),
            "duration": preview_player.property("duration"),
            "error": preview_player.property("errorString"),
            "graph": controllers.workspace.previewGraphPath,
        }
        assert QMetaObject.invokeMethod(workspace, "playPreview")
        assert _process_until(lambda: preview_player.property("playing") is True)
        scrub_target = max(
            controllers.workspace.sequenceInFrame,
            min(sequence_last_frame - 2, controllers.workspace.sequenceInFrame + 8),
        )
        timeline.beginPlayheadScrub(scrub_target)
        assert _process_until(lambda: preview_player.property("playing") is False)
        for frame in range(scrub_target, min(sequence_last_frame, scrub_target + 6)):
            timeline.updatePlayheadScrub(frame)
        final_scrub_frame = int(timeline.property("interactivePlayheadFrame"))
        timeline.finishPlayheadScrub()
        assert _process_until(
            lambda: preview_player.property("playing") is True
            and preview_player.property("position") >= final_scrub_frame
        )
        preview_player.pause()
        waveforms = [item for item in _visual_items(timeline) if item.objectName() == "clipWaveform"]
        video_clips = [item for item in _visual_items(timeline) if item.objectName() == "timelineClip"]
        embedded_audio = [
            item for item in _visual_items(timeline)
            if item.objectName() == "embeddedAudioClip" and item.isVisible()
        ]
        assert waveforms
        assert video_clips and embedded_audio
        assert embedded_audio[0].y() > video_clips[0].y()
        assert abs(embedded_audio[0].x() - video_clips[0].x()) <= 1
        original_video_x = video_clips[0].x()
        timeline.beginClipDrag(
            first_clip_projection["clipId"],
            first_clip_projection["trackPosition"],
            first_clip_projection["trackKind"],
        )
        timeline.updateClipDrag(
            first_clip_projection["clipId"],
            first_clip_projection["startFrame"],
            first_clip_projection["trackPosition"],
            first_clip_projection["allowedTrackKinds"],
            125.0,
            float(timeline.property("trackPitch")),
        )
        assert abs(video_clips[0].x() - original_video_x - 125.0) <= 1
        assert abs(embedded_audio[0].x() - video_clips[0].x()) <= 1
        assert video_clips[0].property("displayedTrackKind") == "audio"
        assert not embedded_audio[0].isVisible()
        timeline.cancelClipDrag()
        assert abs(video_clips[0].x() - original_video_x) <= 1
        assert video_clips[0].property("displayedTrackKind") == "video"
        assert embedded_audio[0].isVisible()

        audio_track = controllers.timeline.tracksModel.get(
            first_clip_projection["audioTrackPosition"]
        )
        controllers.timeline.moveClip(
            first_clip_projection["clipId"],
            first_clip_projection["startFrame"],
            audio_track["trackId"],
            50.0,
            0,
            False,
        )
        assert controllers.timeline.clipsModel.get(0)["trackKind"] == "audio"
        assert video_clips[0].property("displayedTrackKind") == "audio"
        assert not embedded_audio[0].isVisible()
        controllers.timeline.moveClip(
            first_clip_projection["clipId"],
            first_clip_projection["startFrame"],
            first_clip_projection["trackId"],
            50.0,
            0,
            False,
        )
        assert controllers.timeline.clipsModel.get(0)["trackKind"] == "video"
        assert video_clips[0].property("displayedTrackKind") == "video"
        assert embedded_audio[0].isVisible()
        assert any(item.parentItem().width() > item.width() for item in waveforms)
        assert all(item.width() <= timeline.width() for item in waveforms)

        project_id = controllers.session._documents.get_project().id
        subtitle_document = SubtitleDocument(
            project_id=project_id,
            asset_id=asset_id,
            media_asset_id=asset_id,
            language="zh_CN",
        )
        controllers.session._documents.create_subtitle_document(
            subtitle_document,
            [
                SubtitleSegment(
                    document_id=subtitle_document.id,
                    start_frame=0,
                    end_frame=max(1, min(duration_frames, 30)),
                    text="叠加在音频波形上的字幕",
                )
            ],
        )
        controllers.session._projector.refresh_documents()
        subtitle_documents = controllers.subtitles.subtitleDocumentsModel
        assert subtitle_documents.rowCount() > 0
        controllers.subtitles.placeSubtitleDocument(
            subtitle_documents.get(0)["documentId"]
        )
        assert _process_until(
            lambda: controllers.subtitles.subtitlePlacementsModel.rowCount() > 0
        )
        subtitle_projection = controllers.subtitles.subtitlePlacementsModel.get(0)
        assert subtitle_projection["clipId"]
        assert subtitle_projection["audioTrackPosition"] == 1
        subtitle_overlays = [
            item for item in _visual_items(timeline)
            if item.objectName() == "subtitleWaveformOverlay" and item.isVisible()
        ]
        assert subtitle_overlays
        assert embedded_audio[0].y() <= subtitle_overlays[0].y()
        assert (
            subtitle_overlays[0].y() + subtitle_overlays[0].height()
            <= embedded_audio[0].y() + embedded_audio[0].height()
        )
        timeline_overlay_render = root.grabWindow()
        timeline_overlay_path = tmp_path / "timeline-audio-subtitle-overlay.png"
        assert not timeline_overlay_render.isNull()
        assert timeline_overlay_render.save(str(timeline_overlay_path))
        controllers.workspace.cancelWorkflow(workflow_run_id)
        assert controllers.workspace.workflowPending is False
        normal_tool_panel_width = float(tool_panel.width())
        for mode in ("transcript", "translate", "highlight", "edit", "audio", "export", "media"):
            workspace.setProperty("activeMode", mode)
            for _ in range(6):
                QCoreApplication.processEvents()
                time.sleep(0.01)
            assert workspace.property("activeMode") == mode
            task_focused = mode in {"transcript", "translate", "highlight", "audio", "export"}
            assert timeline.isVisible() is (mode not in {"translate", "export"})
            assert inspector.isVisible() is (not task_focused)
            assert compact_inspector.isVisible() is task_focused
            assert compact_inspector_button.isVisible() is task_focused
            if task_focused:
                assert tool_panel.width() >= 540
                assert tool_panel.width() > normal_tool_panel_width

        settings_dialog = workspace.findChild(QObject, "settingsDialog")
        settings_tabs = workspace.findChild(QQuickItem, "settingsTabs")
        auto_continue_setting = workspace.findChild(QQuickItem, "autoContinueSetting")
        auto_save_notice = workspace.findChild(QQuickItem, "settingsAutoSaveNotice")
        settings_close = workspace.findChild(QQuickItem, "settingsCloseButton")
        assert all(
            item is not None
            for item in (
                settings_dialog,
                settings_tabs,
                auto_continue_setting,
                auto_save_notice,
                settings_close,
            )
        )
        assert QMetaObject.invokeMethod(settings_dialog, "open")
        assert _process_until(
            lambda: auto_continue_setting.isVisible() and auto_save_notice.isVisible()
        )
        persisted_before = SettingsRepository(
            os.environ["MEDIAFLOW_SETTINGS_PATH"]
        ).load().workflow.auto_continue
        assert QMetaObject.invokeMethod(auto_continue_setting, "click")
        assert _process_until(
            lambda: SettingsRepository(
                os.environ["MEDIAFLOW_SETTINGS_PATH"]
            ).load().workflow.auto_continue
            is not persisted_before,
            timeout=3,
        )
        assert QMetaObject.invokeMethod(settings_close, "click")

        workspace.setProperty("activeMode", "export")
        QCoreApplication.processEvents()

        def export_buttons_are_visible() -> bool:
            export_panel = workspace.findChild(QQuickItem, "exportPanel")
            export_to_project = workspace.findChild(QQuickItem, "exportToProjectButton")
            export_as = workspace.findChild(QQuickItem, "exportAsButton")
            return (
                export_panel is not None
                and export_panel.isVisible()
                and export_to_project is not None
                and export_to_project.isVisible()
                and export_as is not None
                and export_as.isVisible()
            )

        assert _process_until(export_buttons_are_visible)
        for _ in range(12):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        export_to_project = workspace.findChild(QQuickItem, "exportToProjectButton")
        export_as = workspace.findChild(QQuickItem, "exportAsButton")
        assert export_to_project is not None
        assert export_as is not None
        assert export_to_project.property("text") == "导出到项目"
        assert export_as.property("text") == "另存为…"
        export_render = root.grabWindow()
        assert not export_render.isNull()
        assert export_render.save(str(tmp_path / "one-click-project-export.png"))
        workspace.setProperty("activeMode", "translate")
        QCoreApplication.processEvents()
        translation_observation: dict[str, object] = {}

        def translation_target_is_ready() -> bool:
            translation_panel = workspace.findChild(QQuickItem, "translationPanel")
            if translation_panel is None or not translation_panel.isVisible():
                translation_observation.update(panelVisible=False)
                return False
            translation_target = translation_panel.findChild(
                QQuickItem,
                "translationTargetLanguage",
            )
            translation_observation.update(
                panelVisible=True,
                targetFound=translation_target is not None,
                targetVisible=(
                    translation_target.isVisible() if translation_target is not None else False
                ),
                currentValue=(
                    translation_target.property("currentValue")
                    if translation_target is not None
                    else None
                ),
                controllerDefault=controllers.settings.defaultTranslationLanguage,
            )
            return (
                translation_target is not None
                and translation_target.isVisible()
                and translation_target.property("currentValue") == "zh_CN"
            )

        assert _process_until(translation_target_is_ready), translation_observation
        workspace.setProperty("activeMode", "media")

        master_bus = next(
            controllers.audio.audioBusesModel.get(index)
            for index in range(controllers.audio.audioBusesModel.rowCount())
            if controllers.audio.audioBusesModel.get(index)["name"] == "主总线"
        )
        controllers.audio.addAudioBus("旁白")
        custom_bus = next(
            controllers.audio.audioBusesModel.get(index)
            for index in range(controllers.audio.audioBusesModel.rowCount())
            if controllers.audio.audioBusesModel.get(index)["name"] == "旁白"
        )
        assert custom_bus["parentBusId"] == master_bus["busId"]
        controllers.audio.updateAudioBus(custom_bus["busId"], -1.0, False, False, master_bus["busId"], "mono")
        assert (
            controllers.audio.audioBusesModel.get(
                controllers.audio.audioBusesModel.findRow("busId", custom_bus["busId"])
            )["channelLayout"]
            == "mono"
        )
        controllers.audio.selectAudioBus(master_bus["busId"])
        controllers.audio.updateAudioBus(master_bus["busId"], -3.0, False, False)
        controllers.audio.addAudioEffect(master_bus["busId"], "limiter")
        assert controllers.audio.audioEffectsModel.rowCount() == 1
        assert controllers.audio.audioEffectsModel.get(0)["kind"] == "limiter"
        limiter_id = controllers.audio.audioEffectsModel.get(0)["effectId"]
        controllers.audio.selectAudioEffect(limiter_id)
        assert controllers.audio.audioEffectParametersModel.get(0)["key"] == "ceiling_db"
        controllers.audio.setAudioEffectParameter(limiter_id, "ceiling_db", -2.0)
        assert controllers.audio.audioEffectsModel.get(0)["parameters"]["ceiling_db"] == -2.0
        controllers.audio.addAudioEffect(master_bus["busId"], "compressor")
        compressor_id = controllers.audio.selectedAudioEffectId
        controllers.audio.moveAudioEffect(compressor_id, 0)
        assert controllers.audio.audioEffectsModel.get(0)["effectId"] == compressor_id
        presets = controllers.audio.audioEffectPresets("compressor")
        assert {item["presetId"] for item in presets} >= {"default", "dialogue", "strong"}
        controllers.audio.applyAudioEffectPreset(compressor_id, "dialogue")
        assert controllers.audio.audioEffectsModel.get(0)["parameters"]["threshold_db"] == -20.0
        controllers.audio.removeAudioEffect(compressor_id)
        assert controllers.audio.audioEffectsModel.rowCount() == 1
        controllers.audio.selectAudioEffect(limiter_id)

        controllers.audio.analyzeLoudness()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            analyze_tasks = [
                controllers.tasks.tasksModel.get(index)
                for index in range(controllers.tasks.tasksModel.rowCount())
                if controllers.tasks.tasksModel.get(index)["kind"] == "analyze"
            ]
            if analyze_tasks and analyze_tasks[0]["status"] in {"completed", "failed"}:
                break
            time.sleep(0.02)
        assert analyze_tasks[0]["status"] == "completed", analyze_tasks[0]["error"]
        assert -100.0 < controllers.audio.audioMetrics["integratedLufs"] < 0.0
        workspace.setProperty("activeMode", "audio")
        assert _process_until(
            lambda: any(
                item.isVisible()
                and str(item.property("text")).endswith("LUFS")
                and not str(item.property("text")).startswith("—")
                for item in workspace.findChildren(QQuickItem, "audioMetricValue3")
            )
        )
        integrated_texts = workspace.findChildren(QQuickItem, "audioMetricValue3")
        visible_values = [str(item.property("text")) for item in integrated_texts if item.isVisible()]
        assert any(value.endswith("LUFS") and not value.startswith("—") for value in visible_values)
        parameter_list = workspace.findChild(QQuickItem, "audioParameterList")
        audio_scroll = workspace.findChild(QQuickItem, "audioScroll")
        audio_content = workspace.findChild(QQuickItem, "audioContent")
        assert parameter_list is not None and parameter_list.isVisible(), (
            controllers.audio.selectedAudioBusId,
            controllers.audio.selectedAudioEffectId,
            parameter_list.width() if parameter_list else None,
            parameter_list.height() if parameter_list else None,
            parameter_list.parent().property("width") if parameter_list else None,
            parameter_list.parent().property("height") if parameter_list else None,
            parameter_list.parent().property("visible") if parameter_list else None,
            (audio_scroll.width(), audio_scroll.height()) if audio_scroll else None,
            (audio_content.width(), audio_content.height()) if audio_content else None,
        )

        second_source = tmp_path / "ui-source-2.mp4"
        shutil.copyfile(source, second_source)
        controllers.media.importMedia(QUrl.fromLocalFile(str(second_source)).toString())
        assert _process_until(
            lambda: controllers.media.assetsModel.rowCount() == 2
            and controllers.media.selectedAssetId != asset_id,
            timeout=20,
        )
        second_asset_id = controllers.media.selectedAssetId
        controllers.media.selectAsset(asset_id, True)
        assert controllers.media.selectedAssetIds == [second_asset_id, asset_id]
        clip_count = controllers.timeline.clipsModel.rowCount()
        controllers.media.addSelectedAssetsToTimeline()
        assert controllers.timeline.clipsModel.rowCount() == clip_count + 2

        video_track = next(
            controllers.timeline.tracksModel.get(index)
            for index in range(controllers.timeline.tracksModel.rowCount())
            if controllers.timeline.tracksModel.get(index)["kind"] == "video"
        )
        controllers.timeline.updateTrack(
            video_track["trackId"],
            True,
            True,
            False,
            False,
            video_track["audioBusId"],
        )
        controllers.timeline.moveTrack(video_track["trackId"], 1)
        moved_track = controllers.timeline.tracksModel.get(1)
        assert moved_track["trackId"] == video_track["trackId"]
        assert moved_track["locked"] is True

        project_path = Path(controllers.workspace.projectPath)
        recent_artifact = project_path / "exports" / "recent-output.txt"
        recent_artifact.write_text("observable output", encoding="utf-8")
        TaskRepository(project_path).create(
            Task(
                project_id=controllers.session._documents.get_project().id,
                sequence_id=controllers.workspace.activeSequenceId,
                command=ExportSequenceCommand(
                    sequence_id=controllers.workspace.activeSequenceId,
                    output_path=str(recent_artifact),
                ),
                status=TaskStatus.FAILED,
                error="fixture failure",
                artifacts=[str(recent_artifact)],
            )
        )
        controllers.workspace.closeProject()
        assert _process_until(
            lambda: controllers.workspace.homeSummary["failedTaskCount"] >= 1
            and controllers.workspace.homeSummary["recentArtifactCount"] >= 1
            and controllers.workspace.recentProjectsModel.rowCount() > 0
            and bool(controllers.workspace.recentProjectsModel.get(0).get("coverUrl")),
            timeout=20,
        )
        assert controllers.workspace.homeSummary["failedTaskCount"] >= 1
        assert controllers.workspace.homeSummary["recentArtifactCount"] >= 1
        recent_row = controllers.workspace.recentProjectsModel.get(0)
        assert recent_row["recentArtifact"] == str(recent_artifact)
        assert recent_row["coverUrl"]
        cover_path = Path(QUrl(recent_row["coverUrl"]).toLocalFile())
        assert cover_path.is_file() and cover_path.stat().st_size > 0
        root.setWidth(1920)
        root.setHeight(1080)
        for _ in range(30):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        home = page_loader.property("item")
        home_content = home.findChild(QQuickItem, "homeContent")
        home_scroll = home.findChild(QQuickItem, "homeScroll")
        create_hero = home.findChild(QQuickItem, "homeCreateHero")
        create_hero_icon = home.findChild(QQuickItem, "createProjectHeroIcon")
        create_hero_title = home.findChild(QQuickItem, "createProjectHeroTitle")
        open_existing_button = home.findChild(QQuickItem, "openExistingProjectButton")
        download_url_field = home.findChild(QQuickItem, "downloadUrlField")
        paste_download_url_button = home.findChild(QQuickItem, "pasteDownloadUrlButton")
        quick_start_download_button = home.findChild(QQuickItem, "quickStartDownloadButton")
        recent_section = home.findChild(QQuickItem, "homeRecentSection")
        recent_grid = home.findChild(QQuickItem, "recentProjectGrid")
        recent_cover = next(
            (item for item in _visual_items(home) if item.objectName() == "recentProjectCover"),
            None,
        )
        recent_card = next(
            (item for item in _visual_items(home) if item.objectName() == "recentProjectCard"),
            None,
        )
        recent_preview = next(
            (item for item in _visual_items(home) if item.objectName() == "recentProjectPreview"),
            None,
        )
        recent_remove = next(
            (item for item in _visual_items(home) if item.objectName() == "removeRecentProjectButton"),
            None,
        )
        assert home_content is not None and home_scroll is not None
        assert create_hero is not None and recent_section is not None
        assert home_content.width() >= home.width() - 66
        assert recent_section.property("level") == 1
        assert home.findChild(QQuickItem, "createProjectButton") is None
        assert create_hero.isVisible() and create_hero.property("enabled")
        assert create_hero_icon is not None and create_hero_icon.width() >= 58
        assert create_hero_title is not None and create_hero_title.isVisible()
        assert create_hero_title.property("font").pixelSize() >= 30
        assert open_existing_button is not None and open_existing_button.isVisible()
        assert download_url_field is not None and download_url_field.isVisible()
        assert paste_download_url_button is not None and paste_download_url_button.isVisible()
        assert quick_start_download_button is not None and quick_start_download_button.isVisible()
        assert quick_start_download_button.property("text") == "下载并新建项目"
        assert QMetaObject.invokeMethod(create_hero, "click")
        create_project_dialog = home.findChild(QObject, "createProjectDialog")
        create_project_name_field = home.findChild(QQuickItem, "createProjectNameField")
        confirm_create_project = home.findChild(QQuickItem, "confirmCreateProjectButton")
        assert create_project_dialog is not None and create_project_dialog.property("visible")
        assert create_project_name_field is not None and create_project_name_field.isVisible()
        assert confirm_create_project is not None
        assert confirm_create_project.property("text") == "创建项目"
        assert confirm_create_project.property("enabled") is True
        assert QMetaObject.invokeMethod(create_project_dialog, "close")
        clipboard = QGuiApplication.clipboard()
        clipboard_before = clipboard.text()
        try:
            pasted_url = "https://example.com/video"
            clipboard.setText(pasted_url)
            assert QMetaObject.invokeMethod(paste_download_url_button, "click")
            assert _process_until(lambda: download_url_field.property("text") == pasted_url)
        finally:
            clipboard.setText(clipboard_before)
        assert recent_section.y() > create_hero.y() + create_hero.height()
        assert recent_cover is not None and recent_cover.isVisible(), (
            (recent_section.x(), recent_section.y(), recent_section.width(), recent_section.height()),
            (
                recent_grid.x(),
                recent_grid.y(),
                recent_grid.width(),
                recent_grid.height(),
                recent_grid.property("count"),
            )
            if recent_grid is not None
            else None,
            [item.objectName() for item in _visual_items(home) if item.objectName()],
        )
        assert recent_card is not None and 1.08 <= recent_card.height() / recent_card.width() <= 1.18
        assert recent_preview is not None
        assert recent_remove is not None and recent_remove.isVisible()
        assert abs(recent_preview.width() / recent_preview.height() - 4 / 3) < 0.02
        assert Path(QUrl(recent_cover.property("source")).toLocalFile()) == cover_path
        home_render = engine.rootObjects()[0].grabWindow()
        home_render_path = tmp_path / "home-with-real-project-cover.png"
        assert not home_render.isNull() and home_render.save(str(home_render_path))
        assert home_render_path.is_file() and home_render_path.stat().st_size > 0
        cover_origin = recent_cover.mapToScene(QPointF(0, 0))
        image_scale_x = home_render.width() / root.width()
        image_scale_y = home_render.height() / root.height()
        sampled_colors = {
            home_render.pixelColor(
                round((cover_origin.x() + x) * image_scale_x),
                round((cover_origin.y() + y) * image_scale_y),
            ).rgba()
            for x in range(8, round(recent_cover.width()) - 8, 16)
            for y in range(8, round(recent_cover.height()) - 8, 16)
        }
        assert len(sampled_colors) > 20
        recent_section_height = recent_section.height()
        home_content_height = float(home_scroll.property("contentHeight"))
        for index in range(9):
            name = f"Recent UI {index + 1}"
            extra_project = controllers.session._api.create_project(tmp_path / name, name)
            extra_project.close()
            controllers.session._remember_recent_project(tmp_path / name)
        assert _process_until(lambda: controllers.workspace.recentProjectsModel.rowCount() == 10)
        root.setWidth(1920)
        root.setHeight(1080)
        for _ in range(30):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        home = page_loader.property("item")
        home_scroll = home.findChild(QQuickItem, "homeScroll")
        recent_section = home.findChild(QQuickItem, "homeRecentSection")
        recent_grid = home.findChild(QQuickItem, "recentProjectGrid")
        assert home_scroll is not None and recent_section is not None and recent_grid is not None
        assert abs(recent_section.height() - recent_section_height) <= 1
        assert abs(float(home_scroll.property("contentHeight")) - home_content_height) <= 1
        assert recent_grid.property("interactive") is True
        assert float(recent_grid.property("contentHeight")) > recent_grid.height()
        visible_cards = [
            item
            for item in _visual_items(home)
            if item.objectName() == "recentProjectCard" and item.isVisible()
        ]
        assert visible_cards
        assert all(1.08 <= item.height() / item.width() <= 1.18 for item in visible_cards)
        home_scroll.setProperty("contentY", 0.0)
        recent_grid.setProperty(
            "contentY",
            min(
                float(recent_grid.property("cellHeight")),
                float(recent_grid.property("contentHeight")) - recent_grid.height(),
            ),
        )
        QCoreApplication.processEvents()
        assert float(recent_grid.property("contentY")) > 0
        assert abs(float(home_scroll.property("contentY"))) < 0.5
        assert QMetaObject.invokeMethod(recent_grid, "positionViewAtBeginning")
        QCoreApplication.processEvents()
        many_projects_render = engine.rootObjects()[0].grabWindow()
        many_projects_render_path = tmp_path / "home-with-ten-recent-projects.png"
        assert not many_projects_render.isNull()
        assert many_projects_render.save(str(many_projects_render_path))
        recent_remove = next(
            item
            for item in _visual_items(home)
            if item.objectName() == "removeRecentProjectButton" and item.isVisible()
        )
        removed_project_path = Path(recent_remove.property("projectPath"))
        assert QMetaObject.invokeMethod(recent_remove, "click")
        assert _process_until(lambda: controllers.workspace.recentProjectsModel.rowCount() == 9)
        persisted_recent_paths = SettingsRepository(
            os.environ["MEDIAFLOW_SETTINGS_PATH"]
        ).load().ui.recent_project_paths
        assert str(removed_project_path) not in persisted_recent_paths
        assert (removed_project_path / "project.mfp").is_file()
    finally:
        controllers.workspace.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()
    assert {
        name: _windows_environment(name) for name in ("MLT_DATA", "MLT_REPOSITORY", "MLT_REPOSITORY_DENY")
    } == mlt_environment_before
    assert _windows_dll_directory() == dll_directory_before
