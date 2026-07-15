from __future__ import annotations

import multiprocessing
import os
import sys
from contextlib import contextmanager
from ctypes import WinDLL, create_unicode_buffer
from pathlib import Path

os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
os.environ.setdefault(
    "QT_QPA_FONTDIR",
    str(Path(__file__).resolve().parents[1] / "resources" / "fonts"),
)

from PySide6.QtCore import QCoreApplication, QSettings, QTranslator, QUrl
from PySide6.QtGui import QColorSpace, QFont, QFontDatabase, QGuiApplication, QSurfaceFormat
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QFileDialog, QMessageBox

from mediaflow.desktop.controllers import ProjectController
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.settings_repository import SettingsRepository


@contextmanager
def _qml_dll_search_path():
    if sys.platform != "win32":
        yield
        return
    kernel32 = WinDLL("kernel32", use_last_error=True)
    previous = create_unicode_buffer(32768)
    previous_length = kernel32.GetDllDirectoryW(len(previous), previous)
    import PySide6

    pyside_root = str(Path(PySide6.__file__).resolve().parent)
    if not kernel32.SetDllDirectoryW(pyside_root):
        raise OSError("Unable to configure the PySide6 DLL search directory")
    try:
        yield
    finally:
        kernel32.SetDllDirectoryW(previous.value if previous_length else None)


def create_engine(app: QGuiApplication) -> tuple[QQmlApplicationEngine, ProjectController]:
    engine = QQmlApplicationEngine()
    qml_errors: list[str] = []
    engine.warnings.connect(lambda warnings: qml_errors.extend(item.toString() for item in warnings))
    paths = RuntimePaths.discover()
    if paths.native_qml is None:
        raise RuntimeError("MediaFlow Native preview is not built. Run scripts/build_native.ps1 first.")
    engine.addImportPath(str(paths.native_qml))
    controller = ProjectController(app)
    language = controller.settings.ui.language
    if language != "zh_CN":
        translation = QTranslator(app)
        translation_path = (
            Path(__file__).resolve().parents[1] / "resources" / "i18n" / f"mediaflow_{language}.qm"
        )
        if not translation.load(str(translation_path)):
            raise RuntimeError(f"Failed to load interface translation: {translation_path}")
        app.installTranslator(translation)
        app.setProperty("mediaflowTranslator", translation)
    engine.rootContext().setContextProperty("projectController", controller)
    qml_path = Path(__file__).resolve().parent / "qml" / "Main.qml"
    with _qml_dll_search_path():
        engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        details = "\n".join(qml_errors)
        raise RuntimeError(
            f"Failed to load QML application: {qml_path}" + (f"\n{details}" if details else "")
        )
    return engine, controller


def load_application_font(app: QGuiApplication) -> str:
    font_path = Path(__file__).resolve().parents[1] / "resources" / "fonts" / "LXGWWenKai-Regular.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font_path))
    families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    if not families:
        raise RuntimeError(f"Failed to load application font: {font_path}")
    family = families[0]
    app.setFont(QFont(family, 10))
    return family


def ensure_runtime_directory() -> bool:
    if os.environ.get("MEDIAFLOW_RUNTIME_DIR") or Path("D:/").exists():
        return True
    bootstrap = QSettings("MediaFlow Pro", "MediaFlow Pro Bootstrap")
    saved = Path(str(bootstrap.value("runtimeDirectory", ""))).expanduser()
    if saved.is_dir():
        os.environ["MEDIAFLOW_RUNTIME_DIR"] = str(saved.resolve())
        return True
    selected = QFileDialog.getExistingDirectory(
        None,
        "选择 MediaFlow Pro 运行环境目录",
        str(Path.home()),
        QFileDialog.Option.ShowDirsOnly,
    )
    if not selected:
        QMessageBox.critical(
            None,
            "MediaFlow Pro",
            "D 盘不可用。请选择用于依赖、模型和缓存的目录后再启动。",
        )
        return False
    runtime_directory = Path(selected).resolve()
    os.environ["MEDIAFLOW_RUNTIME_DIR"] = str(runtime_directory)
    bootstrap.setValue("runtimeDirectory", str(runtime_directory))
    bootstrap.sync()
    return True


def main() -> int:
    multiprocessing.freeze_support()
    QCoreApplication.setOrganizationName("MediaFlow Pro")
    QCoreApplication.setApplicationName("MediaFlow Pro")
    QCoreApplication.setApplicationVersion("2.0.0")
    if SettingsRepository().load().preview.hdr_preview:
        surface_format = QSurfaceFormat.defaultFormat()
        surface_format.setColorSpace(QColorSpace(QColorSpace.SRgbLinear))
        QSurfaceFormat.setDefaultFormat(surface_format)
    app = QGuiApplication(sys.argv)
    if not ensure_runtime_directory():
        return 2
    load_application_font(app)
    app.setDesktopFileName("MediaFlow Pro")
    engine, controller = create_engine(app)
    app.aboutToQuit.connect(controller.shutdown)
    if len(sys.argv) > 1:
        controller.openProject(QUrl.fromLocalFile(sys.argv[1]).toString())
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
