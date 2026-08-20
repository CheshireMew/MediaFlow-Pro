from __future__ import annotations

from typing import Protocol

from mediaflow.application.project_storage_ports import (
    AssetDocuments,
    AudioDocuments,
    DubbingDocuments,
    HighlightDocuments,
    ProjectAccess,
    ProjectMetadataDocuments,
    ProjectRecordsDocuments,
    SequenceDocuments,
    SubtitleDocuments,
    TimelineDocuments,
    WebMediaDocuments,
)


class WebTaskDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def timeline(self) -> TimelineDocuments: ...


class AssetTaskDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def projects(self) -> ProjectMetadataDocuments: ...

    @property
    def sequences(self) -> SequenceDocuments: ...

    @property
    def assets(self) -> AssetDocuments: ...


class ExportTaskDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def highlights(self) -> HighlightDocuments: ...

    @property
    def records(self) -> ProjectRecordsDocuments: ...


class TranscriptionTaskDocuments(
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
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...


class AnalysisTaskDocuments(
    ProjectAccess,
    Protocol,
):
    @property
    def assets(self) -> AssetDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...


class ProjectTaskDocuments(
    AssetTaskDocuments,
    WebTaskDocuments,
    ExportTaskDocuments,
    TranscriptionTaskDocuments,
    AnalysisTaskDocuments,
    Protocol,
):
    """Complete persistence surface used only by the task composition root."""

    @property
    def dubbing(self) -> DubbingDocuments: ...
