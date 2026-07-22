from __future__ import annotations

import os
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from mediaflow.desktop.app import configure_application_font, create_engine
from mediaflow.desktop.controllers import EditorControllers
from mediaflow.domain.enums import AssetKind, TaskStatus
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.settings_repository import SettingsRepository
from mediaflow.infrastructure.ytdlp_service import YtDlpDownloadService
from tests.v2.infrastructure.test_media_pipeline import generate_real_media


class _SlowFileHandler(SimpleHTTPRequestHandler):
    def copyfile(self, source, outputfile) -> None:
        while chunk := source.read(4096):
            outputfile.write(chunk)
            outputfile.flush()
            time.sleep(0.04)

    def log_message(self, format, *args) -> None:
        del format, args


def _process_until(predicate, *, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_collection_plan_runs_as_one_workflow_and_publishes_downloaded_assets(
    tmp_path: Path,
) -> None:
    _application = QGuiApplication.instance() or QGuiApplication([])
    web_root = tmp_path / "web"
    web_root.mkdir()
    first_source = web_root / "first.mp4"
    second_source = web_root / "second.mp4"
    paths = RuntimePaths.discover()
    generate_real_media(first_source, paths, width=320, height=180)
    generate_real_media(second_source, paths, width=320, height=180)
    handler = partial(SimpleHTTPRequestHandler, directory=str(web_root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    controllers = EditorControllers()
    session = controllers.session
    try:
        controllers.workspace.createProject(
            QUrl.fromLocalFile(str(tmp_path)).toString(),
            "Download UI",
        )
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        plan = YtDlpDownloadService._plan_from_info(
            {
                "_type": "playlist",
                "id": "local-course",
                "title": "Local Course",
                "extractor_key": "LocalFixture",
                "entries": [
                    {
                        "id": "first",
                        "playlist_index": 1,
                        "title": "First Lesson",
                        "webpage_url": f"{base_url}/{first_source.name}",
                    },
                    {
                        "id": "second",
                        "playlist_index": 2,
                        "title": "Second Lesson",
                        "webpage_url": f"{base_url}/{second_source.name}",
                    },
                ],
            },
            f"{base_url}/collection",
        )
        session._set_download_plan(plan)

        controllers.tasks.submitDownloadPlan("best", "1-2", False, "best", "")

        run = session._documents.list_workflow_runs(active_only=True)[0]
        task_ids = run.payload.task_ids
        assert len(task_ids) == 2
        completed = [session._tasks.wait(task_id, timeout=30) for task_id in task_ids]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and controllers.workspace.workflowStage == "download":
            QCoreApplication.processEvents()
            time.sleep(0.01)

        task_commands = [session._tasks.get(task_id).command for task_id in task_ids]
        asset_paths = [
            session._documents.resolve_asset_path(asset) for asset in session._documents.list_assets()
        ]

        assert all(task.status == TaskStatus.COMPLETED for task in completed)
        assert all(command.command_type == "download_media" for command in task_commands)
        assert all(command.workflow.run_id == run.id for command in task_commands)
        assert controllers.workspace.workflowStage == "prepare_media"
        assert len(asset_paths) == 2
        assert {path.parent.name for path in asset_paths} == {"Local Course"}
        assert all(path.is_file() for path in asset_paths)
        assert controllers.media.assetsModel.rowCount() == 2
        persisted_settings = SettingsRepository(os.environ["MEDIAFLOW_SETTINGS_PATH"]).load()
        assert persisted_settings.ui.recent_project_paths == [str(tmp_path / "Download UI")]
    finally:
        controllers.shutdown()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


def test_default_workspace_contains_real_downloaded_video_and_subtitles(
    tmp_path: Path,
) -> None:
    _application = QGuiApplication.instance() or QGuiApplication([])
    web_root = tmp_path / "captioned-web"
    web_root.mkdir()
    source = web_root / "captioned.mp4"
    generate_real_media(source, RuntimePaths.discover(), width=320, height=180)
    (web_root / "captioned.en.vtt").write_text(
        "WEBVTT\n\n00:00.000 --> 00:00.900\nHello WorkSpace\n",
        encoding="utf-8",
    )
    (web_root / "index.html").write_text(
        """<!doctype html>
<html><head><title>Captioned Video</title></head><body>
<video controls>
  <source src="captioned.mp4" type="video/mp4">
  <track kind="subtitles" srclang="en" src="captioned.en.vtt">
</video>
</body></html>
""",
        encoding="utf-8",
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(SimpleHTTPRequestHandler, directory=str(web_root)),
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    controllers = EditorControllers()
    session = controllers.session
    try:
        controllers.workspace.createProject(
            QUrl.fromLocalFile(str(tmp_path)).toString(),
            "Captioned Download",
        )
        page_url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
        session._set_download_plan(YtDlpDownloadService().analyze(page_url))
        controllers.tasks.submitDownloadPlan("best", "", True, "best", "")

        run = session._documents.list_workflow_runs(active_only=True)[0]
        completed = session._tasks.wait(run.payload.task_ids[0], timeout=30)
        assert completed.status == TaskStatus.COMPLETED

        assets = session._documents.list_assets()
        paths = [session._documents.resolve_asset_path(asset) for asset in assets]
        workspace = Path(session.settings.download.output_directory)
        assert {asset.kind for asset in assets} == {AssetKind.VIDEO, AssetKind.SUBTITLE}
        assert all(path.is_relative_to(workspace) and path.is_file() for path in paths)
        project_subtitles = list(
            (tmp_path / "Captioned Download" / "generated" / "subtitles").rglob("*.srt")
        )
        assert project_subtitles
        assert "Hello WorkSpace" in project_subtitles[0].read_text(encoding="utf-8-sig")
    finally:
        controllers.shutdown()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


def test_quick_start_creates_profiled_project_and_shows_real_download_progress(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("MEDIAFLOW_APP_ROOT", str(tmp_path))
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    web_root = tmp_path / "web"
    web_root.mkdir()
    source = web_root / "quick-start-source.mp4"
    generate_real_media(source, RuntimePaths.discover(), width=640, height=360)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(_SlowFileHandler, directory=str(web_root)),
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    settings_repository = SettingsRepository(os.environ["MEDIAFLOW_SETTINGS_PATH"])
    remembered = settings_repository.load()
    remembered.download.resolution = "360p"
    remembered.download.download_subtitles = True
    remembered.download.codec = "avc"
    settings_repository.save(remembered)
    engine, controllers = create_engine(app)
    try:
        source_url = f"http://127.0.0.1:{server.server_address[1]}/{source.name}"
        plan = YtDlpDownloadService._plan_from_info(
            {
                "id": "quick-start",
                "title": "Quick Start Video",
                "extractor_key": "LocalFixture",
                "width": 640,
                "height": 360,
                "fps": 25,
                "duration": 1,
                "formats": [{"height": 360, "vcodec": "h264"}],
            },
            source_url,
        )
        controllers.session._set_download_plan(plan)
        controllers.tasks.downloadPlanChanged.emit()

        window = engine.rootObjects()[0]
        window.setWidth(1600)
        window.setHeight(980)
        assert _process_until(lambda: window.property("downloadPlanVisible") is True)
        summary = window.findChild(QQuickItem, "downloadMediaSummary")
        confirm = window.findChild(QQuickItem, "confirmDownloadButton")
        resolution = window.findChild(QQuickItem, "downloadResolution")
        subtitles = window.findChild(QQuickItem, "downloadSubtitles")
        project_name = window.findChild(QQuickItem, "downloadProjectName")
        destination = window.findChild(QQuickItem, "downloadDestinationValue")
        choose_destination = window.findChild(QQuickItem, "chooseDownloadDirectoryButton")
        reset_media = window.findChild(QQuickItem, "resetMediaDirectoryButton")
        assert summary is not None and "640×360" in summary.property("text")
        assert confirm is not None and confirm.property("text") == "下载并新建项目"
        assert confirm.property("enabled") is True
        assert resolution is not None and resolution.property("currentValue") == "360p"
        assert subtitles is not None and subtitles.property("checked") is True
        assert project_name is not None and project_name.isVisible()
        project_name.setProperty("text", "AI Industry Project")
        expected_project_path = tmp_path / "Project" / "AI Industry Project"
        assert window.findChild(QQuickItem, "downloadProjectPathValue") is None
        assert window.findChild(QQuickItem, "chooseDownloadProjectDirectoryButton") is None
        assert destination is not None
        assert Path(destination.property("text")) == tmp_path / "WorkSpace"
        assert choose_destination is not None and choose_destination.isVisible()
        assert reset_media is not None and not reset_media.isVisible()
        assert reset_media.property("text") == "恢复默认"
        assert window.findChild(QQuickItem, "toggleAdvancedDownloadOptionsButton") is None

        custom_media = tmp_path / "Custom Media"
        controllers.settings.setDefaultDownloadDirectory(str(custom_media))
        assert _process_until(
            lambda: reset_media.isVisible()
            and Path(destination.property("text")) == custom_media
        )
        assert QMetaObject.invokeMethod(reset_media, "click")
        assert _process_until(
            lambda: not reset_media.isVisible()
            and Path(destination.property("text")) == tmp_path / "WorkSpace"
        )
        dialog_render = window.grabWindow()
        dialog_screenshot = tmp_path / "quick-start-download-settings.png"
        assert not dialog_render.isNull() and dialog_render.save(str(dialog_screenshot))

        codec = window.findChild(QQuickItem, "downloadCodec")
        assert codec is not None and codec.isVisible()
        assert codec.property("currentValue") == "avc"
        codec_center = codec.mapToScene(QPointF(codec.width() / 2, codec.height() / 2))
        QTest.mouseClick(
            window,
            Qt.LeftButton,
            Qt.NoModifier,
            QPoint(round(codec_center.x()), round(codec_center.y())),
        )
        codec_popup = codec.findChild(QObject, "appComboBoxPopup")
        assert codec_popup is not None
        assert _process_until(lambda: bool(codec_popup.property("visible")))
        popup_render = window.grabWindow()
        popup_screenshot = tmp_path / "quick-start-download-codec-menu.png"
        assert not popup_render.isNull() and popup_render.save(str(popup_screenshot))
        QTest.keyClick(window, Qt.Key_Escape)

        resolution.setProperty("currentIndex", 0)
        codec.setProperty("currentIndex", 0)
        subtitles.setProperty("checked", False)
        confirm_center = confirm.mapToScene(QPointF(confirm.width() / 2, confirm.height() / 2))
        QTest.mouseClick(
            window,
            Qt.LeftButton,
            Qt.NoModifier,
            QPoint(round(confirm_center.x()), round(confirm_center.y())),
        )
        assert _process_until(lambda: controllers.workspace.hasProject)
        assert controllers.workspace.projectName == "AI Industry Project"
        assert window.property("title") == "AI Industry Project"
        window_project_name = window.findChild(QQuickItem, "windowProjectName")
        assert window_project_name is not None
        assert window_project_name.property("text") == "AI Industry Project"
        assert (controllers.workspace.profileWidth, controllers.workspace.profileHeight) == (640, 360)
        assert (
            controllers.workspace.profileFpsNumerator,
            controllers.workspace.profileFpsDenominator,
        ) == (25, 1)

        page_loader = window.findChild(QObject, "pageLoader")
        assert page_loader is not None
        assert _process_until(
            lambda: page_loader.property("item") is not None
            and page_loader.property("item").objectName() == "workspace"
        )
        workspace = page_loader.property("item")
        settings_dialog = workspace.findChild(QObject, "settingsDialog")
        settings_tabs = workspace.findChild(QQuickItem, "settingsTabs")
        project_directory_setting = workspace.findChild(
            QQuickItem, "defaultProjectDirectorySetting"
        )
        media_directory_setting = workspace.findChild(
            QQuickItem, "defaultMediaDirectorySetting"
        )
        assert settings_dialog is not None and settings_tabs is not None
        assert project_directory_setting is not None
        assert media_directory_setting is not None
        assert QMetaObject.invokeMethod(settings_dialog, "open")
        settings_tabs.setProperty("currentIndex", 1)
        assert _process_until(
            lambda: Path(project_directory_setting.property("text")) == tmp_path / "Project"
            and media_directory_setting.isVisible()
            and Path(media_directory_setting.property("text")) == tmp_path / "WorkSpace"
        )
        settings_render = window.grabWindow()
        settings_screenshot = tmp_path / "media-default-location-setting.png"
        assert not settings_render.isNull() and settings_render.save(str(settings_screenshot))
        assert QMetaObject.invokeMethod(settings_dialog, "close")

        assert _process_until(lambda: controllers.tasks.downloadProgressVisible)
        banner = workspace.findChild(QQuickItem, "downloadProgressBanner")
        progress_bar = workspace.findChild(QQuickItem, "downloadProgressBar")
        assert banner is not None and progress_bar is not None
        assert _process_until(
            lambda: banner.isVisible() and 0 < float(progress_bar.property("value")) < 100
        )
        assert workspace.findChild(QQuickItem, "workflowBanner") is None
        rendered = window.grabWindow()
        screenshot = tmp_path / "quick-start-download-progress.png"
        assert not rendered.isNull() and rendered.save(str(screenshot))

        assert _process_until(lambda: controllers.media.assetsModel.rowCount() == 1, timeout=30)
        downloaded_asset = controllers.media.assetsModel.get(0)
        downloaded_path = Path(downloaded_asset["path"])
        assert downloaded_path.is_file()
        assert downloaded_path.parent.name == "WorkSpace"
        assert (expected_project_path / "project.mfp").is_file()
        persisted_settings = SettingsRepository(os.environ["MEDIAFLOW_SETTINGS_PATH"]).load()
        assert persisted_settings.ui.default_project_directory == str(
            (tmp_path / "Project").resolve()
        )
        assert persisted_settings.download.output_directory == str(
            (tmp_path / "WorkSpace").resolve()
        )
        assert persisted_settings.download.resolution == "best"
        assert persisted_settings.download.download_subtitles is False
        assert persisted_settings.download.codec == "best"
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
