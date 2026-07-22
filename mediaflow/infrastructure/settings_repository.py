from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from mediaflow.domain.settings import GlobalSettings, default_media_root, default_project_root

from .runtime_paths import RuntimePaths

SETTINGS_SCHEMA_VERSION = 13
_SETTINGS_WRITE_LOCK = threading.RLock()
_UNLOADED = object()


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
            settings = GlobalSettings()
            self._ensure_storage_directories(settings)
            return settings
        content = self.path.read_bytes()
        self._loaded_digest = self._digest(content)
        payload = json.loads(content.decode("utf-8"))
        version = int(payload.get("schema_version", 1))
        if version > SETTINGS_SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported settings schema: {version}")
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
                (item.get("id") for item in providers if isinstance(item, dict) and item.get("enabled")),
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
        payload["schema_version"] = SETTINGS_SCHEMA_VERSION
        settings = GlobalSettings.model_validate(payload)
        self._ensure_storage_directories(settings)
        if version < SETTINGS_SCHEMA_VERSION:
            self.save(settings)
        return settings

    def save(self, settings: GlobalSettings) -> None:
        self._ensure_storage_directories(settings)
        payload = settings.model_dump_json(indent=2)
        with _SETTINGS_WRITE_LOCK:
            with self._interprocess_lock():
                current_digest = self._digest(self.path.read_bytes()) if self.path.is_file() else None
                if self._loaded_digest is not _UNLOADED and current_digest != self._loaded_digest:
                    raise RuntimeError("设置已被另一个 MediaFlow Pro 窗口修改，请重新打开设置后再保存")
                descriptor, temporary_name = tempfile.mkstemp(
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    dir=self.path.parent,
                )
                temporary = Path(temporary_name)
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                    temporary.replace(self.path)
                    self._loaded_digest = self._digest(payload.encode("utf-8"))
                except Exception:
                    temporary.unlink(missing_ok=True)
                    raise

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
    def _ensure_storage_directories(settings: GlobalSettings) -> None:
        Path(settings.ui.default_project_directory).mkdir(parents=True, exist_ok=True)
        Path(settings.download.output_directory).mkdir(parents=True, exist_ok=True)
