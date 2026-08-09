from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.storage_names import require_windows_interop_path
from mediaflow.domain.web_media import (
    EditableMediaManifest,
    WebMediaSourcesManifest,
)

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

    def publish(self) -> None:
        if self.published:
            raise RuntimeError("Editable media package is already published")
        atomic_write_text(
            self.receipt,
            publication_receipt_json(
                asset_id=self.asset_id,
                source_hash=self.source_hash,
                token=self.token,
                status="pending",
            ),
            durable=True,
        )
        self.staging.replace(self.final)
        self.published = True

    def mark_committed(self) -> None:
        if not self.published or not self.final.is_dir():
            raise RuntimeError("Editable media package was not published")
        atomic_write_text(
            self.receipt,
            publication_receipt_json(
                asset_id=self.asset_id,
                source_hash=self.source_hash,
                token=self.token,
                status="committed",
            ),
            durable=True,
        )

    def archive_failed(self) -> None:
        package = self.final if self.final.exists() else self.staging
        if package.exists():
            self.failure.parent.mkdir(parents=True, exist_ok=True)
            package.replace(self.failure)
        if self.receipt.exists():
            self.failed_receipt.parent.mkdir(parents=True, exist_ok=True)
            self.receipt.replace(self.failed_receipt)


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


def read_publication_receipt(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as error:
        raise RuntimeError(f"Editable media publication receipt is invalid: {path}") from error
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "asset_id",
        "source_hash",
        "token",
        "directory",
        "status",
    }:
        raise RuntimeError(f"Editable media publication receipt is invalid: {path}")
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
        or path.name != f"r-{token}.json"
        or publication_token(f"p-{token}", prefix="p-") is None
    ):
        raise RuntimeError(f"Editable media publication receipt is invalid: {path}")
    return payload


def is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    return bool(checker and checker())


def scan_web_package(package_root: Path) -> WebPackageTree:
    root = Path(package_root).resolve(strict=True)
    if not root.is_dir() or root.is_symlink() or is_junction(root):
        raise ValueError("Editable media package root must be a regular directory")
    directories: list[str] = []
    files: list[str] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            children = sorted(
                entries,
                key=lambda item: item.name.casefold(),
            )
        for entry in children:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            if entry.is_symlink() or is_junction(path):
                raise ValueError(f"Editable media packages cannot contain links or junctions: {relative}")
            if entry.is_dir(follow_symlinks=False):
                directories.append(relative)
                stack.append(path)
                continue
            if entry.is_file(follow_symlinks=False):
                mode = entry.stat(follow_symlinks=False).st_mode
                if not stat.S_ISREG(mode):
                    raise ValueError(f"Editable media packages only accept regular files: {relative}")
                files.append(relative)
                continue
            raise ValueError(f"Editable media packages only accept files and directories: {relative}")
    directories.sort()
    files.sort()
    digest = hashlib.sha256()
    file_integrity: dict[str, tuple[int, str]] = {}
    inventory = sorted(
        (
            *((relative, b"D") for relative in directories),
            *((relative, b"F") for relative in files),
        ),
        key=lambda item: (item[0], item[1]),
    )
    for relative, entry_type in inventory:
        encoded = relative.encode("utf-8")
        digest.update(entry_type)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        if entry_type == b"D":
            continue
        with root.joinpath(*PurePosixPath(relative).parts).open("rb") as stream:
            content_length = os.fstat(stream.fileno()).st_size
            digest.update(content_length.to_bytes(8, "big"))
            file_digest = hashlib.sha256()
            bytes_read = 0
            while chunk := stream.read(1024 * 1024):
                bytes_read += len(chunk)
                digest.update(chunk)
                file_digest.update(chunk)
        if bytes_read != content_length:
            raise RuntimeError(f"Editable media package changed while it was scanned: {relative}")
        file_integrity[relative] = (
            content_length,
            file_digest.hexdigest(),
        )
    return WebPackageTree(
        root=root,
        directories=tuple(directories),
        files=tuple(files),
        file_integrity=file_integrity,
        source_hash=digest.hexdigest(),
    )


def editable_media_source_hash(package_root: Path) -> str:
    return scan_web_package(package_root).source_hash


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


def copy_web_package_file(
    source: str,
    destination: str,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    copied = 0
    with (
        open(source, "rb") as input_stream,
        open(
            destination,
            "xb",
        ) as output_stream,
    ):
        while chunk := input_stream.read(1024 * 1024):
            output_stream.write(chunk)
            digest.update(chunk)
            copied += len(chunk)
    shutil.copystat(source, destination)
    return copied, digest.hexdigest()


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
