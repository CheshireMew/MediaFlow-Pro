from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from mediaflow.domain.editable_media_contract import EDITABLE_MEDIA_SCHEMA_PATH
from mediaflow.domain.web_media import (
    WebMediaSourcesManifest,
    parse_editable_media_manifest,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PACKAGES = {
    "starter": FIXTURES / "editable-media-v5",
    "warm": FIXTURES / "editable-media-v5-cases" / "warm-paper-project-list",
    "social": FIXTURES / "editable-media-v5-cases" / "social-evidence-variants",
    "text_card_glossary": (
        FIXTURES
        / "editable-media-v5-cases"
        / "text-card-glossary"
    ),
}
PRODUCERS = {
    "starter": "visual-multimedia/assets/web-media-starter",
    "warm": "visual-multimedia/assets/web-card-cases/warm-paper-project-list",
    "social": "visual-multimedia/assets/web-card-cases/social-evidence-variants",
    "text_card_glossary": (
        "visual-multimedia/assets/web-card-cases/text-card-glossary"
    ),
}
CORPUS_SCHEMA = (
    FIXTURES
    / "editable-media-v5-contract"
    / "editable-media.v5.schema.json"
)


def _read_manifest(name: str) -> dict[str, object]:
    return json.loads(
        (PACKAGES[name] / "editable-media.json").read_text(encoding="utf-8")
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", tuple(PACKAGES))
def test_generated_packages_match_origins_and_v5_contract(name: str) -> None:
    package = PACKAGES[name]
    origin = json.loads((package / "fixture-origin.json").read_text(encoding="utf-8"))
    assert origin["producer"] == PRODUCERS[name]
    assert origin["editable_media_version"] == 5
    assert {
        path.relative_to(package).as_posix()
        for path in package.rglob("*")
        if path.is_file()
    } == set(origin["files"]) | {"fixture-origin.json"}
    for relative, expected_hash in origin["files"].items():
        path = package.joinpath(*relative.split("/"))
        assert path.is_file()
        assert _sha256(path) == expected_hash
    manifest = parse_editable_media_manifest(_read_manifest(name))
    assert manifest.version == 5


def test_mediaflow_executes_the_synced_visual_multimedia_v5_schema() -> None:
    assert EDITABLE_MEDIA_SCHEMA_PATH.read_bytes() == CORPUS_SCHEMA.read_bytes()


def test_rich_v5_features_are_first_class_contract_fields() -> None:
    starter = parse_editable_media_manifest(_read_manifest("starter"))
    parameters = {item.id: item for item in starter.parameters}
    assert parameters["spring_strength"].control == "slider"
    assert parameters["stagger_interval_ms"].scope == "scene"
    assert starter.scenes[0].parameters["orbit_radius_px"] == 32
    assert starter.scenes[0].motion.camera is not None
    assert starter.scenes[0].steps[1].state_kind == "change"

    warm = parse_editable_media_manifest(_read_manifest("warm"))
    warm_fields = {field.id: field for field in warm.data_fields}
    assert warm_fields["curator_avatar"].kind == "media-source"
    assert warm_fields["curator_label"].default == "整理"
    assert warm_fields["legend_items"].kind == "list"
    assert warm.layout_contracts[0].asset_slots[0].id == "curator-avatar"
    assert warm.scenes[0].data == {}
    assert warm.variants[0].layers == {}

    social = parse_editable_media_manifest(_read_manifest("social"))
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

    text_card = parse_editable_media_manifest(
        _read_manifest("text_card_glossary")
    )
    text_card_fields = {field.id: field for field in text_card.data_fields}
    assert text_card_fields["production_watermark"].default == "𝕏@0xCheshire"
    assert "production-watermark" in {layer.id for layer in text_card.layers}
    assert not any(field.id.startswith("creator_") for field in text_card.data_fields)
    text_card_sources = json.loads(
        (
            PACKAGES["text_card_glossary"] / "media-sources.json"
        ).read_text(encoding="utf-8")
    )
    assert text_card_sources["sources"] == []


def test_v5_media_sources_require_an_explicit_pipeline_binding() -> None:
    source_manifest = json.loads(
        (
            PACKAGES["warm"]
            / "media-sources.json"
        ).read_text(encoding="utf-8")
    )
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
def test_v5_rejects_media_type_and_pipeline_mismatches(
    media_type: str,
    binding: dict[str, object],
    message: str,
) -> None:
    source_manifest = json.loads(
        (
            PACKAGES["warm"]
            / "media-sources.json"
        ).read_text(encoding="utf-8")
    )
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
            lambda manifest: manifest.__setitem__(
                "entry", "../../outside/index.html"
            ),
            "entry",
        ),
        (
            lambda manifest: manifest.__setitem__(
                "entry", "https://example.com/index.html"
            ),
            "entry",
        ),
    ),
)
def test_v5_rejects_removed_data_kinds_and_non_package_paths(
    mutate,
    message: str,
) -> None:
    manifest = _read_manifest("starter")
    mutate(manifest)
    with pytest.raises(ValueError, match=message):
        parse_editable_media_manifest(manifest)
