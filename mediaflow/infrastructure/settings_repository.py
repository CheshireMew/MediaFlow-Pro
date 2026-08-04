from __future__ import annotations

import hashlib
import os
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.settings import DesktopSettings, ServiceSettings, SettingsDocument
from mediaflow.environment import (
    DESKTOP_SETTINGS_PATH_VARIABLE,
    SERVICE_SETTINGS_PATH_VARIABLE,
    configured_path,
)
from mediaflow.infrastructure.storage_paths import (
    default_media_root,
    default_project_root,
)

from .runtime_paths import runtime_directory

SERVICE_SETTINGS_SCHEMA_VERSION = 1
DESKTOP_SETTINGS_SCHEMA_VERSION = 1
_SETTINGS_WRITE_LOCK = threading.RLock()
_UNLOADED = object()


class SettingsContentError(RuntimeError):
    """A current split settings file exists but cannot be consumed."""


class _SettingsChangedDuringRecovery(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SettingsLoadResult[DocumentT: SettingsDocument]:
    settings: DocumentT
    archived_path: Path | None = None
    error: str = ""

    @property
    def recovered(self) -> bool:
        return self.archived_path is not None


class _SettingsRepository[DocumentT: SettingsDocument]:
    document_type: type[DocumentT]
    schema_version: int
    filename: str
    environment_variable: str

    def __init__(self, path: str | Path | None = None):
        selected_path = (
            Path(path).expanduser().resolve()
            if path is not None
            else configured_path(self.environment_variable)
        )
        self.path = (
            selected_path
            if selected_path is not None
            else (runtime_directory() / self.filename).resolve()
        )
        self._loaded_digest: str | None | object = _UNLOADED

    def load(self) -> DocumentT:
        if not self.path.is_file():
            self._loaded_digest = None
            return self.default_settings()
        content = self.path.read_bytes()
        self._loaded_digest = self._digest(content)
        try:
            document = self.document_type.model_validate_json(content)
        except (TypeError, UnicodeDecodeError, ValueError) as error:
            raise SettingsContentError(
                f"{self.filename} 内容无效：{error}"
            ) from error
        if int(document.schema_version) != self.schema_version:
            raise SettingsContentError(
                f"{self.filename} schema 必须为 {self.schema_version}，"
                f"实际为 {document.schema_version}"
            )
        return self.normalize(document)

    def load_recovering_invalid(self) -> SettingsLoadResult[DocumentT]:
        for _attempt in range(3):
            try:
                return SettingsLoadResult(self.load())
            except SettingsContentError as error:
                expected_digest = self._loaded_digest
                if not isinstance(expected_digest, str):
                    raise RuntimeError("设置恢复缺少损坏文件的内容指纹") from error
                try:
                    archived_path = self._archive_invalid_file(expected_digest)
                except _SettingsChangedDuringRecovery:
                    continue
                self._loaded_digest = None
                return SettingsLoadResult(
                    self.default_settings(),
                    archived_path=archived_path,
                    error=str(error),
                )
        raise RuntimeError("设置文件在恢复期间被其他进程反复修改，请稍后重试")

    def save(self, settings: DocumentT) -> None:
        candidate = self.normalize(settings)
        payload = candidate.model_dump_json(indent=2)
        with _SETTINGS_WRITE_LOCK:
            with self._interprocess_lock():
                current_digest = (
                    self._digest(self.path.read_bytes())
                    if self.path.is_file()
                    else None
                )
                if (
                    self._loaded_digest is not _UNLOADED
                    and current_digest != self._loaded_digest
                ):
                    raise RuntimeError(
                        f"{self.filename} 已被另一个进程修改，请重新加载后再保存"
                    )
                atomic_write_text(
                    self.path,
                    payload,
                    durable=True,
                    mode=0o600 if sys.platform != "win32" else None,
                )
                self._loaded_digest = self._digest(payload.encode("utf-8"))

    def normalize(self, settings: DocumentT) -> DocumentT:
        return settings.model_copy(deep=True)

    def default_settings(self) -> DocumentT:
        return self.normalize(self.document_type())

    def _archive_invalid_file(self, expected_digest: str) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        archived_path = (
            self.path.parent
            / "archive"
            / f"{self.path.stem}.invalid-{timestamp}-{uuid4().hex[:8]}.json"
        )
        with _SETTINGS_WRITE_LOCK:
            with self._interprocess_lock():
                if not self.path.is_file():
                    raise _SettingsChangedDuringRecovery
                current_content = self.path.read_bytes()
                if self._digest(current_content) != expected_digest:
                    raise _SettingsChangedDuringRecovery
                archived_path.parent.mkdir(parents=True, exist_ok=True)
                self.path.replace(archived_path)
                if sys.platform != "win32":
                    archived_path.chmod(0o600)
        return archived_path

    @contextmanager
    def _interprocess_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with lock_path.open("a+b") as lock_file:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _digest(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()


class ServiceSettingsRepository(_SettingsRepository[ServiceSettings]):
    document_type = ServiceSettings
    schema_version = SERVICE_SETTINGS_SCHEMA_VERSION
    filename = "service-settings.json"
    environment_variable = SERVICE_SETTINGS_PATH_VARIABLE

    def normalize(self, settings: ServiceSettings) -> ServiceSettings:
        candidate = settings.model_copy(deep=True)
        if not candidate.default_project_directory.strip():
            candidate.default_project_directory = default_project_root()
        if not candidate.download.output_directory.strip():
            candidate.download.output_directory = default_media_root()
        return candidate

    @staticmethod
    def prepare_storage(settings: ServiceSettings) -> None:
        Path(settings.default_project_directory).mkdir(parents=True, exist_ok=True)
        Path(settings.download.output_directory).mkdir(parents=True, exist_ok=True)


class DesktopSettingsRepository(_SettingsRepository[DesktopSettings]):
    document_type = DesktopSettings
    schema_version = DESKTOP_SETTINGS_SCHEMA_VERSION
    filename = "desktop-settings.json"
    environment_variable = DESKTOP_SETTINGS_PATH_VARIABLE
