from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.settings import DesktopSettings, ServiceSettings
from mediaflow.infrastructure.settings_repository import (
    DesktopSettingsRepository,
    ServiceSettingsRepository,
)
from mediaflow.infrastructure.storage_paths import (
    default_media_root,
    default_project_root,
)

LEGACY_SETTINGS_SCHEMA_VERSION = 21


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Split one legacy settings.json into service-settings.json and "
            "desktop-settings.json, then archive the source file."
        )
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("--service-target", type=Path)
    parser.add_argument("--desktop-target", type=Path)
    return parser


def _upgrade_legacy_payload(payload: dict) -> dict:
    version = int(payload.get("schema_version", 1))
    if version > LEGACY_SETTINGS_SCHEMA_VERSION:
        raise ValueError(
            f"旧设置 schema {version} 高于迁移器支持的 "
            f"{LEGACY_SETTINGS_SCHEMA_VERSION}"
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
        payload.setdefault(
            "active_llm_provider_id",
            next(
                (
                    item.get("id")
                    for item in providers
                    if isinstance(item, dict) and item.get("enabled")
                ),
                None,
            ),
        )
    if version < 5:
        asr = payload.setdefault("asr", {})
        asr.setdefault("engine", "builtin")
        asr.setdefault("cli_path", None)
    if version < 6:
        payload.setdefault("download", {}).setdefault("output_directory", None)
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
            language = str(payload.setdefault("ui", {}).get("language") or "zh_CN")
            translation["target_language"] = (
                language if language in {"zh_CN", "en", "ja"} else "zh_CN"
            )
    if version < 11:
        payload.setdefault("ui", {})["default_project_directory"] = (
            default_project_root()
        )
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
        payload.setdefault("ui", {})["default_project_directory"] = (
            default_project_root()
        )
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
        payload.setdefault("ui", {}).setdefault("workspace_tour_completed", False)
    return payload


def split_legacy_settings(
    source: Path,
    service_target: Path,
    desktop_target: Path,
) -> tuple[ServiceSettings, DesktopSettings, Path]:
    source = source.expanduser().resolve()
    service_target = service_target.expanduser().resolve()
    desktop_target = desktop_target.expanduser().resolve()
    if service_target == desktop_target:
        raise ValueError("两个设置目标必须是不同文件")
    for target in (service_target, desktop_target):
        if target.exists():
            raise FileExistsError(f"目标设置文件已存在，拒绝覆盖：{target}")
    raw = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict):
        raise ValueError("旧 settings.json 根节点必须是对象")
    payload = _upgrade_legacy_payload(dict(raw))
    ui = dict(payload.get("ui") or {})
    project_directory = str(
        ui.pop("default_project_directory", "") or default_project_root()
    )
    service_defaults = ServiceSettings().model_dump(
        mode="python",
        exclude_computed_fields=True,
    )
    desktop_defaults = DesktopSettings().model_dump(
        mode="python",
        exclude_computed_fields=True,
    )
    service = ServiceSettings.model_validate(
        {
            **service_defaults,
            **{
                key: payload[key]
                for key in service_defaults
                if key in payload and key != "schema_version"
            },
            "schema_version": 1,
            "default_project_directory": project_directory,
        }
    )
    desktop = DesktopSettings.model_validate(
        {
            **desktop_defaults,
            "schema_version": 1,
            "ui": ui,
        }
    )
    service = ServiceSettingsRepository(service_target).normalize(service)
    desktop = DesktopSettingsRepository(desktop_target).normalize(desktop)
    service_text = service.model_dump_json(indent=2)
    desktop_text = desktop.model_dump_json(indent=2)
    ServiceSettings.model_validate_json(service_text)
    DesktopSettings.model_validate_json(desktop_text)

    identity = uuid4().hex
    service_stage = service_target.with_name(f".{service_target.name}.{identity}.migrating")
    desktop_stage = desktop_target.with_name(f".{desktop_target.name}.{identity}.migrating")
    published_service = False
    try:
        service_target.parent.mkdir(parents=True, exist_ok=True)
        desktop_target.parent.mkdir(parents=True, exist_ok=True)
        private_mode = 0o600 if sys.platform != "win32" else None
        atomic_write_text(
            service_stage,
            service_text,
            durable=True,
            mode=private_mode,
        )
        atomic_write_text(
            desktop_stage,
            desktop_text,
            durable=True,
            mode=private_mode,
        )
        ServiceSettingsRepository(service_stage).load()
        DesktopSettingsRepository(desktop_stage).load()
        service_stage.replace(service_target)
        published_service = True
        desktop_stage.replace(desktop_target)
    except BaseException:
        failed_root = source.parent / "archive" / "settings-migration-failed"
        failed_root.mkdir(parents=True, exist_ok=True)
        if published_service and service_target.exists():
            failed_service = failed_root / f"service-{identity}.json"
            service_target.replace(failed_service)
            if sys.platform != "win32":
                failed_service.chmod(0o600)
        elif service_stage.exists():
            failed_service = failed_root / f"service-{identity}.json"
            service_stage.replace(failed_service)
            if sys.platform != "win32":
                failed_service.chmod(0o600)
        if desktop_stage.exists():
            failed_desktop = failed_root / f"desktop-{identity}.json"
            desktop_stage.replace(failed_desktop)
            if sys.platform != "win32":
                failed_desktop.chmod(0o600)
        raise

    ServiceSettingsRepository(service_target).load()
    DesktopSettingsRepository(desktop_target).load()
    archive_root = source.parent / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archived = archive_root / f"settings.v21-{timestamp}-{identity[:8]}.json"
    source.replace(archived)
    if sys.platform != "win32":
        archived.chmod(0o600)
    return service, desktop, archived


def main() -> int:
    args = _parser().parse_args()
    source = args.source.expanduser().resolve()
    service_target = (
        args.service_target.expanduser().resolve()
        if args.service_target is not None
        else source.with_name("service-settings.json")
    )
    desktop_target = (
        args.desktop_target.expanduser().resolve()
        if args.desktop_target is not None
        else source.with_name("desktop-settings.json")
    )
    _service, _desktop, archived = split_legacy_settings(
        source,
        service_target,
        desktop_target,
    )
    print(
        json.dumps(
            {
                "service_settings": str(service_target),
                "desktop_settings": str(desktop_target),
                "archived_legacy_settings": str(archived),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
