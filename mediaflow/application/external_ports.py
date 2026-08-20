from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from mediaflow.domain.enums import AssetKind
from mediaflow.domain.project import AssetFingerprint, MediaMetadata, ProjectProfile
from mediaflow.domain.settings import LlmProviderSettings


class MediaProbeResult(Protocol):
    @property
    def kind(self) -> AssetKind: ...

    @property
    def metadata(self) -> MediaMetadata: ...

    @property
    def suggested_profile(self) -> ProjectProfile | None: ...


class MediaProbePort(Protocol):
    def probe(
        self,
        path: str | Path,
        *,
        timeline_profile: ProjectProfile | None = None,
    ) -> MediaProbeResult: ...


FingerprintFile = Callable[[Path], AssetFingerprint]


class JsonClient(Protocol):
    def complete_json(self, *, system: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class JsonClientFactory(Protocol):
    def __call__(self, provider: LlmProviderSettings) -> JsonClient: ...


class StructuredFileReader(Protocol):
    def resolve_file(self, source: str | Path) -> Path: ...

    def read_json(self, source: Path) -> object: ...

    def read_csv(self, source: Path) -> list[Mapping[str, object]]: ...


class TranslationCachePort(Protocol):
    def get(self, request: dict[str, Any]) -> list[str] | None: ...
    def put(self, request: dict[str, Any], texts: list[str]) -> None: ...
