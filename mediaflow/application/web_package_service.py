from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from mediaflow.application import web_package_contract as web_contract
from mediaflow.application import web_package_files as web_files
from mediaflow.application.ports import WebApplicationDocuments, WebPackageValidatorPort
from mediaflow.application.web_package_storage import (
    WebPackageResidual,
    WebPackageStorage,
)
from mediaflow.domain.editable_media_contract import EditableMediaContract
from mediaflow.domain.enums import AssetKind, AssetOrigin
from mediaflow.domain.model_base import new_id
from mediaflow.domain.project import Asset, MediaMetadata
from mediaflow.domain.web_manifest import (
    EditableMediaManifest,
    WebAssetSpec,
    parse_editable_media_manifest_json,
)
from mediaflow.domain.web_media_sources import (
    WebMediaSourcesManifest,
    web_media_sources_have_audio,
)

T = TypeVar("T")


class WebPackageService:
    """Imports, verifies, and publishes editable-media packages."""

    def __init__(
        self,
        repository: WebApplicationDocuments,
        runtime_validator: WebPackageValidatorPort,
        storage: WebPackageStorage,
        contract: EditableMediaContract,
    ) -> None:
        self.repository = repository
        self._runtime_validator = runtime_validator
        self._storage = storage
        self._contract = contract
        if not repository.read_only:
            self._reconcile_publications()

    def import_package(self, source: str | Path) -> Asset:
        package_tree, manifest, media_sources = self.read_package_tree(source)
        project = self.repository.projects.get_project()
        asset_id = new_id()
        publication = self.stage_package(
            package_tree,
            manifest,
            media_sources,
            asset_id=asset_id,
        )
        main_sequence = self.repository.sequences.get_sequence(project.main_sequence_id)
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
        stored = self.repository.assets.add_asset(asset)
        self.repository.web.save_web_asset_spec(
            WebAssetSpec(
                asset_id=asset.id,
                manifest=publication.manifest,
                source_hash=publication.source_hash,
            )
        )
        return stored

    def inspect_asset(self, asset_id: str) -> WebAssetSpec:
        asset = self.repository.assets.get_asset(asset_id)
        if asset.kind != AssetKind.WEB:
            raise ValueError("Asset is not editable web media")
        spec = self.repository.web.get_web_asset_spec(asset_id)
        actual_hash = self._storage.scan_package(
            web_files.web_package_root(
                self.repository.assets.resolve_asset_path(asset),
                spec.manifest,
            )
        ).source_hash
        if actual_hash != spec.source_hash:
            raise RuntimeError("Editable media package changed after import; rebind it as a new package")
        return spec

    def read_package_tree(
        self,
        source: str | Path,
    ) -> tuple[
        web_files.WebPackageTree,
        EditableMediaManifest,
        WebMediaSourcesManifest,
    ]:
        tree = self._storage.read_source_tree(source)
        manifest = parse_editable_media_manifest_json(
            self._storage.read_package_text(tree, web_files.MANIFEST_FILE_NAME),
            self._contract,
        )
        media_sources = WebMediaSourcesManifest.model_validate_json(
            self._storage.read_package_text(tree, manifest.media_sources)
        )
        web_contract.validate_package_files(tree, manifest, media_sources)
        return tree, manifest, media_sources

    def read_package(self, source: str | Path) -> tuple[Path, EditableMediaManifest]:
        tree, manifest, _media_sources = self.read_package_tree(source)
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
        project_dir = self.repository.project_dir
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
        try:
            copied_tree = self._storage.stage_package(source_tree, publication)
            copied_manifest = parse_editable_media_manifest_json(
                self._storage.read_package_text(
                    copied_tree,
                    web_files.MANIFEST_FILE_NAME,
                ),
                self._contract,
            )
            copied_media_sources = WebMediaSourcesManifest.model_validate_json(
                self._storage.read_package_text(
                    copied_tree,
                    copied_manifest.media_sources,
                )
            )
            web_contract.validate_package_files(
                copied_tree,
                copied_manifest,
                copied_media_sources,
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
                self._storage.archive_failed(publication)
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
        project_dir = self.repository.project_dir
        web_files.validate_web_package_paths(
            tree,
            project_dir / "staging" / "web" / f"s-{token}",
            project_dir / "sources" / "web" / f"p-{token}",
            project_dir / "archive" / "web" / f"f-{token}",
        )

    def _reconcile_publications(self) -> None:
        project_dir = self.repository.project_dir
        for residual in self._storage.staging_residuals(project_dir):
            self._storage.archive_residual(project_dir, residual)
        for location in self._storage.publication_receipts(project_dir):
            receipt = location.receipt
            if receipt.status == "committed":
                tree = self._storage.scan_package(location.final)
                if tree.source_hash != receipt.source_hash:
                    raise RuntimeError("Editable media package changed after it was committed")
                continue
            references = self._web_assets_referencing(location.final)
            if references:
                if references != [receipt.asset_id]:
                    raise RuntimeError("Editable media publication receipt does not match its asset")
                tree = self._storage.scan_package(location.final)
                if tree.source_hash != receipt.source_hash:
                    raise RuntimeError("Editable media publication changed before it was committed")
                self._storage.mark_receipt_committed(location)
                continue
            self._storage.archive_residual(
                project_dir,
                WebPackageResidual(
                    token=receipt.token,
                    package=location.final,
                    receipt=location.path,
                ),
            )

    def _web_assets_referencing(
        self,
        package_root: Path,
    ) -> list[str]:
        references: list[str] = []
        for asset in self.repository.assets.list_assets():
            if asset.kind != AssetKind.WEB:
                continue
            spec = self.repository.web.get_web_asset_spec(asset.id)
            current_root = web_files.web_package_root(
                self.repository.assets.resolve_asset_path(asset),
                spec.manifest,
            )
            if self._storage.paths_equal(current_root, package_root):
                references.append(asset.id)
        return sorted(references)

    def commit_publication(
        self,
        publication: web_files.WebPackagePublication,
        change: Callable[[], T],
    ) -> T:
        try:
            with self.repository.transaction():
                self._storage.publish(publication)
                self.repository.enlist_transaction_publication(
                    on_commit=lambda: self._storage.mark_committed(publication),
                    on_rollback=lambda _error: self._storage.archive_failed(publication),
                )
                return change()
        except BaseException as error:
            try:
                self._storage.archive_failed(publication)
            except BaseException as archive_error:
                error.add_note(f"网页包事务回滚后归档失败：{archive_error}")
            raise
