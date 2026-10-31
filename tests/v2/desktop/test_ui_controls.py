from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QAccessible, QColor, QGuiApplication, QImageReader
from PySide6.QtQml import QQmlComponent
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from mediaflow.composition import EditorApplication
from mediaflow.desktop.app import configure_application_font, create_engine
from mediaflow.infrastructure.settings_repository import DesktopSettingsRepository
from tests.v2.desktop_application_adapter import DesktopPresentationApplication


def _contrast(first: QColor, second: QColor) -> float:
    def luminance(color: QColor) -> float:
        channels = (color.redF(), color.greenF(), color.blueF())
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True))

    values = sorted((luminance(first), luminance(second)))
    return (values[1] + 0.05) / (values[0] + 0.05)


@pytest.mark.parametrize("theme", ["dark", "high_contrast"])
def test_controls_keep_contrast_names_and_long_choices_usable(tmp_path: Path, theme: str) -> None:
    repository = DesktopSettingsRepository()
    settings = repository.load()
    settings.ui.theme = theme
    repository.save(settings)
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app, DesktopPresentationApplication(EditorApplication()))
    component = QQmlComponent(engine)
    qml_directory = Path(__file__).resolve().parents[3] / "mediaflow" / "desktop" / "qml"
    component.setData(
        b'''
import QtQuick
import QtQuick.Controls
import "."
import "components"
Window {
    width: 640; height: 620; visible: true; color: Theme.window
    property int activations: 0
    property color muted: Theme.textMuted
    property color popupSurface: Theme.popup
    Column {
        x: 24; y: 24; spacing: 18
        AppButton {
            objectName: "primary"; text: "Import"; primary: true
            onClicked: activations++
        }
        AppButton { objectName: "danger"; text: "Cancel task"; danger: true }
        AppIconButton { objectName: "icon"; iconName: "add"; toolTipText: "Add track" }
        AppTextField { objectName: "search"; placeholderText: "Search media"; width: 180 }
        AppMenuButton {
            objectName: "menu"; width: 120
            text: "A very long workspace layout name"
        }
        AppComboBox {
            objectName: "choices"; width: 140
            model: ["Short", "An exceptionally long media format and encoding policy name"
                + " that must remain readable"]
        }
        EmptyState {
            objectName: "empty"; width: 180; height: 180
            title: "No matching media in this collection"
        }
    }
}
''',
        QUrl.fromLocalFile(str(qml_directory / "ControlsReview.qml")),
    )
    window = component.create()
    try:
        assert window is not None, [error.toString() for error in component.errors()]
        QTest.qWait(200)
        assert _contrast(window.property("muted"), window.property("popupSurface")) >= 4.5
        assert b"svg" in QImageReader.supportedImageFormats()
        for name in ("primary", "danger"):
            button = window.findChild(QQuickItem, name)
            assert button is not None
            center = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
            assert _contrast(button.property("foregroundColor"), button.property("backgroundColor")) >= 4.5
            QTest.mouseMove(window, center)
            QTest.qWait(150)
            assert _contrast(button.property("foregroundColor"), button.property("backgroundColor")) >= 4.5
            QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, center)
            assert _contrast(button.property("foregroundColor"), button.property("backgroundColor")) >= 4.5
            QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, center)

        icon = window.findChild(QQuickItem, "icon")
        search = window.findChild(QQuickItem, "search")
        assert QAccessible.queryAccessibleInterface(icon).text(QAccessible.Name) == "Add track"
        assert QAccessible.queryAccessibleInterface(search).text(QAccessible.Name) == "Search media"
        primary = window.findChild(QQuickItem, "primary")
        primary.forceActiveFocus(Qt.TabFocusReason)
        assert primary.property("visualFocus")
        before = window.property("activations")
        QTest.keyClick(window, Qt.Key_Space)
        assert window.property("activations") == before + 1
        QTest.keyClick(window, Qt.Key_Tab)
        assert window.findChild(QQuickItem, "danger").property("visualFocus")

        menu = window.findChild(QQuickItem, "menu")
        content = menu.property("contentItem")
        assert content.property("truncated")
        for child in content.childItems():
            assert child.x() >= 0 and child.x() + child.width() <= content.width() + 1

        choices = window.findChild(QQuickItem, "choices")
        choices.forceActiveFocus(Qt.TabFocusReason)
        QTest.keyClick(window, Qt.Key_Space)
        QTest.qWait(150)
        popup = choices.findChild(QObject, "appComboBoxPopup")
        assert popup is not None
        assert popup.property("visible")
        assert choices.width() < popup.property("width") <= window.width() - 24
        QTest.keyClick(window, Qt.Key_Down)
        QTest.keyClick(window, Qt.Key_Return)
        assert choices.property("currentIndex") == 1
        assert not popup.property("visible")
        title = window.findChild(QQuickItem, "emptyStateTitle")
        assert title.height() > title.property("font").pixelSize() * 2
        assert window.grabWindow().save(str(tmp_path / f"controls-{theme}.png"))
    finally:
        if window is not None:
            window.close()
            window.deleteLater()
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def test_compact_navigation_keeps_every_mode_and_settings_reachable(tmp_path: Path) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app)
    try:
        window = engine.rootObjects()[0]
        window.setWidth(1180)
        window.setHeight(720)
        controllers.workspace_project.createProject(str(tmp_path / "projects"), "Navigation")
        controllers.workspace_settings.saveWorkspaceLayout("standard", 520, 400, 210, True, True, True)
        QTest.qWait(250)
        workspace = window.findChild(QObject, "pageLoader").property("item")
        assert workspace is not None
        navigation = workspace.findChild(QQuickItem, "workspaceNavigation")
        assert navigation.property("compact")
        for width in (1180, 1280, 1920):
            window.setWidth(width)
            QTest.qWait(100)
            for name in ("toolPanelContainer", "previewPanel", "inspectorPanel"):
                pane = workspace.findChild(QQuickItem, name)
                origin = pane.mapToItem(workspace, QPointF(0, 0))
                assert origin.x() >= 8
                assert origin.x() + pane.width() <= workspace.width() - 8
            assert workspace.property("toolPanelWidth") == 520
            assert workspace.property("inspectorPanelWidth") == 400
        assert workspace.findChild(QQuickItem, "toolPanelContainer").width() == 520
        assert workspace.findChild(QQuickItem, "inspectorPanel").width() == 400
        window.setWidth(1180)
        QTest.qWait(100)
        handle = workspace.findChild(QQuickItem, "toolPanelResizeHandle")
        start = handle.mapToScene(QPointF(handle.width() / 2, handle.height() / 2)).toPoint()
        end = start - QPoint(40, 0)
        QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(window, start - QPoint(15, 0), delay=20)
        QTest.mouseMove(window, end, delay=40)
        QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end)
        QTest.qWait(100)
        assert workspace.property("toolPanelWidth") < 380
        controllers.workspace_settings.saveWorkspaceLayout("standard", 340, 300, 210, True, True, True)
        QTest.qWait(100)
        keys = [mode["key"] for mode in controllers.workspace.workspaceModes if mode["navigationVisible"]]

        def visual_items(item: QQuickItem):
            for child in item.childItems():
                yield child
                yield from visual_items(child)

        inspector = workspace.findChild(QQuickItem, "inspectorPanel")
        assert inspector is not None
        path_value = next(
            item for item in visual_items(inspector)
            if item.property("text") == controllers.workspace.projectPath
        )
        origin = path_value.mapToItem(inspector, QPointF(0, 0))
        assert origin.x() + path_value.width() <= inspector.width() - 16
        path_label = path_value.parentItem().childItems()[0]
        path_label.setProperty("text", "プロジェクトの状態")
        QTest.qWait(50)
        assert path_label.property("lineCount") > 1
        assert path_label.property("paintedWidth") <= path_label.width() + 1

        for key in [*keys, "settings"]:
            button = next(
                (item for item in visual_items(navigation) if item.objectName() == f"navigationItem_{key}"),
                None,
            )
            assert button is not None and button.isVisible()
            origin = button.mapToItem(navigation, QPointF(0, 0))
            assert 0 <= origin.x() < origin.x() + button.width() <= navigation.width()
            assert QAccessible.queryAccessibleInterface(button).text(QAccessible.Name)
            button.forceActiveFocus(Qt.TabFocusReason)
            QTest.keyClick(window, Qt.Key_Return)
            if key != "settings":
                assert workspace.property("activeMode") == key
            else:
                assert workspace.findChild(QObject, "settingsDialog").property("visible")
        assert window.grabWindow().save(str(tmp_path / "navigation-1180x720.png"))
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()
