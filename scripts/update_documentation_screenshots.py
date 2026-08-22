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
    png_region_metrics,
)
from scripts.run_artifacts import verification_run

WINDOWS_PATH = re.compile(r"(?:^|\s)[A-Za-z]:[\\/]")


def _configure_environment(run_dir: Path) -> None:
    # The native MLT preview item needs a real Windows scene-graph backend.
    # Offscreen QML is sufficient for layout tests but produces a black native
    # surface and therefore cannot be used as product screenshot evidence.
    os.environ["QT_QPA_PLATFORM"] = "windows" if os.name == "nt" else "offscreen"
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


def _grab_complete_window(quick_window):
    # QQuickWindow.grabWindow() forces the current scene graph to render.  A
    # Win32 window capture can lag behind Loader page changes when this script
    # drives Qt with processEvents() instead of entering app.exec().  The real
    # Windows scene-graph backend still keeps the native preview texture in the
    # grab, unlike the offscreen backend rejected above.
    image = quick_window.grabWindow()
    if not image.isNull():
        return image
    if os.name == "nt":
        screen = quick_window.screen()
        if screen is not None:
            pixmap = screen.grabWindow(int(quick_window.winId()))
            if not pixmap.isNull():
                return pixmap.toImage()
    return image


def _save_screenshot(
    image,
    relative_path: str,
    *,
    visual_regions: dict[str, tuple[int, int, int, int]] | None = None,
) -> dict[str, object]:
    destination = ROOT / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_name(f".{destination.name}.staged")
    if image.isNull() or not image.save(str(staged), "PNG"):
        raise RuntimeError(f"Unable to render {destination}")
    os.replace(staged, destination)
    width, height = png_dimensions(destination)
    record: dict[str, object] = {
        "path": relative_path,
        "scenario": DOCUMENTATION_SCREENSHOTS[relative_path],
        "width": width,
        "height": height,
        "sha256": file_sha256(destination),
        "local_paths_exposed": False,
    }
    if visual_regions:
        record["visual_assertions"] = {
            name: png_region_metrics(
                destination,
                x=region[0],
                y=region[1],
                width=region[2],
                height=region[3],
            )
            for name, region in visual_regions.items()
        }
    return record


def _qimage_region_is_real_content(
    image,
    region: tuple[int, int, int, int],
) -> bool:
    x, y, width, height = region
    colors: set[tuple[int, int, int]] = set()
    non_dark = 0
    samples = 0
    luminance_min = 255
    luminance_max = 0
    for row in range(y, y + height, 8):
        for column in range(x, x + width, 8):
            color = image.pixelColor(column, row)
            value = (color.red(), color.green(), color.blue())
            colors.add(value)
            luminance = (54 * value[0] + 183 * value[1] + 19 * value[2]) // 256
            luminance_min = min(luminance_min, luminance)
            luminance_max = max(luminance_max, luminance)
            non_dark += int(max(value) >= 28)
            samples += 1
    return (
        samples > 0
        and non_dark / samples >= 0.55
        and len(colors) >= 24
        and luminance_max - luminance_min >= 40
    )


def update_screenshots(run_dir: Path) -> list[dict[str, object]]:
    _configure_environment(run_dir)

    from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QObject, QPointF
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
                _grab_complete_window(quick_window),
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
        preview_viewport = workspace.findChild(QQuickItem, "previewViewport")
        preview_player = workspace.findChild(QQuickItem, "previewPlayer")
        preview_surface = workspace.findChild(QQuickItem, "previewSurface")
        if preview_viewport is None or preview_player is None or preview_surface is None:
            raise RuntimeError("Sample project program monitor did not load")
        if not _wait(
            QCoreApplication.processEvents,
            lambda: bool(controllers.workspace.previewGraphPath)
            and Path(controllers.workspace.previewGraphPath).is_file()
            and preview_player.property("duration")
            == controllers.workspace.timelineDurationFrames
            and not preview_player.property("errorString"),
            timeout=30,
        ):
            raise RuntimeError("Sample project program monitor did not become ready")
        preview_frame = min(
            controllers.workspace.timelineDurationFrames - 1,
            60,
        )
        preview_viewport.seek(preview_frame)
        if not _wait(
            QCoreApplication.processEvents,
            lambda: preview_player.property("position") == preview_frame,
            timeout=30,
        ):
            raise RuntimeError("Sample project program monitor did not seek to the proof frame")
        origin = preview_surface.mapToScene(QPointF(0, 0))
        proof_image = _grab_complete_window(quick_window)
        horizontal_scale = proof_image.width() / quick_window.width()
        vertical_scale = proof_image.height() / quick_window.height()
        monitor_region = (
            round((origin.x() + 4) * horizontal_scale),
            round((origin.y() + 4) * vertical_scale),
            max(1, round((preview_surface.width() - 8) * horizontal_scale)),
            max(1, round((preview_surface.height() - 8) * vertical_scale)),
        )
        if not _wait(
            QCoreApplication.processEvents,
            lambda: _qimage_region_is_real_content(
                _grab_complete_window(quick_window),
                monitor_region,
            ),
            timeout=30,
        ):
            failed_image = _grab_complete_window(quick_window)
            failure_evidence = run_dir / "workspace-program-monitor-failure.png"
            if not failed_image.save(str(failure_evidence), "PNG"):
                raise RuntimeError(
                    "Sample project program monitor remained blank and its diagnostic "
                    "screenshot could not be saved"
                )
            x, y, width, height = monitor_region
            sample_colors = [
                failed_image.pixelColor(
                    x + round(width * horizontal),
                    y + round(height * vertical),
                ).name()
                for horizontal, vertical in (
                    (0.1, 0.1),
                    (0.5, 0.5),
                    (0.9, 0.9),
                )
            ]
            raise RuntimeError(
                "Sample project program monitor remained blank: "
                f"image={failed_image.width()}x{failed_image.height()}, "
                f"region={monitor_region}, samples={sample_colors}, "
                f"player_size={preview_player.width()}x{preview_player.height()}, "
                f"player_visible={preview_player.isVisible()}, "
                f"player_source={preview_player.property('source')!r}, "
                f"evidence={failure_evidence}"
            )
        _assert_no_visible_local_path(workspace, QQuickItem)
        workspace_record = _save_screenshot(
            _grab_complete_window(quick_window),
            "docs/images/mediaflow-workspace-zh-cn.png",
            visual_regions={"program_monitor": monitor_region},
        )
        program_monitor = workspace_record["visual_assertions"]["program_monitor"]
        program_monitor["ready"] = True
        program_monitor["frame"] = preview_frame
        images.append(workspace_record)
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
