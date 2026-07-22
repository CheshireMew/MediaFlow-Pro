from __future__ import annotations

import shutil
from pathlib import Path

from mediaflow.application.web_media_service import (
    MANIFEST_FILE_NAME,
    WebMediaService,
    editable_media_source_hash,
)
from mediaflow.domain.web_media import EditableMediaManifest, WebComponentRecord


class WebComponentLibrary:
    """Versioned local library of product-independent editable media packages."""

    def __init__(self, root: Path, validator) -> None:
        self.root = root
        self.validator = validator

    def install(self, source: str | Path) -> WebComponentRecord:
        package_root, manifest = WebMediaService.read_package(source)
        if manifest.component is None:
            raise ValueError("Editable media component metadata is required for library installation")
        self.validator.validate(package_root, manifest)
        source_hash = editable_media_source_hash(package_root)
        # Keep the on-disk version directory compact so deeply nested Windows
        # runtime roots do not cross the legacy MAX_PATH boundary. The record
        # still exposes and verifies the complete SHA-256 digest.
        destination = self.root / manifest.component.id / source_hash[:12]
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(package_root, destination)
        elif editable_media_source_hash(destination) != source_hash:
            raise FileExistsError(
                f"Editable media component version prefix collision: {destination}"
            )
        return self._record(destination, manifest, source_hash)

    def list(self) -> list[WebComponentRecord]:
        if not self.root.is_dir():
            return []
        records: list[WebComponentRecord] = []
        for manifest_path in sorted(self.root.glob(f"*/*/{MANIFEST_FILE_NAME}")):
            try:
                manifest = EditableMediaManifest.model_validate_json(
                    manifest_path.read_text(encoding="utf-8")
                )
                if manifest.component is None:
                    continue
                package_root = manifest_path.parent
                records.append(
                    self._record(
                        package_root,
                        manifest,
                        editable_media_source_hash(package_root),
                    )
                )
            except (OSError, ValueError):
                continue
        return sorted(records, key=lambda item: (item.category, item.name, item.version_hash))

    def get(self, component_id: str, version_hash: str | None = None) -> WebComponentRecord:
        candidates = [
            item
            for item in self.list()
            if item.component_id == component_id
            and (version_hash is None or item.version_hash == version_hash)
        ]
        if not candidates:
            raise KeyError(component_id if version_hash is None else f"{component_id}/{version_hash}")
        return candidates[-1]

    @staticmethod
    def _record(
        package_root: Path,
        manifest: EditableMediaManifest,
        source_hash: str,
    ) -> WebComponentRecord:
        component = manifest.component
        if component is None:
            raise ValueError("Editable media component metadata is missing")
        return WebComponentRecord(
            component_id=component.id,
            name=component.name,
            category=component.category,
            tags=component.tags,
            version_hash=source_hash,
            package_path=str(package_root.resolve()),
        )
