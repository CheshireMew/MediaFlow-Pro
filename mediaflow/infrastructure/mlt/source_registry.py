from __future__ import annotations

from pathlib import Path

from mediaflow.application.ports import TimelineCompilationDocuments
from mediaflow.domain.project import Asset
from mediaflow.domain.storage_names import require_windows_interop_path

from .graph import MltGraph


class MltSourceRegistry:
    """Resolve, verify, and deduplicate physical sources for one graph compile."""

    def __init__(
        self,
        repository: TimelineCompilationDocuments,
        *,
        use_proxies: bool,
        prefer_sdr_preview_proxy: bool,
    ) -> None:
        self._repository = repository
        self._use_proxies = use_proxies
        self._prefer_sdr_preview_proxy = prefer_sdr_preview_proxy
        self._asset_sources: dict[str, Path] = {}
        self._sources: dict[Path, None] = {}

    def asset_source(
        self,
        asset: Asset,
        *,
        use_proxies: bool | None = None,
    ) -> Path:
        cached = self._asset_sources.get(asset.id)
        if cached is not None:
            return cached
        resolved = require_windows_interop_path(
            MltGraph.source_path(
                self._repository,
                asset,
                use_proxies=self._use_proxies if use_proxies is None else use_proxies,
                prefer_sdr_preview_proxy=(
                    self._prefer_sdr_preview_proxy if use_proxies is None else False
                ),
            )
        )
        self._asset_sources[asset.id] = self.require_source(resolved)
        return resolved

    def require_source(self, source: Path) -> Path:
        resolved = require_windows_interop_path(source)
        if resolved not in self._sources:
            if not resolved.is_file():
                raise FileNotFoundError(resolved)
            self._sources[resolved] = None
        return resolved

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(self._sources)
