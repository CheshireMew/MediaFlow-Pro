from __future__ import annotations

import json
import multiprocessing
import os
import sys
import time
from contextlib import contextmanager
from ctypes import WinDLL, create_unicode_buffer
from pathlib import Path

os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import QCoreApplication, QSettings, QTranslator, QUrl
from PySide6.QtGui import QColorSpace, QFontDatabase, QGuiApplication, QIcon, QSurfaceFormat
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWebEngineQuick import QtWebEngineQuick
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from mediaflow.atomic_file import atomic_write_text
from mediaflow.composition import EditorApplication
from mediaflow.desktop.controllers import EditorControllers
from mediaflow.domain.product_identity import PRODUCT_NAME
from mediaflow.infrastructure.application_logging import (
    configure_application_logging,
    shutdown_application_logging,
)
from mediaflow.infrastructure.font_assets import register_application_fonts
from mediaflow.infrastructure.runtime_paths import (
    configured_runtime_directory,
    runtime_directory,
)
from mediaflow.infrastructure.settings_repository import SettingsLoadResult, SettingsRepository

STARTUP_READY_PATH_ENV = "MEDIAFLOW_STARTUP_READY_PATH"
STARTUP_READY_SCHEMA_VERSION = 1


def configure_application_identity() -> None:
    QCoreApplication.setOrganizationName(PRODUCT_NAME)
    QCoreApplication.setApplicationName(PRODUCT_NAME)
    QCoreApplication.setApplicationVersion("2.0.0")


def _monospace_font_family() -> str:
    for family in (
        "Cascadia Mono",
        "Consolas",
        "Menlo",
        "DejaVu Sans Mono",
        "Liberation Mono",
    ):
        if QFontDatabase.hasFamily(family):
            return family
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()


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


def create_engine(
    app: QGuiApplication,
    application: EditorApplication | None = None,
) -> tuple[QQmlApplicationEngine, EditorControllers]:
    engine = QQmlApplicationEngine(app)
    qml_errors: list[str] = []
    engine.warnings.connect(lambda warnings: qml_errors.extend(item.toString() for item in warnings))
    api = application or EditorApplication()
    if api.native_qml_root is None:
        raise RuntimeError(
            f"{PRODUCT_NAME} native preview is not built. "
            "Run scripts/build_native.ps1 first."
        )
    engine.addImportPath(str(api.native_qml_root))
    controllers = EditorControllers(engine, application=api)
    engine.rootContext().setContextProperty(
        "applicationMonospaceFontFamily",
        _monospace_font_family(),
    )
    language = controllers.session.settings.ui.language
    if language != "zh_CN":
        translation = QTranslator(app)
        translation_path = (
            Path(__file__).resolve().parents[1] / "resources" / "i18n" / f"mediaflow_{language}.qm"
        )
        if not translation.load(str(translation_path)):
            raise RuntimeError(f"Failed to load interface translation: {translation_path}")
        app.installTranslator(translation)
        app.setProperty("mediaflowTranslator", translation)
    for name, controller in controllers.context_properties().items():
        engine.rootContext().setContextProperty(name, controller)
    qml_path = Path(__file__).resolve().parent / "qml" / "Main.qml"
    with _qml_dll_search_path():
        engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        details = "\n".join(qml_errors)
        raise RuntimeError(
            f"Failed to load QML application: {qml_path}" + (f"\n{details}" if details else "")
        )
    return engine, controllers


def publish_startup_ready(
    engine: QQmlApplicationEngine,
) -> Path | None:
    """Publish opt-in evidence that the real QML root processed initial events."""

    configured = os.environ.get(STARTUP_READY_PATH_ENV, "").strip()
    if not configured:
        return None
    roots = engine.rootObjects()
    if not roots:
        raise RuntimeError("Cannot publish startup readiness without a QML root")
    QCoreApplication.processEvents()
    ready_path = Path(configured).expanduser().resolve()
    atomic_write_text(
        ready_path,
        json.dumps(
            {
                "schema_version": STARTUP_READY_SCHEMA_VERSION,
                "pid": os.getpid(),
                "ready_at_ns": time.time_ns(),
                "root_object_count": len(roots),
                "event_loop_processed": True,
            },
            separators=(",", ":"),
        ),
    )
    return ready_path


def configure_application_font(app: QGuiApplication) -> str:
    register_application_fonts()
    if sys.platform == "win32" and not QFontDatabase.hasFamily("Microsoft YaHei UI"):
        fonts_directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for filename in (
            "msyh.ttc",
            "msyhbd.ttc",
            "segoeui.ttf",
            "segoeuib.ttf",
            "seguisym.ttf",
            "SegoeIcons.ttf",
            "consola.ttf",
            "consolab.ttf",
        ):
            font_path = fonts_directory / filename
            if font_path.is_file():
                QFontDatabase.addApplicationFont(str(font_path))
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    preferred_families = [
        family
        for family in ("Microsoft YaHei UI", "Segoe UI", "Segoe UI Symbol")
        if QFontDatabase.hasFamily(family)
    ]
    if preferred_families:
        font.setFamilies(preferred_families)
    font.setPointSize(10)
    app.setFont(font)
    return font.family()


def configure_application_icon(app: QGuiApplication) -> Path:
    icon_path = Path(__file__).resolve().parents[1] / "resources" / "branding" / "mediaflow-mark.svg"
    icon = QIcon(str(icon_path))
    if icon.isNull():
        raise RuntimeError(f"Failed to load application icon: {icon_path}")
    app.setWindowIcon(icon)
    return icon_path


def create_desktop_application(argv: list[str] | None = None) -> QApplication:
    return QApplication(sys.argv if argv is None else argv)


def _saved_runtime_directory() -> Path | None:
    bootstrap = QSettings(PRODUCT_NAME, f"{PRODUCT_NAME} Bootstrap")
    saved_value = str(bootstrap.value("runtimeDirectory", "")).strip()
    if not saved_value:
        return None
    saved = Path(saved_value).expanduser()
    return saved.resolve() if saved.is_dir() else None


def startup_settings_path() -> Path | None:
    configured_settings = os.environ.get("MEDIAFLOW_SETTINGS_PATH")
    if configured_settings:
        return Path(configured_settings).expanduser().resolve()
    selected_runtime = configured_runtime_directory() or _saved_runtime_directory()
    return (
        (selected_runtime / "settings.json").resolve()
        if selected_runtime is not None
        else None
    )


def load_startup_settings(settings_path: Path | None) -> SettingsLoadResult:
    if settings_path is None:
        return SettingsLoadResult(SettingsRepository.default_settings())
    return SettingsRepository(settings_path).load_recovering_invalid()


def configure_startup_surface(
    settings: SettingsLoadResult | Path | None,
) -> bool:
    loaded = settings if isinstance(settings, SettingsLoadResult) else load_startup_settings(settings)
    preview = loaded.settings.preview
    if preview.hdr_preview:
        surface_format = QSurfaceFormat.defaultFormat()
        surface_format.setColorSpace(QColorSpace(QColorSpace.SRgbLinear))
        QSurfaceFormat.setDefaultFormat(surface_format)
    return preview.hdr_preview


def show_startup_settings_recovery(settings: SettingsLoadResult) -> bool:
    if not settings.recovered:
        return False
    QMessageBox.warning(
        None,
        PRODUCT_NAME,
        "设置文件无法读取，已原样移到归档目录：\n"
        f"{settings.archived_path}\n\n"
        "本次将使用默认设置继续启动。\n"
        f"原因：{settings.error}",
    )
    return True


def ensure_runtime_directory() -> bool:
    configured = configured_runtime_directory()
    if configured is not None:
        os.environ["MEDIAFLOW_RUNTIME_DIR"] = str(configured)
        return True
    saved = _saved_runtime_directory()
    if saved is not None:
        os.environ["MEDIAFLOW_RUNTIME_DIR"] = str(saved)
        return True
    selected = QFileDialog.getExistingDirectory(
        None,
        f"选择 {PRODUCT_NAME} 运行环境目录",
        str(Path.home()),
        QFileDialog.Option.ShowDirsOnly,
    )
    if not selected:
        QMessageBox.critical(
            None,
            PRODUCT_NAME,
            "运行环境目录尚未配置。请选择用于依赖、模型和缓存的目录后再启动。",
        )
        return False
    runtime_directory = Path(selected).resolve()
    os.environ["MEDIAFLOW_RUNTIME_DIR"] = str(runtime_directory)
    bootstrap = QSettings(PRODUCT_NAME, f"{PRODUCT_NAME} Bootstrap")
    bootstrap.setValue("runtimeDirectory", str(runtime_directory))
    bootstrap.sync()
    return True


def main() -> int:
    multiprocessing.freeze_support()
    configure_application_identity()
    settings_path = startup_settings_path()
    startup_settings = load_startup_settings(settings_path)
    configure_startup_surface(startup_settings)
    if settings_path is None:
        print(
            f"{PRODUCT_NAME}: no runtime directory is known before the first-launch chooser; "
            "using the HDR-capable default surface for this launch.",
            file=sys.stderr,
        )
    QtWebEngineQuick.initialize()
    app = create_desktop_application()
    if not ensure_runtime_directory():
        return 2
    configure_application_logging(runtime_directory())
    try:
        show_startup_settings_recovery(startup_settings)
        # Runtime discovery is deliberately after the desktop fallback above. This
        # keeps machines without D: usable instead of failing before the chooser can
        # be shown.
        api = EditorApplication()
        configure_application_font(app)
        configure_application_icon(app)
        app.setDesktopFileName(PRODUCT_NAME)
        engine, controllers = create_engine(app, api)
        publish_startup_ready(engine)
        app.aboutToQuit.connect(controllers.shutdown)
        if len(sys.argv) > 1:
            controllers.workspace.openProject(QUrl.fromLocalFile(sys.argv[1]).toString())
        return app.exec()
    finally:
        shutdown_application_logging()


if __name__ == "__main__":
    raise SystemExit(main())
