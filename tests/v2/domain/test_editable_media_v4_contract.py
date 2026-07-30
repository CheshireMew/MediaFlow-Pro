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
    "starter": FIXTURES / "editable-media-v4",
    "warm": FIXTURES / "editable-media-v4-cases" / "warm-paper-project-list",
    "social": FIXTURES / "editable-media-v4-cases" / "social-evidence-variants",
}
CORPUS_SCHEMA = (
    FIXTURES
    / "editable-media-v4-contract"
    / "editable-media.v4.schema.json"
)


def _read_manifest(name: str) -> dict[str, object]:
    return json.loads(
        (PACKAGES[name] / "editable-media.json").read_text(encoding="utf-8")
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", tuple(PACKAGES))
def test_generated_packages_match_origins_and_v4_contract(name: str) -> None:
    package = PACKAGES[name]
    origin = json.loads((package / "fixture-origin.json").read_text(encoding="utf-8"))
    for relative, expected_hash in origin["files"].items():
        path = package.joinpath(*relative.split("/"))
        assert path.is_file()
        assert _sha256(path) == expected_hash
    manifest = parse_editable_media_manifest(_read_manifest(name))
    assert manifest.version == 4


def test_mediaflow_executes_the_synced_visual_multimedia_v4_schema() -> None:
    assert EDITABLE_MEDIA_SCHEMA_PATH.read_bytes() == CORPUS_SCHEMA.read_bytes()


def test_rich_v4_features_are_first_class_contract_fields() -> None:
    warm = parse_editable_media_manifest(_read_manifest("warm"))
    warm_fields = {field.id: field for field in warm.data_fields}
    assert warm_fields["creator_avatar"].kind == "media-source"
    assert warm_fields["legend_items"].kind == "list"
    assert warm.layout_contracts[0].asset_slots[0].id == "creator-avatar"
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


def test_v4_media_sources_require_an_explicit_pipeline_binding() -> None:
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
def test_v4_rejects_media_type_and_pipeline_mismatches(
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
def test_v4_rejects_removed_data_kinds_and_non_package_paths(
    mutate,
    message: str,
) -> None:
    manifest = _read_manifest("starter")
    mutate(manifest)
    with pytest.raises(ValueError, match=message):
        parse_editable_media_manifest(manifest)
