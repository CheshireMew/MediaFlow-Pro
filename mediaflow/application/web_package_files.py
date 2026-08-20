from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from mediaflow.domain.storage_names import require_windows_interop_path
from mediaflow.domain.web_manifest import EditableMediaManifest
from mediaflow.domain.web_media_sources import WebMediaSourcesManifest

MANIFEST_FILE_NAME = "editable-media.json"
PUBLICATION_SCHEMA_VERSION = 1
PUBLICATION_TOKEN_HEX_CHARS = 24


@dataclass(frozen=True, slots=True)
class WebPackageTree:
    root: Path
    directories: tuple[str, ...]
    files: tuple[str, ...]
    file_integrity: Mapping[str, tuple[int, str]]
    source_hash: str


@dataclass(slots=True)
class WebPackagePublication:
    asset_id: str
    manifest: EditableMediaManifest
    media_sources: WebMediaSourcesManifest
    source_hash: str
    token: str
    staging: Path
    final: Path
    failure: Path
    receipt: Path
    failed_receipt: Path
    published: bool = False

    @property
    def entry(self) -> Path:
        return self.final.joinpath(*PurePosixPath(self.manifest.entry).parts)


@dataclass(frozen=True, slots=True)
class WebPackageReceipt:
    schema_version: int
    asset_id: str
    source_hash: str
    token: str
    directory: str
    status: Literal["pending", "committed"]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "asset_id": self.asset_id,
            "source_hash": self.source_hash,
            "token": self.token,
            "directory": self.directory,
            "status": self.status,
        }


def publication_receipt_json(
    *,
    asset_id: str,
    source_hash: str,
    token: str,
    status: Literal["pending", "committed"],
) -> str:
    return json.dumps(
        {
            "schema_version": PUBLICATION_SCHEMA_VERSION,
            "asset_id": asset_id,
            "source_hash": source_hash,
            "token": token,
            "directory": f"p-{token}",
            "status": status,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def publication_token(name: str, *, prefix: str) -> str | None:
    if not name.startswith(prefix):
        return None
    token = name[len(prefix) :]
    if len(token) != PUBLICATION_TOKEN_HEX_CHARS or any(
        character not in "0123456789abcdef" for character in token
    ):
        return None
    return token


def parse_publication_receipt(
    content: str,
    *,
    file_name: str,
) -> WebPackageReceipt:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Editable media publication receipt is invalid: {file_name}") from error
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "asset_id",
        "source_hash",
        "token",
        "directory",
        "status",
    }:
        raise RuntimeError(f"Editable media publication receipt is invalid: {file_name}")
    token = payload["token"]
    if (
        type(payload["schema_version"]) is not int
        or payload["schema_version"] != PUBLICATION_SCHEMA_VERSION
        or not isinstance(payload["asset_id"], str)
        or not payload["asset_id"]
        or not isinstance(payload["source_hash"], str)
        or len(payload["source_hash"]) != 64
        or any(character not in "0123456789abcdef" for character in payload["source_hash"])
        or not isinstance(token, str)
        or payload["directory"] != f"p-{token}"
        or payload["status"] not in {"pending", "committed"}
        or file_name != f"r-{token}.json"
        or publication_token(f"p-{token}", prefix="p-") is None
    ):
        raise RuntimeError(f"Editable media publication receipt is invalid: {file_name}")
    return WebPackageReceipt(
        schema_version=int(payload["schema_version"]),
        asset_id=str(payload["asset_id"]),
        source_hash=str(payload["source_hash"]),
        token=str(token),
        directory=str(payload["directory"]),
        status=payload["status"],
    )


def web_package_root_for_entry(
    entry_path: str | Path,
    manifest_entry: str,
) -> Path:
    entry = Path(entry_path).resolve()
    relative = PurePosixPath(manifest_entry)
    if not manifest_entry.strip() or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Editable media entry is not a local package path")
    root = entry
    for _part in relative.parts:
        root = root.parent
    expected = root.joinpath(*relative.parts).resolve()
    if expected != entry:
        raise ValueError("Editable media entry does not match its package manifest")
    return root


def web_package_root(
    entry_path: str | Path,
    manifest: EditableMediaManifest,
) -> Path:
    """Resolve a package root without assuming a top-level HTML entry."""

    return web_package_root_for_entry(entry_path, manifest.entry)


def validate_web_package_paths(
    tree: WebPackageTree,
    *roots: Path,
) -> None:
    relative_paths = (*tree.directories, *tree.files)
    for root in roots:
        require_windows_interop_path(root)
        seen: set[str] = set()
        for relative in relative_paths:
            target = root.joinpath(*PurePosixPath(relative).parts)
            require_windows_interop_path(target)
            normalized = str(target).casefold()
            if normalized in seen:
                raise ValueError("Editable media package contains colliding Windows paths")
            seen.add(normalized)
