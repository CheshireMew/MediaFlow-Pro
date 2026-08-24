from __future__ import annotations

import time

from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem

from mediaflow.desktop.app import configure_application_font, create_engine
from mediaflow.desktop.controllers import EditorControllers
from mediaflow.infrastructure.xiaoyuzhou_media import XiaoyuzhouEpisodeResolver
from tests.v2.infrastructure.test_xiaoyuzhou_media import EPISODE_URL, _episode_html


def test_xiaoyuzhou_plan_forces_audio_without_overwriting_video_preferences() -> None:
    _application = QGuiApplication.instance() or QGuiApplication([])
    controllers = EditorControllers()
    session = controllers.session
    original_resolution = session.state.service_settings.download.resolution
    try:
        session._set_download_plan(XiaoyuzhouEpisodeResolver._plan_from_html(EPISODE_URL, _episode_html()))

        [request] = controllers.downloads._build_download_requests(
            "1080p",
            "",
            True,
            "avc",
            "",
        )

        assert request.resolution == "audio"
        assert request.download_subtitles is False
        assert request.entry.suggested_filename
        assert session.state.service_settings.download.resolution == original_resolution
        assert controllers.downloads.downloadPlanData["media_kind"] == "audio"
    finally:
        controllers.shutdown()


def test_xiaoyuzhou_download_plan_is_presented_as_audio_in_qml() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app)
    try:
        controllers.session._set_download_plan(
            XiaoyuzhouEpisodeResolver._plan_from_html(EPISODE_URL, _episode_html())
        )
        window = engine.rootObjects()[0]
        deadline = time.monotonic() + 10
        while not window.property("downloadPlanVisible") and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)

        dialog = window.findChild(QObject, "downloadPlanDialog")
        summary = window.findChild(QQuickItem, "downloadMediaSummary")
        resolution = window.findChild(QQuickItem, "downloadResolution")
        codec = window.findChild(QQuickItem, "downloadCodec")
        subtitles = window.findChild(QQuickItem, "downloadSubtitles")
        filename = window.findChild(QQuickItem, "downloadFilename")

        assert window.property("downloadPlanVisible") is True
        assert window.property("downloadPlanIsAudio") is True
        assert dialog is not None and dialog.property("title") == "媒体信息与下载设置"
        assert summary is not None and "单集音频" in summary.property("text")
        assert resolution is not None and resolution.property("currentValue") == "audio"
        assert resolution.property("enabled") is False
        assert codec is not None and not codec.isVisible()
        assert subtitles is not None and not subtitles.isVisible()
        assert filename is not None
        assert filename.property("placeholderText") == "自定义音频文件名（可选）"
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()
