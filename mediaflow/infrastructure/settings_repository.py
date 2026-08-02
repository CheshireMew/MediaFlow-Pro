from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.settings import GlobalSettings
from mediaflow.infrastructure.storage_paths import default_media_root, default_project_root

from .runtime_paths import RuntimePaths

SETTINGS_SCHEMA_VERSION = 21
_SETTINGS_WRITE_LOCK = threading.RLock()
_UNLOADED = object()


class SettingsContentError(RuntimeError):
    """The settings file exists but cannot be consumed by this application."""


class _SettingsChangedDuringRecovery(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SettingsLoadResult:
    settings: GlobalSettings
    archived_path: Path | None = None
    error: str = ""

    @property
    def recovered(self) -> bool:
        return self.archived_path is not None


class SettingsRepository:
    def __init__(self, path: str | Path | None = None):
        configured_path = os.environ.get("MEDIAFLOW_SETTINGS_PATH")
        selected_path = path if path is not None else configured_path
        self.path = (
            Path(selected_path).expanduser().resolve()
            if selected_path is not None
            else (RuntimePaths.discover().runtime_dir / "settings.json").resolve()
        )
        self._loaded_digest: str | None | object = _UNLOADED

    def load(self) -> GlobalSettings:
        if not self.path.is_file():
            self._loaded_digest = None
            return self.default_settings()
        content = self.path.read_bytes()
        self._loaded_digest = self._digest(content)
        try:
            payload = json.loads(content.decode("utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("settings root must be a JSON object")
            version = int(payload.get("schema_version", 1))
            if version > SETTINGS_SCHEMA_VERSION:
                raise SettingsContentError(
                    f"设置文件来自更新版本（schema {version}），当前版本最高支持 "
                    f"schema {SETTINGS_SCHEMA_VERSION}"
                )
            if version < 3:
                asr = payload.get("asr")
                if isinstance(asr, dict):
                    asr.pop("engine", None)
                payload.setdefault("translation", {"target_language": ""})
            if version < 4:
                translation = payload.setdefault("translation", {"target_language": ""})
                translation.setdefault("mode", "standard")
                translation.setdefault("glossary_terms", [])
                providers = payload.get("llm_providers") or []
                active = next(
                    (
                        item.get("id")
                        for item in providers
                        if isinstance(item, dict) and item.get("enabled")
                    ),
                    None,
                )
                payload.setdefault("active_llm_provider_id", active)
            if version < 5:
                asr = payload.setdefault("asr", {})
                asr.setdefault("engine", "builtin")
                asr.setdefault("cli_path", None)
            if version < 6:
                payload.setdefault("download", {}).setdefault("output_directory", None)
                payload.setdefault("asr", {}).setdefault("auto_trim_silence", False)
            if version < 7:
                payload.setdefault("asr", {}).pop("auto_trim_silence", None)
            if version < 8:
                payload.setdefault("download", {}).setdefault("last_url", "")
                payload.setdefault("ui", {}).setdefault("default_project_directory", None)
            if version < 9:
                ui = payload.setdefault("ui", {})
                if not str(ui.get("default_project_directory") or "").strip():
                    ui["default_project_directory"] = default_project_root()
            if version < 10:
                translation = payload.setdefault("translation", {})
                if not str(translation.get("target_language") or "").strip():
                    ui_language = str(payload.setdefault("ui", {}).get("language") or "zh_CN")
                    translation["target_language"] = (
                        ui_language if ui_language in {"zh_CN", "en", "ja"} else "zh_CN"
                    )
            if version < 11:
                payload.setdefault("ui", {})["default_project_directory"] = default_project_root()
                payload.setdefault("download", {})["output_directory"] = default_media_root()
            if version < 12:
                payload.setdefault("download", {})["codec"] = "avc"
            if version < 13:
                ui = payload.setdefault("ui", {})
                ui.pop("inspector_width", None)
                ui["left_panel_width"] = max(360, int(ui.get("left_panel_width") or 360))
            if version < 15:
                payload.pop("stock_media", None)
            if version < 16:
                payload.setdefault("asr", {}).setdefault("parallel_chunks", 0)
            if version < 17:
                payload.setdefault("ui", {})["default_project_directory"] = default_project_root()
            if version < 18:
                payload.setdefault(
                    "speech_synthesis",
                    {
                        "gpt_sovits_root": None,
                        "device": "auto",
                        "startup_timeout_seconds": 300,
                    },
                )
            if version < 19:
                payload.setdefault("asr", {}).setdefault("model_directory", None)
            if version < 20:
                ui = payload.setdefault("ui", {})
                previous_left = max(340, min(640, int(ui.pop("left_panel_width", 520))))
                previous_timeline = max(210, min(640, int(ui.pop("timeline_height", 330))))
                ui["workspace_layout_preset"] = "standard"
                ui["workspace_layouts"] = {
                    "standard": {
                        "left_panel_width": previous_left,
                        "inspector_panel_width": 400,
                        "timeline_height": previous_timeline,
                        "tool_panel_visible": True,
                        "inspector_panel_visible": True,
                        "timeline_visible": True,
                    },
                    "media": {
                        "left_panel_width": 560,
                        "inspector_panel_width": 360,
                        "timeline_height": 300,
                        "tool_panel_visible": True,
                        "inspector_panel_visible": True,
                        "timeline_visible": True,
                    },
                    "vertical": {
                        "left_panel_width": 420,
                        "inspector_panel_width": 360,
                        "timeline_height": 280,
                        "tool_panel_visible": False,
                        "inspector_panel_visible": True,
                        "timeline_visible": True,
                    },
                }
            if version < 21:
                payload.setdefault("ui", {}).setdefault(
                    "workspace_tour_completed", False
                )
            payload["schema_version"] = SETTINGS_SCHEMA_VERSION
            settings = self.with_storage_defaults(GlobalSettings.model_validate(payload))
        except SettingsContentError:
            raise
        except (AttributeError, TypeError, UnicodeDecodeError, ValueError) as error:
            raise SettingsContentError(f"设置文件内容无效：{error}") from error
        if version < SETTINGS_SCHEMA_VERSION:
            self.save(settings)
        return settings

    def load_recovering_invalid(self) -> SettingsLoadResult:
        for _attempt in range(3):
            try:
                return SettingsLoadResult(self.load())
            except SettingsContentError as error:
                expected_digest = self._loaded_digest
                if not isinstance(expected_digest, str):
                    raise RuntimeError(
                        "设置恢复缺少损坏文件的内容指纹"
                    ) from error
                try:
                    archived_path = self._archive_invalid_file(
                        expected_digest,
                    )
                except _SettingsChangedDuringRecovery:
                    continue
                self._loaded_digest = None
                return SettingsLoadResult(
                    self.default_settings(),
                    archived_path=archived_path,
                    error=str(error),
                )
        raise RuntimeError(
            "设置文件在恢复期间被其他窗口反复修改，请稍后重试"
        )

    def _archive_invalid_file(self, expected_digest: str) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        suffix = self.path.suffix or ".json"
        archived_path = (
            self.path.parent
            / "archive"
            / f"{self.path.stem}.invalid-{timestamp}-{uuid4().hex[:8]}{suffix}"
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
        return archived_path

    def save(self, settings: GlobalSettings) -> None:
        settings = self.with_storage_defaults(settings)
        payload = settings.model_dump_json(indent=2)
        with _SETTINGS_WRITE_LOCK:
            with self._interprocess_lock():
                current_digest = self._digest(self.path.read_bytes()) if self.path.is_file() else None
                if self._loaded_digest is not _UNLOADED and current_digest != self._loaded_digest:
                    raise RuntimeError("设置已被另一个 MediaFlow Pro 窗口修改，请重新打开设置后再保存")
                atomic_write_text(self.path, payload, durable=True)
                self._loaded_digest = self._digest(payload.encode("utf-8"))

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

    @staticmethod
    def default_settings() -> GlobalSettings:
        return SettingsRepository.with_storage_defaults(GlobalSettings())

    @staticmethod
    def with_storage_defaults(settings: GlobalSettings) -> GlobalSettings:
        candidate = settings.model_copy(deep=True)
        if not candidate.ui.default_project_directory.strip():
            candidate.ui.default_project_directory = default_project_root()
        if not candidate.download.output_directory.strip():
            candidate.download.output_directory = default_media_root()
        return candidate

    @staticmethod
    def prepare_storage(settings: GlobalSettings) -> None:
        Path(settings.ui.default_project_directory).mkdir(parents=True, exist_ok=True)
        Path(settings.download.output_directory).mkdir(parents=True, exist_ok=True)
