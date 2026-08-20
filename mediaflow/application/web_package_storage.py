from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mediaflow.application.web_package_files import (
    WebPackagePublication,
    WebPackageReceipt,
    WebPackageTree,
)


@dataclass(frozen=True, slots=True)
class WebPackageResidual:
    token: str
    package: Path
    receipt: Path


@dataclass(frozen=True, slots=True)
class WebPackageReceiptLocation:
    path: Path
    final: Path
    receipt: WebPackageReceipt


class WebPackageStorage(Protocol):
    """Filesystem boundary for immutable editable-media publications."""

    def read_source_tree(self, source: str | Path) -> WebPackageTree: ...

    def read_package_text(self, tree: WebPackageTree, relative_path: str) -> str: ...

    def scan_package(self, package_root: Path) -> WebPackageTree: ...

    def stage_package(
        self,
        source_tree: WebPackageTree,
        publication: WebPackagePublication,
    ) -> WebPackageTree: ...

    def publish(self, publication: WebPackagePublication) -> None: ...

    def mark_committed(self, publication: WebPackagePublication) -> None: ...

    def archive_failed(self, publication: WebPackagePublication) -> None: ...

    def staging_residuals(self, project_dir: Path) -> tuple[WebPackageResidual, ...]: ...

    def publication_receipts(
        self,
        project_dir: Path,
    ) -> tuple[WebPackageReceiptLocation, ...]: ...

    def mark_receipt_committed(self, location: WebPackageReceiptLocation) -> None: ...

    def archive_residual(
        self,
        project_dir: Path,
        residual: WebPackageResidual,
    ) -> None: ...

    def paths_equal(self, left: Path, right: Path) -> bool: ...
