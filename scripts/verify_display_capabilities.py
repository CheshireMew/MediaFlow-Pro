# ruff: noqa: E402

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl
from PySide6.QtGui import QColorSpace, QGuiApplication, QSurfaceFormat
from PySide6.QtQml import QQmlApplicationEngine

from mediaflow.infrastructure.runtime_paths import RuntimePaths


def main() -> int:
    paths = RuntimePaths.discover()
    if paths.native_qml is None:
        raise RuntimeError("The native preview plugin is not built")
    surface_format = QSurfaceFormat.defaultFormat()
    surface_format.setColorSpace(QColorSpace(QColorSpace.SRgbLinear))
    QSurfaceFormat.setDefaultFormat(surface_format)
    _app = QGuiApplication([])
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(paths.native_qml))
    engine.loadData(
        b"""
import QtQuick
import QtQuick.Controls
import MediaFlow.Native 1.0
ApplicationWindow {
    visible: true
    width: 320
    height: 180
    x: 40
    y: 40
    MltPreviewItem { objectName: "preview"; anchors.fill: parent }
}
""",
        QUrl(),
    )
    if not engine.rootObjects():
        raise RuntimeError("The display capability window did not load")
    window = engine.rootObjects()[0]
    preview = window.findChild(QObject, "preview")
    if preview is None:
        raise RuntimeError("The native preview item was not created")
    for _ in range(10):
        QCoreApplication.processEvents()
        time.sleep(0.02)
    preview.setProperty("hdrEnabled", True)
    for _ in range(10):
        QCoreApplication.processEvents()
        time.sleep(0.02)
    screen = window.screen()
    report = {
        "screen_name": screen.name(),
        "manufacturer": screen.manufacturer(),
        "model": screen.model(),
        "logical_dpi": screen.logicalDotsPerInch(),
        "device_pixel_ratio": screen.devicePixelRatio(),
        "depth": screen.depth(),
        "hdr_requested": bool(preview.property("hdrEnabled")),
        "hdr_active": bool(preview.property("hdrActive")),
        "validation_status": (
            "hdr_monitor_path_available"
            if bool(preview.property("hdrActive"))
            else "current_monitor_or_windows_hdr_mode_is_not_hdr10"
        ),
    }
    run_dir = Path("D:/Tools/MediaFlow/test-runs") / (
        "display-capabilities-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    report_path = run_dir / "display-capabilities-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    window.close()
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QCoreApplication.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
