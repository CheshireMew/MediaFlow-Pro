from __future__ import annotations

import json
from pathlib import Path

from mediaflow.domain.settings import GlobalSettings

from .runtime_paths import RuntimePaths

SETTINGS_SCHEMA_VERSION = 3


class SettingsRepository:
    def __init__(self, path: str | Path | None = None):
        self.path = (
            Path(path).resolve()
            if path is not None
            else RuntimePaths.discover().runtime_dir / "settings.json"
        )

    def load(self) -> GlobalSettings:
        if not self.path.is_file():
            return GlobalSettings()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        version = int(payload.get("schema_version", 1))
        if version > SETTINGS_SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported settings schema: {version}")
        if version < 3:
            asr = payload.get("asr")
            if isinstance(asr, dict):
                asr.pop("engine", None)
            payload.setdefault("translation", {"target_language": ""})
            payload["schema_version"] = SETTINGS_SCHEMA_VERSION
        settings = GlobalSettings.model_validate(payload)
        if version < SETTINGS_SCHEMA_VERSION:
            self.save(settings)
        return settings

    def save(self, settings: GlobalSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            settings.model_dump_json(indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)
