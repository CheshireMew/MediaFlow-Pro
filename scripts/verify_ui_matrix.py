# ruff: noqa: E402

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediaflow.atomic_file import atomic_write_text
from mediaflow.desktop.presentation_catalogs import (
    WORKSPACE_MODE_KEYS,
    WORKSPACE_NAVIGATION_MODE_KEYS,
)
from scripts.run_artifacts import verification_run, verification_workspace_root

LANGUAGES = ("zh_CN", "en", "ja")
SCALES = ("1", "1.5", "2")
HOME_SIZES = ((1280, 720), (1600, 980))
SIZES = ((1280, 720), (1920, 1080), (3840, 2160))
SETTINGS_TABS = ("general", "download", "ai")
UI_MATRIX_WORKERS = 3


def probe(root: Path, language: str, scale: str) -> dict:
    workspace = verification_workspace_root(root)
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = scale
    os.environ["MEDIAFLOW_SERVICE_SETTINGS_PATH"] = str(workspace / "settings" / "service-settings.json")
    os.environ["MEDIAFLOW_DESKTOP_SETTINGS_PATH"] = str(workspace / "settings" / "desktop-settings.json")
    os.environ["MEDIAFLOW_MEDIA_ROOT"] = str(workspace / "media")
    os.environ["MEDIAFLOW_PROJECT_ROOT"] = str(workspace / "projects")
    os.environ["MEDIAFLOW_SERVICE_STATE_DIR"] = str(workspace / "editor-service")

    from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QPointF, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickItem, QQuickWindow
    from shiboken6 import getCppPointer, wrapInstance

    from mediaflow.desktop.app import (
        configure_application_font,
        configure_application_identity,
        create_engine,
    )
    from mediaflow.domain.settings import DesktopSettings, ServiceSettings
    from mediaflow.infrastructure.settings_repository import (
        DesktopSettingsRepository,
        ServiceSettingsRepository,
    )
    from mediaflow.service.client import shutdown_sync_service

    service_settings = ServiceSettings()
    desktop_settings = DesktopSettings()
    desktop_settings.ui.language = language
    service_settings_path = workspace / "settings" / "service-settings.json"
    desktop_settings_path = workspace / "settings" / "desktop-settings.json"
    ServiceSettingsRepository(service_settings_path).save(service_settings)
    DesktopSettingsRepository(desktop_settings_path).save(desktop_settings)
    configure_application_identity()
    app = QGuiApplication([])
    if QCoreApplication.applicationName() != "MediaFlow Pro":
        raise RuntimeError("UI verifier did not use the production application identity")
    configure_application_font(app)
    engine, controllers = create_engine(app)
    workspace_controller = controllers.workspace
    workspace_project_controller = controllers.workspace_project

    def visual_items(item: QQuickItem):
        for child in item.childItems():
            yield child
            yield from visual_items(child)

    def horizontal_bounds(item: QQuickItem, ancestor: QQuickItem) -> tuple[float, float]:
        origin = item.mapToItem(ancestor, QPointF(0, 0))
        return origin.x(), origin.x() + float(item.property("width"))

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
        status_message = window.findChild(QObject, "workspaceStatusMessage")
        if any(
            item is None
            for item in (
                title_bar,
                project_name,
                minimize_button,
                maximize_button,
                close_button,
                status_message,
            )
        ):
            raise RuntimeError("Custom window title bar controls were not created")

        workspace_project_controller.createProject(
            QUrl.fromLocalFile(str(workspace / "projects")).toString(),
            "UI Matrix",
        )
        project_path = Path(workspace_controller.projectPath)
        workspace_project_controller.closeProject()
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

        workspace_project_controller.openProject(QUrl.fromLocalFile(str(project_path)).toString())
        for _ in range(12):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        expected_status = {
            "zh_CN": "项目已打开",
            "en": "Project opened",
            "ja": "プロジェクトを開きました",
        }
        if status_message.property("text") != expected_status[language]:
            raise RuntimeError(
                "Runtime status did not use the active translation catalog: "
                f"{status_message.property('text')!r}"
            )
        expected_sequence_name = {
            "zh_CN": "主序列",
            "en": "Main Sequence",
            "ja": "メインシーケンス",
        }
        main_sequence = workspace_controller.sequencesModel.get(0)
        if main_sequence.get("displayName") != expected_sequence_name[language]:
            raise RuntimeError(
                f"System sequence name did not use the active translation catalog: {main_sequence!r}"
            )
        for width, height in SIZES:
            window.setWidth(width)
            window.setHeight(height)
            for _ in range(12):
                QCoreApplication.processEvents()
                time.sleep(0.02)
            workspace = loader.property("item")
            navigation = workspace.findChild(QObject, "workspaceNavigation")
            tool_panel = workspace.findChild(QObject, "toolPanelContainer")
            preview_panel = workspace.findChild(QObject, "previewPanel")
            preview_viewport = workspace.findChild(QObject, "previewViewport")
            preview_control_bar = workspace.findChild(QObject, "previewControlBar")
            inspector = workspace.findChild(QObject, "inspectorPanel")
            timeline = workspace.findChild(QObject, "timelinePanel")
            timeline_toolbar = workspace.findChild(QObject, "timelineToolbarScroll")
            compact_icon_buttons = [
                window.findChild(QObject, object_name)
                for object_name in (
                    "workspaceUndoButton",
                    "workspaceRedoButton",
                    "openProjectVersionsButton",
                    "timelineSplitButton",
                    "timelineDuplicateButton",
                    "timelineDeleteButton",
                )
            ]
            if (
                navigation is None
                or tool_panel is None
                or preview_panel is None
                or preview_viewport is None
                or preview_control_bar is None
                or inspector is None
                or timeline is None
            ):
                raise RuntimeError("Primary workspace controls were not created")
            if any(button is None for button in compact_icon_buttons):
                raise RuntimeError("Primary compact icon buttons were not created")
            if any(
                int(button.property("iconSize")) > 16 or float(button.property("implicitWidth")) > 32
                for button in compact_icon_buttons
            ):
                raise RuntimeError("Toolbar icons regressed to oversized controls")
            for retired_name in (
                "compactInspectorDrawer",
                "compactInspectorButton",
            ):
                if workspace.findChild(QObject, retired_name) is not None:
                    raise RuntimeError(f"Retired inspector control still exists: {retired_name}")
            tool_panel_visible = bool(tool_panel.property("visible"))
            timeline_visible = bool(timeline.property("visible"))
            if not tool_panel_visible or not timeline_visible or timeline_toolbar is None:
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
                    raise RuntimeError(f"Timeline overflow controls are unreachable at {width}x{height}")
                timeline_toolbar.setProperty("contentX", 0)
            if abs(float(navigation.property("width")) - float(tool_panel.property("width"))) > 2:
                raise RuntimeError(f"Workspace navigation does not match the tool pane at {width}x{height}")
            if not bool(inspector.property("visible")):
                raise RuntimeError(f"Persistent inspector is hidden at {width}x{height}")
            gutter = float(workspace.property("workspaceGutter"))
            panel_items = (tool_panel, preview_panel, inspector, timeline)
            if any(float(panel.property("radius")) < 8 for panel in panel_items):
                raise RuntimeError(f"Workspace panels lost their rounded boundary at {width}x{height}")
            tool_origin = tool_panel.mapToItem(workspace, QPointF(0, 0))
            preview_origin = preview_panel.mapToItem(workspace, QPointF(0, 0))
            inspector_origin = inspector.mapToItem(workspace, QPointF(0, 0))
            timeline_origin = timeline.mapToItem(workspace, QPointF(0, 0))
            if (
                abs(tool_origin.x() - gutter) > 2
                or abs(timeline_origin.x() - gutter) > 2
                or abs(preview_origin.x() - tool_origin.x() - float(tool_panel.property("width")) - gutter)
                > 2
                or abs(
                    inspector_origin.x()
                    - preview_origin.x()
                    - float(preview_panel.property("width"))
                    - gutter
                )
                > 2
            ):
                raise RuntimeError(f"Workspace card gutters drifted at {width}x{height}")
            if (
                abs(
                    float(preview_control_bar.property("y"))
                    + float(preview_control_bar.property("height"))
                    - float(preview_viewport.property("height"))
                )
                > 2
            ):
                raise RuntimeError(f"Preview controls are not docked at {width}x{height}")
            if (
                not bool(project_name.property("visible"))
                or float(project_name.property("width")) < 80
                or project_name.property("text") != "UI Matrix"
            ):
                raise RuntimeError(f"Project name is not visible at {width}x{height}")
            project_left, project_right = horizontal_bounds(project_name, title_bar)
            status_left, _status_right = horizontal_bounds(status_message, title_bar)
            if project_right + 8 > status_left:
                raise RuntimeError(
                    "Title-bar project name overlaps the workspace status at "
                    f"{width}x{height}: project={project_left:.1f}..{project_right:.1f}, "
                    f"status starts at {status_left:.1f}"
                )
            navigation_items = {item.objectName(): item for item in visual_items(navigation)}
            for mode in WORKSPACE_NAVIGATION_MODE_KEYS:
                navigation_item = navigation_items.get(f"navigationItem_{mode}")
                if navigation_item is None or not navigation_item.isVisible():
                    raise RuntimeError(f"Persistent navigation label is missing for {mode}")
            if (width, height) == (1280, 720):
                empty_descriptions = [
                    item
                    for item in visual_items(tool_panel)
                    if item.objectName() == "emptyStateDescription" and item.isVisible()
                ]
                if not empty_descriptions:
                    raise RuntimeError("Media empty-state description is missing at minimum size")
                description = empty_descriptions[0]
                description_origin = description.mapToItem(tool_panel, QPointF(0, 0))
                if (
                    description_origin.y() + float(description.property("height"))
                    > float(tool_panel.property("height")) - 4
                ):
                    raise RuntimeError("Media empty-state description is clipped at minimum size")
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
            for mode in WORKSPACE_MODE_KEYS:
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

        window.setWidth(1280)
        window.setHeight(720)
        source_path = ROOT / "tests" / "fixtures" / "media-timeline-v1-project" / "sources" / "moving.mp4"
        controllers.media.importFiles([QUrl.fromLocalFile(str(source_path))])
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and controllers.media.assetsModel.rowCount() == 0:
            QCoreApplication.processEvents()
            time.sleep(0.02)
        if controllers.media.assetsModel.rowCount() == 0:
            raise RuntimeError("Dynamic minimum-size probe could not import its source media")
        asset_id = controllers.media.assetsModel.get(0)["assetId"]
        controllers.media.openSourceMonitor(asset_id)
        source_tab = workspace.findChild(QObject, "sourceMonitorTab")
        if source_tab is None or not QMetaObject.invokeMethod(source_tab, "click"):
            raise RuntimeError("Dynamic minimum-size probe could not open the source monitor")
        for _ in range(20):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        source_insert = workspace.findChild(QObject, "sourceInsertRangeButton")
        source_menu = workspace.findChild(QObject, "sourceActionsMenuButton")
        workflow_banner = workspace.findChild(QObject, "workflowBanner")
        workflow_menu = workspace.findChild(QObject, "workflowCompactMenuButton")
        if any(item is None for item in (source_insert, source_menu, workflow_banner, workflow_menu)):
            raise RuntimeError("Responsive dynamic-state controls were not created")
        for item, ancestor, label in (
            (source_insert, preview_panel, "source insert action"),
            (source_menu, preview_panel, "source overflow action"),
            (workflow_menu, workflow_banner, "workflow overflow action"),
        ):
            left, right = horizontal_bounds(item, ancestor)
            if not item.isVisible() or left < -1 or right > float(ancestor.property("width")) + 1:
                raise RuntimeError(f"{label} is unreachable at 1280x720: {left:.1f}..{right:.1f}")
        dynamic_image = quick_window.grabWindow()
        dynamic_screenshot = root / f"{language}-{scale}x-dynamic-source-workflow-1280x720.png"
        if dynamic_image.isNull() or not dynamic_image.save(str(dynamic_screenshot)):
            raise RuntimeError(f"Unable to render {dynamic_screenshot}")
        controllers.session.updates.report_error("UI matrix retained error")
        for _ in range(6):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        workspace.setProperty("activeMode", "tasks")
        for _ in range(8):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        error_history_button = workspace.findChild(QObject, "openErrorHistoryButton")
        if (
            len(workspace_controller.recentErrors) != 1
            or error_history_button is None
            or not error_history_button.isVisible()
        ):
            raise RuntimeError("Retained error history is not reachable from the task center")
        workspace_controller.clearErrorHistory()
        error_popup = window.findChild(QObject, "globalErrorPopup")
        if error_popup is not None:
            QMetaObject.invokeMethod(error_popup, "close")
        workspace.setProperty("activeMode", "media")
        dynamic_result = {
            "size": [1280, 720],
            "source_insert_visible": bool(source_insert.property("visible")),
            "source_overflow_visible": bool(source_menu.property("visible")),
            "workflow_overflow_visible": bool(workflow_menu.property("visible")),
            "error_history_reachable": True,
            "screenshot": str(dynamic_screenshot),
        }
        window.setWidth(1920)
        window.setHeight(1080)
        mode_results = []
        normal_tool_panel_width = float(tool_panel.property("width"))
        for mode in WORKSPACE_MODE_KEYS:
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
            persisted = ServiceSettingsRepository(service_settings_path).load()
            if persisted.workflow.auto_continue is not previous_auto_continue:
                break
            time.sleep(0.03)
        else:
            raise RuntimeError("Settings change did not reach persistent storage")
        if not QMetaObject.invokeMethod(settings_close, "click"):
            raise RuntimeError("Settings dialog could not be closed")

        shortcut_dialog = window.findChild(QObject, "shortcutReferenceDialog")
        shortcut_search = window.findChild(QObject, "shortcutSearchField")
        shortcut_list = window.findChild(QObject, "shortcutReferenceList")
        if any(item is None for item in (shortcut_dialog, shortcut_search, shortcut_list)):
            raise RuntimeError("Shortcut reference controls were not created")
        if not QMetaObject.invokeMethod(shortcut_dialog, "open"):
            raise RuntimeError("Shortcut reference could not be opened")
        for _ in range(8):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        if not shortcut_dialog.property("visible") or int(shortcut_list.property("count")) < 20:
            raise RuntimeError("Shortcut reference does not expose the complete command list")
        QMetaObject.invokeMethod(shortcut_dialog, "close")

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
            "dynamic": dynamic_result,
        }
    finally:
        try:
            controllers.shutdown()
        finally:
            shutdown_sync_service()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.processEvents()


def _run_scenario(scenario: Path, language: str, scale: str) -> tuple[dict, float]:
    started = time.perf_counter()
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
    return json.loads(completed.stdout), time.perf_counter() - started


def orchestrate(root: Path) -> dict:
    scenarios = [
        (root / f"{language}-{scale}x", language, scale) for language in LANGUAGES for scale in SCALES
    ]
    for scenario, _language, _scale in scenarios:
        scenario.mkdir()
    worker_count = min(UI_MATRIX_WORKERS, len(scenarios))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(_run_scenario, scenario, language, scale)
            for scenario, language, scale in scenarios
        ]
        completed_scenarios = [future.result() for future in futures]
    results = [result for result, _seconds in completed_scenarios]
    report = {
        "scenario_count": len(results),
        "worker_count": worker_count,
        "scenario_seconds": [round(seconds, 3) for _result, seconds in completed_scenarios],
        "render_count": len(results)
        * (len(HOME_SIZES) + len(SIZES) + len(WORKSPACE_MODE_KEYS) + len(SETTINGS_TABS) + 1),
        "results": results,
    }
    report_path = root / "ui-matrix-report.json"
    atomic_write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2))
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
    with verification_run("ui-matrix", explicit_root=args.root) as run_dir:
        print(json.dumps(orchestrate(run_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
