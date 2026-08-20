# ruff: noqa: E402

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediaflow.atomic_file import atomic_write_text
from scripts.documentation_screenshot_contract import (
    DOCUMENTATION_SCREENSHOTS,
    MANIFEST_PATH,
    documentation_ui_digest,
    file_sha256,
    png_dimensions,
)
from scripts.run_artifacts import verification_run

WINDOWS_PATH = re.compile(r"(?:^|\s)[A-Za-z]:[\\/]")


def _configure_environment(run_dir: Path) -> None:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_SCALE_FACTOR"] = "1"
    os.environ["MEDIAFLOW_SERVICE_SETTINGS_PATH"] = str(
        run_dir / "settings/service-settings.json"
    )
    os.environ["MEDIAFLOW_DESKTOP_SETTINGS_PATH"] = str(
        run_dir / "settings/desktop-settings.json"
    )
    os.environ["MEDIAFLOW_MEDIA_ROOT"] = str(run_dir / "media")
    os.environ["MEDIAFLOW_PROJECT_ROOT"] = str(run_dir / "projects")
    os.environ["MEDIAFLOW_SERVICE_STATE_DIR"] = str(run_dir / "editor-service")


def _wait(process_events, predicate, *, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        process_events()
        if predicate():
            return True
        time.sleep(0.02)
    process_events()
    return bool(predicate())


def _assert_no_visible_local_path(root, quick_item_type) -> None:
    exposed: list[str] = []
    for item in [root, *root.findChildren(quick_item_type)]:
        if not item.isVisible():
            continue
        text = item.property("text")
        if isinstance(text, str) and (WINDOWS_PATH.search(text) or "file:///" in text.lower()):
            exposed.append(text)
    if exposed:
        raise RuntimeError(f"Documentation screenshot exposes local paths: {exposed}")


def _save_screenshot(image, relative_path: str) -> dict[str, object]:
    destination = ROOT / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_name(f".{destination.name}.staged")
    if image.isNull() or not image.save(str(staged), "PNG"):
        raise RuntimeError(f"Unable to render {destination}")
    os.replace(staged, destination)
    width, height = png_dimensions(destination)
    return {
        "path": relative_path,
        "scenario": DOCUMENTATION_SCREENSHOTS[relative_path],
        "width": width,
        "height": height,
        "sha256": file_sha256(destination),
        "local_paths_exposed": False,
    }


def update_screenshots(run_dir: Path) -> list[dict[str, object]]:
    _configure_environment(run_dir)

    from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject
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

    service_settings = ServiceSettings(default_project_directory=str(run_dir / "projects"))
    desktop_settings = DesktopSettings()
    desktop_settings.ui.language = "zh_CN"
    desktop_settings.ui.workspace_tour_completed = True
    ServiceSettingsRepository().save(service_settings)
    DesktopSettingsRepository().save(desktop_settings)

    configure_application_identity()
    app = QGuiApplication([])
    configure_application_font(app)
    engine, controllers = create_engine(app)
    images: list[dict[str, object]] = []
    try:
        window = engine.rootObjects()[0]
        quick_window = wrapInstance(getCppPointer(window)[0], QQuickWindow)
        loader = window.findChild(QObject, "pageLoader")
        if loader is None or not _wait(QCoreApplication.processEvents, lambda: loader.property("item")):
            raise RuntimeError("Home page did not load")

        window.setWidth(1600)
        window.setHeight(900)
        if not _wait(
            QCoreApplication.processEvents,
            lambda: controllers.workspace.recentProjectsModel.rowCount() == 0,
        ):
            raise RuntimeError("Documentation home screenshot must not contain recent projects")
        home = loader.property("item")
        _assert_no_visible_local_path(home, QQuickItem)
        images.append(
            _save_screenshot(
                quick_window.grabWindow(),
                "docs/images/mediaflow-home-zh-cn.png",
            )
        )

        sample_button = home.findChild(QQuickItem, "createSampleProjectButton")
        if sample_button is None or not QMetaObject.invokeMethod(sample_button, "click"):
            raise RuntimeError("Sample project button is unavailable")
        if not _wait(
            QCoreApplication.processEvents,
            lambda: controllers.workspace.hasProject and loader.property("item") is not home,
        ):
            raise RuntimeError("Sample project did not open")

        workspace = loader.property("item")
        tour = window.findChild(QQuickItem, "workspaceTour")
        if tour is not None and tour.isVisible():
            if not QMetaObject.invokeMethod(tour, "finish"):
                raise RuntimeError("Workspace tour could not be dismissed")
            if not _wait(QCoreApplication.processEvents, lambda: not tour.isVisible()):
                raise RuntimeError("Workspace tour remained visible")
        controllers.settings.setWorkspaceLayoutPreset("standard")
        clip_model = controllers.timeline_view.clipsModel
        if clip_model.rowCount() < 2:
            raise RuntimeError("Sample project did not create the expected timeline")
        controllers.timeline_view.selectClip(clip_model.get(1)["clipId"])
        window.setWidth(1920)
        window.setHeight(1080)
        if not _wait(
            QCoreApplication.processEvents,
            lambda: workspace.findChild(QQuickItem, "visualEffectStackPanel") is not None,
        ):
            raise RuntimeError("Sample project inspector did not load")
        _assert_no_visible_local_path(workspace, QQuickItem)
        images.append(
            _save_screenshot(
                quick_window.grabWindow(),
                "docs/images/mediaflow-workspace-zh-cn.png",
            )
        )
    finally:
        controllers.shutdown()
        shutdown_sync_service()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.processEvents()
    return images


def main() -> int:
    with verification_run("documentation-screenshots") as run_dir:
        images = update_screenshots(run_dir)
        manifest = {
            "schema": "mediaflow-documentation-screenshots/v1",
            "generator": "scripts/update_documentation_screenshots.py",
            "ui_source_digest": documentation_ui_digest(),
            "images": images,
        }
        atomic_write_text(MANIFEST_PATH, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        receipt = run_dir / "documentation-screenshot-receipt.json"
        atomic_write_text(receipt, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        print(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
