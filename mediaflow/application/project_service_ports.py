from __future__ import annotations

from pathlib import Path
from typing import Protocol

from mediaflow.application.project_storage_ports import (
    AssetDocuments,
    AudioDocuments,
    HighlightDocuments,
    ProjectAccess,
    ProjectMetadataDocuments,
    SequenceDocuments,
    SubtitleDocuments,
    TimelineDocuments,
    WebMediaDocuments,
)
from mediaflow.domain.subtitle_file import SubtitleCue


class AssetServiceDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def projects(self) -> ProjectMetadataDocuments: ...

    @property
    def sequences(self) -> SequenceDocuments: ...

    @property
    def assets(self) -> AssetDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...


class TimelineValidationDocuments(Protocol):
    @property
    def projects(self) -> ProjectMetadataDocuments: ...

    @property
    def sequences(self) -> SequenceDocuments: ...

    @property
    def assets(self) -> AssetDocuments: ...


class WebApplicationDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def projects(self) -> ProjectMetadataDocuments: ...

    @property
    def sequences(self) -> SequenceDocuments: ...

    @property
    def assets(self) -> AssetDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...


class HighlightServiceDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def projects(self) -> ProjectMetadataDocuments: ...

    @property
    def sequences(self) -> SequenceDocuments: ...

    @property
    def assets(self) -> AssetDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def highlights(self) -> HighlightDocuments: ...


class SequenceServiceDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def projects(self) -> ProjectMetadataDocuments: ...

    @property
    def sequences(self) -> SequenceDocuments: ...

    @property
    def assets(self) -> AssetDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...


class SubtitleAcquisitionDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def projects(self) -> ProjectMetadataDocuments: ...

    @property
    def sequences(self) -> SequenceDocuments: ...

    @property
    def assets(self) -> AssetDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...


class SubtitleFileStore(Protocol):
    def resolve_existing_file(self, path: str | Path) -> Path: ...
    def canonical_path(self, path: str | Path) -> Path: ...
    def related_media_candidates(self, subtitle_path: Path) -> list[Path]: ...
    def existing_related_media(self, subtitle_path: Path) -> list[Path]: ...

    def read(
        self,
        path: str | Path,
        *,
        fps_numerator: int,
        fps_denominator: int,
    ) -> list[SubtitleCue]: ...

    def write_srt(
        self,
        path: str | Path,
        cues: list[SubtitleCue],
        *,
        fps_numerator: int,
        fps_denominator: int,
    ) -> Path: ...


class SubtitleEditingDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def projects(self) -> ProjectMetadataDocuments: ...

    @property
    def sequences(self) -> SequenceDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...


class TimelineEditorDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def projects(self) -> ProjectMetadataDocuments: ...

    @property
    def sequences(self) -> SequenceDocuments: ...

    @property
    def assets(self) -> AssetDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...
