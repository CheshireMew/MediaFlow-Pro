from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QUrl, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem

from mediaflow.desktop.app import configure_application_font, create_engine


def _process_until(predicate, *, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_timeline_canvases_render_with_the_application_monospace_font(
    tmp_path: Path,
) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    messages: list[str] = []

    def capture_message(_message_type, _context, message: str) -> None:
        messages.append(message)

    previous_handler = qInstallMessageHandler(capture_message)
    engine = None
    controllers = None
    try:
        engine, controllers = create_engine(app)
        controllers.workspace_project.createProject(
            QUrl.fromLocalFile(str(tmp_path)).toString(),
            "Canvas Font Rendering",
        )
        controllers.timeline_structure.addTrack("audio")

        window = engine.rootObjects()[0]
        timeline_ruler = window.findChild(QQuickItem, "timelineRuler")
        subtitle_overview = window.findChild(QQuickItem, "subtitleOverviewCanvas")
        assert timeline_ruler is not None
        assert subtitle_overview is not None
        assert _process_until(
            lambda: timeline_ruler.isVisible()
            and timeline_ruler.width() > 0
            and timeline_ruler.height() > 0
            and subtitle_overview.isVisible()
            and subtitle_overview.width() > 0
            and subtitle_overview.height() > 0
        )

        assert QMetaObject.invokeMethod(timeline_ruler, "requestPaint")
        assert QMetaObject.invokeMethod(subtitle_overview, "requestPaint")
        rendered = window.grabWindow()
        screenshot = tmp_path / "timeline-canvas-font-rendering.png"
        assert not rendered.isNull()
        assert rendered.save(str(screenshot))
        assert screenshot.is_file() and screenshot.stat().st_size > 0

        context_font_warnings = [
            message
            for message in messages
            if "Context2D" in message and "font families specified are invalid" in message
        ]
        assert context_font_warnings == []
    finally:
        if controllers is not None:
            controllers.shutdown()
        if engine is not None:
            engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()
        qInstallMessageHandler(previous_handler)
