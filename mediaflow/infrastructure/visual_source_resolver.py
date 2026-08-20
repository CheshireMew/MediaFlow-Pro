from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from mediaflow.application.project_storage_ports import AssetDocuments
from mediaflow.domain.enums import AssetKind
from mediaflow.domain.project import Asset

VisualSourcePreference = Literal["original", "proxy"]


class VisualSourceDocuments(Protocol):
    project_dir: Path

    @property
    def assets(self) -> AssetDocuments: ...


def resolve_visual_source(
    documents: VisualSourceDocuments,
    asset: Asset,
    *,
    prefer: VisualSourcePreference,
) -> Path | None:
    original = documents.assets.resolve_asset_path(asset)
    proxies: list[Path] = []
    if asset.kind == AssetKind.VIDEO:
        for value in (asset.sdr_preview_proxy_path, asset.proxy_path):
            if not value:
                continue
            path = Path(value)
            proxies.append(
                (documents.project_dir / path).resolve() if not path.is_absolute() else path.resolve()
            )
    candidates = (original, *proxies) if prefer == "original" else (*proxies, original)
    return next((path for path in candidates if path.is_file()), None)
