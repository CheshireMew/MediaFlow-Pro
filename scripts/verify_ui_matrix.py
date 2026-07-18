from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

LANGUAGES = ("zh_CN", "en", "ja")
SCALES = ("1", "1.5", "2")
HOME_SIZES = ((1280, 720), (1600, 980))
SIZES = ((1280, 720), (1920, 1080), (3840, 2160))
WORK_MODES = ("media", "transcript", "translate", "highlight", "edit", "audio", "export")
TASK_FOCUSED_MODES = {"transcript", "translate", "highlight", "audio", "export"}
TIMELINE_MODES = {"media", "transcript", "highlight", "edit", "audio"}
SETTINGS_TABS = ("general", "download", "ai")


def probe(root: Path, language: str, scale: str) -> dict:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = scale
    os.environ["MEDIAFLOW_RUNTIME_DIR"] = str(root / "runtime")

    from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickItem, QQuickWindow
    from shiboken6 import getCppPointer, wrapInstance

    from mediaflow.desktop.app import configure_application_font, create_engine
    from mediaflow.domain.settings import GlobalSettings
    from mediaflow.infrastructure.settings_repository import SettingsRepository

    settings = GlobalSettings()
    settings.ui.language = language
    SettingsRepository(root / "runtime" / "settings.json").save(settings)
    app = QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app)
    workspace_controller = controllers.workspace

    def visual_items(item: QQuickItem):
        for child in item.childItems():
            yield child
            yield from visual_items(child)
    results = []
    try:
        window = engine.rootObjects()[0]
        quick_window = wrapInstance(getCppPointer(window)[0], QQuickWindow)
        loader = window.findChild(QObject, "pageLoader")
        title_bar = window.findChild(QObject, "appTitleBar")
        minimize_button = window.findChild(QObject, "minimizeWindowButton")
        maximize_button = window.findChild(QObject, "maximizeWindowButton")
        close_button = window.findChild(QObject, "closeWindowButton")
        if any(item is None for item in (title_bar, minimize_button, maximize_button, close_button)):
            raise RuntimeError("Custom window title bar controls were not created")

        workspace_controller.createProject(
            QUrl.fromLocalFile(str(root)).toString(),
            "UI Matrix",
        )
        project_path = Path(workspace_controller.projectPath)
        workspace_controller.closeProject()
        home_results = []
        for home_width, home_height in HOME_SIZES:
            window.setWidth(home_width)
            window.setHeight(home_height)
            for _ in range(12):
                QCoreApplication.processEvents()
                time.sleep(0.02)
            home = loader.property("item")
            home_scroll = home.findChild(QObject, "homeScroll")
            create_hero = home.findChild(QObject, "homeCreateHero")
            recent_section = home.findChild(QObject, "homeRecentSection")
            if home_scroll is None or create_hero is None or recent_section is None:
                raise RuntimeError("Home page primary sections were not created")
            if float(recent_section.property("y")) <= (
                float(create_hero.property("y")) + float(create_hero.property("height"))
            ):
                raise RuntimeError("Home page creation and recent-project sections are not vertical")
            home_image = quick_window.grabWindow()
            home_screenshot = root / (f"{language}-{scale}x-home-{home_width}x{home_height}.png")
            if home_image.isNull() or not home_image.save(str(home_screenshot)):
                raise RuntimeError(f"Unable to render {home_screenshot}")
            home_results.append(
                {
                    "size": [home_width, home_height],
                    "rendered": [home_image.width(), home_image.height()],
                    "title_bar_visible": bool(title_bar.property("visible")),
                    "scrollable": float(home_scroll.property("contentHeight"))
                    > float(home_scroll.property("height")),
                    "recent_project_count": workspace_controller.recentProjectsModel.rowCount(),
                    "screenshot": str(home_screenshot),
                }
            )

        workspace_controller.openProject(QUrl.fromLocalFile(str(project_path)).toString())
        for _ in range(12):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        for width, height in SIZES:
            window.setWidth(width)
            window.setHeight(height)
            for _ in range(12):
                QCoreApplication.processEvents()
                time.sleep(0.02)
            workspace = loader.property("item")
            inspector = workspace.findChild(QObject, "inspectorContainer")
            compact_inspector = workspace.findChild(QObject, "compactInspectorDrawer")
            compact_inspector_button = workspace.findChild(QObject, "compactInspectorButton")
            navigation = workspace.findChild(QObject, "workspaceNavigation")
            tool_panel = workspace.findChild(QObject, "toolPanelContainer")
            timeline = workspace.findChild(QObject, "timelinePanel")
            timeline_toolbar = workspace.findChild(QObject, "timelineToolbarScroll")
            workflow_mode = workspace.findChild(QObject, "workflowMode")
            if (
                compact_inspector is None
                or compact_inspector_button is None
                or navigation is None
            ):
                raise RuntimeError("Compact inspector controls were not created")
            tool_panel_visible = bool(tool_panel.property("visible"))
            timeline_visible = bool(timeline.property("visible"))
            workflow_mode_visible = bool(workflow_mode.property("visible"))
            inspector_visible = bool(inspector.property("visible"))
            compact_inspector_visible = bool(compact_inspector.property("visible"))
            compact_button_visible = bool(compact_inspector_button.property("visible"))
            if (
                not tool_panel_visible
                or not timeline_visible
                or not workflow_mode_visible
                or timeline_toolbar is None
            ):
                raise RuntimeError(f"A primary workspace control is hidden at {width}x{height}")
            toolbar_width = float(timeline_toolbar.property("width"))
            toolbar_content_width = float(timeline_toolbar.property("contentWidth"))
            if toolbar_content_width > toolbar_width:
                expected_content_x = toolbar_content_width - toolbar_width
                timeline_toolbar.setProperty("contentX", expected_content_x)
                for _ in range(4):
                    QCoreApplication.processEvents()
                    time.sleep(0.01)
                if abs(float(timeline_toolbar.property("contentX")) - expected_content_x) > 2:
                    raise RuntimeError(
                        f"Timeline overflow controls are unreachable at {width}x{height}"
                    )
                timeline_toolbar.setProperty("contentX", 0)
            workspace_width = float(workspace.property("width"))
            expected_inspector = workspace_width >= 1320
            if inspector_visible != expected_inspector:
                raise RuntimeError(
                    "Inspector responsive state is wrong at "
                    f"{width}x{height} (workspace width {workspace_width})"
                )
            if compact_inspector_visible != (not expected_inspector):
                raise RuntimeError(f"Compact inspector responsive state is wrong at {width}x{height}")
            if compact_button_visible != (not expected_inspector):
                raise RuntimeError(f"Compact inspector button state is wrong at {width}x{height}")
            if float(navigation.property("width")) < 90:
                raise RuntimeError(f"Workspace navigation is too narrow at {width}x{height}")
            navigation_items = {
                item.objectName(): item for item in visual_items(navigation)
            }
            for mode in WORK_MODES:
                navigation_item = navigation_items.get(f"navigationItem_{mode}")
                if navigation_item is None or not navigation_item.isVisible():
                    raise RuntimeError(f"Persistent navigation label is missing for {mode}")
            compact_inspector_open = False
            if not expected_inspector:
                if not QMetaObject.invokeMethod(compact_inspector_button, "click"):
                    raise RuntimeError("Compact inspector button could not be activated")
                for _ in range(12):
                    QCoreApplication.processEvents()
                    time.sleep(0.02)
                compact_inspector_open = bool(workspace.property("inspectorDrawerOpen"))
                drawer_right = float(compact_inspector.property("x")) + float(
                    compact_inspector.property("width")
                )
                if (
                    not compact_inspector_open
                    or not bool(compact_inspector.property("enabled"))
                    or abs(drawer_right - width) > 2
                ):
                    raise RuntimeError(f"Compact inspector did not open at {width}x{height}")
            # QQuickWindow.grabWindow() captures the entire scene at device-pixel
            # resolution. QScreen.grabWindow() only returned the top-left physical
            # portion of a high-DPI offscreen window and could hide real clipping.
            image = quick_window.grabWindow()
            screenshot = root / f"{language}-{scale}x-{width}x{height}.png"
            if image.isNull() or not image.save(str(screenshot)):
                raise RuntimeError(f"Unable to render {screenshot}")
            results.append(
                {
                    "size": [width, height],
                    "rendered": [image.width(), image.height()],
                    "device_pixel_ratio": image.devicePixelRatio(),
                    "inspector_visible": inspector_visible,
                    "compact_inspector_visible": compact_inspector_visible,
                    "compact_inspector_open": compact_inspector_open,
                    "screenshot": str(screenshot),
                }
            )
            if compact_inspector_open:
                if not QMetaObject.invokeMethod(compact_inspector_button, "click"):
                    raise RuntimeError("Compact inspector button could not close the drawer")
                for _ in range(12):
                    QCoreApplication.processEvents()
                    time.sleep(0.02)
            for mode in TASK_FOCUSED_MODES:
                workspace.setProperty("activeMode", mode)
                for _ in range(4):
                    QCoreApplication.processEvents()
                    time.sleep(0.01)
                if float(tool_panel.property("width")) < 540:
                    raise RuntimeError(
                        f"Task panel is too narrow in {mode} mode at {width}x{height}"
                    )
                if bool(inspector.property("visible")):
                    raise RuntimeError(
                        f"Docked inspector consumes task space in {mode} mode at {width}x{height}"
                    )
                if bool(timeline.property("visible")) != (mode in TIMELINE_MODES):
                    raise RuntimeError(
                        f"Timeline relevance is wrong in {mode} mode at {width}x{height}"
                    )
            workspace.setProperty("activeMode", "media")
            for _ in range(4):
                QCoreApplication.processEvents()
                time.sleep(0.01)
        window.setWidth(1920)
        window.setHeight(1080)
        mode_results = []
        normal_tool_panel_width = 0.0
        for mode in WORK_MODES:
            workspace.setProperty("activeMode", mode)
            for _ in range(6):
                QCoreApplication.processEvents()
                time.sleep(0.02)
            advanced_open = False
            if mode == "export":
                advanced_toggle = workspace.findChild(QObject, "exportAdvancedToggle")
                advanced_section = workspace.findChild(QObject, "exportAdvancedSection")
                if advanced_toggle is None or advanced_section is None:
                    raise RuntimeError("Export advanced controls were not created")
                if bool(advanced_section.property("visible")):
                    raise RuntimeError("Export advanced settings should start collapsed")
                if not QMetaObject.invokeMethod(advanced_toggle, "click"):
                    raise RuntimeError("Export advanced settings could not be activated")
                for _ in range(6):
                    QCoreApplication.processEvents()
                    time.sleep(0.02)
                advanced_open = bool(advanced_section.property("visible"))
                if not advanced_open:
                    raise RuntimeError("Export advanced settings did not open")
            image = quick_window.grabWindow()
            screenshot = root / f"{language}-{scale}x-mode-{mode}.png"
            if image.isNull() or not image.save(str(screenshot)):
                raise RuntimeError(f"Unable to render {screenshot}")
            if workspace.property("activeMode") != mode or not tool_panel.property("visible"):
                raise RuntimeError(f"Workspace mode did not render: {mode}")
            task_focused = mode in TASK_FOCUSED_MODES
            expected_timeline = mode in TIMELINE_MODES
            expected_docked_inspector = not task_focused
            if bool(timeline.property("visible")) != expected_timeline:
                raise RuntimeError(f"Timeline relevance is wrong in {mode} mode")
            if bool(inspector.property("visible")) != expected_docked_inspector:
                raise RuntimeError(f"Docked inspector relevance is wrong in {mode} mode")
            if bool(compact_inspector.property("visible")) != task_focused:
                raise RuntimeError(f"Inspector drawer availability is wrong in {mode} mode")
            if bool(compact_inspector_button.property("visible")) != task_focused:
                raise RuntimeError(f"Inspector button availability is wrong in {mode} mode")
            panel_width = float(tool_panel.property("width"))
            if not task_focused:
                normal_tool_panel_width = max(normal_tool_panel_width, panel_width)
            elif panel_width < 540 or panel_width <= normal_tool_panel_width:
                raise RuntimeError(
                    f"Task panel did not receive primary space in {mode} mode: {panel_width}"
                )
            mode_results.append(
                {
                    "mode": mode,
                    "task_focused": task_focused,
                    "tool_panel_width": panel_width,
                    "timeline_visible": bool(timeline.property("visible")),
                    "inspector_visible": bool(inspector.property("visible")),
                    "advanced_open": advanced_open,
                    "screenshot": str(screenshot),
                }
            )

        settings_dialog = workspace.findChild(QObject, "settingsDialog")
        settings_tabs = workspace.findChild(QObject, "settingsTabs")
        auto_save_notice = workspace.findChild(QObject, "settingsAutoSaveNotice")
        auto_continue = workspace.findChild(QObject, "autoContinueSetting")
        settings_close = workspace.findChild(QObject, "settingsCloseButton")
        if any(
            item is None
            for item in (
                settings_dialog,
                settings_tabs,
                auto_save_notice,
                auto_continue,
                settings_close,
            )
        ):
            raise RuntimeError("Settings auto-save controls were not created")
        if not QMetaObject.invokeMethod(settings_dialog, "open"):
            raise RuntimeError("Settings dialog could not be opened")
        for _ in range(12):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        settings_results = []
        for index, tab_name in enumerate(SETTINGS_TABS):
            settings_tabs.setProperty("currentIndex", index)
            for _ in range(8):
                QCoreApplication.processEvents()
                time.sleep(0.02)
            image = quick_window.grabWindow()
            screenshot = root / f"{language}-{scale}x-settings-{tab_name}.png"
            if image.isNull() or not image.save(str(screenshot)):
                raise RuntimeError(f"Unable to render {screenshot}")
            settings_results.append({"tab": tab_name, "screenshot": str(screenshot)})
        if not bool(auto_save_notice.property("visible")):
            raise RuntimeError("Settings auto-save contract is not visible")
        previous_auto_continue = bool(auto_continue.property("checked"))
        settings_tabs.setProperty("currentIndex", 0)
        if not QMetaObject.invokeMethod(auto_continue, "click"):
            raise RuntimeError("Settings auto-save producer could not be activated")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            persisted = SettingsRepository(root / "runtime" / "settings.json").load()
            if persisted.workflow.auto_continue is not previous_auto_continue:
                break
            time.sleep(0.03)
        else:
            raise RuntimeError("Settings change did not reach persistent storage")
        if not QMetaObject.invokeMethod(settings_close, "click"):
            raise RuntimeError("Settings dialog could not be closed")

        translated = QCoreApplication.translate("HomeView", "新建项目")
        expected = {"zh_CN": "新建项目", "en": "New Project", "ja": "新規プロジェクト"}
        if translated != expected[language]:
            raise RuntimeError(f"Translation did not load: {translated!r}")
        return {
            "language": language,
            "scale": scale,
            "home": home_results,
            "sizes": results,
            "modes": mode_results,
            "settings": settings_results,
        }
    finally:
        controllers.shutdown()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()


def orchestrate(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=False)
    results = []
    for language in LANGUAGES:
        for scale in SCALES:
            scenario = root / f"{language}-{scale}x"
            scenario.mkdir()
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.verify_ui_matrix",
                    "--probe",
                    "--root",
                    str(scenario),
                    "--language",
                    language,
                    "--scale",
                    scale,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    f"UI matrix failed for {language}/{scale}x\n{completed.stdout}\n{completed.stderr}"
                )
            results.append(json.loads(completed.stdout))
    report = {
        "scenario_count": len(results),
        "render_count": len(results)
        * (len(HOME_SIZES) + len(SIZES) + len(WORK_MODES) + len(SETTINGS_TABS)),
        "results": results,
    }
    report_path = root / "ui-matrix-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**report, "report": str(report_path)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--language", choices=LANGUAGES)
    parser.add_argument("--scale", choices=SCALES)
    args = parser.parse_args()
    if args.probe:
        if args.root is None or args.language is None or args.scale is None:
            parser.error("--probe requires --root, --language, and --scale")
        print(json.dumps(probe(args.root, args.language, args.scale), ensure_ascii=False))
        return
    root = args.root or Path("D:/Tools/MediaFlow/test-runs") / (
        "ui-matrix-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    print(json.dumps(orchestrate(root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
