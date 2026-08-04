from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest

from mediaflow.domain.settings import DesktopSettings, ServiceSettings
from mediaflow.infrastructure.settings_repository import (
    DesktopSettingsRepository,
    ServiceSettingsRepository,
    SettingsContentError,
)
from scripts.migrate_settings import split_legacy_settings


def test_split_repositories_use_distinct_explicit_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_path = tmp_path / "settings" / "service-settings.json"
    desktop_path = tmp_path / "settings" / "desktop-settings.json"
    monkeypatch.setenv("MEDIAFLOW_SERVICE_SETTINGS_PATH", str(service_path))
    monkeypatch.setenv("MEDIAFLOW_DESKTOP_SETTINGS_PATH", str(desktop_path))

    service_repository = ServiceSettingsRepository()
    desktop_repository = DesktopSettingsRepository()
    service = service_repository.load()
    desktop = desktop_repository.load()
    service.download.last_url = "https://example.com/media"
    desktop.ui.language = "en"
    service_repository.save(service)
    desktop_repository.save(desktop)

    assert service_repository.path == service_path.resolve()
    assert desktop_repository.path == desktop_path.resolve()
    assert ServiceSettingsRepository().load().download.last_url.endswith("/media")
    assert DesktopSettingsRepository().load().ui.language == "en"
    assert not (service_path.parent / "settings.json").exists()


def test_split_repositories_reject_the_other_settings_boundary(
    tmp_path: Path,
) -> None:
    service_path = tmp_path / "service-settings.json"
    desktop_path = tmp_path / "desktop-settings.json"
    service_path.write_text(DesktopSettings().model_dump_json(), encoding="utf-8")
    desktop_path.write_text(ServiceSettings().model_dump_json(), encoding="utf-8")

    with pytest.raises(SettingsContentError):
        ServiceSettingsRepository(service_path).load()
    with pytest.raises(SettingsContentError):
        DesktopSettingsRepository(desktop_path).load()


def test_invalid_split_settings_are_archived_without_deleting_the_bytes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "service-settings.json"
    original = b'{"schema_version":1,"download":'
    path.write_bytes(original)

    loaded = ServiceSettingsRepository(path).load_recovering_invalid()

    assert loaded.recovered is True
    assert loaded.archived_path is not None
    assert loaded.archived_path.read_bytes() == original
    assert not path.exists()
    assert isinstance(loaded.settings, ServiceSettings)


def test_legacy_settings_migration_splits_validates_and_archives_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "settings.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 21,
                "workflow": {"auto_continue": True},
                "download": {
                    "last_url": "https://example.com/legacy",
                    "output_directory": str(tmp_path / "media"),
                },
                "asr": {"model": "large-v3"},
                "translation": {"target_language": "ja"},
                "preview": {"preview_quality": "proxy"},
                "audio": {"loudness_target_lufs": -16.0},
                "ui": {
                    "language": "en",
                    "theme": "high_contrast",
                    "default_project_directory": str(tmp_path / "projects"),
                    "recent_project_paths": [str(tmp_path / "old-project")],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    service_target = tmp_path / "service-settings.json"
    desktop_target = tmp_path / "desktop-settings.json"

    service, desktop, archived = split_legacy_settings(
        source,
        service_target,
        desktop_target,
    )

    assert service.workflow.auto_continue is True
    assert service.download.last_url.endswith("/legacy")
    assert service.default_project_directory == str((tmp_path / "projects").resolve())
    assert desktop.ui.language == "en"
    assert desktop.ui.recent_project_paths == [str(tmp_path / "old-project")]
    assert ServiceSettingsRepository(service_target).load() == service
    assert DesktopSettingsRepository(desktop_target).load() == desktop
    assert archived.is_file()
    assert not source.exists()
    if sys.platform != "win32":
        assert stat.S_IMODE(service_target.stat().st_mode) == 0o600
        assert stat.S_IMODE(desktop_target.stat().st_mode) == 0o600
        assert stat.S_IMODE(archived.stat().st_mode) == 0o600


def test_legacy_settings_migration_refuses_to_overwrite_either_target(
    tmp_path: Path,
) -> None:
    source = tmp_path / "settings.json"
    source.write_text('{"schema_version":21}', encoding="utf-8")
    service_target = tmp_path / "service-settings.json"
    desktop_target = tmp_path / "desktop-settings.json"
    desktop_target.write_text("owned by desktop", encoding="utf-8")

    with pytest.raises(FileExistsError):
        split_legacy_settings(source, service_target, desktop_target)

    assert source.is_file()
    assert not service_target.exists()
    assert desktop_target.read_text(encoding="utf-8") == "owned by desktop"
