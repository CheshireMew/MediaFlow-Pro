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
SIZES = ((1280, 720), (1920, 1080), (3840, 2160))
WORK_MODES = ("media", "transcript", "translate", "highlight", "edit", "audio", "export")


def probe(root: Path, language: str, scale: str) -> dict:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = scale
    os.environ["MEDIAFLOW_RUNTIME_DIR"] = str(root / "runtime")

    from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickWindow
    from shiboken6 import getCppPointer, wrapInstance

    from mediaflow.desktop.app import create_engine, load_application_font
    from mediaflow.domain.settings import GlobalSettings
    from mediaflow.infrastructure.settings_repository import SettingsRepository

    settings = GlobalSettings()
    settings.ui.language = language
    SettingsRepository(root / "runtime" / "settings.json").save(settings)
    app = QGuiApplication([])
    load_application_font(app)
    engine, controller = create_engine(app)
    results = []
    try:
        controller.createProject(QUrl.fromLocalFile(str(root)).toString(), "UI Matrix")
        window = engine.rootObjects()[0]
        quick_window = wrapInstance(getCppPointer(window)[0], QQuickWindow)
        loader = window.findChild(QObject, "pageLoader")
        for width, height in SIZES:
            window.setWidth(width)
            window.setHeight(height)
            for _ in range(12):
                QCoreApplication.processEvents()
                time.sleep(0.02)
            workspace = loader.property("item")
            inspector = workspace.findChild(QObject, "inspectorContainer")
            tool_panel = workspace.findChild(QObject, "toolPanelContainer")
            timeline = workspace.findChild(QObject, "timelinePanel")
            workflow_mode = workspace.findChild(QObject, "workflowMode")
            # QQuickWindow.grabWindow() captures the entire scene at device-pixel
            # resolution. QScreen.grabWindow() only returned the top-left physical
            # portion of a high-DPI offscreen window and could hide real clipping.
            image = quick_window.grabWindow()
            screenshot = root / f"{language}-{scale}x-{width}x{height}.png"
            if image.isNull() or not image.save(str(screenshot)):
                raise RuntimeError(f"Unable to render {screenshot}")
            tool_panel_visible = bool(tool_panel.property("visible"))
            timeline_visible = bool(timeline.property("visible"))
            workflow_mode_visible = bool(workflow_mode.property("visible"))
            inspector_visible = bool(inspector.property("visible"))
            if not tool_panel_visible or not timeline_visible or not workflow_mode_visible:
                raise RuntimeError(f"A primary workspace control is hidden at {width}x{height}")
            expected_inspector = width >= 1320
            if inspector_visible != expected_inspector:
                raise RuntimeError(f"Inspector responsive state is wrong at {width}x{height}")
            results.append(
                {
                    "size": [width, height],
                    "rendered": [image.width(), image.height()],
                    "device_pixel_ratio": image.devicePixelRatio(),
                    "inspector_visible": inspector_visible,
                    "screenshot": str(screenshot),
                }
            )
        window.setWidth(1920)
        window.setHeight(1080)
        mode_results = []
        for mode in WORK_MODES:
            workspace.setProperty("activeMode", mode)
            for _ in range(6):
                QCoreApplication.processEvents()
                time.sleep(0.02)
            image = quick_window.grabWindow()
            screenshot = root / f"{language}-{scale}x-mode-{mode}.png"
            if image.isNull() or not image.save(str(screenshot)):
                raise RuntimeError(f"Unable to render {screenshot}")
            if workspace.property("activeMode") != mode or not tool_panel.property("visible"):
                raise RuntimeError(f"Workspace mode did not render: {mode}")
            mode_results.append({"mode": mode, "screenshot": str(screenshot)})

        translated = QCoreApplication.translate("HomeView", "新建项目")
        expected = {"zh_CN": "新建项目", "en": "New Project", "ja": "新規プロジェクト"}
        if translated != expected[language]:
            raise RuntimeError(f"Translation did not load: {translated!r}")
        return {
            "language": language,
            "scale": scale,
            "sizes": results,
            "modes": mode_results,
        }
    finally:
        controller.shutdown()
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
                    f"UI matrix failed for {language}/{scale}x\n"
                    f"{completed.stdout}\n{completed.stderr}"
                )
            results.append(json.loads(completed.stdout))
    report = {
        "scenario_count": len(results),
        "render_count": len(results) * (len(SIZES) + len(WORK_MODES)),
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
