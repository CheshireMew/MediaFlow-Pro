import json
from pathlib import Path

import pytest

from mediaflow.domain.exports import SubtitleStyle
from mediaflow.domain.settings import (
    GlobalSettings,
    GlossaryTermSettings,
    LlmProviderSettings,
    SubtitleStylePresetSettings,
    default_media_root,
    default_project_root,
)
from mediaflow.infrastructure.settings_repository import SETTINGS_SCHEMA_VERSION, SettingsRepository


def test_default_repository_honors_isolated_settings_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "isolated" / "settings.json"
    monkeypatch.setenv("MEDIAFLOW_SETTINGS_PATH", str(path))

    repository = SettingsRepository()
    repository.save(GlobalSettings())

    assert repository.path == path.resolve()
    assert path.is_file()


def test_typed_settings_round_trip(tmp_path: Path) -> None:
    repository = SettingsRepository(tmp_path / "settings.json")
    first_provider = LlmProviderSettings(
        name="Local provider",
        base_url="http://127.0.0.1:11434/v1",
        api_key="local-key",
        model="example-model",
    )
    second_provider = LlmProviderSettings(
        name="Cloud provider",
        base_url="https://example.com/v1",
        api_key="cloud-key",
        model="cloud-model",
    )
    settings = GlobalSettings(
        llm_providers=[first_provider, second_provider],
        active_llm_provider_id=second_provider.id,
    )
    settings.workflow.auto_continue = True
    settings.download.last_url = "https://example.com/remembered-video"
    settings.download.output_directory = str(tmp_path / "Downloads")
    settings.ui.default_project_directory = str(tmp_path / "Projects")
    settings.translation.target_language = "ru"
    settings.translation.mode = "intelligent"
    settings.translation.glossary_terms = [
        GlossaryTermSettings(source="MediaFlow", target="媒体流", category="product")
    ]
    settings.subtitle_style_presets = [
        SubtitleStylePresetSettings(
            name="采访字幕",
            style=SubtitleStyle(font_color="#FFFF00", shadow_size=2),
        )
    ]
    settings.ui.left_panel_width = 340
    settings.ui.asset_view_mode = "large_thumbnails"
    repository.save(settings)

    loaded = repository.load()
    assert loaded == settings
    assert loaded.workflow.auto_continue is True
    assert loaded.download.output_directory == str(tmp_path / "Downloads")
    assert loaded.download.last_url == "https://example.com/remembered-video"
    assert loaded.ui.default_project_directory == str(tmp_path / "Projects")
    assert loaded.ui.asset_view_mode == "large_thumbnails"
    assert loaded.translation.target_language == "ru"
    assert loaded.translation.mode == "intelligent"
    assert loaded.translation.glossary_terms[0].target == "媒体流"
    assert loaded.llm_providers[0].api_key == "local-key"
    assert loaded.active_llm_provider_id == second_provider.id
    assert loaded.subtitle_style_presets[0].name == "采访字幕"
    assert loaded.subtitle_style_presets[0].style.shadow_size == 2


def test_version_two_settings_migrate_cli_engine_and_gain_translation(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    payload = GlobalSettings().model_dump(mode="json")
    payload["schema_version"] = 2
    payload.pop("translation")
    payload["asr"]["engine"] = "faster_whisper_cli"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = SettingsRepository(path).load()

    assert loaded.schema_version == SETTINGS_SCHEMA_VERSION
    assert loaded.translation.target_language == "zh_CN"
    assert loaded.translation.mode == "standard"
    assert loaded.translation.glossary_terms == []
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert loaded.asr.engine == "builtin"
    assert loaded.asr.cli_path is None
    assert persisted["asr"]["engine"] == "builtin"


def test_version_seven_settings_gain_quick_download_memory(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    payload = GlobalSettings().model_dump(mode="json")
    payload["schema_version"] = 7
    payload["download"].pop("last_url")
    payload["ui"].pop("default_project_directory")
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = SettingsRepository(path).load()

    assert loaded.schema_version == SETTINGS_SCHEMA_VERSION
    assert loaded.download.last_url == ""
    assert loaded.ui.default_project_directory == default_project_root()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == SETTINGS_SCHEMA_VERSION
    assert persisted["download"]["last_url"] == ""
    assert persisted["ui"]["default_project_directory"] == default_project_root()


def test_version_eight_settings_gain_automatic_project_root(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    payload = GlobalSettings().model_dump(mode="json")
    payload["schema_version"] = 8
    payload["ui"]["default_project_directory"] = None
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = SettingsRepository(path).load()

    assert loaded.schema_version == SETTINGS_SCHEMA_VERSION
    assert loaded.ui.default_project_directory == default_project_root()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == SETTINGS_SCHEMA_VERSION
    assert persisted["ui"]["default_project_directory"] == default_project_root()


def test_version_nine_settings_gain_translation_default_from_ui_language(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    payload = GlobalSettings().model_dump(mode="json")
    payload["schema_version"] = 9
    payload["ui"]["language"] = "en"
    payload["translation"]["target_language"] = ""
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = SettingsRepository(path).load()

    assert loaded.schema_version == SETTINGS_SCHEMA_VERSION
    assert loaded.translation.target_language == "en"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["translation"]["target_language"] == "en"


def test_version_ten_settings_separate_media_and_project_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application_directory = tmp_path / "MediaFlow Pro"
    monkeypatch.setenv("MEDIAFLOW_APP_ROOT", str(application_directory))
    path = tmp_path / "settings.json"
    payload = GlobalSettings().model_dump(mode="json")
    payload["schema_version"] = 10
    payload["ui"]["default_project_directory"] = str(
        Path.home() / "Videos" / "MediaFlow Projects"
    )
    payload["download"]["output_directory"] = None
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = SettingsRepository(path).load()

    assert loaded.schema_version == SETTINGS_SCHEMA_VERSION
    assert loaded.ui.default_project_directory == default_project_root()
    assert loaded.download.output_directory == default_media_root()
    assert Path(default_project_root()).is_dir()
    assert Path(default_media_root()).is_dir()
    assert Path(default_project_root()).parent == application_directory
    assert Path(default_media_root()).parent == application_directory
    assert Path(default_project_root()) != Path(default_media_root())


def test_version_eleven_settings_default_to_compatible_download_codec(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    payload = GlobalSettings().model_dump(mode="json")
    payload["schema_version"] = 11
    payload["download"]["codec"] = "best"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = SettingsRepository(path).load()

    assert loaded.schema_version == SETTINGS_SCHEMA_VERSION
    assert loaded.download.codec == "avc"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["download"]["codec"] == "avc"


def test_version_twelve_settings_remove_inspector_layout(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    payload = GlobalSettings().model_dump(mode="json")
    payload["schema_version"] = 12
    payload["ui"]["left_panel_width"] = 288
    payload["ui"]["inspector_width"] = 420
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = SettingsRepository(path).load()

    assert loaded.schema_version == SETTINGS_SCHEMA_VERSION
    assert loaded.ui.left_panel_width == 360
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == SETTINGS_SCHEMA_VERSION
    assert persisted["ui"]["left_panel_width"] == 360
    assert "inspector_width" not in persisted["ui"]


def test_version_fourteen_settings_remove_stock_media_configuration(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    payload = GlobalSettings().model_dump(mode="json")
    payload["schema_version"] = 14
    payload["stock_media"] = {
        "pexels_api_key": "old-pexels-key",
        "pixabay_api_key": "old-pixabay-key",
        "unsplash_access_key": "old-unsplash-key",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = SettingsRepository(path).load()

    assert loaded.schema_version == SETTINGS_SCHEMA_VERSION
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == SETTINGS_SCHEMA_VERSION
    assert "stock_media" not in persisted


def test_concurrent_settings_writer_is_rejected_instead_of_losing_updates(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    seed = SettingsRepository(path)
    seed.save(GlobalSettings())
    first = SettingsRepository(path)
    second = SettingsRepository(path)
    first_settings = first.load()
    second_settings = second.load()

    first_settings.ui.theme = "high_contrast"
    first.save(first_settings)
    second_settings.ui.language = "en"

    with pytest.raises(RuntimeError, match="另一个 MediaFlow Pro 窗口"):
        second.save(second_settings)

    persisted = SettingsRepository(path).load()
    assert persisted.ui.theme == "high_contrast"
    assert persisted.ui.language == "zh_CN"
