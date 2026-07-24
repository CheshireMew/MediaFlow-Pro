from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from PySide6.QtWebEngineQuick import QtWebEngineQuick

from mediaflow.composition import EditorApplication
from mediaflow.desktop.app import configure_application_font, create_engine
from mediaflow.domain.enums import TrackKind

STARTER = Path(
    "E:/Work/BaiduSyncdisk/Code/Cheshire-skill/visual-multimedia/assets/web-media-starter"
)


def _process_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_unified_import_adds_validated_web_media_and_opens_visible_editor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    if QGuiApplication.instance() is None:
        QtWebEngineQuick.initialize()
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app)
    try:
        controllers.workspace.createProject(
            QUrl.fromLocalFile(str(tmp_path)).toString(),
            "Unified Web Import",
        )
        window = engine.rootObjects()[0]
        loader = window.findChild(QObject, "pageLoader")
        assert loader is not None
        assert _process_until(lambda: loader.property("item") is not None)
        workspace = loader.property("item")
        assert workspace.findChild(QQuickItem, "openWebTemplateCenterButton") is None
        controllers.media.importFiles(
            [QUrl.fromLocalFile(str(STARTER / "editable-media.json"))]
        )
        assert _process_until(lambda: controllers.media.assetsModel.rowCount() == 1)
        manifest_import_id = controllers.media.selectedAssetId
        controllers.media.importFiles([QUrl.fromLocalFile(str(STARTER))])
        assert _process_until(lambda: controllers.media.assetsModel.rowCount() == 2)
        imported = next(
            controllers.media.assetsModel.get(index)
            for index in range(controllers.media.assetsModel.rowCount())
            if controllers.media.assetsModel.get(index)["assetId"]
            == controllers.media.selectedAssetId
        )
        assert imported["kind"] == "web"
        assert imported["assetId"] != manifest_import_id
        controllers.timeline.dropAssets(
            [imported["assetId"]], "", -1, 0, 3.0, 0, True, False
        )
        assert _process_until(lambda: controllers.timeline.clipsModel.rowCount() == 1)
        state = controllers.session._documents.load_timeline(
            controllers.workspace.activeSequenceId
        )
        assert len(state.clips) == 1
        clip = state.clips[0]
        asset = controllers.session._documents.get_asset(clip.asset_id)
        assert asset.kind.value == "web"
        assert clip.id in state.web_states
        assert controllers.web.isWebClip is True
        workspace.setProperty("activeMode", "edit")
        controllers.web.setEditMode(True)
        web_canvas = workspace.findChild(QQuickItem, "webEditorCanvas")
        assert _process_until(
            lambda: web_canvas is not None
            and web_canvas.isVisible()
            and controllers.web.entryUrl.startswith("file:")
        )
        rendered = window.grabWindow()
        screenshot = tmp_path / "unified-web-import-visible-editor.png"
        assert not rendered.isNull() and rendered.save(str(screenshot))
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def test_desktop_web_layer_edit_and_browser_commit_share_project_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MEDIAFLOW_RUNTIME_DIR", str(tmp_path / "runtime"))
    project_path = tmp_path / "Desktop Web Project"
    api = EditorApplication()
    with api.create_project(project_path, "Desktop Web Project") as project:
        asset = project.web.import_package(STARTER)
        sequence_id = project.documents.get_project().main_sequence_id
        editor = project.timeline(sequence_id)
        track = editor.add_track(TrackKind.VIDEO)
        clip = editor.add_clip(
            track_id=track.id,
            asset_id=asset.id,
            timeline_start=0,
            source_in=0,
            duration=2,
        )

    if QGuiApplication.instance() is None:
        QtWebEngineQuick.initialize()
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app, api)
    errors: list[str] = []
    controllers.web.errorOccurred.connect(errors.append)
    try:
        controllers.workspace.openProject(QUrl.fromLocalFile(str(project_path)).toString())
        controllers.timeline.selectClip(clip.id)
        QCoreApplication.processEvents()
        assert controllers.web.isWebClip is True
        assert controllers.web.layersModel.rowCount() == 5
        assert controllers.web.entryUrl.startswith("file:")

        controllers.web.updateLayer("title", {"content": "Desktop edit", "x": 80.0})
        persisted = controllers.session._documents.get_web_clip_state(clip.id)
        assert persisted.revision == 1
        assert persisted.layers["title"].content == "Desktop edit"

        browser_state = json.loads(controllers.web.stateJson)
        browser_state["layers"]["title"]["x"] = 96.0
        controllers.web.commitBrowserState(json.dumps(browser_state))
        persisted = controllers.session._documents.get_web_clip_state(clip.id)
        assert persisted.revision == 2
        assert persisted.layers["title"].x == 80.0
        assert persisted.layout_overrides[controllers.web.activeLayoutId]["title"].x == 96.0
        assert json.loads(controllers.web.stateJson)["layers"]["title"]["x"] == 96.0

        controllers.timeline.undo()
        undone = controllers.session._documents.get_web_clip_state(clip.id)
        assert undone.layers["title"].x == 80.0
        assert undone.layout_overrides == {}
        assert controllers.timeline.canRedo is True

        controllers.web.selectLayout("portrait")
        controllers.web.selectLayer("title")
        controllers.web.setKeyframeAtFrame("opacity", "0.5", "ease_in_out", 0)
        controllers.web.updateThemeValue("accent", "#e6007a")
        controllers.web.updateDataValue("left_value", '"Desktop data"')
        controllers.web.setFieldLocked("title", "x", True)
        assert errors == []
        expanded = controllers.session._documents.get_web_clip_state(clip.id)
        assert expanded.layout_id == "portrait"
        assert expanded.animations["title"]["opacity"].keyframes[0].value == 0.5
        assert expanded.theme["accent"] == "#e6007a"
        assert expanded.data_snapshot.values["left_value"] == "Desktop data"
        assert expanded.locks["title"] == ("x",)
        assert controllers.web.keyframesData[0]["field"] == "opacity"

        current = controllers.session._documents.get_web_clip_state(clip.id)
        cli_request = {
            "protocol": "mediaflow-cli",
            "version": 1,
            "operation": "web.clip.update",
            "project": str(project_path),
            "arguments": {
                "sequence_id": controllers.workspace.activeSequenceId,
                "clip_id": clip.id,
                "updates": {"title": {"content": "CLI while desktop is open"}},
                "expected_revision": current.revision,
                "actor": "automation",
            },
        }
        completed = subprocess.run(
            [sys.executable, "-m", "mediaflow.cli", "execute", "--request", "-"],
            input=json.dumps(cli_request),
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parents[3],
        )
        response = json.loads(completed.stdout)
        assert response["ok"] is True

        QTest.qWait(700)
        assert json.loads(controllers.web.stateJson)["layers"]["title"]["content"] == (
            "CLI while desktop is open"
        )
        assert controllers.timeline.canUndo is False
        assert controllers.timeline.canRedo is False
    finally:
        controllers.shutdown()
        engine.deleteLater()
