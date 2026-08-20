from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from mediaflow.application.edit_history import (
    ProjectEditAction,
    ProjectEditCommand,
    ProjectEditHistory,
)
from mediaflow.application.portable_timeline_import import PortableTimelineImportService
from mediaflow.application.ports import StructuredFileReader
from mediaflow.application.project_workflow_service import ProjectWorkflowService
from mediaflow.application.subtitle_publication import SubtitlePublicationService
from mediaflow.application.task_service import TaskService
from mediaflow.application.timeline_editor import TimelineEditor
from mediaflow.domain.collaboration import ProjectChange, ProjectChangeSet
from mediaflow.domain.settings import ServiceSettings
from mediaflow.domain.task_commands import ImportAssetCommand, TaskCommand
from mediaflow.domain.tasks import Task
from mediaflow.infrastructure.fcpxml_export import FcpxmlExportService
from mediaflow.infrastructure.mlt import (
    LoudnessAnalysisService,
    SequenceBoundaryAnalysisService,
    TimelineCompiler,
)
from mediaflow.infrastructure.project_repository import ProjectRepository
from mediaflow.infrastructure.proxy_service import ProxyService
from mediaflow.infrastructure.runtime_paths import RuntimePaths
from mediaflow.infrastructure.web_render_service import WebRenderService
from mediaflow.project_task_settlement import ProjectTaskResult, ProjectTaskSettlement


class EditorProjectDeliveryCommands:
    _repository: ProjectRepository
    _paths: RuntimePaths
    _portable_timelines: PortableTimelineImportService
    _subtitle_publication: SubtitlePublicationService
    _timelines: dict[str, TimelineEditor]
    _history: ProjectEditHistory
    _structured_files: StructuredFileReader
    _tasks: TaskService
    _task_settlement: ProjectTaskSettlement
    _workflows: ProjectWorkflowService
    _settings: ServiceSettings

    if TYPE_CHECKING:

        def _require_writable(self) -> None: ...

    def inspect_portable_timeline(self, path: str | Path):
        return self._portable_timelines.inspect(path)

    def import_portable_timeline(self, path: str | Path, *, sequence_id: str):
        self._require_writable()
        return self._portable_timelines.import_timeline(
            path,
            sequence_id=sequence_id,
        )

    def create_version(self, name: str):
        return self._repository.records.create_project_version(name)

    def list_versions(self):
        return self._repository.records.list_project_versions()

    def restore_version(self, version_id: str):
        with self._repository.transaction():
            record = self._repository.records.restore_project_version(version_id)
            self._subtitle_publication.reconcile_document_srts()
        sequence_ids = {
            sequence.id for sequence in self._repository.sequences.list_sequences(include_archived=True)
        }
        for sequence_id, editor in list(self._timelines.items()):
            if sequence_id in sequence_ids:
                try:
                    editor.reload()
                except Exception:
                    self._timelines.pop(sequence_id, None)
            else:
                self._timelines.pop(sequence_id)
        self._history.clear()
        return record

    def export_fcpxml(
        self,
        sequence_id: str,
        destination: str | Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        state = self._repository.timeline.load_timeline(sequence_id)
        exporter = FcpxmlExportService(self._repository, self._paths)
        output = exporter.preflight(
            state,
            destination,
            overwrite=overwrite,
        )
        WebRenderService(
            self._repository,
            self._paths,
        ).ensure_sequence(state)
        return exporter.export(
            state,
            output,
            overwrite=overwrite,
        )

    def proxy_decision(self, asset, *, dropped_frames: int = 0, manual: bool = False):
        return ProxyService.decision(asset, dropped_frames=dropped_frames, manual=manual)

    def sequence_boundary_snapshot_hash(self, sequence_id: str) -> str:
        state = self._repository.timeline.load_timeline(sequence_id)
        return SequenceBoundaryAnalysisService(
            TimelineCompiler(self._repository, self._paths),
            self._paths,
        ).snapshot_hash(state)

    def loudness_snapshot_hash(self, sequence_id: str) -> str:
        state = self._repository.timeline.load_timeline(sequence_id)
        return LoudnessAnalysisService(
            TimelineCompiler(self._repository, self._paths),
            self._paths,
        ).snapshot_hash(state)

    def read_loudness_metrics(self, sequence_id: str) -> dict[str, float]:
        metrics = LoudnessAnalysisService.read_current_metrics(
            self._repository.project_dir,
            sequence_id,
            current_project_revision=self._repository.content_revision(),
        )
        return metrics.desktop_payload() if metrics is not None else {}

    def start_task(
        self,
        command: TaskCommand,
        input_asset_ids: list[str] | None = None,
        *,
        sequence_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Task:
        self._require_writable()
        project = self._repository.projects.get_project()
        return self._tasks.start(
            project_id=project.id,
            sequence_id=sequence_id or project.main_sequence_id,
            command=command,
            input_asset_ids=input_asset_ids,
            idempotency_key=idempotency_key,
        )

    def import_asset(
        self,
        source: str | Path,
        *,
        sequence_id: str | None = None,
        purpose: str = "media",
        language: str = "auto",
        media_asset_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Task:
        path = self._structured_files.resolve_file(source)
        if purpose not in {"media", "subtitle", "watermark"}:
            raise ValueError(f"Unknown import purpose: {purpose}")
        import_purpose = cast(Literal["media", "subtitle", "watermark"], purpose)
        return self.start_task(
            ImportAssetCommand(
                source_path=str(path),
                purpose=import_purpose,
                language=language,
                media_asset_id=media_asset_id,
            ),
            sequence_id=sequence_id,
            idempotency_key=idempotency_key,
        )

    def committed_task_result(self, task_id: str) -> ProjectTaskResult | None:
        return self._task_settlement.committed_result(task_id)

    def archive_short_sequence(self, sequence_id: str) -> None:
        sequence = self._repository.sequences.archive_short_sequence(sequence_id)
        self._history.push(
            ProjectEditCommand(
                label="删除短视频序列",
                undo_actions=[
                    ProjectEditAction(
                        kind="sequence.archive-state",
                        payload={"sequence_id": sequence.id, "archived": False},
                    )
                ],
                redo_actions=[
                    ProjectEditAction(
                        kind="sequence.archive-state",
                        payload={"sequence_id": sequence.id, "archived": True},
                    )
                ],
            ),
            ProjectChangeSet(
                changes=[
                    ProjectChange(
                        path=f"/sequences/{sequence.id}/settings/archived",
                        action="update",
                        value=True,
                    )
                ]
            ),
        )

    def _apply_sequence_archive_history_action(
        self,
        action: ProjectEditAction,
    ) -> None:
        sequence_id = str(action.payload.get("sequence_id") or "")
        if bool(action.payload.get("archived")):
            self._repository.sequences.archive_short_sequence(sequence_id)
        else:
            self._repository.sequences.restore_short_sequence(sequence_id)

    def refresh_workflow_mode(self) -> ProjectWorkflowService:
        self._workflows.update_settings(self._settings)
        return self._workflows

    def update_settings(self, settings: ServiceSettings) -> None:
        self._settings = settings
        self.refresh_workflow_mode()
