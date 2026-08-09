from __future__ import annotations

import hashlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import TypeVar

from mediaflow.application import web_package_contract as web_contract
from mediaflow.application import web_package_files as web_files
from mediaflow.application.ports import WebApplicationDocuments, WebPackageValidatorPort
from mediaflow.atomic_file import atomic_write_text
from mediaflow.domain.enums import AssetKind, AssetOrigin
from mediaflow.domain.model_base import new_id
from mediaflow.domain.project import Asset, MediaMetadata
from mediaflow.domain.storage_names import require_windows_interop_path
from mediaflow.domain.web_media import (
    EditableMediaManifest,
    WebAssetSpec,
    WebMediaSourcesManifest,
    parse_editable_media_manifest_json,
    web_media_sources_have_audio,
)

T = TypeVar("T")


class WebPackageService:
    """Imports, verifies, and publishes editable-media packages."""

    def __init__(
        self,
        repository: WebApplicationDocuments,
        runtime_validator: WebPackageValidatorPort,
    ) -> None:
        self.repository = repository
        self._runtime_validator = runtime_validator
        if not repository.read_only:
            self._reconcile_publications()

    def import_package(self, source: str | Path) -> Asset:
        package_tree, manifest, media_sources = self.read_package_tree(source)
        project = self.repository.catalog.get_project()
        asset_id = new_id()
        publication = self.stage_package(
            package_tree,
            manifest,
            media_sources,
            asset_id=asset_id,
        )
        main_sequence = self.repository.catalog.get_sequence(project.main_sequence_id)
        duration_frames = max(
            1,
            round(publication.manifest.duration_ms * main_sequence.profile.fps / 1000),
        )
        default_variant = publication.manifest.default_variant
        has_audio = web_media_sources_have_audio(publication.media_sources)
        asset = Asset(
            id=asset_id,
            project_id=project.id,
            name=(
                publication.manifest.component.name
                if publication.manifest.component
                else package_tree.root.name
            ),
            kind=AssetKind.WEB,
            origin=AssetOrigin.EXTERNAL,
            path=str(publication.entry),
            managed=True,
            metadata=MediaMetadata(
                duration_frames=duration_frames,
                width=default_variant.canvas.width,
                height=default_variant.canvas.height,
                fps_numerator=main_sequence.profile.fps_numerator,
                fps_denominator=main_sequence.profile.fps_denominator,
                has_video=True,
                has_audio=has_audio,
            ),
        )
        return self.commit_publication(
            publication,
            lambda: self._save_imported_package(
                asset,
                publication,
            ),
        )

    def _save_imported_package(
        self,
        asset: Asset,
        publication: web_files.WebPackagePublication,
    ) -> Asset:
        stored = self.repository.catalog.add_asset(asset)
        self.repository.web.save_web_asset_spec(
            WebAssetSpec(
                asset_id=asset.id,
                manifest=publication.manifest,
                source_hash=publication.source_hash,
            )
        )
        return stored

    def inspect_asset(self, asset_id: str) -> WebAssetSpec:
        asset = self.repository.catalog.get_asset(asset_id)
        if asset.kind != AssetKind.WEB:
            raise ValueError("Asset is not editable web media")
        spec = self.repository.web.get_web_asset_spec(asset_id)
        actual_hash = web_files.editable_media_source_hash(
            web_files.web_package_root(
                self.repository.catalog.resolve_asset_path(asset),
                spec.manifest,
            )
        )
        if actual_hash != spec.source_hash:
            raise RuntimeError("Editable media package changed after import; rebind it as a new package")
        return spec

    @staticmethod
    def read_package_tree(
        source: str | Path,
    ) -> tuple[
        web_files.WebPackageTree,
        EditableMediaManifest,
        WebMediaSourcesManifest,
    ]:
        requested = Path(source).expanduser()
        if requested.is_symlink() or web_files.is_junction(requested):
            raise ValueError("Editable media package source cannot be a link or junction")
        requested_root = requested if requested.is_dir() else requested.parent
        if requested_root.is_symlink() or web_files.is_junction(requested_root):
            raise ValueError("Editable media package root cannot be a link or junction")
        path = requested.resolve(strict=True)
        package_root = path if path.is_dir() else path.parent
        manifest_path = package_root / web_files.MANIFEST_FILE_NAME
        if path.is_file() and path.name != web_files.MANIFEST_FILE_NAME:
            raise ValueError(f"Editable media import expects a directory or {web_files.MANIFEST_FILE_NAME}")
        tree = web_files.scan_web_package(package_root)
        if not manifest_path.is_file():
            raise FileNotFoundError(manifest_path)
        manifest = parse_editable_media_manifest_json(manifest_path.read_text(encoding="utf-8"))
        media_sources = web_contract.validate_package_files(tree, manifest)
        return tree, manifest, media_sources

    @staticmethod
    def read_package(source: str | Path) -> tuple[Path, EditableMediaManifest]:
        tree, manifest, _media_sources = WebPackageService.read_package_tree(source)
        return tree.root, manifest

    def stage_package(
        self,
        source_tree: web_files.WebPackageTree,
        manifest: EditableMediaManifest,
        media_sources: WebMediaSourcesManifest,
        *,
        asset_id: str,
    ) -> web_files.WebPackagePublication:
        token = hashlib.sha256(f"{asset_id}\0{source_tree.source_hash}\0{new_id()}".encode()).hexdigest()[
            : web_files.PUBLICATION_TOKEN_HEX_CHARS
        ]
        project_dir = self.repository.project_dir.resolve()
        staging = project_dir / "staging" / "web" / f"s-{token}"
        final = project_dir / "sources" / "web" / f"p-{token}"
        failure = project_dir / "archive" / "web" / f"f-{token}"
        receipt = project_dir / "sources" / "web" / "receipts" / f"r-{token}.json"
        failed_receipt = project_dir / "archive" / "web" / f"r-{token}.json"
        publication = web_files.WebPackagePublication(
            asset_id=asset_id,
            manifest=manifest,
            media_sources=media_sources,
            source_hash=source_tree.source_hash,
            token=token,
            staging=staging,
            final=final,
            failure=failure,
            receipt=receipt,
            failed_receipt=failed_receipt,
        )
        web_files.validate_web_package_paths(
            source_tree,
            staging,
            final,
            failure,
        )
        for path in (staging, final, failure, receipt, failed_receipt):
            require_windows_interop_path(path)
            if path.exists():
                raise FileExistsError(path)
        staging.parent.mkdir(parents=True, exist_ok=True)
        try:
            staging.mkdir()
            for relative in source_tree.directories:
                staging.joinpath(*PurePosixPath(relative).parts).mkdir(parents=True)

            def copy_relative(
                relative: str,
            ) -> tuple[str, tuple[int, str]]:
                source = source_tree.root.joinpath(*PurePosixPath(relative).parts)
                destination = staging.joinpath(*PurePosixPath(relative).parts)
                return relative, web_files.copy_web_package_file(
                    str(source),
                    str(destination),
                )

            copied_integrity: dict[str, tuple[int, str]] = {}
            with ThreadPoolExecutor(max_workers=max(1, min(4, len(source_tree.files)))) as pool:
                for relative, integrity in pool.map(
                    copy_relative,
                    source_tree.files,
                ):
                    copied_integrity[relative] = integrity
            if copied_integrity != source_tree.file_integrity:
                raise RuntimeError("Editable media package changed while it was being copied")
            copied_tree = web_files.WebPackageTree(
                root=staging,
                directories=source_tree.directories,
                files=source_tree.files,
                file_integrity=copied_integrity,
                source_hash=source_tree.source_hash,
            )
            copied_manifest = parse_editable_media_manifest_json(
                (staging / web_files.MANIFEST_FILE_NAME).read_text(encoding="utf-8")
            )
            copied_media_sources = web_contract.validate_package_files(
                copied_tree,
                copied_manifest,
            )
            if copied_manifest != manifest:
                raise RuntimeError("Editable media manifest changed while it was being copied")
            if copied_media_sources != media_sources:
                raise RuntimeError("Editable media source bindings changed while they were being copied")
            self._runtime_validator.validate(staging, copied_manifest)
            publication.manifest = copied_manifest
            publication.media_sources = copied_media_sources
            return publication
        except BaseException as error:
            try:
                publication.archive_failed()
            except BaseException as archive_error:
                raise BaseExceptionGroup(
                    "Editable media staging and failure archival both failed",
                    [error, archive_error],
                ) from error
            raise

    def preflight_package_tree(
        self,
        tree: web_files.WebPackageTree,
    ) -> None:
        token = "0" * web_files.PUBLICATION_TOKEN_HEX_CHARS
        project_dir = self.repository.project_dir.resolve()
        web_files.validate_web_package_paths(
            tree,
            project_dir / "staging" / "web" / f"s-{token}",
            project_dir / "sources" / "web" / f"p-{token}",
            project_dir / "archive" / "web" / f"f-{token}",
        )

    def _reconcile_publications(self) -> None:
        project_dir = self.repository.project_dir.resolve()
        staging_root = project_dir / "staging" / "web"
        receipt_root = project_dir / "sources" / "web" / "receipts"
        if staging_root.is_dir():
            for staging in sorted(staging_root.iterdir()):
                token = web_files.publication_token(staging.name, prefix="s-")
                if token is None or not staging.is_dir():
                    continue
                receipt = receipt_root / f"r-{token}.json"
                self._archive_residual(staging, receipt, token)
        if not receipt_root.is_dir():
            return
        for receipt in sorted(receipt_root.glob("r-*.json")):
            payload = web_files.read_publication_receipt(receipt)
            token = str(payload["token"])
            final = project_dir / "sources" / "web" / str(payload["directory"])
            if payload["status"] == "committed":
                tree = web_files.scan_web_package(final)
                if tree.source_hash != payload["source_hash"]:
                    raise RuntimeError("Editable media package changed after it was committed")
                continue
            references = self._web_assets_referencing(final)
            if references:
                if references != [str(payload["asset_id"])]:
                    raise RuntimeError("Editable media publication receipt does not match its asset")
                tree = web_files.scan_web_package(final)
                if tree.source_hash != payload["source_hash"]:
                    raise RuntimeError("Editable media publication changed before it was committed")
                atomic_write_text(
                    receipt,
                    web_files.publication_receipt_json(
                        asset_id=str(payload["asset_id"]),
                        source_hash=str(payload["source_hash"]),
                        token=token,
                        status="committed",
                    ),
                    durable=True,
                )
                continue
            self._archive_residual(final, receipt, token)

    def _web_assets_referencing(
        self,
        package_root: Path,
    ) -> list[str]:
        references: list[str] = []
        for asset in self.repository.catalog.list_assets():
            if asset.kind != AssetKind.WEB:
                continue
            spec = self.repository.web.get_web_asset_spec(asset.id)
            current_root = web_files.web_package_root(
                self.repository.catalog.resolve_asset_path(asset),
                spec.manifest,
            )
            if current_root == package_root.resolve():
                references.append(asset.id)
        return sorted(references)

    def commit_publication(
        self,
        publication: web_files.WebPackagePublication,
        change: Callable[[], T],
    ) -> T:
        try:
            with self.repository.transaction():
                publication.publish()
                self.repository.enlist_transaction_publication(
                    on_commit=publication.mark_committed,
                    on_rollback=lambda _error: publication.archive_failed(),
                )
                return change()
        except BaseException as error:
            try:
                publication.archive_failed()
            except BaseException as archive_error:
                error.add_note(f"网页包事务回滚后归档失败：{archive_error}")
            raise

    def _archive_residual(
        self,
        package: Path,
        receipt: Path,
        token: str,
    ) -> None:
        archive_token = token
        archive = self.repository.project_dir / "archive" / "web" / f"f-{archive_token}"
        archived_receipt = self.repository.project_dir / "archive" / "web" / f"r-{archive_token}.json"
        while archive.exists() or archived_receipt.exists():
            archive_token = hashlib.sha256(new_id().encode()).hexdigest()[
                : web_files.PUBLICATION_TOKEN_HEX_CHARS
            ]
            archive = self.repository.project_dir / "archive" / "web" / f"f-{archive_token}"
            archived_receipt = self.repository.project_dir / "archive" / "web" / f"r-{archive_token}.json"
        if package.exists():
            tree = web_files.scan_web_package(package)
            web_files.validate_web_package_paths(tree, archive)
            archive.parent.mkdir(parents=True, exist_ok=True)
            package.replace(archive)
        if receipt.exists():
            require_windows_interop_path(archived_receipt)
            archived_receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.replace(archived_receipt)
