from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mediaflow.domain.web_manifest import parse_editable_media_manifest
from mediaflow.domain.web_media_sources import WebMediaSourcesManifest
from mediaflow.domain.web_package_paths import media_mime_type
from mediaflow.infrastructure.editable_media_contract import (
    EDITABLE_MEDIA_SCHEMA_PATH,
    editable_media_contract,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PACKAGES = {
    "starter": FIXTURES / "editable-media-v6",
    "react": FIXTURES / "editable-media-v6-react-reference",
    "warm": FIXTURES / "editable-media-v6-cases" / "warm-paper-project-list",
    "social": FIXTURES / "editable-media-v6-cases" / "social-evidence-variants",
    "technology_cover": (FIXTURES / "editable-media-v6-cases" / "editorial-technology-diagram-cover"),
    "text_card_glossary": (FIXTURES / "editable-media-v6-cases" / "text-card-glossary"),
    "dark_icon_directory": (FIXTURES / "editable-media-v6-cases" / "dark-icon-directory"),
}
PRODUCERS = {
    "starter": "visual-multimedia/assets/web-media-starter",
    "react": "visual-multimedia/assets/react-media-starter/dist",
    "warm": "visual-multimedia/assets/web-card-cases/warm-paper-project-list",
    "social": "visual-multimedia/assets/web-card-cases/social-evidence-variants",
    "technology_cover": ("visual-multimedia/assets/web-card-cases/editorial-technology-diagram-cover"),
    "text_card_glossary": ("visual-multimedia/assets/web-card-cases/text-card-glossary"),
    "dark_icon_directory": ("visual-multimedia/assets/web-card-cases/dark-icon-directory"),
}


def test_supported_media_mime_types_do_not_depend_on_the_host_registry() -> None:
    assert media_mime_type("assets/native-underlay.mkv") == "video/x-matroska"
    assert media_mime_type("assets/voice.wav#track=dialogue") == "audio/wav"


CORPUS_SCHEMA = FIXTURES / "editable-media-v6-contract" / "editable-media.v6.schema.json"
CONTRACT = editable_media_contract()


def _read_manifest(name: str) -> dict[str, object]:
    return json.loads((PACKAGES[name] / "editable-media.json").read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", tuple(PACKAGES))
def test_generated_packages_match_origins_and_v6_contract(name: str) -> None:
    package = PACKAGES[name]
    origin = json.loads((package / "fixture-origin.json").read_text(encoding="utf-8"))
    assert origin["producer"] == PRODUCERS[name]
    assert len(origin["producer_revision"]) == 40
    int(origin["producer_revision"], 16)
    assert origin["editable_media_version"] == 6
    assert {path.relative_to(package).as_posix() for path in package.rglob("*") if path.is_file()} == set(
        origin["files"]
    ) | {"fixture-origin.json"}
    for relative, expected_hash in origin["files"].items():
        path = package.joinpath(*relative.split("/"))
        assert path.is_file()
        assert _sha256(path) == expected_hash
    manifest = parse_editable_media_manifest(_read_manifest(name), CONTRACT)
    assert manifest.version == 6


def test_mediaflow_executes_the_synced_visual_multimedia_v6_schema() -> None:
    assert EDITABLE_MEDIA_SCHEMA_PATH.read_bytes() == CORPUS_SCHEMA.read_bytes()


def test_rich_v6_features_are_first_class_contract_fields() -> None:
    starter = parse_editable_media_manifest(_read_manifest("starter"), CONTRACT)
    parameters = {item.descriptor.id: item for item in starter.parameters}
    assert parameters["spring_strength"].descriptor.control == "slider"
    assert parameters["stagger_interval_ms"].binding.scope == "scene"
    assert parameters["spring_strength"].descriptor.timeline == "keyframe"
    assert starter.frame_readiness.retry_limit == 1
    assert starter.scenes[0].parameters["orbit_radius_px"] == 32
    assert starter.scenes[0].motion.camera is not None
    assert starter.scenes[0].steps[1].state_kind == "change"

    warm = parse_editable_media_manifest(_read_manifest("warm"), CONTRACT)
    warm_fields = {field.id: field for field in warm.data_fields}
    assert warm_fields["curator_avatar"].kind == "media-source"
    assert warm_fields["curator_label"].default == "整理"
    assert warm_fields["legend_items"].kind == "list"
    assert warm.layout_contracts[0].asset_slots[0].id == "curator-avatar"
    assert warm.scenes[0].data == {}
    assert warm.variants[0].layers == {}

    social = parse_editable_media_manifest(_read_manifest("social"), CONTRACT)
    assert social.production is not None
    assert social.production.content_unit_id == "social-card-placement-demo"
    assert social.variants[2].layers["short-title"].visible is True
    assert set(social.quality.variant_overrides) == {
        "portrait-3x4",
        "landscape-21x9",
        "square-1x1",
    }
    merged = social.layer_values_for("square-1x1", "short-title")
    assert merged["rotation"] == 0
    assert merged["visible"] is True

    technology_cover = parse_editable_media_manifest(
        _read_manifest("technology_cover"),
        CONTRACT,
    )
    assert technology_cover.production is not None
    assert technology_cover.production.profile_id == "editorial-technology-diagram-cover"
    assert technology_cover.variants[0].canvas.width == 1500
    assert technology_cover.variants[0].canvas.height == 600
    assert {field.id for field in technology_cover.data_fields} >= {
        "title",
        "diagram_title",
        "input_label",
        "model_label",
        "result_label",
    }

    text_card = parse_editable_media_manifest(
        _read_manifest("text_card_glossary"),
        CONTRACT,
    )
    text_card_fields = {field.id: field for field in text_card.data_fields}
    assert text_card_fields["production_watermark"].default == "𝕏@0xCheshire"
    assert "production-watermark" in {layer.id for layer in text_card.layers}
    assert not any(field.id.startswith("creator_") for field in text_card.data_fields)
    text_card_sources = json.loads(
        (PACKAGES["text_card_glossary"] / "media-sources.json").read_text(encoding="utf-8")
    )
    assert text_card_sources["sources"] == []

    icon_directory = parse_editable_media_manifest(
        _read_manifest("dark_icon_directory"),
        CONTRACT,
    )
    icon_directory_fields = {field.id: field for field in icon_directory.data_fields}
    assert icon_directory.component.id == "dark-icon-directory-card"
    assert icon_directory.default_variant_id == "portrait-4x5"
    assert icon_directory_fields["items"].kind == "table"
    assert len(icon_directory_fields["items"].default) == 8


def test_v6_frame_readiness_never_exceeds_the_protocol_maximum() -> None:
    manifest = _read_manifest("starter")
    frame_readiness = manifest["frame_readiness"]
    assert isinstance(frame_readiness, dict)
    frame_readiness["maximum_timeout_ms"] = 30_001

    with pytest.raises(ValueError, match="maximum_timeout_ms"):
        parse_editable_media_manifest(manifest, CONTRACT)


def test_v6_media_sources_require_an_explicit_pipeline_binding() -> None:
    source_manifest = json.loads((PACKAGES["warm"] / "media-sources.json").read_text(encoding="utf-8"))
    source = source_manifest["sources"][0]

    parsed = WebMediaSourcesManifest.model_validate(source_manifest)
    assert parsed.sources[0].binding.pipeline == "browser"

    source.pop("binding")
    with pytest.raises(ValueError, match="binding"):
        WebMediaSourcesManifest.model_validate(source_manifest)


@pytest.mark.parametrize(
    ("media_type", "binding", "message"),
    (
        (
            "audio",
            {"pipeline": "browser"},
            "audio sources must use the native-audio pipeline",
        ),
        (
            "photo",
            {
                "pipeline": "native-underlay",
                "fit": "cover",
                "playback": "hold",
                "source_in_ms": 0,
                "audio": "exclude",
                "gain_db": 0,
            },
            "Only editable media video sources can use native-underlay",
        ),
    ),
)
def test_v6_rejects_media_type_and_pipeline_mismatches(
    media_type: str,
    binding: dict[str, object],
    message: str,
) -> None:
    source_manifest = json.loads((PACKAGES["warm"] / "media-sources.json").read_text(encoding="utf-8"))
    source = source_manifest["sources"][0]
    source["media_type"] = media_type
    source["binding"] = binding

    with pytest.raises(ValueError, match=message):
        WebMediaSourcesManifest.model_validate(source_manifest)


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda manifest: manifest["data_fields"][0].__setitem__("kind", "image"),
            "kind",
        ),
        (
            lambda manifest: manifest.__setitem__("entry", "../../outside/index.html"),
            "entry",
        ),
        (
            lambda manifest: manifest.__setitem__("entry", "https://example.com/index.html"),
            "entry",
        ),
    ),
)
def test_v6_rejects_removed_data_kinds_and_non_package_paths(
    mutate,
    message: str,
) -> None:
    manifest = _read_manifest("starter")
    mutate(manifest)
    with pytest.raises(ValueError, match=message):
        parse_editable_media_manifest(manifest, CONTRACT)
