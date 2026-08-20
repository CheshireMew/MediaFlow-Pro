from __future__ import annotations

from pathlib import Path
from typing import Protocol

from mediaflow.application.project_service_ports import (
    SubtitleAcquisitionDocuments,
    TimelineEditorDocuments,
)
from mediaflow.application.project_storage_ports import (
    AssetDocuments,
    AudioDocuments,
    HighlightDocuments,
    ProjectAccess,
    ProjectMetadataDocuments,
    ProjectRecordsDocuments,
    SequenceDocuments,
    SubtitleDocuments,
    TimelineDocuments,
    WebMediaDocuments,
    WorkflowDocuments,
)


class ProjectWorkflowMetadataDocuments(
    ProjectMetadataDocuments,
    WorkflowDocuments,
    Protocol,
):
    pass


class PortableTimelineImportDocuments(
    TimelineEditorDocuments,
    SubtitleAcquisitionDocuments,
    Protocol,
):
    project_dir: Path


class TranscriptEditingDocuments(ProjectAccess, Protocol):
    @property
    def projects(self) -> ProjectMetadataDocuments: ...

    @property
    def sequences(self) -> SequenceDocuments: ...

    @property
    def assets(self) -> AssetDocuments: ...

    @property
    def records(self) -> ProjectRecordsDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...


class SubtitlePublicationDocuments(ProjectAccess, Protocol):
    @property
    def projects(self) -> ProjectMetadataDocuments: ...

    @property
    def sequences(self) -> SequenceDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...


class TranslationDocuments(ProjectAccess, Protocol):
    @property
    def projects(self) -> ProjectMetadataDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...


class WorkflowCoordinatorDocuments(ProjectAccess, Protocol):
    @property
    def projects(self) -> ProjectWorkflowMetadataDocuments: ...


class ProjectWorkflowDocuments(ProjectAccess, Protocol):
    @property
    def projects(self) -> ProjectWorkflowMetadataDocuments: ...

    @property
    def sequences(self) -> SequenceDocuments: ...

    @property
    def assets(self) -> AssetDocuments: ...

    @property
    def timeline(self) -> TimelineDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def highlights(self) -> HighlightDocuments: ...


class AssetProcessingDocuments(ProjectAccess, Protocol):
    @property
    def assets(self) -> AssetDocuments: ...


class TimelineCompilationDocuments(ProjectAccess, Protocol):
    @property
    def projects(self) -> ProjectMetadataDocuments: ...

    @property
    def sequences(self) -> SequenceDocuments: ...

    @property
    def assets(self) -> AssetDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...


class InterchangeExportDocuments(ProjectAccess, Protocol):
    @property
    def projects(self) -> ProjectMetadataDocuments: ...

    @property
    def sequences(self) -> SequenceDocuments: ...

    @property
    def assets(self) -> AssetDocuments: ...

    @property
    def subtitles(self) -> SubtitleDocuments: ...

    @property
    def audio(self) -> AudioDocuments: ...

    @property
    def web(self) -> WebMediaDocuments: ...
