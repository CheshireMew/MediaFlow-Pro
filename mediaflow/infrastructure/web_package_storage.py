from __future__ import annotations

import hashlib
import os
import shutil
import stat
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from mediaflow.application.web_package_files import (
    MANIFEST_FILE_NAME,
    PUBLICATION_TOKEN_HEX_CHARS,
    WebPackagePublication,
    WebPackageReceipt,
    WebPackageTree,
    parse_publication_receipt,
    publication_receipt_json,
    publication_token,
    validate_web_package_paths,
)
from mediaflow.application.web_package_storage import (
    WebPackageReceiptLocation,
    WebPackageResidual,
)
from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.storage_names import require_windows_interop_path


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
            children = sorted(entries, key=lambda item: item.name.casefold())
        for entry in children:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            if entry.is_symlink() or is_junction(path):
                raise ValueError(
                    f"Editable media packages cannot contain links or junctions: {relative}"
                )
            if entry.is_dir(follow_symlinks=False):
                directories.append(relative)
                stack.append(path)
                continue
            if entry.is_file(follow_symlinks=False):
                mode = entry.stat(follow_symlinks=False).st_mode
                if not stat.S_ISREG(mode):
                    raise ValueError(
                        f"Editable media packages only accept regular files: {relative}"
                    )
                files.append(relative)
                continue
            raise ValueError(
                f"Editable media packages only accept files and directories: {relative}"
            )
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
            raise RuntimeError(
                f"Editable media package changed while it was scanned: {relative}"
            )
        file_integrity[relative] = (content_length, file_digest.hexdigest())
    return WebPackageTree(
        root=root,
        directories=tuple(directories),
        files=tuple(files),
        file_integrity=file_integrity,
        source_hash=digest.hexdigest(),
    )


def editable_media_source_hash(package_root: Path) -> str:
    return scan_web_package(package_root).source_hash


def copy_web_package_file(source: str, destination: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    copied = 0
    with open(source, "rb") as input_stream, open(destination, "xb") as output_stream:
        while chunk := input_stream.read(1024 * 1024):
            output_stream.write(chunk)
            digest.update(chunk)
            copied += len(chunk)
    shutil.copystat(source, destination)
    return copied, digest.hexdigest()


def read_publication_receipt(path: Path) -> WebPackageReceipt:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise RuntimeError(f"Editable media publication receipt is invalid: {path}") from error
    return parse_publication_receipt(content, file_name=path.name)


class LocalWebPackageStorage:
    """Owns all local filesystem work for immutable editable-media packages."""

    def read_source_tree(self, source: str | Path) -> WebPackageTree:
        requested = Path(source).expanduser()
        if requested.is_symlink() or is_junction(requested):
            raise ValueError("Editable media package source cannot be a link or junction")
        requested_root = requested if requested.is_dir() else requested.parent
        if requested_root.is_symlink() or is_junction(requested_root):
            raise ValueError("Editable media package root cannot be a link or junction")
        path = requested.resolve(strict=True)
        package_root = path if path.is_dir() else path.parent
        if path.is_file() and path.name != MANIFEST_FILE_NAME:
            raise ValueError(
                f"Editable media import expects a directory or {MANIFEST_FILE_NAME}"
            )
        tree = scan_web_package(package_root)
        manifest_path = package_root / MANIFEST_FILE_NAME
        if not manifest_path.is_file():
            raise FileNotFoundError(manifest_path)
        return tree

    def read_package_text(self, tree: WebPackageTree, relative_path: str) -> str:
        relative = PurePosixPath(relative_path)
        if not relative_path.strip() or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Editable media package paths must be local relative paths")
        if relative.as_posix() not in tree.files:
            raise FileNotFoundError(tree.root / relative.as_posix())
        return tree.root.joinpath(*relative.parts).read_text(encoding="utf-8")

    def scan_package(self, package_root: Path) -> WebPackageTree:
        return scan_web_package(package_root)

    def stage_package(
        self,
        source_tree: WebPackageTree,
        publication: WebPackagePublication,
    ) -> WebPackageTree:
        validate_web_package_paths(
            source_tree,
            publication.staging,
            publication.final,
            publication.failure,
        )
        for path in (
            publication.staging,
            publication.final,
            publication.failure,
            publication.receipt,
            publication.failed_receipt,
        ):
            require_windows_interop_path(path)
            if path.exists():
                raise FileExistsError(path)
        publication.staging.parent.mkdir(parents=True, exist_ok=True)
        publication.staging.mkdir()
        for relative in source_tree.directories:
            publication.staging.joinpath(*PurePosixPath(relative).parts).mkdir(parents=True)

        def copy_relative(relative: str) -> tuple[str, tuple[int, str]]:
            source = source_tree.root.joinpath(*PurePosixPath(relative).parts)
            destination = publication.staging.joinpath(*PurePosixPath(relative).parts)
            return relative, copy_web_package_file(str(source), str(destination))

        copied_integrity: dict[str, tuple[int, str]] = {}
        workers = max(1, min(4, len(source_tree.files)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for relative, integrity in pool.map(copy_relative, source_tree.files):
                copied_integrity[relative] = integrity
        if copied_integrity != source_tree.file_integrity:
            raise RuntimeError("Editable media package changed while it was being copied")
        return WebPackageTree(
            root=publication.staging,
            directories=source_tree.directories,
            files=source_tree.files,
            file_integrity=copied_integrity,
            source_hash=source_tree.source_hash,
        )

    def publish(self, publication: WebPackagePublication) -> None:
        if publication.published:
            raise RuntimeError("Editable media package is already published")
        atomic_write_text(
            publication.receipt,
            publication_receipt_json(
                asset_id=publication.asset_id,
                source_hash=publication.source_hash,
                token=publication.token,
                status="pending",
            ),
            durable=True,
        )
        publication.staging.replace(publication.final)
        publication.published = True

    def mark_committed(self, publication: WebPackagePublication) -> None:
        if not publication.published or not publication.final.is_dir():
            raise RuntimeError("Editable media package was not published")
        atomic_write_text(
            publication.receipt,
            publication_receipt_json(
                asset_id=publication.asset_id,
                source_hash=publication.source_hash,
                token=publication.token,
                status="committed",
            ),
            durable=True,
        )

    def archive_failed(self, publication: WebPackagePublication) -> None:
        package = publication.final if publication.final.exists() else publication.staging
        if package.exists():
            publication.failure.parent.mkdir(parents=True, exist_ok=True)
            package.replace(publication.failure)
        if publication.receipt.exists():
            publication.failed_receipt.parent.mkdir(parents=True, exist_ok=True)
            publication.receipt.replace(publication.failed_receipt)

    def staging_residuals(self, project_dir: Path) -> tuple[WebPackageResidual, ...]:
        staging_root = project_dir.resolve() / "staging" / "web"
        receipt_root = project_dir.resolve() / "sources" / "web" / "receipts"
        if not staging_root.is_dir():
            return ()
        residuals: list[WebPackageResidual] = []
        for staging in sorted(staging_root.iterdir()):
            token = publication_token(staging.name, prefix="s-")
            if token is not None and staging.is_dir():
                residuals.append(
                    WebPackageResidual(
                        token=token,
                        package=staging,
                        receipt=receipt_root / f"r-{token}.json",
                    )
                )
        return tuple(residuals)

    def publication_receipts(
        self,
        project_dir: Path,
    ) -> tuple[WebPackageReceiptLocation, ...]:
        publication_root = project_dir.resolve() / "sources" / "web"
        receipt_root = publication_root / "receipts"
        if not receipt_root.is_dir():
            return ()
        locations: list[WebPackageReceiptLocation] = []
        for path in sorted(receipt_root.glob("r-*.json")):
            receipt = read_publication_receipt(path)
            locations.append(
                WebPackageReceiptLocation(
                    path=path,
                    final=publication_root / receipt.directory,
                    receipt=receipt,
                )
            )
        return tuple(locations)

    def mark_receipt_committed(self, location: WebPackageReceiptLocation) -> None:
        receipt = location.receipt
        atomic_write_text(
            location.path,
            publication_receipt_json(
                asset_id=receipt.asset_id,
                source_hash=receipt.source_hash,
                token=receipt.token,
                status="committed",
            ),
            durable=True,
        )

    def archive_residual(
        self,
        project_dir: Path,
        residual: WebPackageResidual,
    ) -> None:
        archive_token = residual.token
        archive_root = project_dir / "archive" / "web"
        archive = archive_root / f"f-{archive_token}"
        archived_receipt = archive_root / f"r-{archive_token}.json"
        while archive.exists() or archived_receipt.exists():
            archive_token = hashlib.sha256(uuid.uuid4().bytes).hexdigest()[
                :PUBLICATION_TOKEN_HEX_CHARS
            ]
            archive = archive_root / f"f-{archive_token}"
            archived_receipt = archive_root / f"r-{archive_token}.json"
        if residual.package.exists():
            tree = scan_web_package(residual.package)
            validate_web_package_paths(tree, archive)
            archive.parent.mkdir(parents=True, exist_ok=True)
            residual.package.replace(archive)
        if residual.receipt.exists():
            require_windows_interop_path(archived_receipt)
            archived_receipt.parent.mkdir(parents=True, exist_ok=True)
            residual.receipt.replace(archived_receipt)

    def paths_equal(self, left: Path, right: Path) -> bool:
        return left.resolve() == right.resolve()
