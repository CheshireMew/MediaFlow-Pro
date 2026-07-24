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
WORK_MODES = ("media", "transcript", "highlight", "edit", "audio", "export", "tasks")
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
        project_name = window.findChild(QObject, "windowProjectName")
        minimize_button = window.findChild(QObject, "minimizeWindowButton")
        maximize_button = window.findChild(QObject, "maximizeWindowButton")
        close_button = window.findChild(QObject, "closeWindowButton")
        if any(
            item is None
            for item in (
                title_bar,
                project_name,
                minimize_button,
                maximize_button,
                close_button,
            )
        ):
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
            navigation = workspace.findChild(QObject, "workspaceNavigation")
            tool_panel = workspace.findChild(QObject, "toolPanelContainer")
            timeline = workspace.findChild(QObject, "timelinePanel")
            timeline_toolbar = workspace.findChild(QObject, "timelineToolbarScroll")
            if navigation is None or tool_panel is None or timeline is None:
                raise RuntimeError("Primary workspace controls were not created")
            for retired_name in (
                "inspectorContainer",
                "compactInspectorDrawer",
                "compactInspectorButton",
            ):
                if workspace.findChild(QObject, retired_name) is not None:
                    raise RuntimeError(f"Retired inspector control still exists: {retired_name}")
            tool_panel_visible = bool(tool_panel.property("visible"))
            timeline_visible = bool(timeline.property("visible"))
            if (
                not tool_panel_visible
                or not timeline_visible
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
            if abs(float(navigation.property("width")) - float(workspace.property("width"))) > 2:
                raise RuntimeError(f"Workspace navigation is not full width at {width}x{height}")
            if (
                not bool(project_name.property("visible"))
                or float(project_name.property("width")) < 80
                or project_name.property("text") != "UI Matrix"
            ):
                raise RuntimeError(f"Project name is not visible at {width}x{height}")
            navigation_items = {
                item.objectName(): item for item in visual_items(navigation)
            }
            for mode in WORK_MODES:
                navigation_item = navigation_items.get(f"navigationItem_{mode}")
                if navigation_item is None or not navigation_item.isVisible():
                    raise RuntimeError(f"Persistent navigation label is missing for {mode}")
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
                    "screenshot": str(screenshot),
                }
            )
            base_panel_width = float(tool_panel.property("width"))
            for mode in WORK_MODES:
                workspace.setProperty("activeMode", mode)
                for _ in range(4):
                    QCoreApplication.processEvents()
                    time.sleep(0.01)
                if abs(float(tool_panel.property("width")) - base_panel_width) > 2:
                    raise RuntimeError(f"Tool panel geometry changed in {mode} mode")
                if not bool(timeline.property("visible")):
                    raise RuntimeError(f"Timeline is hidden in {mode} mode at {width}x{height}")
            workspace.setProperty("activeMode", "media")
            for _ in range(4):
                QCoreApplication.processEvents()
                time.sleep(0.01)
        window.setWidth(1920)
        window.setHeight(1080)
        mode_results = []
        normal_tool_panel_width = float(tool_panel.property("width"))
        for mode in WORK_MODES:
            workspace.setProperty("activeMode", mode)
            for _ in range(6):
                QCoreApplication.processEvents()
                time.sleep(0.02)
            if (
                not bool(project_name.property("visible"))
                or float(project_name.property("width")) < 80
                or project_name.property("text") != "UI Matrix"
            ):
                raise RuntimeError(f"Project name is not visible in {mode} mode")
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
            if not bool(timeline.property("visible")):
                raise RuntimeError(f"Timeline is hidden in {mode} mode")
            panel_width = float(tool_panel.property("width"))
            if abs(panel_width - normal_tool_panel_width) > 2:
                raise RuntimeError(f"Tool panel geometry changed in {mode} mode")
            mode_results.append(
                {
                    "mode": mode,
                    "tool_panel_width": panel_width,
                    "timeline_visible": bool(timeline.property("visible")),
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
