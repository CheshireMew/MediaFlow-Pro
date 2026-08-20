from __future__ import annotations

import json
import os
import shutil
import string
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.product_identity import PRODUCT_NAME
from mediaflow.infrastructure.runtime_contract import load_runtime_contract
from mediaflow.infrastructure.runtime_paths import RuntimePaths, runtime_directory

BOOTSTRAP_APPLICATION = f"{PRODUCT_NAME} Bootstrap"
RUNTIME_DIRECTORY_KEY = "runtimeDirectory"
PENDING_RUNTIME_DIRECTORY_KEY = "pendingRuntimeDirectory"
PENDING_RUNTIME_MIGRATION_KEY = "pendingRuntimeMigration"
RUNTIME_DIRECTORY_SOURCE_ENV = "MEDIAFLOW_RUNTIME_DIR_SOURCE"
MIGRATION_MARKER = ".mediaflow-runtime-migration.json"
RECOMMENDED_FREE_BYTES = 10 * 1024**3


def _bootstrap_settings() -> QSettings:
    return QSettings(PRODUCT_NAME, BOOTSTRAP_APPLICATION)


def saved_runtime_directory() -> Path | None:
    value = str(_bootstrap_settings().value(RUNTIME_DIRECTORY_KEY, "")).strip()
    if not value:
        return None
    path = Path(value).expanduser()
    return path.resolve() if path.is_dir() else None


def set_saved_runtime_directory(path: Path) -> None:
    settings = _bootstrap_settings()
    settings.setValue(RUNTIME_DIRECTORY_KEY, str(path.expanduser().resolve()))
    settings.sync()


@dataclass(frozen=True, slots=True)
class PendingRuntimeDirectoryChange:
    destination: Path
    migrate_existing: bool


def pending_runtime_directory_change() -> PendingRuntimeDirectoryChange | None:
    settings = _bootstrap_settings()
    value = str(settings.value(PENDING_RUNTIME_DIRECTORY_KEY, "")).strip()
    if not value:
        return None
    raw_migration = settings.value(PENDING_RUNTIME_MIGRATION_KEY, False)
    migrate = raw_migration if isinstance(raw_migration, bool) else str(raw_migration).lower() == "true"
    return PendingRuntimeDirectoryChange(Path(value).expanduser().resolve(), migrate)


def schedule_runtime_directory_change(destination: Path, *, migrate_existing: bool) -> None:
    settings = _bootstrap_settings()
    settings.setValue(PENDING_RUNTIME_DIRECTORY_KEY, str(destination.expanduser().resolve()))
    settings.setValue(PENDING_RUNTIME_MIGRATION_KEY, bool(migrate_existing))
    settings.sync()


def cancel_pending_runtime_directory_change() -> None:
    settings = _bootstrap_settings()
    settings.remove(PENDING_RUNTIME_DIRECTORY_KEY)
    settings.remove(PENDING_RUNTIME_MIGRATION_KEY)
    settings.sync()


def runtime_directory_is_managed_externally() -> bool:
    return os.environ.get(RUNTIME_DIRECTORY_SOURCE_ENV, "") == "external"


def configure_runtime_environment(path: Path, *, source: str) -> None:
    os.environ["MEDIAFLOW_RUNTIME_DIR"] = str(path.expanduser().resolve())
    os.environ[RUNTIME_DIRECTORY_SOURCE_ENV] = source


def _drive_candidates() -> list[Path]:
    if os.name != "nt":
        return [Path.home()]
    candidates: list[Path] = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        if root.is_dir():
            candidates.append(root)
    return candidates or [Path.home()]


def recommended_runtime_directory() -> Path:
    system_drive = os.environ.get("SystemDrive", "C:").upper()
    ranked: list[tuple[bool, int, Path]] = []
    for root in _drive_candidates():
        try:
            free = shutil.disk_usage(root).free
        except OSError:
            continue
        is_non_system = str(root).upper().startswith(system_drive) is False
        ranked.append((is_non_system, free, root))
    selected = max(ranked, default=(False, 0, Path.home()), key=lambda item: (item[0], item[1]))[2]
    return (selected / f"{PRODUCT_NAME} Runtime").resolve()


def initial_runtime_directory() -> Path:
    recommended = recommended_runtime_directory()
    candidates = [
        recommended,
        (Path(sys.executable).resolve().parent / "runtime").resolve(),
        (Path(__file__).resolve().parents[2] / "runtime").resolve(),
    ]
    for drive in _drive_candidates():
        candidates.extend(
            [
                (drive / f"{PRODUCT_NAME} Runtime").resolve(),
                (drive / "MediaFlow" / "runtime").resolve(),
                (drive / "Tools" / "MediaFlow" / "runtime").resolve(),
            ]
        )
    for candidate in dict.fromkeys(candidates):
        if not candidate.is_dir():
            continue
        try:
            return validate_existing_runtime_directory(candidate)
        except (OSError, ValueError):
            continue
    return recommended


def format_bytes(value: int) -> str:
    amount = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


def runtime_directory_info(current: Path) -> dict[str, object]:
    pending = pending_runtime_directory_change()
    try:
        free = shutil.disk_usage(current).free
    except OSError:
        free = 0
    return {
        "currentPath": str(current),
        "freeBytes": free,
        "freeLabel": format_bytes(free),
        "managedExternally": runtime_directory_is_managed_externally(),
        "pendingPath": str(pending.destination) if pending else "",
        "pendingMigration": pending.migrate_existing if pending else False,
    }


def current_runtime_directory() -> Path:
    return runtime_directory()


def validate_runtime_change_destination(
    current: Path,
    destination: Path,
    *,
    migrate_existing: bool,
) -> Path:
    source = current.expanduser().resolve()
    target = destination.expanduser().resolve()
    if target == source:
        raise ValueError("新运行环境目录与当前目录相同")
    if source in target.parents or target in source.parents:
        raise ValueError("新旧运行环境目录不能互相包含")
    if target.exists() and not target.is_dir():
        raise ValueError("所选位置不是文件夹")
    if migrate_existing and target.is_dir():
        contents = list(target.iterdir())
        if contents and not (target / MIGRATION_MARKER).is_file():
            raise ValueError("迁移目标必须是空文件夹，请新建一个专用目录")
    parent = target if target.is_dir() else target.parent
    if not parent.exists():
        parent = next((item for item in target.parents if item.exists()), parent)
    if not parent.is_dir() or not os.access(parent, os.W_OK):
        raise PermissionError("所选位置不可写")
    return target


def validate_existing_runtime_directory(directory: Path) -> Path:
    target = directory.expanduser().resolve()
    contract = load_runtime_contract()
    paths = RuntimePaths.from_contract(contract, runtime_root=target)
    required_files = {
        "FFmpeg": paths.ffmpeg,
        "FFprobe": paths.ffprobe,
        "MLT": paths.melt,
        "MLT 动态库": paths.mlt_library,
        "Chromium": paths.chromium,
    }
    required_directories = {
        "MLT 模块": paths.mlt_repository,
        "MLT 预览模块": paths.mlt_preview_repository,
        "MLT 数据": paths.mlt_data,
        "原生预览组件": paths.native_qml,
    }
    missing = [label for label, path in required_files.items() if path is None or not path.is_file()]
    missing.extend(label for label, path in required_directories.items() if path is None or not path.is_dir())
    if missing:
        raise ValueError("所选目录不是完整的 MediaFlow Pro 运行环境，缺少：" + "、".join(missing))
    return target


class FirstLaunchRuntimeDirectoryDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"首次启动 · {PRODUCT_NAME}")
        self.setMinimumWidth(620)
        self._path = QLineEdit(str(initial_runtime_directory()), self)
        self._space = QLabel(self)
        self._space.setWordWrap(True)

        heading = QLabel("选择已准备的运行环境目录", self)
        heading.setStyleSheet("font-size: 18px; font-weight: 600;")
        explanation = QLabel(
            "应用需要从这里读取媒体运行组件、浏览器和原生预览模块，也会在这里保存模型、"
            "代理文件与缓存。请选择已经按安装说明准备完成的目录；内容可能增长到数十 GB，"
            "建议使用空间充足的非系统盘。项目和原始素材不会放在这里。",
            self,
        )
        explanation.setWordWrap(True)

        browse = QPushButton("浏览…", self)
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self._path, 1)
        path_row.addWidget(browse)

        form = QFormLayout()
        form.addRow("运行环境目录", path_row)
        form.addRow("可用空间", self._space)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
            self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("使用此位置并继续")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("退出")
        buttons.accepted.connect(self._accept_selected)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(heading)
        layout.addWidget(explanation)
        layout.addLayout(form)
        layout.addWidget(QLabel("以后可以在“设置 → 常规 → 运行环境”安排迁移；迁移不会删除旧目录。", self))
        layout.addWidget(buttons)
        self._path.textChanged.connect(self._refresh_space)
        self._refresh_space()

    @property
    def selected_directory(self) -> Path:
        return Path(self._path.text().strip()).expanduser().resolve()

    def _browse(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "选择运行环境目录",
            str(self.selected_directory.parent),
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected:
            self._path.setText(selected)

    def _refresh_space(self) -> None:
        try:
            candidate = self.selected_directory
            existing = next((item for item in (candidate, *candidate.parents) if item.exists()), None)
            free = shutil.disk_usage(existing).free if existing is not None else 0
            suffix = "；空间偏少，建议选择其它磁盘" if free < RECOMMENDED_FREE_BYTES else ""
            self._space.setText(f"{format_bytes(free)}{suffix}")
        except (OSError, ValueError):
            self._space.setText("无法读取所选位置")

    def _accept_selected(self) -> None:
        if not self._path.text().strip():
            QMessageBox.warning(self, PRODUCT_NAME, "请选择运行环境目录")
            return
        target = self.selected_directory
        try:
            validate_existing_runtime_directory(target)
            if not os.access(target, os.W_OK):
                raise PermissionError("所选目录不可写")
            free = shutil.disk_usage(target).free
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, PRODUCT_NAME, f"无法使用这个目录：\n{error}")
            return
        if free < RECOMMENDED_FREE_BYTES:
            answer = QMessageBox.question(
                self,
                PRODUCT_NAME,
                f"这个位置只剩 {format_bytes(free)}。运行组件和缓存可能需要更多空间，仍要继续吗？",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.accept()


def choose_first_launch_runtime_directory(parent: QWidget | None = None) -> Path | None:
    dialog = FirstLaunchRuntimeDirectoryDialog(parent)
    return dialog.selected_directory if dialog.exec() == QDialog.DialogCode.Accepted else None


def _migration_files(source: Path) -> tuple[list[Path], int]:
    files = [item for item in source.rglob("*") if item.is_file()]
    return files, sum(item.stat().st_size for item in files)


def _write_migration_marker(destination: Path, payload: dict[str, object]) -> None:
    atomic_write_text(destination / MIGRATION_MARKER, json.dumps(payload, ensure_ascii=False, indent=2))


def apply_pending_runtime_directory_change(parent: QWidget | None = None) -> Path | None:
    pending = pending_runtime_directory_change()
    current = saved_runtime_directory()
    if pending is None:
        return current
    if current is None:
        cancel_pending_runtime_directory_change()
        return None
    try:
        destination = validate_runtime_change_destination(
            current,
            pending.destination,
            migrate_existing=pending.migrate_existing,
        )
        if not pending.migrate_existing:
            validate_existing_runtime_directory(destination)
        destination.mkdir(parents=True, exist_ok=True)
        if pending.migrate_existing:
            files, total_bytes = _migration_files(current)
            free_bytes = shutil.disk_usage(destination).free
            if free_bytes < total_bytes:
                raise OSError(
                    "目标磁盘空间不足："
                    f"迁移至少需要 {format_bytes(total_bytes)}，"
                    f"当前可用 {format_bytes(free_bytes)}"
                )
            _write_migration_marker(
                destination,
                {"status": "copying", "source": str(current), "fileCount": len(files)},
            )
            progress = QProgressDialog(
                "正在迁移运行组件、模型和缓存…",
                "取消迁移",
                0,
                max(1, len(files)),
                parent,
            )
            progress.setWindowTitle(PRODUCT_NAME)
            progress.setMinimumDuration(0)
            copied_bytes = 0
            for index, source_file in enumerate(files, start=1):
                if progress.wasCanceled():
                    cancel_pending_runtime_directory_change()
                    _write_migration_marker(
                        destination,
                        {"status": "cancelled", "source": str(current), "copiedFiles": index - 1},
                    )
                    QMessageBox.information(
                        parent,
                        PRODUCT_NAME,
                        "迁移已取消，应用仍使用原目录。已复制的文件保留在目标目录中，可自行归档。",
                    )
                    return current
                relative = source_file.relative_to(current)
                target_file = destination / relative
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target_file)
                copied_bytes += source_file.stat().st_size
                progress.setLabelText(f"正在迁移：{relative}")
                progress.setValue(index)
            progress.setValue(max(1, len(files)))
            if copied_bytes != total_bytes:
                raise OSError("迁移后的文件总大小与原目录不一致")
            for source_file in files:
                target_file = destination / source_file.relative_to(current)
                if not target_file.is_file() or target_file.stat().st_size != source_file.stat().st_size:
                    raise OSError(f"迁移校验失败：{source_file.relative_to(current)}")
            validate_existing_runtime_directory(destination)
            _write_migration_marker(
                destination,
                {
                    "status": "complete",
                    "source": str(current),
                    "fileCount": len(files),
                    "totalBytes": total_bytes,
                },
            )
        set_saved_runtime_directory(destination)
        cancel_pending_runtime_directory_change()
        QMessageBox.information(
            parent,
            PRODUCT_NAME,
            "运行环境目录已切换。旧目录没有删除，可在确认应用运行正常后自行归档。",
        )
        return destination
    except Exception as error:
        cancel_pending_runtime_directory_change()
        QMessageBox.critical(
            parent,
            PRODUCT_NAME,
            f"运行环境迁移未完成，应用将继续使用原目录。\n\n{error}\n\n目标目录中的已复制文件不会被删除。",
        )
        return current
