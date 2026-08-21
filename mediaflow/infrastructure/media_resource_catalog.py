from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import stat
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, cast

from jsonschema import Draft202012Validator

from mediaflow.domain.media_resources import (
    EditableMediaResourceAdoption,
    MediaFileResourceAdoption,
    MediaResourceCatalog,
    MediaResourceCatalogItem,
)
from mediaflow.file_digest import sha256_file
from mediaflow.infrastructure.editable_media_contract import (
    validate_editable_media_document,
)
from mediaflow.infrastructure.web_package_storage import is_junction

MEDIA_RESOURCE_CATALOG_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "resources"
    / "contracts"
    / "media-resource-catalog.v1.schema.json"
)
CATALOG_FILE_NAME = "catalog.json"
EDITABLE_MEDIA_FILE_NAME = "editable-media.json"


@dataclass(frozen=True, slots=True)
class LoadedMediaResourceCatalog:
    root: Path
    path: Path
    catalog: MediaResourceCatalog

    def item_path(self, value: str) -> Path:
        return resolve_catalog_path(self.root, value)


@lru_cache(maxsize=1)
def media_resource_catalog_validator() -> Draft202012Validator:
    schema = json.loads(MEDIA_RESOURCE_CATALOG_SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise RuntimeError("Media resource catalog schema root must be an object")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(cast(dict[str, Any], schema))


def resolve_catalog_path(root: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if not value or relative.is_absolute() or ".." in relative.parts or "\\" in value:
        raise ValueError(f"Media resource catalog path must be local and relative: {value!r}")
    target = root.joinpath(*relative.parts).resolve(strict=True)
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Media resource catalog path escapes its root: {value}") from error
    return target


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or is_junction(path):
        raise ValueError(f"{label} cannot be a link or junction: {path}")
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode):
        raise ValueError(f"{label} must be a regular file: {path}")
    return path


def _mime_type(path: Path) -> str:
    if path.suffix.lower() == ".cube":
        return "application/x-cube-lut"
    value, _encoding = mimetypes.guess_type(path.name)
    return value or "application/octet-stream"


def media_resource_tree_sha256(root: Path) -> str:
    absolute = root.resolve(strict=True)
    if not absolute.is_dir() or absolute.is_symlink() or is_junction(absolute):
        raise ValueError("Media resource package root must be a regular directory")
    files: list[tuple[str, Path]] = []
    stack = [absolute]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda entry: entry.name.casefold())
        for entry in children:
            path = Path(entry.path)
            relative = path.relative_to(absolute).as_posix()
            if entry.is_symlink() or is_junction(path):
                raise ValueError(f"Media resource packages cannot contain links: {relative}")
            if entry.is_dir(follow_symlinks=False):
                stack.append(path)
            elif entry.is_file(follow_symlinks=False):
                _regular_file(path, "Media resource package entry")
                files.append((relative, path))
            else:
                raise ValueError(f"Unsupported media resource package entry: {relative}")
    digest = hashlib.sha256()
    for relative, path in sorted(files, key=lambda item: item[0]):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _validate_preview(loaded: LoadedMediaResourceCatalog, item: MediaResourceCatalogItem) -> None:
    if item.preview.type == "none":
        return
    preview = _regular_file(
        loaded.item_path(item.preview.path),
        f"Resource {item.stable_key} preview",
    )
    if _mime_type(preview) != item.preview.mime_type:
        raise ValueError(f"Resource {item.stable_key} preview MIME type does not match its file")


def _validate_adoption(loaded: LoadedMediaResourceCatalog, item: MediaResourceCatalogItem) -> None:
    adoption = item.adoption
    if isinstance(adoption, MediaFileResourceAdoption):
        source = _regular_file(
            loaded.item_path(adoption.file),
            f"Resource {item.stable_key} file",
        )
        if source.stat().st_size != adoption.bytes or sha256_file(source) != adoption.sha256:
            raise ValueError(f"Resource {item.stable_key} file integrity does not match its catalog")
        if _mime_type(source) != adoption.mime_type:
            raise ValueError(f"Resource {item.stable_key} MIME type does not match its file")
        return
    if not isinstance(adoption, EditableMediaResourceAdoption):
        return
    package = loaded.item_path(adoption.package)
    if not package.is_dir() or package.is_symlink() or is_junction(package):
        raise ValueError(f"Resource {item.stable_key} editable-media package is invalid")
    manifest_path = _regular_file(
        package / EDITABLE_MEDIA_FILE_NAME,
        f"Resource {item.stable_key} editable-media manifest",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_editable_media_document(manifest)
    if sha256_file(manifest_path) != adoption.manifest_sha256:
        raise ValueError(f"Resource {item.stable_key} manifest integrity does not match")
    if media_resource_tree_sha256(package) != adoption.package_sha256:
        raise ValueError(f"Resource {item.stable_key} package integrity does not match")


def load_media_resource_catalog(value: str | Path) -> LoadedMediaResourceCatalog:
    requested = Path(value).expanduser()
    path = requested / CATALOG_FILE_NAME if requested.is_dir() else requested
    path = _regular_file(path.resolve(strict=True), "Media resource catalog")
    document = json.loads(path.read_text(encoding="utf-8"))
    errors = sorted(
        media_resource_catalog_validator().iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        details = [
            f"$.{'.'.join(map(str, error.absolute_path))}: {error.message}"
            for error in errors
        ]
        raise ValueError("Media resource catalog schema validation failed:\n- " + "\n- ".join(details))
    catalog = MediaResourceCatalog.model_validate(document)
    loaded = LoadedMediaResourceCatalog(root=path.parent, path=path, catalog=catalog)
    for item in catalog.items:
        _validate_preview(loaded, item)
        _validate_adoption(loaded, item)
    return loaded
