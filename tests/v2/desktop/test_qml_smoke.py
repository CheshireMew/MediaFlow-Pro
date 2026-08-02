from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import wave
from ctypes import WinDLL, create_unicode_buffer
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFontDatabase,
    QGuiApplication,
    QImage,
    QWheelEvent,
    QWindow,
)
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from mediaflow.application.task_service import TaskContext, TaskStopped
from mediaflow.composition import EditorApplication
from mediaflow.desktop.app import configure_application_font, create_engine
from mediaflow.desktop.presentation_catalogs import (
    WORKSPACE_MODES,
    WORKSPACE_NAVIGATION_MODE_KEYS,
)
from mediaflow.domain.enums import TaskKind, TaskStatus, TrackKind
from mediaflow.domain.product_identity import PRODUCT_NAME
from mediaflow.domain.settings import AsrSettings
from mediaflow.domain.task_commands import (
    AnalyzeDownloadCommand,
    ExportSequenceCommand,
    TranscribeSequenceCommand,
)
from mediaflow.domain.tasks import ArtifactReference, Task
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.settings_repository import SettingsRepository
from mediaflow.infrastructure.task_repository import TaskRepository
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService
from tests.v2.infrastructure.test_media_pipeline import generate_real_media


class _OpenAITranslationFixtureHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(content_length))
        user_payload = json.loads(request["messages"][-1]["content"])
        translated = {
            "segments": [
                {"id": segment["id"], "text": "叠加在音频波形上的字幕"}
                for segment in user_payload["segments"]
            ]
        }
        response = json.dumps(
            {
                "id": "chatcmpl-mediaflow-fixture",
                "object": "chat.completion",
                "created": 1,
                "model": "fixture-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(translated, ensure_ascii=False),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, _format: str, *args: object) -> None:
        return


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


def _drag_quick_item(
    window: QWindow,
    source: QQuickItem,
    target: QQuickItem,
    target_position: QPointF,
    *,
    source_position: QPointF | None = None,
) -> None:
    source_scene = source.mapToScene(source_position or QPointF(source.width() / 2, source.height() / 2))
    target_scene = target.mapToScene(target_position)
    origin = QPoint(round(source_scene.x()), round(source_scene.y()))
    destination = QPoint(round(target_scene.x()), round(target_scene.y()))
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, origin, delay=20)
    QTest.mouseMove(window, QPoint(origin.x() + 24, origin.y() + 24), delay=50)
    QCoreApplication.processEvents()
    QTest.mouseMove(window, destination, delay=100)
    QCoreApplication.processEvents()
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, destination, delay=20)
    QCoreApplication.processEvents()


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
        monospace_family = str(
            engine.rootContext().contextProperty("applicationMonospaceFontFamily")
        )
        assert monospace_family
        assert QFontDatabase.hasFamily(monospace_family)
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
        empty_origin = empty_state.mapToScene(QPointF(0, 0))
        assert recent_origin.y() >= 0
        assert recent_origin.y() + recent_section.height() <= window.height()
        assert empty_origin.y() >= recent_origin.y()
        assert empty_origin.y() + empty_state.height() <= (
            recent_origin.y() + recent_section.height()
        )
        assert abs(
            empty_origin.x()
            + empty_state.width() / 2
            - recent_origin.x()
            - recent_section.width() / 2
        ) <= 2

        content = empty_state.childItems()[0]
        title = empty_state.findChild(QQuickItem, "emptyStateTitle")
        description = empty_state.findChild(QQuickItem, "emptyStateDescription")
        assert title is not None and title.property("text") == "还没有最近项目"
        assert description is not None
        assert str(description.property("text")).startswith("创建第一个项目后")
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
            lambda: controllers.workspace.hasProject and controllers.workspace.projectName == "未命名项目 1"
        )
        assert (tmp_path / "Projects" / "未命名项目 1" / "project.mfp").is_file()

        closing_project = controllers.session.binding.current
        assert closing_project is not None
        monkeypatch.setattr(
            "mediaflow.desktop.coordinators.project_lifecycle.PROJECT_CLOSE_TIMEOUT_SECONDS",
            0.05,
        )
        handler_started = threading.Event()
        stop_observed = threading.Event()
        allow_handler_to_stop = threading.Event()

        def slow_stop(context: TaskContext):
            handler_started.set()
            try:
                while True:
                    context.cancellation.raise_if_requested()
                    time.sleep(0.005)
            except TaskStopped:
                stop_observed.set()
                if not allow_handler_to_stop.wait(timeout=5):
                    raise RuntimeError("test did not release the slow task") from None
                raise

        closing_project._tasks._handlers[TaskKind.ANALYZE] = slow_stop
        slow_task = closing_project.start_task(
            AnalyzeDownloadCommand(url="test://desktop-slow-close")
        )
        assert handler_started.wait(timeout=5)
        controllers.workspace.closeProject()
        assert _process_until(
            lambda: page_loader.property("item") is not None
            and page_loader.property("item").objectName() == "homeView"
        )
        home = page_loader.property("item")
        create_hero = home.findChild(QQuickItem, "homeCreateHero")
        closing_panel = home.findChild(QQuickItem, "projectClosingPanel")
        retry_close = home.findChild(QQuickItem, "retryProjectCloseButton")
        assert controllers.workspace.projectReleasePending is True
        assert Path(controllers.workspace.closingProjectPath) == closing_project.project_dir
        assert closing_panel is not None and closing_panel.isVisible()
        assert create_hero is not None and create_hero.property("enabled") is False
        assert _process_until(lambda: controllers.workspace.projectCloseFailed)
        assert controllers.session.requests.closing_project is closing_project
        assert controllers.workspace.projectClosing is False
        assert controllers.workspace.projectCloseError
        assert stop_observed.is_set()
        assert retry_close is not None and retry_close.isVisible()

        observer = controllers.session._api.open_project(
            closing_project.project_dir,
            writable=True,
        )
        try:
            assert observer.read_only is True
        finally:
            observer.close()

        allow_handler_to_stop.set()
        assert _process_until(
            lambda: closing_project.get_task(slow_task.id).status == TaskStatus.PAUSED
        )
        assert QMetaObject.invokeMethod(retry_close, "click")
        assert _process_until(lambda: not controllers.workspace.projectReleasePending)
        assert controllers.session.requests.closing_project is None
        assert create_hero.property("enabled") is True

        controllers.workspace.openProject(str(closing_project.project_dir))
        assert _process_until(
            lambda: controllers.workspace.hasProject
            and controllers.workspace.projectName == "未命名项目 1"
        )
        assert controllers.workspace.readOnly is False
        assert controllers.session.binding.current.get_task(slow_task.id).status == TaskStatus.PAUSED
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def test_saved_maximized_window_keeps_clamped_normal_geometry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    settings_repository = SettingsRepository()
    settings = settings_repository.load()
    settings.ui.window_width = 1024
    settings.ui.window_height = 640
    settings.ui.window_maximized = True
    settings_repository.save(settings)
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app)
    try:
        window = engine.rootObjects()[0]
        available = window.screen().availableGeometry()
        expected_width = min(available.width(), max(window.minimumWidth(), 1024))
        expected_height = min(available.height(), max(window.minimumHeight(), 640))

        assert _process_until(
            lambda: window.visibility() == QWindow.Visibility.Maximized
        )
        assert window.minimumWidth() <= available.width()
        assert window.minimumHeight() <= available.height()
        assert window.property("restorableWidth") == expected_width
        assert window.property("restorableHeight") == expected_height
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def test_sample_project_opens_evolved_workspace_and_guided_tour(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    settings_repository = SettingsRepository()
    settings = settings_repository.load()
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
        sample_button = home.findChild(QQuickItem, "createSampleProjectButton")
        assert sample_button is not None and sample_button.isVisible()
        assert QMetaObject.invokeMethod(sample_button, "click")
        assert _process_until(
            lambda: controllers.workspace.hasProject
            and controllers.workspace.projectName.startswith("MediaFlow 示例项目")
            and page_loader.property("item") is not home,
            timeout=20,
        )

        workspace = page_loader.property("item")
        tour = window.findChild(QQuickItem, "workspaceTour")
        assert tour is not None and _process_until(tour.isVisible)
        assert controllers.media.assetsModel.rowCount() == 3
        assert controllers.media.assetBinsModel.rowCount() == 2
        assert controllers.workspace.sequencesModel.rowCount() == 2
        assert controllers.timeline.clipsModel.rowCount() == 3
        assert all(
            window.findChild(QQuickItem, object_name) is not None
            for object_name in ("workspaceLayoutButton", "globalTaskActivity")
        )
        assert all(
            workspace.findChild(QQuickItem, object_name) is not None
            for object_name in (
                "sequenceTabs",
                "programMonitorTab",
                "sourceMonitorTab",
                "assetBinToolbar",
                "assetSearchResultTabs",
            )
        )

        first_asset = controllers.media.assetsModel.get(0)["assetId"]
        controllers.media.openSourceMonitor(first_asset)
        source_tab = workspace.findChild(QQuickItem, "sourceMonitorTab")
        source_panel = workspace.findChild(QQuickItem, "previewPanel")
        assert source_tab is not None and _process_until(source_tab.isVisible)
        assert QMetaObject.invokeMethod(source_tab, "click")
        assert _process_until(lambda: source_panel.property("previewMode") == "source")

        controllers.settings.setWorkspaceLayoutPreset("media")
        assert _process_until(lambda: workspace.property("layoutPreset") == "media")
        controllers.settings.saveWorkspaceLayout(
            "media", 480, 380, 320, True, False, True
        )
        inspector = workspace.findChild(QQuickItem, "inspectorPanel")
        assert inspector is not None and _process_until(lambda: not inspector.isVisible())
        controllers.settings.setWorkspaceLayoutPreset("standard")
        assert _process_until(
            lambda: workspace.property("layoutPreset") == "standard"
            and inspector.isVisible()
        )
        workspace.setProperty("maximizedPanel", "preview")
        tool_panel = workspace.findChild(QQuickItem, "toolPanelContainer")
        timeline = workspace.findChild(QQuickItem, "timelinePanel")
        assert _process_until(
            lambda: not tool_panel.isVisible()
            and not inspector.isVisible()
            and not timeline.isVisible()
        )
        workspace.setProperty("maximizedPanel", "")
        assert _process_until(
            lambda: tool_panel.isVisible() and inspector.isVisible() and timeline.isVisible()
        )

        first_clip = controllers.timeline.clipsModel.get(1)["clipId"]
        second_clip = controllers.timeline.clipsModel.get(2)["clipId"]
        controllers.timeline.selectClip(first_clip)
        effect_panel = workspace.findChild(QQuickItem, "visualEffectStackPanel")
        replace_button = workspace.findChild(QQuickItem, "replaceClipSourceButton")
        assert effect_panel is not None and _process_until(effect_panel.isVisible)
        assert controllers.timeline.selectedClipVisualEffects
        assert replace_button is not None and replace_button.isVisible()
        controllers.timeline.selectClip(second_clip, True)
        multi_panel = workspace.findChild(QQuickItem, "multiClipInspector")
        assert multi_panel is not None and _process_until(multi_panel.isVisible)

        assert QMetaObject.invokeMethod(tour, "finish")
        assert not tour.isVisible()
        assert SettingsRepository().load().ui.workspace_tour_completed is True
        rendered = window.grabWindow()
        screenshot = tmp_path / "sample-evolved-workspace.png"
        assert not rendered.isNull() and rendered.save(str(screenshot))
        assert screenshot.is_file() and screenshot.stat().st_size > 0
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def test_settings_exposes_selectable_external_speech_components(
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
            "Speech Settings",
        )
        assert controllers.workspace.hasProject
        window = engine.rootObjects()[0]
        window.setWidth(1600)
        window.setHeight(980)
        page_loader = window.findChild(QObject, "pageLoader")
        assert page_loader is not None
        assert _process_until(
            lambda: page_loader.property("item") is not None
            and page_loader.property("item").objectName() == "workspace"
        )
        workspace = page_loader.property("item")
        assert workspace is not None
        dialog = workspace.findChild(QObject, "settingsDialog")
        tabs = workspace.findChild(QQuickItem, "settingsTabs")
        xxl = workspace.findChild(QQuickItem, "selectFasterWhisperDownload")
        gpt = workspace.findChild(QQuickItem, "selectGptSoVitsDownload")
        download = workspace.findChild(QQuickItem, "downloadSelectedRuntimeComponents")
        root_field = workspace.findChild(QQuickItem, "gptSoVitsRootField")
        model_directory_field = workspace.findChild(QQuickItem, "asrModelDirectoryField")
        assert all(
            item is not None
            for item in (dialog, tabs, xxl, gpt, download, root_field, model_directory_field)
        )
        assert QMetaObject.invokeMethod(dialog, "open")
        tabs.setProperty("currentIndex", 1)
        assert _process_until(lambda: xxl.isVisible() and gpt.isVisible())
        assert "Faster-Whisper XXL" in str(xxl.property("text"))
        assert "GPT-SoVITS v2Pro" in str(gpt.property("text"))
        assert download.property("enabled") is False
        xxl.setProperty("checked", True)
        assert _process_until(lambda: download.property("enabled") is True)
        dialog.close()
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def test_read_only_project_disables_mutations_across_qml_entry_points(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    project_root = tmp_path / "Read Only"
    holder = EditorApplication().create_project(project_root, "Read Only")
    project = holder.get_project()
    editor = holder.timeline(project.main_sequence_id)
    source = tmp_path / "read-only-source.mp4"
    generate_real_media(
        source,
        RuntimePaths.discover(),
        width=320,
        height=180,
    )
    asset = holder.import_external_asset(source)
    track_ids = {
        kind.value: editor.add_track(kind).id
        for kind in (TrackKind.VIDEO, TrackKind.AUDIO, TrackKind.SUBTITLE)
    }
    editor.add_clip(
        track_id=track_ids["video"],
        asset_id=asset.id,
        timeline_start=0,
        source_in=0,
        duration=min(25, asset.metadata.duration_frames),
    )
    TaskRepository(holder).create(
        Task(
            project_id=project.id,
            sequence_id=project.main_sequence_id,
            command=ExportSequenceCommand(
                sequence_id=project.main_sequence_id,
                output_path=str(project_root / "exports" / "readonly.mp4"),
            ),
            status=TaskStatus.FAILED,
            error="read-only fixture failure",
        )
    )
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app)
    try:
        controllers.workspace.openProject(
            QUrl.fromLocalFile(str(project_root)).toString()
        )
        assert controllers.workspace.readOnly is True
        window = engine.rootObjects()[0]
        page_loader = window.findChild(QQuickItem, "pageLoader")
        assert page_loader is not None
        assert _process_until(
            lambda: page_loader.property("item") is not None
            and page_loader.property("item").objectName() == "workspace"
        )
        workspace = page_loader.property("item")
        track_controls = workspace.findChild(QQuickItem, "trackControlsPanel")
        assert track_controls is not None

        def mute_button(kind: str):
            object_name = f"trackMuteButton_{track_ids[kind]}"
            return next(
                (
                    item
                    for item in _visual_items(track_controls)
                    if item.objectName() == object_name
                ),
                None,
            )

        mute_controls_ready = _process_until(
            lambda: track_controls.isVisible()
            and controllers.timeline.tracksModel.rowCount() >= 3
            and all(mute_button(kind) is not None for kind in ("video", "audio"))
        )
        assert mute_controls_ready, {
            "trackControlsVisible": track_controls.isVisible(),
            "trackCount": controllers.timeline.tracksModel.rowCount(),
            "trackIds": track_ids,
            "muteObjects": [
                item.objectName()
                for item in _visual_items(track_controls)
                if "Mute" in item.objectName()
            ],
        }
        for kind in ("video", "audio"):
            button = mute_button(kind)
            assert button is not None and button.isVisible()
            assert button.property("text") == "M"
            assert button.property("enabled") is False
        assert mute_button("subtitle") is None

        user_errors: list[str] = []
        controllers.audio.errorOccurred.connect(user_errors.append)
        task_count_before_read_only_action = controllers.tasks.tasksModel.rowCount()
        controllers.audio.analyzeLoudness()
        assert user_errors == ["项目以只读方式打开"]
        assert controllers.tasks.tasksModel.rowCount() == task_count_before_read_only_action
        preview_errors: list[str] = []
        controllers.workspace.errorOccurred.connect(
            preview_errors.append
        )
        controllers.workspace.reportPreviewDroppedFrames(
            controllers.session.settings.preview.dropped_frame_proxy_threshold
        )
        QCoreApplication.processEvents()
        assert preview_errors == []
        assert (
            controllers.tasks.tasksModel.rowCount()
            == task_count_before_read_only_action
        )
        assert _process_until(
            lambda: any(
                "项目以只读方式打开" in str(item.property("text") or "")
                and item.isVisible()
                for item in _visual_items(window.contentItem())
            )
        )

        import_button = workspace.findChild(QQuickItem, "openMediaImportButton")
        download_button = workspace.findChild(
            QQuickItem,
            "workspaceAnalyzeDownloadButton",
        )
        import_dialog = workspace.findChild(QObject, "mediaImportDialog")
        media_drop = workspace.findChild(QQuickItem, "mediaFileDropArea")
        timeline_drop = workspace.findChild(QQuickItem, "timelineDropArea")
        assert import_button is not None and import_button.property("enabled") is False
        assert (
            download_button is not None
            and download_button.property("enabled") is False
        )
        assert import_dialog is not None and import_dialog.property("visible") is False
        assert media_drop is not None and media_drop.property("enabled") is False
        assert timeline_drop is not None and timeline_drop.property("enabled") is False

        QTest.keyClick(window, Qt.Key_I, Qt.ControlModifier)
        QCoreApplication.processEvents()
        assert import_dialog.property("visible") is False

        sequence_menu_button = workspace.findChild(QQuickItem, "sequenceMenuButton")
        assert sequence_menu_button is not None
        assert QMetaObject.invokeMethod(sequence_menu_button, "click")
        create_short = workspace.findChild(QQuickItem, "createShortSequenceButton")
        edit_profile = workspace.findChild(QQuickItem, "editSequenceProfileButton")
        assert create_short is not None and create_short.property("enabled") is False
        assert edit_profile is not None and edit_profile.property("enabled") is False
        QTest.keyClick(window, Qt.Key_Escape)

        timeline_zoom = workspace.findChild(QQuickItem, "timelineZoomSlider")
        settings_button = workspace.findChild(QQuickItem, "navigationItem_settings")
        settings_dialog = workspace.findChild(QObject, "settingsDialog")
        assert timeline_zoom is not None and settings_button is not None
        assert settings_dialog is not None
        zoom_before_modal = timeline_zoom.property("value")
        visibility_before_modal = window.visibility()
        assert QMetaObject.invokeMethod(settings_button, "click")
        assert _process_until(lambda: settings_dialog.property("visible") is True)
        QTest.keyClick(window, Qt.Key_Minus)
        QTest.keyClick(window, Qt.Key_F11)
        QCoreApplication.processEvents()
        assert timeline_zoom.property("value") == zoom_before_modal
        assert window.visibility() == visibility_before_modal
        settings_dialog.close()

        workspace.setProperty("activeMode", "tasks")
        assert _process_until(
            lambda: any(
                item.property("text") == "重试" and item.isVisible()
                for item in _visual_items(workspace)
            )
        )
        retry_button = next(
            item
            for item in _visual_items(workspace)
            if item.property("text") == "重试" and item.isVisible()
        )
        assert retry_button.property("enabled") is False
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()
        holder.close()


def test_export_capability_follows_real_active_sequence_content(
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
            "Export Capability",
        )
        assert controllers.workspace.hasProject
        main_sequence_id = controllers.workspace.activeSequenceId
        window = engine.rootObjects()[0]
        window.setWidth(1600)
        window.setHeight(980)
        page_loader = window.findChild(QObject, "pageLoader")
        assert page_loader is not None
        assert _process_until(
            lambda: page_loader.property("item") is not None
            and page_loader.property("item").objectName() == "workspace"
        )
        workspace = page_loader.property("item")
        workspace.setProperty("activeMode", "export")
        export_to_project = workspace.findChild(QQuickItem, "exportToProjectButton")
        export_as = workspace.findChild(QQuickItem, "exportAsButton")
        export_fcpxml = workspace.findChild(QQuickItem, "exportFcpxmlButton")
        export_encoder = workspace.findChild(QQuickItem, "exportEncoderField")
        export_buttons = (export_to_project, export_as, export_fcpxml)
        assert all(button is not None for button in export_buttons)
        assert export_encoder is not None
        assert _process_until(
            lambda: export_encoder.property("currentIndex") >= 0
            and export_encoder.property("currentValue")
            in {option["value"] for option in controllers.export.videoEncoderOptions}
        )
        assert controllers.export.canExportSequence is False
        assert all(not button.isEnabled() for button in export_buttons)

        errors: list[str] = []
        controllers.export.errorOccurred.connect(errors.append)
        blocked_video = tmp_path / "blocked.mp4"
        blocked_fcpxml = tmp_path / "blocked.fcpxml"
        blocked_calls = (
            lambda: controllers.export.exportFcpxml(str(blocked_fcpxml)),
            lambda: controllers.export.exportSequenceToDefaultLocation("h264", "mp4", {}),
            lambda: controllers.export.exportSequenceWithOptions(
                "h264",
                str(blocked_video),
                {},
            ),
        )
        for call in blocked_calls:
            call()
        assert errors == ["当前序列没有可导出的媒体片段"] * len(blocked_calls)
        assert controllers.tasks.tasksModel.rowCount() == 0
        assert not blocked_video.exists()
        assert not blocked_fcpxml.exists()

        source = tmp_path / "exportable-source.mp4"
        generate_real_media(source, RuntimePaths.discover(), width=320, height=180)
        controllers.media.importFiles(
            [QUrl.fromLocalFile(str(source))]
        )
        assert _process_until(
            lambda: controllers.media.assetsModel.rowCount() == 1,
            timeout=20,
        )
        asset_id = controllers.media.assetsModel.get(0)["assetId"]
        controllers.timeline.dropAssets(
            [asset_id],
            "",
            -1,
            0,
            3.0,
            0,
            True,
            False,
        )
        assert _process_until(
            lambda: controllers.timeline.clipsModel.rowCount() == 1
            and controllers.export.canExportSequence,
            timeout=20,
        )
        assert all(button.isEnabled() for button in export_buttons)
        video_track = next(
            controllers.timeline.tracksModel.get(index)
            for index in range(controllers.timeline.tracksModel.rowCount())
            if controllers.timeline.tracksModel.get(index)["kind"] == "video"
        )
        controllers.timeline.updateTrack(
            video_track["trackId"],
            False,
            video_track["locked"],
            video_track["muted"],
            video_track["solo"],
            video_track["audioBusId"],
        )
        assert _process_until(
            lambda: not controllers.export.canExportSequence
            and all(not button.isEnabled() for button in export_buttons)
        )
        controllers.timeline.updateTrack(
            video_track["trackId"],
            True,
            video_track["locked"],
            video_track["muted"],
            video_track["solo"],
            video_track["audioBusId"],
        )
        assert _process_until(
            lambda: controllers.export.canExportSequence
            and all(button.isEnabled() for button in export_buttons)
        )

        assert QMetaObject.invokeMethod(export_to_project, "click")
        assert _process_until(
            lambda: controllers.tasks.latestTask(
                "export",
                main_sequence_id,
            ).get("status")
            in {"completed", "failed", "cancelled"},
            timeout=30,
        )
        export_task = controllers.tasks.latestTask("export", main_sequence_id)
        assert export_task["status"] == "completed", export_task
        assert export_task["artifacts"]
        assert Path(export_task["artifacts"][0]).is_file()

        controllers.workspace.createShortSequence("Empty Sequence")
        assert _process_until(
            lambda: not controllers.export.canExportSequence
            and all(not button.isEnabled() for button in export_buttons)
        )
        controllers.workspace.selectSequence(main_sequence_id)
        assert _process_until(
            lambda: controllers.export.canExportSequence
            and all(button.isEnabled() for button in export_buttons)
        )
        controllers.timeline.selectAllClips()
        controllers.timeline.deleteSelectedClips(False)
        assert _process_until(
            lambda: controllers.timeline.clipsModel.rowCount() == 0
            and not controllers.export.canExportSequence
            and all(not button.isEnabled() for button in export_buttons)
        )
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
    opened_folders: list[Path] = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        staticmethod(lambda url: opened_folders.append(Path(url.toLocalFile()).resolve()) or True),
    )
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
        window.setWidth(1920)
        window.setHeight(1080)
        assert _process_until(lambda: window.findChild(QQuickItem, "timelineDropArea") is not None)
        assert window.findChild(QQuickItem, "mediaFileDropArea") is not None
        snap_button = window.findChild(QQuickItem, "timelineSnapButton")
        assert snap_button is not None and snap_button.property("checked") is True
        QTest.keyClick(window, Qt.Key_S)
        assert _process_until(lambda: snap_button.property("checked") is False)
        QTest.keyClick(window, Qt.Key_S)
        assert _process_until(lambda: snap_button.property("checked") is True)
        media_search = window.findChild(QQuickItem, "mediaSearchField")
        media_toolbar = window.findChild(QQuickItem, "mediaToolbar")
        toolbar_view_mode = window.findChild(QQuickItem, "mediaViewModeButton")
        import_button = window.findChild(QQuickItem, "openMediaImportButton")
        assert all(
            item is not None
            for item in (
                media_search,
                media_toolbar,
                toolbar_view_mode,
                import_button,
            )
        )
        assert all(
            item.parentItem() == media_toolbar for item in (media_search, toolbar_view_mode, import_button)
        )
        toolbar_items = (media_search, toolbar_view_mode, import_button)
        toolbar_positions = [item.mapToItem(media_toolbar, QPointF(0, 0)) for item in toolbar_items]
        assert toolbar_positions[0].x() < toolbar_positions[1].x() < toolbar_positions[2].x()
        assert all(
            abs(position.y() + item.height() / 2 - media_toolbar.height() / 2) <= 1
            for item, position in zip(toolbar_items, toolbar_positions, strict=True)
        )
        media_search.forceActiveFocus()
        QTest.keyClick(window, Qt.Key_S)
        assert media_search.property("text") == "s"
        assert snap_button.property("checked") is True
        media_search.setProperty("text", "")
        window.contentItem().forceActiveFocus()

        assert controllers.timeline.tracksModel.rowCount() == 0
        preview_viewport = window.findChild(QQuickItem, "previewViewport")
        assert preview_viewport is not None
        preview_viewport.seek(17)
        assert preview_viewport.property("scrubFrame") == 17
        preview_viewport.seek(0)
        video_source = tmp_path / "video" / "first-video.mp4"
        video_source.parent.mkdir()
        generate_real_media(video_source, RuntimePaths.discover(), width=640, height=360)
        controllers.media.importFiles([QUrl.fromLocalFile(str(video_source))])
        assert _process_until(
            lambda: controllers.media.assetsModel.rowCount() == 1,
            timeout=20,
        )
        video_asset = controllers.media.assetsModel.get(0)
        media_panel = window.findChild(QQuickItem, "mediaPanel")
        timeline_drop_area = window.findChild(QQuickItem, "timelineDropArea")
        assert media_panel is not None and timeline_drop_area is not None
        assert _process_until(
            lambda: any(
                item.objectName() == "mediaAssetDelegate"
                and item.property("assetId") == video_asset["assetId"]
                for item in _visual_items(media_panel)
            )
        )
        video_delegate = next(
            item
            for item in _visual_items(media_panel)
            if item.objectName() == "mediaAssetDelegate"
            and item.property("assetId") == video_asset["assetId"]
        )
        _drag_quick_item(
            window,
            video_delegate,
            timeline_drop_area,
            QPointF(75 * 3.0, 36),
        )
        assert _process_until(
            lambda: controllers.timeline.clipsModel.rowCount() == 1
            and controllers.workspace.profileConfirmed,
            timeout=20,
        )
        first_clip = controllers.timeline.clipsModel.get(0)
        video_track = next(
            controllers.timeline.tracksModel.get(index)
            for index in range(controllers.timeline.tracksModel.rowCount())
            if controllers.timeline.tracksModel.get(index)["kind"] == "video"
        )
        assert controllers.timeline.tracksModel.rowCount() == 2
        assert first_clip["trackId"] == video_track["trackId"]
        assert first_clip["startFrame"] == 0
        assert controllers.workspace.profileLabel == "640×360  25 fps"
        timeline_view = window.findChild(QQuickItem, "timelinePanel")
        assert timeline_view is not None
        assert _process_until(
            lambda: any(
                item.objectName() == "timelineClip" and item.property("clipId") == first_clip["clipId"]
                for item in _visual_items(timeline_view)
            )
        )
        first_clip_item = next(
            item
            for item in _visual_items(timeline_view)
            if item.objectName() == "timelineClip" and item.property("clipId") == first_clip["clipId"]
        )
        assert first_clip_item.isVisible()
        assert abs(first_clip_item.x()) <= 1
        assert _process_until(
            lambda: controllers.timeline.clipsModel.get(0)["waveformReady"],
            timeout=20,
        )
        assert _process_until(
            lambda: bool(controllers.media.assetsModel.get(0)["previewUrl"]),
            timeout=20,
        )
        video_thumbnail = Path(QUrl(controllers.media.assetsModel.get(0)["previewUrl"]).toLocalFile())
        assert video_thumbnail.is_file() and video_thumbnail.stat().st_size > 0
        timeline_view.openClipContextMenu(first_clip["clipId"])
        clip_context_menu = window.findChild(QObject, "timelineClipContextMenu")
        assert clip_context_menu is not None
        assert _process_until(lambda: clip_context_menu.property("visible"))
        assert controllers.timeline.selectedClipId == first_clip["clipId"]
        split_menu_item = window.findChild(QQuickItem, "timelineSplitClipMenuItem")
        detach_menu_item = window.findChild(QQuickItem, "timelineDetachAudioMenuItem")
        assert split_menu_item is not None and split_menu_item.isVisible()
        assert detach_menu_item is not None and detach_menu_item.isVisible()
        assert detach_menu_item.property("enabled") is True
        assert QMetaObject.invokeMethod(detach_menu_item, "click")
        assert _process_until(
            lambda: controllers.timeline.clipsModel.rowCount() == 2
            and not clip_context_menu.property("visible")
        )
        timeline_view.openClipContextMenu(first_clip["clipId"])
        assert _process_until(lambda: clip_context_menu.property("visible"))
        assert detach_menu_item.property("enabled") is False
        menu_background = clip_context_menu.property("background")
        assert menu_background is not None
        assert menu_background.property("color") == QColor("#303136")
        assert clip_context_menu.setProperty("currentIndex", 2)
        assert _process_until(lambda: detach_menu_item.property("highlighted"))
        detach_background = detach_menu_item.property("background")
        detach_content = detach_menu_item.property("contentItem")
        assert detach_background is not None and detach_content is not None
        assert _process_until(
            lambda: detach_background.property("color") == QColor("#36373b")
        )
        assert detach_content.property("textColor") == QColor("#62656b")
        split_content = split_menu_item.property("contentItem")
        split_text_items = {
            item.property("text"): item for item in split_content.childItems() if item.property("text")
        }
        assert "在播放头处分割" in split_text_items
        assert split_text_items["Ctrl+K"].x() >= (
            split_text_items["在播放头处分割"].x() + split_text_items["在播放头处分割"].width() + 20
        )
        context_menu_render = window.grabWindow()
        context_menu_render_path = tmp_path / "timeline-context-menu-themed.png"
        assert not context_menu_render.isNull()
        assert context_menu_render.save(str(context_menu_render_path))
        assert context_menu_render_path.is_file() and context_menu_render_path.stat().st_size > 0
        QTest.keyClick(window, Qt.Key_Escape)
        assert _process_until(lambda: not clip_context_menu.property("visible"))
        controllers.timeline.undo()
        assert _process_until(lambda: controllers.timeline.clipsModel.rowCount() == 1)
        assert _process_until(
            lambda: any(
                item.isVisible() and item.property("assetId") == first_clip["assetId"]
                for item in _visual_items(timeline_view)
                if item.objectName() == "clipWaveform"
            )
        )
        preview_player = window.findChild(QQuickItem, "previewPlayer")
        timeline_scroll = window.findChild(QQuickItem, "timelineScroll")
        timeline_ruler = window.findChild(QQuickItem, "timelineRuler")
        timeline_toolbar = window.findChild(QQuickItem, "timelineToolbarScroll")
        timeline_zoom_slider = window.findChild(QQuickItem, "timelineZoomSlider")
        assert all(
            item is not None
            for item in (
                preview_player,
                timeline_scroll,
                timeline_ruler,
                timeline_toolbar,
                timeline_zoom_slider,
            )
        )
        assert _process_until(
            lambda: bool(controllers.workspace.previewGraphPath)
            and Path(controllers.workspace.previewGraphPath).is_file()
            and preview_player.property("duration") == controllers.workspace.timelineDurationFrames
            and not preview_player.property("errorString"),
            timeout=20,
            )
        initial_preview_graph = Path(controllers.workspace.previewGraphPath)
        assert initial_preview_graph.name.startswith("pv-")
        assert initial_preview_graph.is_relative_to(
            (
                tmp_path
                / "runtime"
                / "cache"
                / "projects"
            ).resolve()
        )

        timeline_view.setProperty("pixelsPerFrame", 0.5)
        QCoreApplication.processEvents()
        wide_tick_step = int(timeline_ruler.property("majorStepFrames"))
        timeline_view.setProperty("pixelsPerFrame", 12.0)
        QCoreApplication.processEvents()
        detailed_tick_step = int(timeline_ruler.property("majorStepFrames"))
        assert detailed_tick_step < wide_tick_step
        assert not timeline_ruler.childItems()

        timeline_view.setProperty("pixelsPerFrame", 8.0)
        QCoreApplication.processEvents()
        timeline_scroll.setProperty("contentX", 50.0)
        QCoreApplication.processEvents()
        anchor_x = 500.0
        anchor_frame = (timeline_scroll.property("contentX") + anchor_x) / 8.0
        timeline_view.setTimelineZoom(12.0, anchor_frame, anchor_x)
        QCoreApplication.processEvents()
        assert (
            abs(
                anchor_frame * timeline_view.property("pixelsPerFrame")
                - timeline_scroll.property("contentX")
                - anchor_x
            )
            <= 2
        )

        toolbar_end = max(
            0,
            timeline_toolbar.property("contentWidth") - timeline_toolbar.width(),
        )
        timeline_toolbar.setProperty("contentX", toolbar_end)
        QCoreApplication.processEvents()
        slider_scene = timeline_zoom_slider.mapToScene(
            QPointF(timeline_zoom_slider.width() * 0.35, timeline_zoom_slider.height() / 2)
        )
        zoom_before_mouse = float(timeline_view.property("pixelsPerFrame"))
        QTest.mouseClick(
            window,
            Qt.LeftButton,
            Qt.NoModifier,
            QPoint(round(slider_scene.x()), round(slider_scene.y())),
            delay=20,
        )
        assert _process_until(
            lambda: abs(float(timeline_view.property("pixelsPerFrame")) - zoom_before_mouse) > 0.1
        )
        timeline_view.setProperty("pixelsPerFrame", 3.0)
        timeline_scroll.setProperty("contentX", 0.0)
        QCoreApplication.processEvents()
        wheel_scene = timeline_scroll.mapToScene(
            QPointF(timeline_scroll.width() / 2, timeline_scroll.height() / 2)
        )
        wheel_global = window.mapToGlobal(QPoint(round(wheel_scene.x()), round(wheel_scene.y())))
        wheel_event = QWheelEvent(
            wheel_scene,
            QPointF(wheel_global),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.NoButton,
            Qt.ControlModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        QGuiApplication.sendEvent(window, wheel_event)
        assert _process_until(lambda: timeline_view.property("pixelsPerFrame") > 3.1)
        timeline_view.fitTimeline()

        sequence_last_frame = controllers.workspace.timelineDurationFrames - 1
        playback_frame = max(3, sequence_last_frame // 2)
        controllers.timeline.setSequenceInOut(0, 2)
        timeline_view.seekToFrame(playback_frame)
        assert _process_until(
            lambda: preview_player.property("position") == playback_frame
            and timeline_view.property("visiblePlayheadFrame") == playback_frame
        )
        QTest.keyClick(window, Qt.Key_Space)
        assert _process_until(lambda: preview_player.property("playing") is True)
        assert preview_player.property("position") >= playback_frame
        QTest.keyClick(window, Qt.Key_Space)
        assert _process_until(lambda: preview_player.property("playing") is False)
        timeline_view.seekToFrame(sequence_last_frame)
        assert _process_until(lambda: preview_player.property("position") == sequence_last_frame)
        QTest.keyClick(window, Qt.Key_Space)
        QCoreApplication.processEvents()
        assert preview_player.property("playing") is False
        assert preview_player.property("position") == sequence_last_frame
        controllers.timeline.clearSequenceInOut()
        timeline_view.setProperty("pixelsPerFrame", 3.0)
        timeline_scroll.setProperty("contentX", 0.0)
        timeline_view.seekToFrame(0)
        assert _process_until(lambda: preview_player.property("position") == 0)

        image_source = tmp_path / "image" / "still.png"
        image_source.parent.mkdir()
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
        assert _process_until(
            lambda: bool(
                controllers.media.assetsModel.get(
                    controllers.media.assetsModel.findRow("assetId", image_asset["assetId"])
                )["previewUrl"]
            ),
            timeout=20,
        )
        image_asset = controllers.media.assetsModel.get(
            controllers.media.assetsModel.findRow("assetId", image_asset["assetId"])
        )

        def asset_delegate(asset_id: str) -> QQuickItem:
            return next(
                item
                for item in _visual_items(media_panel)
                if item.objectName() == "mediaAssetDelegate" and item.property("assetId") == asset_id
            )

        assert _process_until(
            lambda: any(
                item.objectName() == "mediaAssetDelegate"
                and item.property("assetId") == image_asset["assetId"]
                for item in _visual_items(media_panel)
            )
        )
        image_delegate = asset_delegate(image_asset["assetId"])
        assert media_panel.property("viewMode") == "list"
        assert image_delegate.height() == 30
        assert image_delegate.property("viewMode") == "list"
        image_preview = image_delegate.findChild(QQuickItem, "assetPreviewImage")
        assert image_preview is not None
        assert (image_preview.width(), image_preview.height()) == (36, 22)
        assert (
            QUrl(image_preview.property("source")).toLocalFile()
            == QUrl(image_asset["previewUrl"]).toLocalFile()
        )

        view_mode_button = window.findChild(QQuickItem, "mediaViewModeButton")
        assert view_mode_button is not None
        assert window.findChild(QObject, "mediaViewModeMenu") is None
        assert window.findChild(QObject, "mediaListViewModeButton") is None
        assert window.findChild(QObject, "mediaThumbnailViewModeButton") is None
        assert window.findChild(QObject, "mediaLargeThumbnailViewModeButton") is None
        assert view_mode_button.width() == 32
        assert view_mode_button.property("iconKind") == "list"
        assert QMetaObject.invokeMethod(view_mode_button, "click")
        assert _process_until(
            lambda: media_panel.property("viewMode") == "thumbnails"
            and view_mode_button.property("iconKind") == "thumbnails"
        )
        thumbnail_delegate = asset_delegate(image_asset["assetId"])
        assert thumbnail_delegate.property("viewMode") == "thumbnails"
        assert thumbnail_delegate.height() == 84
        media_search.setProperty("text", "still")
        media_grid = window.findChild(QQuickItem, "mediaAssetGridView")
        assert media_grid is not None
        assert _process_until(lambda: media_grid.property("count") == 1)
        media_search.setProperty("text", "")
        assert _process_until(lambda: media_grid.property("count") == 2)
        thumbnail_render = window.grabWindow()
        assert not thumbnail_render.isNull()
        assert thumbnail_render.save(str(tmp_path / "media-thumbnail-view.png"))

        assert QMetaObject.invokeMethod(view_mode_button, "click")
        assert _process_until(
            lambda: media_panel.property("viewMode") == "large_thumbnails"
            and view_mode_button.property("iconKind") == "large_thumbnails"
        )
        large_delegate = asset_delegate(image_asset["assetId"])
        assert large_delegate.property("viewMode") == "large_thumbnails"
        assert large_delegate.height() == 132
        assert SettingsRepository().load().ui.asset_view_mode == "large_thumbnails"
        large_thumbnail_render = window.grabWindow()
        assert not large_thumbnail_render.isNull()
        assert large_thumbnail_render.save(str(tmp_path / "media-large-thumbnail-view.png"))

        assert QMetaObject.invokeMethod(view_mode_button, "click")
        assert _process_until(
            lambda: media_panel.property("viewMode") == "list"
            and view_mode_button.property("iconKind") == "list"
        )
        image_delegate = asset_delegate(image_asset["assetId"])
        _drag_quick_item(
            window,
            image_delegate,
            timeline_drop_area,
            QPointF(
                (first_clip["endFrame"] - 1) * 3.0,
                video_track["position"] * 73 + 36,
            ),
        )
        assert controllers.timeline.clipsModel.rowCount() == 2
        snapped_image = next(
            controllers.timeline.clipsModel.get(index)
            for index in range(controllers.timeline.clipsModel.rowCount())
            if controllers.timeline.clipsModel.get(index)["assetKind"] == "image"
        )
        assert snapped_image["trackId"] == video_track["trackId"]
        assert snapped_image["startFrame"] == first_clip["endFrame"]
        assert _process_until(
            lambda: bool(controllers.workspace.previewGraphPath)
            and Path(controllers.workspace.previewGraphPath) != initial_preview_graph
            and Path(controllers.workspace.previewGraphPath).is_file(),
            timeout=20,
        )
        assert initial_preview_graph.is_file()

        track_count = controllers.timeline.tracksModel.rowCount()
        _drag_quick_item(
            window,
            image_delegate,
            timeline_drop_area,
            QPointF(0, video_track["position"] * 73 + 36),
        )
        assert controllers.timeline.tracksModel.rowCount() == track_count + 1
        overlapping_image = next(
            controllers.timeline.clipsModel.get(index)
            for index in range(controllers.timeline.clipsModel.rowCount())
            if controllers.timeline.clipsModel.get(index)["assetKind"] == "image"
            and controllers.timeline.clipsModel.get(index)["startFrame"] == 0
        )
        assert overlapping_image["trackId"] != video_track["trackId"]

        subtitle_source = tmp_path / "subtitle" / "captions.srt"
        subtitle_source.parent.mkdir()
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
        assert _process_until(
            lambda: any(
                item.objectName() == "mediaAssetDelegate"
                and item.property("assetId") == subtitle_asset["assetId"]
                for item in _visual_items(media_panel)
            )
        )
        subtitle_delegate = asset_delegate(subtitle_asset["assetId"])
        subtitle_icon = subtitle_delegate.findChild(QQuickItem, "assetKindIcon")
        assert subtitle_icon is not None
        assert subtitle_icon.property("iconName") == "subtitle"

        audio_source = tmp_path / "audio" / "tone.wav"
        audio_source.parent.mkdir()
        with wave.open(str(audio_source), "wb") as audio_file:
            audio_file.setnchannels(1)
            audio_file.setsampwidth(2)
            audio_file.setframerate(48000)
            audio_file.writeframes(b"\0\0" * 4800)
        controllers.media.importFiles([QUrl.fromLocalFile(str(audio_source))])
        assert _process_until(lambda: controllers.media.assetsModel.rowCount() == 4, timeout=20)
        audio_asset = next(
            controllers.media.assetsModel.get(index)
            for index in range(controllers.media.assetsModel.rowCount())
            if controllers.media.assetsModel.get(index)["kind"] == "audio"
        )

        asset_context_menu = window.findChild(QObject, "mediaAssetContextMenu")
        open_asset_folder = window.findChild(QQuickItem, "assetOpenFolderMenuItem")
        assert asset_context_menu is not None and open_asset_folder is not None
        for asset, source in (
            (video_asset, video_source),
            (audio_asset, audio_source),
            (image_asset, image_source),
            (subtitle_asset, subtitle_source),
        ):
            assert (
                Path(controllers.media.assetUrl(asset["assetId"]).toLocalFile()).resolve() == source.resolve()
            )
            media_panel.openAssetContextMenu(asset["assetId"])
            assert _process_until(lambda: asset_context_menu.property("visible"))
            assert open_asset_folder.isVisible()
            assert open_asset_folder.property("text") == "打开素材所在文件夹"
            if asset["kind"] == "subtitle":
                asset_menu_render = window.grabWindow()
                asset_menu_screenshot = tmp_path / "asset-context-menu-open-folder.png"
                assert not asset_menu_render.isNull()
                assert asset_menu_render.save(str(asset_menu_screenshot))
                assert asset_menu_screenshot.is_file() and asset_menu_screenshot.stat().st_size > 0
            assert QMetaObject.invokeMethod(open_asset_folder, "click")
            assert _process_until(lambda: not asset_context_menu.property("visible"))
        assert opened_folders == [
            video_source.parent.resolve(),
            audio_source.parent.resolve(),
            image_source.parent.resolve(),
            subtitle_source.parent.resolve(),
        ]
        audio_track = next(
            controllers.timeline.tracksModel.get(index)
            for index in range(controllers.timeline.tracksModel.rowCount())
            if controllers.timeline.tracksModel.get(index)["kind"] == "audio"
        )
        track_count = controllers.timeline.tracksModel.rowCount()
        controllers.timeline.dropAssets(
            [subtitle_asset["assetId"]],
            audio_track["trackId"],
            audio_track["position"],
            0,
            3.0,
            0,
            True,
            False,
        )
        assert controllers.timeline.tracksModel.rowCount() == track_count + 1
        assert _process_until(lambda: controllers.subtitles.subtitlePlacementsModel.rowCount() == 1)
        placement = controllers.subtitles.subtitlePlacementsModel.get(0)
        assert placement["audioTrackPosition"] == audio_track["position"]
        for _ in range(10):
            QCoreApplication.processEvents()
            time.sleep(0.01)
        rendered = window.grabWindow()
        screenshot = tmp_path / "drag-drop-timeline.png"
        assert not rendered.isNull() and rendered.save(str(screenshot))

        long_timeline_frame = 2 * 60 * 60 * 25
        controllers.timeline.dropAssets(
            [image_asset["assetId"]],
            video_track["trackId"],
            video_track["position"],
            long_timeline_frame,
            3.0,
            0,
            True,
            False,
        )
        assert controllers.workspace.timelineDurationFrames > long_timeline_frame
        timeline_view.fitTimeline()
        QCoreApplication.processEvents()
        assert abs(timeline_scroll.property("contentWidth") - timeline_scroll.width()) <= 2
        assert timeline_ruler.property("majorStepFrames") >= 5 * 60 * 25
        zoom_started = time.monotonic()
        for index in range(30):
            timeline_view.setTimelineZoom(
                0.02 if index % 2 == 0 else 0.04,
                long_timeline_frame / 2,
                timeline_scroll.width() / 2,
            )
            QCoreApplication.processEvents()
        assert time.monotonic() - zoom_started < 2.0
        assert not timeline_ruler.childItems()
        timeline_view.fitTimeline()
        QCoreApplication.processEvents()
        long_render = window.grabWindow()
        assert not long_render.isNull()
        assert long_render.save(str(tmp_path / "long-timeline-ruler.png"))

        online_delegate = next(
            item
            for item in _visual_items(media_panel)
            if item.objectName() == "mediaAssetDelegate" and item.property("status") == "online"
        )
        context_point = online_delegate.mapToScene(
            QPointF(online_delegate.width() / 2, online_delegate.height() / 2)
        )
        context_position = QPoint(round(context_point.x()), round(context_point.y()))
        QTest.mouseClick(
            window,
            Qt.RightButton,
            Qt.NoModifier,
            context_position,
            delay=20,
        )
        asset_context_menu = window.findChild(QObject, "mediaAssetContextMenu")
        add_at_playhead = window.findChild(QQuickItem, "assetAddAtPlayheadMenuItem")
        assert asset_context_menu is not None and _process_until(
            lambda: asset_context_menu.property("visible")
        )
        assert add_at_playhead is not None and add_at_playhead.isVisible()
        clip_count = controllers.timeline.clipsModel.rowCount()
        assert QMetaObject.invokeMethod(add_at_playhead, "click")
        assert _process_until(lambda: controllers.timeline.clipsModel.rowCount() == clip_count + 1)
        assert _process_until(lambda: not asset_context_menu.property("visible"))
        status_message = window.findChild(QQuickItem, "workspaceStatusMessage")
        assert status_message is not None
        assert str(status_message.property("text")).startswith("已将")
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def test_transcript_button_runs_real_timeline_chain_and_opens_generated_subtitles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(runtime_dir))
    fake_cli = tmp_path / "timeline_faster_whisper.py"
    fake_cli.write_text(
        """from pathlib import Path
import sys
import time

output = Path(sys.argv[sys.argv.index('-o') + 1])
output.mkdir(parents=True, exist_ok=True)
print('25%', flush=True)
time.sleep(0.15)
(output / 'timeline.srt').write_text(
    '1\\n00:00:00,100 --> 00:00:00,800\\nTimeline button output\\n',
    encoding='utf-8-sig',
)
print('100%', flush=True)
""",
        encoding="utf-8",
    )
    settings_repository = SettingsRepository()
    settings = settings_repository.load()
    settings.asr = AsrSettings(
        engine="faster_whisper_cli",
        cli_path=str(fake_cli),
        model="tiny.en",
        device="cpu",
        language="en",
    )
    settings_repository.save(settings)

    source = tmp_path / "timeline-source.mp4"
    generate_real_media(source, RuntimePaths.discover())
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app)
    try:
        controllers.workspace.createProject(
            QUrl.fromLocalFile(str(tmp_path)).toString(),
            "Timeline Transcription",
        )
        sequence_id = controllers.workspace.activeSequenceId
        controllers.timeline.importFilesToTimeline(
            [QUrl.fromLocalFile(str(source))],
            "",
            0,
            0,
            3.0,
            0,
            True,
            True,
        )
        assert _process_until(
            lambda: controllers.timeline.clipsModel.rowCount() == 1,
            timeout=20,
        )

        window = engine.rootObjects()[0]
        page_loader = window.findChild(QQuickItem, "pageLoader")
        assert page_loader is not None
        assert _process_until(
            lambda: page_loader.property("item") is not None
            and page_loader.property("item").objectName() == "workspace"
        )
        workspace = page_loader.property("item")
        assert workspace is not None
        navigation = workspace.findChild(QQuickItem, "workspaceNavigation")
        assert navigation is not None
        assert _process_until(
            lambda: any(
                item.objectName() == "navigationItem_transcript" for item in _visual_items(navigation)
            )
        )
        transcript_navigation = next(
            item for item in _visual_items(navigation) if item.objectName() == "navigationItem_transcript"
        )
        assert transcript_navigation is not None
        assert QMetaObject.invokeMethod(transcript_navigation, "click")
        assert _process_until(lambda: workspace.property("activeMode") == "transcript")
        transcribe_button = workspace.findChild(QQuickItem, "transcribeTimelineButton")
        model_select = workspace.findChild(QQuickItem, "transcriptionModelSelect")
        model_detail = workspace.findChild(QQuickItem, "transcriptionModelDetail")
        device_select = workspace.findChild(QQuickItem, "transcriptionDeviceSelect")
        parallel_select = workspace.findChild(QQuickItem, "transcriptionParallelSelect")
        assert transcribe_button is not None
        assert model_select is not None and model_select.isVisible()
        assert model_detail is not None and model_detail.isVisible()
        assert "tiny.en" in model_detail.property("text")
        assert device_select is not None and device_select.isVisible()
        assert parallel_select is not None and parallel_select.isVisible()
        assert model_select.property("currentValue") == "tiny.en"
        plan_summary = controllers.subtitles.transcriptionPlanSummary
        assert plan_summary["available"] is True
        assert plan_summary["sourceCount"] == 1
        assert plan_summary["regionCount"] == 1
        assert plan_summary["recognitionSeconds"] > 0
        progress_snapshots: list[float] = []

        def capture_transcription_progress() -> None:
            row = controllers.tasks.latestTask("transcribe", sequence_id)
            if row.get("hasOverallProgress"):
                progress_snapshots.append(float(row["overallProgressValue"]))

        controllers.tasks.tasksChanged.connect(capture_transcription_progress)
        assert transcribe_button.isVisible() and transcribe_button.property("enabled") is True
        assert QMetaObject.invokeMethod(transcribe_button, "click")

        def transcription_completed() -> bool:
            return any(
                isinstance(task.command, TranscribeSequenceCommand) and task.status == TaskStatus.COMPLETED
                for task in TaskRepository(
                    controllers.session.binding.current
                ).list()
            )

        assert _process_until(transcription_completed, timeout=20)
        transcription_task = next(
            task
            for task in TaskRepository(
                controllers.session.binding.current
            ).list()
            if isinstance(task.command, TranscribeSequenceCommand)
        )
        assert transcription_task.command.plan.asr.model == "tiny.en"
        assert transcription_task.command.plan.timeline_signature != "legacy"
        assert progress_snapshots
        assert progress_snapshots == sorted(progress_snapshots)
        documents = controllers.session.binding.current.list_subtitle_documents(sequence_id=sequence_id)
        assert len(documents) == 1
        assert documents[0].purpose == "sequence_transcript"
        assert not (
            controllers.session.binding.current.project_dir
            / "generated"
            / "audio"
            / f"{sequence_id}-transcription.wav"
        ).exists()
        segments = controllers.session.binding.current.list_subtitle_segments(documents[0].id)
        assert [segment.text for segment in segments] == ["Timeline button output"]
        words = controllers.session.binding.current.list_subtitle_words(documents[0].id)
        assert [word.text for word in words] == ["Timeline", "button", "output"]
        assert all(word.timing_source == "estimated" for word in words)
        subtitle_track = next(
            track
            for track in controllers.session.binding.current.load_timeline(sequence_id).tracks
            if track.kind == TrackKind.SUBTITLE
        )
        assert len(controllers.session.binding.current.list_subtitle_placements(subtitle_track.id)) == 1

        result_panel = workspace.findChild(QQuickItem, "transcriptResultPanel")
        open_subtitles = workspace.findChild(QQuickItem, "transcriptOpenSubtitleButton")
        assert _process_until(
            lambda: result_panel is not None
            and result_panel.isVisible()
            and open_subtitles is not None
            and open_subtitles.isVisible()
        )
        assert workspace.findChild(QQuickItem, "transcriptWordEditor") is None
        assert workspace.findChild(QQuickItem, "transcriptWordSegmentList") is None
        assert workspace.findChild(QQuickItem, "rippleDeleteTranscriptWordsButton") is None
        assert not any(
            item.objectName().startswith("transcriptWordItem_") for item in _visual_items(workspace)
        )
        assert QMetaObject.invokeMethod(open_subtitles, "click")
        transcript_tabs = workspace.findChild(QQuickItem, "transcriptWorkspaceTabs")
        assert transcript_tabs is not None
        assert _process_until(
            lambda: workspace.property("activeMode") == "transcript"
            and transcript_tabs.property("currentIndex") == 1
        )
        subtitle_list = workspace.findChild(QQuickItem, "subtitleSegmentList")
        assert subtitle_list is not None and subtitle_list.isVisible()
        controllers.subtitles.selectSubtitleSegment(segments[0].id, False)
        subtitle_text_editor = workspace.findChild(
            QQuickItem, "subtitleSegmentTextEditor")
        subtitle_save = workspace.findChild(
            QQuickItem, "subtitleSegmentSaveButton")
        assert subtitle_text_editor is not None and subtitle_save is not None
        subtitle_text_editor.forceActiveFocus()
        assert _process_until(
            lambda: subtitle_text_editor.property("activeFocus") is True)
        subtitle_text_editor.setProperty("text", "Timeline button draft")
        controllers.settings.selectGlossaryTerm("")
        QCoreApplication.processEvents()
        assert subtitle_text_editor.property("text") == "Timeline button draft"
        assert QMetaObject.invokeMethod(subtitle_save, "click")
        assert _process_until(
            lambda: controllers.session.binding.current.list_subtitle_segments(
                documents[0].id
            )[0].text
            == "Timeline button draft"
        )
        rendered = window.grabWindow()
        screenshot = tmp_path / "timeline-transcription-result.png"
        assert not rendered.isNull() and rendered.save(str(screenshot))
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def test_large_transcription_avoids_word_editor_and_keeps_native_preview_alive(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(runtime_dir))
    fake_cli = tmp_path / "large_transcript_faster_whisper.py"
    fake_cli.write_text(
        """from pathlib import Path
import sys

def stamp(milliseconds):
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}'

cues = []
for index in range(3000):
    start = index * 100
    end = start + 80
    cues.append(
        f'{index + 1}\\n{stamp(start)} --> {stamp(end)}\\n'
        f'word{index} extra\\n'
    )
output = Path(sys.argv[sys.argv.index('-o') + 1])
output.mkdir(parents=True, exist_ok=True)
(output / 'large.srt').write_text(
    '\\n'.join(cues),
    encoding='utf-8-sig',
)
print('100%', flush=True)
""",
        encoding="utf-8",
    )
    settings_repository = SettingsRepository()
    settings = settings_repository.load()
    settings.asr = AsrSettings(
        engine="faster_whisper_cli",
        cli_path=str(fake_cli),
        model="tiny.en",
        device="cpu",
        language="en",
        parallel_chunks=1,
    )
    settings_repository.save(settings)

    paths = RuntimePaths.discover()
    source = tmp_path / "large-transcript-source.m4a"
    generated = subprocess.run(
        [
            str(paths.ffmpeg),
            "-y",
            "-hide_banner",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=300",
            "-c:a",
            "aac",
            "-b:a",
            "24k",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert generated.returncode == 0, generated.stderr

    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app)
    try:
        controllers.workspace.createProject(
            QUrl.fromLocalFile(str(tmp_path)).toString(),
            "Large Timeline Transcription",
        )
        sequence_id = controllers.workspace.activeSequenceId
        controllers.timeline.importFilesToTimeline(
            [QUrl.fromLocalFile(str(source))],
            "",
            0,
            0,
            3.0,
            0,
            True,
            True,
        )
        assert _process_until(
            lambda: controllers.timeline.clipsModel.rowCount() == 1,
            timeout=20,
        )

        window = engine.rootObjects()[0]
        window.setWidth(1600)
        window.setHeight(980)
        page_loader = window.findChild(QQuickItem, "pageLoader")
        assert page_loader is not None
        assert _process_until(
            lambda: page_loader.property("item") is not None
            and page_loader.property("item").objectName() == "workspace"
        )
        workspace = page_loader.property("item")
        navigation = workspace.findChild(QQuickItem, "workspaceNavigation")
        transcript_navigation = next(
            item for item in _visual_items(navigation) if item.objectName() == "navigationItem_transcript"
        )
        assert QMetaObject.invokeMethod(transcript_navigation, "click")
        assert _process_until(lambda: workspace.property("activeMode") == "transcript")
        transcribe_button = workspace.findChild(
            QQuickItem,
            "transcribeTimelineButton",
        )
        assert transcribe_button is not None
        assert QMetaObject.invokeMethod(transcribe_button, "click")

        def transcription_completed() -> bool:
            return any(
                isinstance(task.command, TranscribeSequenceCommand) and task.status == TaskStatus.COMPLETED
                for task in TaskRepository(
                    controllers.session.binding.current
                ).list()
            )

        assert _process_until(transcription_completed, timeout=90)
        documents = controllers.session.binding.current.list_subtitle_documents(sequence_id=sequence_id)
        document = next(item for item in documents if item.purpose == "sequence_transcript")
        assert len(controllers.session.binding.current.list_subtitle_segments(document.id)) == 3000
        assert len(controllers.session.binding.current.list_subtitle_words(document.id)) == 6000
        assert controllers.subtitles.selectedDocumentId == ""
        assert controllers.subtitles.subtitleSegmentsModel.rowCount() == 0
        result_panel = workspace.findChild(QQuickItem, "transcriptResultPanel")
        assert _process_until(lambda: result_panel is not None and result_panel.isVisible())
        assert workspace.findChild(QQuickItem, "transcriptWordEditor") is None
        assert workspace.findChild(QQuickItem, "transcriptWordSegmentList") is None
        assert workspace.findChild(QQuickItem, "rippleDeleteTranscriptWordsButton") is None
        assert not any(
            item.objectName().startswith("transcriptWordItem_") for item in _visual_items(workspace)
        )

        preview = workspace.findChild(QQuickItem, "previewPlayer")
        assert preview is not None
        assert _process_until(
            lambda: preview.property("duration") > 0 and not preview.property("errorString"),
            timeout=30,
        )
        for _ in range(200):
            QCoreApplication.processEvents()
            time.sleep(0.01)
        assert controllers.workspace.hasProject is True
        assert preview.property("errorString") == ""
        rendered = window.grabWindow()
        screenshot = tmp_path / "large-transcription-without-word-editor.png"
        assert not rendered.isNull() and rendered.save(str(screenshot))
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def test_qml_title_bar_uses_the_mediaflow_pro_product_name(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    app = QGuiApplication.instance() or QGuiApplication([])
    QCoreApplication.setApplicationName(PRODUCT_NAME)
    configure_application_font(app)
    engine, controllers = create_engine(app)
    try:
        root = engine.rootObjects()[0]
        product_name = root.findChild(QQuickItem, "applicationProductName")

        assert product_name is not None
        assert product_name.property("text") == "MediaFlow Pro"
    finally:
        controllers.workspace.shutdown()
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
    QCoreApplication.setApplicationName(PRODUCT_NAME)
    configure_application_font(app)
    engine, controllers = create_engine(app)
    try:
        controllers.workspace.createProject(QUrl.fromLocalFile(str(tmp_path)).toString(), "UI Test")
        assert controllers.workspace.hasProject is True
        assert controllers.workspace.sequencesModel.rowCount() == 1
        assert controllers.timeline.tracksModel.rowCount() == 0

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
        controllers.media.importFiles(
            [QUrl.fromLocalFile(str(source))]
        )
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
        controllers.timeline.dropAssets([asset_id], "", -1, 0, 3.0, 0, True, False)
        QCoreApplication.processEvents()

        assert controllers.timeline.clipsModel.rowCount() == 1
        assert controllers.timeline.selectedClipId == controllers.timeline.clipsModel.get(0)["clipId"]
        assert engine.rootObjects()[0].title() == "UI Test"

        assert _process_until(
            lambda: bool(controllers.workspace.previewGraphPath),
            timeout=30,
        )
        source_preview_graph = controllers.workspace.previewGraphPath
        controllers.workspace.reportPreviewDroppedFrames(
            controllers.session.settings.preview.dropped_frame_proxy_threshold
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
        project = controllers.session.binding.current
        assert project is not None
        proxied_asset = project.get_asset(asset_id)
        assert proxied_asset.proxy_path
        proxy_path = Path(proxied_asset.proxy_path)
        if not proxy_path.is_absolute():
            proxy_path = project.project_dir / proxy_path
        proxy_path = proxy_path.resolve()
        assert proxy_path.is_file()
        assert _process_until(
            lambda: bool(controllers.workspace.previewGraphPath)
            and controllers.workspace.previewGraphPath != source_preview_graph,
            timeout=30,
        )
        proxy_preview_graph = Path(controllers.workspace.previewGraphPath)
        assert proxy_preview_graph.is_file()
        assert proxy_path.name in proxy_preview_graph.read_text(
            encoding="utf-8",
        )
        preview_player = engine.rootObjects()[0].findChild(
            QQuickItem,
            "previewPlayer",
        )
        assert preview_player is not None
        assert _process_until(
            lambda: preview_player.property("duration") > 0
            and not preview_player.property("errorString"),
            timeout=30,
        )
        proxy_start_position = int(preview_player.property("position"))
        preview_player.play()
        assert _process_until(
            lambda: bool(preview_player.property("playing"))
            and int(preview_player.property("position")) > proxy_start_position + 3,
            timeout=5,
        )
        preview_player.pause()
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
        analyzed_clip_after = controllers.timeline.clipsModel.get(0)
        assert {key: value for key, value in analyzed_clip_after.items() if key != "waveformReady"} == {
            key: value for key, value in analyzed_clip_before.items() if key != "waveformReady"
        }

        controllers.timeline.setSequenceInOut(1, analyzed_clip_before["endFrame"] - 1)
        assert (controllers.workspace.sequenceInFrame, controllers.workspace.sequenceOutFrame) == (
            1,
            analyzed_clip_before["endFrame"] - 1,
        )
        QCoreApplication.processEvents()
        sequence_layer = engine.rootObjects()[0].findChild(QQuickItem, "sequenceInOutLayer")
        assert sequence_layer is not None and sequence_layer.isVisible()
        timeline_more_button = engine.rootObjects()[0].findChild(QQuickItem, "timelineMoreButton")
        smart_bounds_button = engine.rootObjects()[0].findChild(QQuickItem, "smartSequenceBoundsButton")
        assert timeline_more_button is not None and smart_bounds_button is not None
        assert QMetaObject.invokeMethod(timeline_more_button, "click")
        assert _process_until(smart_bounds_button.isVisible)
        QTest.keyClick(root_window, Qt.Key_Escape)
        assert _process_until(lambda: not smart_bounds_button.isVisible())
        preview_slider = engine.rootObjects()[0].findChild(QQuickItem, "previewPositionSlider")
        assert preview_slider is not None
        assert (preview_slider.property("from"), preview_slider.property("to")) == (
            0.0,
            float(analyzed_clip_before["endFrame"] - 1),
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
        controllers.timeline.duplicateClip(first_clip["clipId"], 3.0, first_clip["endFrame"])
        assert controllers.timeline.clipsModel.rowCount() == 2
        second_clip_id = controllers.timeline.clipsModel.get(1)["clipId"]
        controllers.timeline.addTransitionAfter(first_clip["clipId"], "dissolve", 8)
        assert controllers.timeline.transitionsModel.rowCount() == 1
        transition_id = controllers.timeline.transitionsModel.get(0)["transitionId"]
        controllers.timeline.selectTransition(transition_id)
        workspace = engine.rootObjects()[0].findChild(QQuickItem, "workspace")
        assert workspace is not None
        QCoreApplication.processEvents()
        transition_kind = engine.rootObjects()[0].findChild(
            QQuickItem, "selectedTransitionKind")
        transition_duration = engine.rootObjects()[0].findChild(
            QQuickItem, "selectedTransitionDuration")
        assert transition_kind is not None and transition_duration is not None
        assert _process_until(
            lambda: transition_kind.property("currentValue") == "dissolve"
            and transition_duration.property("value") == 8
        )
        controllers.timeline.updateTransition(transition_id, "fade", 6)
        assert controllers.timeline.transitionsModel.get(0)["durationFrames"] == 6
        assert _process_until(
            lambda: transition_kind.property("currentValue") == "fade"
            and transition_duration.property("value") == 6
        )
        controllers.timeline.clearSelection()
        multi_select_button = engine.rootObjects()[0].findChild(QQuickItem, "timelineMultiSelectButton")
        assert multi_select_button is not None
        assert QMetaObject.invokeMethod(multi_select_button, "click")
        assert multi_select_button.property("checked") is True
        visible_clips = sorted(
            (
                item
                for item in _visual_items(engine.rootObjects()[0].contentItem())
                if item.objectName() == "timelineClip" and item.isVisible()
            ),
            key=lambda item: item.x(),
        )
        assert len(visible_clips) == 2
        for clip_item in visible_clips:
            click_point = clip_item.mapToScene(QPointF(clip_item.width() / 2, clip_item.height() / 2))
            QTest.mouseClick(
                root_window,
                Qt.LeftButton,
                Qt.NoModifier,
                QPoint(round(click_point.x()), round(click_point.y())),
            )
        assert controllers.timeline.selectedClipIds == [
            first_clip["clipId"],
            second_clip_id,
        ]
        assert controllers.timeline.canCreateCompoundClip is True
        controllers.timeline.createCompoundClip()
        assert controllers.timeline.compoundClipsModel.rowCount() == 1
        compound_id = controllers.timeline.compoundClipsModel.get(0)["compoundId"]
        assert controllers.timeline.selectedCompoundId == compound_id
        compound_item = None
        compound_repeater = engine.rootObjects()[0].findChild(QQuickItem, "compoundClipRepeater")
        assert compound_repeater is not None
        compound_layer = engine.rootObjects()[0].findChild(QQuickItem, "compoundClipLayer")
        assert compound_layer is not None
        assert _process_until(
            lambda: any(item.objectName() == "timelineCompoundClip" for item in _visual_items(compound_layer))
        ), compound_repeater.property("count")
        compound_item = next(
            item for item in _visual_items(compound_layer) if item.objectName() == "timelineCompoundClip"
        )
        assert compound_item is not None and compound_item.isVisible()
        transition_layer = engine.rootObjects()[0].findChild(QQuickItem, "transitionLayer")
        assert transition_layer is not None
        transition_item = next(
            item for item in _visual_items(transition_layer) if item.objectName() == "timelineTransition"
        )
        assert transition_item is not None and not transition_item.isVisible()
        compound_render = engine.rootObjects()[0].grabWindow()
        assert not compound_render.isNull()
        assert compound_render.save(str(tmp_path / "compound-clip-selected.png"))
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
        assert controllers.timeline.compoundClipsModel.rowCount() == 1
        controllers.timeline.selectCompoundClip(compound_id)
        controllers.timeline.dissolveSelectedCompoundClip()
        assert controllers.timeline.compoundClipsModel.rowCount() == 0
        QCoreApplication.processEvents()
        transition_item = next(
            item for item in _visual_items(transition_layer) if item.objectName() == "timelineTransition"
        )
        assert transition_item is not None and transition_item.isVisible()
        transition_render = engine.rootObjects()[0].grabWindow()
        assert not transition_render.isNull()
        assert transition_render.save(str(tmp_path / "transition-crossfade-marker.png"))
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
                engine.rootObjects()[0].findChild(QQuickItem, "createShortSequenceButton") is not None
                and engine.rootObjects()[0].findChild(QQuickItem, "createShortSequenceButton").isVisible()
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
                engine.rootObjects()[0].findChild(QQuickItem, "archiveActiveSequenceButton") is not None
                and engine.rootObjects()[0].findChild(QQuickItem, "archiveActiveSequenceButton").isVisible()
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

        highlight = controllers.session.binding.current.add_manual_highlight(
            asset_id,
            start_frame=2,
            end_frame=10,
            title="UI 高光",
        )
        controllers.session.projectors.highlights.refresh_highlights()
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
        product_name = root.findChild(QQuickItem, "applicationProductName")
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
        assert product_name is not None
        assert product_name.property("text") == "MediaFlow Pro"
        # The offscreen Qt platform has no window manager. Repeated native
        # minimize/maximize/fullscreen transitions can destroy its backing
        # surface on Windows, so this chain verifies the controls and exercises
        # workspace resizing without issuing unsupported native transitions.
        versions_button = root.findChild(
            QQuickItem, "openProjectVersionsButton")
        versions_dialog = root.findChild(QObject, "projectVersionsDialog")
        assert versions_button is not None and versions_dialog is not None
        selected_clip_count = controllers.timeline.clipsModel.rowCount()
        assert QMetaObject.invokeMethod(versions_button, "click")
        assert _process_until(
            lambda: root.property("projectVersionsVisible") is True)
        QTest.keyClick(root_window, Qt.Key_Delete)
        QCoreApplication.processEvents()
        assert controllers.timeline.clipsModel.rowCount() == selected_clip_count
        assert QMetaObject.invokeMethod(versions_dialog, "close")
        assert _process_until(
            lambda: root.property("projectVersionsVisible") is False)
        page_loader = root.findChild(QQuickItem, "pageLoader")
        assert page_loader is not None
        workspace = page_loader.property("item")
        assert workspace is not None
        root.setWidth(1280)
        root.setHeight(720)
        for _ in range(12):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        navigation = workspace.findChild(QQuickItem, "workspaceNavigation")
        tool_panel = workspace.findChild(QQuickItem, "toolPanelContainer")
        preview_viewport = workspace.findChild(QQuickItem, "previewViewport")
        preview_controls_scroll = workspace.findChild(
            QQuickItem, "previewControlsScroll")
        timeline_panel = workspace.findChild(QQuickItem, "timelinePanel")
        inspector_panel = workspace.findChild(QQuickItem, "inspectorPanel")
        assert inspector_panel is not None and inspector_panel.isVisible()
        assert workspace.findChild(QQuickItem, "inspectorContainer") is None
        assert workspace.findChild(QQuickItem, "compactInspectorDrawer") is None
        assert workspace.findChild(QQuickItem, "compactInspectorButton") is None
        assert navigation is not None
        assert preview_controls_scroll is not None
        assert float(preview_controls_scroll.property("contentWidth")) >= (
            preview_controls_scroll.width()
        )
        if float(preview_controls_scroll.property("contentWidth")) > (
            preview_controls_scroll.width()
        ):
            assert preview_controls_scroll.property("interactive") is True
        assert abs(navigation.width() - tool_panel.width()) <= 2
        assert 66 <= navigation.height() <= 70
        assert tool_panel is not None and preview_viewport is not None and timeline_panel is not None
        workspace_gutter = float(workspace.property("workspaceGutter"))
        tool_panel_position = tool_panel.mapToItem(workspace, QPointF(0, 0))
        assert abs(tool_panel_position.x() - workspace_gutter) <= 2
        assert abs(tool_panel_position.y() - workspace_gutter) <= 2
        navigation_items = {item.objectName(): item for item in _visual_items(navigation)}
        modes = WORKSPACE_NAVIGATION_MODE_KEYS
        for mode in modes:
            navigation_item = navigation_items.get(f"navigationItem_{mode}")
            assert navigation_item is not None and navigation_item.isVisible()
        navigation_positions = [
            navigation_items[f"navigationItem_{mode}"].mapToItem(navigation, QPointF(0, 0)) for mode in modes
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

        def geometry(item: QQuickItem) -> tuple[float, float, float, float]:
            origin = item.mapToItem(workspace, QPointF(0, 0))
            return origin.x(), origin.y(), item.width(), item.height()

        fixed_geometry = {
            "tool": geometry(tool_panel),
            "preview": geometry(preview_viewport),
            "inspector": geometry(inspector_panel),
            "timeline": geometry(timeline_panel),
        }
        panel_names = {
            mode.key: mode.panel_object_name
            for mode in WORKSPACE_MODES
        }
        for mode in modes:
            assert QMetaObject.invokeMethod(navigation_items[f"navigationItem_{mode}"], "click")
            assert _process_until(lambda mode=mode: workspace.property("activeMode") == mode)
            active_panel = workspace.findChild(QQuickItem, panel_names[mode])
            assert active_panel is not None and active_panel.isVisible()
            if mode == "media":
                media_toolbar = workspace.findChild(QQuickItem, "mediaToolbar")
                media_search = workspace.findChild(QQuickItem, "mediaSearchField")
                view_mode = workspace.findChild(QQuickItem, "mediaViewModeButton")
                import_button = workspace.findChild(QQuickItem, "openMediaImportButton")
                assert media_toolbar is not None
                assert all(
                    item is not None and item.parentItem() == media_toolbar
                    for item in (media_search, view_mode, import_button)
                )
                drag_hint = workspace.findChild(QQuickItem, "mediaDragHint")
                drag_preview = workspace.findChild(QQuickItem, "mediaDragPreview")
                assert drag_hint is not None and drag_hint.isVisible()
                assert "重复拖入" in drag_hint.property("text")
                assert drag_preview is not None
                for removed_action in (
                    "selectedMediaActions",
                    "mediaGenerateProxyButton",
                    "mediaGenerateWaveformButton",
                    "mediaAddSelectedToTimelineButton",
                ):
                    assert workspace.findChild(QQuickItem, removed_action) is None
                media_task_panel = workspace.findChild(QQuickItem, "mediaTaskPanel")
                assert media_task_panel is not None
                if media_task_panel.isVisible():
                    assert controllers.tasks.latestMediaTask(
                        controllers.media.selectedAssetId
                    ).get("status") != "completed"
            elif mode == "highlight":
                highlight_toolbar = workspace.findChild(QQuickItem, "highlightToolbar")
                source_document = workspace.findChild(QQuickItem, "highlightSourceDocument")
                analyze_button = workspace.findChild(QQuickItem, "analyzeHighlightsButton")
                assert highlight_toolbar is not None
                assert all(
                    item is not None and item.parentItem() == highlight_toolbar
                    for item in (source_document, analyze_button)
                )
            elif mode == "transcript":
                transcript_tabs = workspace.findChild(QQuickItem, "transcriptWorkspaceTabs")
                transcribe_button = workspace.findChild(QQuickItem, "transcribeTimelineButton")
                subtitle_segments = workspace.findChild(QQuickItem, "subtitleSegmentList")
                subtitle_tab = workspace.findChild(QQuickItem, "transcriptSection_subtitle")
                translation_tab = workspace.findChild(QQuickItem, "transcriptSection_translate")
                glossary_tab = workspace.findChild(QQuickItem, "transcriptSection_glossary")
                assert transcript_tabs is not None
                assert transcribe_button is not None and transcribe_button.isVisible()
                assert transcribe_button.property("enabled") is True
                assert transcribe_button.property("text") == "转录当前时间轴"
                assert subtitle_segments is not None and not subtitle_segments.isVisible()
                for removed_parameter in (
                    "transcriptConfigPanel",
                    "transcriptModelSelector",
                    "transcriptUseSequenceRangeButton",
                    "transcriptRegionStart",
                    "transcriptRegionEnd",
                ):
                    assert workspace.findChild(QQuickItem, removed_parameter) is None
                assert all(item is not None for item in (subtitle_tab, translation_tab, glossary_tab))
                for tab, expected_index, expected_item in (
                    (subtitle_tab, 1, "subtitleStartTranscriptionButton"),
                    (translation_tab, 2, "translationImportFileButton"),
                    (glossary_tab, 3, "translationGlossaryList"),
                ):
                    assert QMetaObject.invokeMethod(tab, "click")
                    assert _process_until(
                        lambda expected_index=expected_index, tabs=transcript_tabs: tabs.property(
                            "currentIndex"
                        )
                        == expected_index
                    )
                    section_item = workspace.findChild(QQuickItem, expected_item)
                    assert section_item is not None and section_item.isVisible()
                transcript_tabs.setProperty("currentIndex", 0)
            elif mode == "tasks":
                activity_summary = workspace.findChild(QQuickItem, "taskActivitySummary")
                pause_active = workspace.findChild(QQuickItem, "pauseActiveTasksButton")
                assert activity_summary is not None
                assert activity_summary.property("text") == "进行中 0"
                assert pause_active is not None and not pause_active.property("enabled")
                internal_result_buttons = [
                    item
                    for item in _visual_items(active_panel)
                    if item.objectName() == "taskOpenResultButton"
                    and item.property("taskCommandType") in {"generate_proxy", "generate_waveform"}
                ]
                assert all(not button.isVisible() for button in internal_result_buttons)
            assert preview_viewport.isVisible() and timeline_panel.isVisible()
            for key, item in (
                ("tool", tool_panel),
                ("preview", preview_viewport),
                ("inspector", inspector_panel),
                ("timeline", timeline_panel),
            ):
                assert all(
                    abs(actual - expected) <= 2
                    for actual, expected in zip(geometry(item), fixed_geometry[key], strict=True)
                )

        root.setWidth(1440)
        root.setHeight(900)
        for _ in range(12):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        preview = workspace.findChild(QQuickItem, "previewPlayer")
        overlay = workspace.findChild(QQuickItem, "previewTransformOverlay")
        assert preview is not None and overlay is not None
        preview.setProperty("volume", 0.35)
        assert abs(float(preview.property("volume")) - 0.35) < 0.001
        preview_viewport.seek(0)
        assert _process_until(lambda: preview.property("position") == 0)
        assert overlay.isVisible()
        overlay.setProperty("draftX", 12.5)
        overlay.setProperty("draftY", 7.5)
        assert QMetaObject.invokeMethod(overlay, "commit")
        QCoreApplication.processEvents()
        assert controllers.timeline.selectedClipData["x"] == 12.5
        assert controllers.timeline.selectedClipData["y"] == 7.5

        edit_pos_x = workspace.findChild(QQuickItem, "editClipPosX")
        edit_pos_y = workspace.findChild(QQuickItem, "editClipPosY")
        apply_transform = workspace.findChild(QQuickItem, "applyClipTransformButton")
        assert edit_pos_x is not None and edit_pos_y is not None
        assert apply_transform is not None and apply_transform.isVisible()
        edit_pos_x.setProperty("text", "18.5")
        edit_pos_y.setProperty("text", "9.5")
        assert QMetaObject.invokeMethod(apply_transform, "click")
        QCoreApplication.processEvents()
        assert controllers.timeline.selectedClipData["x"] == 18.5
        assert controllers.timeline.selectedClipData["y"] == 9.5

        assert QMetaObject.invokeMethod(navigation_items["navigationItem_audio"], "click")
        clip_gain = workspace.findChild(QQuickItem, "audioClipGain")
        clip_pan = workspace.findChild(QQuickItem, "audioClipPan")
        apply_audio = workspace.findChild(QQuickItem, "applyClipAudioButton")
        assert clip_gain is not None and clip_pan is not None
        assert apply_audio is not None and apply_audio.isVisible()
        clip_gain.setProperty("text", "-3.5")
        clip_pan.setProperty("text", "0.2")
        assert QMetaObject.invokeMethod(apply_audio, "click")
        QCoreApplication.processEvents()
        assert controllers.timeline.selectedClipData["gainDb"] == -3.5
        assert controllers.timeline.selectedClipData["pan"] == 0.2

        preview_viewport.setProperty("viewportZoom", 2.0)
        preview_viewport.setProperty("viewportPanX", 30.0)
        assert QMetaObject.invokeMethod(workspace, "resetPreviewViewport")
        assert preview_viewport.property("viewportZoom") == 1.0
        assert preview_viewport.property("viewportPanX") == 0.0
        controllers.settings.saveWorkspaceLayout(
            "standard", 400, 360, 380, True, True, True
        )
        controllers.settings.saveWindowState(1440, 900, False)
        persisted_ui = SettingsRepository(os.environ["MEDIAFLOW_SETTINGS_PATH"]).load().ui
        assert (
            persisted_ui.workspace_layouts.standard.left_panel_width,
            persisted_ui.workspace_layouts.standard.inspector_panel_width,
            persisted_ui.workspace_layouts.standard.timeline_height,
        ) == (400, 360, 380)
        assert (persisted_ui.window_width, persisted_ui.window_height) == (1440, 900)
        assert persisted_ui.window_maximized is False
        workflow_mode = workspace.findChild(QQuickItem, "workflowMode")
        workflow_banner = workspace.findChild(QQuickItem, "workflowBanner")
        workflow_continue = workspace.findChild(QQuickItem, "workflowContinue")
        workflow_skip = workspace.findChild(QQuickItem, "workflowSkip")
        workflow_cancel = workspace.findChild(QQuickItem, "workflowCancel")
        assert workflow_banner is not None and workflow_banner.isVisible()
        assert workflow_continue is not None and workflow_continue.isVisible()
        assert workflow_skip is not None and workflow_cancel is not None
        assert workflow_mode is None
        workflow_run_id = controllers.workspace.workflowRunId
        assert workflow_run_id
        assert QMetaObject.invokeMethod(workflow_continue, "click")
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
        assert workflow_continue.isVisible()
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
        assert workflow_skip.isVisible()
        assert QMetaObject.invokeMethod(workflow_skip, "click")
        assert _process_until(
            lambda: controllers.workspace.workflowStage == "translate"
            and controllers.workspace.workflowStatus == "awaiting_confirmation"
        )
        timeline = workspace.findChild(QQuickItem, "timelinePanel")
        timeline_toolbar = workspace.findChild(QQuickItem, "timelineToolbarScroll")
        assert timeline is not None
        timeline_scroll = timeline.findChild(QQuickItem, "timelineScroll")
        blank_selection_area = timeline.findChild(QQuickItem, "timelineBlankSelectionArea")
        track_controls_panel = workspace.findChild(QQuickItem, "trackControlsPanel")
        assert timeline_toolbar is not None
        compact_icon_buttons = [
            root_window.findChild(QQuickItem, object_name)
            for object_name in (
                "workspaceUndoButton",
                "workspaceRedoButton",
                "openProjectVersionsButton",
                "timelineSplitButton",
                "timelineDuplicateButton",
                "timelineDeleteButton",
            )
        ]
        assert all(button is not None for button in compact_icon_buttons)
        assert all(int(button.property("iconSize")) <= 16 for button in compact_icon_buttons)
        assert all(float(button.implicitWidth()) <= 32 for button in compact_icon_buttons)
        timeline_delete_button = compact_icon_buttons[-1]
        assert timeline_delete_button.property("danger") is True
        assert timeline_delete_button.property("backgroundColor") == QColor("#00000000")
        assert timeline_scroll is not None
        assert blank_selection_area is not None
        assert track_controls_panel is not None
        assert _process_until(track_controls_panel.isVisible), {
            "trackCount": controllers.timeline.tracksModel.rowCount(),
            "clipCount": controllers.timeline.clipsModel.rowCount(),
            "timelineVisible": timeline.isVisible(),
            "activeMode": workspace.property("activeMode"),
            "activeSequenceId": controllers.workspace.activeSequenceId,
        }
        visible_track_headers = [
            item
            for item in _visual_items(track_controls_panel)
            if item.objectName() == "trackControlsOverlay" and item.isVisible()
        ]
        assert len(visible_track_headers) == controllers.timeline.tracksModel.rowCount()
        dialogue_track = next(
            controllers.timeline.tracksModel.get(index)
            for index in range(controllers.timeline.tracksModel.rowCount())
            if controllers.timeline.tracksModel.get(index)["kind"] == "audio"
        )
        assert dialogue_track["primaryDialogue"] is True
        dialogue_button = next(
            (
                item
                for item in _visual_items(track_controls_panel)
                if item.objectName() == "primaryDialogueButton" and item.isVisible()
            ),
            None,
        )
        assert dialogue_button is not None
        assert dialogue_button.isVisible()
        assert dialogue_button.property("checked") is True
        assert dialogue_button.property("text") == "转录"
        dialogue_button_background = dialogue_button.findChild(QQuickItem, "primaryDialogueButtonBackground")
        dialogue_microphone = dialogue_button.findChild(QQuickItem, "primaryDialogueMicrophone")
        dialogue_marker = next(
            (
                item
                for item in _visual_items(track_controls_panel)
                if item.objectName() == "primaryDialogueTrackMarker" and item.isVisible()
            ),
            None,
        )
        assert dialogue_button_background is not None
        assert dialogue_microphone is not None
        assert dialogue_marker is not None and dialogue_marker.width() == 3
        assert dialogue_button_background.property("color").name() == "#17383c"
        assert dialogue_microphone.property("iconColor").name() == "#20c7d4"
        timeline_origin = timeline.mapToItem(workspace, QPointF(0, 0))
        tool_panel_bottom = tool_panel.mapToItem(workspace, QPointF(0, tool_panel.height()))
        assert abs(timeline_origin.x() - workspace_gutter) <= 2
        assert abs(timeline.width() - workspace.width() + 2 * workspace_gutter) <= 2
        assert abs(timeline_origin.y() - tool_panel_bottom.y() - workspace_gutter) <= 2
        first_clip_projection = controllers.timeline.clipsModel.get(0)
        assert first_clip_projection["assetKind"] == "video"
        assert first_clip_projection["trackKind"] == "video"
        assert first_clip_projection["mediaKind"] == "linked_av"
        assert "allowedTrackKinds" not in first_clip_projection
        assert first_clip_projection["hasAudio"] is True
        assert first_clip_projection["audioTrackPosition"] == 1
        if timeline_toolbar.property("contentWidth") > timeline_toolbar.width():
            expected_content_x = timeline_toolbar.property("contentWidth") - timeline_toolbar.width()
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
        QCoreApplication.processEvents()
        assert preview_player.property("playing") is False
        playback_start = max(0, sequence_last_frame - 12)
        timeline.seekToFrame(playback_start)
        assert _process_until(lambda: preview_player.property("position") == playback_start)
        assert QMetaObject.invokeMethod(workspace, "playPreview")
        assert _process_until(lambda: preview_player.property("playing") is True)
        scrub_target = max(
            controllers.workspace.sequenceInFrame,
            min(sequence_last_frame - 2, controllers.workspace.sequenceInFrame + 8),
        )
        timeline.beginPlayheadScrub(scrub_target)
        assert _process_until(lambda: preview_player.property("playing") is False)
        assert preview_viewport.property("resumeAfterScrub") is True
        for frame in range(scrub_target, min(sequence_last_frame, scrub_target + 6)):
            timeline.updatePlayheadScrub(frame)
        final_scrub_frame = int(timeline.property("interactivePlayheadFrame"))
        assert preview_viewport.property("scrubFrame") == final_scrub_frame
        timeline.finishPlayheadScrub()
        assert _process_until(
            lambda: preview_player.property("playing") is True
            and preview_player.property("position") >= final_scrub_frame
        ), {
            "position": preview_player.property("position"),
            "playing": preview_player.property("playing"),
            "resume": preview_viewport.property("resumeAfterScrub"),
            "rangeStart": preview_viewport.property("playbackRangeStart"),
            "rangeEnd": preview_viewport.property("playbackRangeEnd"),
            "error": preview_player.property("errorString"),
        }
        preview_player.pause()
        waveforms = [item for item in _visual_items(timeline) if item.objectName() == "clipWaveform"]
        video_clips = [item for item in _visual_items(timeline) if item.objectName() == "timelineClip"]
        embedded_audio = [
            item
            for item in _visual_items(timeline)
            if item.objectName() == "embeddedAudioClip" and item.isVisible()
        ]
        assert waveforms
        assert video_clips and embedded_audio
        assert embedded_audio[0].y() > video_clips[0].y()
        assert abs(embedded_audio[0].x() - video_clips[0].x()) <= 1
        existing_clip_ids = {
            controllers.timeline.clipsModel.get(index)["clipId"]
            for index in range(controllers.timeline.clipsModel.rowCount())
        }
        new_clip_start = controllers.workspace.timelineDurationFrames
        controllers.timeline.dropAssets(
            [asset_id],
            "",
            controllers.timeline.tracksModel.rowCount(),
            new_clip_start,
            50.0,
            0,
            False,
            True,
        )
        assert _process_until(lambda: controllers.timeline.clipsModel.rowCount() == 3)
        new_clip_projection = next(
            controllers.timeline.clipsModel.get(index)
            for index in range(controllers.timeline.clipsModel.rowCount())
            if controllers.timeline.clipsModel.get(index)["clipId"] not in existing_clip_ids
        )
        assert new_clip_projection["trackKind"] == "video"
        assert new_clip_projection["trackPosition"] == 2
        assert controllers.timeline.tracksModel.rowCount() == 4
        assert [
            controllers.timeline.tracksModel.get(index)["kind"]
            for index in range(controllers.timeline.tracksModel.rowCount())
        ] == ["video", "audio", "video", "audio"]

        new_video_item = next(
            item
            for item in _visual_items(timeline)
            if item.objectName() == "timelineClip"
            and item.property("clipId") == new_clip_projection["clipId"]
        )
        new_video_press_position = QPointF(
            min(24.0, new_video_item.width() / 2),
            new_video_item.height() / 2,
        )
        timeline_scroll.setProperty(
            "contentX",
            max(
                0.0,
                new_video_item.x() + new_video_press_position.x() - 40.0,
            ),
        )
        QCoreApplication.processEvents()
        new_video_content_position = new_video_item.mapToItem(
            blank_selection_area,
            new_video_press_position,
        )
        _drag_quick_item(
            root_window,
            new_video_item,
            blank_selection_area,
            QPointF(
                new_video_content_position.x(),
                12 + new_video_press_position.y(),
            ),
            source_position=new_video_press_position,
        )
        assert _process_until(
            lambda: next(
                controllers.timeline.clipsModel.get(index)
                for index in range(controllers.timeline.clipsModel.rowCount())
                if controllers.timeline.clipsModel.get(index)["clipId"] == new_clip_projection["clipId"]
            )["trackPosition"]
            == 0
        )
        moved_linked_clip = next(
            controllers.timeline.clipsModel.get(index)
            for index in range(controllers.timeline.clipsModel.rowCount())
            if controllers.timeline.clipsModel.get(index)["clipId"] == new_clip_projection["clipId"]
        )
        assert moved_linked_clip["audioTrackPosition"] == 1

        moved_linked_audio_item = next(
            item
            for item in _visual_items(timeline)
            if item.objectName() == "embeddedAudioClip"
            and item.property("clipId") == new_clip_projection["clipId"]
        )
        linked_audio_press_position = QPointF(
            min(24.0, moved_linked_audio_item.width() / 2),
            moved_linked_audio_item.height() / 2,
        )
        linked_audio_content_position = moved_linked_audio_item.mapToItem(
            blank_selection_area,
            linked_audio_press_position,
        )
        _drag_quick_item(
            root_window,
            moved_linked_audio_item,
            blank_selection_area,
            QPointF(
                linked_audio_content_position.x(),
                3 * float(timeline.property("trackPitch")) + 10 + linked_audio_press_position.y(),
            ),
            source_position=linked_audio_press_position,
        )
        assert _process_until(
            lambda: next(
                controllers.timeline.clipsModel.get(index)
                for index in range(controllers.timeline.clipsModel.rowCount())
                if controllers.timeline.clipsModel.get(index)["clipId"] == new_clip_projection["clipId"]
            )["trackPosition"]
            == 2
        ), {
            "clip": next(
                controllers.timeline.clipsModel.get(index)
                for index in range(controllers.timeline.clipsModel.rowCount())
                if controllers.timeline.clipsModel.get(index)["clipId"] == new_clip_projection["clipId"]
            ),
            "draggingClipId": timeline.property("draggingClipId"),
            "draggingTrackPosition": timeline.property("draggingClipTrackPosition"),
            "draggingAudioTrackPosition": timeline.property("draggingClipAudioTrackPosition"),
            "selectedClipIds": controllers.timeline.selectedClipIds,
        }
        moved_linked_clip = next(
            controllers.timeline.clipsModel.get(index)
            for index in range(controllers.timeline.clipsModel.rowCount())
            if controllers.timeline.clipsModel.get(index)["clipId"] == new_clip_projection["clipId"]
        )
        assert moved_linked_clip["audioTrackPosition"] == 3
        timeline_scroll.setProperty("contentX", 0)
        QCoreApplication.processEvents()

        controllers.timeline.selectClip(first_clip_projection["clipId"])
        assert timeline.property("multiSelectMode") is True
        detach_audio = workspace.findChild(QQuickItem, "detachClipAudioButton")
        assert detach_audio is not None and _process_until(detach_audio.isVisible)
        assert QMetaObject.invokeMethod(detach_audio, "click")
        detached_video = next(
            controllers.timeline.clipsModel.get(index)
            for index in range(controllers.timeline.clipsModel.rowCount())
            if controllers.timeline.clipsModel.get(index)["clipId"] == first_clip_projection["clipId"]
        )
        detached_audio = next(
            controllers.timeline.clipsModel.get(index)
            for index in range(controllers.timeline.clipsModel.rowCount())
            if controllers.timeline.clipsModel.get(index)["mediaKind"] == "audio_only"
        )
        assert detached_video["mediaKind"] == "video_only"
        assert detached_audio["trackKind"] == "audio"
        assert detached_audio["assetId"] == detached_video["assetId"]
        assert timeline.property("multiSelectMode") is False
        assert controllers.timeline.selectedClipIds == [detached_video["clipId"]]

        detached_audio_start = detached_audio["startFrame"]
        detached_video_item = next(
            item
            for item in _visual_items(timeline)
            if item.objectName() == "timelineClip" and item.property("clipId") == detached_video["clipId"]
        )
        video_press_position = QPointF(
            min(24.0, detached_video_item.width() / 2),
            detached_video_item.height() / 2,
        )
        video_content_position = detached_video_item.mapToItem(
            blank_selection_area,
            video_press_position,
        )
        _drag_quick_item(
            root_window,
            detached_video_item,
            blank_selection_area,
            QPointF(
                video_content_position.x(),
                2 * float(timeline.property("trackPitch")) + 12 + video_press_position.y(),
            ),
            source_position=video_press_position,
        )
        assert _process_until(
            lambda: next(
                controllers.timeline.clipsModel.get(index)
                for index in range(controllers.timeline.clipsModel.rowCount())
                if controllers.timeline.clipsModel.get(index)["clipId"] == detached_video["clipId"]
            )["trackPosition"]
            == 2
        )
        assert (
            next(
                controllers.timeline.clipsModel.get(index)
                for index in range(controllers.timeline.clipsModel.rowCount())
                if controllers.timeline.clipsModel.get(index)["clipId"] == detached_audio["clipId"]
            )["startFrame"]
            == detached_audio_start
        )

        detached_audio_item = next(
            item
            for item in _visual_items(timeline)
            if item.objectName() == "timelineClip" and item.property("clipId") == detached_audio["clipId"]
        )
        audio_press_position = QPointF(
            min(24.0, detached_audio_item.width() / 2),
            detached_audio_item.height() / 2,
        )
        audio_content_position = detached_audio_item.mapToItem(
            blank_selection_area,
            audio_press_position,
        )
        _drag_quick_item(
            root_window,
            detached_audio_item,
            blank_selection_area,
            QPointF(
                audio_content_position.x(),
                3 * float(timeline.property("trackPitch")) + 12 + audio_press_position.y(),
            ),
            source_position=audio_press_position,
        )
        assert _process_until(
            lambda: next(
                controllers.timeline.clipsModel.get(index)
                for index in range(controllers.timeline.clipsModel.rowCount())
                if controllers.timeline.clipsModel.get(index)["clipId"] == detached_audio["clipId"]
            )["trackPosition"]
            == 3
        )
        assert (
            next(
                controllers.timeline.clipsModel.get(index)
                for index in range(controllers.timeline.clipsModel.rowCount())
                if controllers.timeline.clipsModel.get(index)["clipId"] == detached_video["clipId"]
            )["trackPosition"]
            == 2
        )

        timeline.setProperty("multiSelectMode", True)
        assert timeline.property("multiSelectMode") is True
        blank_scene = blank_selection_area.mapToScene(
            QPointF(
                10,
                float(timeline.property("trackPitch")) + float(timeline.property("trackHeight")) / 2,
            )
        )
        QTest.mouseClick(
            root_window,
            Qt.LeftButton,
            Qt.NoModifier,
            QPoint(round(blank_scene.x()), round(blank_scene.y())),
        )
        assert _process_until(lambda: controllers.timeline.selectedClipIds == [])
        assert timeline.property("multiSelectMode") is False

        controllers.timeline.undo()
        QTest.keyClick(root_window, Qt.Key_Escape)
        assert _process_until(lambda: controllers.timeline.selectedClipIds == [])

        independent_audio_start = controllers.workspace.timelineDurationFrames + 10
        controllers.timeline.moveClip(
            detached_audio["clipId"],
            independent_audio_start,
            detached_audio["trackId"],
            50.0,
            0,
            False,
        )
        moved_audio = next(
            controllers.timeline.clipsModel.get(index)
            for index in range(controllers.timeline.clipsModel.rowCount())
            if controllers.timeline.clipsModel.get(index)["clipId"] == detached_audio["clipId"]
        )
        assert moved_audio["startFrame"] == independent_audio_start
        assert detached_video["startFrame"] == first_clip_projection["startFrame"]
        controllers.timeline.selectClip(first_clip_projection["clipId"])
        assert any(item.parentItem().width() > item.width() for item in waveforms)
        assert all(item.width() <= timeline.width() for item in waveforms)

        subtitle_source = tmp_path / "ui-source.en.srt"
        subtitle_source.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nSubtitle over the audio waveform\n",
            encoding="utf-8",
        )
        controllers.media.selectAsset(asset_id)
        controllers.media.importFiles([QUrl.fromLocalFile(str(subtitle_source))])
        subtitle_documents = controllers.subtitles.subtitleDocumentsModel
        assert _process_until(lambda: subtitle_documents.rowCount() == 1, timeout=20)
        subtitle_document_id = subtitle_documents.get(0)["documentId"]

        translation_server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            _OpenAITranslationFixtureHandler,
        )
        translation_server_thread = threading.Thread(
            target=translation_server.serve_forever,
            daemon=True,
        )
        translation_server_thread.start()
        try:
            controllers.settings.saveLlmProvider(
                "",
                "QML translation fixture",
                f"http://127.0.0.1:{translation_server.server_address[1]}/v1",
                "fixture-key",
                "fixture-model",
                True,
            )
            controllers.subtitles.translateDocument(
                subtitle_document_id,
                "zh_CN",
                "standard",
            )
            assert _process_until(lambda: subtitle_documents.rowCount() == 2, timeout=20)
            translated_document_id = next(
                subtitle_documents.get(index)["documentId"]
                for index in range(subtitle_documents.rowCount())
                if subtitle_documents.get(index)["sourceDocumentId"] == subtitle_document_id
            )
            assert (
                controllers.session.binding.current.list_subtitle_segments(translated_document_id)[0].text
                == "叠加在音频波形上的字幕"
            )
        finally:
            translation_server.shutdown()
            translation_server.server_close()
            translation_server_thread.join(timeout=5)

        controllers.subtitles.placeSubtitleDocument(subtitle_document_id)
        assert _process_until(lambda: controllers.subtitles.subtitlePlacementsModel.rowCount() > 0)
        subtitle_projection = controllers.subtitles.subtitlePlacementsModel.get(0)
        assert subtitle_projection["clipId"]
        assert subtitle_projection["audioTrackPosition"] == 1
        controllers.subtitles.selectSubtitlePlacement(
            subtitle_projection["placementId"]
        )
        assert _process_until(
            lambda: controllers.subtitles.selectedSubtitlePlacementId
            == subtitle_projection["placementId"]
        )
        subtitle_overlays = [
            item
            for item in _visual_items(timeline)
            if item.objectName() == "subtitleWaveformOverlay" and item.isVisible()
        ]
        embedded_audio = [
            item
            for item in _visual_items(timeline)
            if item.objectName() == "embeddedAudioClip" and item.isVisible()
        ]
        assert subtitle_overlays
        assert embedded_audio
        assert embedded_audio[0].y() <= subtitle_overlays[0].y()
        assert (
            subtitle_overlays[0].y() + subtitle_overlays[0].height()
            <= embedded_audio[0].y() + embedded_audio[0].height()
        )
        subtitle_overlay = subtitle_overlays[0]
        subtitle_handles = {
            item.objectName() for item in _visual_items(subtitle_overlay) if item.objectName()
        }
        assert {"subtitleOverlayBody", "subtitleLeftTrimHandle", "subtitleRightTrimHandle"} <= (
            subtitle_handles
        )
        original_overlay_x = subtitle_overlay.x()
        original_overlay_width = subtitle_overlay.width()
        original_start = subtitle_projection["startFrame"]
        original_end = subtitle_projection["endFrame"]
        controllers.subtitles.moveSubtitlePlacement(
            subtitle_projection["placementId"],
            original_start + 5,
            50.0,
            0,
            False,
        )
        assert _process_until(
            lambda: controllers.subtitles.subtitlePlacementsModel.get(0)["startFrame"] == original_start + 5
        )
        moved_projection = controllers.subtitles.subtitlePlacementsModel.get(0)
        assert moved_projection["timingOverridden"] is True
        assert subtitle_overlay.x() > original_overlay_x
        controllers.subtitles.resizeSubtitlePlacement(
            moved_projection["placementId"],
            moved_projection["startFrame"],
            original_end + 15,
            50.0,
            0,
            False,
        )
        assert _process_until(
            lambda: controllers.subtitles.subtitlePlacementsModel.get(0)["endFrame"] == original_end + 15
        )
        assert subtitle_overlay.width() > original_overlay_width
        controllers.subtitles.followSubtitleAtFrame(original_start + 6)
        assert controllers.subtitles.selectedSubtitlePlacementId == moved_projection["placementId"]
        assert controllers.subtitles.selectedSubtitleSegmentId == subtitle_projection["segmentId"]
        persisted_placement = controllers.session.binding.current.get_subtitle_placement(
            moved_projection["placementId"]
        )
        assert persisted_placement.timing_overridden is True
        assert (persisted_placement.start_frame, persisted_placement.end_frame) == (
            original_start + 5,
            original_end + 15,
        )
        workspace.setProperty("activeMode", "transcript")
        transcript_result = workspace.findChild(QQuickItem, "transcriptResultPanel")
        assert transcript_result is not None and not transcript_result.isVisible()
        transcript_tabs = workspace.findChild(QQuickItem, "transcriptWorkspaceTabs")
        assert transcript_tabs is not None
        transcript_tabs.setProperty("currentIndex", 1)
        assert _process_until(
            lambda: workspace.property("activeMode") == "transcript"
            and transcript_tabs.property("currentIndex") == 1
        )
        timeline_overlay_render = root.grabWindow()
        timeline_overlay_path = tmp_path / "timeline-audio-subtitle-overlay.png"
        assert not timeline_overlay_render.isNull()
        assert timeline_overlay_render.save(str(timeline_overlay_path))
        controllers.workspace.cancelWorkflow(workflow_run_id)
        assert controllers.workspace.workflowPending is False
        assert _process_until(lambda: not workflow_banner.isVisible())
        fixed_tool_panel_width = float(tool_panel.width())
        fixed_preview_geometry = geometry(preview_viewport)
        fixed_timeline_geometry = geometry(timeline)
        for mode in modes:
            workspace.setProperty("activeMode", mode)
            for _ in range(6):
                QCoreApplication.processEvents()
                time.sleep(0.01)
            assert workspace.property("activeMode") == mode
            assert timeline.isVisible() and preview_viewport.isVisible()
            assert abs(float(tool_panel.width()) - fixed_tool_panel_width) <= 2
            assert all(
                abs(actual - expected) <= 2
                for actual, expected in zip(geometry(preview_viewport), fixed_preview_geometry, strict=True)
            )
            assert all(
                abs(actual - expected) <= 2
                for actual, expected in zip(geometry(timeline), fixed_timeline_geometry, strict=True)
            )

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
        assert _process_until(lambda: auto_continue_setting.isVisible() and auto_save_notice.isVisible())
        settings_render = root.grabWindow()
        assert not settings_render.isNull()
        assert settings_render.save(str(tmp_path / "settings-dialog.png"))
        persisted_before = (
            SettingsRepository(os.environ["MEDIAFLOW_SETTINGS_PATH"]).load().workflow.auto_continue
        )
        assert QMetaObject.invokeMethod(auto_continue_setting, "click")
        assert _process_until(
            lambda: SettingsRepository(os.environ["MEDIAFLOW_SETTINGS_PATH"]).load().workflow.auto_continue
            is not persisted_before,
            timeout=3,
        )
        assert QMetaObject.invokeMethod(settings_close, "click")

        title_export = root.findChild(QQuickItem, "titleExportButton")
        assert title_export is not None and title_export.isVisible()
        assert QMetaObject.invokeMethod(title_export, "click")
        assert _process_until(lambda: workspace.property("activeMode") == "export")

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
        assert export_to_project.property("text") == "开始导出视频"
        export_default_path = workspace.findChild(QQuickItem, "exportDefaultPath")
        assert export_default_path is not None and export_default_path.isVisible()
        assert export_default_path.property("text") == controllers.export.defaultExportDirectory
        assert export_as.property("text") == "另存为…"
        burn_subtitle = workspace.findChild(QQuickItem, "exportBurnSubtitleTrack")
        subtitle_color = workspace.findChild(QQuickItem, "exportSubtitleColor")
        export_subtitle_preview = workspace.findChild(QQuickItem, "exportSubtitlePreview")
        export_subtitle_text = workspace.findChild(QQuickItem, "exportSubtitlePreviewText")
        assert all(
            item is not None
            for item in (
                burn_subtitle,
                subtitle_color,
                export_subtitle_preview,
                export_subtitle_text,
            )
        )
        preview = workspace.findChild(QQuickItem, "previewPlayer")
        preview_viewport.seek(moved_projection["startFrame"] + 1)
        assert _process_until(lambda: preview.property("position") == moved_projection["startFrame"] + 1)
        burn_subtitle.setProperty("currentIndex", 1)
        subtitle_color.setProperty("text", "#00FF00")

        def export_preview_matches_options() -> bool:
            options = workspace.property("exportPreviewOptions").toVariant()
            return (
                options.get("burnSubtitleTrackId", "") == subtitle_projection["trackId"]
                and export_subtitle_preview.isVisible()
                and export_subtitle_text.property("color").name() == "#00ff00"
            )

        assert _process_until(export_preview_matches_options, timeout=3), {
            "options": workspace.property("exportPreviewOptions").toVariant(),
            "subtitleTrackOptions": controllers.export.subtitleTrackOptions,
            "burnCurrentValue": burn_subtitle.property("currentValue"),
            "previewVisible": export_subtitle_preview.isVisible(),
            "previewText": export_subtitle_text.property("text"),
        }
        export_render = root.grabWindow()
        assert not export_render.isNull()
        assert export_render.save(str(tmp_path / "one-click-project-export.png"))
        assert QMetaObject.invokeMethod(export_to_project, "click")
        assert _process_until(
            lambda: controllers.tasks.latestTask("export", controllers.workspace.activeSequenceId).get(
                "status"
            )
            in {"completed", "failed", "cancelled"},
            timeout=30,
        )
        export_task = controllers.tasks.latestTask("export", controllers.workspace.activeSequenceId)
        assert export_task["status"] == "completed", export_task
        assert export_task["artifacts"]
        exported_artifact = Path(export_task["artifacts"][0])
        if not exported_artifact.is_absolute():
            exported_artifact = Path(controllers.workspace.projectPath) / exported_artifact
        assert exported_artifact.is_file() and exported_artifact.stat().st_size > 0
        export_task_panel = workspace.findChild(QQuickItem, "exportTaskPanel")
        assert export_task_panel is not None and export_task_panel.isVisible()
        assert workspace.property("activeMode") == "export"
        task_details_button = export_task_panel.findChild(QQuickItem, "contextTaskDetailsButton")
        assert task_details_button is not None and task_details_button.isVisible()
        assert QMetaObject.invokeMethod(task_details_button, "click")
        assert _process_until(lambda: workspace.property("activeMode") == "tasks")
        task_center_panel = workspace.findChild(QQuickItem, "taskCenterPanel")
        task_center_list = workspace.findChild(QQuickItem, "taskCenterList")
        assert task_center_panel is not None and task_center_panel.isVisible()
        assert task_center_list is not None and task_center_list.isVisible()
        workspace.setProperty("activeMode", "transcript")
        transcript_tabs = workspace.findChild(QQuickItem, "transcriptWorkspaceTabs")
        assert transcript_tabs is not None
        transcript_tabs.setProperty("currentIndex", 2)
        QCoreApplication.processEvents()
        translation_observation: dict[str, object] = {}

        def translation_target_is_ready() -> bool:
            translation_panel = workspace.findChild(QQuickItem, "translationSectionPanel")
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
                targetVisible=(translation_target.isVisible() if translation_target is not None else False),
                currentValue=(
                    translation_target.property("currentValue") if translation_target is not None else None
                ),
                controllerDefault=controllers.settings.defaultTranslationLanguage,
            )
            return (
                translation_target is not None
                and translation_target.isVisible()
                and translation_target.property("currentValue") == "zh_CN"
            )

        assert _process_until(translation_target_is_ready), translation_observation
        translation_summary = workspace.findChild(QQuickItem, "translationComparisonSummary")
        translation_list = workspace.findChild(QQuickItem, "translationComparisonList")
        translation_editor = next(
            (
                item
                for item in _visual_items(translation_list)
                if item.objectName() == "translationTargetEditor"
            ),
            None,
        )
        translation_save = next(
            (
                item
                for item in _visual_items(translation_list)
                if item.objectName() == "translationSaveSegmentButton"
            ),
            None,
        )
        assert translation_summary is not None and translation_summary.isVisible()
        assert translation_list is not None and translation_list.property("count") == 1
        assert translation_editor is not None and translation_editor.isVisible(), {
            "listHeight": translation_list.height(),
            "listWidth": translation_list.width(),
            "contentHeight": translation_list.property("contentHeight"),
            "visible": translation_list.isVisible(),
        }
        assert translation_editor.property("text") == "叠加在音频波形上的字幕"
        translation_editor.forceActiveFocus()
        assert _process_until(lambda: translation_editor.property("activeFocus") is True)
        translation_editor.setProperty("text", "用户校对后的译文")
        controllers.settings.selectGlossaryTerm("")
        QCoreApplication.processEvents()
        translation_editor = next(
            item
            for item in _visual_items(translation_list)
            if item.objectName() == "translationTargetEditor" and item.isVisible()
        )
        translation_save = next(
            item
            for item in _visual_items(translation_list)
            if item.objectName() == "translationSaveSegmentButton" and item.isVisible()
        )
        assert translation_editor.property("text") == "用户校对后的译文"
        assert translation_save is not None
        assert QMetaObject.invokeMethod(translation_save, "click")
        assert _process_until(
            lambda: controllers.session.binding.current.list_subtitle_segments(translated_document_id)[0].text
            == "用户校对后的译文"
        )
        assert (
            controllers.session.binding.current.list_subtitle_segments(subtitle_document_id)[0].text
            == "Subtitle over the audio waveform"
        )
        translation_render = root.grabWindow()
        assert not translation_render.isNull()
        assert translation_render.save(str(tmp_path / "translation-bilingual-review.png"))
        workspace.setProperty("activeMode", "highlight")
        highlight_document = workspace.findChild(
            QQuickItem, "highlightSourceDocument")
        controllers.subtitles.selectSubtitleDocument(translated_document_id)
        assert highlight_document is not None
        assert _process_until(
            lambda: highlight_document.property("currentValue")
            == translated_document_id
        )
        controllers.subtitles.selectSubtitleDocument(subtitle_document_id)
        assert _process_until(
            lambda: highlight_document.property("currentValue")
            == subtitle_document_id
        )
        workspace.setProperty("activeMode", "media")
        media_task_panel = workspace.findChild(QQuickItem, "mediaTaskPanel")
        assert media_task_panel is not None
        if media_task_panel.isVisible():
            assert controllers.tasks.latestMediaTask(
                controllers.media.selectedAssetId
            ).get("status") != "completed"

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
        audio_task = controllers.tasks.latestCommandTask(
            "analyze_loudness", controllers.workspace.activeSequenceId
        )
        assert audio_task["status"] == "completed"
        audio_task_panel = workspace.findChild(QQuickItem, "audioAnalysisTaskPanel")
        assert audio_task_panel is not None and audio_task_panel.isVisible()
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
        asset_count_before_second_import = controllers.media.assetsModel.rowCount()
        controllers.media.importFiles(
            [QUrl.fromLocalFile(str(second_source))]
        )
        assert _process_until(
            lambda: controllers.media.assetsModel.rowCount() == asset_count_before_second_import + 1
            and controllers.media.selectedAssetData.get("name") == second_source.name,
            timeout=20,
        )
        second_asset_id = controllers.media.selectedAssetId
        controllers.media.selectAsset(asset_id, True)
        assert controllers.media.selectedAssetIds == [second_asset_id, asset_id]
        clip_count = controllers.timeline.clipsModel.rowCount()
        drop_errors: list[str] = []
        controllers.timeline.errorOccurred.connect(drop_errors.append)
        controllers.timeline.dropAssets(
            controllers.media.selectedAssetIds,
            "",
            -1,
            controllers.workspace.timelineDurationFrames,
            3.0,
            0,
            True,
            False,
        )
        assert controllers.timeline.clipsModel.rowCount() == clip_count + 2, {
            "errors": drop_errors,
            "pendingAssetIds": list(controllers.session.asset_state.pending_batch_ids),
            "pendingProfileAssetId": controllers.session.asset_state.pending_profile_asset_id,
            "selectedAssetIds": controllers.media.selectedAssetIds,
        }

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
        TaskRepository(controllers.session.binding.current).create(
            Task(
                project_id=controllers.session.binding.current.get_project().id,
                sequence_id=controllers.workspace.activeSequenceId,
                command=ExportSequenceCommand(
                    sequence_id=controllers.workspace.activeSequenceId,
                    output_path=str(recent_artifact),
                ),
                status=TaskStatus.FAILED,
                error="fixture failure",
                artifacts=[ArtifactReference.external(recent_artifact)],
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
        assert create_hero.height() <= 144
        assert create_hero_icon is not None and 52 <= create_hero_icon.width() <= 58
        assert create_hero_title is not None and create_hero_title.isVisible()
        assert 18 <= create_hero_title.property("font").pixelSize() <= 22
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
            controllers.session.lifecycle.remember_recent(tmp_path / name)
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
        assert recent_remove.property("text") == "从列表移除"
        assert recent_remove.property("danger") is False
        removed_project_path = Path(recent_remove.property("projectPath"))
        assert QMetaObject.invokeMethod(recent_remove, "click")
        assert _process_until(lambda: controllers.workspace.recentProjectsModel.rowCount() == 9)
        persisted_recent_paths = (
            SettingsRepository(os.environ["MEDIAFLOW_SETTINGS_PATH"]).load().ui.recent_project_paths
        )
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
