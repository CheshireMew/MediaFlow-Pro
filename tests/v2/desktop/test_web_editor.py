from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    Qt,
    QUrl,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from PySide6.QtWebEngineQuick import QtWebEngineQuick

from mediaflow.composition import EditorApplication
from mediaflow.desktop.app import configure_application_font, create_engine
from mediaflow.domain.enums import TrackKind
from mediaflow.infrastructure.project_repository import ProjectRepository

STARTER = Path(__file__).resolve().parents[2] / "fixtures" / "editable-media-v5"


def _process_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _application() -> QGuiApplication:
    if QGuiApplication.instance() is None:
        QtWebEngineQuick.initialize()
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    return app


def test_unified_import_opens_the_v5_package_through_local_preview_server(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    app = _application()
    engine, controllers = create_engine(app)
    try:
        controllers.workspace.createProject(
            QUrl.fromLocalFile(str(tmp_path)).toString(),
            "Unified V5 Web Import",
        )
        window = engine.rootObjects()[0]
        loader = window.findChild(QObject, "pageLoader")
        assert loader is not None
        assert _process_until(lambda: loader.property("item") is not None)
        workspace = loader.property("item")

        controllers.media.importFiles([QUrl.fromLocalFile(str(STARTER / "editable-media.json"))])
        assert _process_until(lambda: controllers.media.assetsModel.rowCount() == 1)
        imported = controllers.media.assetsModel.get(0)
        assert imported["kind"] == "web"

        controllers.timeline.dropAssets(
            [imported["assetId"]],
            "",
            -1,
            0,
            3.0,
            0,
            True,
            False,
        )
        assert _process_until(lambda: controllers.timeline.clipsModel.rowCount() == 1)
        state = controllers.session.binding.current.load_timeline(controllers.workspace.activeSequenceId)
        assert len(state.clips) == 1
        assert state.clips[0].id in state.web_states

        workspace.setProperty("activeMode", "edit")
        controllers.web.setEditMode(True)
        web_canvas = workspace.findChild(QQuickItem, "webEditorCanvas")
        assert _process_until(
            lambda: web_canvas is not None
            and web_canvas.isVisible()
            and controllers.web.entryUrl.startswith("http://127.0.0.1:")
        )
        web_view = workspace.findChild(QQuickItem, "webEditorWebView")
        assert web_view is not None
        assert _process_until(
            lambda: controllers.web.browserReady and bool(controllers.web.browserLayerSnapshot),
            15,
        )
        assert controllers.web.manifestData["version"] == 5
        assert controllers.web.browserRevision == 0
        controllers.web.selectLayer("title")
        assert _process_until(
            lambda: controllers.web.selectedLayerId == "title"
            and len(controllers.web.parameterDescriptors) == 5
            and bool(controllers.web.selectedLayerDescriptors)
        )
        spring = next(
            item for item in controllers.web.parameterDescriptors if item["source_id"] == "spring_strength"
        )
        assert spring["control"] == "slider"
        assert spring["unit"] == "ratio"
        timeline_editor = workspace.findChild(QQuickItem, "webTimelineEditor")
        assert timeline_editor is not None
        assert timeline_editor.isVisible()
        assert {
            item["label"] for item in controllers.web_timeline.timelineItemsData if item["kind"] == "interval"
        } == {"显示区间", "动画区间"}
        assert controllers.web_timeline.snapSceneTimeMs(875) == 900

        controllers.web.updateDescriptorValue(
            "parameter",
            "spring_strength",
            0.86,
        )
        controllers.web_timeline.setDescriptorKeyframeAtFrame(
            "parameter",
            "spring_strength",
            0.7,
            "ease_out",
            15,
        )
        clip_state = controllers.session.binding.current.get_web_clip(state.clips[0].id)
        assert clip_state.parameters["spring_strength"] == 0.86
        assert (
            clip_state.scenes["opening"].parameter_animations["spring_strength"].keyframes[0].time_ms == 500
        )
        assert any(
            item.get("target") == "parameter" and item.get("sourceId") == "spring_strength"
            for item in controllers.web_timeline.timelineItemsData
        )
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def test_real_dom_drag_crosses_webchannel_persists_and_is_read_back_by_page(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    project_path = tmp_path / "Desktop V5 Web Project"
    api = EditorApplication()
    with api.create_project(project_path, "Desktop V5 Web Project") as project:
        asset = project.import_web_package(STARTER)
        sequence_id = project.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=4,
        )

    app = _application()
    engine, controllers = create_engine(app, api)
    errors: list[str] = []
    controllers.web.errorOccurred.connect(errors.append)
    try:
        controllers.workspace.openProject(QUrl.fromLocalFile(str(project_path)).toString())
        controllers.timeline.selectClip(clip.id)
        window = engine.rootObjects()[0]
        loader = window.findChild(QObject, "pageLoader")
        assert loader is not None
        assert _process_until(lambda: loader.property("item") is not None)
        workspace = loader.property("item")
        workspace.setProperty("activeMode", "edit")
        controllers.web.setEditMode(True)

        web_canvas = workspace.findChild(QQuickItem, "webEditorCanvas")
        web_view = workspace.findChild(QQuickItem, "webEditorWebView")
        assert web_canvas is not None and web_view is not None
        assert _process_until(
            lambda: web_canvas.isVisible() and controllers.web.entryUrl.startswith("http://127.0.0.1:"),
            15,
        )
        assert _process_until(lambda: controllers.web.browserReady, 15)
        controllers.web.selectLayer("title")
        assert _process_until(
            lambda: controllers.web.browserEditMode
            and controllers.web.browserSelectedLayerId == "title"
            and bool(controllers.web.browserLayerSnapshot.get("width")),
            15,
        ), (
            controllers.web.selectedLayerId,
            controllers.web.browserLayerSnapshot,
            controllers.web.browserRevision,
        )
        metrics = dict(controllers.web.browserLayerSnapshot)
        title_definition = next(
            layer for layer in controllers.web.manifestData["layers"] if layer["id"] == "title"
        )
        state_x = float(title_definition["default_bounds"]["x"])
        zoom = float(web_view.property("zoomFactor") or 1.0)
        local_start = QPointF(
            (float(metrics["x"]) + min(40.0, float(metrics["width"]) / 3)) * zoom,
            (float(metrics["y"]) + min(40.0, float(metrics["height"]) / 3)) * zoom,
        )
        scene_start = web_view.mapToScene(local_start)
        start = QPoint(round(scene_start.x()), round(scene_start.y()))
        end = QPoint(start.x() + round(137 * zoom), start.y() + round(83 * zoom))
        quick_window = web_view.window()
        assert quick_window is not None
        controllers.web.selectLayer("eyebrow")
        assert _process_until(
            lambda: controllers.web.selectedLayerId == "eyebrow"
            and controllers.web.browserSelectedLayerId == "eyebrow",
            5,
        ), (
            controllers.web.selectedLayerId,
            controllers.web.browserSelectedLayerId,
            controllers.web.browserEditMode,
            controllers.web.editMode,
            controllers.web.isWebClip,
        )
        quick_window.requestActivate()
        QTest.mouseMove(quick_window, start, 50)

        QTest.mousePress(
            quick_window,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            start,
        )
        assert _process_until(
            lambda: controllers.web.selectedLayerId == "title",
            5,
        ), (
            (
                metrics["x"],
                metrics["y"],
                metrics["width"],
                metrics["height"],
            ),
            zoom,
            start,
            web_view.x(),
            web_view.y(),
            web_view.width(),
            web_view.height(),
        )
        QTest.mouseMove(quick_window, end, 120)
        QTest.mouseRelease(
            quick_window,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            end,
        )

        assert _process_until(
            lambda: controllers.web.selectedLayerId == "title"
            and controllers.session.binding.current.get_web_clip(clip.id).revision > 0,
            15,
        ), (
            controllers.web.selectedLayerId,
            controllers.session.binding.current.get_web_clip(clip.id).revision,
            (
                metrics["x"],
                metrics["y"],
                metrics["width"],
                metrics["height"],
            ),
            zoom,
            start,
            end,
            web_view.x(),
            web_view.y(),
            web_view.width(),
            web_view.height(),
        )
        assert errors == []

        with ProjectRepository.open(project_path, writable=False) as reread:
            persisted = reread.web.get_web_clip_state(clip.id)
        persisted_x = persisted.scenes["opening"].layers["title"].x
        persisted_y = persisted.scenes["opening"].layers["title"].y
        assert persisted_x is not None and persisted_x != state_x
        assert persisted_y is not None

        assert _process_until(
            lambda: controllers.web.browserRevision == persisted.revision
            and controllers.web.browserLayerSnapshot.get("x") != metrics["x"],
            15,
        )
        assert controllers.web.browserLayerSnapshot["content"] == "One source, three ways to play"
        web_view.forceActiveFocus()
        assert _process_until(lambda: not bool(workspace.property("shortcutsEnabled")))
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()
