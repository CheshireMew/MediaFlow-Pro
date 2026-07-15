import json
from pathlib import Path

from mediaflow.domain.settings import GlobalSettings, LlmProviderSettings
from mediaflow.infrastructure.settings_repository import SettingsRepository


def test_typed_settings_round_trip(tmp_path: Path) -> None:
    repository = SettingsRepository(tmp_path / "settings.json")
    settings = GlobalSettings(
        llm_providers=[
            LlmProviderSettings(
                name="Local provider",
                base_url="http://127.0.0.1:11434/v1",
                api_key="local-key",
                model="example-model",
            )
        ]
    )
    settings.workflow.auto_continue = True
    settings.translation.target_language = "ja"
    settings.ui.left_panel_width = 340
    repository.save(settings)

    loaded = repository.load()
    assert loaded == settings
    assert loaded.workflow.auto_continue is True
    assert loaded.translation.target_language == "ja"
    assert loaded.llm_providers[0].api_key == "local-key"


def test_version_two_settings_drop_the_unused_cli_engine_and_gain_translation(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    payload = GlobalSettings().model_dump(mode="json")
    payload["schema_version"] = 2
    payload.pop("translation")
    payload["asr"]["engine"] = "faster_whisper_cli"
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = SettingsRepository(path).load()

    assert loaded.schema_version == 3
    assert loaded.translation.target_language == ""
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert "engine" not in persisted["asr"]
