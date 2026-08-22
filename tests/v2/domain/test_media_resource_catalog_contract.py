from __future__ import annotations

import json
import mimetypes
import shutil
from pathlib import Path

import pytest

from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure.media_resource_catalog import (
    MEDIA_RESOURCE_CATALOG_SCHEMA_PATH,
    load_media_resource_catalog,
    media_resource_tree_sha256,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SYNCED_SCHEMA = (
    FIXTURES
    / "media-resource-catalog-v1-contract"
    / "media-resource-catalog.v1.schema.json"
)
SYNCED_PRODUCTION_CATALOG = (
    FIXTURES
    / "media-resource-catalog-v1-production"
    / "media-resource-catalog.json"
)


def _origin(kind: str = "builtin") -> dict[str, object]:
    return {
        "type": kind,
        "library_id": None,
        "library_version": None,
        "item_id": None,
        "content_sha256": None,
    }


def _rights() -> dict[str, str]:
    return {
        "status": "not-required",
        "license": "MediaFlow Pro built-in",
        "attribution": "",
        "terms_url": "",
    }


def _catalog(items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "protocol": "visual-multimedia-media-resource-catalog",
        "version": 1,
        "catalog_id": "mediaflow-test-resources",
        "catalog_version": "1.0.0",
        "name": "MediaFlow test resources",
        "description": "Contract fixture",
        "items": items,
    }


def _write_catalog(root: Path, items: list[dict[str, object]]) -> Path:
    path = root / "catalog.json"
    path.write_text(json.dumps(_catalog(items), ensure_ascii=False), encoding="utf-8")
    return path


def test_mediaflow_consumes_the_synced_resource_catalog_schema() -> None:
    assert SYNCED_SCHEMA.read_bytes() == MEDIA_RESOURCE_CATALOG_SCHEMA_PATH.read_bytes()


def test_mediaflow_consumes_the_synced_production_resource_catalog() -> None:
    origin = json.loads(
        (SYNCED_PRODUCTION_CATALOG.parent / "fixture-origin.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(origin["producer_revision"]) == 40
    int(origin["producer_revision"], 16)
    loaded = load_media_resource_catalog(SYNCED_PRODUCTION_CATALOG)

    assert loaded.catalog.catalog_id == "visual-multimedia-core-resources"
    assert loaded.catalog.catalog_version == "1.1.0"
    assert [item.category for item in loaded.catalog.items] == [
        "motion-graphic",
        "motion-graphic",
        "lut",
        "lut",
        "sound-effect",
        "sound-effect",
    ]
    assert all(item.preview.type != "none" for item in loaded.catalog.items)
    assert all(
        (loaded.root / item.preview.path).is_file()
        for item in loaded.catalog.items
    )
    assert all(
        (loaded.root / item.adoption.file).stat().st_size == item.adoption.bytes
        for item in loaded.catalog.items
        if item.category == "sound-effect"
    )


def test_resource_catalog_loads_builtin_presets_and_real_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sound = tmp_path / "resources" / "click.wav"
    sound.parent.mkdir(parents=True)
    sound.write_bytes(b"RIFF-real-catalog-fixture")
    sound_mime = "audio/wav"
    monkeypatch.setattr(mimetypes, "guess_type", lambda _name: ("audio/x-wav", None))
    items = [
        {
            "id": "dissolve",
            "resource_version": "1.0.0",
            "category": "transition",
            "name": "Dissolve",
            "description": "Built-in transition",
            "provider": "MediaFlow Pro",
            "tags": ["soft"],
            "capabilities": ["timeline-ready"],
            "featured_rank": 0,
            "preview": {"type": "none", "path": "", "mime_type": ""},
            "rights": _rights(),
            "origin": _origin(),
            "adoption": {
                "type": "editor-preset",
                "target": "transition",
                "preset_id": "dissolve",
                "parameters": {},
                "default_duration_frames": 15,
            },
        },
        {
            "id": "click-sfx",
            "resource_version": "1.0.0",
            "category": "sound-effect",
            "name": "Click",
            "description": "Real file fixture",
            "provider": "Test provider",
            "tags": ["click"],
            "capabilities": ["timeline-ready"],
            "featured_rank": None,
            "preview": {
                "type": "audio",
                "path": "resources/click.wav",
                "mime_type": sound_mime,
            },
            "rights": _rights(),
            "origin": _origin(),
            "adoption": {
                "type": "media-file",
                "file": "resources/click.wav",
                "sha256": sha256_file(sound),
                "bytes": sound.stat().st_size,
                "mime_type": sound_mime,
                "media_type": "audio",
                "placement": "audio-track",
            },
        },
    ]
    loaded = load_media_resource_catalog(_write_catalog(tmp_path, items))

    assert [item.stable_key for item in loaded.catalog.items] == [
        "dissolve@1.0.0",
        "click-sfx@1.0.0",
    ]


def test_resource_catalog_validates_editable_media_package_integrity(tmp_path: Path) -> None:
    source = FIXTURES / "editable-media-v6"
    package = tmp_path / "resources" / "editable-card"
    shutil.copytree(source, package)
    manifest = package / "editable-media.json"
    item = {
        "id": "editable-card",
        "resource_version": "1.0.0",
        "category": "motion-graphic",
        "name": "Editable card",
        "description": "Full editable package",
        "provider": "Test provider",
        "tags": ["editable-media"],
        "capabilities": ["editable", "timeline-ready"],
        "featured_rank": 0,
        "preview": {"type": "none", "path": "", "mime_type": ""},
        "rights": _rights(),
        "origin": _origin(),
        "adoption": {
            "type": "editable-media-package",
            "package": "resources/editable-card",
            "manifest_sha256": sha256_file(manifest),
            "package_sha256": media_resource_tree_sha256(package),
            "default_duration_frames": 150,
        },
    }
    catalog_path = _write_catalog(tmp_path, [item])

    assert load_media_resource_catalog(catalog_path).catalog.items[0].id == "editable-card"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        load_media_resource_catalog(catalog_path)
